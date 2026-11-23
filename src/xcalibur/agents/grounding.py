"""Numbers in an answer must come from tool results (issue #9).

A tool marked ``grounded=True`` returns facts about data (counts, eigenvalues, support
values). When a turn used such a tool, :class:`~xcalibur.agents.runtime.AgentLoop` checks
the final answer: every number written in it must match a number in those results, or in
the user's own message, at the precision the answer shows (``78.2`` matches ``78.18``).
The model gets one chance to correct an answer that fails; if it still fails, the answer
is shown with a warning listing the numbers that could not be traced.

This is a safety net, not a proof: numbers written as words, and numbers that happen to
appear in a result for another reason, are not caught.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

# A number on its own: not part of a word or identifier (PC1, SA2, R1, run_3), not a
# version or a date fragment glued to letters. Commas and points may be decimal marks.
_NUMBER = re.compile(r"(?<![\w.,/-])-?\d+(?:[.,]\d+)*(?![\w])")
# Leading list markers ("1. ", "2) ") are not data.
_ENUMERATION = re.compile(r"^(\s*)\d+[.)](?=\s)", re.MULTILINE)


def numbers_in_text(text: str) -> list[str]:
    """Number tokens as written in ``text`` (list markers excluded), in order."""
    return _NUMBER.findall(_ENUMERATION.sub(r"\1", text))


def source_values(sources: Iterable[Any]) -> list[float]:
    """Every number in ``sources``: JSON-like data or text (numbers inside strings too)."""
    values: list[float] = []
    for source in sources:
        text = source if isinstance(source, str) else json.dumps(source, default=str)
        for token in _NUMBER.findall(text):
            values.extend(_readings(token))
        # Exponent notation (4.8e-16) is data, but the pattern above splits it.
        for token in re.findall(r"-?\d+(?:\.\d+)?[eE][-+]?\d+", text):
            values.append(float(token))
    return values


def ungrounded_numbers(text: str, sources: Iterable[Any]) -> list[str]:
    """Numbers in ``text`` that no value in ``sources`` supports, without repeats."""
    values = source_values(sources)
    missing: list[str] = []
    for token in numbers_in_text(text):
        if token in missing:
            continue
        if not any(_matches(reading, decimals, values)
                   for reading, decimals in _readings_with_precision(token)):
            missing.append(token)
    return missing


def _matches(number: float, decimals: int, values: list[float]) -> bool:
    tolerance = 0.5 * 10 ** -decimals + 1e-9
    return any(abs(number - v) <= tolerance for v in values)


def _readings(token: str) -> list[float]:
    return [value for value, _ in _readings_with_precision(token)]


def _readings_with_precision(token: str) -> list[tuple[float, int]]:
    """``1,5`` may be 1.5 (decimal comma) or 15 (thousands); ``1.000`` likewise."""
    sign = -1.0 if token.startswith("-") else 1.0
    body = token.lstrip("-")
    out: list[tuple[float, int]] = []
    marks = [m for m in (".", ",") if m in body]
    if not marks:
        return [(sign * float(body), 0)]
    # One kind of mark used once: a decimal mark.
    for mark in marks:
        if body.count(mark) == 1 and len(marks) == 1:
            whole, frac = body.split(mark)
            out.append((sign * float(f"{whole}.{frac}"), len(frac)))
    # Marks as thousands separators (1.234.567 or 1,234), the last one maybe decimal.
    for decimal in (".", ","):
        thousands = "," if decimal == "." else "."
        cleaned = body.replace(thousands, "")
        if cleaned.count(decimal) <= 1 and cleaned.replace(decimal, "").isdigit():
            frac = cleaned.split(decimal)[1] if decimal in cleaned else ""
            out.append((sign * float(cleaned.replace(decimal, ".")), len(frac)))
    return out
