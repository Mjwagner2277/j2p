"""Exclusive run ownership, recovery, and auditable publication of planned state."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import subprocess
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Dict

from . import __version__
from .models import J2PError
from .state import write_json, write_bytes_atomic


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_identity(path: Path) -> Dict[str, Any]:
    path = path.expanduser().resolve()
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
        return {"path": str(path), "sha256": digest.hexdigest(), "bytes": size}
    except OSError as exc:
        raise J2PError(f"Could not read input {path}: {exc.strerror or exc}") from exc


def environment_details() -> Dict[str, Any]:
    try:
        pywin32 = metadata.version("pywin32")
    except metadata.PackageNotFoundError:
        pywin32 = None
    commit = None
    dirty = None
    try:
        result = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent.parent),
                                 "rev-parse", "HEAD"], capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            commit = result.stdout.strip()
            status = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent.parent),
                                     "status", "--porcelain"], capture_output=True, text=True, timeout=2)
            if status.returncode == 0:
                dirty = bool(status.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return {"j2p_version": __version__, "git_commit": commit, "git_worktree_dirty": dirty,
            "python": platform.python_version(),
            "platform": platform.platform(), "pywin32": pywin32}


@contextmanager
def exclusive_lock(path: Path):
    """Kernel lock: released on process death; never unlink an open lock inode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise J2PError(f"Another j2p run is using this project/state: {path}. Retry after it finishes.") from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise J2PError(f"Another j2p run is using this project/state: {path}. Retry after it finishes.") from exc
        locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def journal_path(state_path: Path) -> Path:
    return state_path.with_name(state_path.name + ".pending.json")


def recover_pending_state(state_path: Path, force_rollback: bool = False) -> None:
    """Called only while holding both project and state locks."""
    journal = journal_path(state_path)
    if not journal.exists():
        return
    try:
        data = json.loads(journal.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("expected a journal object")
        manifest_path = Path(data["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        if not isinstance(manifest, dict):
            raise ValueError("expected a run manifest object")
        if force_rollback or manifest.get("status") not in {"completed", "completed_with_warnings"}:
            current = file_identity(state_path)["sha256"] if state_path.exists() else None
            if current not in {data["new_sha256"], data["old_sha256"]}:
                raise J2PError(f"State changed outside j2p during recovery: {state_path}. Preserve the pending journal and inspect it.")
            previous = data["previous_state"]
            if previous is None:
                state_path.unlink(missing_ok=True)
            else:
                write_bytes_atomic(state_path, base64.b64decode(previous, validate=True))
            marker = data.get("new_sprint_marker")
            if marker:
                Path(marker).unlink(missing_ok=True)
            if manifest:
                manifest.update(status="failed", finished_at=utc_now(),
                                error="Interrupted during publication; previous root state restored.")
                write_json(manifest_path, manifest)
        journal.unlink()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise J2PError(f"Could not recover interrupted state update {journal}: {exc}") from exc


def recover_incomplete_run(workspace: Path) -> None:
    pointer = workspace / ".j2p-active-run.json"
    if not pointer.exists():
        return
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("expected an active run object")
        path = Path(data["manifest_path"])
        if workspace.resolve() not in path.resolve().parents:
            raise ValueError("active run path is outside the project workspace")
        manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(manifest, dict):
            raise ValueError("expected a run manifest object")
        if manifest.get("status") not in {"completed", "completed_with_warnings", "failed"}:
            manifest.update(status="failed", finished_at=utc_now(), error="Previous process stopped before run completion; outputs are incomplete.")
            write_json(path, manifest)
        pointer.unlink()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise J2PError(f"Could not inspect interrupted run {pointer}: {exc}") from exc


class RunTransaction:
    def __init__(self, context, args):
        self.context, self.args = context, args
        self.locks = ExitStack()
        self.manifest: Dict[str, Any] = {}
        self.started = time.monotonic()
        self.completed = False
        self.created_marker = False
        self.manifest_path = context["run_dir"] / "run-manifest.json"

    def __enter__(self):
        context = self.context
        state_path = context["state_path"].resolve()
        locks = {context["workspace_dir"].resolve() / ".j2p.lock",
                 state_path.with_name(state_path.name + ".lock")}
        try:
            for path in sorted(locks, key=str):
                self.locks.enter_context(exclusive_lock(path))
            recover_pending_state(state_path)
            recover_incomplete_run(context["workspace_dir"])
            sprint = context["sprint_dir"]
            if sprint and (sprint / ".j2p-sprint").exists() and not self.args.allow_existing_sprint:
                raise J2PError(f"Sprint output already exists: {sprint}. Use --allow-existing-sprint to add a new run.")
            try:
                context["run_dir"].mkdir(parents=True, exist_ok=False)
            except FileExistsError as exc:
                raise J2PError(f"Run ID already exists: {context['run_id']}. Use a new --run-id; history is never overwritten.") from exc
            # This exclusively owned directory stages outputs until the manifest is completed.
            context["state_dir"].mkdir()
            if self.args.command != "validate":
                context["project_dir"].mkdir()
            config_json = json.dumps(context["config"], sort_keys=True, separators=(",", ":"))
            self.manifest = {
                "schema_version": 1, "status": "in_progress", "started_at": utc_now(),
                "run_id": context["run_id"], "command": self.args.command,
                "project_name": self.args.project_name, "sprint": self.args.sprint,
                "environment": environment_details(), "inputs": [],
                "resolved_config": context["config"],
                "config_sha256": hashlib.sha256(config_json.encode()).hexdigest(),
                "state_path": str(state_path), "comparison_source": getattr(self.args, "comparison_source", "state" if getattr(self.args, "compare_state", False) else "none"),
                "outputs": {}, "timings_seconds": {},
            }
            write_json(context["workspace_dir"] / ".j2p-active-run.json", {"manifest_path": str(self.manifest_path.resolve())})
            write_json(self.manifest_path, self.manifest)
            return self
        except BaseException:
            self.locks.close()
            raise

    def capture_inputs(self):
        self.manifest["inputs"] = [file_identity(path) for path in self.args.jira_csv]
        # All later reads use these exact targets even if a supplied symlink rotates.
        self.args.jira_csv = [Path(source["path"]) for source in self.manifest["inputs"]]
        for name in ("config", "main_project", "previous_sandbox", "profile"):
            value = getattr(self.args, name, None)
            if value:
                source = file_identity(Path(value))
                expected = (self.context.get("config_identity") if name == "config" else
                            getattr(self.args, "_profile_identity", None) if name == "profile" else None)
                if expected and source != expected:
                    raise J2PError(f"{name} changed after it was read: {value}. Retry with stable input files.")
                self.manifest[name] = source
                setattr(self.args, name, Path(source["path"]))
        state = self.context["state_path"]
        self.manifest["baseline_state"] = file_identity(state) if state.exists() else None
        if self.args.command == "create" and state.exists():
            self.manifest["comparison_source"] = "state"
        write_json(self.manifest_path, self.manifest)

    def record_plan(self, plan):
        self.manifest["stats"] = plan.stats
        self.manifest["audit_counts"] = {level: sum(item.severity == level for item in plan.audit_items)
                                         for level in ("Error", "Warning", "Review", "Info")}
        self.manifest["timings_seconds"]["elapsed_to_plan"] = round(time.monotonic() - self.started, 3)
        write_json(self.manifest_path, self.manifest)

    def complete(self, state, publish_state, paths):
        for source in self.manifest["inputs"]:
            if file_identity(Path(source["path"]))["sha256"] != source["sha256"]:
                raise J2PError(f"Input changed during the run: {source['path']}. Retry with stable export files.")
        for name in ("config", "main_project", "previous_sandbox", "profile"):
            source = self.manifest.get(name)
            if source and file_identity(Path(source["path"]))["sha256"] != source["sha256"]:
                raise J2PError(f"{name} changed during the run: {source['path']}. Root state was not advanced.")
        self.manifest["outputs"] = {key: str(path) for key, path in paths.items()}
        self.manifest["timings_seconds"]["total"] = round(time.monotonic() - self.started, 3)
        state_path = self.context["state_path"]
        sprint = self.context["sprint_dir"]
        marker = sprint / ".j2p-sprint" if sprint else None
        # Journal even a report-only run so interrupted sprint publication can recover.
        previous = state_path.read_bytes() if state_path.exists() else None
        captured = self.manifest.get("baseline_state")
        captured_hash = captured["sha256"] if captured else None
        current_hash = hashlib.sha256(previous).hexdigest() if previous is not None else None
        if captured_hash != current_hash:
            raise J2PError(f"Root state changed outside this run: {state_path}. Refusing to overwrite it.")
        proposed = (json.dumps(state, indent=2, sort_keys=True) + "\n").encode() if publish_state else previous
        write_json(journal_path(state_path), {
            "manifest_path": str(self.manifest_path.resolve()),
            "previous_state": base64.b64encode(previous).decode() if previous is not None else None,
            "old_sha256": hashlib.sha256(previous).hexdigest() if previous is not None else None,
            "new_sha256": hashlib.sha256(proposed).hexdigest() if proposed is not None else None,
            "new_sprint_marker": str(marker.resolve()) if marker and not marker.exists() else None,
        })
        if publish_state:
            write_json(state_path, state)
        if marker and not marker.exists():
            write_bytes_atomic(marker, f"project_name={self.args.project_name}\nsprint={self.args.sprint}\ncreated_by=j2p\n".encode())
        counts = self.manifest.get("audit_counts", {})
        self.manifest.update(status="completed_with_warnings" if any(counts.get(x, 0) for x in ("Error", "Warning", "Review")) else "completed",
                             finished_at=utc_now(), root_state_updated=publish_state)
        write_json(self.manifest_path, self.manifest)
        self.completed = True
        try:
            journal_path(state_path).unlink()
        except OSError:
            # The durable completed manifest makes journal cleanup safe next run.
            pass

    def __exit__(self, exc_type, exc, tb):
        try:
            if not self.completed:
                if journal_path(self.context["state_path"]).exists():
                    recover_pending_state(self.context["state_path"], force_rollback=True)
                self.manifest.update(status="failed", finished_at=utc_now(),
                                     error=str(exc) if exc is not None else "Run did not complete")
                self.manifest["timings_seconds"]["total"] = round(time.monotonic() - self.started, 3)
                try:
                    write_json(self.manifest_path, self.manifest)
                    write_json(self.context["run_dir"] / "failure.json", {
                        "error_type": exc_type.__name__ if exc_type else "IncompleteRun", "message": self.manifest["error"],
                        "run_id": self.context["run_id"], "root_state_updated": False,
                    })
                except OSError:
                    pass  # Preserve the original error if the output disk itself failed.
        finally:
            try:
                (self.context["workspace_dir"] / ".j2p-active-run.json").unlink(missing_ok=True)
            except OSError:
                pass
            self.locks.close()
        return False
