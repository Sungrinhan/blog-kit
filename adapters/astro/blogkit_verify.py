#!/usr/bin/env python3
"""blog-kit publish verifier for an Astro blog repo (stdlib only).

The engine owns this file. The blog repo keeps a byte-identical copy and runs
it before every build and again right before every deploy, so a post can only
go public with complete publish evidence bound to its exact content hash.

Source mode (default) walks every .md and .mdx under src/content/posts. A post
is public when its frontmatter says draft: false. For each public post it
checks:

- the post is src/content/posts/<slug>.md (no .mdx, no subfolder)
- the frontmatter follows the public-copy grammar: UTF-8, no BOM, LF, a
  "---" line on both sides, one "key: <JSON value>" line per key, keys from
  title, description, pubDate, updatedDate, tags and draft, and draft as a
  bare true or false
- its post-only assets (src/assets/posts/<slug>/) are flat regular files
  with lowercase names and allowed types, within the size caps
- inbox/<slug>/publish-evidence.json follows a supported contract and
  carries every record (publish gates 1 to 6 passed, review with zero P0,
  learning check passed, approval matching the warning set) for the post's
  current content hash

A file outside the grammar counts as a draft only when its frontmatter has
exactly one top-level "draft: true" line and no other draft-shaped key line.
Anything else fails, because the verifier cannot tell whether Astro would
publish it. BLOGKIT_INCLUDE_DRAFTS (the local preview switch) fails source
mode whenever it is set, since every build that runs this check writes the
deployable output.

Deploy output mode (--dist FOLDER) runs source mode, then checks the built
site the deploy command is about to upload:

- no symlink on the folder (or on any component of its path) or
  anywhere inside it, only regular files and folders, at most 20000 entries,
  HTML and XML files at most 5MB each
- the home page, posts/index.html, rss.xml, sitemap-index.xml and every
  sub-sitemap it lists exist, even with no public posts
- post pages are posts/<slug>/index.html only, and their slugs are exactly
  the public posts that passed source mode
- every URL in rss.xml and the sitemaps shares the origin of the first
  sitemap-index URL, maps to <path>/index.html inside the folder, and, when it
  is a post URL, names a verified public post; sub-sitemaps are root
  sitemap-<number>.xml files followed one level deep; XML that is not
  UTF-8 or has a DOCTYPE or entity declaration is refused
- no inbox/ or .blogkit/ folder, evidence file, review, state, claims or
  approval record, or .md/.mdx draft source ships

--ci is the strict mode for CI and WORKERS_CI deploys: the preview switch
fails even when set to an empty or "0" value.

Content hash (blogkit-content-v1, SHA-256): a fixed header, then the post and
each asset as (kind, path length and bytes, content length and bytes), the
post first and the assets sorted by the UTF-8 bytes of their repo-relative
paths. The post bytes have their single frontmatter "draft: true|false" line
replaced by "draft: <omitted>", so publishing (flipping that line) keeps the
hash. tests/fixtures/blogkit/hash-vectors.json pins the exact bytes.

The file checks its own digest on every run (SELF_DIGEST below holds the
SHA-256 of this file with that value replaced by zeros), so a hand-edited copy
fails (the value is read from the file bytes, so a stale bytecode cache cannot
mask an edit). Symlinks anywhere on the checked paths are refused, and every read is
size-capped. Output is one JSON object of statuses, error codes and
repo-relative paths. It never prints post text, URLs or evidence contents.

Usage:
    python3 blogkit_verify.py [--repo PATH]
    python3 blogkit_verify.py --ci --dist dist
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlsplit

VERIFIER_VERSION = "2.3.1-kit.2"
SELF_DIGEST = {"sha256": "eb44cc61993a70911ab1d59a7e18b8cbd8ad9e99af2f3a0553a1b73c2343ebec"}

HASH_ALGO = "blogkit-content-v1"
HASH_HEADER = b"blogkit-content-v1\n"
SUPPORTED_CONTRACTS = frozenset({1})
POSTS_DIR = "src/content/posts"
ASSETS_DIR = "src/assets/posts"
INBOX_DIR = "inbox"
EVIDENCE_NAME = "publish-evidence.json"
PREVIEW_VAR = "BLOGKIT_INCLUDE_DRAFTS"
DIST_PREFIX = "dist:"
REQUIRED_PAGES = ("index.html", "posts/index.html", "rss.xml")
PRIVATE_FOLDERS = frozenset({INBOX_DIR, ".blogkit"})
PRIVATE_NAMES = frozenset({EVIDENCE_NAME, "state.md", "review.md", "claims.json", "approval.json"})
SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
SUB_SITEMAP_RE = re.compile(r"sitemap-[0-9]+\.xml")
DOCTYPE_RE = re.compile(rb"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)
XML_ENCODING_RE = re.compile(rb"""\A(?:\xef\xbb\xbf)?<\?xml[^>]*?\bencoding\s*=\s*["']([^"']*)["']""")

MAX_POST_BYTES = 1024 * 1024
MAX_ASSET_BYTES = 10 * 1024 * 1024
MAX_ASSETS_TOTAL = 50 * 1024 * 1024
MAX_EVIDENCE_BYTES = 1024 * 1024
MAX_SELF_BYTES = 1024 * 1024
MAX_ENTRIES = 20000
MAX_PAGE_BYTES = 5 * 1024 * 1024

SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_SLUG = 80
ASSET_NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
ASSET_TYPES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".svg"})
HEX64_RE = re.compile(r"[0-9a-f]{64}")
VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
DIGEST_LINE_RE = re.compile(rb'^SELF_DIGEST = \{"sha256": "[0-9a-f]{64}"\}$', re.MULTILINE)
DRAFT_LINE_RE = re.compile(rb"^draft: (?:true|false)$")
GRAMMAR_LINE_RE = re.compile(r"(title|description|pubDate|updatedDate|tags|draft): (.*)")
DRAFT_SHAPED_RE = re.compile(r"""^(?:["']?draft["']?\s*:|\?|<<\s*:)""")
REQUIRED_KEYS = ("title", "description", "pubDate", "draft")

GATES = (1, 2, 3, 4, 5, 6)
TOP_KEYS = frozenset(
    {"contract", "slug", "post_path", "hash_algo", "content_hash", "engine_version", "public_copy", "records"}
)
PUBLIC_COPY_KEYS = frozenset({"post_path", "post_digest", "assets", "engine_version"})
RECORD_KEYS = {
    "gate": frozenset({"kind", "gate", "passed", "warnings", "content_hash", "engine_version"}),
    "review": frozenset({"kind", "p0", "content_hash", "engine_version"}),
    "learning": frozenset({"kind", "passed", "content_hash", "engine_version"}),
    "approval": frozenset({"kind", "warnings_digest", "content_hash", "engine_version"}),
}


class VerifyError(Exception):
    """A check failed. The message is a fixed code; the path is repo-relative."""

    def __init__(self, code: str, path: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.path = path


# ---------------------------------------------------------------- reading


def _no_symlink_components(root: Path, relative: str) -> None:
    """Refuse a symlink on any component of root/relative (CWE-59)."""
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise VerifyError("symlink_refused", current.relative_to(root).as_posix())


def _read_capped(root: Path, relative: str, cap: int) -> bytes:
    """Read a regular file under root without following symlinks or reading past cap."""
    _no_symlink_components(root, relative)
    path = root / relative
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        raise VerifyError("missing_file", relative) from None
    except OSError:
        raise VerifyError("unreadable_file", relative) from None
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise VerifyError("not_a_regular_file", relative)
        data = handle.read(cap + 1)
    if len(data) > cap:
        raise VerifyError("file_too_large", relative)
    return data


# ---------------------------------------------------------------- hashing


def _frontmatter_bounds(data: bytes) -> tuple[int, int]:
    """Byte offsets of the frontmatter body (between the two '---' lines)."""
    if not data.startswith(b"---\n"):
        raise VerifyError("frontmatter_missing")
    start = 4
    pos = start
    while True:
        end = data.find(b"\n", pos)
        line = data[pos:] if end == -1 else data[pos:end]
        if line == b"---":
            return start, pos
        if end == -1:
            raise VerifyError("frontmatter_unclosed")
        pos = end + 1


def hashed_post_bytes(data: bytes) -> bytes:
    """The post bytes with the single frontmatter draft line replaced."""
    start, stop = _frontmatter_bounds(data)
    lines = data[start:stop].split(b"\n")
    hits = [i for i, line in enumerate(lines) if DRAFT_LINE_RE.fullmatch(line)]
    if len(hits) != 1:
        raise VerifyError("draft_line_count")
    lines[hits[0]] = b"draft: <omitted>"
    return data[:start] + b"\n".join(lines) + data[stop:]


def _item(kind: bytes, path: str, data: bytes) -> bytes:
    raw = path.encode("utf-8")
    return b"".join((kind, b"\n", str(len(raw)).encode(), b"\n", raw, b"\n",
                     str(len(data)).encode(), b"\n", data, b"\n"))


def hash_parts(post_path: str, post: bytes, assets: dict[str, bytes]) -> str:
    """blogkit-content-v1 over already-read bytes (post path and asset paths repo-relative)."""
    digest = hashlib.sha256(HASH_HEADER)
    digest.update(_item(b"post", post_path, hashed_post_bytes(post)))
    for path in sorted(assets, key=lambda p: p.encode("utf-8")):
        digest.update(_item(b"asset", path, assets[path]))
    return digest.hexdigest()


def read_assets(root: Path, slug: str) -> dict[str, bytes]:
    """The post's asset files (repo-relative path -> bytes), enforcing the asset rules."""
    folder = f"{ASSETS_DIR}/{slug}"
    _no_symlink_components(root, folder)
    path = root / folder
    if not path.exists():
        return {}
    if not path.is_dir():
        raise VerifyError("asset_folder_not_a_folder", folder)
    assets: dict[str, bytes] = {}
    total = 0
    for entry in sorted(os.scandir(path), key=lambda e: e.name):
        relative = f"{folder}/{entry.name}"
        if entry.is_symlink():
            raise VerifyError("symlink_refused", relative)
        if not entry.is_file(follow_symlinks=False):
            raise VerifyError("asset_not_a_regular_file", relative)
        if not ASSET_NAME_RE.fullmatch(entry.name):
            raise VerifyError("asset_name_invalid", relative)
        if Path(entry.name).suffix not in ASSET_TYPES:
            raise VerifyError("asset_type_not_allowed", relative)
        data = _read_capped(root, relative, MAX_ASSET_BYTES)
        total += len(data)
        if total > MAX_ASSETS_TOTAL:
            raise VerifyError("assets_too_large", folder)
        assets[relative] = data
    return assets


def post_path_for(slug: str) -> str:
    return f"{POSTS_DIR}/{slug}.md"


def content_hash(root: Path, slug: str) -> str:
    """The current content hash of a public copy in the blog repo."""
    post_path = post_path_for(slug)
    return hash_parts(post_path, _read_capped(root, post_path, MAX_POST_BYTES), read_assets(root, slug))


# ---------------------------------------------------------------- frontmatter


def _frontmatter_text(data: bytes, relative: str) -> list[str]:
    if data.startswith(b"\xef\xbb\xbf"):
        raise VerifyError("bom_not_allowed", relative)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise VerifyError("not_utf8", relative) from None
    try:
        start, stop = _frontmatter_bounds(data)
    except VerifyError as exc:
        raise VerifyError(exc.code, relative) from None
    # The body ends with the newline before the closing "---" line.
    return data[start:stop - 1].decode("utf-8").split("\n") if stop > start else []


def parse_public_frontmatter(data: bytes, relative: str) -> dict:
    """Parse frontmatter that follows the public-copy grammar, or raise."""
    if b"\r" in data:
        raise VerifyError("crlf_not_allowed", relative)
    values: dict = {}
    for line in _frontmatter_text(data, relative):
        match = GRAMMAR_LINE_RE.fullmatch(line)
        if match is None:
            raise VerifyError("frontmatter_grammar", relative)
        key, raw = match.groups()
        if key in values:
            raise VerifyError("frontmatter_duplicate_key", relative)
        if key == "draft":
            if raw not in ("true", "false"):
                raise VerifyError("frontmatter_grammar", relative)
            values[key] = raw == "true"
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            raise VerifyError("frontmatter_grammar", relative) from None
        if key == "tags":
            ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
        elif key in ("pubDate", "updatedDate"):
            ok = isinstance(value, str) and DATE_RE.fullmatch(value) is not None
        else:
            ok = isinstance(value, str)
        if not ok:
            raise VerifyError("frontmatter_grammar", relative)
        values[key] = value
    if any(key not in values for key in REQUIRED_KEYS):
        raise VerifyError("frontmatter_missing_key", relative)
    return values


def is_public(data: bytes, relative: str) -> bool:
    """Whether Astro would publish this post (draft: false); raise when that is unclear."""
    try:
        return parse_public_frontmatter(data, relative)["draft"] is False
    except VerifyError:
        pass
    # Outside the grammar: a draft only with one exact "draft: true" line.
    lines = _frontmatter_text(data, relative)
    if lines.count("draft: true") == 1:
        others = [line for line in lines if line != "draft: true" and DRAFT_SHAPED_RE.match(line)]
        if not others:
            return False
    raise VerifyError("draft_status_unclear", relative)


# ---------------------------------------------------------------- evidence


def warnings_digest(warning_ids) -> str:
    """SHA-256 of the sorted, de-duplicated warning ids joined by newlines."""
    return hashlib.sha256("\n".join(sorted(set(warning_ids))).encode("utf-8")).hexdigest()


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _hex64(value) -> bool:
    return isinstance(value, str) and HEX64_RE.fullmatch(value) is not None


def _version(value) -> bool:
    return isinstance(value, str) and VERSION_RE.fullmatch(value) is not None


def validate_evidence(evidence) -> None:
    """Raise unless the evidence object follows a supported contract exactly."""
    if not isinstance(evidence, dict):
        raise VerifyError("evidence_not_an_object")
    contract = evidence.get("contract")
    if not _is_int(contract):
        raise VerifyError("evidence_contract_invalid")
    if contract not in SUPPORTED_CONTRACTS:
        raise VerifyError("evidence_contract_unsupported")
    if set(evidence) != TOP_KEYS:
        raise VerifyError("evidence_fields")
    if not (isinstance(evidence["slug"], str) and isinstance(evidence["post_path"], str)):
        raise VerifyError("evidence_field_type")
    if evidence["hash_algo"] != HASH_ALGO:
        raise VerifyError("evidence_hash_algo")
    if not _hex64(evidence["content_hash"]) or not _version(evidence["engine_version"]):
        raise VerifyError("evidence_field_type")
    copy = evidence["public_copy"]
    if not isinstance(copy, dict) or set(copy) != PUBLIC_COPY_KEYS:
        raise VerifyError("evidence_public_copy")
    assets = copy["assets"]
    if not (
        isinstance(copy["post_path"], str) and _hex64(copy["post_digest"]) and _version(copy["engine_version"])
        and isinstance(assets, list)
        and all(isinstance(a, dict) and set(a) == {"path", "digest"} and isinstance(a["path"], str)
                and _hex64(a["digest"]) for a in assets)
    ):
        raise VerifyError("evidence_public_copy")
    records = evidence["records"]
    if not isinstance(records, list):
        raise VerifyError("evidence_records")
    seen: set = set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("kind"), str) or record["kind"] not in RECORD_KEYS:
            raise VerifyError("evidence_record_kind")
        kind = record["kind"]
        if set(record) != RECORD_KEYS[kind]:
            raise VerifyError("evidence_record_fields")
        if not _hex64(record["content_hash"]) or not _version(record["engine_version"]):
            raise VerifyError("evidence_record_type")
        if kind == "gate":
            ok = (_is_int(record["gate"]) and record["gate"] in GATES and isinstance(record["passed"], bool)
                  and isinstance(record["warnings"], list) and all(isinstance(w, str) for w in record["warnings"]))
            key = ("gate", record["gate"]) if ok else None
        elif kind == "review":
            ok, key = _is_int(record["p0"]) and record["p0"] >= 0, ("review",)
        elif kind == "learning":
            ok, key = isinstance(record["passed"], bool), ("learning",)
        else:
            ok, key = _hex64(record["warnings_digest"]), ("approval",)
        if not ok:
            raise VerifyError("evidence_record_type")
        if key in seen:
            raise VerifyError("evidence_duplicate_record")
        seen.add(key)


def evidence_gaps(evidence: dict, current_hash: str) -> list[str]:
    """What a structurally valid evidence object still lacks for current_hash (empty when complete)."""
    gaps: list[str] = []
    if evidence["content_hash"] != current_hash:
        return ["content_hash_mismatch"]
    records = evidence["records"]
    if any(r["content_hash"] != current_hash for r in records):
        gaps.append("record_hash_mismatch")
    gates = {r["gate"]: r for r in records if r["kind"] == "gate"}
    for number in GATES:
        if number not in gates:
            gaps.append(f"gate_{number}_missing")
        elif gates[number]["passed"] is not True:
            gaps.append(f"gate_{number}_not_passed")
    by_kind = {r["kind"]: r for r in records if r["kind"] != "gate"}
    review = by_kind.get("review")
    if review is None:
        gaps.append("review_missing")
    elif review["p0"] != 0:
        gaps.append("review_has_p0")
    learning = by_kind.get("learning")
    if learning is None:
        gaps.append("learning_missing")
    elif learning["passed"] is not True:
        gaps.append("learning_not_passed")
    approval = by_kind.get("approval")
    if approval is None:
        gaps.append("approval_missing")
    else:
        expected = warnings_digest(w for g in gates.values() for w in g["warnings"])
        if approval["warnings_digest"] != expected:
            gaps.append("approval_warnings_mismatch")
    return gaps


def read_evidence(root: Path, slug: str) -> dict:
    relative = f"{INBOX_DIR}/{slug}/{EVIDENCE_NAME}"
    data = _read_capped(root, relative, MAX_EVIDENCE_BYTES)
    try:
        evidence = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise VerifyError("evidence_not_json", relative) from None
    try:
        validate_evidence(evidence)
    except VerifyError as exc:
        raise VerifyError(exc.code, relative) from None
    return evidence


# ---------------------------------------------------------------- source mode


def _candidates(root: Path) -> list[str]:
    """Every .md and .mdx under the posts folder (repo-relative), refusing symlinks."""
    _no_symlink_components(root, POSTS_DIR)
    base = root / POSTS_DIR
    if not base.is_dir():
        return []
    found: list[str] = []
    count = 0
    stack = [base]
    while stack:
        folder = stack.pop()
        for entry in os.scandir(folder):
            count += 1
            if count > MAX_ENTRIES:
                raise VerifyError("too_many_entries", POSTS_DIR)
            relative = Path(entry.path).relative_to(root).as_posix()
            if entry.is_symlink():
                raise VerifyError("symlink_refused", relative)
            if entry.is_dir(follow_symlinks=False):
                stack.append(Path(entry.path))
            elif entry.name.endswith((".md", ".mdx")):
                found.append(relative)
    return sorted(found)


def _check_public(root: Path, relative: str, data: bytes) -> str:
    """Verify one public post; return its slug."""
    folder, _, name = relative.rpartition("/")
    if name.endswith(".mdx"):
        raise VerifyError("public_mdx_not_supported", relative)
    if folder != POSTS_DIR:
        raise VerifyError("public_post_in_subfolder", relative)
    slug = name[: -len(".md")]
    if not SLUG_RE.fullmatch(slug) or len(slug) > MAX_SLUG:
        raise VerifyError("slug_invalid", relative)
    parse_public_frontmatter(data, relative)
    current = hash_parts(relative, data, read_assets(root, slug))
    evidence = read_evidence(root, slug)
    evidence_path = f"{INBOX_DIR}/{slug}/{EVIDENCE_NAME}"
    if evidence["slug"] != slug or evidence["post_path"] != relative:
        raise VerifyError("evidence_wrong_post", evidence_path)
    gaps = evidence_gaps(evidence, current)
    if gaps:
        raise VerifyError(gaps[0], evidence_path)
    return slug


def verify_source(root: Path, environ=os.environ, strict: bool = False) -> tuple[list[str], list[dict]]:
    """Return (public slugs, errors) for the blog repo's posts (strict: the preview switch in any form)."""
    errors: list[dict] = []
    if environ.get(PREVIEW_VAR) or (strict and PREVIEW_VAR in environ):
        errors.append({"code": "preview_drafts_enabled", "path": ""})
    public: list[str] = []
    try:
        candidates = _candidates(root)
    except VerifyError as exc:
        return public, errors + [{"code": exc.code, "path": exc.path}]
    for relative in candidates:
        try:
            data = _read_capped(root, relative, MAX_POST_BYTES)
            if is_public(data, relative):
                public.append(_check_public(root, relative, data))
        except VerifyError as exc:
            errors.append({"code": exc.code, "path": exc.path or relative})
        except OSError:
            errors.append({"code": "unreadable_file", "path": relative})
    return sorted(public), errors


# ---------------------------------------------------------------- deploy output mode


def _dist_files(dist: Path) -> list[str]:
    """Every file under the deploy output (dist-relative), refusing symlinks and odd entries."""
    found: list[str] = []
    count = 0
    stack = [dist]
    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            raise VerifyError("unreadable_file", folder.relative_to(dist).as_posix()) from None
        for entry in entries:
            count += 1
            if count > MAX_ENTRIES:
                raise VerifyError("too_many_entries")
            relative = Path(entry.path).relative_to(dist).as_posix()
            try:
                if entry.is_symlink():
                    raise VerifyError("symlink_refused", relative)
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise VerifyError("not_a_regular_file", relative)
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                raise VerifyError("unreadable_file", relative) from None
            if entry.name.endswith((".html", ".xml")) and size > MAX_PAGE_BYTES:
                raise VerifyError("file_too_large", relative)
            found.append(relative)
    return sorted(found)


def _dist_error(code: str, relative: str = "") -> dict:
    return {"code": code, "path": f"{DIST_PREFIX}{relative}"}


def _is_private(relative: str) -> bool:
    """Whether a deploy output file is blog-kit working state that must never ship."""
    parts = relative.split("/")
    return (any(part in PRIVATE_FOLDERS for part in parts[:-1]) or parts[-1] in PRIVATE_NAMES
            or parts[-1].endswith((".md", ".mdx")))


def _built_posts(files: list[str]) -> tuple[set[str], list[dict]]:
    """Slugs of the post pages (posts/<slug>/index.html) and any other file under posts/."""
    slugs: set[str] = set()
    errors: list[dict] = []
    for relative in files:
        parts = relative.split("/")
        if parts[0] != "posts" or relative == "posts/index.html":
            continue
        if len(parts) == 3 and parts[2] == "index.html" and SLUG_RE.fullmatch(parts[1]):
            slugs.add(parts[1])
        else:
            errors.append(_dist_error("dist_post_path_invalid", relative))
    return slugs, errors


def _read_xml(dist: Path, relative: str) -> ET.Element:
    """Parse a feed or sitemap, refusing DOCTYPE and entity declarations before expat sees them.

    Only UTF-8 is read (no NUL, no other declared encoding), so the byte check
    for declarations sees what expat will parse; UTF-16 would hide them.
    """
    data = _read_capped(dist, relative, MAX_PAGE_BYTES)
    declared = XML_ENCODING_RE.match(data)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise VerifyError("dist_xml_encoding", relative) from None
    if b"\x00" in data or (declared and declared[1].lower() not in (b"utf-8", b"utf8")):
        raise VerifyError("dist_xml_encoding", relative)
    if DOCTYPE_RE.search(data):
        raise VerifyError("dist_xml_doctype", relative)
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        raise VerifyError("dist_xml_invalid", relative) from None


def _text(element: ET.Element) -> str:
    return (element.text or "").strip()


def _origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or "@" in parts.netloc:
        return ("", "")
    return (parts.scheme, parts.netloc.lower())


def _url_segments(url: str, origin: tuple[str, str], source: str) -> list[str]:
    """The decoded path segments of a same-origin URL that stays inside the output."""
    if _origin(url) == ("", "") or _origin(url) != origin:
        raise VerifyError("dist_url_origin", source)
    path = unquote(urlsplit(url).path)
    if not path.startswith("/") or "\\" in path or "\0" in path:
        raise VerifyError("dist_url_path_invalid", source)
    inner = path.strip("/")
    segments = inner.split("/") if inner else []
    if any(part in ("", ".", "..") for part in segments):
        raise VerifyError("dist_url_path_invalid", source)
    return segments


def _sitemap_urls(dist: Path) -> tuple[tuple[str, str], list[tuple[str, str]]]:
    """The site origin and (sitemap file, page URL) for every page the sitemap index reaches."""
    index = _read_xml(dist, "sitemap-index.xml")
    if index.tag != f"{SITEMAP_NS}sitemapindex":
        raise VerifyError("dist_xml_invalid", "sitemap-index.xml")
    locs = [_text(loc) for loc in index.iterfind(f"{SITEMAP_NS}sitemap/{SITEMAP_NS}loc")]
    if not locs:
        raise VerifyError("dist_sitemap_empty", "sitemap-index.xml")
    origin = _origin(locs[0])
    urls: list[tuple[str, str]] = []
    for loc in locs:
        segments = _url_segments(loc, origin, "sitemap-index.xml")
        if len(segments) != 1 or not SUB_SITEMAP_RE.fullmatch(segments[0]):
            raise VerifyError("dist_sitemap_not_allowed", "sitemap-index.xml")
        name = segments[0]
        sitemap = _read_xml(dist, name)
        if sitemap.tag != f"{SITEMAP_NS}urlset":
            raise VerifyError("dist_sitemap_not_allowed", name)
        urls += [(name, _text(u)) for u in sitemap.iterfind(f"{SITEMAP_NS}url/{SITEMAP_NS}loc")]
    return origin, urls


def _rss_urls(dist: Path) -> list[tuple[str, str]]:
    """("rss.xml", URL) for the channel link and every item link and permalink guid."""
    channel = _read_xml(dist, "rss.xml").find("channel")
    if channel is None:
        raise VerifyError("dist_xml_invalid", "rss.xml")
    found = [_text(link) for link in channel.findall("link")]
    for item in channel.findall("item"):
        found += [_text(link) for link in item.findall("link")]
        found += [_text(g) for g in item.findall("guid") if g.get("isPermaLink", "true") != "false"]
    return [("rss.xml", url) for url in found]


def _check_url(source: str, url: str, origin: tuple[str, str], files: set[str], public: set[str]) -> None:
    """Map a page URL to <path>/index.html in the output and check post URLs against public."""
    segments = _url_segments(url, origin, source)
    if len(segments) == 2 and segments[0] == "posts" and segments[1] not in public:
        raise VerifyError("dist_feed_post_without_evidence", source)
    if "/".join(segments + ["index.html"]) not in files:
        raise VerifyError("dist_url_target_missing", source)


def verify_dist(dist: Path, public: list[str]) -> list[dict]:
    """Errors for a deploy output folder checked against the verified public slugs."""
    if not dist.is_dir():
        return [_dist_error("dist_missing")]
    try:
        files = _dist_files(dist)
    except VerifyError as exc:
        return [_dist_error(exc.code, exc.path)]
    built, errors = _built_posts(files)
    errors += [_dist_error("dist_private_file", relative) for relative in files if _is_private(relative)]
    for relative in REQUIRED_PAGES:
        if relative not in files:
            errors.append(_dist_error("missing_file", relative))
    try:
        origin, urls = _sitemap_urls(dist)
        urls += _rss_urls(dist)
    except VerifyError as exc:
        return errors + [_dist_error(exc.code, exc.path)]
    known = set(files)
    for source, url in urls:
        try:
            _check_url(source, url, origin, known, set(public))
        except VerifyError as exc:
            errors.append(_dist_error(exc.code, exc.path))
    for slug in sorted(built - set(public)):
        errors.append(_dist_error("dist_post_without_evidence", f"posts/{slug}/index.html"))
    for slug in sorted(set(public) - built):
        errors.append(_dist_error("dist_post_missing", f"posts/{slug}/index.html"))
    return errors


def dist_root(argument: str) -> Path:
    """Resolve --dist, refusing a symlink on the folder or on any component of its path."""
    given = Path(argument)
    current = Path()
    for part in given.parts:
        current = current / part
        if current.is_symlink():
            raise VerifyError("symlink_refused")
    return given.resolve()


# ---------------------------------------------------------------- self check


def self_digest(data: bytes) -> str:
    """SHA-256 of this file's bytes with the SELF_DIGEST value zeroed."""
    zeroed = DIGEST_LINE_RE.sub(b'SELF_DIGEST = {"sha256": "' + b"0" * 64 + b'"}', data, count=1)
    return hashlib.sha256(zeroed).hexdigest()


def check_self(path: Path) -> str | None:
    """Return an error code when this file's bytes do not match its recorded digest."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_SELF_BYTES + 1)
    except OSError:
        return "self_unreadable"
    lines = DIGEST_LINE_RE.findall(data)
    if len(data) > MAX_SELF_BYTES or len(lines) != 1:
        return "self_digest_mismatch"
    # Compare with the digest in the file bytes, not the imported constant,
    # which a stale bytecode cache could hold for an edited copy.
    recorded = lines[0].split(b'"')[3].decode("ascii")
    return None if self_digest(data) == recorded else "self_digest_mismatch"


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify publish evidence for an Astro blog repo.")
    parser.add_argument("--repo", default=None, help="Blog repo folder (default: current folder)")
    parser.add_argument("--dist", default=None, help="Also check this built site folder before deploy")
    parser.add_argument("--ci", action="store_true",
                        help="Strict mode for CI and WORKERS_CI deploys: the preview switch fails in any form")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.repo and Path(args.repo).is_symlink():
        print(json.dumps({"status": "error", "errors": [{"code": "repo_is_symlink", "path": ""}]}, indent=2))
        return 2
    root = Path(args.repo or Path.cwd()).resolve()
    report = {"status": "fail", "mode": "dist" if args.dist else "source", "ci": args.ci,
              "verifier_version": VERIFIER_VERSION}
    problem = check_self(Path(__file__))
    if problem:
        report["errors"] = [{"code": problem, "path": ""}]
        print(json.dumps(report, indent=2))
        return 1
    public, errors = verify_source(root, strict=args.ci)
    if args.dist:
        try:
            errors += verify_dist(dist_root(args.dist), public)
        except VerifyError as exc:
            errors.append(_dist_error(exc.code))
    report.update(public=public, errors=errors, status="fail" if errors else "ok")
    print(json.dumps(report, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
