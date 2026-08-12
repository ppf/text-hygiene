"""Scanning and cleaning. No I/O lives here.

`clean` applies exactly what `scan` reported, so the context rules exist in one
place; a parallel implementation would let the two drift and make the scan/clean
agreement invariant unenforceable.
"""

from __future__ import annotations

import bisect
from collections import Counter
from dataclasses import dataclass

from .chars import FLAG_BASE, PROSE_ALLOWED_CF, TAG_SPEC, TAG_TERM, char_name, classify

PROFILES = ("prose", "code")

STRIP = "strip"
REPLACE = "replace"
REPORT = "report"

# Classes whose verdict depends on what survives around them.
CONTEXT_SENSITIVE = frozenset({"joiner", "variation", "zero_width"})


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


def _resolve(category: str, profile: str, codepoint: int, leading: bool,
             between_ascii: bool) -> tuple[str | None, str]:
    """Return (action, replacement) for one classified character."""
    if category == "bom":
        # A leading BOM is an encoding artifact, not a carrier; only mid-file
        # occurrences are suspicious.
        return (REPORT, "") if leading else (STRIP, "")

    if category in ("soft_hyphen", "control", "tag"):
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

    # Unreachable while every classify() output has a branch above. Raising rather
    # than returning None stops a future class from being detected and then
    # silently ignored; test_core.py asserts exhaustiveness.
    raise AssertionError(f"unhandled character class: {category}")


def _survivors(text: str, classes: list[str | None], protected: set[int],
               verdicts: list[tuple[str | None, str] | None]) -> list[str | None]:
    """What each position contributes to the cleaned text, or None if nothing.

    Context-sensitive positions are None because their verdict is not settled yet,
    which makes them transparent to each other so a doubled ZWJ cannot evade the
    adjacency test.
    """
    out: list[str | None] = []
    for i, ch in enumerate(text):
        category = classes[i]
        if category is None or i in protected:
            out.append(ch)
        elif category in CONTEXT_SENSITIVE:
            out.append(None)
        else:
            action, replacement = verdicts[i] or (None, "")
            out.append(None if action == STRIP
                       else replacement if action == REPLACE else ch)
    return out


def _between_ascii(survivors: list[str | None], i: int) -> bool:
    """Whether the nearest surviving characters on both sides are ASCII.

    Neighbours come from the survivor view rather than the raw text, so a character
    that cleaning is about to remove - a mid-file BOM, a C1 control - cannot stand
    in as a non-ASCII neighbour and shield a carrier from removal. Deciding against
    what survives is also what makes cleaning idempotent: a second pass sees the
    neighbours the first pass already assumed.
    """
    def nearest(step: int) -> str | None:
        j = i + step
        while 0 <= j < len(survivors):
            if survivors[j] is not None:
                return survivors[j]
            j += step
        return None

    return all(n is None or ord(n) < 128 for n in (nearest(-1), nearest(1)))


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

    # Settle every context-free verdict first so the survivor view below reflects
    # what cleaning will actually leave behind.
    verdicts: list[tuple[str | None, str] | None] = [None] * len(text)
    for i, category in enumerate(classes):
        if category is None or i in protected or category in CONTEXT_SENSITIVE:
            continue
        verdicts[i] = _resolve(category, profile, ord(text[i]),
                               leading=(i == 0), between_ascii=False)

    survivors = _survivors(text, classes, protected, verdicts)
    for i, category in enumerate(classes):
        if category in CONTEXT_SENSITIVE and i not in protected:
            verdicts[i] = _resolve(category, profile, ord(text[i]),
                                   leading=(i == 0),
                                   between_ascii=_between_ascii(survivors, i))

    starts = _line_starts(text)
    findings = []
    for i, verdict in enumerate(verdicts):
        if verdict is None or verdict[0] is None:
            continue
        action, replacement = verdict
        line = bisect.bisect_right(starts, i)
        findings.append(Finding(
            index=i,
            line=line,
            column=i - starts[line - 1] + 1,
            char=text[i],
            codepoint=ord(text[i]),
            name=char_name(text[i]),
            category=classes[i],
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
