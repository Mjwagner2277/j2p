"""Configuration loading for j2p.

The project intentionally avoids a hard dependency on PyYAML. If PyYAML is
installed, it is used. Otherwise a small YAML subset parser supports the
configuration patterns used by this repository: nested maps, scalar values,
and string lists.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


DEFAULT_CONFIG: Dict[str, Any] = {
    "rollup_mode": "initiative",
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
        "status": ["Status"],
        "resolution": ["Resolution"],
        "target_start": ["Target start", "Target Start"],
        "target_end": ["Target end", "Target End"],
        "successors": ["Outward issue link (Blocks)", "Blocks"],
        "predecessors": ["Inward issue link (Blocks)", "Blocked by", "is blocked by"],
    },
    "resource_groups": {},
    "multi_fixversion_policy": {
        "default": "reference",
    },
    "behavior": {
        "unknown_prefix": "exclude",
        "hide_completed_epics": True,
        "write_state_on_validate": False,
    },
    "project_fields": {
        "jira_key": "Text1",
        "jira_issue_id": "Text2",
        "jira_issue_type": "Text3",
        "rollup_mode": "Text4",
        "rollup_key": "Text5",
        "resource_group": "Text6",
        "jira_key_prefix": "Text7",
        "dependency_review": "Text8",
        "jira_status": "Text9",
        "j2p_key": "Text10",
        "row_role": "Text11",
        "fix_version": "Text12",
        "primary_schedule_key": "Text13",
        "total_story_points": "Number1",
        "completed_story_points": "Number2",
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
        "resource_group": "Resource Group",
        "jira_key_prefix": "Jira Key Prefix",
        "dependency_review": "Dependency Review",
        "jira_status": "Jira Status",
        "j2p_key": "j2p Unique Key",
        "row_role": "j2p Row Role",
        "fix_version": "Jira Fix Version",
        "primary_schedule_key": "Primary Schedule Key",
        "total_story_points": "Total Story Points",
        "completed_story_points": "Completed Story Points",
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
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ImportError:
        return parse_yaml_subset(text)
    data = yaml.safe_load(text)
    return data or {}


def normalize_config(config: Dict[str, Any]) -> None:
    if config.get("rollup_mode") not in {"initiative", "fixVersion"}:
        raise ConfigError("rollup_mode must be either 'initiative' or 'fixVersion'.")

    rollup_modes = {}
    for prefix, rollup_mode in config.get("rollup_modes", {}).items():
        if rollup_mode not in {"initiative", "fixVersion"}:
            raise ConfigError(
                f"rollup_modes.{prefix} must be either 'initiative' or 'fixVersion'."
            )
        rollup_modes[str(prefix).upper()] = str(rollup_mode)
    config["rollup_modes"] = rollup_modes

    for key, value in list(config.get("columns", {}).items()):
        config["columns"][key] = ensure_list(value)

    for section_name in ("issue_types",):
        for key, value in list(config.get(section_name, {}).items()):
            config[section_name][key] = [str(v) for v in ensure_list(value)]

    config["done_statuses"] = [str(v) for v in ensure_list(config.get("done_statuses", []))]
    config["resource_groups"] = {
        str(k).upper(): str(v) for k, v in config.get("resource_groups", {}).items()
    }

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
    lines = _clean_yaml_lines(text)
    if not lines:
        return {}
    parsed, index = _parse_block(lines, 0, lines[0][1])
    if index != len(lines):
        line_number = lines[index][0]
        raise ConfigError(f"Could not parse YAML near line {line_number}.")
    if not isinstance(parsed, dict):
        raise ConfigError("Top-level YAML value must be a mapping.")
    return parsed


def _clean_yaml_lines(text: str) -> List[tuple[int, int, str]]:
    cleaned: List[tuple[int, int, str]] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ConfigError(f"Tabs are not supported for YAML indentation on line {line_number}.")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        raw = re.sub(r"\s+#.*$", "", raw).rstrip()
        indent = len(raw) - len(raw.lstrip(" "))
        cleaned.append((line_number, indent, raw.strip()))
    return cleaned


def _parse_block(
    lines: List[tuple[int, int, str]], index: int, indent: int
) -> tuple[Any, int]:
    is_list = lines[index][2].startswith("- ")
    if is_list:
        values: List[Any] = []
        while index < len(lines):
            line_number, current_indent, text = lines[index]
            if current_indent < indent:
                break
            if current_indent > indent:
                raise ConfigError(f"Unexpected indentation on line {line_number}.")
            if not text.startswith("- "):
                break
            item_text = text[2:].strip()
            if item_text:
                values.append(_parse_scalar(item_text))
                index += 1
            else:
                if index + 1 >= len(lines) or lines[index + 1][1] <= current_indent:
                    values.append(None)
                    index += 1
                else:
                    child, index = _parse_block(lines, index + 1, lines[index + 1][1])
                    values.append(child)
        return values, index

    mapping: Dict[str, Any] = {}
    while index < len(lines):
        line_number, current_indent, text = lines[index]
        if current_indent < indent:
            break
        if current_indent > indent:
            raise ConfigError(f"Unexpected indentation on line {line_number}.")
        if ":" not in text:
            raise ConfigError(f"Expected 'key: value' on line {line_number}.")
        key, raw_value = text.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            raise ConfigError(f"Empty key on line {line_number}.")
        if raw_value:
            mapping[key] = _parse_scalar(raw_value)
            index += 1
        else:
            if index + 1 >= len(lines) or lines[index + 1][1] <= current_indent:
                mapping[key] = {}
                index += 1
            else:
                child, index = _parse_block(lines, index + 1, lines[index + 1][1])
                mapping[key] = child
    return mapping, index


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value[0] in {"'", '"'} and value[-1:] == value[0]:
        return value[1:-1]
    lower = value.lower()
    if lower in {"true", "false"}:
        return lower == "true"
    if lower in {"null", "none", "~"}:
        return None
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part.strip()) for part in inner.split(",")]
    if re.fullmatch(r"-?\d+", value):
        try:
            return int(value)
        except ValueError:
            pass
    if re.fullmatch(r"-?\d+\.\d+", value):
        try:
            return float(value)
        except ValueError:
            pass
    return value


def logical_columns(config: Dict[str, Any], name: str) -> List[str]:
    return [str(value) for value in config["columns"].get(name, [])]


def lowered(values: Iterable[str]) -> set[str]:
    return {str(value).strip().lower() for value in values}
