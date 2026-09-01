"""Small formatting helpers shared by reports and planning."""

from __future__ import annotations

import html
from typing import Any


def format_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "" if value is None else str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def html_escape(value: Any) -> str:
    return html.escape("" if value is None else str(value))
