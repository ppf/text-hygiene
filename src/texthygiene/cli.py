"""Command line interface. The only module that touches the filesystem."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import TextIO

from .core import PROFILES, REPORT, Finding, clean, scan, summarize

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

BINARY_SNIFF_BYTES = 8192
DEFAULT_MAX_SIZE = 10 * 1024 * 1024


class InputError(Exception):
    pass


def _read(path: Path, max_size: int) -> str:
    if path.stat().st_size > max_size:
        raise InputError(f"{path}: larger than {max_size} bytes (raise --max-size)")
    data = path.read_bytes()
    if b"\x00" in data[:BINARY_SNIFF_BYTES]:
        raise InputError(f"{path}: looks binary")
    try:
        # A UTF-8 BOM decodes to U+FEFF and is kept in the string, so a leading BOM
        # can be reported and re-emitted rather than silently dropped.
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputError(f"{path}: not valid UTF-8 ({exc.reason})") from exc


def _load_all(names: list[str], max_size: int) -> list[tuple[str, str]]:
    """Read every input up front, as (label, text).

    Loading before any write means a bad file in the middle of an --in-place run
    fails before the first file is modified, rather than leaving the run half
    applied with nothing saying which half.
    """
    loaded = []
    for name in names:
        if name == "-":
            loaded.append(("<stdin>", sys.stdin.read()))
        else:
            loaded.append((name, _read(Path(name), max_size)))
    return loaded


def _write_in_place(path: Path, text: str) -> None:
    target = Path(os.path.realpath(path))
    if target.stat().st_nlink > 1:
        raise InputError(f"{target}: has hard links, refusing to replace")
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        shutil.copystat(target, tmp)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _emit(results: list[tuple[str, list[Finding]]], args, stream: TextIO) -> None:
    """Write one report covering every input.

    JSON is emitted as a single document rather than one object per file, so that
    passing several paths still produces something json.loads can read.
    """
    if args.json:
        json.dump({"profile": args.profile,
                   "files": [{"path": path,
                              "findings": [f.to_dict() for f in findings]}
                             for path, findings in results]}, stream, indent=2)
        stream.write("\n")
        return
    for path, findings in results:
        stream.writelines(
            f"{path}:{f.line}:{f.column}: {f.action}: "
            f"U+{f.codepoint:04X} {f.name} [{f.category}]\n"
            for f in findings)
    if args.stats:
        merged = [f for _, findings in results for f in findings]
        stream.writelines(f"  {key}: {count}\n"
                          for key, count in sorted(summarize(merged).items()))


def _inspect(args) -> int:
    """Exit status answers "would `clean` modify these files?".

    Report-only findings are advisory - the profile has already decided not to
    touch them - so they print without failing. Otherwise a file whose invisible
    characters are all legitimate, such as an emoji fixture, could never pass.
    """
    results = [(label, scan(text, args.profile))
               for label, text in _load_all(args.files, args.max_size)]
    _emit(results, args, sys.stdout)
    actionable = any(f.action != REPORT for _, findings in results for f in findings)
    return EXIT_FINDINGS if actionable else EXIT_CLEAN


def _clean(args) -> int:
    if args.output and len(args.files) > 1:
        raise InputError("-o takes a single input file")
    if args.in_place and "-" in args.files:
        raise InputError("--in-place cannot be used with stdin")

    loaded = _load_all(args.files, args.max_size)
    results = []
    for (label, text), name in zip(loaded, args.files, strict=True):
        cleaned, findings = clean(text, args.profile)
        results.append((label, findings))
        if args.in_place:
            _write_in_place(Path(name), cleaned)
        elif args.output:
            Path(args.output).write_text(cleaned, encoding="utf-8", newline="")
        else:
            sys.stdout.write(cleaned)

    if args.stats or args.json:
        _emit(results, args, sys.stderr)
    return EXIT_CLEAN


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="text-hygiene",
        description="Detect and strip invisible Unicode from text.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("files", nargs="+", help="input files, or - for stdin")
    common.add_argument("--profile", choices=PROFILES, default="prose")
    common.add_argument("--json", action="store_true")
    common.add_argument("--stats", action="store_true")
    common.add_argument("--max-size", type=int, default=DEFAULT_MAX_SIZE)

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inspect", parents=[common],
                   help="report findings; exit 1 if any are actionable")
    cleaner = sub.add_parser("clean", parents=[common], help="write cleaned text")
    destination = cleaner.add_mutually_exclusive_group()
    destination.add_argument("-o", "--output")
    destination.add_argument("--in-place", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _inspect(args) if args.command == "inspect" else _clean(args)
    except (InputError, OSError, UnicodeDecodeError) as exc:
        sys.stderr.write(f"text-hygiene: {exc}\n")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
