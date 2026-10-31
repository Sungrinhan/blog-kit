#!/usr/bin/env python3
"""blog-kit engine CLI for an Astro blog repo (stdlib only).

Commands (each prints one JSON object and exits 1 on failure):

    convert SLUG        public copy from inbox/<slug>/draft/<slug>.md
    check SLUG          publish gates for the public copy
    status SLUG         current hash, evidence and missing records
    approve SLUG        record approval for --hash and --warnings
    publish SLUG        flip the draft line and commit the post and evidence
    sync-verifier       copy the installed verifier to the blog repo

Every command first requires the install identity: this file must not live
inside the blog repo, and the install doctor's identity checks (a to f,
scripts/doctor.py in this install) must all be ok for this file's own path.
sync-verifier stops there; every other command also requires the blog repo's
verifier copy to match this install (doctor check i), so a stale copy is
repaired with sync-verifier and nothing else, and the blog repo to be the root
of a git work tree. Commands that change the evidence or the public copy
(convert, check, approve, publish) hold an exclusive flock on
inbox/<slug>/blogkit.lock, which records the process id and host; only a lock
left by a dead process on this host is cleared, and judging it happens under
the flock.

convert creates or replaces the public copy from the upstream-format draft. The
draft frontmatter is a restricted subset (title, description of at most 160
characters, date, optional lastUpdated and tags, and dropped upstream keys);
any other key fails. References are sorted in this order: a backslash or a
scheme-less authority is refused, a scheme or authority makes an external URL
that the upstream renderer's link-safety rule must allow, "/" and "#" stay as
site URLs and anchors, and the rest are file references that must name an
image inside the draft folder; those are copied to the post's asset folder
and rewritten. A remaining draft marker fails. A published post is always
refused. An existing post is replaced only when it is a draft whose post and
assets match the evidence's public_copy record exactly; a file without a
record, a changed copy and an asset the record does not own are kept and
refused. A new content hash starts the records afresh; the same hash keeps
them. The changed post, assets and evidence go in as one unit: staged under
inbox/<slug>/, a journal, a re-check that the targets are as they were at
the start, a backup and one rename per target, an applied mark, then
cleanup. A handled error while moving rolls the unit back on the spot.
When a run stops midway, the stage, backup or journal stays behind: every
other command for that slug is refused, and the next convert first judges
the files actually at each target, backup and stage path against the
journal's digests. Before the applied mark it undoes every target (each step
can run again); after it the new copy stays and only cleanup is left. A lost
original, a file the journal does not know or a journal naming any path
convert would not use stops it with nothing changed.

check runs publish gates 1 to 6 and records each one in the evidence:
1 tools (Python 3.11+, node and npx on PATH, the git root, the verifier copy,
the term list through scripts/confidential_check.py), 2 body safety (raw HTML
and inline SVG outside code and autolinks, and SVG asset files, against the
upstream renderer's allow-list of tags, attributes and href/src schemes;
backticks inside a raw HTML block are not code; an autolink's scheme must be
one that rule allows; Markdown references by the convert rules; a file
reference must be an image
in the post's asset folder; violations are reported, never fixed), 3
`npx --no-install astro check` by its exit code, 4 a review record for the
current hash with no P0 (written by the M2c reviewer), 5 the confidential
check over the post and its asset files (a term blocks; heuristics become
warning ids), 6 the draft preview build (`npx --no-install astro build
--outDir .blogkit-preview` with BLOGKIT_INCLUDE_DRAFTS=1, into a folder the
blog repo's .gitignore must list, emptied first) whose post page must exist,
whose local images must resolve inside it, and against which the post's site
links and anchors are resolved; the post's external URLs (links, images and
autolinks) go through the link
check module (adapters/astro/blogkit_links.py, with the draft folder's
external-links.allowed), and a transient failure is a warning. A warning id
is <gate>:<category>:<file>:<line>[:<URL digest>]. The content hash is
fixed at the start and computed again before writing; a change in between
records nothing. Evidence for another hash starts its records afresh and
keeps public_copy. approve records the person's approval only for the full
current hash and the digest of the latest gate warnings, and only when gates
1 to 6, the review and the learning check pass. publish needs complete
evidence, every asset file in HEAD with the same bytes, and nothing staged
but the post and its evidence; it flips the draft line atomically and
commits those two paths as "publish: <slug>" from the checked bytes (blobs in
a private index that the commit hooks run against; anything else a hook
stages refuses the commit, and an edit made before the swap is kept and
stops it; a swap or index update a stopped run left behind is finished
first). After a failed commit,
running it again only commits; nothing is ever reset. status shows the current and evidence hash, the gate
records for the current hash, their warning ids and digest, and what the
evidence still lacks.

Output holds fixed error codes, check ids and reasons, versions,
repo-relative paths and line numbers only. It never prints post text, external command output
or URLs.

Usage:
    python3 blogkit.py COMMAND [SLUG] [--blog-repo PATH]
"""

from __future__ import annotations

import argparse
import bisect
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
import posixpath
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

INSTALL_ROOT = Path(__file__).resolve().parent.parent.parent
DOCTOR_PATH = INSTALL_ROOT / "scripts" / "doctor.py"
VERIFIER_PATH = INSTALL_ROOT / "adapters" / "astro" / "blogkit_verify.py"
MANIFEST_PATH = INSTALL_ROOT / ".claude-plugin" / "plugin.json"
RENDER_PATH = INSTALL_ROOT / "scripts" / "blog_render.py"
CONFIDENTIAL_PATH = INSTALL_ROOT / "scripts" / "confidential_check.py"
LINKS_PATH = INSTALL_ROOT / "adapters" / "astro" / "blogkit_links.py"
# The draft preview build's own output folder; the blog repo's .gitignore must list it.
PREVIEW_DIR = ".blogkit-preview"
VERIFIER_COPY = "scripts/blogkit_verify.py"
IDENTITY_TIMEOUT = 20.0
COMMAND_TIMEOUT = 60.0
ASTRO_CHECK_TIMEOUT = 600.0
PREVIEW_TIMEOUT = 600.0
MAX_LOCATIONS = 50
# A path-shaped token followed by :<line> in external command output.
LOCATION_RE = re.compile(r"(?P<path>[\w./@+-]*[\w@+-]\.[A-Za-z0-9]+):(?P<line>[0-9]+)")
SLUG_COMMANDS = ("convert", "check", "status", "approve", "publish")
LOCK_COMMANDS = ("convert", "check", "approve", "publish")
LOCK_NAME = "blogkit.lock"
MAX_LOCK_BYTES = 4096

# convert: the restricted subset of the upstream draft frontmatter.
DRAFT_KEPT_KEYS = ("title", "description", "date", "lastUpdated", "tags")
DRAFT_DROPPED_KEYS = frozenset({"author", "canonical", "lang", "kicker", "og_image_alt"})
DRAFT_REQUIRED_KEYS = ("title", "description", "date")
MAX_DESCRIPTION = 160
DRAFT_KEY_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*):(?: (.*))?")
DRAFT_ITEM_RE = re.compile(r"  - (.*)")
SINGLE_QUOTED_RE = re.compile(r"'((?:[^']|'')*)'")
# A YAML plain scalar that cannot be read as anything but this string.
PLAIN_RE = re.compile(r"""[^\s\-?:,\[\]{}#&*!|>'"%@`](?:[^#:]|:(?! )|(?<! )#)*""")
FENCE_RE = re.compile(r" {0,3}(`{3,}|~{3,})")
# Block quote and list item markers in front of a line's content.
CONTAINER_RE = re.compile(r"(?:[ \t]*>[ \t]?|[ \t]*(?:[-+*]|\d{1,9}[.)])[ \t]+)*")
# CommonMark raw HTML block start conditions 1 to 7 and their end patterns (None: a blank line).
HTML_BLOCK_TAGS = (
    "address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|details|dialog|dir|div|"
    "dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|header|hr|html|iframe|legend|li|"
    "link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|search|section|summary|table|tbody|td|"
    "tfoot|th|thead|title|tr|track|ul")
HTML_BLOCKS = (
    (re.compile(r"<(?:script|pre|style|textarea)(?:[\s>]|$)", re.IGNORECASE),
     re.compile(r"</(?:script|pre|style|textarea)>", re.IGNORECASE)),
    (re.compile(r"<!--"), re.compile(r"-->")),
    (re.compile(r"<\?"), re.compile(r"\?>")),
    (re.compile(r"<![A-Za-z]"), re.compile(r">")),
    (re.compile(r"<!\[CDATA\["), re.compile(r"\]\]>")),
    (re.compile(rf"</?(?:{HTML_BLOCK_TAGS})(?:[\s/>]|$)", re.IGNORECASE), None),
    (re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>[ \t]*$"), None),
)
# A Markdown inline destination right after "](", and a reference definition.
INLINE_DEST_RE = re.compile(r"\]\([ \t]*(<[^<>\n]*>|[^\s()<>]*(?:\([^\s()<>]*\)[^\s()<>]*)*)(?=[ \t\n)])")
REF_DEF_RE = re.compile(r"^ {0,3}\[(?:[^\]\\\n]|\\.)+\]:[ \t]*(<[^<>\n]*>|\S+)", re.MULTILINE)
FILE_REF_RE = re.compile(r"[A-Za-z0-9._/-]+")
BACKTICKS_RE = re.compile(r"`+")
# Where an inline raw HTML tag, comment, declaration or autolink could start: "<" before a non-space.
TAG_START_RE = re.compile(r"<(?=[^\s<>])")
# CommonMark autolinks (<scheme:...>, <user@host>): links, not raw HTML.
AUTOLINK_RE = re.compile(
    r"<(?:[A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*|[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*)>")
STAGE_DIR = ".convert-stage"
BACKUP_DIR = ".convert-backup"
JOURNAL_NAME = "convert-journal.json"
MAX_FOLDER_ENTRIES = 1000
MAX_JOURNAL_BYTES = 1024 * 1024
JOURNAL_TARGET_KEYS = frozenset({"path", "existed", "original_digest", "new_digest", "staged", "backup"})


class CliError(Exception):
    """A fixed error code, an optional repo-relative path and line, and safe extra fields."""

    def __init__(self, code: str, path: str = "", line: int | None = None, **fields) -> None:
        super().__init__(code)
        self.code = code
        self.path = path
        self.line = line
        self.fields = fields

    def error(self) -> dict:
        error = {"code": self.code, "path": self.path}
        if self.line is not None:
            error["line"] = self.line
        return error


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require_identity(blog: Path, doctor) -> None:
    """Refuse unless the doctor's identity checks pass for this file's path."""
    checks = doctor.identity_checks(
        blog=blog, claude=None, git=None, registry=doctor.default_registry(),
        timeout=IDENTITY_TIMEOUT, script=Path(__file__),
    )
    if any(check["status"] != "ok" for check in checks):
        raise CliError("install_identity", checks=checks)


def engine_version() -> str:
    """This install's version, from its plugin manifest."""
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["version"]


def require_verifier_copy(blog: Path, doctor) -> None:
    """Refuse unless the blog repo's verifier copy matches this install (doctor check i)."""
    try:
        doctor.check_verifier_copy(blog, INSTALL_ROOT, engine_version())
    except (doctor.Fail, doctor.Unknown) as exc:
        raise CliError("verifier_copy", VERIFIER_COPY, reason=str(exc)) from None


def _locations(output: str, blog: Path) -> list[str]:
    """repo-relative path:line pairs from output that name an existing regular file in the blog repo."""
    found: set[str] = set()
    root = str(blog)
    for match in LOCATION_RE.finditer(output):
        path = match["path"]
        if path.startswith(root + "/"):
            path = path[len(root) + 1:]
        parts = Path(path).parts
        if path.startswith("/") or ".." in parts or not parts:
            continue
        target = blog / path
        try:
            refuse_symlinks(blog, path)
        except CliError:
            continue
        if target.is_file():
            found.add(f"{Path(path).as_posix()}:{int(match['line'])}")
    return sorted(found)[:MAX_LOCATIONS]


def run_quiet(command: list[str], blog: Path, timeout: float = COMMAND_TIMEOUT, extra_env: dict | None = None) -> dict:
    """Run an external command in the blog repo and keep only its exit code and repo locations.

    The command's own output never leaves this function: it may hold post
    text, confidential words or URLs.
    """
    try:
        result = subprocess.run(
            command, cwd=blog, capture_output=True, text=True, errors="replace", timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(extra_env or {})},
        )
    except FileNotFoundError:
        raise CliError("command_not_found") from None
    except subprocess.TimeoutExpired:
        raise CliError("command_timed_out") from None
    except OSError:
        raise CliError("command_failed") from None
    return {"exit_code": result.returncode, "locations": _locations(result.stdout + result.stderr, blog)}


def require_git_repo(blog: Path) -> None:
    result = run_quiet([shutil.which("git") or "git", "rev-parse", "--is-inside-work-tree"], blog)
    if result["exit_code"] != 0:
        raise CliError("blog_repo_not_git", **result)
    # Inside some work tree is not enough: the blog repo must be its root.
    if (blog / ".git").is_symlink() or not (blog / ".git").exists():
        raise CliError("blog_repo_not_git_root")


def refuse_symlinks(blog: Path, relative: str) -> None:
    """Refuse a symlink on any component of blog/relative (CWE-59)."""
    current = blog
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise CliError("symlink_refused", current.relative_to(blog).as_posix())


def sync_verifier(blog: Path) -> dict:
    """Atomically replace the blog repo's verifier copy with this install's canonical."""
    refuse_symlinks(blog, VERIFIER_COPY)
    data = VERIFIER_PATH.read_bytes()
    target = blog / VERIFIER_COPY
    temp = target.with_name(f".blogkit_verify.py.{os.getpid()}.tmp")
    try:
        target.parent.mkdir(exist_ok=True)
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    except OSError:
        raise CliError("verifier_copy_write_failed", VERIFIER_COPY) from None
    finally:
        temp.unlink(missing_ok=True)
    return {"copied": VERIFIER_COPY}


class SlugLock:
    """inbox/<slug>/blogkit.lock holding this process id and host.

    The lock is an exclusive flock on that file, taken without waiting, so
    judging and taking over a lock is one step that two runs cannot share. A
    lock left by a process that no longer runs on this host is cleared; a lock
    from another host, a live process or an unreadable lock file is kept and
    the command is refused. Release removes the file only while it is still
    the one this run locked.
    """

    def __init__(self, blog: Path, slug: str, verifier=None) -> None:
        verifier = verifier or _load("blogkit_verify", VERIFIER_PATH)
        self.blog = blog
        self.folder = verifier.inbox_folder(slug)
        self.relative = f"{self.folder}/{LOCK_NAME}"
        self.path = blog / self.relative
        self.held = False
        self.fd: int | None = None

    def __enter__(self) -> "SlugLock":
        refuse_symlinks(self.blog, self.relative)
        if not (self.blog / self.folder).is_dir():
            raise CliError("slug_folder_missing", self.folder)
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0), 0o644)
        except OSError:
            raise CliError("slug_lock_unreadable", self.relative) from None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise CliError("slug_lock_unreadable", self.relative)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise CliError("slug_locked", self.relative) from None
            if not self._same_file(fd):  # released and removed by its holder since we opened it
                raise CliError("slug_locked", self.relative)
            data = os.read(fd, MAX_LOCK_BYTES + 1)
            if data and not self._stale(data):
                raise CliError("slug_locked", self.relative)
            os.ftruncate(fd, 0)
            os.pwrite(fd, json.dumps({"pid": os.getpid(), "host": socket.gethostname()}).encode("utf-8"), 0)
        except BaseException:
            os.close(fd)
            raise
        self.fd, self.held = fd, True
        return self

    def _same_file(self, fd: int) -> bool:
        try:
            here = os.lstat(self.path)
        except OSError:
            return False
        mine = os.fstat(fd)
        return (here.st_dev, here.st_ino) == (mine.st_dev, mine.st_ino)

    def _stale(self, data: bytes) -> bool:
        """Whether the lock content names a process that is gone from this host."""
        try:
            holder = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CliError("slug_lock_unreadable", self.relative) from None
        pid = holder.get("pid") if isinstance(holder, dict) else None
        host = holder.get("host") if isinstance(holder, dict) else None
        # pid 0 and negative pids would signal process groups, never a holder.
        if (len(data) > MAX_LOCK_BYTES or not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
                or not isinstance(host, str)):
            raise CliError("slug_lock_unreadable", self.relative)
        if host != socket.gethostname():
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        return False

    def __exit__(self, *exc) -> None:
        if self.fd is None:
            return
        try:
            if self._same_file(self.fd):
                self.path.unlink(missing_ok=True)
        finally:
            os.close(self.fd)
            self.fd, self.held = None, False


# ---------------------------------------------------------------- convert


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _read(verifier, blog: Path, relative: str, cap: int) -> bytes:
    """A regular file in the blog repo, no symlink components, at most cap bytes."""
    try:
        return verifier._read_capped(blog, relative, cap)
    except verifier.VerifyError as exc:
        raise CliError(exc.code, exc.path) from None


def _draft_text(data: bytes, path: str) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        raise CliError("bom_not_allowed", path)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise CliError("not_utf8", path) from None
    if "\r" in text:
        raise CliError("crlf_not_allowed", path)
    return text


def _scalar(raw: str, path: str, line: int):
    """A JSON or single-quoted string, a JSON list of strings, or a plain scalar."""
    raw = raw.strip()
    try:
        if raw.startswith('"'):
            value = json.loads(raw)
            if isinstance(value, str):
                return value
        elif raw.startswith("["):
            value = json.loads(raw)
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                return value
        elif match := SINGLE_QUOTED_RE.fullmatch(raw):
            return match[1].replace("''", "'")
        elif PLAIN_RE.fullmatch(raw):
            return raw
    except json.JSONDecodeError:
        pass
    raise CliError("draft_frontmatter_grammar", path, line)


def parse_draft_frontmatter(lines: list[str], path: str) -> dict:
    """The draft's frontmatter lines (file line 2 onward) as key -> value, or raise.

    Each entry is `key: value` or `key:` followed by `  - item` lines. Blank
    and comment lines are skipped; any other YAML form is refused.
    """
    values: dict = {}
    index = 0
    while index < len(lines):
        line, number = lines[index], index + 2
        index += 1
        if not line.strip() or line.startswith("#"):
            continue
        match = DRAFT_KEY_RE.fullmatch(line)
        if match is None:
            raise CliError("draft_frontmatter_grammar", path, number)
        key, raw = match.groups()
        if key in values:
            raise CliError("draft_key_duplicate", path, number)
        if key not in DRAFT_KEPT_KEYS and key not in DRAFT_DROPPED_KEYS:
            raise CliError("draft_key_not_allowed", path, number)
        if raw is not None and raw.strip():
            values[key] = _scalar(raw, path, number)
            continue
        items = []
        while index < len(lines) and (item := DRAFT_ITEM_RE.fullmatch(lines[index])):
            value = _scalar(item[1], path, index + 2)
            if not isinstance(value, str):
                raise CliError("draft_frontmatter_grammar", path, index + 2)
            items.append(value)
            index += 1
        values[key] = items
    return values


def _draft_date(verifier, values: dict, key: str, path: str) -> datetime.date:
    value = values[key]
    try:
        if isinstance(value, str) and verifier.DATE_RE.fullmatch(value):
            return datetime.date.fromisoformat(value)
    except ValueError:
        pass
    raise CliError("date_invalid", path, key=key)


def public_frontmatter(verifier, values: dict, path: str) -> str:
    """The public-copy frontmatter (draft: true) for validated draft values."""
    for key in DRAFT_REQUIRED_KEYS:
        if key not in values:
            raise CliError("draft_key_missing", path, key=key)
    for key in ("title", "description"):
        if not isinstance(values[key], str) or not values[key].strip():
            raise CliError("draft_value_invalid", path, key=key)
    if not isinstance(values.get("tags", []), list):
        raise CliError("draft_value_invalid", path, key="tags")
    if len(values["description"]) > MAX_DESCRIPTION:
        raise CliError("description_too_long", path)
    dates = {key: _draft_date(verifier, values, key, path) for key in ("date", "lastUpdated") if key in values}
    if "lastUpdated" in dates and dates["lastUpdated"] < dates["date"]:
        raise CliError("updated_before_date", path)
    lines = [f"title: {_json(values['title'])}", f"description: {_json(values['description'])}",
             f"pubDate: {_json(values['date'])}"]
    if "lastUpdated" in values:
        lines.append(f"updatedDate: {_json(values['lastUpdated'])}")
    if "tags" in values:
        lines.append(f"tags: {_json(values['tags'])}")
    lines.append("draft: true")
    return "---\n" + "\n".join(lines) + "\n---\n"


class _Ahead:
    """Next position of a few fixed strings at or after a cursor that only moves forward (linear in total)."""

    def __init__(self, text: str, stop: int) -> None:
        self.text, self.stop, self.found = text, stop, {}

    def next(self, needle: str, at: int) -> int:
        cached = self.found.get(needle)
        if cached is None or (cached != -1 and cached < at):
            cached = self.text.find(needle, at, self.stop)
            self.found[needle] = cached
        return cached


def _tag_end(text: str, at: int, stop: int, ahead: _Ahead) -> int:
    """End of the stretch that a possible inline tag starting at text[at] == "<" could cover.

    Read generously (any "<" before a non-space starts one): a comment, processing
    instruction or CDATA runs to its closing mark, anything else to the first ">"
    outside quotes, and an unclosed one to stop. Reading too much as a tag only hides less.
    """
    for opener, closer in (("<!--", "-->"), ("<?", "?>"), ("<![CDATA[", "]]>")):
        if text.startswith(opener, at):
            end = ahead.next(closer, at + len(opener))
            return stop if end < 0 else end + len(closer)
    cursor = at + 1
    while True:
        marks = [(ahead.next(mark, cursor), mark) for mark in (">", '"', "'")]
        marks = [(pos, mark) for pos, mark in marks if pos >= 0]
        if not marks:
            return stop
        pos, mark = min(marks)
        if mark == ">":
            return pos + 1
        close = ahead.next(mark, pos + 1)
        if close < 0:
            return stop
        cursor = close + 1


def _inline_code(text: str, start: int, stop: int) -> list[tuple[int, int]]:
    """Code spans in text[start:stop], read left to right as CommonMark does: an inline tag or
    autolink that starts first is taken whole, so a backtick inside it opens nothing."""
    runs = [(m.start(), m.end()) for m in BACKTICKS_RE.finditer(text, start, stop)]
    by_length: dict[int, list[int]] = {}
    for index, (begin, end) in enumerate(runs):
        by_length.setdefault(end - begin, []).append(index)
    ahead = _Ahead(text, stop)
    ranges = []
    pos, index = start, 0
    while index < len(runs):
        begin, end = runs[index]
        if begin < pos:
            index += 1
            continue
        tag = TAG_START_RE.search(text, pos, begin + 1)  # + 1: the lookahead may need the backtick itself
        if tag:
            pos = _tag_end(text, tag.start(), stop, ahead)
            continue
        slashes = 0
        while begin - slashes > start and text[begin - slashes - 1] == "\\":
            slashes += 1
        opener = begin + slashes % 2  # outside code a backslash makes the first backtick a plain character
        same = by_length.get(end - opener, [])
        later = bisect.bisect_right(same, index)
        if opener == end or later == len(same):
            pos, index = end, index + 1
            continue
        closer = same[later]
        ranges.append((opener, runs[closer][1]))
        pos, index = runs[closer][1], closer + 1
    return ranges


def _html_block_start(line: str):
    """The end pattern of the raw HTML block this line starts (None: ends at a blank line), or False."""
    rest = CONTAINER_RE.match(line).end()
    content = line[rest:].lstrip()
    for start, end in HTML_BLOCKS:
        match = start.match(content)
        if match:
            return end
    return False


def _code_ranges(text: str) -> list[tuple[int, int]]:
    """Character ranges of fenced code blocks and inline code spans.

    A raw HTML block (CommonMark start conditions 1 to 7, read generously,
    also inside block quotes and list items) is HTML up to its end condition,
    so backticks in it are not code. Reading too much as HTML only hides less.
    """
    ranges: list[tuple[int, int]] = []
    fence = None
    block = False  # False: not in a raw HTML block; None: one that ends at a blank line; else its end pattern
    segment = pos = 0
    for line in text.splitlines(keepends=True):
        start, pos = pos, pos + len(line)
        stripped = line.rstrip("\n")
        if block is not False:
            if block is None and not stripped[CONTAINER_RE.match(stripped).end():].strip():
                block, segment = False, start
            elif block is not None and block.search(stripped):
                block, segment = False, pos
            continue
        if fence is None:
            opened = _html_block_start(stripped)
            if opened is not False:
                ranges += _inline_code(text, segment, start)
                content = stripped[CONTAINER_RE.match(stripped).end():].lstrip()
                block, segment = opened, pos
                if opened is not None and opened.search(content, 1):
                    block = False
                continue
            match = FENCE_RE.match(stripped)
            if match and not (match[1][0] == "`" and "`" in stripped[match.end():]):
                ranges += _inline_code(text, segment, start)
                fence = (match[1][0], len(match[1]), start)
        elif re.fullmatch(rf" {{0,3}}{re.escape(fence[0])}{{{fence[1]},}}[ \t]*", stripped):
            ranges.append((fence[2], pos))
            fence = None
            segment = pos
    if fence is not None:
        ranges.append((fence[2], len(text)))
    elif block is False:
        ranges += _inline_code(text, segment, len(text))
    return ranges


def _opener(text: str, close: int) -> int | None:
    """Index of the "[" that the "]" at close ends, within one paragraph."""
    depth = 0
    for index in range(close - 1, -1, -1):
        char = text[index]
        if char in "[]" and index > 0 and text[index - 1] == "\\":
            continue
        if char == "]":
            depth += 1
        elif char == "[":
            if depth == 0:
                return index
            depth -= 1
        elif char == "\n" and index > 0 and text[index - 1] == "\n":
            return None
    return None


def _references(body: str) -> list[tuple[int, int, str, bool]]:
    """(start, end, destination, is_image) of each Markdown destination outside code."""
    code = _code_ranges(body)

    def in_code(pos: int) -> bool:
        return any(start <= pos < end for start, end in code)

    found = []
    for match in INLINE_DEST_RE.finditer(body):
        close = match.start()
        if in_code(close) or (close > 0 and body[close - 1] == "\\"):
            continue
        opener = _opener(body, close)
        if opener is not None:
            found.append((match.start(1), match.end(1), match[1], opener > 0 and body[opener - 1] == "!"))
    for match in REF_DEF_RE.finditer(body):
        if not in_code(match.start()):
            found.append((match.start(1), match.end(1), match[1], False))
    return sorted(found)


def _file_reference(dest: str, image: bool, render, path: str, line: int) -> str | None:
    """None for a reference kept as it is, or the draft-relative file it names.

    Order: a backslash or a scheme-less authority is refused (the upstream
    link-safety rule), a scheme or authority makes an external URL that the
    upstream rule must allow, "/" and "#" stay as site URLs and anchors, and
    everything else is a file reference.
    """
    url = dest[1:-1] if dest.startswith("<") else dest
    if not url or "\\" in url or url.startswith("//"):
        raise CliError("reference_unsafe", path, line)
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        raise CliError("reference_unsafe", path, line) from None
    if parsed.scheme or parsed.netloc:
        if not render._is_safe_url(url, media=image):
            raise CliError("reference_scheme_not_allowed", path, line)
        return None
    if url.startswith(("/", "#")):
        return None
    return url


def _asset_source(blog: Path, verifier, draft_folder: str, url: str, path: str, line: int) -> tuple[str, str]:
    """(asset name, repo-relative source) for a file reference inside the draft folder."""
    if not FILE_REF_RE.fullmatch(url):
        raise CliError("reference_file_invalid", path, line)
    normal = posixpath.normpath(url)
    if normal == ".." or normal.startswith(("../", "/")):
        raise CliError("reference_outside_draft", path, line)
    source = f"{draft_folder}/{normal}"
    refuse_symlinks(blog, source)
    name = posixpath.basename(normal)
    if Path(name).suffix.lower() not in verifier.ASSET_TYPES:
        raise CliError("reference_not_image", path, line)
    if not verifier.ASSET_NAME_RE.fullmatch(name) or Path(name).suffix not in verifier.ASSET_TYPES:
        raise CliError("asset_name_invalid", path, line)
    if not (blog / source).is_file():
        raise CliError("reference_missing", path, line)
    return name, source


def _target_state(blog: Path, paths: list[str], folder: str, cap: int) -> dict[str, str | None]:
    """Digest of each target path and of each file in folder (None = absent, "unknown" = not a file).

    folder itself maps to "folder" when it is a real folder.
    """
    state: dict[str, str | None] = {}
    for relative in (*paths, folder):
        refuse_symlinks(blog, relative)
    for relative in paths:
        state[relative] = _file_digest(blog / relative, cap)
    location = blog / folder
    if not os.path.lexists(location):
        state[folder] = None
    elif not location.is_dir():
        state[folder] = "unknown"
    else:
        state[folder] = "folder"
        with os.scandir(location) as entries:
            for count, entry in enumerate(entries, 1):
                if count > MAX_FOLDER_ENTRIES:
                    raise CliError("too_many_files", folder)
                state[f"{folder}/{entry.name}"] = _file_digest(Path(entry.path), cap)
    return state


def _owned_copy(blog: Path, verifier, slug: str, state: dict[str, str | None], absent: list[str]) -> dict | None:
    """The evidence of a public copy that convert may replace, None when there is none yet.

    Refuses a published post, a file or folder without a record, a copy that
    differs from its public_copy record and an asset the record does not own.
    """
    post_path = verifier.post_path_for(slug)
    assets_dir = f"{verifier.ASSETS_DIR}/{slug}"
    evidence_path = verifier.evidence_path_for(slug)
    for relative in absent:
        if state[relative] is not None:
            raise CliError("target_exists", relative)
    if state[post_path] is None:
        for relative, digest in state.items():
            if digest is not None:
                raise CliError("target_exists", relative)
        return None
    post = _read(verifier, blog, post_path, verifier.MAX_POST_BYTES)
    try:
        published = verifier.is_public(post, post_path)
    except verifier.VerifyError:
        published = False
    if published:
        raise CliError("target_published", post_path)
    if state[evidence_path] is None:
        raise CliError("target_exists", post_path)
    try:
        evidence = verifier.read_evidence(blog, slug)
    except verifier.VerifyError:
        raise CliError("evidence_invalid", evidence_path) from None
    record = evidence["public_copy"]
    if (evidence["slug"], evidence["post_path"], record["post_path"]) != (slug, post_path, post_path):
        raise CliError("evidence_invalid", evidence_path)
    if state[post_path] != record["post_digest"]:
        raise CliError("target_modified", post_path)
    owned = {asset["path"]: asset["digest"] for asset in record["assets"]}
    if state[assets_dir] == "unknown":
        raise CliError("asset_unowned", assets_dir)
    for relative in state:
        if relative.startswith(f"{assets_dir}/") and relative not in owned:
            raise CliError("asset_unowned", relative)
    for relative, digest in owned.items():
        if state.get(relative) != digest:
            raise CliError("target_modified", relative)
    return evidence


def _missing_dirs(blog: Path, paths) -> list[str]:
    """Folders (shallowest first) that must be created to hold paths."""
    missing: list[str] = []
    for relative in paths:
        parts = Path(relative).parent.parts
        for depth in range(1, len(parts) + 1):
            folder = "/".join(parts[:depth])
            if folder not in missing and not os.path.lexists(blog / folder):
                missing.append(folder)
    return missing


def _write_new(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _write_journal(path: Path, journal: dict) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.unlink(missing_ok=True)
    _write_new(temp, (json.dumps(journal, indent=2) + "\n").encode("utf-8"))
    os.replace(temp, path)


def _file_digest(path: Path, cap: int) -> str | None:
    """SHA-256 of a regular file, None when nothing is there, "unknown" otherwise."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError:
        return "unknown"
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return "unknown"
        data = handle.read(cap + 1)
    return "unknown" if len(data) > cap else _sha256(data)


def _discard(blog: Path, inbox: str) -> None:
    """Remove the stage, backup and journal of a unit that was not applied or is done."""
    for folder in (STAGE_DIR, BACKUP_DIR):
        if os.path.lexists(blog / inbox / folder):
            shutil.rmtree(blog / inbox / folder)
    (blog / inbox / f"{JOURNAL_NAME}.tmp").unlink(missing_ok=True)
    (blog / inbox / JOURNAL_NAME).unlink(missing_ok=True)


def _roll_back(blog: Path, journal: dict, cap: int) -> bool:
    """Undo a unit that was not marked applied, judging by the files that are there.

    Nothing is changed when a target holds a file the journal does not know.
    """
    targets = journal["targets"]
    digests = [_file_digest(blog / target["path"], cap) for target in targets]
    for digest, target in zip(digests, targets):
        original, new = target["original_digest"], target["new_digest"]
        backup = _file_digest(blog / target["backup"], cap)
        if digest not in (None, original, new) or backup not in (None, original):
            return False
        if _file_digest(blog / target["staged"], cap) not in (None, new):
            return False
        if target["existed"] and digest != original and backup != original:
            return False
    try:
        for digest, target in zip(reversed(digests), reversed(targets)):
            here = blog / target["path"]
            if digest is not None and digest == target["new_digest"] != target["original_digest"]:
                if os.path.lexists(blog / target["staged"]):
                    here.unlink()
                else:
                    os.replace(here, blog / target["staged"])
            if target["existed"] and not os.path.lexists(here):
                os.replace(blog / target["backup"], here)
        for folder in reversed(journal["created_dirs"]):
            if os.path.lexists(blog / folder):
                os.rmdir(blog / folder)
    except OSError:
        return False
    return True


def apply_unit(blog: Path, inbox: str, changes: dict[str, bytes | None], before: dict[str, str | None],
               recheck, cap: int) -> None:
    """Put new files in place, and remove old ones (None), as one unit.

    Stage every new file under inbox/<slug>/, write the journal (per target:
    path, whether it existed, original and new digest, staged and backup
    location, plus the folders to create and an applied flag), call recheck
    (it raises when a target moved on since the start), move each existing
    target to its backup and each staged file into place, mark the journal
    applied, then remove the stage, backups and journal. A handled error
    while moving rolls the unit back on the spot.
    """
    stage = f"{inbox}/{STAGE_DIR}"
    journal_path = blog / inbox / JOURNAL_NAME
    refuse_symlinks(blog, stage)
    refuse_symlinks(blog, f"{inbox}/{JOURNAL_NAME}")
    if os.path.lexists(blog / stage) or os.path.lexists(journal_path):
        raise CliError("convert_leftover", inbox)
    refuse_symlinks(blog, f"{inbox}/{BACKUP_DIR}")
    if os.path.lexists(blog / inbox / BACKUP_DIR):
        raise CliError("convert_leftover", inbox)
    targets = [
        {"path": path, "existed": before.get(path) is not None, "original_digest": before.get(path),
         "new_digest": None if data is None else _sha256(data),
         "staged": f"{stage}/{index}", "backup": f"{inbox}/{BACKUP_DIR}/{index}"}
        for index, (path, data) in enumerate(changes.items())
    ]
    created = _missing_dirs(blog, [path for path, data in changes.items() if data is not None])
    journal = {"journal": 1, "applied": False, "created_dirs": created, "targets": targets}
    try:
        os.mkdir(blog / stage)
        os.mkdir(blog / inbox / BACKUP_DIR)
        for target, data in zip(targets, changes.values()):
            if data is not None:
                _write_new(blog / target["staged"], data)
        _write_journal(journal_path, journal)
    except OSError:
        _discard(blog, inbox)
        raise CliError("stage_failed", stage) from None
    try:
        recheck()
    except CliError as exc:
        _discard(blog, inbox)
        raise CliError("target_changed", exc.path) from None
    try:
        for folder in journal["created_dirs"]:
            os.mkdir(blog / folder)
        for target in targets:
            if target["existed"]:
                os.replace(blog / target["path"], blog / target["backup"])
            if target["new_digest"] is not None:
                os.replace(blog / target["staged"], blog / target["path"])
    except OSError:
        if not _roll_back(blog, journal, cap):
            raise CliError("apply_failed", f"{inbox}/{JOURNAL_NAME}", rolled_back=False) from None
        _discard(blog, inbox)
        raise CliError("apply_failed", rolled_back=True) from None
    journal["applied"] = True
    try:
        _write_journal(journal_path, journal)
        _discard(blog, inbox)
    except OSError:
        raise CliError("cleanup_failed", f"{inbox}/{JOURNAL_NAME}") from None


def _leftovers(inbox: str) -> list[str]:
    return [f"{inbox}/{STAGE_DIR}", f"{inbox}/{BACKUP_DIR}", f"{inbox}/{JOURNAL_NAME}.tmp", f"{inbox}/{JOURNAL_NAME}"]


def interrupted(blog: Path, inbox: str) -> bool:
    """Whether a convert unit was left behind (stage, backup or journal)."""
    for relative in _leftovers(inbox):
        refuse_symlinks(blog, relative)
    return any(os.path.lexists(blog / relative) for relative in _leftovers(inbox))


def _ancestors(relative: str) -> set[str]:
    parts = Path(relative).parts
    return {"/".join(parts[:depth]) for depth in range(1, len(parts))}


def _read_journal(blog: Path, verifier, slug: str) -> dict:
    """The journal of an interrupted unit, refused unless every path is one convert itself would use."""
    inbox = verifier.inbox_folder(slug)
    relative = f"{inbox}/{JOURNAL_NAME}"
    try:
        journal = json.loads(_read(verifier, blog, relative, MAX_JOURNAL_BYTES).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CliError("journal_invalid", relative) from None
    assets_dir = f"{verifier.ASSETS_DIR}/{slug}"
    fixed = {verifier.post_path_for(slug), verifier.evidence_path_for(slug)}
    folders = set().union(*map(_ancestors, (*fixed, f"{assets_dir}/x")))

    def target_ok(index: int, target) -> bool:
        if not isinstance(target, dict) or set(target) != JOURNAL_TARGET_KEYS:
            return False
        name = target["path"].removeprefix(f"{assets_dir}/") if isinstance(target["path"], str) else None
        return (
            (target["path"] in fixed or (name != target["path"] and bool(verifier.ASSET_NAME_RE.fullmatch(name))))
            and isinstance(target["existed"], bool)
            and all(digest is None or (isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest))
                    for digest in (target["original_digest"], target["new_digest"]))
            and target["existed"] == (target["original_digest"] is not None)
            and target["staged"] == f"{inbox}/{STAGE_DIR}/{index}"
            and target["backup"] == f"{inbox}/{BACKUP_DIR}/{index}"
        )

    if not (
        isinstance(journal, dict) and set(journal) == {"journal", "applied", "created_dirs", "targets"}
        and journal["journal"] == 1 and isinstance(journal["applied"], bool)
        and isinstance(journal["created_dirs"], list) and all(folder in folders for folder in journal["created_dirs"])
        and isinstance(journal["targets"], list)
        and all(target_ok(index, target) for index, target in enumerate(journal["targets"]))
        and len({target["path"] for target in journal["targets"]}) == len(journal["targets"])
    ):
        raise CliError("journal_invalid", relative)
    for target in journal["targets"]:
        for key in ("path", "staged", "backup"):
            refuse_symlinks(blog, target[key])
    return journal


def _only_journal_files(blog: Path, inbox: str, journal: dict) -> bool:
    """Whether the stage and backup folders hold nothing but files the journal names."""
    known = {target[key] for target in journal["targets"] for key in ("staged", "backup")}
    for folder in (STAGE_DIR, BACKUP_DIR):
        path = blog / inbox / folder
        if not os.path.lexists(path):
            continue
        if path.is_symlink() or not path.is_dir():
            return False
        with os.scandir(path) as entries:
            for count, entry in enumerate(entries):
                if count >= MAX_FOLDER_ENTRIES or f"{inbox}/{folder}/{entry.name}" not in known:
                    return False
                if not entry.is_file(follow_symlinks=False):
                    return False
    return True


def recover(blog: Path, slug: str, verifier) -> str | None:
    """Finish an interrupted convert unit by the files that are there.

    Before the applied mark every target goes back to its original state;
    after it the new state stays and only cleanup is left. A lost original, a
    file the journal does not know (at a target or anywhere in the stage and
    backup folders) stops recovery with nothing changed.
    """
    inbox = verifier.inbox_folder(slug)
    if not interrupted(blog, inbox):
        return None
    blocked = CliError("convert_recovery_blocked", f"{inbox}/{JOURNAL_NAME}")
    if not os.path.lexists(blog / inbox / JOURNAL_NAME):
        # Stopped before the journal was in place: no target was moved yet.
        backup = blog / inbox / BACKUP_DIR
        if os.path.lexists(backup) and (not backup.is_dir() or any(backup.iterdir())):
            raise blocked
        _discard(blog, inbox)
        return "discarded"
    journal = _read_journal(blog, verifier, slug)
    if not _only_journal_files(blog, inbox, journal):
        raise blocked
    if journal["applied"]:
        _discard(blog, inbox)
        return "cleaned_up"
    if not _roll_back(blog, journal, verifier.MAX_ASSET_BYTES):
        raise blocked
    _discard(blog, inbox)
    return "rolled_back"


def convert(blog: Path, slug: str, verifier, render) -> dict:
    """Create or replace the public copy (draft: true), its assets and evidence."""
    inbox = verifier.inbox_folder(slug)
    draft_folder = verifier.draft_folder_for(slug)
    draft_path = verifier.draft_path_for(slug)
    post_path = verifier.post_path_for(slug)
    assets_dir = f"{verifier.ASSETS_DIR}/{slug}"
    evidence_path = verifier.evidence_path_for(slug)
    absent = [f"{verifier.POSTS_DIR}/{slug}.mdx", f"{verifier.POSTS_DIR}/{slug}"]

    def target_state() -> dict[str, str | None]:
        return _target_state(blog, [post_path, *absent, evidence_path], assets_dir, verifier.MAX_ASSET_BYTES)

    before = target_state()
    old = _owned_copy(blog, verifier, slug, before, absent)

    text = _draft_text(_read(verifier, blog, draft_path, verifier.MAX_POST_BYTES), draft_path)
    marker = render._PLACEHOLDER_RE.search(text)
    if marker:
        raise CliError("marker_left", draft_path, text.count("\n", 0, marker.start()) + 1)
    lines = text.split("\n")
    if lines[0] != "---":
        raise CliError("frontmatter_missing", draft_path)
    if "---" not in lines[1:]:
        raise CliError("frontmatter_unclosed", draft_path)
    close = lines.index("---", 1)
    header = public_frontmatter(verifier, parse_draft_frontmatter(lines[1:close], draft_path), draft_path)
    body = "\n".join(lines[close + 1:])
    first_body_line = close + 2

    asset_link = posixpath.relpath(assets_dir, verifier.POSTS_DIR)
    sources: dict[str, str] = {}
    pieces: list[str] = []
    last = 0
    for start, end, dest, image in _references(body):
        line = first_body_line + body.count("\n", 0, start)
        url = _file_reference(dest, image, render, draft_path, line)
        if url is None:
            continue
        name, source = _asset_source(blog, verifier, draft_folder, url, draft_path, line)
        if sources.setdefault(name, source) != source:
            raise CliError("asset_name_collision", draft_path, line)
        pieces += [body[last:start], f"{asset_link}/{name}"]
        last = end
    post = (header + "".join(pieces) + body[last:]).encode("utf-8")
    if len(post) > verifier.MAX_POST_BYTES:
        raise CliError("file_too_large", post_path)

    assets: dict[str, bytes] = {}
    for name, source in sorted(sources.items()):
        assets[f"{assets_dir}/{name}"] = _read(verifier, blog, source, verifier.MAX_ASSET_BYTES)
        if sum(map(len, assets.values())) > verifier.MAX_ASSETS_TOTAL:
            raise CliError("assets_too_large", draft_folder)
    version = engine_version()
    try:
        verifier.parse_public_frontmatter(post, post_path)
        content = verifier.hash_parts(post_path, post, assets)
        kept = old is not None and old["content_hash"] == content
        evidence = {
            "contract": 1, "slug": slug, "post_path": post_path,
            "hash_algo": verifier.HASH_ALGO, "content_hash": content,
            "engine_version": old["engine_version"] if kept else version,
            "public_copy": {
                "post_path": post_path, "post_digest": _sha256(post),
                "assets": [{"path": path, "digest": _sha256(data)} for path, data in assets.items()],
                "engine_version": version,
            },
            "records": old["records"] if kept else [],
        }
        verifier.validate_evidence(evidence)
    except verifier.VerifyError:
        raise CliError("convert_output_invalid", post_path) from None
    files: dict[str, bytes | None] = {
        **assets, post_path: post,
        evidence_path: (json.dumps(evidence, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    }
    stale = [path for path in before if path.startswith(f"{assets_dir}/") and path not in files]
    changes = {path: data for path, data in files.items() if before.get(path) != _sha256(data)}
    changes.update(dict.fromkeys(stale))

    def recheck() -> None:
        now = target_state()
        for relative in sorted(set(before) | set(now)):
            if before.get(relative) != now.get(relative):
                raise CliError("target_changed", relative)

    if changes:
        apply_unit(blog, inbox, changes, before, recheck, verifier.MAX_ASSET_BYTES)
    return {"post_path": post_path, "assets": list(assets), "evidence": evidence_path, "content_hash": content}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="blog-kit engine CLI for an Astro blog repo.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in SLUG_COMMANDS:
        command = commands.add_parser(name)
        command.add_argument("slug")
        command.add_argument("--blog-repo", default=None, help="Blog repo folder (default: current folder)")
        if name == "approve":
            command.add_argument("--hash", required=True, help="Full content hash shown by status")
            command.add_argument("--warnings", required=True, help="Warning set digest shown by status")
    sync = commands.add_parser("sync-verifier")
    sync.add_argument("--blog-repo", default=None, help="Blog repo folder (default: current folder)")
    return parser


def run(args: argparse.Namespace, blog: Path) -> dict:
    if Path(__file__).resolve().is_relative_to(blog):
        raise CliError("engine_inside_blog_repo")
    doctor = _load("blogkit_doctor", DOCTOR_PATH)
    require_identity(blog, doctor)
    if args.command == "sync-verifier":
        return sync_verifier(blog)
    require_verifier_copy(blog, doctor)
    require_git_repo(blog)
    verifier = _load("blogkit_verify", VERIFIER_PATH)
    if not verifier.valid_slug(args.slug):
        raise CliError("slug_invalid")
    if args.command in LOCK_COMMANDS:
        with SlugLock(blog, args.slug, verifier):
            return dispatch(args, blog, verifier)
    return dispatch(args, blog, verifier)


# ---------------------------------------------------------------- publish gates and status


def _error(code: str, path: str = "", line: int | None = None) -> dict:
    return CliError(code, path, line).error()


def _gate(number: int, errors: list[dict], warnings=()) -> dict:
    return {"gate": number, "passed": not errors, "errors": errors, "warnings": sorted(set(warnings))}


def _load_evidence(blog: Path, verifier, slug: str) -> dict:
    relative = verifier.evidence_path_for(slug)
    refuse_symlinks(blog, relative)
    if not os.path.lexists(blog / relative):
        raise CliError("evidence_missing", relative)
    try:
        evidence = verifier.read_evidence(blog, slug)
    except verifier.VerifyError:
        raise CliError("evidence_invalid", relative) from None
    if (evidence["slug"], evidence["post_path"]) != (slug, verifier.post_path_for(slug)):
        raise CliError("evidence_invalid", relative)
    return evidence


def _current_hash(blog: Path, verifier, slug: str) -> str:
    try:
        return verifier.content_hash(blog, slug)
    except verifier.VerifyError as exc:
        raise CliError(exc.code, exc.path) from None


def _save_evidence(blog: Path, verifier, slug: str, evidence: dict) -> None:
    """Replace the evidence file atomically after checking it against the contract."""
    try:
        verifier.validate_evidence(evidence)
    except verifier.VerifyError:
        raise CliError("evidence_invalid", verifier.evidence_path_for(slug)) from None
    path = blog / verifier.evidence_path_for(slug)
    temp = path.with_name(path.name + ".tmp")
    refuse_symlinks(blog, verifier.evidence_path_for(slug) + ".tmp")
    try:
        temp.unlink(missing_ok=True)
        _write_new(temp, (json.dumps(evidence, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        os.replace(temp, path)
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise CliError("evidence_write_failed", verifier.evidence_path_for(slug)) from None


def gate_1(blog: Path, doctor) -> dict:
    """Tools: Python 3.11+, node and npx on PATH, the git root, the verifier copy, the term list."""
    errors = []
    if sys.version_info < (3, 11):
        errors.append(_error("python_too_old"))
    for tool in ("node", "npx"):
        if shutil.which(tool) is None:
            errors.append(_error(f"{tool}_missing"))
    for check in (lambda: require_git_repo(blog), lambda: require_verifier_copy(blog, doctor)):
        try:
            check()
        except CliError as exc:
            errors.append(_error(exc.code, exc.path))
    try:
        confidential = _load("blogkit_confidential", CONFIDENTIAL_PATH)
        try:
            confidential.load_terms()
        except confidential.Blocked as exc:
            errors.append(_error(f"confidential_{exc.code}"))
    except Exception:  # noqa: BLE001 - the term list could not be checked; never print why
        errors.append(_error("confidential_check_failed"))
    return _gate(1, errors)


class _HtmlCheck(HTMLParser):
    """Report what the upstream renderer's allow-list would strip, instead of stripping it.

    Tags, attributes and href/src schemes come from scripts/blog_render.py.
    Comments are allowed; declarations, CDATA and processing instructions are
    not (an SVG file may start with an XML declaration).
    """

    def __init__(self, render, on_file, svg_file: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.render, self.on_file, self.svg_file = render, on_file, svg_file
        self.found: list[tuple[str, int]] = []
        self.urls: list[tuple[str, int, bool]] = []

    def _report(self, code: str) -> None:
        self.found.append((code, self.getpos()[0]))

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag not in self.render.ALLOWED_TAGS:
            self._report("html_tag_not_allowed")
            return
        allowed = self.render.ALLOWED_ATTRS.get(tag, frozenset()) | self.render.GLOBAL_ATTRS
        for raw_name, value in attrs:
            name = raw_name.lower()
            if name.startswith("on") or name not in allowed or value is None:
                self._report("html_attribute_not_allowed")
            elif name in ("href", "src"):
                if not self.render._is_safe_url(value, media=name == "src"):
                    self._report("html_url_not_allowed")
                else:
                    self.urls.append((value.strip(), self.getpos()[0], name == "src"))
                    self.on_file(value.strip(), self.getpos()[0])

    handle_startendtag = handle_starttag

    def handle_decl(self, decl: str) -> None:
        self._report("html_declaration_not_allowed")

    def unknown_decl(self, data: str) -> None:
        self._report("html_declaration_not_allowed")

    def handle_pi(self, data: str) -> None:
        if not (self.svg_file and self.getpos() == (1, 0) and data.startswith("xml ")):
            self._report("html_declaration_not_allowed")


def _blank(text: str, ranges) -> str:
    """text with each range replaced by spaces, newlines kept so line numbers stay."""
    chars = list(text)
    for start, end in ranges:
        for index in range(start, end):
            if chars[index] != "\n":
                chars[index] = " "
    return "".join(chars)


def _post_parts(post: bytes) -> tuple[str, list, str, list[tuple[str, int]]]:
    """The post body with its frontmatter turned into blank lines, its Markdown references, the
    same text with code, autolinks and reference destinations blanked (what is left of raw HTML),
    and the URL and line of each scheme autolink outside code (e-mail autolinks are left out).

    Offsets and line numbers match the post file.
    """
    lines = post.decode("utf-8").split("\n")
    close = lines.index("---", 1)
    masked = "\n" * (close + 1) + "\n".join(lines[close + 1:])
    references = _references(masked)
    code = _code_ranges(masked)
    found = list(AUTOLINK_RE.finditer(_blank(masked, code)))
    autolinks = [(m[0][1:-1], masked.count("\n", 0, m.start()) + 1) for m in found if "@" not in m[0].split(":")[0]]
    hidden = code + [m.span() for m in found] + [r[:2] for r in references]
    return masked, references, _blank(masked, hidden), autolinks


def gate_2(blog: Path, slug: str, verifier, render, post: bytes, assets: dict[str, bytes]) -> dict:
    """Body safety: raw HTML and inline SVG outside code, SVG asset files, link and image URLs.

    Violations are reported, never fixed. A file reference must name an
    image of this post's asset folder. Asset name, type, size and symlink
    rules were already enforced while reading the assets.
    """
    post_path = verifier.post_path_for(slug)
    errors: list[dict] = []

    def file_reference(base: str, path: str):
        def check(url: str, line: int) -> None:
            if url.startswith(("/", "#")) or urllib.parse.urlparse(url).scheme:
                return
            if posixpath.normpath(posixpath.join(posixpath.dirname(base), url)) not in assets:
                errors.append(_error("reference_not_in_assets", path, line))
        return check

    masked, references, blanked, autolinks = _post_parts(post)
    in_post = file_reference(post_path, post_path)
    for url, line in autolinks:
        if not render._is_safe_url(url, media=False):
            errors.append(_error("reference_scheme_not_allowed", post_path, line))
    for start, _end, dest, image in references:
        line = masked.count("\n", 0, start) + 1
        try:
            url = _file_reference(dest, image, render, post_path, line)
        except CliError as exc:
            errors.append(exc.error())
            continue
        if url is not None:
            in_post(url, line)
    parsers = [(post_path, blanked, _HtmlCheck(render, in_post))]
    for path, data in assets.items():
        if path.endswith(".svg"):
            try:
                svg = data.decode("utf-8")
            except UnicodeDecodeError:
                errors.append(_error("asset_not_utf8", path))
                continue
            parsers.append((path, svg, _HtmlCheck(render, file_reference(path, path), svg_file=True)))
    for path, source, parser in parsers:
        parser.feed(source)
        parser.close()
        errors += [_error(code, path, line) for code, line in parser.found]
    return _gate(2, sorted(errors, key=lambda e: (e["path"] != post_path, e["path"], e.get("line", 0))))


def gate_3(blog: Path) -> dict:
    """astro check in the blog repo, judged by its exit code; only repo locations of its output are kept."""
    try:
        result = run_quiet([shutil.which("npx") or "npx", "--no-install", "astro", "check"], blog,
                           timeout=ASTRO_CHECK_TIMEOUT)
    except CliError as exc:
        return _gate(3, [_error(exc.code)])
    if result["exit_code"] == 0:
        return _gate(3, [])
    errors = [_error("astro_check_failed")]
    for location in result["locations"]:
        path, _, line = location.rpartition(":")
        errors.append(_error("astro_check_location", path, int(line)))
    return _gate(3, errors)


def gate_4(evidence: dict, current: str) -> dict:
    """A review record for the current hash with no P0 finding (written by the M2c reviewer)."""
    reviews = [r for r in evidence["records"] if r["kind"] == "review" and r["content_hash"] == current]
    if not reviews:
        return _gate(4, [_error("review_missing")])
    return _gate(4, [] if reviews[0]["p0"] == 0 else [_error("review_has_p0")])


def _warning_id(gate: int, category: str, path: str, line: int | None, url: str | None = None) -> str:
    """<gate>:<category>:<file>:<line>[:<URL digest>], never the text that was found."""
    return f"{gate}:{category}:{path}:{line if line is not None else ''}" + (f":{url}" if url else "")


def gate_5(blog: Path, slug: str) -> dict:
    """The confidential check over the post and its asset files: a term blocks, a heuristic warns."""
    try:
        confidential = _load("blogkit_confidential", CONFIDENTIAL_PATH)
        result = confidential.scan_files(blog, confidential.slug_files(blog, slug))
    except Exception:  # noqa: BLE001 - could not check; never print why
        return _gate(5, [_error("confidential_check_failed")])
    if result["list"] != "ok":
        return _gate(5, [_error(f"confidential_{result['list']}")])
    errors = [_error("confidential_term", b["file"], b["line"]) for b in result["blocks"]]
    errors += [_error(f"confidential_{e['code']}", e["file"]) for e in result["errors"]]
    warnings = [_warning_id(5, w["category"], w["file"], w["line"]) for w in result["warnings"]]
    return _gate(5, errors, warnings)


class _PageScan(HTMLParser):
    """Element ids and img src values of one built page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "img" and values.get("src"):
            self.images.append(values["src"].strip())

    handle_startendtag = handle_starttag


def _built_file(out: Path, path: str) -> str | None:
    """The preview file a site path names (directory index, the file itself, or <path>.html)."""
    path = urllib.parse.unquote(path).lstrip("/")
    if ".." in Path(path).parts:
        return None
    candidates = [f"{path}index.html"] if path == "" or path.endswith("/") else [path, f"{path}/index.html",
                                                                                  f"{path}.html"]
    for candidate in candidates:
        try:
            refuse_symlinks(out, candidate)
        except CliError:
            return None
        if (out / candidate).is_file():
            return candidate
    return None


def _page_scan(verifier, out: Path, relative: str) -> _PageScan:
    scan = _PageScan()
    scan.feed(verifier._read_capped(out, relative, verifier.MAX_PAGE_BYTES).decode("utf-8", "replace"))
    scan.close()
    return scan


def gate_6(blog: Path, slug: str, verifier, render, post: bytes) -> dict:
    """The draft preview build into its own git-ignored folder, then the post page, its local images,
    the post's site links and anchors, and its external URLs through the link check module."""
    post_path = verifier.post_path_for(slug)
    try:
        refuse_symlinks(blog, PREVIEW_DIR)
        ignored = run_quiet([shutil.which("git") or "git", "check-ignore", "-q", f"{PREVIEW_DIR}/"], blog)
        if ignored["exit_code"] != 0:
            return _gate(6, [_error("preview_not_ignored", PREVIEW_DIR)])
        if os.path.lexists(blog / PREVIEW_DIR):
            if not (blog / PREVIEW_DIR).is_dir():
                return _gate(6, [_error("preview_not_a_folder", PREVIEW_DIR)])
            shutil.rmtree(blog / PREVIEW_DIR)
        built = run_quiet([shutil.which("npx") or "npx", "--no-install", "astro", "build", "--outDir", PREVIEW_DIR],
                          blog, timeout=PREVIEW_TIMEOUT, extra_env={verifier.PREVIEW_VAR: "1"})
    except CliError as exc:
        return _gate(6, [_error(exc.code, exc.path)])
    if built["exit_code"] != 0:
        errors = [_error("preview_build_failed")]
        for location in built["locations"]:
            path, _, line = location.rpartition(":")
            errors.append(_error("preview_build_location", path, int(line)))
        return _gate(6, errors)
    out = blog / PREVIEW_DIR
    page_path = f"posts/{slug}/index.html"
    try:
        page = _page_scan(verifier, out, page_path)
    except verifier.VerifyError:
        return _gate(6, [_error("preview_page_missing", post_path)])
    errors: list[dict] = []
    warnings: list[str] = []
    for src in page.images:
        target = urllib.parse.urljoin(f"/posts/{slug}/", src)
        parts = urllib.parse.urlsplit(target)
        if not parts.scheme and not parts.netloc and _built_file(out, parts.path) is None:
            errors.append(_error("image_missing", post_path))
    links = _load("blogkit_links", LINKS_PATH)
    try:
        allowed = links.load_exceptions(blog / verifier.draft_folder_for(slug))
    except links.LinkError as exc:
        return _gate(6, [_error(exc.code, verifier.draft_folder_for(slug), exc.line)])
    masked, references, blanked, autolinks = _post_parts(post)
    html = _HtmlCheck(render, lambda url, line: None)
    html.feed(blanked)
    html.close()
    urls = [((dest[1:-1] if dest.startswith("<") else dest), masked.count("\n", 0, start) + 1)
            for start, _end, dest, _image in references] + [(url, line) for url, line, _media in html.urls] + autolinks
    results: dict[str, dict] = {}
    pages: dict[str, set[str] | None] = {}
    for url, line in sorted(urls, key=lambda item: item[1]):
        try:
            parts = urllib.parse.urlsplit(url)
        except ValueError:
            continue  # gate 2 reports it
        if parts.scheme in ("http", "https") or parts.netloc:
            result = results.setdefault(url, links.check_url(url, allowed=allowed))
            if result["result"] == "blocked":
                errors.append({**_error(f"link_{result['kind']}", post_path, line), "url": result["url"]})
            elif result["result"] == "warning":
                warnings.append(_warning_id(6, "link_transient", post_path, line, result["url"]))
        elif url.startswith("#"):
            if urllib.parse.unquote(url[1:]) not in page.ids:
                errors.append(_error("anchor_broken", post_path, line))
        elif url.startswith("/"):
            target = _built_file(out, parts.path)
            if target is None:
                errors.append(_error("internal_link_broken", post_path, line))
            elif parts.fragment:
                if target not in pages:
                    try:
                        pages[target] = _page_scan(verifier, out, target).ids
                    except verifier.VerifyError:
                        pages[target] = None
                if pages[target] is None or urllib.parse.unquote(parts.fragment) not in pages[target]:
                    errors.append(_error("anchor_broken", post_path, line))
    return _gate(6, errors, warnings)


def check(blog: Path, slug: str, verifier, render, doctor) -> dict:
    """Run gates 1 to 4 for the public copy and record them in its evidence.

    The content hash is fixed at the start and computed again before the
    record is written; a change in between drops the results. Evidence for
    another hash starts its records afresh (public_copy is kept).
    """
    evidence = _load_evidence(blog, verifier, slug)
    current = _current_hash(blog, verifier, slug)
    post_path = verifier.post_path_for(slug)
    post = _read(verifier, blog, post_path, verifier.MAX_POST_BYTES)
    try:
        assets = verifier.read_assets(blog, slug)
    except verifier.VerifyError as exc:
        raise CliError(exc.code, exc.path) from None
    version = engine_version()
    if evidence["content_hash"] != current:
        evidence.update(content_hash=current, engine_version=version, records=[])
    gates = [gate_1(blog, doctor), gate_2(blog, slug, verifier, render, post, assets), gate_3(blog),
             gate_4(evidence, current), gate_5(blog, slug), gate_6(blog, slug, verifier, render, post)]
    if _current_hash(blog, verifier, slug) != current:
        raise CliError("content_changed_during_check", post_path)
    numbers = {gate["gate"] for gate in gates}
    kept = [r for r in evidence["records"] if not (r["kind"] == "gate" and r["gate"] in numbers)]
    fresh = [{"kind": "gate", "gate": g["gate"], "passed": g["passed"], "warnings": g["warnings"],
              "content_hash": current, "engine_version": version} for g in gates]
    gate_records = sorted([r for r in kept if r["kind"] == "gate"] + fresh, key=lambda r: r["gate"])
    evidence["records"] = gate_records + [r for r in kept if r["kind"] != "gate"]
    _save_evidence(blog, verifier, slug, evidence)
    result = {"content_hash": current, "gates": gates, "recorded": True}
    if not all(gate["passed"] for gate in gates):
        raise CliError("gates_failed", **result)
    return result


def status(blog: Path, slug: str, verifier) -> dict:
    """Current hash, evidence hash, gate results, warnings and their digest, and what is still missing."""
    evidence = _load_evidence(blog, verifier, slug)
    current = _current_hash(blog, verifier, slug)
    gates: dict[str, dict] = {}
    if evidence["content_hash"] == current:
        for record in evidence["records"]:
            if record["kind"] == "gate" and record["content_hash"] == current:
                gates[str(record["gate"])] = {"passed": record["passed"], "warnings": record["warnings"]}
    warnings = sorted({w for gate in gates.values() for w in gate["warnings"]})
    return {"content_hash": current, "evidence_hash": evidence["content_hash"], "gates": gates,
            "warnings": warnings, "warnings_digest": verifier.warnings_digest(warnings),
            "missing": verifier.evidence_gaps(evidence, current)}


def _gate_warnings(evidence: dict) -> list[str]:
    return sorted({w for r in evidence["records"] if r["kind"] == "gate" for w in r["warnings"]})


def approve(blog: Path, slug: str, verifier, content_hash: str, warnings: str) -> dict:
    """Record the person's approval of exactly this hash and warning set.

    Every gate (1 to 6), the review and the learning check must already pass
    for the current hash; only the approval itself may be missing or stale.
    """
    evidence = _load_evidence(blog, verifier, slug)
    current = _current_hash(blog, verifier, slug)
    if content_hash != current:
        raise CliError("hash_mismatch")
    gaps = [gap for gap in verifier.evidence_gaps(evidence, current)
            if gap not in ("approval_missing", "approval_warnings_mismatch")]
    if gaps:
        raise CliError("evidence_incomplete", missing=gaps)
    digest = verifier.warnings_digest(_gate_warnings(evidence))
    if warnings != digest:
        raise CliError("warnings_mismatch")
    evidence["records"] = [r for r in evidence["records"] if r["kind"] != "approval"] + [
        {"kind": "approval", "warnings_digest": digest, "content_hash": current, "engine_version": engine_version()}]
    if _current_hash(blog, verifier, slug) != current:
        raise CliError("content_changed_during_check", verifier.post_path_for(slug))
    _save_evidence(blog, verifier, slug, evidence)
    return {"content_hash": current, "warnings_digest": digest, "approved": True}


def _git_output(blog: Path, *args: str) -> tuple[int, str]:
    """git in the blog repo for the engine's own decisions; its output is parsed here, never printed."""
    try:
        result = subprocess.run([shutil.which("git") or "git", *args], cwd=blog, capture_output=True, text=True,
                                errors="replace", timeout=COMMAND_TIMEOUT, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.TimeoutExpired):
        raise CliError("git_failed") from None
    return result.returncode, result.stdout


def _assets_in_head(blog: Path, verifier, slug: str) -> dict[str, str]:
    """Blob id of each file HEAD holds in the post's asset folder (empty without HEAD)."""
    code, listing = _git_output(blog, "ls-tree", "-r", "-z", "HEAD", "--", f"{verifier.ASSETS_DIR}/{slug}")
    if code != 0:
        return {}
    entries = {}
    for entry in filter(None, listing.split("\0")):
        meta, _, path = entry.partition("\t")
        entries[path] = meta.split()[-1]
    return entries


def _assets_on_disk(blog: Path, verifier, slug: str) -> dict[str, str]:
    """Blob id git would store for each file in the post's asset folder (git hash-object, filters applied).

    Compared with HEAD it catches an added, changed or deleted file, and a
    change that skip-worktree or assume-unchanged hides from git status.
    """
    try:
        paths = sorted(verifier.read_assets(blog, slug))
    except verifier.VerifyError as exc:
        raise CliError(exc.code, exc.path) from None
    if not paths:
        return {}
    code, ids = _git_output(blog, "hash-object", "--", *paths)
    if code != 0 or len(ids.split()) != len(paths):
        raise CliError("git_failed")
    return dict(zip(paths, ids.split()))


def _flipped(verifier, data: bytes, post_path: str) -> bytes:
    """The post with its single frontmatter "draft" line set to "draft: false" (unchanged if already false)."""
    try:
        start, stop = verifier._frontmatter_bounds(data)
    except verifier.VerifyError:
        raise CliError("draft_line_count", post_path) from None
    lines = data[start:stop].split(b"\n")
    hits = [i for i, line in enumerate(lines) if verifier.DRAFT_LINE_RE.fullmatch(line)]
    if len(hits) != 1:
        raise CliError("draft_line_count", post_path)
    lines[hits[0]] = b"draft: false"
    return data[:start] + b"\n".join(lines) + data[stop:]


def _swap_post(blog: Path, verifier, post_path: str, checked: bytes, data: bytes) -> None:
    """Put data in place of the post only while the post still holds the checked bytes.

    The post is moved aside first and compared there, and the new file is
    linked in, which never replaces a file that appeared meanwhile. An edit
    made before the swap is put back and publishing stops; nothing a person
    wrote is overwritten.
    """
    path = blog / post_path
    temp = path.with_name(f".{path.name}.publish-tmp")
    old = path.with_name(f".{path.name}.publish-old")
    old_relative = f"{verifier.POSTS_DIR}/{old.name}"
    for name in (temp.name, old.name):
        refuse_symlinks(blog, f"{verifier.POSTS_DIR}/{name}")
    if os.path.lexists(old):
        raise CliError("publish_backup_left", old_relative)
    temp.unlink(missing_ok=True)
    _write_new(temp, data)
    try:
        os.rename(path, old)
        if _read(verifier, blog, old_relative, verifier.MAX_POST_BYTES) != checked:
            os.link(old, path)
            old.unlink()
            raise CliError("content_changed_during_check", post_path)
        try:
            os.link(temp, path)
        except FileExistsError:
            old.unlink()
            raise CliError("content_changed_during_check", post_path) from None
        except OSError:
            os.link(old, path)  # the post goes back where it was
            old.unlink()
            raise CliError("publish_swap_failed", post_path) from None
        if _read(verifier, blog, old_relative, verifier.MAX_POST_BYTES) != checked:
            raise CliError("content_changed_during_check", old_relative)
        old.unlink()
    finally:
        temp.unlink(missing_ok=True)


def _recover_swap(blog: Path, verifier, post_path: str) -> None:
    """Finish a post swap that a stopped run left behind, judging by the files there.

    With the post moved aside and nothing in its place, it is put back. When
    the post already holds the moved-aside bytes (a put-back that stopped
    before its cleanup) or their flipped copy (a swap that stopped before its
    cleanup), the leftover goes. Anything else is kept and refused.
    """
    path = blog / post_path
    old = path.with_name(f".{path.name}.publish-old")
    old_relative = f"{verifier.POSTS_DIR}/{old.name}"
    refuse_symlinks(blog, old_relative)
    if not os.path.lexists(old):
        return
    moved = _read(verifier, blog, old_relative, verifier.MAX_POST_BYTES)
    if not os.path.lexists(path):
        os.link(old, path)
        old.unlink()
        return
    current = _read(verifier, blog, post_path, verifier.MAX_POST_BYTES)
    try:
        flipped = _flipped(verifier, moved, post_path)
    except CliError:
        raise CliError("publish_backup_left", old_relative) from None
    if current in (moved, flipped):
        old.unlink()
        return
    raise CliError("publish_backup_left", old_relative)


def _sync_index(blog: Path, blobs: dict[str, str]) -> None:
    """Point the real index at the committed blobs of these paths (other entries untouched)."""
    for path, blob in blobs.items():
        if _git_output(blog, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}")[0] != 0:
            raise CliError("index_update_failed", path)


def _git_blob(blog: Path, data: bytes) -> str:
    """Write data into the blog repo's object store as a blob; its id."""
    try:
        result = subprocess.run([shutil.which("git") or "git", "hash-object", "-w", "--stdin"], cwd=blog, input=data,
                                capture_output=True, timeout=COMMAND_TIMEOUT, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.TimeoutExpired):
        raise CliError("git_failed") from None
    if result.returncode != 0:
        raise CliError("git_failed")
    return result.stdout.decode("ascii", "replace").strip()


def _head_blob(blog: Path, path: str) -> str | None:
    code, out = _git_output(blog, "rev-parse", "--verify", "-q", f"HEAD:{path}")
    return out.strip() if code == 0 else None


def _staged_paths(blog: Path) -> set[str]:
    code, staged = _git_output(blog, "diff", "--cached", "--name-only", "-z")
    if code != 0:
        raise CliError("git_failed")
    return set(filter(None, staged.split("\0")))


def _git_env(blog: Path, index: Path | None = None, *args: str) -> tuple[int, str]:
    """git with GIT_INDEX_FILE pointing at index (hooks run with it too); output parsed, never printed."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **({"GIT_INDEX_FILE": str(index)} if index else {})}
    try:
        result = subprocess.run([shutil.which("git") or "git", *args], cwd=blog, capture_output=True, text=True,
                                errors="replace", timeout=COMMAND_TIMEOUT, env=env)
    except (OSError, subprocess.TimeoutExpired):
        raise CliError("git_failed") from None
    return result.returncode, result.stdout


def _commit_blobs(blog: Path, blobs: dict[str, str], message: str, post_path: str) -> None:
    """Commit HEAD plus exactly these path -> blob entries, with the repo's commit hooks.

    The commit is built in a private index in a folder of this run's own
    under the git folder: pre-commit and commit-msg run
    against it, and it is refused (HEAD untouched) when a hook staged anything
    else or changed the blobs. HEAD moves only if it did not move meanwhile;
    the real index then gets the same two entries, and post-commit runs.
    """
    code, git_dir = _git_output(blog, "rev-parse", "--git-dir")
    if code != 0:
        raise CliError("git_failed")
    work = Path(tempfile.mkdtemp(prefix="blogkit-publish-", dir=blog / git_dir.strip()))  # this run's own files
    index, message_file = work / "index", work / "message"
    code, head = _git_output(blog, "rev-parse", "--verify", "-q", "HEAD")
    head = head.strip() if code == 0 else ""
    try:
        message_file.write_text(message, encoding="utf-8")
        steps = [("read-tree", head) if head else ("read-tree", "--empty")]
        steps += [("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}") for path, blob in blobs.items()]
        for args in steps:
            if _git_env(blog, index, *args)[0] != 0:
                raise CliError("git_failed")
        for hook in (("pre-commit",), ("commit-msg", "--", str(message_file))):
            if _git_env(blog, index, "hook", "run", "--ignore-missing", *hook)[0] != 0:
                raise CliError("commit_failed", post_path)
        base = head or _empty_tree(blog)
        code, changed = _git_env(blog, index, "diff-index", "--cached", "--name-only", "-z", base)
        code_files, listing = _git_env(blog, index, "ls-files", "-s", "-z", "--", *blobs)
        staged = {entry.split("\t", 1)[1]: entry.split()[1] for entry in filter(None, listing.split("\0"))}
        if code or code_files or set(filter(None, changed.split("\0"))) - set(blobs) or staged != blobs:
            raise CliError("other_changes_staged")
        code, tree = _git_env(blog, index, "write-tree")
        if code != 0:
            raise CliError("git_failed")
        parents = ["-p", head] if head else []
        code, commit = _git_output(blog, "commit-tree", tree.strip(), *parents, "-F", str(message_file))
        if code != 0:
            raise CliError("commit_failed", post_path)
        if _git_output(blog, "update-ref", "-m", message.splitlines()[0] if message.strip() else "publish",
                       "HEAD", commit.strip(), head)[0] != 0:
            raise CliError("commit_failed", post_path)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    _sync_index(blog, blobs)
    _git_output(blog, "hook", "run", "--ignore-missing", "post-commit")


def _empty_tree(blog: Path) -> str:
    """The empty tree id in this repo's hash format."""
    try:
        result = subprocess.run([shutil.which("git") or "git", "hash-object", "-t", "tree", "--stdin"], cwd=blog,
                                input=b"", capture_output=True, timeout=COMMAND_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        raise CliError("git_failed") from None
    return result.stdout.decode("ascii", "replace").strip()


def publish(blog: Path, slug: str, verifier) -> dict:
    """Flip the draft line and commit the post and its evidence, and nothing else, as "publish: <slug>".

    Refused unless the evidence is complete for the current hash, every file
    of the post's asset folder is in HEAD with the same bytes (and HEAD has
    no other file there), and nothing but those two paths is staged. The
    commit holds exactly the bytes that were checked: they are written as
    blobs and committed from a private index (see _commit_blobs), never read
    again from the work tree, and HEAD is compared with them afterwards. The
    post is swapped only while it still holds those bytes. A run after a
    failed commit finds draft: false and complete evidence and only commits;
    it never resets anything.
    """
    post_path = verifier.post_path_for(slug)
    evidence_path = verifier.evidence_path_for(slug)
    assets_dir = f"{verifier.ASSETS_DIR}/{slug}"
    _recover_swap(blog, verifier, post_path)
    post = _read(verifier, blog, post_path, verifier.MAX_POST_BYTES)
    current = _current_hash(blog, verifier, slug)
    evidence = _load_evidence(blog, verifier, slug)
    record = _read(verifier, blog, evidence_path, verifier.MAX_EVIDENCE_BYTES)
    gaps = verifier.evidence_gaps(evidence, current)
    if gaps:
        raise CliError("evidence_incomplete", missing=gaps)
    if _assets_in_head(blog, verifier, slug) != _assets_on_disk(blog, verifier, slug):
        raise CliError("assets_not_committed", assets_dir)
    if _staged_paths(blog) - {post_path, evidence_path}:
        raise CliError("other_changes_staged")
    if (_current_hash(blog, verifier, slug) != current
            or _read(verifier, blog, post_path, verifier.MAX_POST_BYTES) != post
            or _read(verifier, blog, evidence_path, verifier.MAX_EVIDENCE_BYTES) != record
            or verifier.read_evidence(blog, slug) != evidence):
        raise CliError("content_changed_during_check", post_path)
    flipped = _flipped(verifier, post, post_path)
    resumed = flipped == post
    if not resumed:
        _swap_post(blog, verifier, post_path, post, flipped)
    blobs = {post_path: _git_blob(blog, flipped), evidence_path: _git_blob(blog, record)}
    if resumed and all(_head_blob(blog, path) == blob for path, blob in blobs.items()):
        _sync_index(blog, blobs)  # a run stopped after moving HEAD may have left the index behind
        code, head = _git_output(blog, "rev-parse", "HEAD")
        return {"published": True, "commit": head.strip(), "resumed": True, "already_published": True}
    _commit_blobs(blog, blobs, f"publish: {slug}\n", post_path)
    code, changed = _git_output(blog, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "--root", "HEAD")
    if (code != 0 or set(filter(None, changed.split("\0"))) - set(blobs)
            or any(_head_blob(blog, path) != blob for path, blob in blobs.items())):
        raise CliError("published_commit_mismatch", post_path)
    code, head = _git_output(blog, "rev-parse", "HEAD")
    return {"published": True, "commit": head.strip(), "resumed": resumed, "already_published": False}


def dispatch(args: argparse.Namespace, blog: Path, verifier) -> dict:
    inbox = verifier.inbox_folder(args.slug)
    if args.command == "convert":
        recovered = recover(blog, args.slug, verifier)
        result = convert(blog, args.slug, verifier, _load("blog_render", RENDER_PATH))
        return {**result, "recovered": recovered} if recovered else result
    if interrupted(blog, inbox):
        raise CliError("convert_interrupted", f"{inbox}/{JOURNAL_NAME}")
    if args.command == "check":
        return check(blog, args.slug, verifier, _load("blog_render", RENDER_PATH), _load("blogkit_doctor", DOCTOR_PATH))
    if args.command == "status":
        return status(blog, args.slug, verifier)
    if args.command == "approve":
        return approve(blog, args.slug, verifier, args.hash, args.warnings)
    if args.command == "publish":
        return publish(blog, args.slug, verifier)
    raise CliError("not_implemented")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.blog_repo and Path(args.blog_repo).is_symlink():
        print(json.dumps({"status": "fail", "errors": [{"code": "blog_repo_is_symlink", "path": ""}]}, indent=2))
        return 1
    blog = Path(args.blog_repo or Path.cwd()).resolve()
    report: dict = {"status": "fail", "command": args.command}
    try:
        report.update(run(args, blog), status="ok", errors=[])
    except CliError as exc:
        report.update(exc.fields, errors=[exc.error()])
    except OSError:  # a file operation outside the handled cases; its message names local paths
        report.update(errors=[{"code": "io_failed", "path": ""}])
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
