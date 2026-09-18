"""Jira CSV reading and field parsing."""

from __future__ import annotations

import csv
import io
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .models import AuditItem, J2PError, JiraIssue


JIRA_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
SINGLE_BYTE_FALLBACK_ENCODINGS = ("cp1252", "latin-1")
DEFAULT_MAX_FIELD_CHARS = 8 * 1024 * 1024
NUMBER_RE = re.compile(r"[+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
AGGREGATE_HOURS_HEADERS = {"σ time spent", "aggregate time spent"}


class CsvTable:
    def __init__(self, path: Path, max_field_chars: int = DEFAULT_MAX_FIELD_CHARS) -> None:
        self.path = path
        try:
            rows, self.encoding = read_csv_rows(path, max_field_chars)
        except OSError as exc:
            raise J2PError(f"Could not read Jira CSV {path}: {exc.strerror}") from exc
        if not rows:
            raise J2PError(f"CSV is empty: {path}")
        self.headers = [header.strip() for header in rows[0]]
        self.row_numbers = [index for index, row in enumerate(rows[1:], start=2) if any(cell.strip() for cell in row)]
        self.rows = [row for row in rows[1:] if any(cell.strip() for cell in row)]
        for number, row in zip(self.row_numbers, self.rows):
            if len(row) != len(self.headers):
                raise J2PError(
                    f"CSV {path}, row {number}: expected {len(self.headers)} columns, got {len(row)}. "
                    "Re-export the file or correct its quoting and trailing empty columns."
                )
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
        return self.get_first_with_header(row, candidates)[1]

    def get_first_with_header(self, row: Sequence[str], candidates: Sequence[str]) -> Tuple[str, str]:
        for candidate in candidates:
            for index in self.header_index.get(normalize_header(candidate), []):
                if index < len(row) and row[index].strip():
                    return self.headers[index], row[index].strip()
        return self.selected_header(candidates), ""


def read_csv_rows(path: Path, max_field_chars: int = DEFAULT_MAX_FIELD_CHARS) -> Tuple[List[List[str]], str]:
    raw = path.read_bytes()
    attempted: List[str] = []
    failures: List[str] = []
    for encoding in csv_encoding_candidates(raw):
        if encoding in attempted:
            continue
        attempted.append(encoding)
        try:
            text = raw.decode(encoding)
        except UnicodeError as exc:
            failures.append(f"{encoding}: {exc}")
            continue
        if decoded_text_looks_binary(text):
            failures.append(f"{encoding}: decoded text contains NUL characters")
            continue
        previous_limit = csv.field_size_limit(max_field_chars)
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        try:
            return list(reader), encoding
        except csv.Error as exc:
            raise J2PError(
                f"Could not parse CSV {path} near physical line {reader.line_num}: {exc}. "
                f"Configured maximum field length: {max_field_chars} characters."
            ) from exc
        finally:
            csv.field_size_limit(previous_limit)
    attempted_text = ", ".join(attempted)
    failure_text = "; ".join(failures)
    details = f" Details: {failure_text}" if failure_text else ""
    raise J2PError(
        "Could not decode Jira CSV. "
        f"Tried these encodings: {attempted_text}. "
        "Re-export the Jira CSV as UTF-8, UTF-16, or Windows CSV and retry."
        f"{details}"
    )


def csv_encoding_candidates(raw: bytes) -> List[str]:
    if raw_has_utf16_shape(raw):
        return ["utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", *SINGLE_BYTE_FALLBACK_ENCODINGS]
    return ["utf-8-sig", *SINGLE_BYTE_FALLBACK_ENCODINGS]


def raw_has_utf16_shape(raw: bytes) -> bool:
    sample = raw[:4096]
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True
    return bool(sample) and sample.count(b"\x00") / len(sample) > 0.1


def decoded_text_looks_binary(text: str) -> bool:
    sample = text[:4096]
    return "\x00" in sample


def parse_issues(table: CsvTable, config: Dict[str, Any], audit: List[AuditItem]) -> List[JiraIssue]:
    issues: List[JiraIssue] = []
    columns = config["columns"]
    for row_index, row in zip(table.row_numbers, table.rows):
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
        point_header, point_value = table.get_first_with_header(row, columns["story_points"])
        hours_header, hours_value = table.get_first_with_header(row, columns.get("logged_hours", []))
        metrics = config.get("metrics", {})
        if hours_value and normalize_header(hours_header) in AGGREGATE_HOURS_HEADERS and metrics.get("logged_hours_source", "direct") != "aggregate":
            raise J2PError(
                f"CSV {table.path}, row {row_index}, Jira key {key}, field {hours_header!r}: "
                "aggregate logged time requires metrics.logged_hours_source: aggregate."
            )
        hours_unit = metrics.get("logged_hours_units", {}).get(
            normalize_header(hours_header), metrics.get("logged_hours_unit", "hours")
        )
        context = f"CSV {table.path}, row {row_index}, Jira key {key}"
        issues.append(
            JiraIssue(
                key=key,
                issue_id=table.get_first(row, columns.get("issue_id", [])),
                issue_type=table.get_first(row, columns["issue_type"]),
                summary=table.get_first(row, columns["summary"]),
                epic_link=table.get_first(row, columns["epic_link"]).upper(),
                parent=table.get_first(row, columns.get("parent", [])).upper(),
                fix_versions=split_multi_values(table.get_all(row, columns.get("fix_versions", []))),
                story_points=parse_number(point_value, context=context, field=point_header or "Story Points"),
                logged_hours=parse_logged_hours(
                    hours_value,
                    audit,
                    key,
                    row_index,
                    numeric_unit=hours_unit,
                    source_file=str(table.path),
                    field=hours_header or "Logged Hours",
                ),
                status=table.get_first(row, columns["status"]),
                resolution=table.get_first(row, columns.get("resolution", [])),
                resolved=parse_date(table.get_first(row, columns.get("resolved", [])), audit, key, row_index),
                target_start=parse_date(table.get_first(row, columns.get("target_start", [])), audit, key, row_index),
                target_end=parse_date(table.get_first(row, columns.get("target_end", [])), audit, key, row_index),
                warning_suppression_date=parse_date(
                    table.get_first(row, columns.get("warning_suppression_date", [])),
                    audit,
                    key,
                    row_index,
                ),
                predecessors=parse_issue_keys(table.get_all(row, columns.get("predecessors", []))),
                successors=parse_issue_keys(table.get_all(row, columns.get("successors", []))),
                source_row=row_index,
                source_file=str(table.path),
            )
        )
    return issues


def parse_number(value: str, *, context: str = "CSV", field: str = "numeric value") -> Optional[float]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        number = float(raw.replace(",", ""))
    except ValueError:
        raise J2PError(f"{context}, field={field!r}: invalid numeric value {raw!r}.") from None
    if not math.isfinite(number):
        raise J2PError(f"{context}, field={field!r}: value {raw!r} must be finite; NaN and infinity are not supported.")
    if not NUMBER_RE.fullmatch(raw):
        raise J2PError(f"{context}, field={field!r}: invalid numeric format {raw!r}; use decimal numbers or grouped thousands.")
    if number < 0:
        raise J2PError(f"{context}, field={field!r}: value {raw!r} must be nonnegative.")
    return number


def parse_logged_hours(
    value: str,
    audit: Optional[List[AuditItem]] = None,
    key: str = "",
    row_index: int = 0,
    *,
    numeric_unit: str = "hours",
    source_file: str = "",
    field: str = "Logged Hours",
) -> float:
    raw = "" if value is None else str(value).strip()
    if not raw:
        return 0.0
    context = f"CSV {source_file or '<input>'}, row {row_index}, Jira key {key or '<unknown>'}"
    unit_divisors = {"hours": 1.0, "minutes": 60.0, "seconds": 3600.0}
    if numeric_unit not in unit_divisors:
        raise J2PError(f"{context}, field={field!r}: unsupported numeric time unit {numeric_unit!r}.")
    if NUMBER_RE.fullmatch(raw) or raw.casefold() in {"nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}:
        return parse_number(raw, context=context, field=field) / unit_divisors[numeric_unit]
    time_text = raw.lower().replace(",", " ")
    clock_match = re.fullmatch(r"(\d+):([0-5]\d)(?::([0-5]\d))?", time_text)
    if clock_match:
        return int(clock_match.group(1)) + int(clock_match.group(2)) / 60 + int(clock_match.group(3) or 0) / 3600
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
    position = 0
    for match in re.finditer(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([a-z]+)", time_text):
        if time_text[position:match.start()].strip():
            break
        amount, unit = match.groups()
        multiplier = multipliers.get(unit)
        if multiplier is None:
            break
        total += parse_number(amount, context=context, field=field) * multiplier
        position = match.end()
    if position and not time_text[position:].strip() and math.isfinite(total):
        return total
    raise J2PError(
        f"{context}, field={field!r}: could not parse complete logged time {raw!r}. "
        "Use a nonnegative number in the configured unit, HH:MM[:SS], or duration text such as 1h 30m."
    )


def parse_date(value: str, audit: List[AuditItem], key: str, row_index: int) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    for candidate in date_parse_candidates(raw):
        for fmt in (
            "%Y-%m-%d",
            "%m/%d/%Y",
            "%m/%d/%y",
            "%Y/%m/%d",
            "%d-%b-%Y",
            "%d-%b-%y",
            "%d/%b/%Y",
            "%d/%b/%y",
            "%d %b %Y",
            "%d %b %y",
        ):
            try:
                return datetime.strptime(candidate, fmt).date().isoformat()
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
                reviewer_action="Use YYYY-MM-DD, DD-MON-YY, or configure a supported export date format.",
                source_row=row_index,
            )
        )
        return raw


def date_parse_candidates(raw: str) -> List[str]:
    candidates: List[str] = []
    for candidate in (
        raw,
        raw.replace("Z", ""),
        re.split(r"[T\s,]+", raw, maxsplit=1)[0],
    ):
        cleaned = candidate.strip().strip('"').strip("'")
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)
    return candidates


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
