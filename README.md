# text-hygiene

Detect and strip invisible Unicode from text — zero-width characters, bidi controls,
format characters, blank-rendering fillers. Python 3.10+, stdlib only, no runtime
dependencies.

```bash
pipx install .            # or: pip install -e .

text-hygiene inspect draft.md              # exit 0 clean / 1 findings / 2 error
text-hygiene clean draft.md -o out.md
text-hygiene clean --in-place --profile code src/*.py
cat draft.md | text-hygiene clean -
```

## Profiles

| | `prose` (default) | `code` |
|---|---|---|
| Intent | human text | source, configs, commit messages |
| Format characters (`Cf`) | strip | strip |
| Joiners, variation selectors | strip only between ASCII | strip |
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

Carrier characters are transparent when looking for a neighbour, so doubling a ZWJ
cannot evade the test — and that same property is what makes cleaning idempotent.

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

- A **leading BOM** is an encoding artifact: preserved and reported. A mid-file `U+FEFF`
  is a carrier: stripped.
- Line numbers use `split("\n")`, not `splitlines()` — the latter also breaks on
  `U+2028`/`U+2029`/`U+0085`, so reported lines would disagree with your editor on
  exactly the files this tool inspects.
- Non-UTF-8 input **fails loudly** rather than decoding with replacement.
- Binary files (NUL in the first 8 KiB) are skipped; files over `--max-size` (10 MiB)
  are refused.
- `--in-place` preserves mode and mtime, writes through symlinks to the real file, and
  refuses files with hard links.

## Pre-commit hook

```bash
ln -sf "$(pwd)/hooks/pre-commit" .git/hooks/pre-commit
```

Runs `--profile code` on staged files. Python files are delegated to ruff
(RUF001-003, PLE2502) when it is installed, so repos already running ruff don't get two
divergent sets of diagnostics for the same characters.

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
