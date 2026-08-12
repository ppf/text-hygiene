# text-hygiene

Detect and strip invisible Unicode from text — zero-width characters, bidi controls,
format characters, blank-rendering fillers. Python 3.10+, stdlib only, no runtime
dependencies.

```bash
pipx install .            # or: pip install -e .

text-hygiene inspect draft.md              # exit 0 nothing to fix / 1 actionable / 2 error
text-hygiene clean draft.md -o out.md
text-hygiene clean --in-place --profile code src/*.py
cat draft.md | text-hygiene clean -
```

## Profiles

| | `prose` (default) | `code` |
|---|---|---|
| Intent | human text | source, configs, commit messages |
| Format characters (`Cf`) | strip | strip |
| Joiners, variation selectors, zero-width | strip only between ASCII | strip |
| Bidi controls | report | strip |
| NBSP and friends | report | → ASCII space |
| Arabic format characters | kept | strip |
| Emoji tag sequences | kept | kept |

The default is conservative because the tool edits files. Use `--profile code` where
nothing invisible is ever legitimate.

## How it decides

**Scope is derived, not curated.** Anything with General_Category `Cf` is in scope
automatically, so new Unicode versions need no code change. This matters more than it
sounds: a list built from the obvious suspects misses 54 of the 170 `Cf` codepoints in
UCD 16.0, including `U+206A–206F` and `U+FFF9–FFFB`. Non-`Cf` invisibles that the
category rule cannot catch — Hangul fillers, CGJ, variation selectors — are listed
explicitly.

**Context comes from ASCII adjacency, not script detection.** ZWJ is a steganography
carrier in `Hel<ZWJ>lo` and load-bearing in `👨‍👩‍👧` and `क्‍ष`. Telling those apart
appears to need Unicode's `Script` and `Emoji` properties, which `unicodedata` does not
expose. It doesn't: every script that gives these characters meaning is non-ASCII, so a
joiner is stripped only when **both** neighbours are ASCII. The rule can under-strip but
never corrupt.

The same rule covers characters that are carriers in one place and meaningful in
another — `U+2060` WORD JOINER is real non-breaking glue, `U+2061`–`U+2064` are semantic
in mathematical markup, and a zero-width space is a legitimate line-break hint in CJK.

**Neighbours are read from what survives cleaning, not from the raw text.** Otherwise a
character that is itself about to be removed — a mid-file BOM, a C1 control — counts as
a non-ASCII neighbour and shields the carrier next to it, so one pass returns text the
tool calls clean while a live ZWJ remains. Deciding against the survivor view also makes
doubling a carrier useless and makes cleaning idempotent: the second pass sees exactly
the neighbours the first pass assumed.

Verified against all 3781 fully-qualified RGI emoji sequences from Unicode's
`emoji-test.txt`: every one survives the `prose` profile byte-identical, including
keycaps like `1️⃣` (whose base is an ASCII digit) and skin-tone sequences (where the
joiner's neighbour is a modifier, not the base emoji).

## What it does not do

- No normalization. `clean` never applies NFC or NFKC.
- No confusable/homoglyph replacement. Rewriting Cyrillic to Latin silently corrupts
  legitimate Russian; detection may return later as an opt-in report.
- No C2PA, Content Credentials, or statistical watermark handling.
- It makes no claim that cleaned text was written by a human.

**Honest scope note:** on ordinary Latin prose, `prose` and a naive always-strip tool
produce identical output. The context-awareness only earns its keep on text containing
emoji, Indic, or RTL — where naive stripping corrupts content.

## Behaviour worth knowing

- **Exit status answers "would `clean` modify this file?"** Report-only findings still
  print, but exit 0 — the profile has already decided not to touch them. Otherwise a
  file whose invisible characters are all legitimate, like an emoji fixture or a
  document containing RTL, could never pass the hook.
- A **leading BOM** is an encoding artifact: preserved and reported. A mid-file `U+FEFF`
  is a carrier: stripped.
- Line numbers use `split("\n")`, not `splitlines()` — the latter also breaks on
  `U+2028`/`U+2029`/`U+0085`, so reported lines would disagree with your editor on
  exactly the files this tool inspects.
- Non-UTF-8 input **fails loudly** rather than decoding with replacement.
- `--json` emits **one document** covering every input, so passing several paths still
  produces something `json.loads` can read.
- `clean --in-place` reads every input before writing any of them, so a bad file in the
  middle of the list fails before the first file is modified.
- Binary files (NUL in the first 8 KiB) are skipped; files over `--max-size` (10 MiB)
  are refused.
- `--in-place` preserves mode and mtime, writes through symlinks to the real file, and
  refuses files with hard links.

## Pre-commit hook

```bash
ln -sf "$(pwd)/hooks/pre-commit" .git/hooks/pre-commit
```

Documentation (`.md`, `.rst`, `.txt`, `.adoc`) is checked with `prose`; everything else
gets `code`. Docs legitimately contain emoji and other scripts, and `code` would flag —
and on fix, corrupt — a ZWJ emoji sequence in a README.

Python files are delegated to ruff (RUF001-003, PLE2502) when it is installed, so repos
already running ruff don't get two divergent sets of diagnostics for the same
characters.

The repo passes its own hook, and a test enforces that source files contain no *literal*
invisible characters — escapes only. Nobody can review a `U+200D` they cannot see.

## API

```python
from texthygiene import scan, clean

findings = scan(text, profile="prose")     # list[Finding], nothing mutated
cleaned, findings = clean(text, "code")    # clean is apply(scan(...))
```

`clean` is defined in terms of `scan`, so the context rules exist in exactly one place.
Findings with action `strip`/`replace` correspond 1:1 to modifications; findings with
action `report` never alter the text.

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install -e . pytest
.venv/bin/python -m pytest
```
