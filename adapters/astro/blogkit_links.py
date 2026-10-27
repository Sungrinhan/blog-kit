"""External link check for blog-kit publish gate 6 (stdlib only).

It reuses the upstream preflight's URL safety from this install
(scripts/blog_preflight.py, not modified): URL form, every resolved address
public, the connection pinned to the addresses already checked, no proxy, no
automatic redirect, a time limit and a one-byte read. On top of that:

- a URL with credentials (user or password) is refused;
- redirects are followed by hand, at most MAX_REDIRECTS hops, and every hop
  gets the same checks as the first URL;
- the outcome is ok, allowed (403 or 405 for a URL listed exactly in the
  upstream-format external-links.allowed file), skipped (mailto, no request),
  or blocked by kind: policy (refused by the safety rules, never retried),
  permanent (404, 410 and other client errors, a broken redirect) or
  transient (timeout, connection or DNS failure, 429, 5xx). Only transient
  failures are retried, RETRIES more times, and then become a warning.

A result names the URL by the first 16 hex digits of its SHA-256 and holds
fixed codes and HTTP statuses only, never URL text.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import socket
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

INSTALL_ROOT = Path(__file__).resolve().parent.parent.parent
PREFLIGHT_PATH = INSTALL_ROOT / "scripts" / "blog_preflight.py"
MAX_REDIRECTS = 5
MAX_EXCEPTIONS_BYTES = 64 * 1024
RETRIES = 2


def _preflight():
    name = "blogkit_upstream_preflight"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, PREFLIGHT_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


class LinkError(Exception):
    """A fixed code and an optional line number; never URL text."""

    def __init__(self, code: str, line: int | None = None) -> None:
        super().__init__(code if line is None else f"{code} (line {line})")
        self.code = code
        self.line = line


def load_exceptions(folder: Path) -> set[str]:
    """Exact URLs allowed to answer 403 or 405, from folder/external-links.allowed (upstream format).

    One http(s) URL per line, "#" lines and blank lines ignored, fragments
    ignored for matching. A symlink, a file over 64 KB, a file that is not
    UTF-8 or an entry that is not a well-formed http(s) URL is refused. The
    list never relaxes the safety checks; it only accepts those two statuses.
    """
    preflight = _preflight()
    path = folder / preflight.EXTERNAL_LINKS_ALLOWED_FILE
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return set()
    except OSError:
        raise LinkError("exceptions_symlink" if path.is_symlink() else "exceptions_unreadable") from None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise LinkError("exceptions_unreadable")
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(MAX_EXCEPTIONS_BYTES + 1)
    if len(data) > MAX_EXCEPTIONS_BYTES:
        raise LinkError("exceptions_too_large")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise LinkError("exceptions_unreadable") from None
    allowed: set[str] = set()
    for number, raw in enumerate(text.splitlines(), 1):
        entry = raw.strip()
        if not entry or entry.startswith("#"):
            continue
        ok, _reason = preflight._well_formed_http_url(entry)
        if not ok:
            raise LinkError("exceptions_invalid", number)
        allowed.add(preflight._normalize_external_url(entry))
    return allowed


def url_digest(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


class _Outcome(Exception):
    """How one attempt ended early: result, kind, fixed reason and status."""

    def __init__(self, result: str, kind: str | None, reason: str, status: int | None = None) -> None:
        super().__init__(reason)
        self.result, self.kind, self.reason, self.status = result, kind, reason, status


def _refuse_unsafe(preflight, url: str):
    """The validated host, port and addresses of one hop, or a policy outcome."""
    try:
        parts = urllib.parse.urlsplit(url)
        credentials = parts.username is not None or parts.password is not None
        host = parts.hostname or ""
    except ValueError:
        raise _Outcome("blocked", "policy", "url_not_allowed") from None
    if credentials:
        raise _Outcome("blocked", "policy", "credentials_in_url")
    if any(char.isspace() or ord(char) < 0x21 for char in host):
        raise _Outcome("blocked", "policy", "url_not_allowed")
    try:
        ok, reason, host, port, infos = preflight._resolve_public_http_url(url)
    except ValueError:  # UnicodeError from IDNA encoding, whose message names the host
        raise _Outcome("blocked", "policy", "url_not_allowed") from None
    # Upstream reports why only as text; a failed lookup is the one transient case.
    if not ok and (reason or "").startswith("DNS resolution"):
        raise _Outcome("transient", "transient", "dns_failed")
    if not ok:
        raise _Outcome("blocked", "policy", "url_not_allowed")
    return host, port, infos


def _request(preflight, url: str, method: str, opener, headers: dict[str, str] | None = None) -> tuple[int, str | None]:
    host, port, infos = _refuse_unsafe(preflight, url)
    try:
        request = urllib.request.Request(url, method=method,
                                         headers={"User-Agent": preflight.USER_AGENT, **(headers or {})})
    except ValueError:
        raise _Outcome("blocked", "policy", "url_not_allowed") from None
    try:
        with preflight._PinnedDNS(host, port, infos):
            with opener.open(request, timeout=preflight.HEAD_TIMEOUT) as response:
                if method == "GET":
                    response.read(1)
                return int(getattr(response, "status", 200)), response.headers.get("Location")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.headers.get("Location") if exc.headers else None
    except (urllib.error.URLError, OSError) as exc:
        cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        timed_out = isinstance(cause, (socket.timeout, TimeoutError))
        raise _Outcome("transient", "transient", "timeout" if timed_out else "connection_failed") from None


def _status_outcome(status: int) -> tuple[str, str | None, str]:
    if 200 <= status < 300:
        return "ok", None, "ok"
    if status in (404, 410):
        return "blocked", "permanent", "not_found"
    if status in (403, 405):
        return "blocked", "permanent", "forbidden"
    if status == 429:
        return "transient", "transient", "rate_limited"
    if 500 <= status < 600:
        return "transient", "transient", "server_error"
    return "blocked", "permanent", "http_error"


def _attempt(preflight, url: str, opener, allowed: set[str]) -> dict:
    """One pass over the redirect chain; every hop is checked like the first URL."""
    try:
        listed = preflight._normalize_external_url(url) in {preflight._normalize_external_url(a) for a in allowed}
    except ValueError:
        listed = False
    hops = 0
    while True:
        try:
            status, location = _request(preflight, url, "HEAD", opener)
            if status in (403, 405):
                status, location = _request(preflight, url, "GET", opener, {"Range": "bytes=0-0"})
        except _Outcome as exc:
            return {"result": exc.result, "kind": exc.kind, "reason": exc.reason, "status": exc.status, "hops": hops}
        if not 300 <= status < 400:
            break
        if not location:
            return {"result": "blocked", "kind": "permanent", "reason": "redirect_without_location",
                    "status": status, "hops": hops}
        if hops == MAX_REDIRECTS:
            return {"result": "blocked", "kind": "permanent", "reason": "too_many_redirects",
                    "status": status, "hops": hops}
        hops += 1
        try:
            url = urllib.parse.urljoin(url, location)
        except ValueError:
            return {"result": "blocked", "kind": "policy", "reason": "url_not_allowed", "status": status, "hops": hops}
    if status in (403, 405) and listed:
        return {"result": "allowed", "kind": None, "reason": "listed_exception", "status": status, "hops": hops}
    result, kind, reason = _status_outcome(status)
    return {"result": result, "kind": kind, "reason": reason, "status": status, "hops": hops}


def check_url(url: str, *, opener=None, allowed: set[str] = frozenset(), pause: float = 1.0) -> dict:
    """Check one external URL; see the module docstring for the outcomes."""
    if url[:7].lower() == "mailto:":
        return {"url": url_digest(url), "result": "skipped", "kind": None, "reason": "mailto", "status": None,
                "hops": 0, "attempts": 0}
    preflight = _preflight()
    opener = opener or preflight._PREFLIGHT_NO_REDIRECT_OPENER
    for attempt in range(1, RETRIES + 2):
        try:
            outcome = _attempt(preflight, url, opener, allowed)
        except Exception:  # noqa: BLE001 - anything else is refused, never a pass, and its text is dropped
            outcome = {"result": "blocked", "kind": "policy", "reason": "check_failed", "status": None, "hops": 0}
        if outcome["result"] != "transient":
            break
        if attempt <= RETRIES:
            time.sleep(pause)
    else:
        outcome["result"] = "warning"
    return {"url": url_digest(url), **outcome, "attempts": attempt}
