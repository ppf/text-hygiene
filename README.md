# text-hygiene

Detect and strip invisible Unicode from text — zero-width characters, bidi controls,
format characters, blank-rendering fillers. Python 3.10+, stdlib only, no runtime
dependencies.

```bash
pipx install .            # or: pip install -e .

text-hygiene inspect draft.md              # exit 0 nothing to fix / 1 actionable / 2 error
text-hygiene inspect --fail-on report *.md # gate mode: also fail on flagged-but-kept
text-hygiene clean draft.md -o out.md
text-hygiene clean --in-place --profile code src/*.py
cat draft.md | text-hygiene clean -
```

## Profiles

| | `prose` (default) | `code` |
|---|---|---|
| Intent | human text | source, configs, commit messages |
| Format characters (`Cf`) | strip | strip |
| Zero-width (incl. WORD JOINER) | strip | strip |
| Joiners, variation selectors | strip only between ASCII | strip |
| Bidi controls | report | strip |
| NBSP and friends | allow | → ASCII space |
| Arabic format characters | kept, unless between ASCII | strip |
| Emoji tag sequences | kept | kept |

The default is conservative because the tool edits files. Use `--profile code` where
nothing invisible is ever legitimate.

## How it decides

**Scope is derived, not curated.** Anything with General_Category `Cf` is in scope
automatically, so new Unicode versions need no code change. The explicit tables name
only the classes needing distinct handling; they cover well under half the `Cf`
codepoints in UCD 16.0, and the rest — `U+206A–206F`, `U+FFF9–FFFB` and friends — fall
through to the derived branch rather than being missed. A test pins that no `Cf`
codepoint goes unclassified. Non-`Cf` invisibles the category rule cannot catch — Hangul
fillers, CGJ, variation selectors — are listed explicitly.

**Context comes from ASCII adjacency, not script detection.** ZWJ is a steganography
carrier in `Hel<ZWJ>lo` and load-bearing in `👨‍👩‍👧` and `क्‍ष`. Telling those apart
appears to need Unicode's `Script` and `Emoji` properties, which `unicodedata` does not
expose. It doesn't: every script that gives these characters meaning is non-ASCII, so a
joiner is stripped only when **both** neighbours are ASCII. The rule can under-strip but
never corrupt.

**Neighbours are read from what survives cleaning, not from the raw text.** Otherwise a
character that is itself about to be removed — a mid-file BOM, a C1 control — counts as
a non-ASCII neighbour and shields the carrier next to it, so one pass returns text the
tool calls clean while a live ZWJ remains. Deciding against the survivor view also makes
doubling a carrier useless and makes cleaning idempotent: the second pass sees exactly
the neighbours the first pass assumed.

**Adjacency applies to joiners and variation selectors only — not to zero-width
characters**, which are stripped unconditionally. Extending it there was a mistake worth
recording: it meant any single non-ASCII character shielded every carrier beside it, so
one em dash — which LLM output is full of — hid an unlimited run of zero-width spaces,
and *all* non-Latin prose became unprotected. The cost of stripping unconditionally is
that `U+2060` WORD JOINER, the invisible maths operators, and a CJK line-break hint go
with it. That loses a rendering hint; the alternative lost the tool's whole purpose.

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

**`allow` is a side channel, and that is a deliberate cost.** Anything the `prose`
profile keeps can carry information: a variation selector on each non-Latin letter is
roughly a bit apiece, and so is the presence or absence of a joiner between two emoji.
The capacity is bounded — runs of more than two consecutive joiners or variation
selectors are stripped outright, since the longest such run in Unicode's entire RGI
emoji set is two — but it is not zero.
Gate mode does not fail on `allow` and `clean` will not remove it, so a document rich in
emoji or non-Latin script has invisible capacity this tool deliberately leaves alone —
that is the price of never corrupting valid content. `--profile code` removes all of it.
Every one of those characters *is* reported, so `--json` will show you the channel even
though nothing acts on it.

## Behaviour worth knowing

- **Findings carry one of four actions.** `strip` and `replace` are what `clean`
  applies. The other two both leave the character alone, for different reasons, and a
  commit gate has to tell them apart:
  - `allow` — context proved it legitimate here: a joiner inside an emoji sequence,
    NBSP in prose, a leading BOM. Never fails, under either `--fail-on`.
  - `report` — policy declines to modify it, but it still warrants a look. Currently
    only bidi controls: removing a direction override could change how the text
    renders, so `clean` leaves it to a human. These need a manual edit; the hook says
    so rather than printing a `clean` command that would change nothing.
- **`inspect --fail-on`** picks the question the exit status answers. `actionable`
  (default) means "would `clean` change this?". `report` is gate mode and also fails on
  `report` findings — without it a Trojan Source bidi override commits into a README,
  since `clean` won't touch it under prose. Neither setting fails on `allow`, or the
  gate could never be satisfied.
- **Anything the gate blocks must be fixable without losing content** — by `clean` where
  it can, by a documented manual edit otherwise. That rule is why Hangul fillers and CGJ
  are stripped rather than reported, and why bidi is the one class left to human
  judgement: ordinary RTL prose needs no direction override at all, since the Bidi
  Algorithm derives direction from the letters themselves.
- A **leading BOM** is an encoding artifact: preserved and allowed. A mid-file `U+FEFF`
  is a carrier: stripped.
- Line numbers use `split("\n")`, not `splitlines()` — the latter also breaks on
  `U+2028`/`U+2029`/`U+0085`, so reported lines would disagree with your editor on
  exactly the files this tool inspects.
- Non-UTF-8 input **fails loudly** rather than decoding with replacement.
- `--json` emits **one document** covering every input, so passing several paths still
  produces something `json.loads` can read.
- `clean --in-place` reads every input, then pre-flights hard-link and writability
  checks on all of them, before writing anything. A write that fails anyway (ENOSPC, a
  race) names on stderr which files were already replaced — it does not pretend the run
  was atomic.
- Binary files (NUL in the first 8 KiB) are skipped; files over `--max-size` (10 MiB)
  are refused.
- `--in-place` preserves mode and mtime, writes through symlinks to the real file, and
  refuses files with hard links.

## Pre-commit hook

```bash
# Check this first: a global core.hooksPath REPLACES .git/hooks, so the symlink
# below would silently never run.
git config core.hooksPath

# unset -> the standard path works
ln -sf "$(pwd)/hooks/pre-commit" .git/hooks/pre-commit

# set -> either add the hook to that directory (applies to every repo), or give
# this repo its own hooks dir, which overrides the global one for this repo only:
mkdir -p .githooks && ln -sf "$(pwd)/hooks/pre-commit" .githooks/pre-commit
git config core.hooksPath .githooks
```

**The hook reads the staged blob, never the working tree.** Checking the file on disk
approves content git is not about to commit: fix the file, commit again without
re-staging, and the worktree is clean while the index still carries the character.
`git add -p` diverges the same way. Every finding is read from `git show :path`.

Documentation (`.md`, `.mdx`, `.rst`, `.txt`, `.adoc`, `.tex`) is checked with `prose`;
everything else gets `code`. Docs legitimately contain emoji and other scripts, and
`code` would flag — and on fix, corrupt — a ZWJ emoji sequence in a README.

The hook runs `--fail-on report`, so a bidi override in a doc is blocked even though
`clean` leaves it alone under `prose`. Emoji, NBSP and a leading BOM are `allow` and
pass.

Python files run ruff **in addition to** text-hygiene, not instead of it. ruff's
RUF001-003 and PLE2502 cover confusables and bidi but detect no zero-width or joiner
characters at all, so delegating to ruff alone left Python sources the least protected
files in the repo.

Binary, oversize and non-UTF-8 files are **skipped with a notice**, not treated as tool
failures — otherwise one PNG blocks every commit in the repo with no way to comply.
Renames are included (`--diff-filter=ACMR`): git classifies `git mv` plus an edit as
`R`, and an `R`-filtered-out path was never scanned at all.

The repo passes its own hook, and a test enforces that source files contain no *literal*
invisible characters — escapes only. Nobody can review a `U+200D` they cannot see.

## Use as an agent skill

`skills/text-hygiene/SKILL.md` uses the `name`/`description` frontmatter that Claude
Code, Codex, and the shared `~/.agents/skills` convention all read, so one file serves
all three. Symlink it rather than copying, so `git pull` updates the skill:

```bash
# Claude Code
ln -sfn "$(pwd)/skills/text-hygiene" ~/.claude/skills/text-hygiene
# Codex
ln -sfn "$(pwd)/skills/text-hygiene" ~/.codex/skills/text-hygiene
# cross-agent
ln -sfn "$(pwd)/skills/text-hygiene" ~/.agents/skills/text-hygiene
```

Cursor does not read `SKILL.md` — it reads `AGENTS.md` and `.cursor/rules/*.mdc`. This
repo's [`AGENTS.md`](AGENTS.md) points at the skill and repeats the two things most
easily got wrong, so Cursor gets them without following the pointer. It also carries a
copy-pasteable block for other projects that use the tool.

The skill assumes the CLI is on `PATH`; install it once with
`pipx install git+https://github.com/ppf/text-hygiene.git`.

## API

```python
from texthygiene import scan, clean

findings = scan(text, profile="prose")     # list[Finding], nothing mutated
cleaned, findings = clean(text, "code")    # clean is apply(scan(...))
```

`clean` is defined in terms of `scan`, so the context rules exist in exactly one place.
Findings with action `strip`/`replace` correspond 1:1 to modifications; `report` and
`allow` never alter the text.

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install -e . pytest
.venv/bin/python -m pytest
```
