"""Scanning and cleaning. No I/O lives here.

`clean` is defined as `apply(scan(...))` so the context rules exist in exactly one
place; a parallel implementation would let the two drift and make the scan/clean
agreement invariant unenforceable.
"""

from __future__ import annotations

import bisect
from collections import Counter
from dataclasses import dataclass

from .chars import (BOM, FLAG_BASE, PROSE_ALLOWED_CF, TAG_SPEC, TAG_TERM,
                    TRANSPARENT, char_name, classify)

PROFILES = ("prose", "code")

STRIP = "strip"
REPLACE = "replace"
REPORT = "report"

CONTEXT_SENSITIVE = frozenset({"joiner", "variation"})


@dataclass(frozen=True)
class Finding:
    index: int
    line: int
    column: int
    char: str
    codepoint: int
    name: str
    category: str
    action: str
    replacement: str = ""

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "line": self.line,
            "column": self.column,
            "codepoint": f"U+{self.codepoint:04X}",
            "name": self.name,
            "class": self.category,
            "action": self.action,
            "replacement": self.replacement,
        }


def _flag_sequence_indices(text: str) -> set[int]:
    """Indices belonging to a well-formed emoji tag sequence, which stays intact.

    The U+E007F terminator is part of the sequence; dropping it breaks every
    subdivision flag.
    """
    protected: set[int] = set()
    i, n = 0, len(text)
    while i < n:
        if ord(text[i]) == FLAG_BASE:
            j = i + 1
            while j < n and ord(text[j]) in TAG_SPEC:
                j += 1
            if j > i + 1 and j < n and ord(text[j]) == TAG_TERM:
                protected.update(range(i, j + 1))
                i = j + 1
                continue
        i += 1
    return protected


def _neighbour(text: str, classes: list[str | None], i: int, step: int) -> str | None:
    """First neighbour that is not itself an invisible carrier.

    Skipping carriers is what stops a doubled ZWJ from evading the adjacency test.
    It is also why cleaning is idempotent: the characters that are about to be
    removed are already transparent to the rule that decides removals.
    """
    j = i + step
    while 0 <= j < len(text):
        if classes[j] not in TRANSPARENT:
            return text[j]
        j += step
    return None


def _between_ascii(text: str, classes: list[str | None], i: int) -> bool:
    left = _neighbour(text, classes, i, -1)
    right = _neighbour(text, classes, i, 1)
    return all(n is None or ord(n) < 128 for n in (left, right))


def _resolve(category: str, profile: str, codepoint: int, leading: bool,
             between_ascii: bool) -> tuple[str | None, str]:
    """Return (action, replacement) for one classified character."""
    if category == "bom":
        # A leading BOM is an encoding artifact, not a carrier; only mid-file
        # occurrences are suspicious.
        return (REPORT, "") if leading else (STRIP, "")

    if category in ("zero_width", "soft_hyphen", "control", "tag"):
        return STRIP, ""

    if category == "other_format":
        if profile == "prose" and codepoint in PROSE_ALLOWED_CF:
            return None, ""
        return STRIP, ""

    if category in CONTEXT_SENSITIVE:
        if profile == "code":
            return STRIP, ""
        return (STRIP, "") if between_ascii else (REPORT, "")

    if category == "bidi":
        # Report-only in prose: stripping bidi from genuinely RTL text is itself a
        # silent rendering change. No balance analysis - balance is attacker
        # controlled, so it cannot gate a security decision.
        return (STRIP, "") if profile == "code" else (REPORT, "")

    if category == "space":
        return (REPLACE, " ") if profile == "code" else (REPORT, "")

    if category == "line_sep":
        return (REPLACE, "\n") if profile == "code" else (REPORT, "")

    if category in ("hangul_filler", "cgj"):
        return (STRIP, "") if profile == "code" else (REPORT, "")

    return None, ""


def _line_starts(text: str) -> list[int]:
    # split("\n"), never splitlines(): the latter also breaks on U+2028/2029/0085,
    # so reported line numbers would disagree with the editor's on exactly the
    # files this tool exists to inspect.
    starts, offset = [0], 0
    for line in text.split("\n")[:-1]:
        offset += len(line) + 1
        starts.append(offset)
    return starts


def scan(text: str, profile: str = "prose") -> list[Finding]:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile: {profile}")

    classes = [classify(ch) for ch in text]
    protected = _flag_sequence_indices(text)
    starts = _line_starts(text)
    findings = []

    for i, (ch, category) in enumerate(zip(text, classes)):
        if category is None or i in protected:
            continue
        between = (_between_ascii(text, classes, i)
                   if category in CONTEXT_SENSITIVE else False)
        action, replacement = _resolve(category, profile, ord(ch),
                                       leading=(i == 0), between_ascii=between)
        if action is None:
            continue
        line = bisect.bisect_right(starts, i)
        findings.append(Finding(
            index=i,
            line=line,
            column=i - starts[line - 1] + 1,
            char=ch,
            codepoint=ord(ch),
            name=char_name(ch),
            category=category,
            action=action,
            replacement=replacement,
        ))
    return findings


def clean(text: str, profile: str = "prose") -> tuple[str, list[Finding]]:
    """Return (cleaned text, all findings). Report-only findings do not alter text."""
    findings = scan(text, profile)
    acted = {f.index: f for f in findings if f.action in (STRIP, REPLACE)}
    out = []
    for i, ch in enumerate(text):
        finding = acted.get(i)
        if finding is None:
            out.append(ch)
        elif finding.action == REPLACE:
            out.append(finding.replacement)
    return "".join(out), findings


def summarize(findings: list[Finding]) -> dict[str, int]:
    return dict(Counter(f"{f.category}/{f.action}" for f in findings))
