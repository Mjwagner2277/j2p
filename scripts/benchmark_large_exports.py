"""Deterministic report-only benchmarks; never opens Microsoft Project."""

from __future__ import annotations

import argparse
import csv
import json
import platform
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from j2p.config import load_config
from j2p.core import build_run_plan
from j2p.models import AuditItem, PlanEpic, RunPlan
from j2p.reports import render_schedule_cascade_review, write_reports


def write_wide_export(path: Path, count: int, extra_columns: int = 100) -> None:
    """All rows are epics; 100 unused Jira fields exercise wide export parsing."""
    headers = ["Issue key", "Issue Type", "Summary", "Epic Link", "Story Points", "Status", "Fix versions"]
    headers += [f"Custom field (Unused {index})" for index in range(extra_columns)]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for index in range(count):
            writer.writerow([
                f"TEAM-{index}", "Epic", f"Benchmark epic {index}", "", "1", "Open", "Release",
                *[f"value-{index % 100}-{column}" for column in range(extra_columns)],
            ])


def make_cascade_plan(count: int, shape: str) -> RunPlan:
    epics = {}
    audit = []
    for index in range(count):
        key = f"TEAM-{index:06d}"
        if shape == "chain":
            next_indices = [index + 1] if index + 1 < count else []
        else:
            # Two nodes per layer, fully joined to the next layer. The number
            # of edges is linear while the number of possible paths explodes.
            start = (index // 2 + 1) * 2
            next_indices = range(start, min(start + 2, count))
        successors = [f"TEAM-{following:06d}" for following in next_indices]
        epics[key] = PlanEpic(
            key=key, jira_key=key, issue_id=str(index), summary=f"Benchmark epic {index}",
            status="Open", rollup_mode="fixVersion", rollup_key="Release", rollup_name="Release",
            resource_group="Team", key_prefix="TEAM", total_story_points=1,
            completed_story_points=0, logged_hours=0, completed_logged_hours=0,
            story_point_ratio=0, percent_complete=0, in_planning=False, completed=False,
            target_start="2026-01-01", target_end="2026-02-01", successors=successors,
        )
        audit.append(AuditItem(
            severity="Review", category="CascadeBranchDriver" if successors else "CascadingDateChange",
            jira_key=key, schedule_key=key, field="Finish", summary=f"Benchmark epic {index}",
            old_value="2026-01-01", new_value="2026-02-01",
        ))
    return RunPlan("2026-01-01", "synthetic.csv", "fixVersion", {}, {}, {}, epics, audit)


def measured(operation):
    tracemalloc.start()
    started = time.perf_counter()
    try:
        result = operation()
        seconds = time.perf_counter() - started
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    return result, round(seconds, 3), round(peak / 1024 / 1024, 2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[5000, 10000])
    parser.add_argument("--output", type=Path, help="Optional JSON measurements file.")
    args = parser.parse_args(argv)
    if any(size < 2 for size in args.sizes):
        parser.error("--sizes must be at least 2")
    measurements = []
    with tempfile.TemporaryDirectory(prefix="j2p-benchmark-") as tmp:
        root = Path(tmp)
        config = load_config(None, {"resource_groups": {"TEAM": "Team"}, "rollup_modes": {"TEAM": "fixVersion"}})
        for size in args.sizes:
            source = root / f"wide-{size}.csv"
            write_wide_export(source, size)

            def wide_run():
                plan = build_run_plan(source, config)
                return write_reports(plan, root / f"reports-{size}", config)

            paths, seconds, peak = measured(wide_run)
            entry = {"scenario": "wide-export", "issues": size, "columns": 107,
                     "seconds": seconds, "python_peak_mib": peak,
                     "input_bytes": source.stat().st_size,
                     "manager_html_bytes": paths["manager_report"].stat().st_size}
            measurements.append(entry)
            print(json.dumps(entry), flush=True)
            for shape in ("joins", "chain"):
                plan = make_cascade_plan(size, shape)
                html, seconds, peak = measured(lambda: render_schedule_cascade_review(plan, True))
                entry = {"scenario": shape, "issues": size, "seconds": seconds,
                         "python_peak_mib": peak, "cascade_html_bytes": len(html.encode("utf-8")),
                         "visual_cards": html.count('class="cascade-node '),
                         "references": html.count('class="cascade-reference"')}
                measurements.append(entry)
                print(json.dumps(entry), flush=True)
    report = {"python": platform.python_version(), "platform": platform.platform(),
              "timing_includes_tracemalloc": True, "measurements": measurements}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
