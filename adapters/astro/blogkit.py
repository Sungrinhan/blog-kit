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

Output holds fixed error codes, check ids and reasons, versions and
repo-relative paths only. It never prints post text, external command output
or URLs.

Usage:
    python3 blogkit.py COMMAND [SLUG] [--blog-repo PATH]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

INSTALL_ROOT = Path(__file__).resolve().parent.parent.parent
DOCTOR_PATH = INSTALL_ROOT / "scripts" / "doctor.py"
VERIFIER_PATH = INSTALL_ROOT / "adapters" / "astro" / "blogkit_verify.py"
MANIFEST_PATH = INSTALL_ROOT / ".claude-plugin" / "plugin.json"
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


class CliError(Exception):
    """A fixed error code, an optional repo-relative path and safe extra fields."""

    def __init__(self, code: str, path: str = "", **fields) -> None:
        super().__init__(code)
        self.code = code
        self.path = path
        self.fields = fields


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
            return dispatch(args, blog)
    return dispatch(args, blog)


def dispatch(args: argparse.Namespace, blog: Path) -> dict:
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
        report.update(exc.fields, errors=[{"code": exc.code, "path": exc.path}])
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
