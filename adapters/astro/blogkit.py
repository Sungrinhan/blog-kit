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
(convert, check, approve, publish) hold inbox/<slug>/blogkit.lock, which
records the process id and host; only a lock left by a dead process on this
host is cleared.

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

Output holds fixed error codes, check ids and reasons, versions,
repo-relative paths and line numbers only. It never prints post text, external command output
or URLs.

Usage:
    python3 blogkit.py COMMAND [SLUG] [--blog-repo PATH]
"""

from __future__ import annotations

import argparse
import datetime
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
import urllib.parse
from pathlib import Path

INSTALL_ROOT = Path(__file__).resolve().parent.parent.parent
DOCTOR_PATH = INSTALL_ROOT / "scripts" / "doctor.py"
VERIFIER_PATH = INSTALL_ROOT / "adapters" / "astro" / "blogkit_verify.py"
MANIFEST_PATH = INSTALL_ROOT / ".claude-plugin" / "plugin.json"
RENDER_PATH = INSTALL_ROOT / "scripts" / "blog_render.py"
VERIFIER_COPY = "scripts/blogkit_verify.py"
IDENTITY_TIMEOUT = 20.0
COMMAND_TIMEOUT = 60.0
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
# A Markdown inline destination right after "](", and a reference definition.
INLINE_DEST_RE = re.compile(r"\]\([ \t]*(<[^<>\n]*>|[^\s()<>]*(?:\([^\s()<>]*\)[^\s()<>]*)*)(?=[ \t\n)])")
REF_DEF_RE = re.compile(r"^ {0,3}\[(?:[^\]\\\n]|\\.)+\]:[ \t]*(<[^<>\n]*>|\S+)", re.MULTILINE)
FILE_REF_RE = re.compile(r"[A-Za-z0-9._/-]+")
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


def run_quiet(command: list[str], blog: Path, timeout: float = COMMAND_TIMEOUT) -> dict:
    """Run an external command in the blog repo and keep only its exit code and repo locations.

    The command's own output never leaves this function: it may hold post
    text, confidential words or URLs.
    """
    try:
        result = subprocess.run(
            command, cwd=blog, capture_output=True, text=True, errors="replace", timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
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

    A lock left by a process that no longer runs on this host is cleared; a
    lock from another host, a live process or an unreadable lock file is kept
    and the command is refused.
    """

    def __init__(self, blog: Path, slug: str) -> None:
        self.blog = blog
        self.folder = f"inbox/{slug}"
        self.relative = f"{self.folder}/{LOCK_NAME}"
        self.path = blog / self.relative
        self.held = False

    def __enter__(self) -> "SlugLock":
        refuse_symlinks(self.blog, self.relative)
        if not (self.blog / self.folder).is_dir():
            raise CliError("slug_folder_missing", self.folder)
        for attempt in (1, 2):
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
            except FileExistsError:
                if attempt == 1 and self._stale():
                    self.path.unlink(missing_ok=True)
                    continue
                raise CliError("slug_locked", self.relative) from None
            except OSError:
                raise CliError("slug_lock_unreadable", self.relative) from None
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "host": socket.gethostname()}, handle)
            self.held = True
            return self
        raise CliError("slug_locked", self.relative)

    def _stale(self) -> bool:
        """Whether the existing lock belongs to a process that is gone from this host."""
        try:
            fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as handle:
                data = handle.read(MAX_LOCK_BYTES + 1)
            holder = json.loads(data.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
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
        if self.held:
            self.path.unlink(missing_ok=True)


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


def _inline_code(text: str, start: int, stop: int) -> list[tuple[int, int]]:
    runs = list(re.finditer(r"`+", text[start:stop]))
    ranges = []
    index = 0
    while index < len(runs):
        opener = runs[index]
        closer = next((j for j in range(index + 1, len(runs)) if len(runs[j][0]) == len(opener[0])), None)
        if closer is None:
            index += 1
            continue
        ranges.append((start + opener.start(), start + runs[closer].end()))
        index = closer + 1
    return ranges


def _code_ranges(text: str) -> list[tuple[int, int]]:
    """Character ranges of fenced code blocks and inline code spans."""
    ranges: list[tuple[int, int]] = []
    fence = None
    segment = pos = 0
    for line in text.splitlines(keepends=True):
        start, pos = pos, pos + len(line)
        stripped = line.rstrip("\n")
        if fence is None:
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
    else:
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
    evidence_path = f"{verifier.INBOX_DIR}/{slug}/{verifier.EVIDENCE_NAME}"
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
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
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
    inbox = f"{verifier.INBOX_DIR}/{slug}"
    relative = f"{inbox}/{JOURNAL_NAME}"
    try:
        journal = json.loads(_read(verifier, blog, relative, MAX_JOURNAL_BYTES).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CliError("journal_invalid", relative) from None
    assets_dir = f"{verifier.ASSETS_DIR}/{slug}"
    fixed = {verifier.post_path_for(slug), f"{inbox}/{verifier.EVIDENCE_NAME}"}
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


def recover(blog: Path, slug: str, verifier) -> str | None:
    """Finish an interrupted convert unit by the files that are there.

    Before the applied mark every target goes back to its original state;
    after it the new state stays and only cleanup is left. A lost original or
    a file the journal does not know stops recovery with nothing changed.
    """
    inbox = f"{verifier.INBOX_DIR}/{slug}"
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
    if journal["applied"]:
        _discard(blog, inbox)
        return "cleaned_up"
    if not _roll_back(blog, journal, verifier.MAX_ASSET_BYTES):
        raise blocked
    _discard(blog, inbox)
    return "rolled_back"


def convert(blog: Path, slug: str, verifier, render) -> dict:
    """Create or replace the public copy (draft: true), its assets and evidence."""
    inbox = f"{verifier.INBOX_DIR}/{slug}"
    draft_folder = f"{inbox}/draft"
    draft_path = f"{draft_folder}/{slug}.md"
    post_path = verifier.post_path_for(slug)
    assets_dir = f"{verifier.ASSETS_DIR}/{slug}"
    evidence_path = f"{inbox}/{verifier.EVIDENCE_NAME}"
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
    if not verifier.SLUG_RE.fullmatch(args.slug) or len(args.slug) > verifier.MAX_SLUG:
        raise CliError("slug_invalid")
    if args.command in LOCK_COMMANDS:
        with SlugLock(blog, args.slug):
            return dispatch(args, blog, verifier)
    return dispatch(args, blog, verifier)


def dispatch(args: argparse.Namespace, blog: Path, verifier) -> dict:
    inbox = f"{verifier.INBOX_DIR}/{args.slug}"
    if args.command == "convert":
        recovered = recover(blog, args.slug, verifier)
        result = convert(blog, args.slug, verifier, _load("blog_render", RENDER_PATH))
        return {**result, "recovered": recovered} if recovered else result
    if interrupted(blog, inbox):
        raise CliError("convert_interrupted", f"{inbox}/{JOURNAL_NAME}")
    raise CliError("not_implemented")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.blog_repo and Path(args.blog_repo).is_symlink():
        print(json.dumps({"status": "error", "errors": [{"code": "blog_repo_is_symlink", "path": ""}]}, indent=2))
        return 2
    blog = Path(args.blog_repo or Path.cwd()).resolve()
    report: dict = {"status": "fail", "command": args.command}
    try:
        report.update(run(args, blog), status="ok", errors=[])
    except CliError as exc:
        report.update(exc.fields, errors=[exc.error()])
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
