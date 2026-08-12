"""Character classification.

Scope is *derived* from Unicode's General_Category rather than curated: any `Cf`
character is in scope automatically, so new Unicode versions need no code change.
A curated list built from the obvious suspects misses 54 of the 170 `Cf` codepoints
in UCD 16.0.

Classification is per-character-encountered, not a precomputed 1.1M-codepoint table,
so import cost stays at zero.
"""

from __future__ import annotations

import unicodedata

# Pure carriers: no legitimate use in any script.
ZERO_WIDTH = frozenset({0x200B, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064})

# Semantic in emoji sequences, Indic conjuncts and Arabic; carriers between ASCII.
JOINERS = frozenset({0x200C, 0x200D})

# Includes the invisible marks LRM/RLM/ALM, which are as usable as the embedding
# controls but are routinely left out of "bidi" lists.
BIDI = frozenset({0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
                  0x2066, 0x2067, 0x2068, 0x2069,
                  0x200E, 0x200F, 0x061C})

# Semantic in CJK (Ideographic Variation Database), Mongolian, and emoji presentation.
VARIATION = frozenset(range(0xFE00, 0xFE10)) | frozenset(range(0xE0100, 0xE01F0)) \
    | frozenset(range(0x180B, 0x180E))

# Emoji tag sequences: U+1F3F4 + U+E0020..E007E + U+E007F terminator.
TAG_RANGE = frozenset(range(0xE0000, 0xE0080))
TAG_SPEC = frozenset(range(0xE0020, 0xE007F))
TAG_TERM = 0xE007F
FLAG_BASE = 0x1F3F4

# Render blank but are Lo, not Cf — the standard steganography trick.
HANGUL_FILLER = frozenset({0x115F, 0x1160, 0x3164, 0xFFA0})

LINE_SEP = frozenset({0x2028, 0x2029, 0x0085})

SPACES = frozenset({0x00A0, 0x1680, 0x202F, 0x205F, 0x3000}) | frozenset(range(0x2000, 0x200B))

BOM = 0xFEFF
SOFT_HYPHEN = 0x00AD
CGJ = 0x034F

# Format characters that are semantic in Arabic prose; kept under the prose profile.
PROSE_ALLOWED_CF = frozenset(range(0x0600, 0x0606)) | {0x06DD, 0x070F, 0x0890, 0x0891,
                                                       0x08E2, 0x110BD, 0x110CD}

# Tab, newline and carriage return are the only controls that survive.
CONTROL_KEEP = frozenset({0x09, 0x0A, 0x0D})

# Classes whose characters are invisible enough that they should not count as a
# neighbour when deciding whether a joiner sits between ASCII: otherwise doubling
# a ZWJ would defeat the adjacency test.
TRANSPARENT = frozenset({"zero_width", "joiner", "variation", "soft_hyphen",
                         "bidi", "cgj", "other_format", "tag"})


def classify(ch: str) -> str | None:
    """Return this character's class, or None if it is unremarkable."""
    cp = ord(ch)
    if cp < 0x80:
        return "control" if cp not in CONTROL_KEEP and unicodedata.category(ch) == "Cc" else None
    if cp in ZERO_WIDTH:
        return "zero_width"
    if cp == BOM:
        return "bom"
    if cp in JOINERS:
        return "joiner"
    if cp in BIDI:
        return "bidi"
    if cp in VARIATION:
        return "variation"
    if cp in TAG_RANGE:
        return "tag"
    if cp in HANGUL_FILLER:
        return "hangul_filler"
    if cp in LINE_SEP:
        return "line_sep"
    if cp in SPACES:
        return "space"
    if cp == SOFT_HYPHEN:
        return "soft_hyphen"
    if cp == CGJ:
        return "cgj"
    category = unicodedata.category(ch)
    if category == "Cf":
        return "other_format"
    if category == "Cc":
        return "control"
    return None


def char_name(ch: str) -> str:
    return unicodedata.name(ch, f"U+{ord(ch):04X}")
