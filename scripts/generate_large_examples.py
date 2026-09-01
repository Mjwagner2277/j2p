from __future__ import annotations

import argparse
import csv
import difflib
import io
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Sequence


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "examples" / "large-scenario"
BASELINE_CSV = OUTPUT_DIR / "project-wide-jira-baseline-1200.csv"
UPDATED_CSV = OUTPUT_DIR / "project-wide-jira-updated-1200.csv"
LINE_TARGET = 1200
DATA_ROW_TARGET = LINE_TARGET - 1
STORY_ROW_TARGET = 1000
PREDECESSOR_COVERAGE_TARGET = 0.62
PREDECESSOR_TARGET_MINIMUM_COUNT = 112

CSV_COLUMNS = [
    "Issue key",
    "Issue id",
    "Issue Type",
    "Summary",
    "Epic Link",
    "Parent",
    "Fix versions",
    "Story Points",
    "Logged Hours",
    "Status",
    "Resolution",
    "Target start",
    "Target end",
    "Outward issue link (Blocks)",
    "Inward issue link (Blocks)",
]

INITIATIVES = [
    ("INIT-100", "Customer Identity And Access"),
    ("INIT-200", "Revenue And Billing Modernization"),
    ("INIT-300", "Data Foundations"),
    ("INIT-400", "Operational Readiness"),
    ("INIT-500", "Web Experience Refresh"),
    ("INIT-600", "Program Reporting"),
    ("INIT-700", "Security Hardening"),
    ("INIT-800", "Integrations Roadmap"),
    ("INIT-900", "Planning Backlog"),
    ("INIT-1000", "Partner Enablement"),
    ("INIT-1100", "Compliance Controls"),
    ("INIT-1200", "Search And Discovery"),
    ("INIT-1300", "Mobile Architecture"),
    ("INIT-1400", "Release Governance"),
    ("INIT-1500", "Forecasting Program"),
]


@dataclass(frozen=True)
class PrefixSpec:
    prefix: str
    first_number: int
    count: int
    rollup_mode: str
    parents: Sequence[str]
    fix_versions: Sequence[str]


PREFIX_SPECS = [
    PrefixSpec("CORE", 1000, 50, "initiative", ("INIT-100", "INIT-200", "INIT-700", "INIT-800"), ()),
    PrefixSpec("WEB", 2000, 45, "initiative", ("INIT-500", "INIT-100", "INIT-1200"), ()),
    PrefixSpec("DATA", 3000, 35, "initiative", ("INIT-300", "INIT-600", "INIT-1500"), ()),
    PrefixSpec(
        "PLAT",
        4000,
        30,
        "fixVersion",
        ("INIT-400",),
        ("Platform Q1", "Platform Q2", "Shared Services 2026"),
    ),
    PrefixSpec("OPS", 5000, 20, "fixVersion", ("INIT-400",), ("Operations Q1", "Operations Q2")),
    PrefixSpec("UNK", 9000, 4, "initiative", ("INIT-100",), ()),
]

CURATED_ORDER = [
    "CORE-1000",
    "CORE-1001",
    "CORE-1002",
    "CORE-1003",
    "CORE-1004",
    "CORE-1005",
    "CORE-1006",
    "CORE-1007",
    "CORE-1048",
    "CORE-1980",
    "CORE-1049",
    "WEB-2008",
    "WEB-2010",
    "DATA-3008",
    "DATA-3009",
    "DATA-3034",
    "PLAT-4028",
    "PLAT-4029",
    "OPS-5019",
    "UNK-9000",
]

CURATED_SORT = {key: index for index, key in enumerate(CURATED_ORDER)}
CURATED_SORT["CORE-1980"] = CURATED_SORT["CORE-1048"]

BASELINE_CURATED_SUMMARIES = {
    "CORE-1000": "Client Walkthrough - Identity API name before Jira rename",
    "CORE-1001": "Client Walkthrough - Login service progress example",
    "CORE-1002": "Client Walkthrough - Access audit completion example",
    "CORE-1003": "Client Walkthrough - Billing integration rollup move example",
    "CORE-1004": "Client Walkthrough - Payment cutover schedule driver candidate",
    "CORE-1005": "Client Walkthrough - Payment cutover downstream dependency",
    "CORE-1006": "Client Walkthrough - Vendor gateway missing dependency example",
    "CORE-1007": "Client Walkthrough - Account settings dependency change example",
    "CORE-1048": "Client Walkthrough - Legacy schedule item present only in baseline",
    "CORE-1049": "Client Walkthrough - Marketplace experiment missing rollup example",
    "WEB-2008": "Client Walkthrough - Web launch self dependency example",
    "WEB-2010": "Client Walkthrough - Design system discovery with no pointed child work",
    "DATA-3008": "Client Walkthrough - Warehouse migration circular dependency A",
    "DATA-3009": "Client Walkthrough - Warehouse migration circular dependency B",
    "DATA-3034": "Client Walkthrough - Forecast refresh invalid date example",
    "PLAT-4028": "Client Walkthrough - Qualification software reference example",
    "PLAT-4029": "Client Walkthrough - Deployment runner fixVersion example",
    "OPS-5019": "Client Walkthrough - Shop deliverable split fixVersion example",
    "UNK-9000": "Client Walkthrough - Unknown team prefix example",
}

UPDATED_CURATED_SUMMARIES = {
    **BASELINE_CURATED_SUMMARIES,
    "CORE-1000": "Client Walkthrough - Identity API renamed in Jira",
    "CORE-1980": "Client Walkthrough - Mobile onboarding added after baseline",
}

SCALE_TOPICS = {
    "CORE": [
        "Profile service rollout",
        "Entitlement cleanup",
        "Notification routing",
        "Session management",
        "Account recovery",
        "API gateway refresh",
        "Consent preference updates",
        "Fraud signal ingestion",
    ],
    "WEB": [
        "Search results tuning",
        "Checkout web flow",
        "Homepage personalization",
        "Accessibility remediation",
        "Content publishing",
        "Navigation refresh",
        "Localization pass",
        "Experiment cleanup",
    ],
    "DATA": [
        "Warehouse model refresh",
        "Pipeline observability",
        "Metrics certification",
        "Forecast data mart",
        "Streaming ingestion",
        "Retention model update",
        "Quality rule expansion",
        "Analytics contract cleanup",
    ],
    "PLAT": [
        "Kubernetes platform upgrade",
        "Build runner rotation",
        "Secrets management",
        "Shared logging",
        "Service mesh controls",
        "Environment provisioning",
        "Artifact retention",
        "Deployment guardrails",
    ],
    "OPS": [
        "Release checklist automation",
        "Incident readiness",
        "Support workflow routing",
        "Change advisory preparation",
        "Runbook modernization",
        "Capacity review",
        "Monitoring handoff",
        "Service review cadence",
    ],
    "UNK": [
        "Unmapped intake",
        "Legacy partner request",
        "Triage placeholder",
        "Unassigned portfolio item",
    ],
}

STORY_PHASES = [
    "Discovery",
    "Implementation",
    "Validation",
    "Documentation",
    "Release readiness",
    "Stakeholder review",
    "Operational handoff",
    "Post-release check",
]

AUTO_DEPENDENCY_EXCLUDED_KEYS = set(CURATED_ORDER)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate authored 1,200-line Jira CSV examples.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify generated CSVs match the committed fixtures without writing files.",
    )
    args = parser.parse_args(argv)

    outputs = {
        BASELINE_CSV: csv_text(build_rows("baseline")),
        UPDATED_CSV: csv_text(build_rows("updated")),
    }
    for path, text in outputs.items():
        assert_line_count(path, text)
        if args.check:
            assert_matches_existing(path, text)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    action = "Verified" if args.check else "Generated"
    print(f"{action} {len(outputs)} authored large example CSV files with {LINE_TARGET} lines each.")
    return 0


def build_rows(variant: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    rows.extend(initiative_rows())
    epics = epic_rows(variant)
    rows.extend(epics)
    rows.extend(story_rows(epics, variant))
    if len(rows) != DATA_ROW_TARGET:
        raise AssertionError(f"{variant} generated {len(rows)} rows; expected {DATA_ROW_TARGET}.")
    return rows


def initiative_rows() -> List[Dict[str, str]]:
    rows = []
    for offset, (key, summary) in enumerate(INITIATIVES):
        start = date(2026, 1, 5) + timedelta(days=offset * 14)
        rows.append(
            row(
                key=key,
                issue_id=str(100000 + offset),
                issue_type="Initiative",
                summary=summary,
                fix_versions="Portfolio 2026",
                status="In Progress",
                target_start=start.isoformat(),
                target_end=(start + timedelta(days=180)).isoformat(),
            )
        )
    return rows


def epic_rows(variant: str) -> List[Dict[str, str]]:
    epics = []
    sequence = 0
    for spec in PREFIX_SPECS:
        for offset in range(spec.count):
            number = spec.first_number + offset
            key = f"{spec.prefix}-{number}"
            if variant == "updated" and key == "CORE-1048":
                key = "CORE-1980"
            epics.append(make_epic(spec, key, offset, sequence, variant))
            sequence += 1
    epics = sorted(epics, key=epic_sort_key)
    apply_scale_predecessors(epics)
    return epics


def apply_scale_predecessors(epics: List[Dict[str, str]]) -> None:
    scheduled_epics = [epic for epic in epics if is_schedule_included_epic(epic)]
    target_count = max(
        PREDECESSOR_TARGET_MINIMUM_COUNT,
        round_up(len(scheduled_epics) * PREDECESSOR_COVERAGE_TARGET),
    )
    groups: Dict[str, List[Dict[str, str]]] = {}
    for epic in epics:
        if is_auto_dependency_candidate(epic):
            groups.setdefault(project_key(epic["Issue key"]), []).append(epic)

    for group in groups.values():
        group.sort(key=lambda epic: jira_number(epic["Issue key"]))

    created = 0
    positions = {prefix: 1 for prefix in groups}
    prefixes = [spec.prefix for spec in PREFIX_SPECS if spec.prefix in groups]
    while created < target_count:
        progressed = False
        for prefix in prefixes:
            position = positions[prefix]
            group = groups[prefix]
            if position >= len(group):
                continue
            predecessor = group[position - 1]["Issue key"]
            target = group[position]
            target["Inward issue link (Blocks)"] = predecessor
            positions[prefix] += 1
            created += 1
            progressed = True
            if created >= target_count:
                break
        if not progressed:
            break


def is_auto_dependency_candidate(epic: Dict[str, str]) -> bool:
    key = epic["Issue key"]
    return (
        is_schedule_included_epic(epic)
        and key not in AUTO_DEPENDENCY_EXCLUDED_KEYS
        and not epic["Inward issue link (Blocks)"]
        and not epic["Outward issue link (Blocks)"]
    )


def is_schedule_included_epic(epic: Dict[str, str]) -> bool:
    key = epic["Issue key"]
    prefix = project_key(key)
    if prefix == "UNK":
        return False
    if prefix in {"CORE", "WEB", "DATA"}:
        return bool(epic["Parent"])
    if prefix in {"PLAT", "OPS"}:
        return bool(epic["Fix versions"])
    return False


def epic_sort_key(epic: Dict[str, str]) -> tuple[int, str]:
    key = epic["Issue key"]
    if key in CURATED_SORT:
        return CURATED_SORT[key], key
    return len(CURATED_SORT) + stable_number(key), key


def make_epic(
    spec: PrefixSpec,
    key: str,
    offset: int,
    sequence: int,
    variant: str,
) -> Dict[str, str]:
    parent = spec.parents[offset % len(spec.parents)]
    fix_version = (
        spec.fix_versions[offset % len(spec.fix_versions)]
        if spec.fix_versions
        else "Portfolio 2026"
    )
    status = "In Progress" if offset % 3 else "To Do"
    resolution = ""
    start = date(2026, 2, 2) + timedelta(days=(sequence % 28) * 7 + (sequence // 28) * 21)
    finish = start + timedelta(days=28 + (offset % 5) * 7)
    summary = epic_summary(spec.prefix, key, offset, variant)
    successors = ""
    predecessors = ""

    if key == "CORE-1002":
        status = "Done" if variant == "updated" else "In Progress"
        resolution = "Done" if variant == "updated" else ""
    if key == "CORE-1003":
        parent = "INIT-200" if variant == "updated" else "INIT-100"
    if key == "CORE-1004":
        successors = "CORE-1005"
        if variant == "updated":
            finish += timedelta(days=35)
    if key == "CORE-1006" and variant == "updated":
        predecessors = "EXT-999"
    if key == "CORE-1007" and variant == "updated":
        predecessors = "CORE-1001"
    if key == "CORE-1049" and variant == "updated":
        parent = ""
    if key == "CORE-1980":
        parent = "INIT-100"
        status = "To Do"
        start = date(2026, 9, 8)
        finish = date(2026, 10, 20)
    if key == "CORE-1000" and variant == "updated":
        finish += timedelta(days=7)
    if key == "CORE-1001" and variant == "updated":
        start += timedelta(days=3)
        finish += timedelta(days=14)
    if key == "WEB-2008" and variant == "updated":
        successors = "WEB-2008"
    if key == "WEB-2010":
        status = "To Do"
    if key == "DATA-3008" and variant == "updated":
        successors = "DATA-3009"
    if key == "DATA-3009" and variant == "updated":
        successors = "DATA-3008"
    if key == "DATA-3034" and variant == "updated":
        finish_value = "not-a-date"
    else:
        finish_value = finish.isoformat()
    if key == "PLAT-4028":
        fix_version = "Qualification Event 1"
    if key == "PLAT-4028" and variant == "updated":
        fix_version = "Qualification Event 1;Shop Deliverable A"
    if key == "PLAT-4029" and variant == "updated":
        fix_version = ""
    if key == "OPS-5019":
        fix_version = "Qualification Event 2"
    if key == "OPS-5019" and variant == "updated":
        fix_version = "Qualification Event 2;Shop Deliverable B"

    return row(
        key=key,
        issue_id=str(200000 + sequence),
        issue_type="Epic",
        summary=summary,
        parent=parent,
        fix_versions=fix_version,
        status=status,
        resolution=resolution,
        target_start=start.isoformat(),
        target_end=finish_value,
        successors=successors,
        predecessors=predecessors,
    )


def epic_summary(prefix: str, key: str, offset: int, variant: str) -> str:
    summaries = UPDATED_CURATED_SUMMARIES if variant == "updated" else BASELINE_CURATED_SUMMARIES
    if key in summaries:
        return summaries[key]
    topics = SCALE_TOPICS[prefix]
    topic = topics[offset % len(topics)]
    return f"Scale Rows - {team_name(prefix)} - {topic} wave {offset + 1:02d}"


def story_rows(epics: Sequence[Dict[str, str]], variant: str) -> List[Dict[str, str]]:
    stories = [
        row(
            key="",
            issue_id="399998",
            issue_type="Story",
            summary="Client Walkthrough - Blank-key story row for CSV quality warning",
            story_points="3",
            logged_hours="0.5",
            status="To Do",
        ),
        row(
            key="CORE-899999",
            issue_id="399999",
            issue_type="Story",
            summary="Client Walkthrough - Orphan child story with no Epic Link",
            story_points="5",
            logged_hours="1h 15m",
            status="Done",
            resolution="Done",
        ),
    ]
    eligible_epics = [item for item in epics if has_child_stories(item, variant)]
    counts = story_counts([item["Issue key"] for item in eligible_epics])
    sequence = 0
    for epic in eligible_epics:
        epic_key = epic["Issue key"]
        for local_index in range(counts[epic_key]):
            sequence += 1
            points = story_points(epic_key, local_index)
            status = story_status(epic_key, local_index, variant)
            prefix = epic_key.split("-", 1)[0]
            issue_type = ("Story", "Task", "Bug")[sequence % 3]
            stories.append(
                row(
                    key=f"{prefix}-{700000 + sequence}",
                    issue_id=str(400000 + sequence),
                    issue_type=issue_type,
                    summary=child_summary(issue_type, epic["Summary"], local_index),
                    epic_link=epic_key,
                    fix_versions=epic.get("Fix versions", "Portfolio 2026"),
                    story_points=str(points),
                    logged_hours=story_logged_hours(epic_key, local_index, variant),
                    status=status,
                    resolution="Done" if status == "Done" else "",
                )
            )
    if len(stories) != STORY_ROW_TARGET:
        raise AssertionError(f"{variant} generated {len(stories)} story rows; expected {STORY_ROW_TARGET}.")
    return stories


def has_child_stories(epic: Dict[str, str], variant: str) -> bool:
    key = epic["Issue key"]
    if key in {"WEB-2010"}:
        return False
    if key.startswith("UNK-"):
        return False
    if variant == "updated" and key in {"CORE-1049", "PLAT-4029"}:
        return False
    return True


def story_counts(epic_keys: Sequence[str]) -> Dict[str, int]:
    counts = {key: 5 + (stable_number(key) % 2) for key in epic_keys}
    remaining = STORY_ROW_TARGET - 2 - sum(counts.values())
    if remaining < 0:
        raise AssertionError("Base child story distribution exceeded target row count.")
    index = 0
    while remaining:
        counts[epic_keys[index % len(epic_keys)]] += 1
        remaining -= 1
        index += 1
    return counts


def story_status(epic_key: str, local_index: int, variant: str) -> str:
    if epic_key == "CORE-1001":
        if variant == "baseline":
            return "Done" if local_index == 0 else "To Do"
        return "Done" if local_index < 4 else "In Progress"
    if epic_key == "CORE-1002" and variant == "updated":
        return "Done"
    if epic_key == "CORE-1980":
        return "To Do"
    pattern = ("Done", "In Progress", "To Do", "Done", "To Do", "In Progress")
    return pattern[(stable_number(epic_key) + local_index) % len(pattern)]


def story_points(epic_key: str, local_index: int) -> int:
    values = (1, 2, 3, 5, 8)
    return values[(stable_number(epic_key) + local_index) % len(values)]


def story_logged_hours(epic_key: str, local_index: int, variant: str) -> str:
    base = ((stable_number(epic_key) + local_index * 3) % 13) / 2
    if epic_key == "CORE-1001" and variant == "updated":
        base += 2
    if epic_key == "CORE-1980":
        base = local_index / 4
    if local_index % 11 == 0:
        whole_hours = int(base)
        minutes = 30 if base % 1 else 0
        if minutes:
            return f"{whole_hours}h {minutes}m"
        return f"{whole_hours}h"
    return f"{base:.2f}".rstrip("0").rstrip(".")


def child_summary(issue_type: str, epic_summary_text: str, local_index: int) -> str:
    phase = STORY_PHASES[local_index % len(STORY_PHASES)]
    cleaned_epic = epic_summary_text.replace("Client Walkthrough - ", "")
    return f"{phase} {issue_type.lower()} for {cleaned_epic}"


def team_name(prefix: str) -> str:
    return {
        "CORE": "Core Product",
        "WEB": "Web Experience",
        "DATA": "Data Platform",
        "PLAT": "Platform Services",
        "OPS": "Operations",
        "UNK": "Unknown Prefix",
    }.get(prefix, prefix)


def stable_number(value: str) -> int:
    return sum((index + 1) * ord(character) for index, character in enumerate(value))


def project_key(value: str) -> str:
    return value.split("-", 1)[0].upper() if "-" in value else value.upper()


def jira_number(value: str) -> int:
    try:
        return int(value.split("-", 1)[1])
    except (IndexError, ValueError):
        return stable_number(value)


def round_up(value: float) -> int:
    whole = int(value)
    return whole if value == whole else whole + 1


def row(
    *,
    key: str,
    issue_id: str,
    issue_type: str,
    summary: str,
    epic_link: str = "",
    parent: str = "",
    fix_versions: str = "",
    story_points: str = "",
    logged_hours: str = "",
    status: str = "",
    resolution: str = "",
    target_start: str = "",
    target_end: str = "",
    successors: str = "",
    predecessors: str = "",
) -> Dict[str, str]:
    return {
        "Issue key": key,
        "Issue id": issue_id,
        "Issue Type": issue_type,
        "Summary": summary,
        "Epic Link": epic_link,
        "Parent": parent,
        "Fix versions": fix_versions,
        "Story Points": story_points,
        "Logged Hours": logged_hours,
        "Status": status,
        "Resolution": resolution,
        "Target start": target_start,
        "Target end": target_end,
        "Outward issue link (Blocks)": successors,
        "Inward issue link (Blocks)": predecessors,
    }


def csv_text(rows: Iterable[Dict[str, str]]) -> str:
    handle = io.StringIO()
    writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return handle.getvalue()


def assert_line_count(path: Path, text: str) -> None:
    line_count = len(text.splitlines())
    if line_count != LINE_TARGET:
        raise AssertionError(f"{path} has {line_count} lines; expected {LINE_TARGET}.")


def assert_matches_existing(path: Path, expected: str) -> None:
    if not path.exists():
        raise AssertionError(f"Missing generated fixture: {path}")
    actual = path.read_text(encoding="utf-8")
    if actual == expected:
        return
    diff = "".join(
        difflib.unified_diff(
            actual.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=str(path),
            tofile=f"{path} (generated)",
            n=3,
        )
    )
    raise AssertionError(f"Generated fixture is out of date:\n{diff[:4000]}")


if __name__ == "__main__":
    raise SystemExit(main())
