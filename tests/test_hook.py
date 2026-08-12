"""Execute the real pre-commit hook against real git commits.

Every hook defect found in review shipped with a green suite, because nothing here
ran the hook - the only test that touched it read it as text looking for literal
invisible characters. These tests drive `git commit` and then inspect the committed
blob, which is the only thing that actually settles whether the gate works.

Two traps that make hook tests pass vacuously, both hit during review:
  - a global `core.hooksPath` silently overrides `.git/hooks`, so each repo here
    sets its own and the fixture asserts the copied hook is non-empty;
  - macOS ships bash 3.2, whose `printf %b` does not understand `\\U` escapes, so
    fixtures are written by Python, never by shell echo.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks/pre-commit"
ZWSP = "\u200b"


@pytest.fixture
def repo(tmp_path):
    """A scratch git repo whose pre-commit hook is this repo's hook."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    hooks = tmp_path / ".githooks"
    hooks.mkdir()
    shutil.copy(HOOK, hooks / "pre-commit")
    os.chmod(hooks / "pre-commit", 0o755)
    assert (hooks / "pre-commit").stat().st_size > 0, "hook was not copied"

    for key, value in [("core.hooksPath", str(hooks)),
                       ("user.email", "t@example.com"),
                       ("user.name", "t")]:
        subprocess.run(["git", "-C", str(tmp_path), "config", key, value], check=True)

    # The CLI must be importable as a console script for the hook to find it.
    bindir = Path(sys.executable).parent
    env = dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")
    tmp_path.joinpath(".env_marker").write_text("")
    return tmp_path, env


def commit(repo, message="t"):
    path, env = repo
    return subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", message],
                          capture_output=True, text=True, env=env, check=False)


def stage(repo, name, content):
    path, env = repo
    target = path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", name], check=True, env=env)
    return target


def committed_bytes(repo, name):
    path, env = repo
    return subprocess.run(["git", "-C", str(path), "show", f"HEAD:{name}"],
                          capture_output=True, env=env, check=True).stdout


# --- the gate must read the index, not the worktree -------------------------

def test_cleaning_the_worktree_without_restaging_does_not_pass(repo):
    """The defect: fix-then-commit shipped the dirty blob from the index."""
    path, _ = repo
    stage(repo, "c.md", f"hel{ZWSP}lo\n")
    assert commit(repo).returncode != 0

    # Follow the hook's own advice, but do not re-stage.
    (path / "c.md").write_text("hello\n", encoding="utf-8")
    result = commit(repo)
    assert result.returncode != 0, (
        "worktree was cleaned but the index still holds the ZWSP; the hook must "
        "read the staged blob")


def test_restaging_the_fix_lets_the_commit_through(repo):
    stage(repo, "c.md", f"hel{ZWSP}lo\n")
    assert commit(repo).returncode != 0
    stage(repo, "c.md", "hello\n")
    assert commit(repo).returncode == 0
    assert committed_bytes(repo, "c.md") == b"hello\n"


# --- coverage of the file kinds the gate has to handle ----------------------

def test_python_file_with_zero_width_is_blocked(repo):
    """ruff detects no zero-width characters, so delegating to it alone let these
    straight through - on the most common file type in the repos this guards."""
    stage(repo, "mod.py", f'x = "a{ZWSP}b"\n')
    assert commit(repo).returncode != 0


def test_renamed_and_modified_file_is_scanned(repo):
    path, env = repo
    stage(repo, "doc.md", "original content here\n")
    assert commit(repo).returncode == 0
    subprocess.run(["git", "-C", str(path), "mv", "doc.md", "renamed.md"],
                   check=True, env=env)
    stage(repo, "renamed.md", f"original{ZWSP} content here\n")
    assert commit(repo).returncode != 0, "git classifies mv+edit as R, not ACM"


def test_path_with_spaces_does_not_break_the_hook(repo):
    stage(repo, "My Notes.md", "clean content\n")
    result = commit(repo)
    assert result.returncode == 0, f"spaces in a path broke the hook: {result.stderr}"


def test_path_with_spaces_is_still_scanned(repo):
    stage(repo, "My Notes.md", f"dirty{ZWSP} content\n")
    assert commit(repo).returncode != 0


def test_binary_file_is_skipped_not_fatal(repo):
    path, env = repo
    (path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    subprocess.run(["git", "-C", str(path), "add", "logo.png"], check=True, env=env)
    result = commit(repo)
    assert result.returncode == 0, (
        f"a binary file must not block commits: {result.stderr}")


def test_non_utf8_file_is_skipped_not_fatal(repo):
    path, env = repo
    (path / "legacy.csv").write_bytes(b"caf\xe9,1\n")
    subprocess.run(["git", "-C", str(path), "add", "legacy.csv"], check=True, env=env)
    result = commit(repo)
    assert result.returncode == 0, f"latin-1 file blocked the commit: {result.stderr}"


# --- what must still get through -------------------------------------------

@pytest.mark.parametrize("name,content", [
    ("emoji.md", "family \U0001F468\u200d\U0001F469\u200d\U0001F467\n"),
    ("keycap.md", "press 1\ufe0f⃣\n"),
    ("nbsp.md", "10\u00a0km\n"),
    ("bom.md", "\ufeffheading\n"),
    ("flag.md",
     "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F\n"),
    ("clean.md", "nothing here\n"),
])
def test_legitimate_content_commits(repo, name, content):
    stage(repo, name, content)
    result = commit(repo)
    assert result.returncode == 0, (
        f"{name} was wrongly blocked: {result.stdout}{result.stderr}")


# --- payloads that previously produced zero findings ------------------------

def test_tag_sequence_payload_is_blocked(repo):
    payload = "".join(chr(0xE0000 + ord(c)) for c in "SECRETPAYLOAD")
    stage(repo, "note.md", f"hello \U0001F3F4{payload}\U000E007F world\n")
    assert commit(repo).returncode != 0


def test_shielded_carrier_run_is_blocked(repo):
    stage(repo, "note.md", "Report—" + "\u200d\u200c" * 16 + "end\n")
    assert commit(repo).returncode != 0


def test_trojan_source_bidi_is_blocked(repo):
    stage(repo, "note.md", "admin\u202e txet\n")
    assert commit(repo).returncode != 0
