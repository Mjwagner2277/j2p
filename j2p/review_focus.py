"""Prioritize review actions without discarding their detailed audit evidence."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Set

from .models import AuditItem, RunPlan


_CHILD_OMISSIONS = {"StoryEpicExcluded", "StoryEpicNotFound"}
_DEPENDENCY_FAILURES = {
    "MissingDependencyTarget", "CircularDependencySkipped", "SelfDependencySkipped",
    "ProjectDependencyTaskMissing", "ProjectDependencyWriteFailed",
}
_DATE_FAILURES = {"UnparsedDate", "InvalidDate", "InvalidDateRange", "TargetDateOrderInvalid"}
_SCOPE_FAILURES = {
    "ExcludedMissingRollup", "ExcludedUnknownPrefix", "StoryEpicExcluded", "StoryEpicNotFound",
    "StoryMissingEpicLink", "StoryParentNotEpic", "MissingResourceGroupConfig",
}
_TIERS = {"Fix first": 0, "Focus now": 1, "Later": 2, "Unscheduled": 3, "Historical": 4}


def _key(value: Any) -> str:
    return str(value or "").strip().upper()


def _date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _issue_context(plan: RunPlan) -> Dict[str, Dict[str, Any]]:
    """Use source issue facts, with planned rows as a fallback for Project audits."""
    contexts = {
        _key(key): dict(context)
        for key, context in plan.stats.get("review_issue_context", {}).items()
        if key
    }
    rows: Dict[str, List[Any]] = defaultdict(list)
    for epic in plan.epics.values():
        rows[_key(epic.jira_key or epic.key)].append(epic)
    for key, epics in rows.items():
        if key in contexts:
            continue
        epic = sorted(epics, key=lambda row: (not row.drives_schedule, row.key))[0]
        contexts[key] = {
            "summary": epic.summary,
            "completed": all(row.completed for row in epics),
            "target_start": epic.target_start,
            "target_end": epic.target_end,
            "parent_epic": "",
            "resource_group": epic.resource_group,
        }
    return contexts


def _successors(plan: RunPlan) -> Dict[str, Set[str]]:
    """Merge schedule/reference rows into one accepted graph per Jira issue."""
    identities = {
        _key(schedule_key): _key(epic.jira_key or epic.key)
        for schedule_key, epic in plan.epics.items()
    }
    graph: Dict[str, Set[str]] = defaultdict(set)
    for schedule_key, epic in plan.epics.items():
        key = identities[_key(schedule_key)]
        for successor in epic.successors:
            target = identities.get(_key(successor), _key(successor))
            if target != key:
                graph[key].add(target)
        for predecessor in epic.predecessors:
            source = identities.get(_key(predecessor), _key(predecessor))
            if source != key:
                graph[source].add(key)
    return graph


def _downstream(keys: Set[str], graph: Dict[str, Set[str]], contexts: Dict[str, Dict[str, Any]]) -> Set[str]:
    seen = set(keys)
    stack = [target for key in keys for target in graph.get(key, ())]
    unfinished = set()
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        if contexts.get(key, {}).get("completed") is not True:
            unfinished.add(key)
        stack.extend(graph.get(key, ()))
    return unfinished


def _is_dependency_failure(category: str) -> bool:
    return category in _DEPENDENCY_FAILURES or (
        category.startswith("ProjectDependency")
        and any(word in category for word in ("Fail", "Reject", "Missing", "Invalid"))
    )


def _is_project_failure(category: str) -> bool:
    return (
        category != "ProjectNativeCompletionRecalculated"
        and category.startswith("Project")
        and any(word in category for word in ("WriteFailed", "Rejected", "VerificationFailed", "ReadbackFailed"))
    )


def build_review_focus(
    plan: RunPlan, days: int = 90, dependency_plan: Optional[RunPlan] = None,
) -> Dict[str, Any]:
    """Return grouped, ranked actions plus counts; leave the original audit intact.

    A past date alone never makes an item historical. Completion must be confirmed
    for every known affected issue, and unfinished downstream work keeps its
    upstream action visible. Undated issue groups stay in Unscheduled, including
    those with dependency impact or errors. ``days=0`` includes all dated
    unfinished work in Focus now.
    Supply the full ``dependency_plan`` for filtered reports so cross-team
    successors remain part of impact analysis; only ``plan`` audits are grouped.
    """
    if days < 0:
        raise ValueError("Review focus days cannot be negative.")
    as_of = _date(plan.generated_at) or date.today()
    cutoff = as_of + timedelta(days=days)
    contexts = _issue_context(dependency_plan or plan)
    if dependency_plan is not None:
        for key, context in _issue_context(plan).items():
            contexts.setdefault(key, context)
    graph = _successors(dependency_plan or plan)
    children: Dict[str, Set[str]] = defaultdict(set)
    for key, context in contexts.items():
        parent = _key(context.get("parent_epic"))
        if parent:
            children[parent].add(key)

    candidates = [
        item for item in plan.audit_items
        if item.severity.lower() in {"error", "warning", "review"}
        or item.category == "FutureInPlanning"
    ]
    buckets: Dict[str, List[AuditItem]] = defaultdict(list)
    parent_groups = set()
    missing_initiative_groups = set()
    affected_parents: Dict[str, Set[str]] = defaultdict(set)
    for item in candidates:
        jira_key = _key(item.jira_key)
        parent = ""
        if item.category in _CHILD_OMISSIONS:
            parent = _key(contexts.get(jira_key, {}).get("parent_epic") or item.old_value)
        if item.category == "ExcludedMissingRollup" and contexts.get(jira_key, {}).get("missing_rollup_parent"):
            parent = jira_key
        if parent:
            missing_initiative = _key(contexts.get(parent, {}).get("missing_rollup_parent"))
            group_key = missing_initiative or parent
            parent_groups.add(group_key)
            affected_parents[group_key].add(parent)
            if missing_initiative:
                missing_initiative_groups.add(group_key)
        else:
            group_key = jira_key or f"{item.category}:{item.source_file}:{item.source_row or ''}"
        buckets[group_key].append(item)

    groups = []
    for key, items in buckets.items():
        items = sorted(items, key=lambda item: (
            item.category, item.jira_key, item.schedule_key, item.field,
            item.source_file, item.source_row or 0, item.message,
        ))
        source_keys = {_key(item.jira_key) for item in items if item.jira_key}
        entity_keys = set(source_keys)
        if key in parent_groups:
            # Parent status and known siblings prevent a completed child from
            # hiding a still-open omission. An absent parent is not invented.
            for parent in affected_parents[key]:
                entity_keys.update(children.get(parent, ()))
                if parent in contexts:
                    entity_keys.add(parent)
        entities = [contexts.get(entity_key, {}) for entity_key in sorted(entity_keys)]
        complete = bool(entities) and all(entity.get("completed") is True for entity in entities)
        active = [entity for entity in entities if entity.get("completed") is not True]
        unknown = not entities or any("completed" not in entity for entity in entities)
        downstream = _downstream(entity_keys | {key}, graph, contexts)
        categories = sorted({item.category for item in items})
        errors = any(item.severity.lower() == "error" for item in items)
        dependency_failure = any(_is_dependency_failure(category) for category in categories)
        project_failure = any(_is_project_failure(category) for category in categories)
        invalid_dates = any(category in _DATE_FAILURES for category in categories)
        for entity in active:
            start, end = _date(entity.get("target_start")), _date(entity.get("target_end"))
            if (entity.get("target_start") and not start) or (entity.get("target_end") and not end):
                invalid_dates = True
            if start and end and end < start:
                invalid_dates = True

        # Only unfinished issue dates determine urgency, so an old completed
        # sibling does not pull a future action into the current window.
        active_dates = [
            value for entity in active for field in ("target_start", "target_end")
            if (value := _date(entity.get(field))) is not None
        ]
        active_ends = [value for entity in active if (value := _date(entity.get("target_end"))) is not None]
        overdue = min((value for value in active_ends if value < as_of), default=None)
        # Only Jira dates establish priority. Blank dates are different from
        # supplied but invalid dates, which remain actionable date errors.
        # For grouped omissions, dated unfinished parents/children can establish
        # urgency; a completed sibling's dates cannot. Global run/data errors
        # without an issue identity are not undated task groups.
        relevant_entities = active or entities
        unscheduled = bool(entity_keys) and not invalid_dates and not any(
            str(entity.get(field) or "").strip()
            for entity in relevant_entities for field in ("target_start", "target_end")
        )
        root = contexts.get(key, {})
        if key in missing_initiative_groups:
            parent_ends = [
                value for parent in affected_parents[key]
                if contexts.get(parent, {}).get("completed") is not True
                and (value := _date(contexts.get(parent, {}).get("target_end"))) is not None
            ]
            display_end = min(parent_ends, default=None) or min(active_ends, default=None)
        else:
            display_end = _date(root.get("target_end")) or min(active_ends, default=None)
        if display_end is None:
            display_end = min((value for entity in entities if (value := _date(entity.get("target_end"))) is not None), default=None)

        reasons = []
        critical_rank = 4
        if errors:
            reasons.append("An error requires review.")
            critical_rank = 0
        if dependency_failure:
            reasons.append("Dependency links are missing, invalid, or rejected; their impact needs review.")
            critical_rank = min(critical_rank, 1)
        if project_failure:
            reasons.append("Microsoft Project did not retain a requested update.")
            critical_rank = min(critical_rank, 2)
        if invalid_dates and (not complete or downstream):
            reasons.append("Target dates are invalid or inconsistent.")
            critical_rank = min(critical_rank, 3)
        if downstream:
            reasons.append(f"Impacts {len(downstream)} unfinished downstream epic(s).")
        current_scope = (not complete) and bool(active_dates) and (
            not days or min(active_dates) <= cutoff
        )
        if current_scope and any(category in _SCOPE_FAILURES for category in categories):
            reasons.append("Unfinished or unconfirmed work is omitted from schedule scope or completion totals.")
            critical_rank = min(critical_rank, 3)

        if complete and not downstream and critical_rank == 4:
            tier = "Historical"
            reasons.append("All affected work is complete; retained for traceability.")
        elif unscheduled:
            tier = "Unscheduled"
            reasons.append("No Jira target dates are supplied for the affected work; retained outside high-priority fixes.")
        elif critical_rank < 4:
            tier = "Fix first"
        elif downstream:
            tier = "Focus now"
        elif unknown and not active_dates:
            tier = "Focus now"
            reasons.append("Completion could not be confirmed.")
        elif not days or min(active_dates) <= cutoff:
            tier = "Focus now"
            if not overdue:
                reasons.append("All unfinished work is included." if not days else f"Work is active or due by {cutoff.isoformat()}.")
        else:
            tier = "Later"
            reasons.append(f"Unfinished work is scheduled after {cutoff.isoformat()}.")
        if overdue:
            reasons.append(f"Unfinished work has a past target end ({overdue.isoformat()}).")
        if key in parent_groups:
            omitted_count = len({_key(item.jira_key) for item in items if item.category in _CHILD_OMISSIONS and item.jira_key})
            if key in missing_initiative_groups:
                reasons.append(f"Review this missing initiative once for {len(affected_parents[key])} excluded epic(s) and {omitted_count} omitted child issue(s).")
            else:
                reasons.append(f"Review this parent once for {omitted_count} omitted child issue(s).")

        actions = set()
        for item in items:
            action = item.reviewer_action
            if item.category == "FutureInPlanning" and tier in {"Fix first", "Focus now"}:
                action = "Confirm this epic is intentionally unestimated or add pointed child work."
            if action:
                actions.add(action)
        actions_ordered = sorted(actions)
        if key in missing_initiative_groups:
            actions_ordered.insert(0, f"Include initiative {key} in the Jira export, or correct the affected epics' parent links if {key} is wrong.")
        resource_groups = sorted({str(entity.get("resource_group")) for entity in entities if entity.get("resource_group")})
        group = {
            "key": key,
            "summary": root.get("summary") or (
                f"Missing initiative {key}" if key in missing_initiative_groups
                else f"Parent epic {key}" if key in parent_groups
                else next((item.summary for item in items if item.summary), "")
            ),
            "resource_group": root.get("resource_group") or ", ".join(resource_groups),
            "tier": tier,
            "reasons": reasons,
            "categories": categories,
            "actions": actions_ordered,
            "audit_count": len(items),
            "issue_count": len(source_keys),
            "target_end": display_end.isoformat() if display_end else "",
            "downstream_count": len(downstream),
            "items": items,
            "_priority": (
                _TIERS[tier], 0 if errors or (project_failure and tier == "Fix first") else 1,
                -max(len(downstream), len(source_keys)), -len(downstream),
                overdue is None, overdue or date.max, display_end or date.max, key,
            ),
        }
        groups.append(group)
    groups.sort(key=lambda group: group["_priority"])
    for group in groups:
        del group["_priority"]
    return {
        "groups": groups,
        "total_audit_count": len(candidates),
        "focus_count": sum(group["tier"] in {"Fix first", "Focus now"} for group in groups),
        "historical_count": sum(group["tier"] == "Historical" for group in groups),
        "later_count": sum(group["tier"] == "Later" for group in groups),
        "unscheduled_count": sum(group["tier"] == "Unscheduled" for group in groups),
        "grouped_count": len(groups),
    }
