"""Adjacency is decided against what survives cleaning, not the raw text.

The bug this file exists to prevent: a character that cleaning is about to remove
counted as a non-ASCII neighbour, so it shielded a carrier from removal. One pass
of `clean` then returned text the tool called clean while a live ZWJ remained, and
a second pass produced different output. A mid-file BOM and a C1 control both did
it.
"""

from itertools import product

import pytest

from texthygiene import clean, scan
from texthygiene.chars import classify
from texthygiene.core import CONTEXT_SENSITIVE, UNMODIFIED

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
            leftover = [f for f in scan(cleaned, "prose") if f.action not in UNMODIFIED]
            assert leftover == [], f"{cleaned!r} still has {leftover}"


@pytest.mark.parametrize("text", [
    "\U0001F468\u200d\U0001F469\u200d\U0001F467",   # family emoji
    "1\ufe0f⃣",                                 # keycap, ASCII base
    "\U0001F469\U0001F3FD\u200d\U0001F680",          # skin-tone modifier
    "क्\u200dष",                      # Devanagari conjunct
])
def test_semantic_uses_survive_prose(text):
    assert clean(text, "prose")[0] == text


@pytest.mark.parametrize("shield", ["\u2014", "\u2019", "\U0001F44D", "\u043f", "\u00e9"])
def test_one_non_ascii_char_cannot_shield_a_zero_width_run(shield):
    """An em dash, curly quote, emoji or non-Latin letter must not hide a payload.

    Making zero_width context-sensitive meant appending one such character protected
    an unlimited run of zero-width spaces, in the profile that is the tool's main use.
    """
    text = "hello" + "\u200b" * 20 + shield
    cleaned, _ = clean(text, "prose")
    assert "\u200b" not in cleaned


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


# Exhaustive sweep over short sequences. The hand-picked grid above checks one
# shield beside one carrier; this covers runs, both-side shields, boundaries and
# protected-sequence adjacency without anyone having to think of each case.
ALPHABET = [
    "a", "\u00e9",                          # ASCII and non-ASCII letters
    "\u200b", "\u200d", "\ufe0f",            # zero width, joiner, variation selector
    "\ufeff", "\u00a0", "\u2028",            # BOM, NBSP, line separator
    "\u202e", "\u3164", "\u0086",            # bidi override, hangul filler, C1 control
    "\U000e0067", "\u00ad", "\u0600",        # tag char, soft hyphen, Arabic format
    "\U0001F3F4", "\U000e007f", "\u2060",    # flag base, tag terminator, word joiner
    "\u0600", "\u034f", "\u0085",            # allowlisted Cf, CGJ, NEL
]


@pytest.mark.parametrize("profile", ["prose", "code"])
def test_exhaustive_short_sequences_reach_a_fixpoint(profile):
    for combo in product(ALPHABET, repeat=3):
        text = "".join(combo)
        once, findings = clean(text, profile)
        twice, _ = clean(once, profile)
        assert once == twice, f"not idempotent: {text!r} -> {once!r} -> {twice!r}"
        leftover = [f for f in scan(once, profile) if f.action not in UNMODIFIED]
        assert not leftover, f"not a fixpoint: {text!r} -> {once!r} leaves {leftover}"
        stripped = sum(1 for f in findings if f.action == "strip")
        assert len(once) == len(text) - stripped, f"length mismatch on {text!r}"


@pytest.mark.parametrize("profile", ["prose", "code"])
def test_every_replacement_is_ascii(profile):
    """_survivors feeds replacements into the ASCII adjacency test.

    A non-ASCII replacement would silently change which carriers get stripped, and
    nothing else in the suite would notice.
    """
    for ch in ALPHABET:
        for f in scan(f"a{ch}b", profile):
            assert all(ord(c) < 128 for c in f.replacement), f
