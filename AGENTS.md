# AGENTS.md

Guidance for coding agents (Cursor, Codex, Claude Code) working in this repo.

Full usage reference: **`skills/text-hygiene/SKILL.md`** — read it before running the
tool. Claude Code and Codex auto-discover it as a skill; Cursor reads this file
instead, so the two things most easily got wrong are repeated below.

## Two things to get right

**`inspect` exiting 0 does not mean "no invisible characters."** It means "nothing
`clean` would change." Findings carry four actions — `strip` and `replace` are applied
by `clean`; `report` is left alone but needs a human edit (currently only bidi
controls); `allow` means context proved the character legitimate. Gating on the default
exit status lets a Trojan Source bidi override through. Use `--fail-on report` for a
gate.

**Never run `--profile code` over documentation.** It strips every invisible character,
which flattens `👨‍👩‍👧` and `1️⃣` into their component codepoints. Docs get `prose`;
source, configs, and commit messages get `code`.

## Working on this repo

```bash
python3 -m venv .venv && .venv/bin/pip install -e . pytest ruff
.venv/bin/python -m pytest -q      # 211 tests
.venv/bin/ruff check src tests
```

Source files must contain **escapes, not literal invisible characters** —
`tests/test_selfhost.py` enforces it. Nobody can review a `U+200D` they cannot see, and
that test already caught a fixture committed with `U+E010` instead of `U+E0100`, which
made its test assert nothing.

`tests/test_hook.py` drives real `git commit` against the real hook. Two traps that make
hook tests pass vacuously, both hit for real here: a global `core.hooksPath` silently
overrides `.git/hooks`, and macOS bash 3.2's `printf %b` does not understand `\U`
escapes. Build fixtures with Python and set a per-repo `core.hooksPath`.

## Invariants that look like bugs

Each of these was changed at some point and had to be reverted. Do not "fix" them
without a concrete failure case:

- **`zero_width` is not context-sensitive.** Making it so meant one em dash shielded an
  unlimited run of zero-width spaces, and all non-Latin prose went unprotected.
- **`_between_ascii` reads the survivor view, not raw text.** Otherwise a character
  about to be removed acts as a non-ASCII neighbour and shields the carrier beside it.
- **`bidi` is `report`, and there is no script detection.** Ordinary RTL prose needs no
  direction override, so an RLO is either an attack or noise.
- **Scope derives from `unicodedata.category == "Cf"`.** A curated list missed 54 of the
  170 `Cf` codepoints in UCD 16.0.
- **The hook reads `git show :path`, not the working tree.** Reading the worktree
  approves content git is not about to commit.

## Using this tool in another project

Install once: `pipx install git+https://github.com/ppf/text-hygiene.git`

Then paste into that project's `AGENTS.md`:

```markdown
## Invisible Unicode
Before committing text, run `text-hygiene inspect --profile code <files>`
(use `--profile prose` for docs — `code` corrupts emoji sequences).
Exit 0 means "nothing to fix", not "nothing found": use `--fail-on report`
to gate on bidi controls too.
```

Optionally install the pre-commit gate:
`ln -sf "$(path-to)/hooks/pre-commit" .git/hooks/pre-commit`
