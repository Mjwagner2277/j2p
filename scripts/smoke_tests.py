from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, List, Sequence


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_REPORT_FILES = [
    "Manager-Review-Report.html",
    "audit-detail.csv",
    "planned-epics.csv",
    "summary-rollups.csv",
    "dependency-review.csv",
    "FIELD_MAPPING.md",
    "j2p-state.after.json",
]

RETIRED_PATTERNS = {
    ".ps1": "PowerShell implementation files are retired",
    "package.json": "web app package files are not part of this repo",
    "next.config.js": "Next.js files are not part of this repo",
    "next.config.ts": "Next.js files are not part of this repo",
}

RETIRED_DIRECTORIES = {"src", "app", "pages", "public", "components", "node_modules", ".next"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local pre-merge smoke checks for j2p.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Optional output folder. Defaults to a temporary directory.",
    )
    parser.add_argument(
        "--keep-output",
        action="store_true",
        help="Do not delete the temporary smoke output folder.",
    )
    args = parser.parse_args(argv)

    if args.output_dir:
        output_dir = args.output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        return run_smoke(output_dir)

    with tempfile.TemporaryDirectory(prefix="j2p-smoke-") as temp:
        output_dir = Path(temp)
        result = run_smoke(output_dir)
        if args.keep_output:
            kept = Path(tempfile.mkdtemp(prefix="j2p-smoke-kept-"))
            for child in output_dir.iterdir():
                destination = kept / child.name
                if child.is_dir():
                    shutil.copytree(child, destination)
                else:
                    shutil.copy2(child, destination)
            print(f"Kept smoke output: {kept}", flush=True)
        return result


def run_smoke(output_dir: Path) -> int:
    print(f"Repository: {ROOT}", flush=True)
    print(f"Smoke output: {output_dir}", flush=True)

    check_repo_hygiene()
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests"])
    compile_env = os.environ.copy()
    compile_env["PYTHONPYCACHEPREFIX"] = str(output_dir / "pycache")
    run([sys.executable, "-m", "compileall", "j2p", "tests", "scripts"], env=compile_env)
    run([sys.executable, "scripts/generate_large_examples.py", "--check"])

    initial_dir = run_validate(
        output_dir,
        "initial-state",
        "project-wide-jira-initial.csv",
        "config.example.yaml",
        ["--write-state"],
    )
    assert_report_bundle(initial_dir)

    follow_on_dir = run_validate(
        output_dir,
        "follow-on-demo",
        "project-wide-jira-update.csv",
        "config.example.yaml",
        ["--compare-state"],
    )
    assert_report_bundle(follow_on_dir)
    assert_audit_categories(
        follow_on_dir / "audit-detail.csv",
        {
            "ChangedName",
            "CompletedSinceLastUpdate",
            "RollupMove",
            "ExcludedMissingRollup",
            "ExcludedUnknownPrefix",
            "MissingDependencyTarget",
            "AddedEpic",
        },
    )
    assert_report_contains(
        follow_on_dir / "Manager-Review-Report.html",
        [
            "Decision Briefing",
            "Reviewer Action Needed",
            "Rollup Status",
            "Review Type Summary",
            "Report Context",
            "Logged Hours",
            "Hours Accuracy %",
            "Project-Wide Hours Accuracy",
            "Accuracy By Resource Group",
            "Color Key",
            "Project Key Rollup Mapping",
            "Full Planned Epic Rows",
            "<details class=\"detail-block\">",
            "Changed Names",
            "Completed Since Last Update",
            "Dependency Review",
            "TEAM-101",
            "TEAM-107",
        ],
    )

    fixversion_dir = run_validate(
        output_dir,
        "fixversion-demo",
        "project-wide-jira-fixversion.csv",
        "config.fixversion.example.yaml",
        [],
    )
    assert_report_bundle(fixversion_dir)
    assert_audit_categories(fixversion_dir / "audit-detail.csv", {"MultiFixVersionReference"})
    assert_report_contains(
        fixversion_dir / "Manager-Review-Report.html",
        ["Multi-FixVersion Epics", "TEAM-503", "Reference", "Drives Schedule"],
    )

    assert_line_count(ROOT / "examples" / "large-scenario" / "project-wide-jira-baseline-1200.csv", 1200)
    assert_line_count(ROOT / "examples" / "large-scenario" / "project-wide-jira-updated-1200.csv", 1200)
    large_baseline_dir = run_validate(
        output_dir,
        "large-baseline-1200",
        "large-scenario/project-wide-jira-baseline-1200.csv",
        "large-scenario/config.large-example.yaml",
        ["--write-state"],
    )
    assert_report_bundle(large_baseline_dir)
    large_updated_dir = run_validate(
        output_dir,
        "large-updated-1200",
        "large-scenario/project-wide-jira-updated-1200.csv",
        "large-scenario/config.large-example.yaml",
        ["--compare-state"],
    )
    assert_report_bundle(large_updated_dir)
    assert_project_key_outputs(
        large_updated_dir,
        {"CORE", "DATA", "OPS", "PLAT", "UNASSIGNED", "UNK", "WEB"},
    )
    assert_audit_categories(
        large_updated_dir / "audit-detail.csv",
        {
            "AddedEpic",
            "ChangedField",
            "ChangedName",
            "CircularDependencySkipped",
            "CompletedSinceLastUpdate",
            "CsvRowMissingJiraKey",
            "DependencyChange",
            "ExcludedMissingRollup",
            "ExcludedUnknownPrefix",
            "InPlanning",
            "MissingDependencyTarget",
            "MultiFixVersionReference",
            "MultiFixVersionSplit",
            "RollupMove",
            "SelfDependencySkipped",
            "StoryMissingEpicLink",
            "UnmatchedProjectTask",
            "UnparsedDate",
        },
    )
    assert_audit_colors(
        large_updated_dir / "audit-detail.csv",
        {"changed_cell", "review_needed", "dependency_review", "in_planning"},
    )
    assert_report_contains(
        large_updated_dir / "Manager-Review-Report.html",
        [
            "Decision Briefing",
            "Rollup Status",
            "Reviewer Action Needed",
            "Review Type Summary",
            "Report Context",
            "Logged Hours",
            "Hours Accuracy %",
            "Project-Wide Hours Accuracy",
            "Accuracy By Resource Group",
            "Color Case Examples",
            "Project update only",
            "Full Planned Epic Rows",
            "<details class=\"detail-block\">",
            "CORE-1000",
            "CORE-1004",
            "CORE-1980",
            "WEB-2010",
            "DATA-3009",
        ],
    )
    assert_expected_review_cases(
        ROOT / "examples" / "large-scenario" / "expected-review-cases.csv",
        large_updated_dir / "audit-detail.csv",
        large_updated_dir / "Manager-Review-Report.html",
    )

    print("Smoke tests passed.", flush=True)
    return 0


def run_validate(
    output_dir: Path,
    run_id: str,
    csv_name: str,
    config_name: str,
    extra_args: Sequence[str],
) -> Path:
    run(
        [
            sys.executable,
            "-m",
            "j2p",
            "validate",
            "--jira-csv",
            str(ROOT / "examples" / csv_name),
            "--config",
            str(ROOT / "examples" / config_name),
            "--output-dir",
            str(output_dir),
            "--run-id",
            run_id,
            *extra_args,
        ]
    )
    return output_dir / f"j2p-run-{run_id}"


def check_repo_hygiene() -> None:
    violations: List[str] = []
    for path in ROOT.rglob("*"):
        if ".git" in path.parts:
            continue
        if path.is_dir() and path.name in RETIRED_DIRECTORIES:
            violations.append(f"{path.relative_to(ROOT)}: retired directory")
            continue
        if path.is_file():
            reason = RETIRED_PATTERNS.get(path.name) or RETIRED_PATTERNS.get(path.suffix)
            if reason:
                violations.append(f"{path.relative_to(ROOT)}: {reason}")
    if violations:
        details = "\n".join(f"  - {item}" for item in violations)
        raise AssertionError(f"Repository hygiene check failed:\n{details}")


def assert_report_bundle(run_dir: Path) -> None:
    missing = [name for name in REQUIRED_REPORT_FILES if not (run_dir / name).exists()]
    if not (run_dir / "by-project-key" / "index.csv").exists():
        missing.append("by-project-key/index.csv")
    if missing:
        raise AssertionError(f"Missing report files in {run_dir}: {', '.join(missing)}")


def assert_audit_categories(path: Path, expected: Iterable[str]) -> None:
    with path.open("r", encoding="utf-8", newline="") as handle:
        categories = {row["category"] for row in csv.DictReader(handle)}
    missing = set(expected) - categories
    if missing:
        raise AssertionError(f"{path} is missing audit categories: {', '.join(sorted(missing))}")


def assert_audit_colors(path: Path, expected: Iterable[str]) -> None:
    with path.open("r", encoding="utf-8", newline="") as handle:
        colors = {row["color"] for row in csv.DictReader(handle) if row.get("color")}
    missing = set(expected) - colors
    if missing:
        raise AssertionError(f"{path} is missing audit colors: {', '.join(sorted(missing))}")


def assert_project_key_outputs(run_dir: Path, expected_project_keys: Iterable[str]) -> None:
    index_path = run_dir / "by-project-key" / "index.csv"
    with index_path.open("r", encoding="utf-8", newline="") as handle:
        project_keys = {row["project_key"] for row in csv.DictReader(handle)}
    missing = set(expected_project_keys) - project_keys
    if missing:
        raise AssertionError(f"{index_path} is missing project keys: {', '.join(sorted(missing))}")


def assert_line_count(path: Path, expected_count: int) -> None:
    with path.open("r", encoding="utf-8") as handle:
        actual_count = sum(1 for _line in handle)
    if actual_count != expected_count:
        raise AssertionError(f"{path} has {actual_count} lines; expected {expected_count}.")


def assert_expected_review_cases(case_path: Path, audit_path: Path, report_path: Path) -> None:
    project_only = {"CriticalPathCascadeRoot", "CascadingDateChange"}
    with case_path.open("r", encoding="utf-8", newline="") as handle:
        cases = list(csv.DictReader(handle))
    if len(cases) < 15:
        raise AssertionError(f"{case_path} should document at least 15 teaching cases.")

    with audit_path.open("r", encoding="utf-8", newline="") as handle:
        audit_rows = list(csv.DictReader(handle))
    report_text = report_path.read_text(encoding="utf-8")

    missing = []
    for case in cases:
        jira_key = case["jira_key"]
        category = case["expected_category"]
        if category in project_only:
            if jira_key not in report_text:
                missing.append(f"{jira_key}/{category} should be described in the report")
            continue
        if not any(row["jira_key"] == jira_key and row["category"] == category for row in audit_rows):
            missing.append(f"{jira_key}/{category}")
    if missing:
        raise AssertionError(f"Large scenario is missing expected review cases: {', '.join(missing)}")


def assert_report_contains(path: Path, expected: Iterable[str]) -> None:
    text = path.read_text(encoding="utf-8")
    missing = [item for item in expected if item not in text]
    if missing:
        raise AssertionError(f"{path} is missing expected text: {', '.join(missing)}")
    forbidden = ["<script src=", "<link rel=", "http://", "https://"]
    found_forbidden = [item for item in forbidden if item in text]
    if found_forbidden:
        raise AssertionError(
            f"{path} should be self-contained but contains: {', '.join(found_forbidden)}"
        )


def run(command: Sequence[str], env: dict[str, str] | None = None) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


if __name__ == "__main__":
    raise SystemExit(main())
