"""Command-line interface for j2p."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from uuid import uuid4
from pathlib import Path
from typing import Any, Dict, Optional

from . import __version__
from .config import ConfigError, load_config
from .run_lifecycle import RunTransaction, file_identity
from .operator_tools import expand_profile_args, run_doctor, run_init_profile, run_support_bundle
from .core import build_run_plan
from .models import J2PError
from .project import (
    ProjectAutomationError,
    apply_plan_to_sandbox,
    create_project_from_plan,
    prepare_sandbox_copy,
    snapshot_project_file,
)
from .reports import write_reports
from .state import run_plan_to_state, snapshots_from_state, write_json


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    try:
        profile_identity = {}
        argv = expand_profile_args(list(sys.argv[1:] if argv is None else argv), profile_identity)
        args = parser.parse_args(argv)
        if profile_identity:
            args._profile_identity = profile_identity
            args.profile = Path(profile_identity["path"])
        if args.command == "doctor":
            return run_doctor(args)
        if args.command == "init-profile":
            return run_init_profile(args)
        if args.command == "support-bundle":
            return run_support_bundle(args)
        if args.command == "validate":
            return run_validate(args)
        if args.command == "update":
            return run_update(args)
        if args.command == "create":
            return run_create(args)
    except (ConfigError, J2PError, ProjectAutomationError, OSError, ValueError) as exc:
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
    parser.add_argument("--version", action="version", version=f"j2p {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor", help="Read-only environment, configuration, and export checks.")
    doctor.add_argument("--profile", type=Path)
    doctor.add_argument("--config", type=Path)
    doctor.add_argument("--jira-csv", type=Path, nargs="+", action="extend")
    doctor.add_argument("--main-project", type=Path)
    doctor.add_argument("--project-name")
    doctor.add_argument("--output-dir", type=Path, default=Path("review-output"))
    doctor.add_argument("--state-path", type=Path)
    doctor.add_argument("--expected-issues", type=int)
    doctor.add_argument("--json", action="store_true", help="Print machine-readable diagnostics.")
    init = subparsers.add_parser("init-profile", help="Save reusable project arguments in a JSON profile.")
    init.add_argument("--path", required=True, type=Path)
    init.add_argument("--project-name", required=True)
    init.add_argument("--config", type=Path)
    init.add_argument("--main-project", type=Path)
    init.add_argument("--output-dir", type=Path, default=Path("review-output"))
    support = subparsers.add_parser("support-bundle", help="Package one run's diagnostics for support.")
    support.add_argument("--run-dir", required=True, type=Path)
    support.add_argument("--output", required=True, type=Path)
    support.add_argument("--include-inputs", action="store_true", help="Also copy original CSV inputs into the ZIP.")

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
    add_common_args(update, sprint_required=True)
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
        "--dependency-write-mode",
        choices=["fast", "diagnostic"],
        default="fast",
        help=(
            "Microsoft Project predecessor write strategy. Default: fast. "
            "Use diagnostic only when troubleshooting dependency write failures."
        ),
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
        "--dependency-write-mode",
        choices=["fast", "diagnostic"],
        default="fast",
        help=(
            "Microsoft Project predecessor write strategy. Default: fast. "
            "Use diagnostic only when troubleshooting dependency write failures."
        ),
    )
    create.add_argument(
        "--visible",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def add_common_args(parser: argparse.ArgumentParser, sprint_required: bool = False) -> None:
    parser.add_argument("--jira-csv", required=True, type=Path, nargs="+", action="extend",
                        help="One or more Jira CSV export batches. May be repeated; combine only one snapshot.")
    parser.add_argument("--profile", type=Path, help="Saved JSON project settings; explicit CLI options override them.")
    parser.add_argument("--expected-issues", type=int, help="Expected unique issue count across all CSV batches.")
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
        help="Persistent state JSON path. Default: <output-dir>/<Project>/j2p-state.json",
    )
    parser.add_argument("--run-id", help="Override timestamped run id; useful for repeatable tests.")
    parser.add_argument(
        "--project-name",
        required=True,
        help="Program/project folder name that groups all sprint and team outputs.",
    )
    parser.add_argument(
        "--sprint",
        required=sprint_required,
        help="Sprint or planning increment value to encode into the output folder structure.",
    )
    parser.add_argument(
        "--allow-existing-sprint",
        action="store_true",
        help="Allow writing a new run under an existing --project-name/--sprint folder.",
    )
    parser.add_argument(
        "--suppress-warnings-before",
        help=(
            "Suppress configured warning/review audit items for Jira issues dated before this cutoff. "
            "Example: 2025-01-01. Overrides warning_suppression.before in YAML."
        ),
    )


def run_validate(args: argparse.Namespace) -> int:
    return run_command(args)


def run_update(args: argparse.Namespace) -> int:
    return run_command(args)


def run_create(args: argparse.Namespace) -> int:
    return run_command(args)


def run_command(args: argparse.Namespace) -> int:
    context = make_context(args)
    with RunTransaction(context, args) as transaction:
        transaction.capture_inputs()
        debug_visible = get_debug_visible(args)
        sandbox_path = None
        if args.command == "update":
            # Parse before launching Project, so invalid inputs never need a COM session.
            progress("Reading Jira CSV and checking input values")
            preflight = build_run_plan(args.jira_csv, context["config"])
            check_expected_issues(args, preflight)
            progress("Copying source-of-truth MPP to a timestamped sandbox")
            sandbox_path = prepare_sandbox_copy(args.main_project, context["project_dir"], context["run_id"])
            progress(f"Loading comparison baseline from {args.comparison_source}")
            baseline = load_update_baseline(args, sandbox_path, context["config"], context["state_path"])
        else:
            compare = (args.command == "create" or getattr(args, "compare_state", False))
            if getattr(args, "compare_state", False) and not context["state_path"].exists():
                raise J2PError(f"Comparison state does not exist: {context['state_path']}. Create a baseline with --write-state first.")
            baseline = snapshots_from_state(context["state_path"]) if compare else {}
        progress("Reading Jira CSV and building review plan")
        plan = build_run_plan(args.jira_csv, context["config"], baseline)
        check_expected_issues(args, plan)
        progress(f"Read {plan.stats['csv_files_read']} CSV file(s); "
                 f"skipped {plan.stats['duplicate_csv_issues_skipped']} matching duplicate issue(s)")
        transaction.record_plan(plan)
        if args.command == "update":
            progress(f"Planned {planned_dependency_count(plan)} Project predecessor link(s)")
            apply_plan_to_sandbox(sandbox_path, plan, context["config"], visible=debug_visible,
                                  dependency_write_mode=args.dependency_write_mode)
        elif args.command == "create":
            sandbox_path = context["project_dir"] / args.output_project_name
            create_project_from_plan(sandbox_path, plan, context["config"], visible=debug_visible,
                                     dependency_write_mode=args.dependency_write_mode)
        transaction.record_plan(plan)
        state = run_plan_to_state(plan)
        write_json(context["state_dir"] / "j2p-state.after.json", state)
        publish_state = (args.command != "validate" or getattr(args, "write_state", False)
                         or context["config"].get("behavior", {}).get("write_state_on_validate", False))
        progress("Writing manager report and audit CSV files")
        paths = write_reports(plan, context["run_dir"], context["config"], sandbox_path,
                              context["state_path"] if publish_state or context["state_path"].exists() else None)
        published_paths = dict(paths, state_snapshot=context["state_dir"] / "j2p-state.after.json")
        if sandbox_path:
            published_paths["sandbox"] = sandbox_path
        transaction.complete(state, bool(publish_state), published_paths)
        message = {"validate": "Validation complete. No Microsoft Project file was opened.",
                   "update": "Sandbox update complete.", "create": "Initial Project file created."}[args.command]
        print_run_result(message, context, paths, sandbox_path)
        print(f"Run status: {transaction.manifest['status']}")
    return 0


def check_expected_issues(args, plan):
    expected = getattr(args, "expected_issues", None)
    if expected is not None:
        if expected < 0:
            raise J2PError("--expected-issues must be zero or greater.")
        actual = plan.stats.get("unique_issues_read", plan.stats.get("jira_issue_count"))
        if actual != expected:
            raise J2PError(f"Export coverage mismatch: expected {expected} unique issues, read {actual}. Check missing or overlapping batches.")


def make_context(args: argparse.Namespace) -> Dict[str, Any]:
    validate_output_scope(args)
    overrides: Dict[str, Any] = {}
    if getattr(args, "suppress_warnings_before", None):
        overrides["warning_suppression"] = {"before": args.suppress_warnings_before}
    config_identity = file_identity(args.config) if args.config else None
    if config_identity:
        args.config = Path(config_identity["path"])
    config = load_config(args.config, overrides or None)
    if config_identity and file_identity(args.config)["sha256"] != config_identity["sha256"]:
        raise J2PError(f"Configuration changed while being read: {args.config}. Retry with stable input files.")
    run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + uuid4().hex[:6]
    output_dir = args.output_dir.expanduser().resolve()
    project_name = getattr(args, "project_name", None)
    sprint = getattr(args, "sprint", None)
    workspace_dir = output_dir / slugify_path_part(project_name)
    sprint_dir = workspace_dir / "sprints" / slugify_path_part(sprint) if sprint else None
    if sprint_dir is not None:
        run_parent = sprint_dir / "runs"
    else:
        run_parent = workspace_dir / "runs"
    run_dir = run_parent / f"j2p-run-{run_id}"
    state_path = (args.state_path or workspace_dir / "j2p-state.json").expanduser().resolve()
    project_dir = run_dir / "project"
    state_dir = run_dir / "state"
    if state_path == run_dir or run_dir in state_path.parents:
        raise J2PError("--state-path must be outside the run folder.")
    reserved_state_names = {"run-manifest.json", "failure.json", "project-verification.json", "j2p-state.after.json"}
    if (state_path.name.lower() in reserved_state_names
            or state_path.name.lower().endswith(".pending.json")
            or any(part.lower().startswith((".j2p", "j2p-run-")) for part in state_path.parts)):
        raise J2PError("--state-path must not use a reserved lifecycle path or historical run folder.")
    inputs = [Path(path).expanduser().resolve() for path in args.jira_csv]
    inputs.extend(Path(getattr(args, name)).expanduser().resolve() for name in ("config", "profile", "main_project", "previous_sandbox") if getattr(args, name, None))
    state_files = {state_path, state_path.with_name(state_path.name + ".pending.json"),
                   state_path.with_name(state_path.name + ".lock")}
    if state_files.intersection(inputs):
        raise J2PError("--state-path must not overwrite an input file.")
    return {
        "config": config,
        "config_identity": config_identity,
        "run_id": run_id,
        "output_dir": output_dir,
        "workspace_dir": workspace_dir,
        "sprint_dir": sprint_dir,
        "run_dir": run_dir,
        "project_dir": project_dir,
        "state_dir": state_dir,
        "state_path": state_path,
    }


def validate_output_scope(args: argparse.Namespace) -> None:
    if not args.project_name.strip() or (getattr(args, "sprint", None) is not None and not args.sprint.strip()):
        raise J2PError("Project and sprint names must not be blank.")
    for name in ("run_id", "output_project_name"):
        value = getattr(args, name, None)
        if value is not None:
            if (not value or value in {".", ".."} or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
                    or value.endswith((".", " ")) or is_reserved_windows_name(value)):
                raise J2PError(f"--{name.replace('_', '-')} must be a safe filename without path separators.")
    if args.command == "create" and not args.output_project_name.lower().endswith(".mpp"):
        raise J2PError("--output-project-name must end in .mpp.")
    if getattr(args, "state_path", None) and args.state_path.suffix.lower() != ".json":
        raise J2PError("--state-path must be a JSON file.")
    if args.command == "update":
        if not getattr(args, "sprint", None):
            raise J2PError("update requires --sprint so sandbox updates are grouped by project and sprint.")
    if getattr(args, "sprint", None) and not getattr(args, "project_name", None):
        raise J2PError("--sprint requires --project-name.")


def is_reserved_windows_name(value: str) -> bool:
    return value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{x}" for x in range(1, 10)), *(f"LPT{x}" for x in range(1, 10))}


def slugify_path_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    cleaned = cleaned.strip(".-_")
    return "_" + cleaned if is_reserved_windows_name(cleaned) else cleaned or "unnamed"


def check_sprint_folder(sprint_dir: Path, allow_existing: bool) -> None:
    marker = sprint_dir / ".j2p-sprint"
    if marker.exists() and not allow_existing:
        raise J2PError(
            f"Sprint output already exists: {sprint_dir}. "
            "Use a different --sprint value or rerun with --allow-existing-sprint to add another run."
        )


def write_sprint_marker(sprint_dir: Path, project_name: str, sprint: str) -> None:
    sprint_dir.mkdir(parents=True, exist_ok=True)
    marker = sprint_dir / ".j2p-sprint"
    if not marker.exists():
        marker.write_text(
            f"project_name={project_name}\nsprint={sprint}\ncreated_by=j2p\n",
            encoding="utf-8",
        )


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
    if not state_path.exists():
        raise J2PError(f"Comparison state does not exist: {state_path}. Create a baseline first.")
    return snapshots_from_state(state_path)


def print_run_result(
    message: str,
    context: Dict[str, Any],
    paths: Dict[str, Path],
    sandbox_path: Optional[Path] = None,
) -> None:
    print(message)
    print(f"Run folder: {context['run_dir']}")
    print(f"Open report: {context['run_dir'] / 'reports' / 'html' / 'index.html'}")
    print(f"Run manifest: {context['run_dir'] / 'run-manifest.json'}")
    if sandbox_path:
        print(f"Sandbox Project file: {sandbox_path}")
    print(f"Manager report: {paths['manager_report']}")
    print(f"HTML reports folder: {paths['html_report']}")
    print(f"Audit detail CSV: {paths['audit_detail']}")
    print(f"Planned epics CSV: {paths['planned_epics']}")
    print(f"Dependency review CSV: {paths['dependency_review']}")
    print(f"Per-project-key CSVs: {paths['by_project_key']}")
    print(f"Field mapping: {paths['field_mapping']}")


def get_debug_visible(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "debug_visible", False) or getattr(args, "visible", False))


def progress(message: str) -> None:
    print(f"[j2p] {datetime.now().strftime('%H:%M:%S')} {message}...", flush=True)


def planned_dependency_count(plan: Any) -> int:
    return sum(len(epic.predecessors) for epic in plan.epics.values() if epic.drives_schedule)


if __name__ == "__main__":
    raise SystemExit(main())
