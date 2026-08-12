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

from .core import ALLOW, PROFILES, UNMODIFIED, Finding, clean, scan, summarize

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

BINARY_SNIFF_BYTES = 8192
DEFAULT_MAX_SIZE = 10 * 1024 * 1024


class InputError(Exception):
    pass


def _decode(data: bytes, label: str, max_size: int) -> str:
    """Shared by file and stdin input, so both reject the same things the same way.

    Piped input used to skip these checks entirely and surface a bare
    UnicodeDecodeError, which callers could not distinguish from a crash.
    """
    if len(data) > max_size:
        raise InputError(f"{label}: larger than {max_size} bytes (raise --max-size)")
    if b"\x00" in data[:BINARY_SNIFF_BYTES]:
        raise InputError(f"{label}: looks binary")
    try:
        # A UTF-8 BOM decodes to U+FEFF and is kept in the string, so a leading BOM
        # can be reported and re-emitted rather than silently dropped.
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputError(f"{label}: not valid UTF-8 ({exc.reason})") from exc


def _read(path: Path, max_size: int) -> str:
    if path.stat().st_size > max_size:
        raise InputError(f"{path}: larger than {max_size} bytes (raise --max-size)")
    return _decode(path.read_bytes(), str(path), max_size)


def _load_all(names: list[str], max_size: int, label: str = "") -> list[tuple[str, str]]:
    """Read every input up front, as (label, text).

    Loading before any write means an unreadable file part-way through an
    --in-place run fails before the first file is modified.
    """
    loaded = []
    for name in names:
        if name == "-":
            stdin_label = label or "<stdin>"
            loaded.append((stdin_label,
                           _decode(sys.stdin.buffer.read(), stdin_label, max_size)))
        else:
            loaded.append((name, _read(Path(name), max_size)))
    return loaded


def _check_writable(path: Path) -> None:
    """Pre-flight the checks this tool raises itself, before anything is written."""
    target = Path(os.path.realpath(path))
    if target.stat().st_nlink > 1:
        raise InputError(f"{target}: has hard links, refusing to replace")
    if not os.access(target, os.W_OK) or not os.access(target.parent, os.W_OK):
        raise InputError(f"{target}: not writable")


def _write_in_place(path: Path, text: str) -> None:
    target = Path(os.path.realpath(path))
    _check_writable(target)
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
    """Exit status answers whichever question --fail-on selects.

    `actionable` (the default) asks "would clean modify this?". A commit gate asks
    the wider "is anything here worth a human look?", which additionally catches
    REPORT findings - a bidi override in a README is exactly what a gate exists to
    stop, and clean leaves it alone under prose. Neither setting fails on ALLOW,
    since clean will never remove those and the gate would be unsatisfiable.
    """
    results = [(label, scan(text, args.profile))
               for label, text in _load_all(args.files, args.max_size, args.label)]
    _emit(results, args, sys.stdout)
    exempt = UNMODIFIED if args.fail_on == "actionable" else (ALLOW,)
    failing = [f for _, findings in results for f in findings
               if f.action not in exempt]
    return EXIT_FINDINGS if failing else EXIT_CLEAN


def _clean(args) -> int:
    if args.output and len(args.files) > 1:
        raise InputError("-o takes a single input file")
    if args.in_place and "-" in args.files:
        raise InputError("--in-place cannot be used with stdin")

    loaded = _load_all(args.files, args.max_size)
    if args.in_place:
        for name in args.files:
            _check_writable(Path(name))

    results = []
    written: list[str] = []
    for (label, text), name in zip(loaded, args.files, strict=True):
        cleaned, findings = clean(text, args.profile)
        results.append((label, findings))
        try:
            if args.in_place:
                _write_in_place(Path(name), cleaned)
                written.append(name)
            elif args.output:
                Path(args.output).write_text(cleaned, encoding="utf-8", newline="")
            else:
                sys.stdout.write(cleaned)
        except OSError as exc:
            # Pre-flight cannot rule out ENOSPC or a race, so say plainly which
            # files were already replaced rather than leaving the run ambiguous.
            if written:
                sys.stderr.write(
                    f"text-hygiene: already rewrote {', '.join(written)} "
                    f"before failing on {name}\n")
            raise InputError(f"{name}: {exc}") from exc

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
    inspector = sub.add_parser("inspect", parents=[common],
                               help="report findings; exit 1 per --fail-on")
    inspector.add_argument("--label", default="",
                           help="path to report for stdin input, so a caller "
                                "streaming a file's content can name it")
    inspector.add_argument("--fail-on", choices=("actionable", "report"),
                           default="actionable",
                           help="actionable: only what clean would change (default). "
                                "report: also flag characters left in place that "
                                "still warrant a look, for use as a gate.")
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
