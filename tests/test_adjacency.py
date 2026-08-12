"""Adjacency is decided against what survives cleaning, not the raw text.

The bug this file exists to prevent: a character that cleaning is about to remove
counted as a non-ASCII neighbour, so it shielded a carrier from removal. One pass
of `clean` then returned text the tool called clean while a live ZWJ remained, and
a second pass produced different output. A mid-file BOM and a C1 control both did
it.
"""

import pytest

from texthygiene import clean, scan
from texthygiene.chars import classify
from texthygiene.core import CONTEXT_SENSITIVE, REPORT

CARRIERS = {
    "zwj": "\u200d",
    "zwsp": "\u200b",
    "vs16": "\ufe0f",
    "word_joiner": "\u2060",
}

# Characters that are stripped or ASCII-replaced, so they must not act as a
# non-ASCII neighbour and shield a carrier.
DISAPPEARING = {
    "mid_bom": "\ufeff",
    "c1_control": "\u0086",
    "soft_hyphen": "\u00ad",
    "tag_char": "\U000e0067",
    "deprecated_format": "\u206a",
}


@pytest.mark.parametrize("carrier_name,carrier", CARRIERS.items())
@pytest.mark.parametrize("shield_name,shield", DISAPPEARING.items())
@pytest.mark.parametrize("order", ["before", "after"])
def test_disappearing_char_cannot_shield_a_carrier(carrier_name, carrier,
                                                   shield_name, shield, order):
    text = f"a{carrier}{shield}b" if order == "after" else f"a{shield}{carrier}b"
    cleaned, _ = clean(text, "prose")
    assert cleaned == "ab", (
        f"{shield_name} shielded {carrier_name}: {text!r} -> {cleaned!r}")


@pytest.mark.parametrize("carrier", CARRIERS.values())
@pytest.mark.parametrize("shield", DISAPPEARING.values())
@pytest.mark.parametrize("profile", ["prose", "code"])
def test_idempotent_across_carrier_shield_pairs(carrier, shield, profile):
    text = f"a{carrier}{shield}b"
    once, _ = clean(text, profile)
    twice, _ = clean(once, profile)
    assert once == twice


def test_one_pass_leaves_no_actionable_carrier():
    """Cleaning must reach a fixpoint in a single pass, for every pair."""
    for carrier in CARRIERS.values():
        for shield in DISAPPEARING.values():
            cleaned, _ = clean(f"a{carrier}{shield}b", "prose")
            leftover = [f for f in scan(cleaned, "prose") if f.action != REPORT]
            assert leftover == [], f"{cleaned!r} still has {leftover}"


@pytest.mark.parametrize("text", [
    "\U0001F468\u200d\U0001F469\u200d\U0001F467",   # family emoji
    "1\ufe0f⃣",                                 # keycap, ASCII base
    "\U0001F469\U0001F3FD\u200d\U0001F680",          # skin-tone modifier
    "क्\u200dष",                      # Devanagari conjunct
    "café\u2060bar",                            # word joiner as real glue
    "日\u200b本",                            # CJK line-break hint
])
def test_semantic_uses_survive_prose(text):
    assert clean(text, "prose")[0] == text


def test_context_sensitive_names_are_real_classes():
    """A typo in CONTEXT_SENSITIVE would silently disable a rule."""
    produced = {classify(chr(cp)) for cp in range(0x110000)} - {None}
    assert CONTEXT_SENSITIVE <= produced, CONTEXT_SENSITIVE - produced


def test_every_class_has_a_resolve_branch():
    """_resolve raises on an unhandled class; this proves none is unhandled."""
    produced = {classify(chr(cp)) for cp in range(0x110000)} - {None}
    for profile in ("prose", "code"):
        for text in (f"a{chr(cp)}b" for cp in _one_codepoint_per_class(produced)):
            scan(text, profile)


def _one_codepoint_per_class(classes):
    seen = {}
    for cp in range(0x110000):
        category = classify(chr(cp))
        if category in classes and category not in seen:
            seen[category] = cp
        if len(seen) == len(classes):
            break
    return seen.values()
