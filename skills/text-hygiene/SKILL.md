---
name: text-hygiene
description: Detect and strip invisible Unicode from text and source files — zero-width characters, bidi controls (Trojan Source), format characters, blank-rendering fillers, stray BOMs. Use when the user asks to clean invisible or hidden characters, remove zero-width spaces, check for Trojan Source or bidi overrides, sanitise pasted or LLM-generated text before committing, or diagnose why a string compares unequal / a diff looks identical / a linter flags a line that looks fine. Also use before committing text that was pasted from a chat, PDF, or web page.
---

# text-hygiene

CLI that finds and removes invisible Unicode. Two profiles: `prose` (conservative,
context-aware, default) and `code` (aggressive).

## Check availability first

```bash
command -v text-hygiene || echo "not installed"
```

If missing: `pipx install git+https://github.com/ppf/text-hygiene.git`
(or `pip install -e /path/to/text-hygiene`). Python 3.10+, no runtime dependencies.

## Usage

```bash
text-hygiene inspect FILE...              # report; exit 0 clean / 1 actionable / 2 error
text-hygiene inspect --json FILE          # one JSON document covering every input
text-hygiene inspect --fail-on report F   # gate mode: also fail on flagged-but-kept
text-hygiene clean FILE -o OUT            # default: stdout
text-hygiene clean --in-place --profile code src/*.py
cat draft.md | text-hygiene clean -       # stdin; add --label PATH to name it
```

## Which profile

| Situation | Profile |
|---|---|
| Source code, configs, commit messages, anything machine-parsed | `code` |
| Documentation, prose, anything containing emoji or non-Latin script | `prose` (default) |

`code` strips every invisible character. `prose` keeps ones that context proves
legitimate — a joiner inside an emoji sequence, NBSP, a leading BOM — so it will not
corrupt `👨‍👩‍👧`, `1️⃣`, or a Devanagari conjunct. **Never run `--profile code` over
documentation containing emoji**: it flattens those sequences.

## Reading the output

Four actions. Only the first two change the file:

- `strip` / `replace` — what `clean` applies.
- `report` — left alone but worth a human look; currently only bidi controls. `clean`
  will **not** remove these, so they need a manual edit.
- `allow` — context proved it legitimate. Never fails a gate.

So `inspect` exiting 0 does not mean "no invisible characters" — it means "nothing
`clean` would change". Use `--fail-on report` when you want the wider question.

## Workflow

1. `inspect` first and show the user what was found — never clean silently.
2. Choose the profile from the table above.
3. `clean` to a new file or stdout unless the user asked to modify in place.
4. Re-run `inspect` to confirm.

For a whole repo: `git ls-files | xargs text-hygiene inspect --profile code` —
binary and non-UTF-8 files will error, so filter to text files first.

## Pre-commit gate

The repo ships `hooks/pre-commit`. It reads the **staged blob**, not the working tree,
routes docs to `prose` and everything else to `code`, runs ruff alongside (not instead
of) text-hygiene on Python files, and skips binary/oversize/non-UTF-8 files with a
notice rather than blocking the commit.

```bash
git config core.hooksPath          # if this prints a path, .git/hooks is IGNORED
ln -sf "$(pwd)/hooks/pre-commit" .git/hooks/pre-commit
```

If `core.hooksPath` is set, installing into `.git/hooks` silently does nothing. Put
the hook in that directory instead, or give the repo its own with
`git config core.hooksPath .githooks`.

If it blocks a commit, fix **and re-stage** — it reads the index, so cleaning the
working tree alone changes nothing it can see.

## Scope

Does not normalize (no NFC/NFKC), does not replace confusables/homoglyphs, and does
not touch C2PA or statistical watermarks. It makes no claim that cleaned text was
written by a human.
