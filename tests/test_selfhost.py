"""The repo must pass its own hook.

A tool that detects invisible characters attracts them into its own test fixtures,
where they are unreviewable: nobody can see a U+200D in a diff. Escapes are the
only readable form, and this test is what keeps them that way. It already caught a
CJK test that had been committed with U+E010 (private use) instead of U+E0100, so
it asserted nothing about variation selectors while appearing to pass.
"""

from pathlib import Path

import pytest

from texthygiene.chars import classify

ROOT = Path(__file__).resolve().parent.parent
SOURCES = (sorted(ROOT.glob("src/texthygiene/*.py"))
           + sorted(ROOT.glob("tests/*.py"))
           + [ROOT / "hooks/pre-commit", ROOT / "pyproject.toml", ROOT / ".gitignore"])

# README.md is deliberately absent: it demonstrates the characters this tool
# removes, so literal emoji sequences there are content, not contamination. The
# pre-commit hook still checks it under the prose profile.


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_source_contains_no_literal_invisible_characters(path):
    text = path.read_text("utf-8")
    literal = [(i, f"U+{ord(c):04X}", classify(c))
               for i, c in enumerate(text) if ord(c) > 0x7F and classify(c)]
    assert literal == [], (
        f"{path.name} contains literal invisible characters; use escapes: {literal[:5]}")


def test_fixture_corpus_is_not_empty():
    """Guards against the emoji corpus test passing vacuously on a missing file."""
    corpus = (ROOT / "tests/fixtures/emoji_rgi.txt").read_text("utf-8").splitlines()
    assert len(corpus) > 3000
