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
ALLOW = "allow"

# REPORT and ALLOW both leave the character alone, for different reasons, and a
# commit gate has to tell them apart. ALLOW means context proved the character
# legitimate here - a joiner inside an emoji sequence, NBSP in prose, a leading BOM.
# REPORT means policy declines to modify it but it is still worth a human look - a
# bidi override, a Hangul filler. A gate that fails on ALLOW cannot be satisfied,
# because `clean` will not remove those; a gate that ignores REPORT lets Trojan
# Source into a README.
UNMODIFIED = (REPORT, ALLOW)

# Classes whose verdict depends on what survives around them.
#
# zero_width is deliberately NOT here. Making it context-sensitive means a single
# non-ASCII character shields every carrier near it: an em dash, a curly quote or
# any non-Latin script is enough, and LLM output is full of the first two. Appending
# one em dash then hides an unlimited run of zero-width spaces. That is the tool's
# primary use case, so the class is stripped unconditionally instead - see
# ZERO_WIDTH in chars.py for what that costs.
CONTEXT_SENSITIVE = frozenset({"joiner", "variation", "other_format"})


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


# A subdivision flag carries an ISO 3166-2 code: every tag sequence in the RGI set
# is 5 lowercase alphanumerics (gbeng, gbsct, gbwls). Accepting any tag run made the
# construct an unbounded silent carrier - wrap arbitrary text in the flag base and
# terminator and scan returned nothing at all, because protected indices are skipped
# before they are ever classified. These bounds still admit a future RGI flag.
MAX_TAG_PAYLOAD = 6


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
            payload = [chr(ord(c) - 0xE0000) for c in text[i + 1:j]]
            if (2 <= len(payload) <= MAX_TAG_PAYLOAD
                    and all(c.isalnum() and c.islower() and c.isascii() for c in payload)
                    and j < n and ord(text[j]) == TAG_TERM):
                protected.update(range(i, j + 1))
                i = j + 1
                continue
        i += 1
    return protected


def _resolve(category: str, profile: str, codepoint: int, leading: bool,
             between_ascii: bool, oversized_run: bool = False) -> tuple[str | None, str]:
    """Return (action, replacement) for one classified character."""
    if category == "bom":
        # A leading BOM is an encoding artifact, not a carrier; only mid-file
        # occurrences are suspicious.
        return (ALLOW, "") if leading else (STRIP, "")

    if category in ("zero_width", "soft_hyphen", "control", "tag"):
        return STRIP, ""

    if category == "other_format":
        if profile == "prose" and codepoint in PROSE_ALLOWED_CF:
            # The allowlist earns its keep in Arabic text, not in English. Without
            # the adjacency check these 14 codepoints were a silent carrier in
            # pure-ASCII prose - no finding at all, so nothing to grep and nothing
            # for a gate to block.
            return (STRIP, "") if between_ascii else (ALLOW, "")
        return STRIP, ""

    if category in CONTEXT_SENSITIVE:
        if profile == "code" or oversized_run:
            return STRIP, ""
        # Surviving the adjacency test is positive evidence the character is doing
        # a job here, so this is ALLOW rather than REPORT.
        return (STRIP, "") if between_ascii else (ALLOW, "")

    if category == "bidi":
        # Not stripped in prose: removing bidi from genuinely RTL text is itself a
        # silent rendering change. No balance analysis - balance is attacker
        # controlled, so it cannot gate a security decision. REPORT, not ALLOW:
        # an unannounced direction override is the Trojan Source attack.
        return (STRIP, "") if profile == "code" else (REPORT, "")

    if category == "space":
        return (REPLACE, " ") if profile == "code" else (ALLOW, "")

    if category == "line_sep":
        return (REPLACE, "\n") if profile == "code" else (ALLOW, "")

    if category in ("hangul_filler", "cgj"):
        # Stripped in both profiles so the gate stays satisfiable: blocking these as
        # REPORT left no way to comply, since clean would not remove them. The same
        # trade as ZERO_WIDTH - a Hangul filler does render isolated jamo and CGJ
        # blocks collation reordering, so this costs those rare uses.
        return STRIP, ""

    # Unreachable while every classify() output has a branch above. Raising rather
    # than returning None stops a future class from being detected and then
    # silently ignored.
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


# The longest run of consecutive context-sensitive characters anywhere in Unicode's
# RGI emoji set is 2 - VS16 followed by ZWJ, as in the heart-on-fire sequence. A real
# ZWJ sequence never repeats a joiner and a variation selector never repeats, so a
# longer run is not doing typographic work. Without this bound a single non-ASCII
# neighbour made an arbitrarily long carrier run ALLOW, which no gate blocks and
# `clean` will not remove: ZWJ and ZWNJ alone are a complete binary alphabet.
MAX_CONTEXT_RUN = 2


def _context_run_lengths(classes: list[str | None], protected: set[int]) -> list[int]:
    """For each index, the length of the maximal context-sensitive run it sits in."""
    lengths = [0] * len(classes)
    start = None
    for i in range(len(classes) + 1):
        inside = (i < len(classes) and classes[i] in CONTEXT_SENSITIVE
                  and i not in protected)
        if inside and start is None:
            start = i
        elif not inside and start is not None:
            for j in range(start, i):
                lengths[j] = i - start
            start = None
    return lengths


def _nearest_surviving(survivors: list[str | None]) -> tuple[list, list]:
    """Nearest surviving character to the left and right of every index.

    Two linear passes rather than an outward walk per character: the walk was
    quadratic, and a run of carriers is exactly the input that triggers it - 16k
    joiners took ~9s and the 10 MiB input limit allowed a payload that never
    returned, hanging any hook that ran over it.
    """
    n = len(survivors)
    left: list[str | None] = [None] * n
    seen = None
    for i in range(n):
        left[i] = seen
        if survivors[i] is not None:
            seen = survivors[i]
    right: list[str | None] = [None] * n
    seen = None
    for i in range(n - 1, -1, -1):
        right[i] = seen
        if survivors[i] is not None:
            seen = survivors[i]
    return left, right


def _between_ascii(left: str | None, right: str | None) -> bool:
    """Whether the nearest surviving characters on both sides are ASCII.

    Neighbours come from the survivor view rather than the raw text, so a character
    that cleaning is about to remove - a mid-file BOM, a C1 control - cannot stand
    in as a non-ASCII neighbour and shield a carrier from removal. Deciding against
    what survives is also what makes cleaning idempotent: a second pass sees the
    neighbours the first pass already assumed.
    """
    return all(n is None or ord(n) < 128 for n in (left, right))


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
    left, right = _nearest_surviving(survivors)
    runs = _context_run_lengths(classes, protected)
    for i, category in enumerate(classes):
        if category in CONTEXT_SENSITIVE and i not in protected:
            verdicts[i] = _resolve(category, profile, ord(text[i]),
                                   leading=(i == 0),
                                   between_ascii=_between_ascii(left[i], right[i]),
                                   oversized_run=runs[i] > MAX_CONTEXT_RUN)

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
