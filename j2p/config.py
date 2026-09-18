"""Configuration loading for j2p.

The same restricted YAML reader is used on every installation: nested maps,
scalar values and string lists. Unsupported YAML features fail explicitly.
"""

from __future__ import annotations

import copy
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .yaml_subset import YamlSubsetError, parse_yaml


DEFAULT_CONFIG: Dict[str, Any] = {
    "rollup_modes": {},
    "done_statuses": ["Done"],
    "issue_types": {
        "initiative": ["Initiative"],
        "epic": ["Epic"],
        "story": ["Story", "Task", "Sub-task", "Bug"],
    },
    "columns": {
        "jira_key": ["Issue key", "Key"],
        "issue_id": ["Issue id", "Issue ID", "ID"],
        "issue_type": ["Issue Type", "Issue type", "Work Item Type"],
        "summary": ["Summary", "Name"],
        "epic_link": ["Epic Link", "Epic link"],
        "parent": ["Parent", "Parent key", "Parent Key"],
        "fix_versions": ["Fix versions", "Fix Version/s", "Fix Version", "FixVersions"],
        "story_points": [
            "Story Points",
            "Story point estimate",
            "Custom field (Story point estimate)",
        ],
        "logged_hours": [
            "Logged Hours",
            "Hours Logged",
            "Worklog Hours",
            "Time Spent Hours",
            "Time Spent",
            "Σ Time Spent",
            "Aggregate time spent",
            "Custom field (Logged Hours)",
        ],
        "status": ["Status"],
        "resolution": ["Resolution"],
        "resolved": ["Resolved", "Resolution date", "Resolution Date"],
        "target_start": ["Target start", "Target Start"],
        "target_end": ["Target end", "Target End"],
        "warning_suppression_date": [
            "Warning suppression date",
            "Created",
            "Created date",
            "Resolved",
            "Resolution date",
            "Updated",
        ],
        "successors": ["Outward issue link (Blocks)", "Blocks"],
        "predecessors": ["Inward issue link (Blocks)", "Blocked by", "is blocked by"],
    },
    "resource_groups": {},
    "multi_fixversion_policy": {
        "default": "reference",
    },
    "metrics": {
        "hours_per_story_point": 8.0,
        "logged_hours_unit": "hours",
        "logged_hours_units": {},
        "logged_hours_source": "direct",
    },
    "input": {
        "csv_max_field_chars": 8 * 1024 * 1024,
    },
    "warning_suppression": {
        "before": "",
        "date_fields": ["target_end", "target_start"],
        "severities": ["Warning", "Review"],
        "keep_summary": True,
    },
    "fixversion_completion_suppression": {
        "enabled": True,
        "stale_after_days": 90,
        "as_of_date": "",
        "keep_audit_summary": True,
    },
    "planning_horizon": {
        "enabled": True,
        "immediate_months": 6,
        "bucket_months": 6,
        "as_of_date": "",
    },
    "behavior": {
        "unknown_prefix": "exclude",
        "hide_completed_epics": True,
        "write_state_on_validate": False,
    },
    "review_table": {
        "exposed_columns": [
            "jira_key",
            "summary",
            "resource_group",
            "dependency_review",
            "jira_status",
            "start",
            "finish",
            "percent_complete",
            "predecessors",
        ],
        "include_audit_columns": False,
    },
    "project_fields": {
        "jira_key": "Text1",
        "jira_issue_id": "Text2",
        "jira_issue_type": "Text3",
        "rollup_mode": "Text4",
        "rollup_key": "Text5",
        "jira_key_prefix": "Text7",
        "dependency_review": "Text8",
        "jira_status": "Text9",
        "j2p_key": "Text10",
        "row_role": "Text11",
        "fix_version": "Text12",
        "primary_schedule_key": "Text13",
        "total_story_points": "Number1",
        "completed_story_points": "Number2",
        "logged_hours": "Number3",
        "story_point_ratio": "Number4",
        "in_planning": "Flag1",
        "unmatched_project_task": "Flag2",
        "dependency_review_needed": "Flag3",
        "drives_schedule": "Flag4",
        "jira_target_start": "Date1",
        "jira_target_end": "Date2",
    },
    "project_field_names": {
        "jira_key": "Jira Key",
        "jira_issue_id": "Jira Issue ID",
        "jira_issue_type": "Jira Issue Type",
        "rollup_mode": "Rollup Mode",
        "rollup_key": "Rollup Key",
        "jira_key_prefix": "Jira Key Prefix",
        "dependency_review": "Dependency Review",
        "jira_status": "Jira Status",
        "j2p_key": "j2p Unique Key",
        "row_role": "j2p Row Role",
        "fix_version": "Jira Fix Version",
        "primary_schedule_key": "Primary Schedule Key",
        "total_story_points": "Total Story Points",
        "completed_story_points": "Completed Story Points",
        "logged_hours": "Logged Hours",
        "story_point_ratio": "Story Point Ratio",
        "in_planning": "In Planning",
        "unmatched_project_task": "Unmatched Project Task",
        "dependency_review_needed": "Dependency Review Needed",
        "drives_schedule": "Drives Schedule",
        "jira_target_start": "Jira Target Start",
        "jira_target_end": "Jira Target End",
    },
    "colors": {
        "changed_cell": "#C6EFCE",
        "cascade_root": "#FFC7CE",
        "review_needed": "#FFEB9C",
        "dependency_review": "#BDD7EE",
        "in_planning": "#D9EAD3",
    },
}


class ConfigError(ValueError):
    """Raised when configuration is invalid."""


def load_config(path: Optional[Path], overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = copy.deepcopy(DEFAULT_CONFIG)
    if path:
        file_config = read_yaml_file(path)
        if not isinstance(file_config, dict):
            raise ConfigError(f"Config file must contain a YAML mapping: {path}")
        deep_merge(config, file_config)
    if overrides:
        deep_merge(config, overrides)
    normalize_config(config)
    return config


def read_yaml_file(path: Path) -> Dict[str, Any]:
    try:
        return parse_yaml_subset(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ConfigError) as exc:
        raise ConfigError(f"Could not read config {path}: {exc}") from exc


def validate_config_shape(config: Dict[str, Any]) -> None:
    """Validate before normalization so coercion cannot conceal invalid settings."""
    if not isinstance(config, dict):
        raise ConfigError("Config must be a mapping.")
    if "rollup_mode" in config:
        raise ConfigError("Top-level rollup_mode is no longer supported. Use rollup_modes per prefix.")
    if isinstance(config.get("behavior"), dict) and "multiple_fix_versions" in config["behavior"]:
        raise ConfigError("behavior.multiple_fix_versions is no longer supported. Use multi_fixversion_policy.")
    unknown = set(config) - set(DEFAULT_CONFIG)
    if unknown:
        raise ConfigError(f"Unknown config setting(s): {', '.join(sorted(map(str, unknown)))}.")

    def strings(value: Any, path: str, allow_empty: bool = False) -> None:
        values = value if isinstance(value, list) else [value]
        if not values and not allow_empty:
            raise ConfigError(f"{path} must contain at least one string.")
        if any(not isinstance(item, str) or not item.strip() for item in values):
            raise ConfigError(f"{path} must be a string or a list of nonempty strings.")

    dynamic = {"resource_groups", "rollup_modes", "multi_fixversion_policy"}
    string_maps = {"project_fields", "project_field_names", "colors", *dynamic}
    boolean_keys = {
        "behavior": {"hide_completed_epics", "write_state_on_validate"},
        "warning_suppression": {"keep_summary"},
        "fixversion_completion_suppression": {"enabled", "keep_audit_summary"},
        "planning_horizon": {"enabled"},
        "review_table": {"include_audit_columns"},
    }
    integer_keys = {
        "input": {"csv_max_field_chars"},
        "fixversion_completion_suppression": {"stale_after_days"},
        "planning_horizon": {"immediate_months", "bucket_months"},
    }
    for section, value in config.items():
        if section == "done_statuses":
            strings(value, section)
            continue
        if section == "multi_fixversion_policy" and isinstance(value, str):
            continue
        if not isinstance(value, dict):
            raise ConfigError(f"{section} must be a mapping.")
        if section not in dynamic:
            unknown_keys = set(value) - set(DEFAULT_CONFIG[section])
            if unknown_keys:
                raise ConfigError(f"Unknown {section} setting(s): {', '.join(sorted(map(str, unknown_keys)))}.")
        for key, item in value.items():
            path = f"{section}.{key}"
            if not isinstance(key, str) or not key.strip():
                raise ConfigError(f"{section} keys must be nonempty strings.")
            if section in {"columns", "issue_types"}:
                strings(item, path, allow_empty=section == "columns")
            elif section in string_maps:
                if not isinstance(item, str) or not item.strip():
                    raise ConfigError(f"{path} must be a nonempty string.")
            elif key in boolean_keys.get(section, set()):
                if type(item) is not bool:
                    raise ConfigError(f"{path} must be true or false, without quotes.")
            elif key in integer_keys.get(section, set()):
                if type(item) is not int or item <= 0:
                    raise ConfigError(f"{path} must be a positive integer.")
            elif section == "metrics" and key == "hours_per_story_point":
                if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) or item <= 0:
                    raise ConfigError(f"{path} must be a finite positive number.")
            elif section == "metrics" and key == "logged_hours_units":
                if not isinstance(item, dict) or any(
                    not isinstance(header, str) or not header.strip() or unit not in ("hours", "minutes", "seconds")
                    for header, unit in item.items()
                ):
                    raise ConfigError(f"{path} must map column headers to hours, minutes or seconds.")
                normalized_headers = [re.sub(r"\s+", " ", header.strip()).casefold() for header in item]
                if len(normalized_headers) != len(set(normalized_headers)):
                    raise ConfigError(f"{path} contains duplicate column headers after normalization.")
            elif section == "warning_suppression" and key in {"date_fields", "severities"}:
                strings(item, path)
            elif section == "review_table" and key == "exposed_columns":
                strings(item, path, allow_empty=True)
            elif not isinstance(item, str):
                raise ConfigError(f"{path} must be a string.")
    if config["behavior"]["unknown_prefix"] != "exclude":
        raise ConfigError("behavior.unknown_prefix only supports 'exclude'.")
    metrics = config["metrics"]
    if metrics["logged_hours_unit"] not in {"hours", "minutes", "seconds"}:
        raise ConfigError("metrics.logged_hours_unit must be hours, minutes or seconds.")
    if metrics["logged_hours_source"] not in {"direct", "aggregate"}:
        raise ConfigError("metrics.logged_hours_source must be direct or aggregate.")
    if config["input"]["csv_max_field_chars"] > 64 * 1024 * 1024:
        raise ConfigError("input.csv_max_field_chars must be at most 67108864 (64 MiB characters).")
    for key, color in config["colors"].items():
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            raise ConfigError(f"colors.{key} must be a six-digit hex color, for example '#C6EFCE'.")
    from .jira import parse_date
    for section, key in (
        ("warning_suppression", "before"),
        ("fixversion_completion_suppression", "as_of_date"),
        ("planning_horizon", "as_of_date"),
    ):
        value = config[section][key]
        if value:
            date_audit = []
            parse_date(value, date_audit, "CONFIG", 0)
            if date_audit:
                raise ConfigError(f"{section}.{key} must be a valid date; use YYYY-MM-DD.")
    for section in ("resource_groups", "rollup_modes", "multi_fixversion_policy"):
        value = config[section]
        if isinstance(value, dict):
            keys = [key.strip().upper() for key in value]
            if len(keys) != len(set(keys)):
                raise ConfigError(f"{section} contains duplicate prefixes after normalization.")


def normalize_config(config: Dict[str, Any]) -> None:
    validate_config_shape(config)
    fields = config.get("project_fields")
    if not isinstance(fields, dict):
        raise ConfigError("project_fields must be a mapping.")
    used = set()
    for logical, field in fields.items():
        default = DEFAULT_CONFIG["project_fields"].get(logical)
        if default is None:
            raise ConfigError(f"Unknown project_fields entry: {logical}.")
        family = re.match(r"[A-Za-z]+", default).group()
        limit = {"Text": 30, "Number": 20, "Flag": 20, "Date": 10}[family]
        match = re.fullmatch(rf"{family}([1-9][0-9]*)", str(field))
        if not match or int(match.group(1)) > limit:
            raise ConfigError(f"project_fields.{logical} must map to {family}1 through {family}{limit}.")
        if field in used:
            raise ConfigError(f"project_fields maps more than one value to {field}.")
        used.add(field)

    if "rollup_mode" in config:
        raise ConfigError(
            "Top-level rollup_mode is no longer supported. "
            "Use rollup_modes.<JIRA_KEY_PREFIX> for every prefix in resource_groups."
        )

    for key, value in list(config.get("columns", {}).items()):
        config["columns"][key] = ensure_list(value)

    for section_name in ("issue_types",):
        for key, value in list(config.get(section_name, {}).items()):
            config[section_name][key] = [str(v) for v in ensure_list(value)]

    config["done_statuses"] = [str(v) for v in ensure_list(config.get("done_statuses", []))]
    config["resource_groups"] = {
        str(k).strip().upper(): str(v).strip() for k, v in config.get("resource_groups", {}).items()
    }

    raw_rollup_modes = config.get("rollup_modes", {})
    if raw_rollup_modes is None:
        raw_rollup_modes = {}
    if not isinstance(raw_rollup_modes, dict):
        raise ConfigError("rollup_modes must be a YAML mapping from Jira key prefix to rollup type.")
    rollup_modes = {}
    for prefix, rollup_mode in raw_rollup_modes.items():
        prefix_text = str(prefix).strip().upper()
        rollup_mode_text = str(rollup_mode).strip()
        if rollup_mode_text not in {"initiative", "fixVersion"}:
            raise ConfigError(
                f"rollup_modes.{prefix} must be either 'initiative' or 'fixVersion'."
            )
        rollup_modes[prefix_text] = rollup_mode_text
    missing_rollup_modes = sorted(set(config["resource_groups"]) - set(rollup_modes))
    if missing_rollup_modes:
        raise ConfigError(
            "rollup_modes must include every configured resource_groups prefix. "
            f"Missing: {', '.join(missing_rollup_modes)}."
        )
    extra_rollup_modes = sorted(set(rollup_modes) - set(config["resource_groups"]))
    if extra_rollup_modes:
        raise ConfigError(
            "rollup_modes contains prefix entries that are not in resource_groups. "
            f"Remove or add resource_groups entries for: {', '.join(extra_rollup_modes)}."
        )
    config["rollup_modes"] = rollup_modes

    policy_config = config.get("multi_fixversion_policy", {})
    if policy_config is None:
        policy_config = {}
    if isinstance(policy_config, str):
        policy_config = {"default": policy_config}
    if not isinstance(policy_config, dict):
        raise ConfigError("multi_fixversion_policy must be a YAML mapping or a scalar policy.")
    normalized_policy = {"default": "reference"}
    for prefix, policy in policy_config.items():
        policy_text = str(policy).strip().lower()
        if policy_text not in {"reference", "split"}:
            raise ConfigError(
                f"multi_fixversion_policy.{prefix} must be either 'reference' or 'split'."
            )
        prefix_text = str(prefix).strip()
        policy_key = "default" if prefix_text.lower() == "default" else prefix_text.upper()
        normalized_policy[policy_key] = policy_text
    config["multi_fixversion_policy"] = normalized_policy

    legacy_policy = config.get("behavior", {}).get("multiple_fix_versions")
    if legacy_policy is not None:
        raise ConfigError(
            "behavior.multiple_fix_versions is no longer supported. "
            "Use multi_fixversion_policy with 'reference' or 'split'."
        )

    metrics = config.get("metrics", {})
    if metrics is None:
        metrics = {}
    if not isinstance(metrics, dict):
        raise ConfigError("metrics must be a YAML mapping.")
    hours_per_story_point = metrics.get("hours_per_story_point", 8.0)
    try:
        hours_per_story_point = float(hours_per_story_point)
    except (TypeError, ValueError) as exc:
        raise ConfigError("metrics.hours_per_story_point must be a positive number.") from exc
    if not math.isfinite(hours_per_story_point) or hours_per_story_point <= 0:
        raise ConfigError("metrics.hours_per_story_point must be greater than zero.")
    metrics["hours_per_story_point"] = hours_per_story_point
    metrics["logged_hours_units"] = {
        re.sub(r"\s+", " ", header.strip()).casefold(): unit
        for header, unit in metrics["logged_hours_units"].items()
    }
    config["metrics"] = metrics

    warning_suppression = config.get("warning_suppression", {})
    if warning_suppression is None:
        warning_suppression = {}
    if not isinstance(warning_suppression, dict):
        raise ConfigError("warning_suppression must be a YAML mapping.")

    cutoff = warning_suppression.get("before", "")
    warning_suppression["before"] = "" if cutoff is None else str(cutoff).strip()

    date_fields = [
        str(value).strip()
        for value in ensure_list(warning_suppression.get("date_fields", ["target_end", "target_start"]))
        if str(value).strip()
    ]
    if not date_fields:
        date_fields = ["target_end", "target_start"]
    allowed_date_fields = {"target_end", "target_start", "warning_suppression_date"}
    invalid_date_fields = [value for value in date_fields if value not in allowed_date_fields]
    if invalid_date_fields:
        raise ConfigError(
            "warning_suppression.date_fields only supports: "
            f"{', '.join(sorted(allowed_date_fields))}. Invalid: {', '.join(invalid_date_fields)}."
        )
    warning_suppression["date_fields"] = date_fields

    severity_lookup = {"error": "Error", "warning": "Warning", "review": "Review", "info": "Info"}
    severities = []
    for value in ensure_list(warning_suppression.get("severities", ["Warning", "Review"])):
        severity_key = str(value).strip().lower()
        if not severity_key:
            continue
        severity = severity_lookup.get(severity_key)
        if not severity:
            raise ConfigError(
                "warning_suppression.severities only supports: "
                "Error, Warning, Review, Info."
            )
        severities.append(severity)
    if not severities:
        severities = ["Warning", "Review"]
    warning_suppression["severities"] = severities
    warning_suppression["keep_summary"] = bool(warning_suppression.get("keep_summary", True))
    config["warning_suppression"] = warning_suppression

    fixversion_suppression = config.get("fixversion_completion_suppression", {})
    if fixversion_suppression is None:
        fixversion_suppression = {}
    if not isinstance(fixversion_suppression, dict):
        raise ConfigError("fixversion_completion_suppression must be a YAML mapping.")
    fixversion_suppression["enabled"] = bool(fixversion_suppression.get("enabled", True))
    try:
        stale_after_days = int(fixversion_suppression.get("stale_after_days", 90))
    except (TypeError, ValueError) as exc:
        raise ConfigError("fixversion_completion_suppression.stale_after_days must be a positive integer.") from exc
    if stale_after_days <= 0:
        raise ConfigError("fixversion_completion_suppression.stale_after_days must be greater than zero.")
    fixversion_suppression["stale_after_days"] = stale_after_days
    as_of_date = fixversion_suppression.get("as_of_date", "")
    fixversion_suppression["as_of_date"] = "" if as_of_date is None else str(as_of_date).strip()
    fixversion_suppression["keep_audit_summary"] = bool(
        fixversion_suppression.get("keep_audit_summary", True)
    )
    config["fixversion_completion_suppression"] = fixversion_suppression

    planning_horizon = config.get("planning_horizon", {})
    if planning_horizon is None:
        planning_horizon = {}
    if not isinstance(planning_horizon, dict):
        raise ConfigError("planning_horizon must be a YAML mapping.")
    planning_horizon["enabled"] = bool(planning_horizon.get("enabled", True))
    try:
        immediate_months = int(planning_horizon.get("immediate_months", 6))
        bucket_months = int(planning_horizon.get("bucket_months", 6))
    except (TypeError, ValueError) as exc:
        raise ConfigError("planning_horizon immediate_months and bucket_months must be positive integers.") from exc
    if immediate_months <= 0:
        raise ConfigError("planning_horizon.immediate_months must be greater than zero.")
    if bucket_months <= 0:
        raise ConfigError("planning_horizon.bucket_months must be greater than zero.")
    planning_horizon["immediate_months"] = immediate_months
    planning_horizon["bucket_months"] = bucket_months
    as_of_date = planning_horizon.get("as_of_date", "")
    planning_horizon["as_of_date"] = "" if as_of_date is None else str(as_of_date).strip()
    config["planning_horizon"] = planning_horizon

    review_table = config.get("review_table", {})
    if review_table is None:
        review_table = {}
    if not isinstance(review_table, dict):
        raise ConfigError("review_table must be a YAML mapping.")
    exposed_columns = review_table.get("exposed_columns", "all")
    if isinstance(exposed_columns, str):
        if exposed_columns.strip().lower() != "all":
            raise ConfigError("review_table.exposed_columns must be 'all' or a list of column names.")
        review_table["exposed_columns"] = "all"
    else:
        review_table["exposed_columns"] = [str(v).strip() for v in ensure_list(exposed_columns) if str(v).strip()]
    review_table["include_audit_columns"] = bool(review_table.get("include_audit_columns", True))
    config["review_table"] = review_table


def ensure_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def deep_merge(base: Dict[str, Any], incoming: Dict[str, Any]) -> None:
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)


def parse_yaml_subset(text: str) -> Dict[str, Any]:
    try:
        return parse_yaml(text)
    except YamlSubsetError as exc:
        raise ConfigError(str(exc)) from exc


def logical_columns(config: Dict[str, Any], name: str) -> List[str]:
    return [str(value) for value in config["columns"].get(name, [])]


def lowered(values: Iterable[str]) -> set[str]:
    return {str(value).strip().lower() for value in values}
