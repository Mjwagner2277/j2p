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

CSV_COLUMNS = [
    "Issue key",
    "Issue id",
    "Issue Type",
    "Summary",
    "Epic Link",
    "Parent",
    "Fix versions",
    "Story Points",
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
    PrefixSpec("PLAT", 4000, 30, "fixVersion", ("INIT-400",), ("Platform Q1", "Platform Q2", "Shared Services 2026")),
    PrefixSpec("OPS", 5000, 20, "fixVersion", ("INIT-400",), ("Operations Q1", "Operations Q2")),
    PrefixSpec("UNK", 9000, 4, "initiative", ("INIT-100",), ()),
]

SPECIAL_DESCRIPTIONS = {
    "CORE-1000": "green changed name",
    "CORE-1001": "green percent/date changes",
    "CORE-1002": "completed since last update",
    "CORE-1003": "rollup move",
    "CORE-1004": "intended red schedule cascade root when run through Microsoft Project",
    "CORE-1005": "intended downstream schedule cascade item",
    "CORE-1006": "blue missing dependency target",
    "CORE-1007": "green dependency change",
    "CORE-1048": "baseline-only unmatched Project task",
    "CORE-1049": "yellow missing initiative rollup in updated CSV",
    "WEB-2008": "blue self dependency",
    "WEB-2010": "green-gray in planning",
    "DATA-3008": "blue circular dependency pair",
    "DATA-3009": "blue circular dependency pair",
    "DATA-3034": "unparsed date warning",
    "PLAT-4029": "yellow missing fixVersion in updated CSV",
    "OPS-5019": "yellow multiple fixVersions in updated CSV",
    "CORE-1980": "green added epic",
    "UNK-9000": "yellow unknown Jira key prefix",
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic 1,200-line Jira CSV examples.")
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
    print(f"{action} {len(outputs)} large example CSV files with {LINE_TARGET} lines each.")
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
    return epics


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
    summary = f"{team_name(spec.prefix)} epic {offset + 1:02d}"
    successors = ""
    predecessors = ""

    if key in SPECIAL_DESCRIPTIONS:
        summary = f"{team_name(spec.prefix)} {SPECIAL_DESCRIPTIONS[key]}"

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
        summary += " - renamed in Jira"
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
    if key == "PLAT-4029" and variant == "updated":
        fix_version = ""
    if key == "OPS-5019" and variant == "updated":
        fix_version = "Operations Q1;Operations Q2"

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


def story_rows(epics: Sequence[Dict[str, str]], variant: str) -> List[Dict[str, str]]:
    stories = [
        row(
            key="",
            issue_id="399998",
            issue_type="Story",
            summary="Blank-key row included to show CSV row missing Jira key handling",
            story_points="3",
            status="To Do",
        ),
        row(
            key="CORE-899999",
            issue_id="399999",
            issue_type="Story",
            summary="Orphan story with no Epic Link",
            story_points="5",
            status="Done",
            resolution="Done",
        ),
    ]
    eligible_keys = [item["Issue key"] for item in epics if has_child_stories(item, variant)]
    counts = story_counts(eligible_keys)
    sequence = 0
    for epic_key in eligible_keys:
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
                    summary=f"{issue_type} {local_index + 1} for {epic_key}",
                    epic_link=epic_key,
                    fix_versions="Portfolio 2026",
                    story_points=str(points),
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
    if variant == "updated" and key in {"CORE-1049", "PLAT-4029", "OPS-5019"}:
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


def team_name(prefix: str) -> str:
    return {
        "CORE": "Core platform",
        "WEB": "Web experience",
        "DATA": "Data platform",
        "PLAT": "Platform services",
        "OPS": "Operations",
        "UNK": "Unknown prefix",
    }.get(prefix, prefix)


def stable_number(value: str) -> int:
    return sum((index + 1) * ord(character) for index, character in enumerate(value))


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
