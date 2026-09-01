"""Command-line interface for j2p."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .config import ConfigError, load_config
from .core import J2PError, build_run_plan, run_plan_to_state, snapshots_from_state, write_json
from .project import (
    ProjectAutomationError,
    apply_plan_to_sandbox,
    create_project_from_plan,
    prepare_sandbox_copy,
    snapshot_project_file,
)
from .reports import write_reports


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            return run_validate(args)
        if args.command == "update":
            return run_update(args)
        if args.command == "create":
            return run_create(args)
    except (ConfigError, J2PError, ProjectAutomationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: interrupted", file=sys.stderr)
        return 130
    parser.print_help()
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="j2p",
        description="Create Microsoft Project review sandboxes from project-wide Jira CSV exports.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate",
        help="Parse Jira CSV and generate manager/audit reports without opening Microsoft Project.",
    )
    add_common_args(validate)
    validate.add_argument(
        "--compare-state",
        action="store_true",
        help="Compare against the persistent state file if it exists.",
    )
    validate.add_argument(
        "--write-state",
        action="store_true",
        help="Write the persistent state file after validation.",
    )

    update = subparsers.add_parser(
        "update",
        help="Copy the main MPP to a timestamped sandbox and apply Jira updates to the sandbox.",
    )
    add_common_args(update)
    update.add_argument("--main-project", required=True, type=Path, help="Source-of-truth MPP file.")
    update.add_argument(
        "--comparison-source",
        choices=["main", "previous-sandbox", "state"],
        default="main",
        help="Baseline used for changed/completed/unmatched reporting. Default: main.",
    )
    update.add_argument(
        "--previous-sandbox",
        type=Path,
        help="Previous sandbox MPP used when --comparison-source previous-sandbox is selected.",
    )
    update.add_argument(
        "--debug-visible",
        action="store_true",
        help="Debug only: ask Microsoft Project to show its window while automation runs.",
    )
    update.add_argument(
        "--visible",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    create = subparsers.add_parser(
        "create",
        help="Create an initial MPP from Jira CSV. Intended for first setup only.",
    )
    add_common_args(create)
    create.add_argument(
        "--output-project-name",
        default="j2p-initial-sandbox.mpp",
        help="Initial Project filename created inside the timestamped run folder.",
    )
    create.add_argument(
        "--debug-visible",
        action="store_true",
        help="Debug only: ask Microsoft Project to show its window while automation runs.",
    )
    create.add_argument(
        "--visible",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--jira-csv", required=True, type=Path, help="Project-wide Jira CSV export.")
    parser.add_argument("--config", type=Path, help="YAML configuration file.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("review-output"),
        help="Base output folder for timestamped runs and persistent state.",
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        help="Persistent state JSON path. Default: <output-dir>/j2p-state.json",
    )
    parser.add_argument(
        "--rollup-mode",
        choices=["initiative", "fixVersion"],
        help="Override rollup_mode from config.",
    )
    parser.add_argument("--run-id", help="Override timestamped run id; useful for repeatable tests.")


def run_validate(args: argparse.Namespace) -> int:
    context = make_context(args)
    progress("Reading Jira CSV and building review plan")
    baseline = snapshots_from_state(context["state_path"]) if args.compare_state else {}
    plan = build_run_plan(args.jira_csv, context["config"], baseline)
    state_after_path = context["run_dir"] / "j2p-state.after.json"
    progress("Writing report state")
    write_json(state_after_path, run_plan_to_state(plan))
    if args.write_state or context["config"].get("behavior", {}).get("write_state_on_validate"):
        write_json(context["state_path"], run_plan_to_state(plan))
    progress("Writing manager report and audit CSV files")
    paths = write_reports(
        plan,
        context["run_dir"],
        context["config"],
        sandbox_path=None,
        state_path=context["state_path"] if context["state_path"].exists() else None,
    )
    print_run_result("Validation complete. No Microsoft Project file was opened.", context, paths)
    return 0


def run_update(args: argparse.Namespace) -> int:
    context = make_context(args)
    debug_visible = get_debug_visible(args)
    progress("Copying source-of-truth MPP to a timestamped sandbox")
    sandbox_path = prepare_sandbox_copy(args.main_project, context["run_dir"], context["run_id"])
    progress(f"Loading comparison baseline from {args.comparison_source}")
    baseline = load_update_baseline(args, sandbox_path, context["config"], context["state_path"])
    progress("Reading Jira CSV and building update plan")
    plan = build_run_plan(args.jira_csv, context["config"], baseline)
    progress("Opening sandbox MPP and applying Jira updates")
    apply_plan_to_sandbox(sandbox_path, plan, context["config"], visible=debug_visible)
    progress("Writing state files")
    write_json(context["state_path"], run_plan_to_state(plan))
    write_json(context["run_dir"] / "j2p-state.after.json", run_plan_to_state(plan))
    progress("Writing manager report and audit CSV files")
    paths = write_reports(plan, context["run_dir"], context["config"], sandbox_path, context["state_path"])
    print_run_result("Sandbox update complete.", context, paths, sandbox_path)
    return 0


def run_create(args: argparse.Namespace) -> int:
    context = make_context(args)
    debug_visible = get_debug_visible(args)
    progress("Reading Jira CSV and building initial Project plan")
    baseline = snapshots_from_state(context["state_path"]) if context["state_path"].exists() else {}
    plan = build_run_plan(args.jira_csv, context["config"], baseline)
    output_project = context["run_dir"] / args.output_project_name
    progress("Creating initial sandbox MPP")
    create_project_from_plan(output_project, plan, context["config"], visible=debug_visible)
    progress("Writing state files")
    write_json(context["state_path"], run_plan_to_state(plan))
    write_json(context["run_dir"] / "j2p-state.after.json", run_plan_to_state(plan))
    progress("Writing manager report and audit CSV files")
    paths = write_reports(plan, context["run_dir"], context["config"], output_project, context["state_path"])
    print_run_result("Initial Project file created.", context, paths, output_project)
    return 0


def make_context(args: argparse.Namespace) -> Dict[str, Any]:
    overrides: Dict[str, Any] = {}
    if args.rollup_mode:
        overrides["rollup_mode"] = args.rollup_mode
    config = load_config(args.config, overrides)
    run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir
    run_dir = output_dir / f"j2p-run-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = args.state_path or output_dir / "j2p-state.json"
    return {
        "config": config,
        "run_id": run_id,
        "output_dir": output_dir,
        "run_dir": run_dir,
        "state_path": state_path,
    }


def load_update_baseline(
    args: argparse.Namespace,
    sandbox_path: Path,
    config: Dict[str, Any],
    state_path: Path,
) -> Dict[str, Any]:
    if args.comparison_source == "main":
        return snapshot_project_file(sandbox_path, config, visible=get_debug_visible(args))
    if args.comparison_source == "previous-sandbox":
        if not args.previous_sandbox:
            raise J2PError("--previous-sandbox is required when --comparison-source previous-sandbox is used.")
        return snapshot_project_file(args.previous_sandbox, config, visible=get_debug_visible(args))
    return snapshots_from_state(state_path)


def print_run_result(
    message: str,
    context: Dict[str, Any],
    paths: Dict[str, Path],
    sandbox_path: Optional[Path] = None,
) -> None:
    print(message)
    print(f"Run folder: {context['run_dir']}")
    if sandbox_path:
        print(f"Sandbox Project file: {sandbox_path}")
    print(f"Manager report: {paths['manager_report']}")
    print(f"Audit detail CSV: {paths['audit_detail']}")
    print(f"Planned epics CSV: {paths['planned_epics']}")
    print(f"Dependency review CSV: {paths['dependency_review']}")
    print(f"Per-project-key CSVs: {paths['by_project_key']}")
    print(f"Field mapping: {paths['field_mapping']}")


def get_debug_visible(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "debug_visible", False) or getattr(args, "visible", False))


def progress(message: str) -> None:
    print(f"[j2p] {datetime.now().strftime('%H:%M:%S')} {message}...", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
