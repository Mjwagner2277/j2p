"""Jira CSV reading and field parsing."""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from .models import AuditItem, J2PError, JiraIssue


JIRA_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


class CsvTable:
    def __init__(self, path: Path) -> None:
        self.path = path
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        if not rows:
            raise J2PError(f"CSV is empty: {path}")
        self.headers = [header.strip() for header in rows[0]]
        self.rows = [row for row in rows[1:] if any(cell.strip() for cell in row)]
        self.header_index: Dict[str, List[int]] = {}
        for index, header in enumerate(self.headers):
            self.header_index.setdefault(normalize_header(header), []).append(index)

    def has_any(self, candidates: Sequence[str]) -> bool:
        return any(normalize_header(candidate) in self.header_index for candidate in candidates)

    def selected_header(self, candidates: Sequence[str]) -> str:
        for candidate in candidates:
            norm = normalize_header(candidate)
            if norm in self.header_index:
                return self.headers[self.header_index[norm][0]]
        return ""

    def get_all(self, row: Sequence[str], candidates: Sequence[str]) -> List[str]:
        values: List[str] = []
        for candidate in candidates:
            norm = normalize_header(candidate)
            for index in self.header_index.get(norm, []):
                if index < len(row):
                    value = row[index].strip()
                    if value:
                        values.append(value)
        return values

    def get_first(self, row: Sequence[str], candidates: Sequence[str]) -> str:
        values = self.get_all(row, candidates)
        return values[0] if values else ""


def parse_issues(table: CsvTable, config: Dict[str, Any], audit: List[AuditItem]) -> List[JiraIssue]:
    issues: List[JiraIssue] = []
    columns = config["columns"]
    for row_index, row in enumerate(table.rows, start=2):
        key = table.get_first(row, columns["jira_key"]).upper()
        if not key:
            audit.append(
                AuditItem(
                    "Warning",
                    "CsvRowMissingJiraKey",
                    message=f"CSV row {row_index} has no Jira key and was skipped.",
                    reviewer_action="Correct the Jira export or remove the blank row.",
                    source_row=row_index,
                )
            )
            continue
        if not JIRA_KEY_RE.fullmatch(key):
            audit.append(
                AuditItem(
                    "Warning",
                    "UnexpectedJiraKeyFormat",
                    jira_key=key,
                    message="Jira key does not match the expected PREFIX-123 format.",
                    reviewer_action="Confirm the row is a valid Jira issue.",
                    source_row=row_index,
                )
            )
        issues.append(
            JiraIssue(
                key=key,
                issue_id=table.get_first(row, columns.get("issue_id", [])),
                issue_type=table.get_first(row, columns["issue_type"]),
                summary=table.get_first(row, columns["summary"]),
                epic_link=table.get_first(row, columns["epic_link"]).upper(),
                parent=table.get_first(row, columns.get("parent", [])).upper(),
                fix_versions=split_multi_values(table.get_all(row, columns.get("fix_versions", []))),
                story_points=parse_number(table.get_first(row, columns["story_points"])),
                logged_hours=parse_logged_hours(
                    table.get_first(row, columns.get("logged_hours", [])),
                    audit,
                    key,
                    row_index,
                ),
                status=table.get_first(row, columns["status"]),
                resolution=table.get_first(row, columns.get("resolution", [])),
                target_start=parse_date(table.get_first(row, columns.get("target_start", [])), audit, key, row_index),
                target_end=parse_date(table.get_first(row, columns.get("target_end", [])), audit, key, row_index),
                predecessors=parse_issue_keys(table.get_all(row, columns.get("predecessors", []))),
                successors=parse_issue_keys(table.get_all(row, columns.get("successors", []))),
                source_row=row_index,
            )
        )
    return issues


def parse_number(value: str) -> Optional[float]:
    if value is None:
        return None
    cleaned = str(value).strip().replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_logged_hours(
    value: str,
    audit: Optional[List[AuditItem]] = None,
    key: str = "",
    row_index: int = 0,
) -> float:
    raw = "" if value is None else str(value).strip()
    if not raw:
        return 0.0
    numeric = parse_number(raw)
    if numeric is not None:
        return numeric
    time_text = raw.lower().replace(",", " ")
    clock_match = re.fullmatch(r"(\d+):([0-5]\d)(?::[0-5]\d)?", time_text)
    if clock_match:
        return round(int(clock_match.group(1)) + int(clock_match.group(2)) / 60, 2)
    multipliers = {
        "w": 40.0,
        "week": 40.0,
        "weeks": 40.0,
        "d": 8.0,
        "day": 8.0,
        "days": 8.0,
        "h": 1.0,
        "hr": 1.0,
        "hrs": 1.0,
        "hour": 1.0,
        "hours": 1.0,
        "m": 1.0 / 60.0,
        "min": 1.0 / 60.0,
        "mins": 1.0 / 60.0,
        "minute": 1.0 / 60.0,
        "minutes": 1.0 / 60.0,
        "s": 1.0 / 3600.0,
        "sec": 1.0 / 3600.0,
        "secs": 1.0 / 3600.0,
        "second": 1.0 / 3600.0,
        "seconds": 1.0 / 3600.0,
    }
    total = 0.0
    matched = False
    for amount, unit in re.findall(r"(-?\d+(?:\.\d+)?)\s*([a-z]+)", time_text):
        multiplier = multipliers.get(unit)
        if multiplier is not None:
            matched = True
            total += float(amount) * multiplier
    if matched:
        return round(total, 2)
    if audit is not None:
        audit.append(
            AuditItem(
                "Warning",
                "UnparsedLoggedHours",
                jira_key=key,
                field="Logged Hours",
                old_value=raw,
                new_value="0",
                message=f"Could not parse logged hours '{raw}' on CSV row {row_index}.",
                reviewer_action="Use decimal hours, HH:MM, or duration text such as 1h 30m.",
                source_row=row_index,
            )
        )
    return 0.0


def parse_date(value: str, audit: List[AuditItem], key: str, row_index: int) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        audit.append(
            AuditItem(
                "Warning",
                "UnparsedDate",
                jira_key=key,
                old_value=raw,
                message=f"Could not parse date '{raw}' on CSV row {row_index}.",
                reviewer_action="Use YYYY-MM-DD or configure a supported export date format.",
                source_row=row_index,
            )
        )
        return raw


def parse_issue_keys(values: Iterable[str]) -> Set[str]:
    keys: Set[str] = set()
    for value in values:
        for match in JIRA_KEY_RE.findall(value.upper()):
            keys.add(match)
    return keys


def split_multi_values(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    for value in values:
        for part in re.split(r"[;\n,]+", value):
            cleaned = part.strip()
            if cleaned and cleaned not in result:
                result.append(cleaned)
    return result


def normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def jira_key_prefix(key: str) -> str:
    return key.split("-", 1)[0].upper() if "-" in key else key.upper()
