"""Deterministic, deliberately small YAML reader for j2p configuration.

Supports indentation-based mappings, scalar lists and inline scalar lists.
Unsupported YAML features fail instead of changing behavior with dependencies.
"""

import json
import math
import re


class YamlSubsetError(ValueError):
    pass


def _unquoted_positions(text, delimiter):
    quote = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote == '"' and char == "\\":
            index += 2
            continue
        if quote == "'" and char == "'" and text[index:index + 2] == "''":
            index += 2
            continue
        if quote:
            if char == quote:
                quote = None
        elif char in {"'", '"'} and (index == 0 or text[index - 1] in " [:,\t"):
            quote = char
        elif char == delimiter:
            yield index
        index += 1
    if quote:
        raise YamlSubsetError("Unterminated quoted scalar.")


def _scalar(value):
    value = value.strip()
    if not value:
        raise YamlSubsetError("Empty list values are not supported.")
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError) as exc:
            raise YamlSubsetError("Double-quoted values must use JSON-compatible escapes.") from exc
        if not isinstance(parsed, str):
            raise YamlSubsetError("Expected a quoted string.")
        return parsed
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'") or re.search(r"(?<!')'(?!')", value[1:-1]):
            raise YamlSubsetError("Invalid single-quoted string; escape apostrophes as ''.")
        return value[1:-1].replace("''", "'")
    if value == "{}":
        return {}
    if value.startswith("["):
        if not value.endswith("]"):
            raise YamlSubsetError("Unterminated inline list.")
        inner = value[1:-1].strip()
        if not inner:
            return []
        positions = [-1, *list(_unquoted_positions(inner, ",")), len(inner)]
        values = [_scalar(inner[start + 1:end]) for start, end in zip(positions, positions[1:])]
        if any(isinstance(item, (dict, list)) for item in values):
            raise YamlSubsetError("Inline lists must contain scalar values only.")
        return values
    if value[0] in "{}&*!|>%@`" or value in {"---", "..."}:
        raise YamlSubsetError("Unsupported YAML feature; use nested mappings and scalar lists.")
    if any(value[pos:pos + 2] == ": " for pos in _unquoted_positions(value, ":")):
        raise YamlSubsetError("Quote values containing ': '.")
    lower = value.lower()
    if lower in {"true", "false"}:
        return lower == "true"
    if lower in {"null", "~"}:
        return None
    if lower in {".nan", ".inf", "-.inf", "+.inf"}:
        raise YamlSubsetError("Non-finite numeric values are not supported.")
    if re.fullmatch(r"[+-]?\d+", value):
        return int(value)
    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?", value):
        number = float(value)
        if not math.isfinite(number):
            raise YamlSubsetError("Non-finite numeric values are not supported.")
        return number
    return value


def parse_yaml(text):
    lines = []
    for number, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw[:len(raw) - len(raw.lstrip())]:
            raise YamlSubsetError(f"Tabs are not supported for indentation on line {number}.")
        try:
            for position in _unquoted_positions(raw, "#"):
                if position == 0 or raw[position - 1].isspace():
                    raw = raw[:position]
                    break
        except YamlSubsetError as exc:
            raise YamlSubsetError(f"Line {number}: {exc}") from exc
        raw = raw.rstrip()
        if raw.strip():
            lines.append((number, len(raw) - len(raw.lstrip()), raw.strip()))
    if not lines:
        return {}
    if lines[0][1] != 0:
        raise YamlSubsetError(f"Top-level mapping must start at column 1 on line {lines[0][0]}.")

    def block(index, indent):
        is_list = lines[index][2].startswith("- ") or lines[index][2] == "-"
        result = [] if is_list else {}
        while index < len(lines) and lines[index][1] >= indent:
            number, current_indent, content = lines[index]
            if current_indent != indent:
                raise YamlSubsetError(f"Unexpected indentation on line {number}.")
            try:
                if is_list:
                    if not content.startswith("- "):
                        raise YamlSubsetError("Expected '- value' list entry.")
                    value = _scalar(content[2:])
                    if isinstance(value, (list, dict)):
                        raise YamlSubsetError("Block lists must contain scalar values only.")
                    result.append(value)
                    index += 1
                    continue
                separators = list(_unquoted_positions(content, ":"))
                separator = next((pos for pos in separators if pos + 1 == len(content) or content[pos + 1].isspace()), None)
                if separator is None:
                    raise YamlSubsetError("Expected 'key: value'.")
                key = _scalar(content[:separator])
                if not isinstance(key, str) or not key or key == "<<":
                    raise YamlSubsetError("Mapping keys must be nonempty strings; merges are unsupported.")
                if key in result:
                    raise YamlSubsetError(f"Duplicate key {key!r}.")
                raw_value = content[separator + 1:].strip()
                index += 1
                if raw_value:
                    result[key] = _scalar(raw_value)
                elif index < len(lines) and lines[index][1] > indent:
                    result[key], index = block(index, lines[index][1])
                else:
                    result[key] = {}
            except YamlSubsetError as exc:
                raise YamlSubsetError(f"Line {number}: {exc}") from exc
        return result, index

    parsed, index = block(0, 0)
    if index != len(lines) or not isinstance(parsed, dict):
        raise YamlSubsetError("Configuration must be one top-level mapping.")
    return parsed
