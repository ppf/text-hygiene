import unicodedata
from pathlib import Path

import pytest

from texthygiene import clean, scan
from texthygiene.chars import classify

FIXTURES = Path(__file__).parent / "fixtures"
RGI = [line for line in (FIXTURES / "emoji_rgi.txt").read_text("utf-8").splitlines() if line]

ZWJ = "\u200d"
ZWSP = "\u200b"
VS16 = "\ufe0f"


# --- invariants ------------------------------------------------------------

@pytest.mark.parametrize("profile", ["prose", "code"])
@pytest.mark.parametrize("text", [
    "plain ascii",
    f"Hel{ZWJ}lo",
    f"a{ZWSP}b\u202ec\u00a0d",
    "\ufeffleading bom",
    "mid\ufeffbom",
    "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F",
    "क्\u200dष",
    "1\ufe0f⃣",
    "",
])
def test_idempotent(text, profile):
    once, _ = clean(text, profile)
    twice, _ = clean(once, profile)
    assert once == twice


@pytest.mark.parametrize("profile", ["prose", "code"])
def test_ascii_is_byte_identical(profile):
    text = "".join(chr(c) for c in range(0x20, 0x7F)) + "\t\n\r"
    cleaned, _ = clean(text, profile)
    assert cleaned == text


@pytest.mark.parametrize("profile", ["prose", "code"])
def test_no_silent_loss(profile):
    """Every character the cleaner touches is accounted for in the report."""
    text = f"a{ZWSP}b{ZWJ}c\u00a0d\u202ee\u00adf\u2028g\ufeffh"
    cleaned, findings = clean(text, profile)
    acted = [f for f in findings if f.action in ("strip", "replace")]
    stripped = sum(1 for f in acted if f.action == "strip")
    assert len(cleaned) == len(text) - stripped
    for f in acted:
        assert text[f.index] == f.char


@pytest.mark.parametrize("profile", ["prose", "code"])
def test_scan_clean_agreement(profile):
    text = f"x{ZWSP}\u00a0\u202e{ZWJ}y"
    cleaned, findings = clean(text, profile)
    reported = [f for f in findings if f.action == "report"]
    # Report-only findings must leave their character in place.
    for f in reported:
        assert f.char in cleaned


def test_emoji_corpus_survives_prose():
    """All 3781 RGI sequences pass through the prose profile byte-identical."""
    broken = [s for s in RGI if clean(s, "prose")[0] != s]
    assert broken == [], f"{len(broken)} sequences corrupted, e.g. {broken[:3]!r}"


# --- context rules ---------------------------------------------------------

def test_zwj_stripped_between_ascii():
    cleaned, _ = clean(f"Hel{ZWJ}lo", "prose")
    assert cleaned == "Hello"


def test_zwj_preserved_in_devanagari_conjunct():
    text = "क्\u200dष"
    assert clean(text, "prose")[0] == text


def test_zwj_preserved_in_emoji_family():
    text = f"\U0001F468{ZWJ}\U0001F469{ZWJ}\U0001F467"
    assert clean(text, "prose")[0] == text


def test_vs16_preserved_on_ascii_based_keycap():
    """The keycap base is an ASCII digit, so a naive emoji test would break it."""
    text = "1\ufe0f⃣"
    assert clean(text, "prose")[0] == text


def test_zwj_preserved_after_skin_tone_modifier():
    text = f"\U0001F469\U0001F3FD{ZWJ}\U0001F680"
    assert clean(text, "prose")[0] == text


def test_doubled_zwj_between_ascii_still_stripped():
    """Carrier chars are transparent to the adjacency test, so doubling cannot evade it."""
    cleaned, _ = clean(f"Hel{ZWJ}{ZWJ}lo", "prose")
    assert cleaned == "Hello"


def test_flag_sequence_intact_including_terminator():
    scotland = "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F"
    for profile in ("prose", "code"):
        assert clean(scotland, profile)[0] == scotland


def test_loose_tag_chars_are_stripped():
    cleaned, _ = clean("hi\U000E0067\U000E0062there", "prose")
    assert cleaned == "hithere"


# --- profile differences ---------------------------------------------------

def test_bidi_reported_in_prose_stripped_in_code():
    text = "a\u202eb"
    assert clean(text, "prose")[0] == text
    assert clean(text, "code")[0] == "ab"


def test_nbsp_reported_in_prose_replaced_in_code():
    assert clean("a\u00a0b", "prose")[0] == "a\u00a0b"
    assert clean("a\u00a0b", "code")[0] == "a b"


def test_arabic_format_chars_allowed_in_prose_only():
    text = "\u0600م"
    assert clean(text, "prose")[0] == text
    assert clean(text, "code")[0] == "م"


def test_ideographic_space_preserved_in_prose():
    assert clean("あ\u3000い", "prose")[0] == "あ\u3000い"


def test_cjk_variation_selector_preserved_in_prose():
    """U+E0100 is an Ideographic Variation Database selector, semantic in Japanese."""
    text = "葛\U000e0100"
    assert classify("\U000e0100") == "variation"
    assert clean(text, "prose")[0] == text
    assert clean(text, "code")[0] == "葛"


# --- BOM, line numbers, derivation ----------------------------------------

def test_leading_bom_preserved_mid_file_bom_stripped():
    assert clean("\ufeffhello", "prose")[0] == "\ufeffhello"
    assert clean("hel\ufefflo", "prose")[0] == "hello"


def test_line_numbers_not_confused_by_u2028():
    """The ZWSP is on line 2; splitlines() also breaks on U+2028 and would say 3."""
    findings = scan(f"a\u2028b\n{ZWSP}", "prose")
    zwsp = [f for f in findings if f.codepoint == 0x200B][0]
    assert zwsp.line == 2


def test_hangul_filler_detected():
    assert classify("\u3164") == "hangul_filler"
    assert clean("a\u3164b", "code")[0] == "ab"


def test_unlisted_format_chars_caught_by_derivation():
    """U+206A-206F are in no curated list; category derivation catches them anyway."""
    for cp in range(0x206A, 0x2070):
        assert classify(chr(cp)) == "other_format"
        assert clean(f"a{chr(cp)}b", "prose")[0] == "ab"


def test_every_cf_codepoint_is_classified():
    unclassified = [cp for cp in range(0x110000)
                    if unicodedata.category(chr(cp)) == "Cf" and classify(chr(cp)) is None]
    assert unclassified == []


def test_unknown_profile_rejected():
    with pytest.raises(ValueError):
        scan("x", "strict")
