"""Dependency graph handling for planned epic rows."""

from __future__ import annotations

from typing import Dict, List, Set, Tuple

from .models import AuditItem, JiraIssue, PlanEpic


def apply_dependencies(
    planned_epics: Dict[str, PlanEpic],
    all_epics: List[JiraIssue],
    audit: List[AuditItem],
) -> None:
    row_keys_by_jira: Dict[str, List[str]] = {}
    driving_keys_by_jira: Dict[str, List[str]] = {}
    for schedule_key, planned_epic in planned_epics.items():
        jira_key = planned_epic.jira_key or planned_epic.key
        row_keys_by_jira.setdefault(jira_key, []).append(schedule_key)
        if planned_epic.drives_schedule:
            driving_keys_by_jira.setdefault(jira_key, []).append(schedule_key)

    included_keys = set(driving_keys_by_jira)
    raw_epics = {epic.key: epic for epic in all_epics}
    candidate_edges: List[Tuple[str, str, str]] = []
    for epic_key, epic in raw_epics.items():
        if epic_key not in row_keys_by_jira:
            continue
        for predecessor_key in sorted(epic.predecessors):
            candidate_edges.append((predecessor_key, epic_key, "blocked by"))
        for successor_key in sorted(epic.successors):
            candidate_edges.append((epic_key, successor_key, "blocks"))

    accepted: Set[Tuple[str, str]] = set()
    driving_schedule_keys = {key for keys in driving_keys_by_jira.values() for key in keys}
    graph: Dict[str, Set[str]] = {key: set() for key in driving_schedule_keys}
    seen: Set[Tuple[str, str]] = set()
    for predecessor_key, successor_key, relation in sorted(candidate_edges):
        edge = (predecessor_key, successor_key)
        if edge in seen:
            continue
        seen.add(edge)
        source_key = successor_key if relation == "blocked by" else predecessor_key
        source_schedule_key = primary_planned_key(row_keys_by_jira, source_key)
        source_epic = planned_epics.get(source_schedule_key)
        if predecessor_key == successor_key:
            add_dependency_review(
                planned_epics,
                source_schedule_key,
                f"Skipped self-dependency {predecessor_key} -> {successor_key}.",
            )
            audit.append(
                AuditItem(
                    "Warning",
                    "SelfDependencySkipped",
                    jira_key=source_key,
                    schedule_key=source_schedule_key,
                    issue_type="Epic" if source_epic else "",
                    summary=source_epic.summary if source_epic else "",
                    field="Dependency Review",
                    color="dependency_review",
                    message=f"Skipped self-dependency {predecessor_key} -> {successor_key}.",
                    reviewer_action="Correct the Jira blocker link.",
                    source_row=source_epic.source_row if source_epic else None,
                )
            )
            continue
        if predecessor_key not in included_keys or successor_key not in included_keys:
            missing = predecessor_key if predecessor_key not in included_keys else successor_key
            add_dependency_review(
                planned_epics,
                source_schedule_key,
                f"Missing dependency target {missing}.",
            )
            audit.append(
                AuditItem(
                    "Warning",
                    "MissingDependencyTarget",
                    jira_key=source_key,
                    schedule_key=source_schedule_key,
                    issue_type="Epic" if source_epic else "",
                    summary=source_epic.summary if source_epic else "",
                    field="Dependency Review",
                    new_value=missing,
                    color="dependency_review",
                    message=f"Dependency target '{missing}' is not an included epic.",
                    reviewer_action="Confirm the target epic is in the Jira export and meets rollup/resource rules.",
                    source_row=source_epic.source_row if source_epic else None,
                )
            )
            continue

        for predecessor_schedule_key in driving_keys_by_jira[predecessor_key]:
            for successor_schedule_key in driving_keys_by_jira[successor_key]:
                if creates_cycle(graph, predecessor_schedule_key, successor_schedule_key):
                    add_dependency_review(
                        planned_epics,
                        source_schedule_key,
                        f"Skipped circular dependency {predecessor_key} -> {successor_key}.",
                    )
                    audit.append(
                        AuditItem(
                            "Warning",
                            "CircularDependencySkipped",
                            jira_key=source_key,
                            schedule_key=source_schedule_key,
                            issue_type="Epic" if source_epic else "",
                            summary=source_epic.summary if source_epic else "",
                            field="Dependency Review",
                            color="dependency_review",
                            message=f"Skipped circular dependency {predecessor_key} -> {successor_key}.",
                            reviewer_action="Resolve the circular Jira blocker relationship.",
                            source_row=source_epic.source_row if source_epic else None,
                        )
                    )
                    continue
                graph[predecessor_schedule_key].add(successor_schedule_key)
                accepted.add((predecessor_schedule_key, successor_schedule_key))

    for predecessor_schedule_key, successor_schedule_key in sorted(accepted):
        planned_epics[successor_schedule_key].predecessors.append(predecessor_schedule_key)
        planned_epics[predecessor_schedule_key].successors.append(successor_schedule_key)

    for epic in planned_epics.values():
        if epic.drives_schedule:
            continue
        primary = planned_epics.get(epic.primary_schedule_key)
        if primary:
            add_dependency_review(
                planned_epics,
                epic.key,
                (
                    f"Reference row only. Schedule logic is driven by {primary.key} "
                    f"under fixVersion {primary.fix_version or primary.rollup_key}."
                )
            )


def add_dependency_review(epics: Dict[str, PlanEpic], key: str, note: str) -> None:
    epic = epics.get(key)
    if not epic:
        return
    if epic.dependency_review:
        epic.dependency_review += " "
    epic.dependency_review += note


def primary_planned_key(row_keys_by_jira: Dict[str, List[str]], jira_key: str) -> str:
    keys = row_keys_by_jira.get(jira_key, [])
    return keys[0] if keys else jira_key


def creates_cycle(graph: Dict[str, Set[str]], predecessor_key: str, successor_key: str) -> bool:
    stack = [successor_key]
    seen: Set[str] = set()
    while stack:
        current = stack.pop()
        if current == predecessor_key:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(graph.get(current, set()))
    return False
