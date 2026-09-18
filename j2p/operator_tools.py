"""Saved CLI profiles, read-only setup diagnostics, and explicit support exports."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

from .config import load_config
from .core import build_run_plan
from .models import J2PError
from .run_lifecycle import environment_details, file_identity

PROFILE_KEYS = {"project_name", "config", "output_dir", "state_path", "main_project",
                "comparison_source", "dependency_write_mode", "jira_csv", "expected_issues"}
PATH_KEYS = {"config", "output_dir", "state_path", "main_project"}


def expand_profile_args(argv, identity_out=None):
    profile_path = None
    for index, arg in enumerate(argv):
        if arg == "--profile" and index + 1 < len(argv):
            profile_path = Path(argv[index + 1])
        elif arg.startswith("--profile="):
            profile_path = Path(arg.split("=", 1)[1])
    if profile_path is None:
        return argv
    command = argv[0] if argv else ""
    if command not in {"validate", "create", "update", "doctor"}:
        raise J2PError("--profile applies to validate, create, update, or doctor.")
    profile_path = profile_path.expanduser().resolve()
    try:
        raw_profile = profile_path.read_bytes()
        data = json.loads(raw_profile.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise J2PError(f"Could not read profile {profile_path}: {exc}") from exc
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise J2PError("Profile must be a JSON object with version 1.")
    unknown = set(data) - PROFILE_KEYS - {"version"}
    if unknown:
        raise J2PError(f"Unknown profile setting(s): {', '.join(sorted(unknown))}.")
    if identity_out is not None:
        identity_out.update(path=str(profile_path), sha256=hashlib.sha256(raw_profile).hexdigest(),
                            bytes=len(raw_profile))
    injected = []
    present = {arg.split("=", 1)[0] for arg in argv if arg.startswith("--")}
    for key in sorted(PROFILE_KEYS & data.keys()):
        option = "--" + key.replace("_", "-")
        if option in present:
            continue
        if key in {"main_project", "comparison_source"} and command not in {"update", "doctor"}:
            continue
        if command == "doctor" and key in {"comparison_source", "dependency_write_mode"}:
            continue
        if key == "dependency_write_mode" and command == "validate":
            continue
        value = data[key]
        if key == "expected_issues":
            if type(value) is not int or value < 0:
                raise J2PError("Profile expected_issues must be a nonnegative integer.")
            values = [str(value)]
        elif key == "jira_csv":
            if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x for x in value):
                raise J2PError("Profile jira_csv must be a nonempty list of paths.")
            values = [str((profile_path.parent / x).resolve()) for x in value]
        else:
            if not isinstance(value, str) or not value.strip():
                raise J2PError(f"Profile {key} must be a nonempty string.")
            values = [str((profile_path.parent / value).resolve()) if key in PATH_KEYS else value]
        injected.extend([option, *values])
    return [argv[0], *injected, *argv[1:]]


def run_init_profile(args):
    data = {"version": 1, "project_name": args.project_name,
            "output_dir": str(args.output_dir.expanduser().resolve())}
    for field in ("config", "main_project"):
        value = getattr(args, field)
        if value:
            data[field] = str(value.expanduser().resolve())
    if args.config:
        load_config(args.config)
    args.path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.path.open("x", encoding="utf-8") as target:
            json.dump(data, target, indent=2)
            target.write("\n")
    except FileExistsError as exc:
        raise J2PError(f"Profile already exists: {args.path}. Choose a new path or edit the existing file.") from exc
    print(f"Saved project profile: {args.path}")
    print(f'Next: j2p doctor --profile "{args.path}"')
    return 0


def run_doctor(args):
    report = {"environment": environment_details(), "checks": [], "read_only": True}
    checks = report["checks"]
    def check(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})
    config = None
    try:
        config = load_config(args.config)
        check("configuration", "passed", "Configuration parsed and validated.")
    except (J2PError, ValueError, OSError, RuntimeError) as exc:
        check("configuration", "failed", str(exc))
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"MSProject.Application\CLSID"):
                pass
            check("Microsoft Project", "passed", "COM registration exists; live save/reopen still requires the Windows harness.")
        except OSError:
            check("Microsoft Project", "failed", "MSProject.Application COM registration was not found.")
        try:
            available = importlib.util.find_spec("win32com") is not None
        except (ImportError, ValueError):
            available = False
        check("pywin32", "passed" if available else "failed", "Installed" if available else 'Install j2p with the [project] extra.')
    else:
        check("Microsoft Project", "unavailable", "This host supports validation/reports; create/update require Windows and Microsoft Project.")
    if args.main_project:
        check("main project", "passed" if args.main_project.is_file() else "failed", str(args.main_project))
    destination = args.output_dir.expanduser().resolve()
    while not destination.exists() and destination.parent != destination:
        destination = destination.parent
    check("output parent", "passed" if destination.is_dir() and os.access(destination, os.W_OK) else "failed",
          f"Nearest existing parent: {destination}. No test file was written.")
    if args.state_path:
        state = args.state_path
    elif args.project_name:
        from .cli import slugify_path_part
        state = args.output_dir / slugify_path_part(args.project_name) / "j2p-state.json"
    else:
        state = None
    if state:
        pending = state.with_name(state.name + ".pending.json")
        check("state recovery", "warning" if pending.exists() else "passed",
              f"Pending publication at {pending}; the next run will recover it under a lock." if pending.exists() else "No pending publication journal.")
        if state.exists():
            from .state import snapshots_from_state
            try:
                snapshots_from_state(state)
                check("state", "passed", str(state))
            except (J2PError, ValueError, TypeError, AttributeError) as exc:
                check("state", "failed", str(exc))
    if args.jira_csv and config is not None:
        try:
            plan = build_run_plan(args.jira_csv, config)
            from .cli import check_expected_issues
            check_expected_issues(args, plan)
            report["input_stats"] = plan.stats
            check("CSV inputs", "passed", f"Read {plan.stats.get('unique_issues_read', 'unknown')} unique issues. No reports/state written.")
        except (J2PError, OSError, ValueError) as exc:
            check("CSV inputs", "failed", str(exc))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"j2p {report['environment']['j2p_version']} — read-only setup check")
        for item in checks:
            print(f"{item['status'].upper()}: {item['name']}: {item['detail']}")
    return 2 if any(item["status"] == "failed" for item in checks) else 0


def run_support_bundle(args):
    root = args.run_dir.expanduser().resolve()
    manifest_path = root / "run-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise J2PError(f"Cannot read run manifest {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("inputs", []), list):
        raise J2PError(f"Invalid run manifest: {manifest_path}; expected an object with an inputs list.")
    files = [(manifest_path, "run-manifest.json")]
    for name in ("failure.json", "project-verification.json"):
        if (root / name).is_file():
            files.append((root / name, name))
    for subdir in ("reports", "docs"):
        if (root / subdir).is_dir():
            files.extend((path, str(path.relative_to(root))) for path in sorted((root / subdir).rglob("*"))
                         if path.is_file() and not path.is_symlink() and root in path.resolve().parents)
    if args.include_inputs:
        for index, item in enumerate(manifest.get("inputs", []), start=1):
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str):
                raise J2PError("Invalid input identity in run manifest.")
            path = Path(item["path"])
            if file_identity(path)["sha256"] != item["sha256"]:
                raise J2PError(f"Original input has changed since this run: {path}.")
            files.append((path, f"inputs/{index:03d}-{path.name}"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.output.open("xb") as stream:
            try:
                with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                    for path, name in files:
                        bundle.write(path, name)
            except BaseException:
                stream.close()
                args.output.unlink(missing_ok=True)
                raise
    except FileExistsError as exc:
        raise J2PError(f"Support bundle already exists: {args.output}.") from exc
    print(f"Support bundle created: {args.output}")
    print("Includes run metadata, configuration, and reports; original CSVs included only with --include-inputs.")
    return 0
