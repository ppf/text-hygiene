import json
import os
import subprocess
import sys

import pytest

from texthygiene.cli import EXIT_CLEAN, EXIT_ERROR, EXIT_FINDINGS, main

ZWSP = "\u200b"


def write(tmp_path, name, text, encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding) if isinstance(text, str) else text)
    return path


def test_inspect_exit_codes(tmp_path, capsys):
    clean_file = write(tmp_path, "clean.txt", "hello\n")
    dirty_file = write(tmp_path, "dirty.txt", f"hel{ZWSP}lo\n")
    assert main(["inspect", str(clean_file)]) == EXIT_CLEAN
    assert main(["inspect", str(dirty_file)]) == EXIT_FINDINGS


def test_report_only_findings_do_not_fail(tmp_path, capsys):
    """Exit status answers "would clean modify this?", so advisory findings pass.

    Without this, a file whose invisible characters are all legitimate - an emoji
    fixture, a doc containing RTL - could never satisfy the hook.
    """
    emoji = write(tmp_path, "emoji.txt", "\U0001F468\u200d\U0001F469\u200d\U0001F467")
    assert main(["inspect", str(emoji), "--profile", "prose"]) == EXIT_CLEAN
    assert "report" in capsys.readouterr().out, "findings must still be printed"
    # The same file under `code`, which does strip those joiners, must fail.
    assert main(["inspect", str(emoji), "--profile", "code"]) == EXIT_FINDINGS


def test_error_exit_code_is_distinct_from_findings(tmp_path, capsys):
    """A crash must not look like a finding to the pre-commit hook."""
    assert main(["inspect", str(tmp_path / "missing.txt")]) == EXIT_ERROR


def test_binary_file_rejected(tmp_path):
    write(tmp_path, "img.png", b"\x89PNG\r\n\x1a\n\x00\x00")
    assert main(["inspect", str(tmp_path / "img.png")]) == EXIT_ERROR


def test_non_utf8_fails_loudly(tmp_path, capsys):
    write(tmp_path, "latin.txt", b"caf\xe9 not utf8")
    assert main(["inspect", str(tmp_path / "latin.txt")]) == EXIT_ERROR
    assert "not valid UTF-8" in capsys.readouterr().err


def test_oversize_rejected(tmp_path):
    big = write(tmp_path, "big.txt", "x" * 5000)
    assert main(["inspect", str(big), "--max-size", "100"]) == EXIT_ERROR


def test_json_output(tmp_path, capsys):
    path = write(tmp_path, "d.txt", f"a{ZWSP}b")
    main(["inspect", str(path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    finding = payload["files"][0]["findings"][0]
    assert finding["codepoint"] == "U+200B"
    assert finding["action"] == "strip"


def test_json_stays_parseable_with_multiple_files(tmp_path, capsys):
    """One document, not one per file - concatenated objects don't parse."""
    a = write(tmp_path, "a.txt", f"a{ZWSP}b")
    b = write(tmp_path, "b.txt", f"c{ZWSP}d")
    main(["inspect", str(a), str(b), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert [f["path"] for f in payload["files"]] == [str(a), str(b)]


def test_in_place_does_not_partially_apply_when_an_input_fails(tmp_path, capsys):
    """A bad file must fail before the first write, not halfway through."""
    good = write(tmp_path, "good.txt", f"a{ZWSP}b")
    write(tmp_path, "bad.png", b"\x89PNG\x00\x00")
    later = write(tmp_path, "later.txt", f"c{ZWSP}d")
    before = good.read_bytes()
    assert main(["clean", str(good), str(tmp_path / "bad.png"), str(later),
                 "--in-place"]) == EXIT_ERROR
    assert good.read_bytes() == before, "first file was modified despite the failure"
    assert later.read_bytes() == f"c{ZWSP}d".encode()


def test_output_and_in_place_are_mutually_exclusive(tmp_path, capsys):
    path = write(tmp_path, "d.txt", f"a{ZWSP}b")
    with pytest.raises(SystemExit):
        main(["clean", str(path), "-o", str(tmp_path / "o.txt"), "--in-place"])


def test_stats_output(tmp_path, capsys):
    path = write(tmp_path, "d.txt", f"a{ZWSP}b")
    main(["inspect", str(path), "--stats"])
    assert "zero_width/strip: 1" in capsys.readouterr().out


def test_clean_json_goes_to_stderr_not_stdout(tmp_path, capsys):
    path = write(tmp_path, "d.txt", f"a{ZWSP}b")
    main(["clean", str(path), "--json"])
    captured = capsys.readouterr()
    assert captured.out == "ab", "stdout must carry only the cleaned text"
    assert json.loads(captured.err)["files"][0]["findings"]


def test_clean_exits_zero_even_with_findings(tmp_path, capsys):
    """clean reports through its output, not its exit status."""
    path = write(tmp_path, "d.txt", f"a{ZWSP}b")
    assert main(["clean", str(path)]) == EXIT_CLEAN


def test_clean_to_stdout(tmp_path, capsys):
    path = write(tmp_path, "d.txt", f"a{ZWSP}b")
    assert main(["clean", str(path)]) == EXIT_CLEAN
    assert capsys.readouterr().out == "ab"


def test_in_place_preserves_mode(tmp_path):
    path = write(tmp_path, "d.txt", f"a{ZWSP}b")
    os.chmod(path, 0o640)
    before = path.stat().st_mode
    assert main(["clean", str(path), "--in-place"]) == EXIT_CLEAN
    assert path.read_text("utf-8") == "ab"
    assert path.stat().st_mode == before


def test_in_place_writes_through_symlink(tmp_path):
    real = write(tmp_path, "real.txt", f"a{ZWSP}b")
    link = tmp_path / "link.txt"
    link.symlink_to(real)
    main(["clean", str(link), "--in-place"])
    assert link.is_symlink(), "symlink must survive, not be replaced by a regular file"
    assert real.read_text("utf-8") == "ab"


def test_in_place_refuses_hardlinks(tmp_path, capsys):
    real = write(tmp_path, "real.txt", f"a{ZWSP}b")
    os.link(real, tmp_path / "hard.txt")
    assert main(["clean", str(real), "--in-place"]) == EXIT_ERROR
    assert real.read_bytes() == f"a{ZWSP}b".encode()


def test_output_flag_rejected_for_multiple_inputs(tmp_path, capsys):
    a = write(tmp_path, "a.txt", "a")
    b = write(tmp_path, "b.txt", "b")
    assert main(["clean", str(a), str(b), "-o", str(tmp_path / "o.txt")]) == EXIT_ERROR


def test_in_place_rejected_with_stdin():
    assert main(["clean", "-", "--in-place"]) == EXIT_ERROR


def test_stdin_roundtrip():
    proc = subprocess.run(
        [sys.executable, "-m", "texthygiene.cli", "clean", "-"],
        input=f"a{ZWSP}b".encode(), capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b"ab"


@pytest.mark.parametrize("profile,expected", [("prose", "a\u00a0b"), ("code", "a b")])
def test_profile_flag(tmp_path, capsys, profile, expected):
    path = write(tmp_path, "d.txt", "a\u00a0b")
    main(["clean", str(path), "--profile", profile])
    assert capsys.readouterr().out == expected
