#!/usr/bin/env python3
"""Confidential-term check for blog-kit (stdlib only).

The term list is ~/.config/blog-kit/confidential.txt on each machine. There
is no option or environment variable that points elsewhere. The file must be
a regular file (a symlink is refused), at most 256 KB, strict UTF-8, one term
per line; "#" lines and blank lines are ignored and each term is stripped. A
list with no terms blocks.

    list     check the term list only
    scan     check the term list, then scan text files for terms

scan takes files under --root (default: current folder) or --slug, which
names the public copy src/content/posts/<slug>.md and the files in
src/assets/posts/<slug>/ (the paths and slug rule come from the installed
verifier, adapters/astro/blogkit_verify.py); both give the same result for
the same files. Paths must stay inside the root with no symlink
component. Markdown (.md, at most 1 MB) and SVG (.svg, at most 10 MB) are
read as strict UTF-8; binary images are skipped and listed (their content is
out of scope), any other type is an error. Both sides are NFC-normalized and
casefolded and a term anywhere in a line blocks. Heuristics only warn, once
per category and line: private_ip (a private IPv4 address), internal_host
(a name ending in .internal, .corp, .local or .lan), email, issue_key (like
ABC-123), local_path (/Users/, /home/, ~/ or a drive letter) and number_unit
(a number with %, 원, 만, 명, ms, TPS, QPS, 건 or 배). They are noisy on
purpose; a person confirms them at approval. A file that cannot be read or
decoded, and any unexpected failure, blocks as "could not check".

Output is one JSON object with fixed codes and categories only. Terms never
appear in it. Exit 0 when the result is pass, 1 when it blocks.

Other modules load this file from the install path and call the same checks:
scan_text(data, terms) checks bytes (strict UTF-8) or text the caller already
read and decided is text, whatever its extension, and returns categories and
line numbers only; scan uses it for every file. terms_digest(terms) and
list_digest() give the SHA-256 hex of the normalized terms (NFC, casefold,
deduplicated, sorted, joined by newlines), so comments, blank lines, order
and case do not change it; a missing or empty list blocks instead.

Usage:
    python3 confidential_check.py list
    python3 confidential_check.py scan [--root DIR] FILE [FILE ...]
    python3 confidential_check.py scan [--root DIR] --slug SLUG
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import ipaddress
import json
import os
import re
import stat
import sys
import unicodedata
from pathlib import Path

VERIFIER_PATH = Path(__file__).resolve().parent.parent / "adapters" / "astro" / "blogkit_verify.py"
LIST_RELATIVE = Path(".config") / "blog-kit" / "confidential.txt"
MAX_LIST_BYTES = 256 * 1024
TEXT_CAPS = {".md": 1024 * 1024, ".svg": 10 * 1024 * 1024}
SKIPPED_TYPES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"})
MAX_FILES = 1000
IPV4_RE = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])")
# Each pattern anchors on a short fixed part (a suffix, "@", a hyphen, a unit)
# so a long line is scanned in linear time; lines of an SVG can be megabytes.
HEURISTICS = (
    ("internal_host", re.compile(r"(?<=[a-z0-9-])\.(?:internal|corp|local|lan)\b", re.IGNORECASE)),
    ("email", re.compile(r"(?<=[A-Za-z0-9._%+-])@[A-Za-z0-9-]+\.[A-Za-z0-9]")),
    ("issue_key", re.compile(r"\b[A-Z][A-Z0-9]{1,9}-[0-9]")),
    ("local_path", re.compile(r"(?<![\w.~/])(?:/Users/|/home/|~/|[A-Za-z]:\\)")),
    ("number_unit", re.compile(r"[0-9]\s?(?:%|원|만|명|건|배|ms\b|TPS\b|QPS\b)")),
)


class Blocked(Exception):
    """A fixed code; never carries text from the files read."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _normal(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def load_terms() -> list[str]:
    """The normalized terms of the term list, or Blocked with a list_* code."""
    path = Path.home() / LIST_RELATIVE
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        raise Blocked("list_missing") from None
    except OSError:
        if path.is_symlink():
            raise Blocked("list_symlink") from None
        raise Blocked("list_unreadable") from None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise Blocked("list_not_regular")
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(MAX_LIST_BYTES + 1)
    if len(data) > MAX_LIST_BYTES:
        raise Blocked("list_too_large")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Blocked("list_not_utf8") from None
    terms = [_normal(line.strip()) for line in text.splitlines()]
    terms = [term for term in terms if term and not term.startswith("#")]
    if not terms:
        raise Blocked("list_empty")
    return terms


def terms_digest(terms: list[str]) -> str:
    """SHA-256 hex of the normalized terms, deduplicated and sorted, joined by newlines.

    Comments, blank lines, order, case and duplicates do not change it, so
    two machines compare lists without sharing them. No terms blocks.
    """
    unique = sorted({_normal(term) for term in terms if term})
    if not unique:
        raise Blocked("list_empty")
    return hashlib.sha256("\n".join(unique).encode("utf-8")).hexdigest()


def list_digest() -> str:
    """terms_digest of the term list, or Blocked with a list_* code."""
    return terms_digest(load_terms())


def _inside(root: Path, argument: str) -> str:
    """The root-relative POSIX path of a file argument, refusing escapes and symlinks."""
    path = Path(argument)
    if path.is_absolute():
        try:
            path = path.relative_to(root)
        except ValueError:
            raise Blocked("outside_root") from None
    if not path.parts or ".." in path.parts:
        raise Blocked("outside_root")
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise Blocked("symlink_refused")
    return path.as_posix()


def _read_bytes(root: Path, relative: str, cap: int) -> bytes:
    try:
        fd = os.open(root / relative, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        raise Blocked("missing_file") from None
    except OSError:
        raise Blocked("unreadable_file") from None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise Blocked("not_a_regular_file")
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(cap + 1)
    if len(data) > cap:
        raise Blocked("file_too_large")
    return data


def _private_ip(line: str) -> bool:
    for candidate in IPV4_RE.findall(line):
        try:
            if ipaddress.IPv4Address(candidate).is_private:
                return True
        except ValueError:
            continue
    return False


def heuristic_categories(line: str) -> list[str]:
    """The warning categories one line falls in, in a fixed order."""
    line = unicodedata.normalize("NFC", line)
    found = ["private_ip"] if _private_ip(line) else []
    return found + [category for category, pattern in HEURISTICS if pattern.search(line)]


def scan_text(data: bytes | str, terms: list[str]) -> dict:
    """Term matches (blocks) and heuristic warnings in text the caller already read.

    The caller decides that the data is text (any extension: md, svg, json,
    yaml) and applies its own size cap; bytes are decoded as strict UTF-8 or
    Blocked("not_utf8"). terms comes from load_terms(). The result holds
    {"category", "line"} entries only, never a term or a line.
    """
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError:
            raise Blocked("not_utf8") from None
    blocks: list[dict] = []
    warnings: list[dict] = []
    for number, line in enumerate(data.split("\n"), 1):
        normal = _normal(line)
        if any(term in normal for term in terms):
            blocks.append({"category": "confidential_term", "line": number})
        warnings += [{"category": category, "line": number} for category in heuristic_categories(line)]
    return {"blocks": blocks, "warnings": warnings}


def scan_files(root: Path, arguments: list[str]) -> dict:
    """Term matches (blocks) for each file argument, by category, file and line."""
    try:
        terms = load_terms()
    except Blocked as exc:
        return {"status": "block", "list": exc.code, "blocks": [], "warnings": [], "skipped": [], "errors": []}
    blocks: list[dict] = []
    warnings: list[dict] = []
    skipped: list[str] = []
    errors: list[dict] = []
    for argument in arguments[:MAX_FILES]:
        try:
            relative = _inside(root, argument)
        except Blocked as exc:
            errors.append({"code": exc.code, "file": ""})
            continue
        suffix = Path(relative).suffix.lower()
        if suffix in SKIPPED_TYPES:
            skipped.append(relative)
            continue
        if suffix not in TEXT_CAPS:
            errors.append({"code": "unsupported_type", "file": relative})
            continue
        try:
            found = scan_text(_read_bytes(root, relative, TEXT_CAPS[suffix]), terms)
        except Blocked as exc:
            errors.append({"code": exc.code, "file": relative})
            continue
        blocks += [{**block, "file": relative} for block in found["blocks"]]
        warnings += [{**warning, "file": relative} for warning in found["warnings"]]
    if len(arguments) > MAX_FILES:
        errors.append({"code": "too_many_files", "file": ""})
    status = "block" if blocks or errors else "pass"
    return {"status": status, "list": "ok", "blocks": blocks, "warnings": warnings, "skipped": skipped,
            "errors": errors}


def slug_files(root: Path, slug: str) -> list[str]:
    """The public copy's post and its asset files (root-relative), post first, assets by name."""
    spec = importlib.util.spec_from_file_location("blogkit_verify", VERIFIER_PATH)
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    if not verifier.SLUG_RE.fullmatch(slug) or len(slug) > verifier.MAX_SLUG:
        raise Blocked("arguments_invalid")
    folder = _inside(root, f"{verifier.ASSETS_DIR}/{slug}")
    files = [verifier.post_path_for(slug)]
    if (root / folder).is_dir():
        with os.scandir(root / folder) as entries:
            names = []
            for entry in entries:
                names.append(entry.name)
                if len(names) > MAX_FILES:
                    raise Blocked("too_many_files")
        files += [f"{folder}/{name}" for name in sorted(names)]
    return files


def check_list() -> dict:
    try:
        load_terms()
    except Blocked as exc:
        return {"status": "block", "list": exc.code}
    return {"status": "pass", "list": "ok"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Confidential-term check for blog-kit.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="Check the term list only")
    scan = commands.add_parser("scan", help="Check the term list, then scan files")
    scan.add_argument("--root", default=None, help="Folder the files are in (default: current folder)")
    scan.add_argument("--slug", default=None, help="Scan the public copy of this slug instead of files")
    scan.add_argument("files", nargs="*", help="Files under the root")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report: dict = {"command": args.command}
    try:
        if args.command == "list":
            report.update(check_list())
        else:
            root = Path(args.root or Path.cwd())
            if root.is_symlink() or not root.is_dir():
                raise Blocked("root_invalid")
            if bool(args.slug) == bool(args.files):
                raise Blocked("arguments_invalid")
            root = root.resolve()
            report.update(scan_files(root, slug_files(root, args.slug) if args.slug else args.files))
    except Blocked as exc:
        report.update(status="block", errors=[{"code": exc.code, "file": ""}])
    except Exception:  # noqa: BLE001 - any failure is "could not check", never a pass
        report.update(status="block", errors=[{"code": "check_failed"}])
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
