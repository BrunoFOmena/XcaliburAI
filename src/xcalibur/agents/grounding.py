"""Numbers in an answer must come from tool results (issues #9 and #68).

A tool marked ``grounded=True`` returns facts about data (counts, eigenvalues, support
values). When a turn used such a tool, :class:`~xcalibur.agents.runtime.AgentLoop` checks
the final answer: every number written in it must match a number in those results, or in
the user's own message, at the precision the answer shows (``78.2`` matches ``78.18``).
The model gets one chance to correct an answer that fails; if it still fails, the answer
is shown with a warning listing the numbers that could not be traced.

Rules (#68):

* Only **numeric values** of a result count as sources. Numbers inside its strings (a
  rule such as ``"UFBoot >= 95"``, a note, a caveat) do not, so they cannot vouch for an
  unrelated claim; a tool that wants a threshold to be quotable returns it as a number.
* A **percentage** in the answer (``95%``, ``95 por cento``, ``95 percent``) must match a
  value whose key contains ``percent`` or ``pct``.
* Numbers **written as words** from zero to one hundred are checked too, in Portuguese
  and English (``seis``, ``vinte e sete``, ``twenty-seven``). ``um``, ``uma`` and ``one``
  are left out: they are mostly articles or pronouns.
* The user's message counts as a source for any number it contains.

It is a safety net, not a proof: a wrong number that equals another value of the results
of the same kind still passes.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# A number on its own: not part of a word or identifier (PC1, SA2, R1, run_3), not an
# issue or PR reference (#52), not glued to letters. Points and commas may be decimal
# marks or thousands separators.
_NUMBER = re.compile(r"(?<![\w.,/#-])-?\d+(?:[.,]\d+)*(?![\w])")
_EXPONENT = re.compile(r"-?\d+(?:\.\d+)?[eE][-+]?\d+")
# Leading list markers ("1. ", "2) ") are not data.
_ENUMERATION = re.compile(r"^(\s*)\d+[.)](?=\s)", re.MULTILINE)
_PERCENT_AFTER = re.compile(r"\s?(?:%|por\s?cento\b|percent\b|per\s?cent\b)", re.IGNORECASE)
_PERCENT_KEY = re.compile(r"percent|pct", re.IGNORECASE)

_UNITS = {
    "zero": 0, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6,
    "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13,
    "catorze": 14, "quatorze": 14, "quinze": 15, "dezesseis": 16, "dezasseis": 16,
    "dezessete": 17, "dezassete": 17, "dezoito": 18, "dezenove": 19, "dezanove": 19,
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50, "sessenta": 60,
    "setenta": 70, "oitenta": 80, "noventa": 90,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
_HUNDRED = {"cem": 100, "hundred": 100}
# Units that may follow a ten ("vinte e um", "twenty-one"): here "um/one" is a number.
_TAIL_UNITS = {**{k: v for k, v in _UNITS.items() if 0 < v < 10}, "um": 1, "uma": 1,
               "one": 1}
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


@dataclass(frozen=True)
class Mention:
    """A number as written in the answer."""

    text: str
    readings: tuple[tuple[float, int], ...]
    """(value, decimals shown) for every way the token can be read."""
    percent: bool


def numbers_in_text(text: str) -> list[str]:
    """Number tokens as written in ``text`` (digits and words), in order."""
    return [m.text for m in mentions(text)]


def mentions(text: str) -> list[Mention]:
    cleaned = _ENUMERATION.sub(r"\1", text)
    found: list[tuple[int, Mention]] = []
    for match in _NUMBER.finditer(cleaned):
        token = match.group(0)
        percent = bool(_PERCENT_AFTER.match(cleaned, match.end()))
        found.append((match.start(), Mention(token, _readings(token), percent)))
    found.extend(_word_mentions(cleaned))
    return [m for _, m in sorted(found, key=lambda item: item[0])]


def ungrounded_numbers(text: str, sources: Iterable[Any]) -> list[str]:
    """Numbers of ``text`` that no source supports, without repeats.

    A ``str`` source is free text (the user's message): any number in it counts. Any
    other source is data (a tool result): only its numeric values count, and those under
    a key containing ``percent``/``pct`` are the only ones that can back a percentage.
    """
    values: list[float] = []
    percents: list[float] = []
    for source in sources:
        if isinstance(source, str):
            for mention in mentions(source):
                numbers = [value for value, _ in mention.readings]
                values.extend(numbers)
                percents.extend(numbers)
            percents.extend(float(t) for t in _EXPONENT.findall(source))
        else:
            _walk(source, False, values, percents)
    missing: list[str] = []
    for mention in mentions(text):
        if mention.text in missing:
            continue
        pool = percents if mention.percent else values
        if not any(_matches(value, decimals, pool) for value, decimals in mention.readings):
            missing.append(mention.text)
    return missing


def _walk(data: Any, in_percent: bool, values: list[float], percents: list[float]) -> None:
    """Collect numeric leaves; strings are skipped on purpose."""
    if isinstance(data, bool) or data is None or isinstance(data, str):
        return
    if isinstance(data, (int, float)):
        values.append(float(data))
        if in_percent:
            percents.append(float(data))
    elif isinstance(data, dict):
        for key, value in data.items():
            _walk(value, in_percent or bool(_PERCENT_KEY.search(str(key))), values, percents)
    elif isinstance(data, (list, tuple, set)):
        for item in data:
            _walk(item, in_percent, values, percents)


def _matches(number: float, decimals: int, pool: list[float]) -> bool:
    tolerance = 0.5 * 10 ** -decimals + 1e-9
    return any(abs(number - v) <= tolerance for v in pool)


def _readings(token: str) -> tuple[tuple[float, int], ...]:
    """``1,5`` may be 1.5 (decimal comma) or 15 (thousands); ``1.000`` likewise."""
    sign = -1.0 if token.startswith("-") else 1.0
    body = token.lstrip("-")
    out: list[tuple[float, int]] = []
    marks = [m for m in (".", ",") if m in body]
    if not marks:
        return ((sign * float(body), 0),)
    for mark in marks:  # one kind of mark used once: a decimal mark
        if body.count(mark) == 1 and len(marks) == 1:
            whole, frac = body.split(mark)
            out.append((sign * float(f"{whole}.{frac}"), len(frac)))
    for decimal in (".", ","):  # marks as thousands separators, the last one maybe decimal
        thousands = "," if decimal == "." else "."
        cleaned = body.replace(thousands, "")
        if cleaned.count(decimal) <= 1 and cleaned.replace(decimal, "").isdigit():
            frac = cleaned.split(decimal)[1] if decimal in cleaned else ""
            out.append((sign * float(cleaned.replace(decimal, ".")), len(frac)))
    return tuple(out)


def _word_mentions(text: str) -> list[tuple[int, Mention]]:
    words = [(m.start(), m.end(), _fold(m.group(0))) for m in _WORD.finditer(text)]
    out: list[tuple[int, Mention]] = []
    i = 0
    while i < len(words):
        start, end, word = words[i]
        value: int | None = None
        used = 1
        if word in _HUNDRED:
            value = 100
        elif word in _TENS:
            value = _TENS[word]
            # "vinte e sete" / "twenty seven" / "twenty-seven"
            if i + 2 < len(words) and words[i + 1][2] == "e" and words[i + 2][2] in _TAIL_UNITS:
                value += _TAIL_UNITS[words[i + 2][2]]
                used = 3
            elif i + 1 < len(words) and words[i + 1][2] in _TAIL_UNITS and \
                    text[end:words[i + 1][0]] in ("-", " "):
                value += _TAIL_UNITS[words[i + 1][2]]
                used = 2
        elif word in _UNITS:
            value = _UNITS[word]
        if value is None:
            i += 1
            continue
        last_end = words[i + used - 1][1]
        percent = bool(_PERCENT_AFTER.match(text, last_end))
        out.append((start, Mention(text[start:last_end], ((float(value), 0),), percent)))
        i += used
    return out


def _fold(word: str) -> str:
    """Lowercase without accents ("três" -> "tres")."""
    decomposed = unicodedata.normalize("NFKD", word.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))
