"""Select observed schedule branches without changing their audit evidence."""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, Optional, Set

from .cascade import CascadeGraph
from .models import AuditItem, RunPlan


_CHANGE_FIELDS = {
    "ScheduledStartChange": "Start",
    "CascadeBranchDriver": "Finish",
    "CascadingDateChange": "Finish",
}


def _key(value: Any) -> str:
    return str(value or "").strip().upper()


def _has_jira_dates(epic: Any) -> bool:
    return bool(str(epic.target_start or "").strip() or str(epic.target_end or "").strip())


def _topmost_roots(graph: CascadeGraph, eligible: Set[str]) -> list[str]:
    """Select the first eligible ancestor on each accepted dependency path."""
    indegree = dict.fromkeys(graph.successors, 0)
    for following in graph.successors.values():
        for key in following:
            indegree[key] += 1
    queue = deque(key for key, degree in indegree.items() if not degree)
    covered: Set[str] = set()
    roots = []
    while queue:
        key = queue.popleft()
        if key in eligible and key not in covered:
            roots.append(key)
        for following in graph.successors[key]:
            if key in covered or key in eligible:
                covered.add(following)
            indegree[following] -= 1
            if not indegree[following]:
                queue.append(following)
    # Accepted links are acyclic. Malformed direct callers remain bounded;
    # cyclic components cannot establish an unambiguous topmost driver.
    return sorted(roots, key=lambda key: (-graph.downstream_counts[key], key))


def build_schedule_drivers(
    plan: RunPlan, root_resource_group: Optional[str] = None,
) -> Dict[str, Any]:
    """Find linked date shifts affecting unfinished work with Jira target dates.

    An edge needs an observed upstream Finish shift and a downstream Start or
    Finish shift. These are candidate branches for review, not causal proof.
    Unchanged Jira target mismatches never establish a shift. Rows missing both
    Jira dates and completed rows can supply context on paths to unfinished
    dated work; dead-end context is omitted. Only unfinished dated descendants
    count as impact. A completed upstream remains relevant when its shifted
    Finish precedes unfinished affected work.
    """
    epics = {_key(key): epic for key, epic in plan.epics.items() if epic.drives_schedule}
    changes: Dict[str, Dict[str, AuditItem]] = {}
    for item in plan.audit_items:
        expected_field = _CHANGE_FIELDS.get(item.category)
        key = _key(item.schedule_key or item.jira_key)
        if expected_field is None or key not in epics:
            continue
        field = item.field or expected_field
        if field != expected_field:
            continue
        old, new = str(item.old_value or "").strip(), str(item.new_value or "").strip()
        if old and new and old != new:
            changes.setdefault(key, {})[field] = item

    successors = {key: set() for key in changes}
    for key, epic in epics.items():
        if key not in changes:
            continue
        if "Finish" in changes[key]:
            successors[key].update(
                target for value in epic.successors
                if (target := _key(value)) in changes and target != key
            )
        # Accepted plans normally store both directions. Support either side
        # for report callers without introducing links outside planned rows.
        for value in epic.predecessors:
            source = _key(value)
            if source != key and "Finish" in changes.get(source, {}):
                successors[source].add(key)

    counted = {
        key for key in changes
        if not epics[key].completed and _has_jira_dates(epics[key])
    }
    # Walk backward from affected dated work once. Keep completed/undated
    # intermediates only when they connect to that work, so unrelated dead-end
    # siblings neither clutter a branch nor consume its bounded visual budget.
    predecessors = {key: set() for key in changes}
    for source, targets in successors.items():
        for target in targets:
            predecessors[target].add(source)
    relevant = set(counted)
    pending = list(counted)
    while pending:
        for source in predecessors[pending.pop()]:
            if source not in relevant:
                relevant.add(source)
                pending.append(source)
    graph = CascadeGraph(
        {key: targets & relevant for key, targets in successors.items() if key in relevant},
        counted_keys=counted,
    )
    eligible = {
        key for key, fields in changes.items()
        if "Finish" in fields
        and _has_jira_dates(epics[key])
        and graph.downstream_counts.get(key, 0) > 0
    }
    root_candidates = {
        key for key in eligible
        if not root_resource_group or epics[key].resource_group == root_resource_group
    }
    return {
        "changes": changes,
        "graph": graph,
        "roots": _topmost_roots(graph, root_candidates),
        "driver_keys": eligible,
    }
