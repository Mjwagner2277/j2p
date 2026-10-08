"""Select a configuration and one snapshot's CSV batches from an input folder."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any, Dict, Optional

from .models import J2PError


def _folder_candidates(folder: Path, suffixes: set[str]) -> list[Dict[str, str]]:
    """Keep alias names as well as targets so a changed discovery cannot go unnoticed."""
    try:
        paths = sorted(
            (path for path in folder.iterdir()
             if path.suffix.lower() in suffixes and path.is_file()),
            key=lambda path: (path.name.casefold(), path.name),
        )
        return [{"name": path.name, "path": str(path), "target": str(path.resolve())}
                for path in paths]
    except (OSError, RuntimeError) as exc:
        raise J2PError(f"Could not inspect config folder {folder}: {exc}") from exc


def _require_csv(args: Namespace, required: bool) -> None:
    if required and not getattr(args, "jira_csv", None):
        raise J2PError("No Jira CSV inputs selected. Supply --jira-csv FILE [FILE ...] "
                       "or --config-folder DIR containing CSV exports.")


def resolve_input_folder(args: Namespace, *, require_csv: bool = True) -> None:
    """Resolve optional folder discovery once, preserving explicit file overrides.

    Content validation and hashing belong to the existing input pipeline. This
    selection records the discovered names and targets so that pipeline can also
    reject additions, removals, or symlink rotations during a run.
    """
    if getattr(args, "_input_folder_selection", None) is not None:
        _require_csv(args, require_csv)
        return
    supplied = getattr(args, "config_folder", None)
    if supplied is None:
        _require_csv(args, require_csv)
        return

    source_path = Path(supplied).expanduser().absolute()
    try:
        folder = source_path.resolve()
        is_directory = folder.is_dir()
    except (OSError, RuntimeError) as exc:
        raise J2PError(f"Could not open config folder {source_path}: {exc}") from exc
    if not is_directory:
        raise J2PError(f"Config folder is not an existing directory: {source_path}. "
                       "Use --config-folder DIR with a configuration and CSV exports.")

    config_discovered = not bool(getattr(args, "config", None))
    csv_discovered = not bool(getattr(args, "jira_csv", None))
    config_candidates = _folder_candidates(folder, {".yaml", ".yml"}) if config_discovered else []
    csv_candidates = _folder_candidates(folder, {".csv"}) if csv_discovered else []
    if config_discovered:
        if not config_candidates:
            raise J2PError(f"No YAML configuration found directly in config folder {folder}. "
                           "Add one .yaml/.yml file or select a file with --config FILE.")
        if len(config_candidates) != 1:
            names = ", ".join(item["name"] for item in config_candidates)
            raise J2PError(f"Multiple YAML configurations found in config folder {folder}: {names}. "
                           "Select one with --config FILE.")
        config = Path(config_candidates[0]["target"])
    else:
        config = args.config

    if csv_discovered:
        # Two folder entries pointing at the same export are still recorded for
        # integrity checks, but need only be parsed once.
        csv_paths = [Path(target) for target in dict.fromkeys(
            item["target"] for item in csv_candidates)]
        if require_csv and not csv_paths:
            raise J2PError(f"No CSV exports found directly in config folder {folder}. "
                           "Add .csv files or supply --jira-csv FILE [FILE ...]. "
                           "Subfolders are not searched.")
    else:
        csv_paths = args.jira_csv

    # Commit the selection only after discovery succeeds. In particular, retrying
    # a rejected folder must not turn a partial discovery into explicit overrides.
    args.config_folder = folder
    args.config = config
    args.jira_csv = csv_paths
    args._input_folder_selection = {
        "path": str(folder),
        "source_path": str(source_path),
        "config_discovered": config_discovered,
        "csv_discovered": csv_discovered,
        "config": str(Path(config).expanduser().resolve()),
        "csv_paths": [str(Path(path).expanduser().resolve()) for path in csv_paths],
        "config_candidates": config_candidates,
        "csv_candidates": csv_candidates,
    }


def verify_input_folder(selection: Optional[Dict[str, Any]]) -> None:
    """Reject a changed folder selection without rediscovering explicit overrides."""
    if not selection:
        return
    folder = Path(selection["path"])
    source_path = Path(selection.get("source_path", selection["path"]))
    try:
        stable_folder = source_path.resolve() == folder and folder.is_dir()
    except (OSError, RuntimeError):
        stable_folder = False
    if not stable_folder:
        raise J2PError(f"Config folder changed during the run: {source_path}. "
                       "Retry with a stable input folder; root state was not advanced.")
    for dimension, suffixes in (("config", {".yaml", ".yml"}), ("csv", {".csv"})):
        if not selection.get(f"{dimension}_discovered"):
            continue
        current = _folder_candidates(folder, suffixes)
        if current != selection.get(f"{dimension}_candidates"):
            label = "YAML configuration" if dimension == "config" else "CSV export"
            raise J2PError(f"Config folder {label} selection changed during the run: {folder}. "
                           "A file was added, removed, renamed, or its symlink target changed. "
                           "Retry with a stable input folder; root state was not advanced.")
