"""S1: the engine CLI (adapters/astro/blogkit.py) through its subprocess interface.

Each test builds a temp world: the engine copied into a fake install path under
a temp HOME, an installed-plugins registry, a real git blog repo with shared
settings and a verifier copy, and stub `claude` and `git` executables on PATH.
The stub `claude` imitates `claude plugin list --json`; the stub `git` answers
`ls-remote` from a canned file and hands every other call to the real git. Both
stubs print a planted secret and URL on stderr. The CLI's JSON output, exit
code and the files it writes are the interface.

Stdlib + pytest only. No network.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ENGINE_FILES = (
    "adapters/astro/blogkit.py",
    "adapters/astro/blogkit_verify.py",
    "scripts/doctor.py",
    ".claude-plugin/plugin.json",
)
PLUGIN_ID = "claude-blog@blog-kit"
VERSION = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
TAG = f"v{VERSION}"
COMMIT = "c" * 40
# Planted in stub output. The CLI must never print either.
PLANTED = "sk-" + "Q" * 30
PLANTED_URL = "https://leak.example.test/private-path"
REAL_GIT = shutil.which("git")


class Engine:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.home = tmp / "home"
        self.install = self.home / ".claude" / "plugins" / "cache" / "blog-kit" / "claude-blog" / VERSION
        for relative in ENGINE_FILES:
            target = self.install / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())
        self.cli = self.install / "adapters" / "astro" / "blogkit.py"
        self.canonical = self.install / "adapters" / "astro" / "blogkit_verify.py"
        self.registry = self.home / ".claude" / "plugins" / "installed_plugins.json"
        self.bin = tmp / "bin"
        self.bin.mkdir()

        self.blog = tmp / "blog"
        (self.blog / ".claude").mkdir(parents=True)
        (self.blog / "src" / "content" / "posts").mkdir(parents=True)
        subprocess.run([REAL_GIT, "init", "-q", str(self.blog)], check=True, capture_output=True)
        self.copy = self.blog / "scripts" / "blogkit_verify.py"
        self.copy.parent.mkdir()
        self.copy.write_bytes(self.canonical.read_bytes())

        self.shared = {
            "extraKnownMarketplaces": {
                "blog-kit": {"source": {"source": "github", "repo": "Sungrinhan/blog-kit", "ref": TAG}}
            },
            "enabledPlugins": {PLUGIN_ID: True},
        }
        self.plugins = [{
            "id": PLUGIN_ID, "version": VERSION, "scope": "project", "enabled": True,
            "installPath": str(self.install), "projectEnabled": True,
        }]
        self.registry_data = {"version": 2, "plugins": {PLUGIN_ID: [{
            "scope": "project", "projectPath": str(self.blog), "installPath": str(self.install),
            "version": VERSION, "gitCommitSha": COMMIT,
        }]}}
        self.ls_remote = f"{COMMIT}\trefs/tags/{TAG}\n"
        self.env: dict[str, str] = {}
        self.git_failure: str | None = None

    def _stubs(self) -> None:
        out = self.tmp / "claude-out.json"
        out.write_text(json.dumps(self.plugins))
        remote = self.tmp / "ls-remote.txt"
        remote.write_text(self.ls_remote)
        claude = self.bin / "claude"
        claude.write_text(
            "#!/bin/sh\n"
            f'echo "stderr {PLANTED} {PLANTED_URL}" >&2\n'
            f'cat "{out}"\n'
        )
        git = self.bin / "git"
        fail = self.tmp / "git-rev-parse-fails.txt"
        if self.git_failure is None:
            fail.unlink(missing_ok=True)
        else:
            fail.write_text(self.git_failure)
        git.write_text(
            "#!/bin/sh\n"
            f'echo "stderr {PLANTED} {PLANTED_URL}" >&2\n'
            f'if [ "$1" = "ls-remote" ]; then cat "{remote}"; exit 0; fi\n'
            f'case "$*" in *rev-parse*) if [ -f "{fail}" ]; then cat "{fail}"; cat "{fail}" >&2; exit 128; fi;; esac\n'
            f'exec "{REAL_GIT}" "$@"\n'
        )
        for stub in (claude, git):
            stub.chmod(0o755)

    def run(self, *args: str, script: Path | None = None) -> tuple[int, dict, str]:
        (self.blog / ".claude" / "settings.json").write_text(json.dumps(self.shared))
        self.registry.write_text(json.dumps(self.registry_data))
        self._stubs()
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "BLOGKIT_INCLUDE_DRAFTS")}
        env.update(HOME=str(self.home), PATH=f"{self.bin}{os.pathsep}{env.get('PATH', '')}", **self.env)
        result = subprocess.run(
            [sys.executable, str(script or self.cli), *args, "--blog-repo", str(self.blog)],
            capture_output=True, text=True, env=env, cwd=self.tmp, timeout=120,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = {}
        return result.returncode, payload, result.stdout + result.stderr


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return Engine(tmp_path)


def _codes(payload: dict) -> list[str]:
    return [error["code"] for error in payload.get("errors", [])]


def _no_leak(output: str) -> None:
    assert PLANTED not in output and PLANTED_URL not in output


# ------------------------------------------------------------------ sync-verifier


def test_sync_verifier_repairs_a_stale_copy_when_the_identity_holds(engine: Engine) -> None:
    engine.copy.write_bytes(b"# an older verifier\n")
    rc, payload, output = engine.run("sync-verifier")
    assert (rc, payload["status"], payload["errors"]) == (0, "ok", []), output
    assert engine.copy.read_bytes() == engine.canonical.read_bytes()
    _no_leak(output)


# ------------------------------------------------------------------ install identity


def _statuses(payload: dict) -> dict[str, str]:
    return {check["id"]: check["status"] for check in payload.get("checks", [])}


def _version_mismatch(e: Engine) -> None:
    e.plugins[0]["version"] = "2.3.1-kit.0"
    e.registry_data["plugins"][PLUGIN_ID][0]["version"] = "2.3.1-kit.0"


@pytest.mark.parametrize("command", [["sync-verifier"], ["status", "first-post"], ["convert", "first-post"]])
def test_a_version_mismatch_is_refused(engine: Engine, command: list[str]) -> None:
    _version_mismatch(engine)
    before = engine.copy.read_bytes()
    rc, payload, output = engine.run(*command)
    assert rc == 1 and _codes(payload) == ["install_identity"]
    assert _statuses(payload)["d_version"] == "fail"
    assert engine.copy.read_bytes() == before
    _no_leak(output)


def test_running_from_outside_the_install_path_is_refused(engine: Engine) -> None:
    rc, payload, _ = engine.run("sync-verifier", script=ROOT / "adapters" / "astro" / "blogkit.py")
    assert rc == 1 and _codes(payload) == ["install_identity"]
    assert _statuses(payload)["f_exec_path"] == "fail"


def test_an_engine_copy_inside_the_blog_repo_is_refused(engine: Engine) -> None:
    inside = engine.blog / ".engine"
    for relative in ENGINE_FILES:
        target = inside / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    rc, payload, _ = engine.run("sync-verifier", script=inside / "adapters" / "astro" / "blogkit.py")
    assert rc == 1 and _codes(payload) == ["engine_inside_blog_repo"]


def test_a_symlinked_blog_repo_argument_is_refused(engine: Engine) -> None:
    link = engine.tmp / "blog-link"
    link.symlink_to(engine.blog)
    engine.blog = link
    rc, payload, _ = engine.run("sync-verifier")
    assert rc == 2 and _codes(payload) == ["blog_repo_is_symlink"]


def _copy_is_a_symlink(e: Engine) -> Path:
    real = e.tmp / "verifier-real.py"
    real.write_bytes(b"# outside the blog repo\n")
    e.copy.unlink()
    e.copy.symlink_to(real)
    return real


def _scripts_folder_is_a_symlink(e: Engine) -> Path:
    real = e.tmp / "scripts-real"
    e.copy.parent.rename(real)
    e.copy.parent.symlink_to(real)
    (real / "blogkit_verify.py").write_bytes(b"# outside the blog repo\n")
    return real / "blogkit_verify.py"


@pytest.mark.parametrize("setup", [_copy_is_a_symlink, _scripts_folder_is_a_symlink])
def test_sync_verifier_refuses_a_symlinked_target(engine: Engine, setup) -> None:
    outside = setup(engine)
    rc, payload, _ = engine.run("sync-verifier")
    assert rc == 1 and _codes(payload) == ["symlink_refused"]
    assert outside.read_bytes() == b"# outside the blog repo\n"


# ------------------------------------------------------------------ verifier copy precondition


@pytest.mark.parametrize("change", ["edited", "missing"])
def test_other_commands_refuse_a_copy_that_does_not_match_the_install(engine: Engine, change: str) -> None:
    if change == "edited":
        engine.copy.write_bytes(engine.copy.read_bytes() + b"# edited\n")
    else:
        engine.copy.unlink()
    rc, payload, _ = engine.run("status", "first-post")
    assert rc == 1 and _codes(payload) == ["verifier_copy"]
    assert payload["reason"]


def test_a_matching_copy_lets_the_command_run(engine: Engine) -> None:
    rc, payload, _ = engine.run("status", "first-post")
    assert rc == 1 and _codes(payload) == ["not_implemented"]


# ------------------------------------------------------------------ slug lock


def _lock(engine: Engine, slug: str = "first-post") -> Path:
    folder = engine.blog / "inbox" / slug
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "blogkit.lock"


def _dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def test_a_locking_command_releases_its_lock(engine: Engine) -> None:
    lock = _lock(engine)
    rc, payload, _ = engine.run("convert", "first-post")
    assert _codes(payload) == ["not_implemented"]
    assert not lock.exists()


def test_a_live_lock_refuses_a_second_run(engine: Engine) -> None:
    lock = _lock(engine)
    held = json.dumps({"pid": os.getpid(), "host": socket.gethostname()})
    lock.write_text(held)
    rc, payload, _ = engine.run("convert", "first-post")
    assert rc == 1 and _codes(payload) == ["slug_locked"]
    assert lock.read_text() == held


def test_a_dead_process_lock_on_this_host_is_cleared(engine: Engine) -> None:
    lock = _lock(engine)
    lock.write_text(json.dumps({"pid": _dead_pid(), "host": socket.gethostname()}))
    rc, payload, _ = engine.run("check", "first-post")
    assert _codes(payload) == ["not_implemented"]
    assert not lock.exists()


@pytest.mark.parametrize("content", [
    json.dumps({"pid": 1, "host": "another-host.example"}),
    "not json",
    json.dumps({"pid": "1", "host": "x"}),
])
def test_a_lock_from_another_host_or_unreadable_is_kept(engine: Engine, content: str) -> None:
    lock = _lock(engine)
    lock.write_text(content)
    rc, payload, _ = engine.run("publish", "first-post")
    assert rc == 1 and _codes(payload)[0] in ("slug_locked", "slug_lock_unreadable")
    assert lock.read_text() == content


def test_a_read_only_command_takes_no_lock(engine: Engine) -> None:
    lock = _lock(engine)
    lock.write_text(json.dumps({"pid": os.getpid(), "host": socket.gethostname()}))
    rc, payload, _ = engine.run("status", "first-post")
    assert _codes(payload) == ["not_implemented"]


def test_a_missing_slug_folder_is_reported(engine: Engine) -> None:
    rc, payload, _ = engine.run("approve", "first-post", "--hash", "0" * 64, "--warnings", "0" * 64)
    assert rc == 1 and _codes(payload) == ["slug_folder_missing"]


def test_a_symlinked_slug_folder_is_refused(engine: Engine) -> None:
    real = engine.tmp / "inbox-real"
    real.mkdir()
    (engine.blog / "inbox").mkdir()
    (engine.blog / "inbox" / "first-post").symlink_to(real)
    rc, payload, _ = engine.run("convert", "first-post")
    assert rc == 1 and _codes(payload) == ["symlink_refused"]
    assert list(real.iterdir()) == []


@pytest.mark.parametrize("slug", ["Bad_Slug", "../escape", "a" * 81])
def test_an_invalid_slug_is_refused(engine: Engine, slug: str) -> None:
    rc, payload, _ = engine.run("convert", slug)
    assert rc == 1 and _codes(payload) == ["slug_invalid"]


# ------------------------------------------------------------------ external command output


def test_a_blog_folder_that_is_not_a_git_repo_is_refused(engine: Engine) -> None:
    shutil.rmtree(engine.blog / ".git")
    engine.env["GIT_CEILING_DIRECTORIES"] = str(engine.tmp)
    rc, payload, output = engine.run("status", "first-post")
    assert rc == 1 and _codes(payload) == ["blog_repo_not_git"]
    assert payload["exit_code"] != 0
    _no_leak(output)


def test_a_blog_folder_inside_another_git_repo_is_refused(engine: Engine) -> None:
    shutil.rmtree(engine.blog / ".git")
    subprocess.run([REAL_GIT, "init", "-q", str(engine.tmp)], check=True, capture_output=True)
    rc, payload, _ = engine.run("status", "first-post")
    assert rc == 1 and _codes(payload) == ["blog_repo_not_git_root"]


def test_external_output_is_dropped_except_repo_paths_and_lines(engine: Engine) -> None:
    post = engine.blog / "src" / "content" / "posts" / "first-post.md"
    post.write_text("---\ndraft: true\n---\n")
    engine.git_failure = (
        f"fatal: {PLANTED} while reading {PLANTED_URL}\n"
        "src/content/posts/first-post.md:3: bad\n"
        f"{post}:7: same file by absolute path\n"
        "src/content/posts/missing.md:9: not a file\n"
        f"/etc/hosts:1: outside the blog repo {PLANTED}\n"
        "../outside.md:2: parent traversal\n"
    )
    rc, payload, output = engine.run("status", "first-post")
    assert rc == 1 and _codes(payload) == ["blog_repo_not_git"]
    assert payload["exit_code"] == 128
    assert payload["locations"] == ["src/content/posts/first-post.md:3", "src/content/posts/first-post.md:7"]
    _no_leak(output)
    assert "/etc/hosts" not in output and "fatal" not in output
