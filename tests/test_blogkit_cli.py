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
    "scripts/blog_render.py",
    "scripts/confidential_check.py",
    "scripts/blog_preflight.py",
    "adapters/astro/blogkit_links.py",
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
PREVIEW_DIR = ".blogkit-preview"
# What the stub `astro build` writes into its --outDir unless a test changes it.
DEFAULT_PREVIEW = {
    "index.html": "<html><body><a href=\"/posts/\">posts</a></body></html>",
    "posts/first-post/index.html": (
        "<html><body><article><h1 id=\"heading\">Heading</h1>"
        "<img src=\"/_astro/diagram.abc123.png\" alt=\"Diagram\">"
        "<img src=\"/_astro/logo.def456.svg\" alt=\"logo\"></article></body></html>"),
    "_astro/diagram.abc123.png": "png",
    "_astro/logo.def456.svg": "<svg></svg>",
    "posts/other/index.html": "<html><body><h1 id=\"top\">Other</h1></body></html>",
    "about/index.html": "<html><body>About</body></html>",
}
# Test bootstrap: fake DNS and HTTP for every CLI run, so no test reaches the network.
NETFAKE = '''\
import io, json, runpy, socket, sys, urllib.error, urllib.request
from email.message import Message

cli, table_path, calls_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(table_path, encoding="utf-8") as handle:
    table = json.load(handle)


def getaddrinfo(host, port, *args, **kwargs):
    address = table["hosts"].get(host, "93.184.216.34")
    if address is None:
        raise socket.gaierror(socket.EAI_NONAME, "unknown host")
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))]


def fake_open(self, request, data=None, timeout=None):
    url = request.full_url
    with open(calls_path, "a", encoding="utf-8") as handle:
        handle.write(request.get_method() + " " + url + "\\n")
    answer = table["answers"].get(url, 200)
    if answer == "timeout":
        raise urllib.error.URLError(socket.timeout("timed out"))
    if answer >= 300:
        raise urllib.error.HTTPError(url, answer, "scripted", Message(), io.BytesIO(b""))
    response = io.BytesIO(b"x")
    response.status, response.headers = answer, Message()
    return response


socket.getaddrinfo = getaddrinfo
urllib.request.OpenerDirector.open = fake_open
sys.argv = [cli, *sys.argv[4:]]
runpy.run_path(cli, run_name="__main__")
'''


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
        # Stub npx: exit code for `astro check`, and text appended to a file while it runs.
        self.astro_exit = 0
        self.astro_touch: tuple[str, str] | None = None
        self.isolated_path = False
        self.tools = ["node", "npx"]
        self.build_exit = 0
        self.preview = dict(DEFAULT_PREVIEW)
        self.net = {"hosts": {}, "answers": {}}
        self.timeout = 120.0

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
        calls = self.tmp / "npx-calls.txt"
        touch = ""
        if self.astro_touch is not None:
            relative, text = self.astro_touch
            touch = f'printf %s {json.dumps(text)} >> "{self.blog / relative}"\n'
        for name in ("node", "npx"):
            (self.bin / name).unlink(missing_ok=True)
        if "node" in self.tools:
            node = self.bin / "node"
            node.write_text("#!/bin/sh\necho v24.0.0\n")
            node.chmod(0o755)
        source = self.tmp / "preview-src"
        if source.exists():
            shutil.rmtree(source)
        for relative, text in self.preview.items():
            (source / relative).parent.mkdir(parents=True, exist_ok=True)
            (source / relative).write_text(text, encoding="utf-8")
        source.mkdir(exist_ok=True)
        if "npx" in self.tools:
            npx = self.bin / "npx"
            npx.write_text(
                "#!/bin/sh\n"
                f'echo "$* drafts=$BLOGKIT_INCLUDE_DRAFTS" >> "{calls}"\n'
                f'echo "stdout {PLANTED} {PLANTED_URL}"\n'
                f'echo "src/content/posts/first-post.md:2: fake diagnostic {PLANTED}" >&2\n'
                + touch +
                'case "$2 $3" in\n'
                f'  "astro check") exit {self.astro_exit};;\n'
                '  "astro build")\n'
                '    out=""; prev=""\n'
                '    for arg in "$@"; do [ "$prev" = "--outDir" ] && out="$arg"; prev="$arg"; done\n'
                f'    mkdir -p "$out" && cp -R "{source}/." "$out/"\n'
                f'    exit {self.build_exit};;\n'
                "esac\n"
                "exit 0\n"
            )
            npx.chmod(0o755)
        if self.isolated_path:
            sysbin = self.tmp / "sysbin"
            sysbin.mkdir(exist_ok=True)
            for tool in ("cat", "mkdir", "cp"):
                if not (sysbin / tool).exists():
                    (sysbin / tool).symlink_to(shutil.which(tool))
        (self.tmp / "net.json").write_text(json.dumps(self.net))
        netfake = self.tmp / "netfake.py"
        netfake.write_text(NETFAKE)
        wrapper = self.tmp / "run-netfake.py"
        wrapper.write_text(
            "import runpy, sys\n"
            f"sys.argv = [{str(netfake)!r}, {str(self.cli)!r}, {str(self.tmp / 'net.json')!r}, "
            f"{str(self.tmp / 'net-calls.txt')!r}, *sys.argv[1:]]\n"
            f"runpy.run_path({str(netfake)!r}, run_name='__main__')\n"
        )

    def run(self, *args: str, script: Path | None = None) -> tuple[int, dict, str]:
        (self.blog / ".claude" / "settings.json").write_text(json.dumps(self.shared))
        self.registry.write_text(json.dumps(self.registry_data))
        self._stubs()
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "BLOGKIT_INCLUDE_DRAFTS")}
        rest = str(self.tmp / "sysbin") if self.isolated_path else env.get("PATH", "")
        env.update(HOME=str(self.home), PATH=f"{self.bin}{os.pathsep}{rest}", **self.env)
        result = subprocess.run(
            [sys.executable, str(script or self.tmp / "run-netfake.py"), *args, "--blog-repo", str(self.blog)],
            capture_output=True, text=True, env=env, cwd=self.tmp, timeout=self.timeout,
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
    # Past every precondition: the command itself runs and finds no converted copy.
    assert rc == 1 and _codes(payload) == ["evidence_missing"]


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
    rc, payload, _ = engine.run("check", "first-post")
    assert _codes(payload) == ["evidence_missing"]
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
    assert _codes(payload) == ["evidence_missing"]
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


LOCK_RACE = '''\
"""Test bootstrap: a second process takes the lock while this one is judging a dead holder."""
import os
import runpy
import subprocess
import sys
import time
from pathlib import Path

cli, holder, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
real_kill = os.kill
started = False


def kill(pid, sig):
    global started
    if sig == 0 and not started:
        started = True
        with out.open("w") as handle:
            subprocess.Popen([sys.executable, holder], stdout=handle)
        deadline = time.time() + 20
        while time.time() < deadline and not out.read_text():
            time.sleep(0.05)
    return real_kill(pid, sig)


os.kill = kill
sys.argv = [cli, *sys.argv[4:]]
runpy.run_path(cli, run_name="__main__")
'''
LOCK_HOLDER = '''\
"""The second process: the engine's own slug lock, held until the test releases it."""
import runpy
import time
from pathlib import Path

engine = runpy.run_path({cli!r})
lock = engine["SlugLock"](Path({blog!r}), "first-post")
try:
    lock.__enter__()
except engine["CliError"] as exc:
    print(exc.code, flush=True)
else:
    print("held", flush=True)
    deadline = time.time() + 30
    while time.time() < deadline and not Path({release!r}).exists():
        time.sleep(0.05)
    lock.__exit__(None, None, None)
'''


def test_two_runs_reclaiming_a_dead_lock_never_both_hold_it(engine: Engine) -> None:
    _lock(engine).write_text(json.dumps({"pid": _dead_pid(), "host": socket.gethostname()}))
    release, out = engine.tmp / "release", engine.tmp / "holder-out.txt"
    holder = engine.tmp / "holder.py"
    holder.write_text(LOCK_HOLDER.format(cli=str(engine.cli), blog=str(engine.blog), release=str(release)))
    script = engine.tmp / "lock-race.py"
    script.write_text(LOCK_RACE)
    wrapper = engine.tmp / "run-lock-race.py"
    wrapper.write_text(
        "import runpy, sys\n"
        f"sys.argv = [{str(script)!r}, {str(engine.cli)!r}, {str(holder)!r}, {str(out)!r}, *sys.argv[1:]]\n"
        f"runpy.run_path({str(script)!r}, run_name='__main__')\n"
    )
    try:
        rc, payload, output = engine.run("approve", "first-post", "--hash", "0" * 64, "--warnings", "0" * 64,
                                         script=wrapper)
    finally:
        release.write_text("")
    second = out.read_text().strip()
    assert second in ("held", "slug_locked"), output
    assert "slug_locked" in (second, *_codes(payload)), (second, output)


def test_a_lock_that_is_a_fifo_is_refused_without_waiting(engine: Engine) -> None:
    os.mkfifo(_lock(engine))
    engine.timeout = 20
    rc, payload, output = engine.run("check", "first-post")
    assert (rc, _codes(payload)) == (1, ["slug_lock_unreadable"]), output


def test_a_read_only_command_takes_no_lock(engine: Engine) -> None:
    lock = _lock(engine)
    lock.write_text(json.dumps({"pid": os.getpid(), "host": socket.gethostname()}))
    rc, payload, _ = engine.run("status", "first-post")
    assert _codes(payload) == ["evidence_missing"]


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


# ------------------------------------------------------------------ convert: first conversion

SLUG = "first-post"
PNG = b"\x89PNG\r\n\x1a\n" + b"fake png bytes"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>\n'
FRONTMATTER = (
    "---\n"
    'title: "첫 글: 가짜 제목"\n'
    'description: "가짜 설명입니다"\n'
    'date: "2026-01-02"\n'
    'lastUpdated: "2026-01-05"\n'
    'author: "Fake Author"\n'
    'canonical: "https://site.example/posts/first-post/"\n'
    'lang: "ko"\n'
    'kicker: "Fake kicker"\n'
    'og_image_alt: "Fake alt text"\n'
    'tags: ["alpha", "베타"]\n'
    "---\n"
)
BODY = (
    "\n"
    "# Heading\n"
    "\n"
    '![Diagram](images/diagram.png "Fake title")\n'
    "See [the guide](/posts/other/), [the section](#heading), [a site](https://example.com/a)"
    " and [mail](mailto:someone@example.com).\n"
    "![remote](https://example.com/remote.png)\n"
    "Again ![same](images/diagram.png) and [![logo](logo.svg)](/about/).\n"
    "\n"
    "```md\n"
    "![inside a fence](not-there.png)\n"
    "```\n"
    "Inline `![inside code](also-not-there.png)` too.\n"
)
EXPECTED_POST = (
    "---\n"
    'title: "첫 글: 가짜 제목"\n'
    'description: "가짜 설명입니다"\n'
    'pubDate: "2026-01-02"\n'
    'updatedDate: "2026-01-05"\n'
    'tags: ["alpha", "베타"]\n'
    "draft: true\n"
    "---\n"
    "\n"
    "# Heading\n"
    "\n"
    '![Diagram](../../assets/posts/first-post/diagram.png "Fake title")\n'
    "See [the guide](/posts/other/), [the section](#heading), [a site](https://example.com/a)"
    " and [mail](mailto:someone@example.com).\n"
    "![remote](https://example.com/remote.png)\n"
    "Again ![same](../../assets/posts/first-post/diagram.png) and"
    " [![logo](../../assets/posts/first-post/logo.svg)](/about/).\n"
    "\n"
    "```md\n"
    "![inside a fence](not-there.png)\n"
    "```\n"
    "Inline `![inside code](also-not-there.png)` too.\n"
)
POST = f"src/content/posts/{SLUG}.md"
ASSETS = f"src/assets/posts/{SLUG}"
EVIDENCE = f"inbox/{SLUG}/publish-evidence.json"


def _draft(engine: Engine, frontmatter: str = FRONTMATTER, body: str = BODY) -> Path:
    folder = engine.blog / "inbox" / SLUG / "draft"
    (folder / "images").mkdir(parents=True, exist_ok=True)
    (folder / "images" / "diagram.png").write_bytes(PNG)
    (folder / "logo.svg").write_bytes(SVG)
    draft = folder / f"{SLUG}.md"
    draft.write_text(frontmatter + body, encoding="utf-8")
    return draft


def _tree(engine: Engine) -> dict[str, bytes]:
    """Every file in the blog repo outside .git and .claude (the harness rewrites settings each run)."""
    files = {}
    for path in sorted(engine.blog.rglob("*")):
        relative = path.relative_to(engine.blog).as_posix()
        if relative.split("/")[0] in (".git", ".claude"):
            continue
        files[relative] = path.read_bytes() if path.is_file() and not path.is_symlink() else b"<dir>"
    return files


def _verifier():
    import importlib.util
    spec = importlib.util.spec_from_file_location("verifier_oracle", ROOT / "adapters" / "astro" / "blogkit_verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def test_convert_creates_the_first_public_copy(engine: Engine) -> None:
    _draft(engine)
    before = _tree(engine)
    rc, payload, output = engine.run("convert", SLUG)
    assert (rc, payload["status"], payload["errors"]) == (0, "ok", []), output

    post = (engine.blog / POST).read_bytes()
    assert post.decode("utf-8") == EXPECTED_POST
    assert (engine.blog / ASSETS / "diagram.png").read_bytes() == PNG
    assert (engine.blog / ASSETS / "logo.svg").read_bytes() == SVG
    assert sorted(p.name for p in (engine.blog / ASSETS).iterdir()) == ["diagram.png", "logo.svg"]

    evidence = json.loads((engine.blog / EVIDENCE).read_text(encoding="utf-8"))
    verifier = _verifier()
    verifier.validate_evidence(evidence)
    assert evidence == {
        "contract": 1, "slug": SLUG, "post_path": POST,
        "hash_algo": "blogkit-content-v1", "content_hash": verifier.content_hash(engine.blog, SLUG),
        "engine_version": VERSION,
        "public_copy": {
            "post_path": POST, "post_digest": _sha(post),
            "assets": [
                {"path": f"{ASSETS}/diagram.png", "digest": _sha(PNG)},
                {"path": f"{ASSETS}/logo.svg", "digest": _sha(SVG)},
            ],
            "engine_version": VERSION,
        },
        "records": [],
    }
    assert payload["content_hash"] == evidence["content_hash"]

    # Only the three targets were added; the draft folder and the rest are untouched.
    after = _tree(engine)
    added = sorted(set(after) - set(before))
    assert added == sorted(["src/assets", "src/assets/posts", ASSETS, f"{ASSETS}/diagram.png",
                            f"{ASSETS}/logo.svg", EVIDENCE, POST])
    assert {k: after[k] for k in before} == before
    _no_leak(output)
    assert "example.com" not in output


# ------------------------------------------------------------------ convert: refusals leave the repo unchanged


def _fm(old: str, new: str):
    def change(e: Engine) -> None:
        assert old in FRONTMATTER
        _draft(e, frontmatter=FRONTMATTER.replace(old, new))
    return change


def _body(text: str):
    def change(e: Engine) -> None:
        _draft(e, body=BODY + text)
    return change


def _existing(relative: str, folder: bool = False):
    def change(e: Engine) -> None:
        _draft(e)
        target = e.blog / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if folder:
            target.mkdir()
        else:
            target.write_bytes(b"made by hand\n")
    return change


def _collision(e: Engine) -> None:
    _draft(e, body=BODY + "![other](other/diagram.png)\n")
    (e.blog / "inbox" / SLUG / "draft" / "other").mkdir()
    (e.blog / "inbox" / SLUG / "draft" / "other" / "diagram.png").write_bytes(PNG + b"other")


def _non_image(e: Engine) -> None:
    _draft(e, body=BODY + "[notes](notes.txt)\n")
    (e.blog / "inbox" / SLUG / "draft" / "notes.txt").write_text("fake notes\n")


def _outside_file(e: Engine) -> None:
    _draft(e, body=BODY + "![up](../outside.png)\n")
    (e.blog / "inbox" / SLUG / "outside.png").write_bytes(PNG)


def _upper_name(e: Engine) -> None:
    _draft(e, body=BODY + "![upper](Upper.png)\n")
    (e.blog / "inbox" / SLUG / "draft" / "Upper.png").write_bytes(PNG)


def _symlinked_image(e: Engine) -> None:
    _draft(e, body=BODY + "![link](linked.png)\n")
    real = e.tmp / "outside.png"
    real.write_bytes(PNG)
    (e.blog / "inbox" / SLUG / "draft" / "linked.png").symlink_to(real)


def _raw_draft(data: bytes):
    def change(e: Engine) -> None:
        _draft(e).write_bytes(data)
    return change


def _symlinked_draft(e: Engine) -> None:
    draft = _draft(e)
    real = e.tmp / "outside-draft.md"
    real.write_bytes(draft.read_bytes())
    draft.unlink()
    draft.symlink_to(real)


def _symlinked_draft_folder(e: Engine) -> None:
    folder = _draft(e).parent
    real = e.tmp / "outside-draft-folder"
    folder.rename(real)
    folder.symlink_to(real)


def _symlinked_assets_parent(e: Engine) -> None:
    _draft(e)
    real = e.tmp / "outside-assets"
    real.mkdir()
    (e.blog / "src" / "assets").symlink_to(real)


BODY_FIRST_LINE = FRONTMATTER.count("\n") + 1
APPENDED_LINE = BODY_FIRST_LINE + BODY.count("\n")

REFUSALS = [
    ("unknown key", _fm('lang: "ko"\n', 'lang: "ko"\nsummary: "x"\n'), "draft_key_not_allowed", 9),
    ("alias dateModified", _fm('lang: "ko"\n', 'lang: "ko"\ndateModified: "2026-01-05"\n'), "draft_key_not_allowed", 9),
    ("alias slug", _fm('lang: "ko"\n', 'lang: "ko"\nslug: "first-post"\n'), "draft_key_not_allowed", 9),
    ("alias site_name", _fm('lang: "ko"\n', 'lang: "ko"\nsite_name: "Fake"\n'), "draft_key_not_allowed", 9),
    ("duplicate key", _fm('lang: "ko"\n', 'lang: "ko"\nlang: "en"\n'), "draft_key_duplicate", 9),
    ("block scalar", _fm('title: "첫 글: 가짜 제목"\n', "title: >\n  folded\n"), "draft_frontmatter_grammar", 2),
    ("anchor", _fm('kicker: "Fake kicker"\n', "kicker: &a Fake\n"), "draft_frontmatter_grammar", 9),
    ("description over 160", _fm('description: "가짜 설명입니다"\n', f'description: "{"가" * 161}"\n'),
     "description_too_long", None),
    ("missing date", _fm('date: "2026-01-02"\n', ""), "draft_key_missing", None),
    ("impossible date", _fm('date: "2026-01-02"\n', 'date: "2026-02-30"\n'), "date_invalid", None),
    ("date format", _fm('date: "2026-01-02"\n', 'date: "2026/01/02"\n'), "date_invalid", None),
    ("updated before date", _fm('lastUpdated: "2026-01-05"\n', 'lastUpdated: "2026-01-01"\n'),
     "updated_before_date", None),
    ("marker", _body("[IMAGE: a fake marker]\n"), "marker_left", APPENDED_LINE),
    ("outside the draft folder", _outside_file, "reference_outside_draft", APPENDED_LINE),
    ("non-image file", _non_image, "reference_not_image", APPENDED_LINE),
    ("missing file", _body("![gone](gone.png)\n"), "reference_missing", APPENDED_LINE),
    ("name collision", _collision, "asset_name_collision", APPENDED_LINE),
    ("asset name", _upper_name, "asset_name_invalid", APPENDED_LINE),
    ("scheme-less authority", _body("![host](//example.com/x.png)\n"), "reference_unsafe", APPENDED_LINE),
    ("backslash", _body("![win](images\\diagram.png)\n"), "reference_unsafe", APPENDED_LINE),
    ("javascript link", _body("[x](javascript:alert(1))\n"), "reference_scheme_not_allowed", APPENDED_LINE),
    ("mailto image", _body("![x](mailto:someone@example.com)\n"), "reference_scheme_not_allowed", APPENDED_LINE),
    ("query on a file", _body("![q](logo.svg?v=1)\n"), "reference_file_invalid", APPENDED_LINE),
    ("symlinked image", _symlinked_image, "symlink_refused", None),
    ("crlf", _raw_draft((FRONTMATTER + BODY).replace("\n", "\r\n").encode()), "crlf_not_allowed", None),
    ("bom", _raw_draft(b"\xef\xbb\xbf" + (FRONTMATTER + BODY).encode()), "bom_not_allowed", None),
    ("symlinked draft", _symlinked_draft, "symlink_refused", None),
    ("symlinked draft folder", _symlinked_draft_folder, "symlink_refused", None),
    ("symlinked assets parent", _symlinked_assets_parent, "symlink_refused", None),
    ("draft over 1MB", _body("x" * (1024 * 1024)), "file_too_large", None),
    ("existing post", _existing(POST), "target_exists", None),
    ("existing mdx post", _existing(f"src/content/posts/{SLUG}.mdx"), "target_exists", None),
    ("existing asset folder", _existing(ASSETS, folder=True), "target_exists", None),
    ("existing evidence", _existing(EVIDENCE), "target_exists", None),
]


@pytest.mark.parametrize("setup,code,line", [case[1:] for case in REFUSALS], ids=[case[0] for case in REFUSALS])
def test_convert_refusals_change_nothing(engine: Engine, setup, code: str, line: int | None) -> None:
    setup(engine)
    before = _tree(engine)
    rc, payload, output = engine.run("convert", SLUG)
    assert rc == 1 and _codes(payload) == [code], output
    if line is not None:
        assert payload["errors"][0]["line"] == line
    assert _tree(engine) == before
    _no_leak(output)
    assert "example.com" not in output and "가짜" not in output


def test_a_description_of_exactly_160_characters_is_kept(engine: Engine) -> None:
    _draft(engine, frontmatter=FRONTMATTER.replace('"가짜 설명입니다"', f'"{"가" * 160}"'))
    rc, payload, output = engine.run("convert", SLUG)
    assert rc == 0, output
    assert f'description: "{"가" * 160}"\n' in (engine.blog / POST).read_text(encoding="utf-8")


# ------------------------------------------------------------------ convert: one unit

BOOTSTRAP = '''\
"""Test bootstrap: run the installed CLI with os.replace swapped (failure injection)."""
import os
import runpy
import sys
from pathlib import Path

cli, blog, mode, count = sys.argv[1], Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
real_replace = os.replace
seen = 0


def replace(src, dst, *args, **kwargs):
    global seen
    relative = Path(dst).relative_to(blog).as_posix()
    internal = "/.convert-stage/" in relative or "/.convert-backup/" in relative or "convert-journal" in relative
    if mode == "fail-evidence" and relative.endswith("/publish-evidence.json"):
        raise PermissionError(13, "injected failure " + str(dst))
    if mode == "fail-target" and not internal:
        seen += 1
        if seen == count:
            raise OSError("injected failure")
    real_replace(src, dst, *args, **kwargs)
    if mode == "appear-before-apply" and relative.endswith("/convert-journal.json"):
        (blog / "src" / "content" / "posts" / "first-post.md").write_text("made by hand\\n")


real_rename, real_link = os.rename, os.link


def rename(src, dst, *args, **kwargs):
    if mode == "edit-before-swap" and str(src).endswith("/src/content/posts/first-post.md"):
        with open(src, "a", encoding="utf-8") as handle:
            handle.write("edited just before the swap\\n")
    real_rename(src, dst, *args, **kwargs)


def link(src, dst, *args, **kwargs):
    if mode == "fail-link" and ".publish-tmp" in str(src):
        raise OSError(5, "injected failure")
    real_link(src, dst, *args, **kwargs)
    if mode == "edit-after-flip" and ".publish-tmp" in str(src):
        with open(dst, "a", encoding="utf-8") as handle:
            handle.write("edited after the last check\\n")


os.replace, os.rename, os.link = replace, rename, link

if mode == "fail-index-update":
    import subprocess
    real_run = subprocess.run

    def run(args, *rest, **kwargs):
        env = kwargs.get("env") or {}
        if "update-index" in args and "GIT_INDEX_FILE" not in env:
            return subprocess.CompletedProcess(args, 1, "", "")
        return real_run(args, *rest, **kwargs)

    subprocess.run = run
sys.argv = [cli, *sys.argv[5:]]
runpy.run_path(cli, run_name="__main__")
'''


def _bootstrap(engine: Engine, mode: str, count: int = 0) -> Path:
    script = engine.tmp / "bootstrap.py"
    script.write_text(BOOTSTRAP)
    wrapper = engine.tmp / "run-bootstrap.py"
    wrapper.write_text(
        "import runpy, sys\n"
        f"sys.argv = [{str(script)!r}, {str(engine.cli)!r}, {str(engine.blog)!r}, {mode!r}, '{count}', *sys.argv[1:]]\n"
        f"runpy.run_path({str(script)!r}, run_name='__main__')\n"
    )
    return wrapper


@pytest.mark.parametrize("count", [2, 3, 4])
def test_a_failed_later_move_rolls_the_unit_back(engine: Engine, count: int) -> None:
    _draft(engine)
    before = _tree(engine)
    rc, payload, output = engine.run("convert", SLUG, script=_bootstrap(engine, "fail-target", count))
    assert rc == 1 and _codes(payload) == ["apply_failed"], output
    assert payload["rolled_back"] is True
    assert _tree(engine) == before
    _no_leak(output)


def test_a_target_that_appears_before_the_moves_stops_the_unit(engine: Engine) -> None:
    _draft(engine)
    before = _tree(engine)
    rc, payload, output = engine.run("convert", SLUG, script=_bootstrap(engine, "appear-before-apply"))
    assert rc == 1 and _codes(payload) == ["target_changed"], output
    assert payload["errors"][0]["path"] == POST
    after = _tree(engine)
    assert after.pop(POST) == b"made by hand\n"
    assert after == before


def test_reference_definitions_and_angle_destinations_are_rewritten(engine: Engine) -> None:
    _draft(engine, body="\n[diagram]: images/diagram.png\n![logo](<logo.svg>)\n[site]: https://example.com/b\n")
    rc, payload, output = engine.run("convert", SLUG)
    assert rc == 0, output
    body = (engine.blog / POST).read_text(encoding="utf-8").split("---\n", 2)[2]
    assert body == (
        "\n[diagram]: ../../assets/posts/first-post/diagram.png\n"
        "![logo](../../assets/posts/first-post/logo.svg)\n"
        "[site]: https://example.com/b\n"
    )


# ------------------------------------------------------------------ convert: again over its own copy

PNG_V2 = b"\x89PNG\r\n\x1a\n" + b"fake png bytes, second version"


def _gate_record(content_hash: str) -> dict:
    return {"kind": "gate", "gate": 1, "passed": True, "warnings": [],
            "content_hash": content_hash, "engine_version": VERSION}


def _converted(engine: Engine) -> dict:
    """A first conversion plus one gate record, as later commands would leave it."""
    _draft(engine)
    rc, _, output = engine.run("convert", SLUG)
    assert rc == 0, output
    evidence = json.loads((engine.blog / EVIDENCE).read_text(encoding="utf-8"))
    evidence["records"] = [_gate_record(evidence["content_hash"])]
    (engine.blog / EVIDENCE).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return evidence


def test_reconverting_a_recorded_copy_starts_new_evidence(engine: Engine) -> None:
    old = _converted(engine)
    draft = engine.blog / "inbox" / SLUG / "draft"
    (draft / "images" / "diagram.png").write_bytes(PNG_V2)
    (draft / "chart.png").write_bytes(PNG)
    (draft / f"{SLUG}.md").write_text(FRONTMATTER + "\nNew body ![d](images/diagram.png) ![c](chart.png)\n",
                                      encoding="utf-8")

    rc, payload, output = engine.run("convert", SLUG)
    assert (rc, payload["errors"]) == (0, []), output

    post = (engine.blog / POST).read_bytes()
    assert post.decode("utf-8").endswith(
        "---\n\nNew body ![d](../../assets/posts/first-post/diagram.png)"
        " ![c](../../assets/posts/first-post/chart.png)\n")
    assert sorted(p.name for p in (engine.blog / ASSETS).iterdir()) == ["chart.png", "diagram.png"]
    assert (engine.blog / ASSETS / "diagram.png").read_bytes() == PNG_V2

    evidence = json.loads((engine.blog / EVIDENCE).read_text(encoding="utf-8"))
    verifier = _verifier()
    verifier.validate_evidence(evidence)
    new_hash = verifier.content_hash(engine.blog, SLUG)
    assert new_hash != old["content_hash"]
    assert evidence["content_hash"] == payload["content_hash"] == new_hash
    assert evidence["records"] == []
    assert evidence["engine_version"] == VERSION
    assert evidence["public_copy"] == {
        "post_path": POST, "post_digest": _sha(post),
        "assets": [
            {"path": f"{ASSETS}/chart.png", "digest": _sha(PNG)},
            {"path": f"{ASSETS}/diagram.png", "digest": _sha(PNG_V2)},
        ],
        "engine_version": VERSION,
    }
    assert not (engine.blog / "inbox" / SLUG / ".convert-stage").exists()
    assert not (engine.blog / "inbox" / SLUG / ".convert-backup").exists()
    assert not (engine.blog / "inbox" / SLUG / "convert-journal.json").exists()
    _no_leak(output)


def test_reconverting_the_same_content_keeps_the_records(engine: Engine) -> None:
    old = _converted(engine)
    rc, payload, output = engine.run("convert", SLUG)
    assert (rc, payload["errors"]) == (0, []), output
    evidence = json.loads((engine.blog / EVIDENCE).read_text(encoding="utf-8"))
    assert evidence == old


def _rewrite_draft(e: Engine) -> None:
    """A changed draft, so an overwrite would show."""
    (e.blog / "inbox" / SLUG / "draft" / f"{SLUG}.md").write_text(FRONTMATTER + "\nChanged body\n", encoding="utf-8")


def _published(e: Engine) -> None:
    post = e.blog / POST
    post.write_text(post.read_text(encoding="utf-8").replace("draft: true\n", "draft: false\n"), encoding="utf-8")


def _hand_edited(e: Engine) -> None:
    with (e.blog / POST).open("a", encoding="utf-8") as handle:
        handle.write("A line written by hand.\n")


def _unowned_asset(e: Engine) -> None:
    (e.blog / ASSETS / "extra.png").write_bytes(PNG)


def _changed_asset(e: Engine) -> None:
    (e.blog / ASSETS / "diagram.png").write_bytes(PNG_V2)


def _missing_asset(e: Engine) -> None:
    (e.blog / ASSETS / "logo.svg").unlink()


def _broken_evidence(e: Engine) -> None:
    (e.blog / EVIDENCE).write_text("{}\n", encoding="utf-8")


TEMPLATE_POST = (
    "---\n"
    "title: 'First post'\n"
    "description: 'Lorem ipsum dolor sit amet'\n"
    "pubDate: 'Jul 08 2022'\n"
    "heroImage: '../../assets/blog-placeholder-3.jpg'\n"
    "draft: true\n"
    "---\n"
    "\n"
    "Lorem ipsum dolor sit amet.\n"
)


def _template_post(e: Engine) -> None:
    _draft(e)
    (e.blog / POST).write_text(TEMPLATE_POST, encoding="utf-8")


OVERWRITE_REFUSALS = [
    ("published post", _published, "target_published", POST),
    ("hand-edited post", _hand_edited, "target_modified", POST),
    ("unowned asset", _unowned_asset, "asset_unowned", f"{ASSETS}/extra.png"),
    ("changed asset", _changed_asset, "target_modified", f"{ASSETS}/diagram.png"),
    ("missing asset", _missing_asset, "target_modified", f"{ASSETS}/logo.svg"),
    ("broken evidence", _broken_evidence, "evidence_invalid", EVIDENCE),
]


@pytest.mark.parametrize("change,code,path", [case[1:] for case in OVERWRITE_REFUSALS],
                         ids=[case[0] for case in OVERWRITE_REFUSALS])
def test_reconvert_refusals_change_nothing(engine: Engine, change, code: str, path: str) -> None:
    _converted(engine)
    change(engine)
    _rewrite_draft(engine)
    before = _tree(engine)
    rc, payload, output = engine.run("convert", SLUG)
    assert rc == 1 and _codes(payload) == [code], output
    assert payload["errors"][0]["path"] == path
    assert _tree(engine) == before
    _no_leak(output)


def test_a_template_post_without_a_record_is_kept(engine: Engine) -> None:
    _template_post(engine)
    before = _tree(engine)
    rc, payload, output = engine.run("convert", SLUG)
    assert rc == 1 and _codes(payload) == ["target_exists"], output
    assert payload["errors"][0]["path"] == POST
    assert _tree(engine) == before


def test_a_published_template_post_is_kept(engine: Engine) -> None:
    # Outside the public grammar the status is unclear, so it is refused as a file without a record.
    _draft(engine)
    (engine.blog / POST).write_text(TEMPLATE_POST.replace("draft: true", "draft: false"), encoding="utf-8")
    before = _tree(engine)
    rc, payload, output = engine.run("convert", SLUG)
    assert rc == 1 and _codes(payload) == ["target_exists"], output
    assert _tree(engine) == before


def test_a_recorded_target_that_changes_before_the_moves_stops_the_unit(engine: Engine) -> None:
    _converted(engine)
    _rewrite_draft(engine)
    before = _tree(engine)
    rc, payload, output = engine.run("convert", SLUG, script=_bootstrap(engine, "appear-before-apply"))
    assert rc == 1 and _codes(payload) == ["target_changed"], output
    assert payload["errors"][0]["path"] == POST
    after = _tree(engine)
    assert after.pop(POST) == b"made by hand\n"
    before.pop(POST)
    assert after == before


@pytest.mark.parametrize("count", [1, 2, 3])
def test_a_failed_move_while_reconverting_restores_the_old_copy(engine: Engine, count: int) -> None:
    _converted(engine)
    draft = engine.blog / "inbox" / SLUG / "draft"
    (draft / "images" / "diagram.png").write_bytes(PNG_V2)
    (draft / f"{SLUG}.md").write_text(FRONTMATTER + "\nNew body ![d](images/diagram.png)\n", encoding="utf-8")
    before = _tree(engine)
    rc, payload, output = engine.run("convert", SLUG, script=_bootstrap(engine, "fail-target", count))
    assert rc == 1 and _codes(payload) == ["apply_failed"], output
    assert payload["rolled_back"] is True
    assert _tree(engine) == before


# ------------------------------------------------------------------ convert: recovery after an interrupted unit

CRASH = '''\
"""Test bootstrap: run the installed CLI and kill the process at one file operation."""
import os
import runpy
import shutil
import sys

cli, mode, count = sys.argv[1], sys.argv[2], int(sys.argv[3])
seen = {"replace": 0, "rmtree": 0, "rename": 0}
real = {"replace": os.replace, "rmtree": shutil.rmtree, "rename": os.rename}


def wrap(name):
    def call(*args, **kwargs):
        seen[name] += 1
        if mode == f"before-{name}" and seen[name] == count:
            os._exit(9)
        result = real[name](*args, **kwargs)
        if mode == f"after-{name}" and seen[name] == count:
            os._exit(9)
        return result
    return call


os.replace = wrap("replace")
shutil.rmtree = wrap("rmtree")
os.rename = wrap("rename")
sys.argv = [cli, *sys.argv[4:]]
runpy.run_path(cli, run_name="__main__")
'''


def _crash(engine: Engine, mode: str, count: int) -> Path:
    script = engine.tmp / "crash.py"
    script.write_text(CRASH)
    wrapper = engine.tmp / f"run-crash-{mode}-{count}.py"
    wrapper.write_text(
        "import runpy, sys\n"
        f"sys.argv = [{str(script)!r}, {str(engine.cli)!r}, {mode!r}, '{count}', *sys.argv[1:]]\n"
        f"runpy.run_path({str(script)!r}, run_name='__main__')\n"
    )
    return wrapper


def _changed_draft(engine: Engine) -> None:
    draft = engine.blog / "inbox" / SLUG / "draft"
    (draft / "images" / "diagram.png").write_bytes(PNG_V2)
    (draft / "chart.png").write_bytes(PNG)
    (draft / f"{SLUG}.md").write_text(FRONTMATTER + "\nNew body ![d](images/diagram.png) ![c](chart.png)\n",
                                      encoding="utf-8")


DRAFT = f"inbox/{SLUG}/draft/{SLUG}.md"


def _break_draft(engine: Engine) -> None:
    """Make the conversion after recovery fail, so the recovered tree stays visible."""
    draft = engine.blog / DRAFT
    draft.write_text(draft.read_text(encoding="utf-8").replace('lang: "ko"\n', 'lang: "ko"\nsummary: "x"\n'),
                     encoding="utf-8")


def _without_draft(tree: dict[str, bytes]) -> dict[str, bytes]:
    return {k: v for k, v in tree.items() if k != DRAFT}


def test_an_interrupted_reconvert_is_rolled_back_by_the_next_convert(engine: Engine) -> None:
    _converted(engine)
    _changed_draft(engine)
    before = _tree(engine)
    rc, _, output = engine.run("convert", SLUG, script=_crash(engine, "after-replace", 3))
    assert rc == 9, output
    assert _tree(engine) != before
    _break_draft(engine)
    rc, payload, output = engine.run("convert", SLUG)
    assert rc == 1 and _codes(payload) == ["draft_key_not_allowed"], output
    assert _without_draft(_tree(engine)) == _without_draft(before)


def _reconverted_reference(tmp_path: Path) -> dict[str, bytes]:
    """What a finished reconvert leaves, built in a separate world (draft excluded)."""
    folder = tmp_path / "reference"
    folder.mkdir()
    reference = Engine(folder)
    _converted(reference)
    _changed_draft(reference)
    rc, _, output = reference.run("convert", SLUG)
    assert rc == 0, output
    return _without_draft(_tree(reference))


# A reconvert of _changed_draft moves 8 files after the journal is written
# (os.replace 1); os.replace 10 writes the applied mark.
@pytest.mark.parametrize("mode,count", [("before-replace", 1), ("before-replace", 2),
                                        *(("after-replace", n) for n in range(1, 10))])
def test_a_reconvert_stopped_before_the_applied_mark_is_undone(engine: Engine, mode: str, count: int) -> None:
    _converted(engine)
    _changed_draft(engine)
    before = _tree(engine)
    rc, _, output = engine.run("convert", SLUG, script=_crash(engine, mode, count))
    assert rc == 9, output
    _break_draft(engine)
    rc, payload, output = engine.run("convert", SLUG)
    assert rc == 1 and _codes(payload) == ["draft_key_not_allowed"], output
    assert _without_draft(_tree(engine)) == _without_draft(before)


@pytest.mark.parametrize("mode,count", [("after-replace", 10), ("after-rmtree", 1), ("after-rmtree", 2)])
def test_a_reconvert_stopped_after_the_applied_mark_keeps_the_new_copy(
        engine: Engine, tmp_path: Path, mode: str, count: int) -> None:
    _converted(engine)
    _changed_draft(engine)
    rc, _, output = engine.run("convert", SLUG, script=_crash(engine, mode, count))
    assert rc == 9, output
    _break_draft(engine)
    rc, payload, output = engine.run("convert", SLUG)
    assert rc == 1 and _codes(payload) == ["draft_key_not_allowed"], output
    assert _without_draft(_tree(engine)) == _reconverted_reference(tmp_path)


# A first conversion moves 4 files (os.replace 2 to 5); os.replace 6 writes the applied mark.
@pytest.mark.parametrize("count", [1, 2, 3, 4, 5])
def test_a_first_conversion_stopped_midway_is_undone(engine: Engine, count: int) -> None:
    _draft(engine)
    before = _tree(engine)
    rc, _, output = engine.run("convert", SLUG, script=_crash(engine, "after-replace", count))
    assert rc == 9, output
    _break_draft(engine)
    rc, payload, output = engine.run("convert", SLUG)
    assert rc == 1 and _codes(payload) == ["draft_key_not_allowed"], output
    assert _without_draft(_tree(engine)) == _without_draft(before)


def test_recovery_reports_what_it_did(engine: Engine) -> None:
    _draft(engine)
    rc, _, output = engine.run("convert", SLUG, script=_crash(engine, "after-replace", 3))
    assert rc == 9, output
    rc, payload, output = engine.run("convert", SLUG)
    assert (rc, payload["errors"], payload["recovered"]) == (0, [], "rolled_back"), output
    assert (engine.blog / POST).read_text(encoding="utf-8") == EXPECTED_POST


@pytest.mark.parametrize("second", [1, 2, 3])
def test_a_rollback_stopped_again_is_finished_later(engine: Engine, second: int) -> None:
    _converted(engine)
    _changed_draft(engine)
    before = _tree(engine)
    rc, _, output = engine.run("convert", SLUG, script=_crash(engine, "after-replace", 7))
    assert rc == 9, output
    rc, _, output = engine.run("convert", SLUG, script=_crash(engine, "after-replace", second))
    assert rc == 9, output
    _break_draft(engine)
    rc, payload, output = engine.run("convert", SLUG)
    assert rc == 1 and _codes(payload) == ["draft_key_not_allowed"], output
    assert _without_draft(_tree(engine)) == _without_draft(before)


def _stopped_reconvert(engine: Engine, count: int) -> None:
    _converted(engine)
    _changed_draft(engine)
    rc, _, output = engine.run("convert", SLUG, script=_crash(engine, "after-replace", count))
    assert rc == 9, output


LOCK = f"inbox/{SLUG}/blogkit.lock"


def _unlocked(tree: dict[str, bytes]) -> dict[str, bytes]:
    """The tree without the lock a killed run leaves (the next command clears it)."""
    return {k: v for k, v in tree.items() if k != LOCK}


def _lose_first_backup(e: Engine) -> None:
    """Lose the earliest backup while a later target still has its own, so a partial undo would show."""
    backups = sorted((e.blog / "inbox" / SLUG / ".convert-backup").iterdir(), key=lambda p: int(p.name))
    assert len(backups) == 2
    backups[0].unlink()


def _unknown_new_target(e: Engine) -> None:
    (e.blog / ASSETS / "chart.png").write_bytes(b"unknown bytes\n")


def _unknown_post(e: Engine) -> None:
    (e.blog / POST).write_text("made by hand\n")


def _unknown_backup(e: Engine) -> None:
    folder = e.blog / "inbox" / SLUG / ".convert-backup"
    for index in range(10):
        if not (folder / str(index)).exists():
            (folder / str(index)).write_bytes(b"unknown bytes\n")


def _unknown_staged(e: Engine) -> None:
    for staged in (e.blog / "inbox" / SLUG / ".convert-stage").iterdir():
        staged.write_bytes(b"unknown bytes\n")


def _stray(folder: str):
    """A file the journal does not name, left in the unit's stage or backup folder."""
    def change(e: Engine) -> None:
        path = e.blog / "inbox" / SLUG / folder
        path.mkdir(exist_ok=True)
        (path / "notes").write_bytes(b"not the journal's\n")
    return change


def _tampered_journal(e: Engine) -> None:
    journal = e.blog / "inbox" / SLUG / "convert-journal.json"
    data = json.loads(journal.read_text(encoding="utf-8"))
    data["targets"][0]["path"] = "README.md"
    journal.write_text(json.dumps(data), encoding="utf-8")


# After os.replace 3 the new chart is in place and the old diagram is in its backup;
# after os.replace 5 the old post is in its backup too.
@pytest.mark.parametrize("count,change,code", [
    (5, _lose_first_backup, "convert_recovery_blocked"),
    (3, _unknown_new_target, "convert_recovery_blocked"),
    (3, _unknown_post, "convert_recovery_blocked"),
    (3, _unknown_backup, "convert_recovery_blocked"),
    (3, _unknown_staged, "convert_recovery_blocked"),
    (3, _tampered_journal, "journal_invalid"),
    (3, _stray(".convert-backup"), "convert_recovery_blocked"),
    (3, _stray(".convert-stage"), "convert_recovery_blocked"),
    (10, _stray(".convert-backup"), "convert_recovery_blocked"),
], ids=["lost backup", "unknown new target", "unknown post", "unknown backup", "unknown staged", "tampered journal",
        "stray backup file", "stray stage file", "stray file after the applied mark"])
def test_recovery_that_cannot_be_judged_changes_nothing(engine: Engine, count: int, change, code: str) -> None:
    _stopped_reconvert(engine, count)
    change(engine)
    (engine.blog / "README.md").write_text("fake readme\n")
    before = _tree(engine)
    rc, payload, output = engine.run("convert", SLUG)
    assert rc == 1 and _codes(payload) == [code], output
    assert payload["errors"][0]["path"] == f"inbox/{SLUG}/convert-journal.json"
    assert _tree(engine) == _unlocked(before)
    _no_leak(output)


@pytest.mark.parametrize("command", [
    ["check", SLUG], ["status", SLUG], ["publish", SLUG],
    ["approve", SLUG, "--hash", "0" * 64, "--warnings", "0" * 64],
], ids=["check", "status", "publish", "approve"])
def test_other_commands_refuse_an_interrupted_slug(engine: Engine, command: list[str]) -> None:
    _stopped_reconvert(engine, 5)
    before = _tree(engine)
    rc, payload, output = engine.run(*command)
    assert rc == 1 and _codes(payload) == ["convert_interrupted"], output
    assert _unlocked(_tree(engine)) == _unlocked(before)


# ------------------------------------------------------------------ check (gates 1 to 4) and status

FAKE_TERM = "가짜금칙어"


def _terms_file(engine: Engine, text: str = f"# fake\n{FAKE_TERM}\n") -> None:
    path = engine.home / ".config" / "blog-kit" / "confidential.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _checkable(engine: Engine) -> None:
    (engine.blog / ".gitignore").write_text(f"{PREVIEW_DIR}/\n")
    _draft(engine)
    rc, _, output = engine.run("convert", SLUG)
    assert rc == 0, output
    _terms_file(engine)


def _evidence(engine: Engine) -> dict:
    return json.loads((engine.blog / EVIDENCE).read_text(encoding="utf-8"))


def _write_evidence(engine: Engine, evidence: dict) -> None:
    (engine.blog / EVIDENCE).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


def _review(engine: Engine, p0: int = 0) -> None:
    """The M2c reviewer's record for the current hash, written by the test in its place.

    Like every record writer, it starts the records afresh when the hash moved on.
    """
    evidence = _evidence(engine)
    current = _verifier().content_hash(engine.blog, SLUG)
    if evidence["content_hash"] != current:
        evidence.update(content_hash=current, engine_version=VERSION, records=[])
    evidence["records"] = [r for r in evidence["records"] if r["kind"] != "review"] + [
        {"kind": "review", "p0": p0, "content_hash": current, "engine_version": VERSION}]
    _write_evidence(engine, evidence)


def _gates(payload: dict) -> dict[int, dict]:
    return {gate["gate"]: gate for gate in payload["gates"]}


def _gate_codes(payload: dict, number: int) -> list[str]:
    return [error["code"] for error in _gates(payload)[number]["errors"]]


def test_status_after_convert_lists_every_missing_record(engine: Engine) -> None:
    _checkable(engine)
    rc, payload, output = engine.run("status", SLUG)
    assert (rc, payload["errors"]) == (0, []), output
    current = _verifier().content_hash(engine.blog, SLUG)
    assert (payload["content_hash"], payload["evidence_hash"]) == (current, current)
    assert payload["gates"] == {} and payload["warnings"] == []
    assert payload["warnings_digest"] == _verifier().warnings_digest([])
    assert payload["missing"] == [*(f"gate_{n}_missing" for n in range(1, 7)),
                                  "review_missing", "learning_missing", "approval_missing"]
    assert not (engine.blog / "inbox" / SLUG / "blogkit.lock").exists()


# The fixture post's mailto link is an email address on this line, so gate 5 warns there.
EMAIL_LINE = EXPECTED_POST.split("\n").index(next(l for l in EXPECTED_POST.split("\n") if "mailto:" in l)) + 1
EMAIL_WARNING = f"5:email:{POST}:{EMAIL_LINE}"


def test_check_records_gates_1_to_6_and_needs_a_review(engine: Engine) -> None:
    _checkable(engine)
    rc, payload, output = engine.run("check", SLUG)
    assert rc == 1 and _codes(payload) == ["gates_failed"], output
    gates = _gates(payload)
    assert sorted(gates) == [1, 2, 3, 4, 5, 6]
    assert [gates[n]["passed"] for n in range(1, 7)] == [True, True, True, False, True, True]
    assert _gate_codes(payload, 4) == ["review_missing"]
    current = _verifier().content_hash(engine.blog, SLUG)
    records = {r["gate"]: r for r in _evidence(engine)["records"] if r["kind"] == "gate"}
    assert {n: (r["passed"], r["warnings"], r["content_hash"], r["engine_version"]) for n, r in records.items()} == {
        1: (True, [], current, VERSION), 2: (True, [], current, VERSION),
        3: (True, [], current, VERSION), 4: (False, [], current, VERSION),
        5: (True, [EMAIL_WARNING], current, VERSION), 6: (True, [], current, VERSION)}
    _verifier().validate_evidence(_evidence(engine))
    _no_leak(output)
    _no_leak((engine.blog / EVIDENCE).read_text(encoding="utf-8"))
    assert "example.com" not in output


def test_check_passes_every_gate_with_a_clean_review(engine: Engine) -> None:
    _checkable(engine)
    _review(engine)
    rc, payload, output = engine.run("check", SLUG)
    assert (rc, payload["errors"]) == (0, []), output
    assert [_gates(payload)[n]["passed"] for n in range(1, 7)] == [True] * 6
    calls = (engine.tmp / "npx-calls.txt").read_text().splitlines()
    # astro check runs without the draft preview variable; the preview build runs with it, into its own folder.
    assert calls == ["--no-install astro check drafts=", f"--no-install astro build --outDir {PREVIEW_DIR} drafts=1"]
    rc, status, output = engine.run("status", SLUG)
    assert rc == 0, output
    assert status["gates"] == {str(n): {"passed": True, "warnings": [EMAIL_WARNING] if n == 5 else []}
                               for n in range(1, 7)}
    assert status["warnings"] == [EMAIL_WARNING]
    assert status["missing"] == ["learning_missing", "approval_missing"]


def test_a_review_with_p0_fails_gate_4(engine: Engine) -> None:
    _checkable(engine)
    _review(engine, p0=2)
    rc, payload, output = engine.run("check", SLUG)
    assert rc == 1 and _gate_codes(payload, 4) == ["review_has_p0"], output


def test_check_and_status_need_a_converted_copy(engine: Engine) -> None:
    _draft(engine)
    _terms_file(engine)
    for command in ("check", "status"):
        rc, payload, output = engine.run(command, SLUG)
        assert rc == 1 and _codes(payload) == ["evidence_missing"], output


@pytest.mark.parametrize("tools,codes", [
    (["npx"], ["node_missing"]),
    (["node"], ["npx_missing"]),
], ids=["node", "npx"])
def test_a_missing_tool_fails_gate_1(engine: Engine, tools: list[str], codes: list[str]) -> None:
    _checkable(engine)
    _review(engine)
    engine.isolated_path = True
    engine.tools = tools
    rc, payload, output = engine.run("check", SLUG)
    assert rc == 1 and _codes(payload) == ["gates_failed"], output
    assert _gate_codes(payload, 1) == codes
    assert _gates(payload)[1]["passed"] is False


def _symlinked_terms(e: Engine) -> None:
    real = e.tmp / "real-terms.txt"
    real.write_text(FAKE_TERM + "\n", encoding="utf-8")
    path = e.home / ".config" / "blog-kit" / "confidential.txt"
    path.unlink()
    path.symlink_to(real)


@pytest.mark.parametrize("change,code", [
    (lambda e: (e.home / ".config" / "blog-kit" / "confidential.txt").unlink(), "confidential_list_missing"),
    (lambda e: _terms_file(e, "# only a comment\n"), "confidential_list_empty"),
    (_symlinked_terms, "confidential_list_symlink"),
    (lambda e: _terms_file(e, "x" * (300 * 1024)), "confidential_list_too_large"),
], ids=["missing", "empty", "symlink", "too large"])
def test_an_unusable_term_list_fails_gate_1(engine: Engine, change, code: str) -> None:
    _checkable(engine)
    _review(engine)
    change(engine)
    rc, payload, output = engine.run("check", SLUG)
    assert rc == 1 and _gate_codes(payload, 1) == [code], output
    assert FAKE_TERM not in output


POST_LINES = EXPECTED_POST.count("\n")


def _append_post(e: Engine, text: str) -> None:
    with (e.blog / POST).open("a", encoding="utf-8") as handle:
        handle.write(text)


GATE_2_BLOCKS = [
    ("disallowed tag", '<iframe src="https://example.com/x"></iframe>\n', "html_tag_not_allowed"),
    ("event handler", '<p onclick="steal()">hi</p>\n', "html_attribute_not_allowed"),
    ("style attribute", '<p style="color: red">hi</p>\n', "html_attribute_not_allowed"),
    ("javascript href", '<a href="javascript:alert(1)">x</a>\n', "html_url_not_allowed"),
    ("data image", '<img src="data:image/png;base64,AAAA" alt="x">\n', "html_url_not_allowed"),
    ("inline svg script", "<svg><script>alert(1)</script></svg>\n", "html_tag_not_allowed"),
    ("inline svg foreign object", "<svg><foreignObject></foreignObject></svg>\n", "html_tag_not_allowed"),
    ("markdown javascript link", "[x](javascript:alert(1))\n", "reference_scheme_not_allowed"),
    ("javascript autolink", "See <javascript:alert(1)> here.\n", "reference_scheme_not_allowed"),
    ("file autolink", "<file:///etc/hosts>\n", "reference_scheme_not_allowed"),
    ("markdown file outside assets", "![x](../../assets/posts/other/a.png)\n", "reference_not_in_assets"),
    ("markdown missing asset", "![x](../../assets/posts/first-post/none.png)\n", "reference_not_in_assets"),
    ("raw html file outside assets", '<img src="../../assets/posts/other/a.png" alt="x">\n', "reference_not_in_assets"),
    ("scheme-less authority", "![x](//example.com/a.png)\n", "reference_unsafe"),
]


@pytest.mark.parametrize("text,code", [case[1:] for case in GATE_2_BLOCKS], ids=[case[0] for case in GATE_2_BLOCKS])
def test_body_safety_violations_fail_gate_2(engine: Engine, text: str, code: str) -> None:
    _checkable(engine)
    _append_post(engine, "\n" + text)
    _review(engine)
    rc, payload, output = engine.run("check", SLUG)
    assert rc == 1, output
    assert _gate_codes(payload, 2) == [code]
    assert _gates(payload)[2]["errors"][0] == {"code": code, "path": POST, "line": POST_LINES + 2}
    assert "example.com" not in output and "steal" not in output


@pytest.mark.parametrize("svg,code,line", [
    ('<svg xmlns="http://www.w3.org/2000/svg">\n<script>alert(1)</script>\n</svg>\n', "html_tag_not_allowed", 2),
    ('<svg xmlns="http://www.w3.org/2000/svg" onload="x()">\n</svg>\n', "html_attribute_not_allowed", 1),
    ('<svg xmlns="http://www.w3.org/2000/svg">\n<a href="javascript:x()"><text>t</text></a>\n</svg>\n',
     "html_url_not_allowed", 2),
    ('<!DOCTYPE svg [<!ENTITY x "y">]>\n<svg></svg>\n', "html_declaration_not_allowed", 1),
], ids=["script", "onload", "javascript link", "doctype"])
def test_an_unsafe_svg_asset_fails_gate_2(engine: Engine, svg: str, code: str, line: int) -> None:
    _checkable(engine)
    (engine.blog / ASSETS / "logo.svg").write_text(svg, encoding="utf-8")
    _review(engine)
    rc, payload, output = engine.run("check", SLUG)
    assert rc == 1, output
    assert _gates(payload)[2]["errors"] == [{"code": code, "path": f"{ASSETS}/logo.svg", "line": line}]


def test_html_inside_code_autolinks_and_comments_pass_gate_2(engine: Engine) -> None:
    _checkable(engine)
    _append_post(engine, (
        "\n```html\n<script>alert(1)</script>\n<p onclick=\"x()\">a</p>\n```\n\n"
        "Inline `<iframe src=\"javascript:x\">` stays text.\n\n"
        "See <https://example.com/page> and <someone@example.com>.\n\n"
        "<!-- a note -->\n"
        '<figure class="wide"><img src="../../assets/posts/first-post/diagram.png" alt="d">'
        "<figcaption>Fig</figcaption></figure>\n"
        '<svg viewBox="0 0 10 10" width="10"><rect x="1" y="1" width="2" height="2" fill="red"/></svg>\n'
    ))
    _review(engine)
    rc, payload, output = engine.run("check", SLUG)
    assert (rc, payload["errors"]) == (0, []), output


@pytest.mark.parametrize("text,offset", [
    ("<div>\n```\n<script>alert(1)</script>\n```\n</div>\n", 2),
    ("<div>\n`<script>alert(1)</script>`\n</div>\n", 1),
    ("<div>`<script>alert(1)</script>`</div>\n", 0),
    ("> <div>\n> `<script>alert(1)</script>`\n", 1),
    ("<!-- note\n```\n-->\n<script>alert(1)</script>\n", 3),
], ids=["fence in block", "inline code in block", "one-line block", "block in quote", "fence in comment"])
def test_backticks_inside_a_raw_html_block_are_not_code(engine: Engine, text: str, offset: int) -> None:
    """CommonMark reads a raw HTML block as HTML up to its end; backticks in it are not code."""
    _checkable(engine)
    _append_post(engine, "\n" + text)
    _review(engine)
    rc, payload, output = engine.run("check", SLUG)
    assert rc == 1, output
    assert {"code": "html_tag_not_allowed", "path": POST, "line": POST_LINES + 2 + offset} in _gates(payload)[2]["errors"]


def test_backticks_inside_an_inline_html_tag_are_not_code(engine: Engine) -> None:
    """CommonMark reads a complete inline tag before code spans, so a backtick in an attribute opens nothing."""
    _checkable(engine)
    _append_post(engine, '\nSee <span class="`">prefix<script>alert(1)</script>end` here.\n')
    _review(engine)
    rc, payload, output = engine.run("check", SLUG)
    assert rc == 1, output
    assert {"code": "html_tag_not_allowed", "path": POST, "line": POST_LINES + 2} in _gates(payload)[2]["errors"]


@pytest.mark.parametrize("unit", ["<!--`", '<a b="`', "` x ", "``` ` <x `", "<`", "<a '`\" ", "\\\\\\`"],
                         ids=["open comments", "open quotes", "ticks", "mixed runs", "bare tags", "mixed quotes",
                              "escaped ticks"])
def test_reading_code_and_tags_stays_linear_on_a_large_post(engine: Engine, unit: str) -> None:
    _checkable(engine)
    _append_post(engine, "\n" + unit * (900 * 1024 // len(unit)) + "\n")
    _review(engine)
    engine.timeout = 60
    rc, payload, output = engine.run("check", SLUG)
    assert payload.get("command") == "check", output


@pytest.mark.parametrize("text", ["\\`<script>alert(1)</script>`\n", "\\``<img src=x onerror=alert(1)>``\n"],
                         ids=["escaped backtick", "escaped double backtick"])
def test_an_escaped_backtick_opens_no_code(engine: Engine, text: str) -> None:
    _checkable(engine)
    _append_post(engine, "\n" + text)
    _review(engine)
    rc, payload, output = engine.run("check", SLUG)
    assert rc == 1, output
    codes = {error["code"] for error in _gates(payload)[2]["errors"]}
    assert codes & {"html_tag_not_allowed", "html_attribute_not_allowed"}, payload


def test_a_backtick_after_an_escaped_backslash_still_opens_code(engine: Engine) -> None:
    _checkable(engine)
    _append_post(engine, "\nA path \\\\`<script>` is code.\n")
    _review(engine)
    rc, payload, output = engine.run("check", SLUG)
    assert (rc, payload["errors"]) == (0, []), output


def test_code_after_a_raw_html_block_ends_is_still_code(engine: Engine) -> None:
    _checkable(engine)
    _append_post(engine, "\n<div>\n</div>\n\n```html\n<script>alert(1)</script>\n```\n\n"
                         "Inline `<script>` after <span>text</span> stays code.\n")
    _review(engine)
    rc, payload, output = engine.run("check", SLUG)
    assert (rc, payload["errors"]) == (0, []), output


def test_a_failing_astro_check_fails_gate_3_with_repo_locations_only(engine: Engine) -> None:
    _checkable(engine)
    _review(engine)
    engine.astro_exit = 1
    rc, payload, output = engine.run("check", SLUG)
    assert rc == 1, output
    assert _gates(payload)[3]["errors"] == [
        {"code": "astro_check_failed", "path": ""}, {"code": "astro_check_location", "path": POST, "line": 2}]
    _no_leak(output)
    _no_leak((engine.blog / EVIDENCE).read_text(encoding="utf-8"))


@pytest.mark.parametrize("relative", [POST, f"{ASSETS}/logo.svg"], ids=["post", "asset"])
def test_a_change_while_checking_records_nothing(engine: Engine, relative: str) -> None:
    _checkable(engine)
    _review(engine)
    engine.astro_touch = (relative, "\n<!-- changed while checking -->\n")
    before = (engine.blog / EVIDENCE).read_bytes()
    rc, payload, output = engine.run("check", SLUG)
    assert rc == 1 and _codes(payload) == ["content_changed_during_check"], output
    assert (engine.blog / EVIDENCE).read_bytes() == before
    assert "gates" not in payload


def test_a_hash_change_starts_the_records_afresh_and_keeps_public_copy(engine: Engine) -> None:
    _checkable(engine)
    _review(engine)
    rc, _, output = engine.run("check", SLUG)
    assert rc == 0, output
    kept = _evidence(engine)["public_copy"]
    _append_post(engine, "\nOne more line.\n")
    rc, payload, output = engine.run("check", SLUG)
    assert rc == 1 and _gate_codes(payload, 4) == ["review_missing"], output
    evidence = _evidence(engine)
    current = _verifier().content_hash(engine.blog, SLUG)
    assert evidence["content_hash"] == current and evidence["public_copy"] == kept
    assert sorted(r["gate"] for r in evidence["records"]) == [1, 2, 3, 4, 5, 6]
    assert all(r["content_hash"] == current for r in evidence["records"])


def test_a_review_for_another_hash_does_not_count(engine: Engine) -> None:
    _checkable(engine)
    _review(engine)
    evidence = _evidence(engine)
    for record in evidence["records"]:
        if record["kind"] == "review":
            record["content_hash"] = "f" * 64
    _write_evidence(engine, evidence)
    rc, payload, output = engine.run("check", SLUG)
    assert rc == 1 and _gate_codes(payload, 4) == ["review_missing"], output


# ------------------------------------------------------------------ check: gates 5 and 6


def test_a_confidential_term_fails_gate_5_with_file_and_line_only(engine: Engine) -> None:
    _checkable(engine)
    _append_post(engine, f"\n이 줄에 {FAKE_TERM}가 있다\n")
    (engine.blog / ASSETS / "logo.svg").write_text(f"<svg>\n<text>{FAKE_TERM}</text>\n</svg>\n", encoding="utf-8")
    _review(engine)
    rc, payload, output = engine.run("check", SLUG)
    assert rc == 1, output
    assert _gates(payload)[5]["errors"] == [
        {"code": "confidential_term", "path": POST, "line": POST_LINES + 2},
        {"code": "confidential_term", "path": f"{ASSETS}/logo.svg", "line": 2},
    ]
    assert FAKE_TERM not in output and FAKE_TERM not in (engine.blog / EVIDENCE).read_text(encoding="utf-8")


def test_heuristics_pass_gate_5_as_warning_ids_in_the_digest(engine: Engine) -> None:
    _checkable(engine)
    _append_post(engine, "\n서버 10.1.2.3 에서 30% 줄였다\n")
    _review(engine)
    rc, payload, output = engine.run("check", SLUG)
    line = POST_LINES + 2
    expected = [EMAIL_WARNING, f"5:number_unit:{POST}:{line}", f"5:private_ip:{POST}:{line}"]
    gate = _gates(payload)[5]
    assert (gate["passed"], gate["errors"], gate["warnings"]) == (True, [], expected), output
    rc, status, output = engine.run("status", SLUG)
    assert status["warnings"] == expected
    assert status["warnings_digest"] == _verifier().warnings_digest(expected)
    assert "10.1.2.3" not in output


def test_an_unusable_term_list_fails_gate_5_too(engine: Engine) -> None:
    _checkable(engine)
    _review(engine)
    (engine.home / ".config" / "blog-kit" / "confidential.txt").unlink()
    rc, payload, output = engine.run("check", SLUG)
    assert rc == 1 and _gate_codes(payload, 5) == ["confidential_list_missing"], output


def _line_of(text: str) -> int:
    return next(i for i, line in enumerate(EXPECTED_POST.split("\n"), 1) if text in line)


def _url_digest(url: str) -> str:
    return _sha(url.encode("utf-8"))[:16]


def _gate_6(engine: Engine) -> dict:
    _review(engine)
    rc, payload, output = engine.run("check", SLUG)
    assert "example.com" not in output and PLANTED not in output, output
    return _gates(payload)[6]


def _without(page: str):
    def change(e: Engine) -> None:
        del e.preview[page]
    return change


def _page(page: str, html: str):
    def change(e: Engine) -> None:
        e.preview[page] = html
    return change


GATE_6_BLOCKS = [
    ("post page missing", _without("posts/first-post/index.html"),
     [{"code": "preview_page_missing", "path": POST}]),
    ("broken internal link", _without("about/index.html"),
     [{"code": "internal_link_broken", "path": POST, "line": _line_of("(/about/)")}]),
    ("broken anchor", _page("posts/first-post/index.html", "<html><body><h1>no id</h1></body></html>"),
     [{"code": "anchor_broken", "path": POST, "line": _line_of("(#heading)")}]),
    ("missing local image", _page("posts/first-post/index.html",
                                  '<html><body><h1 id="heading">H</h1><img src="/_astro/gone.png"></body></html>'),
     [{"code": "image_missing", "path": POST}]),
]


@pytest.mark.parametrize("change,errors", [case[1:] for case in GATE_6_BLOCKS], ids=[case[0] for case in GATE_6_BLOCKS])
def test_preview_problems_fail_gate_6(engine: Engine, change, errors: list[dict]) -> None:
    _checkable(engine)
    change(engine)
    gate = _gate_6(engine)
    assert (gate["passed"], gate["errors"]) == (False, errors)


def test_internal_links_with_anchors_resolve_in_the_preview(engine: Engine) -> None:
    _checkable(engine)
    _append_post(engine, "\n[ok](/posts/other/#top) and [bad](/posts/other/#nope) and [also](/posts/other)\n")
    gate = _gate_6(engine)
    assert gate["errors"] == [{"code": "anchor_broken", "path": POST, "line": POST_LINES + 2}]


EXTERNAL_LINK = "https://example.com/a"
EXTERNAL_IMAGE = "https://example.com/remote.png"


@pytest.mark.parametrize("net,code,url,text", [
    ({"answers": {EXTERNAL_IMAGE: 404}}, "link_permanent", EXTERNAL_IMAGE, "(https://example.com/remote.png)"),
    ({"hosts": {"example.com": "10.0.0.1"}}, "link_policy", EXTERNAL_LINK, "(https://example.com/a)"),
], ids=["external image 404", "private address"])
def test_a_blocked_external_url_fails_gate_6_by_digest(engine: Engine, net: dict, code: str, url: str,
                                                        text: str) -> None:
    _checkable(engine)
    engine.net.update(hosts=net.get("hosts", {}), answers=net.get("answers", {}))
    gate = _gate_6(engine)
    assert {"code": code, "path": POST, "line": _line_of(text), "url": _url_digest(url)} in gate["errors"]
    assert gate["passed"] is False


def test_an_autolink_goes_through_the_external_link_check(engine: Engine) -> None:
    _checkable(engine)
    url = "https://autolink.example.com/private"
    _append_post(engine, f"\nSee <{url}>.\n")
    engine.net["hosts"] = {"autolink.example.com": "10.0.0.1"}
    gate = _gate_6(engine)
    assert gate["errors"] == [{"code": "link_policy", "path": POST, "line": POST_LINES + 2, "url": _url_digest(url)}]


def test_a_transient_external_failure_is_a_gate_6_warning(engine: Engine) -> None:
    _checkable(engine)
    engine.net["answers"] = {EXTERNAL_LINK: 503}
    gate = _gate_6(engine)
    line = _line_of("(https://example.com/a)")
    assert (gate["passed"], gate["errors"]) == (True, [])
    assert gate["warnings"] == [f"6:link_transient:{POST}:{line}:{_url_digest(EXTERNAL_LINK)}"]
    calls = (engine.tmp / "net-calls.txt").read_text().splitlines()
    assert calls.count(f"HEAD {EXTERNAL_LINK}") == 3
    assert not any("mailto" in call for call in calls)


def test_a_listed_403_passes_gate_6(engine: Engine) -> None:
    _checkable(engine)
    engine.net["answers"] = {EXTERNAL_LINK: 403}
    (engine.blog / "inbox" / SLUG / "draft" / "external-links.allowed").write_text(EXTERNAL_LINK + "\n")
    gate = _gate_6(engine)
    assert (gate["passed"], gate["errors"]) == (True, [])


def _preview_symlink(e: Engine) -> None:
    real = e.tmp / "elsewhere"
    real.mkdir()
    (e.blog / PREVIEW_DIR).symlink_to(real)


@pytest.mark.parametrize("change,code", [
    (lambda e: setattr(e, "build_exit", 1), "preview_build_failed"),
    (lambda e: (e.blog / ".gitignore").unlink(), "preview_not_ignored"),
    (_preview_symlink, "symlink_refused"),
], ids=["build fails", "not ignored", "symlinked folder"])
def test_a_preview_build_that_cannot_be_used_fails_gate_6(engine: Engine, change, code: str) -> None:
    _checkable(engine)
    change(engine)
    gate = _gate_6(engine)
    assert gate["passed"] is False and gate["errors"][0]["code"] == code


def test_the_preview_build_replaces_an_old_preview_and_leaves_dist_alone(engine: Engine) -> None:
    _checkable(engine)
    (engine.blog / PREVIEW_DIR / "posts" / "stale").mkdir(parents=True)
    (engine.blog / PREVIEW_DIR / "posts" / "stale" / "index.html").write_text("old")
    (engine.blog / "dist").mkdir()
    (engine.blog / "dist" / "index.html").write_text("deployed")
    gate = _gate_6(engine)
    assert gate["passed"] is True
    assert not (engine.blog / PREVIEW_DIR / "posts" / "stale").exists()
    assert (engine.blog / "dist" / "index.html").read_text() == "deployed"


# ------------------------------------------------------------------ approve and publish


def _learning(engine: Engine, passed: bool = True) -> None:
    """The M2c learning check's record for the current hash, written by the test in its place."""
    evidence = _evidence(engine)
    current = _verifier().content_hash(engine.blog, SLUG)
    assert evidence["content_hash"] == current
    evidence["records"] = [r for r in evidence["records"] if r["kind"] != "learning"] + [
        {"kind": "learning", "passed": passed, "content_hash": current, "engine_version": VERSION}]
    _write_evidence(engine, evidence)


def _checked(engine: Engine) -> dict:
    """Converted, reviewed, checked through all six gates and learning-checked; returns status."""
    _checkable(engine)
    _review(engine)
    rc, _, output = engine.run("check", SLUG)
    assert rc == 0, output
    _learning(engine)
    rc, status, output = engine.run("status", SLUG)
    assert rc == 0, output
    return status


def _approve(engine: Engine, content_hash: str, digest: str) -> tuple[int, dict, str]:
    return engine.run("approve", SLUG, "--hash", content_hash, "--warnings", digest)


def test_approve_records_the_approval_for_hash_and_warnings(engine: Engine) -> None:
    status = _checked(engine)
    rc, payload, output = _approve(engine, status["content_hash"], status["warnings_digest"])
    assert (rc, payload["errors"]) == (0, []), output
    approvals = [r for r in _evidence(engine)["records"] if r["kind"] == "approval"]
    assert approvals == [{"kind": "approval", "warnings_digest": status["warnings_digest"],
                          "content_hash": status["content_hash"], "engine_version": VERSION}]
    rc, status, output = engine.run("status", SLUG)
    assert status["missing"] == [], output


@pytest.mark.parametrize("change,code", [
    (lambda s: ("0" * 64, s["warnings_digest"]), "hash_mismatch"),
    (lambda s: (s["content_hash"][:16], s["warnings_digest"]), "hash_mismatch"),
    (lambda s: (s["content_hash"], _verifier().warnings_digest([])), "warnings_mismatch"),
], ids=["other hash", "short hash", "other warnings"])
def test_approve_refuses_arguments_that_do_not_match(engine: Engine, change, code: str) -> None:
    status = _checked(engine)
    before = _evidence(engine)
    rc, payload, output = _approve(engine, *change(status))
    assert rc == 1 and _codes(payload) == [code], output
    assert _evidence(engine) == before


def _no_learning(e: Engine) -> None:
    evidence = _evidence(e)
    evidence["records"] = [r for r in evidence["records"] if r["kind"] != "learning"]
    _write_evidence(e, evidence)


def _failed_gate(e: Engine) -> None:
    evidence = _evidence(e)
    for record in evidence["records"]:
        if record.get("gate") == 3:
            record["passed"] = False
    _write_evidence(e, evidence)


@pytest.mark.parametrize("change,gap", [
    (_no_learning, "learning_missing"),
    (lambda e: _learning(e, passed=False), "learning_not_passed"),
    (_failed_gate, "gate_3_not_passed"),
], ids=["no learning", "learning failed", "gate failed"])
def test_approve_needs_every_gate_and_the_learning_check(engine: Engine, change, gap: str) -> None:
    status = _checked(engine)
    change(engine)
    before = _evidence(engine)
    rc, payload, output = _approve(engine, status["content_hash"], status["warnings_digest"])
    assert rc == 1 and _codes(payload) == ["evidence_incomplete"] and gap in payload["missing"], output
    assert _evidence(engine) == before


def _git(engine: Engine, *args: str) -> str:
    result = subprocess.run([REAL_GIT, "-C", str(engine.blog), *args], capture_output=True, text=True, check=True)
    return result.stdout


def _commit_assets(engine: Engine) -> None:
    _git(engine, "config", "user.email", "tester@example.invalid")
    _git(engine, "config", "user.name", "Tester")
    _git(engine, "add", "--", ASSETS)
    _git(engine, "commit", "-q", "-m", "assets")


def _approved(engine: Engine) -> dict:
    status = _checked(engine)
    rc, _, output = _approve(engine, status["content_hash"], status["warnings_digest"])
    assert rc == 0, output
    _commit_assets(engine)
    return status


def _verify_source(engine: Engine) -> dict:
    """The blog repo's verifier copy in source mode, as the site's prebuild runs it."""
    env = {k: v for k, v in os.environ.items() if k != "BLOGKIT_INCLUDE_DRAFTS"}
    result = subprocess.run([sys.executable, str(engine.copy), "--repo", str(engine.blog)],
                            capture_output=True, text=True, env=env)
    return json.loads(result.stdout)


def test_the_whole_path_publishes_the_post_and_evidence_only(engine: Engine) -> None:
    outputs = []
    _checkable(engine)
    _review(engine)
    for command in (["check", SLUG], ["status", SLUG]):
        rc, payload, output = engine.run(*command)
        outputs.append(output)
        assert rc == 0, output
    _learning(engine)
    rc, status, output = engine.run("status", SLUG)
    rc, payload, output = _approve(engine, status["content_hash"], status["warnings_digest"])
    outputs.append(output)
    assert rc == 0, output
    _commit_assets(engine)
    rc, payload, output = engine.run("publish", SLUG)
    outputs.append(output)
    assert (rc, payload["errors"]) == (0, []), output
    assert _git(engine, "log", "-1", "--format=%s") == f"publish: {SLUG}\n"
    assert sorted(_git(engine, "show", "--name-only", "--format=", "HEAD").split()) == sorted([EVIDENCE, POST])
    assert (engine.blog / POST).read_text(encoding="utf-8") == EXPECTED_POST.replace("draft: true", "draft: false")
    assert payload["commit"] == _git(engine, "rev-parse", "HEAD").strip()
    report = _verify_source(engine)
    assert (report["status"], report["public"], report["errors"]) == ("ok", [SLUG], [])
    for output in outputs:
        _no_leak(output)
        assert "example.com" not in output and FAKE_TERM not in output
    _no_leak((engine.blog / EVIDENCE).read_text(encoding="utf-8"))


def test_a_failed_evidence_write_is_a_json_error_and_leaves_no_temp_file(engine: Engine) -> None:
    _checkable(engine)
    _review(engine)
    before = (engine.blog / EVIDENCE).read_bytes()
    rc, payload, output = engine.run("check", SLUG, script=_bootstrap(engine, "fail-evidence"))
    assert (rc, _codes(payload)) == (1, ["evidence_write_failed"]), output
    assert "Traceback" not in output and "injected" not in output and str(engine.blog) not in output
    assert (engine.blog / EVIDENCE).read_bytes() == before
    assert not (engine.blog / (EVIDENCE + ".tmp")).exists()


def test_an_edit_just_before_the_swap_is_kept_and_stops_publishing(engine: Engine) -> None:
    _approved(engine)
    head = _git(engine, "rev-parse", "HEAD")
    rc, payload, output = engine.run("publish", SLUG, script=_bootstrap(engine, "edit-before-swap"))
    assert (rc, _codes(payload)) == (1, ["content_changed_during_check"]), output
    assert (engine.blog / POST).read_text(encoding="utf-8") == EXPECTED_POST + "edited just before the swap\n"
    assert _git(engine, "rev-parse", "HEAD") == head
    assert sorted(p.name for p in (engine.blog / "src" / "content" / "posts").iterdir()) == [f"{SLUG}.md"]


def test_a_hook_that_stages_another_file_changes_nothing(engine: Engine) -> None:
    _approved(engine)
    (engine.blog / "README.md").write_text("tracked\n")
    _git(engine, "add", "--", "README.md")
    _git(engine, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "readme")
    (engine.blog / "README.md").write_text("tracked, edited by hand\n")
    hook = engine.blog / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\ngit add -u\nexit 0\n")
    hook.chmod(0o755)
    head = _git(engine, "rev-parse", "HEAD")
    for _ in range(2):
        rc, payload, output = engine.run("publish", SLUG)
        assert (rc, _codes(payload)) == (1, ["other_changes_staged"]), output
        assert _git(engine, "rev-parse", "HEAD") == head
    assert "README.md" not in _git(engine, "diff", "--cached", "--name-only")


def test_a_failed_swap_puts_the_post_back(engine: Engine) -> None:
    _approved(engine)
    before = (engine.blog / POST).read_bytes()
    rc, payload, output = engine.run("publish", SLUG, script=_bootstrap(engine, "fail-link"))
    assert rc == 1, output
    assert (engine.blog / POST).read_bytes() == before
    assert sorted(p.name for p in (engine.blog / "src" / "content" / "posts").iterdir()) == [f"{SLUG}.md"]
    rc, payload, output = engine.run("publish", SLUG)
    assert (rc, payload["errors"]) == (0, []), output


def test_a_swap_stopped_midway_is_finished_by_the_next_publish(engine: Engine) -> None:
    _approved(engine)
    rc, _, output = engine.run("publish", SLUG, script=_crash(engine, "after-rename", 1))
    assert rc == 9, output
    assert not (engine.blog / POST).exists()
    rc, payload, output = engine.run("publish", SLUG)
    assert (rc, payload["errors"]) == (0, []), output
    assert _git(engine, "show", f"HEAD:{POST}") == EXPECTED_POST.replace("draft: true", "draft: false")
    assert sorted(p.name for p in (engine.blog / "src" / "content" / "posts").iterdir()) == [f"{SLUG}.md"]


def test_a_restore_stopped_before_its_cleanup_is_finished_by_the_next_publish(engine: Engine) -> None:
    """The post went back but the moved-aside copy of the same bytes stayed."""
    _approved(engine)
    os.link(engine.blog / POST, engine.blog / "src" / "content" / "posts" / f".{SLUG}.md.publish-old")
    rc, payload, output = engine.run("publish", SLUG)
    assert (rc, payload["errors"]) == (0, []), output
    assert sorted(p.name for p in (engine.blog / "src" / "content" / "posts").iterdir()) == [f"{SLUG}.md"]


def test_a_leftover_that_cannot_be_read_as_a_post_is_kept_and_refused(engine: Engine) -> None:
    _approved(engine)
    leftover = engine.blog / "src" / "content" / "posts" / f".{SLUG}.md.publish-old"
    leftover.write_text("no frontmatter here\n")
    before = (engine.blog / POST).read_bytes()
    rc, payload, output = engine.run("publish", SLUG)
    assert (rc, _codes(payload)) == (1, ["publish_backup_left"]), output
    assert leftover.read_text() == "no frontmatter here\n" and (engine.blog / POST).read_bytes() == before


def test_an_index_left_behind_after_the_commit_is_finished_by_the_next_publish(engine: Engine) -> None:
    _approved(engine)
    rc, payload, output = engine.run("publish", SLUG, script=_bootstrap(engine, "fail-index-update"))
    assert rc == 1, output
    assert _git(engine, "log", "-1", "--format=%s") == f"publish: {SLUG}\n"
    rc, payload, output = engine.run("publish", SLUG)
    assert (rc, payload["errors"], payload["already_published"]) == (0, [], True), output
    assert _git(engine, "status", "--porcelain", "--", POST, EVIDENCE) == ""


def test_publishing_leaves_other_runs_files_in_the_git_folder_alone(engine: Engine) -> None:
    _approved(engine)
    others = [engine.blog / ".git" / name for name in ("blogkit-publish-index", "blogkit-publish-msg")]
    for path in others:
        path.write_text("another run's file\n")
    rc, payload, output = engine.run("publish", SLUG)
    assert (rc, payload["errors"]) == (0, []), output
    assert [path.read_text() for path in others] == ["another run's file\n"] * 2


def test_an_edit_after_the_last_check_is_not_committed(engine: Engine) -> None:
    _approved(engine)
    rc, payload, output = engine.run("publish", SLUG, script=_bootstrap(engine, "edit-after-flip"))
    assert (rc, payload["errors"]) == (0, []), output
    published = EXPECTED_POST.replace("draft: true", "draft: false")
    assert _git(engine, "show", f"HEAD:{POST}") == published
    assert (engine.blog / POST).read_text(encoding="utf-8") == published + "edited after the last check\n"
    assert sorted(_git(engine, "show", "--name-only", "--format=", "HEAD").split()) == sorted([EVIDENCE, POST])


def _stage_other(e: Engine) -> None:
    (e.blog / "notes.md").write_text("fake notes\n")
    _git(e, "add", "--", "notes.md")


def _no_approval(e: Engine) -> None:
    evidence = _evidence(e)
    evidence["records"] = [r for r in evidence["records"] if r["kind"] != "approval"]
    _write_evidence(e, evidence)


def _assets_never_committed(e: Engine) -> None:
    _git(e, "rm", "-q", "-r", "--cached", "--", ASSETS)
    _git(e, "commit", "-q", "-m", "drop assets from git")


@pytest.mark.parametrize("change,code", [
    (_stage_other, "other_changes_staged"),
    (_assets_never_committed, "assets_not_committed"),
    (_no_approval, "evidence_incomplete"),
    (lambda e: _append_post(e, "\none more byte\n"), "evidence_incomplete"),
    (lambda e: (e.blog / POST).write_text(EXPECTED_POST.replace("가짜 설명입니다", "가짜 설명입니다!"),
                                          encoding="utf-8"), "evidence_incomplete"),
    (lambda e: (e.blog / ASSETS / "diagram.png").write_bytes(PNG + b"!"), "evidence_incomplete"),
], ids=["other staged", "assets not committed", "no approval", "body byte", "frontmatter byte", "asset byte"])
def test_publish_refusals_change_nothing(engine: Engine, change, code: str) -> None:
    _approved(engine)
    change(engine)
    head = _git(engine, "rev-parse", "HEAD")
    post = (engine.blog / POST).read_bytes()
    rc, payload, output = engine.run("publish", SLUG)
    assert rc == 1 and _codes(payload) == [code], output
    assert _git(engine, "rev-parse", "HEAD") == head
    assert (engine.blog / POST).read_bytes() == post


def test_a_new_asset_not_in_git_blocks_publish(engine: Engine) -> None:
    status = _checked(engine)
    _commit_assets(engine)
    (engine.blog / ASSETS / "late.svg").write_text("<svg></svg>\n")
    # Gates and approval for the hash that includes the new asset, but the asset is not committed.
    _review(engine)
    rc, _, output = engine.run("check", SLUG)
    assert rc == 0, output
    _learning(engine)
    rc, status, _ = engine.run("status", SLUG)
    rc, _, output = _approve(engine, status["content_hash"], status["warnings_digest"])
    assert rc == 0, output
    rc, payload, output = engine.run("publish", SLUG)
    assert rc == 1 and _codes(payload) == ["assets_not_committed"], output


@pytest.mark.parametrize("change", [
    lambda e: _append_post(e, "\nchanged after publishing\n"),
    lambda e: (e.blog / ASSETS / "diagram.png").write_bytes(PNG + b"!"),
], ids=["body", "asset"])
def test_a_change_after_publishing_fails_the_verifier(engine: Engine, change) -> None:
    _approved(engine)
    rc, _, output = engine.run("publish", SLUG)
    assert rc == 0, output
    assert _verify_source(engine)["status"] == "ok"
    change(engine)
    report = _verify_source(engine)
    assert report["status"] == "fail" and report["errors"]


HOOK = """#!/bin/sh
if [ -f "{flag}" ]; then echo "hook refused {planted}" >&2; exit 1; fi
exit 0
"""


def test_a_failed_commit_is_finished_by_running_publish_again(engine: Engine) -> None:
    _approved(engine)
    flag = engine.tmp / "refuse-commit"
    flag.write_text("x")
    hook = engine.blog / ".git" / "hooks" / "pre-commit"
    hook.write_text(HOOK.format(flag=flag, planted=PLANTED))
    hook.chmod(0o755)
    (engine.blog / "README.md").write_text("tracked\n")
    _git(engine, "add", "--", "README.md")
    _git(engine, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "readme")
    (engine.blog / "README.md").write_text("tracked, edited by hand\n")
    head = _git(engine, "rev-parse", "HEAD")
    rc, payload, output = engine.run("publish", SLUG)
    assert rc == 1 and _codes(payload) == ["commit_failed"], output
    _no_leak(output)
    assert _git(engine, "rev-parse", "HEAD") == head
    assert "draft: false\n" in (engine.blog / POST).read_text(encoding="utf-8")
    flag.unlink()
    rc, payload, output = engine.run("publish", SLUG)
    assert (rc, payload["errors"], payload["resumed"]) == (0, [], True), output
    assert sorted(_git(engine, "show", "--name-only", "--format=", "HEAD").split()) == sorted([EVIDENCE, POST])
    assert (engine.blog / "README.md").read_text() == "tracked, edited by hand\n"
    assert _git(engine, "status", "--porcelain", "--", "README.md") == " M README.md\n"
    rc, payload, output = engine.run("publish", SLUG)
    assert (rc, payload["already_published"]) == (0, True), output


ALL_COMMANDS = [["convert", SLUG], ["check", SLUG], ["status", SLUG], ["approve", SLUG, "--hash", "0" * 64,
                "--warnings", "0" * 64], ["publish", SLUG], ["sync-verifier"]]


def test_no_command_prints_planted_secrets_on_the_normal_or_failing_path(engine: Engine) -> None:
    _checkable(engine)
    (engine.blog / ASSETS / "logo.svg").write_text(f"<svg><text>{FAKE_TERM}</text></svg>\n", encoding="utf-8")
    engine.astro_exit = 1
    engine.build_exit = 1
    engine.net["answers"] = {EXTERNAL_LINK: "timeout"}
    for failing in (None, f"fatal: {PLANTED} at {PLANTED_URL}\n"):
        engine.git_failure = failing
        for command in ALL_COMMANDS:
            rc, payload, output = engine.run(*command)
            assert payload, output
            _no_leak(output)
            assert "example.com" not in output and FAKE_TERM not in output, command
    text = (engine.blog / EVIDENCE).read_text(encoding="utf-8")
    _no_leak(text)
    assert "example.com" not in text and FAKE_TERM not in text


def _reapprove(engine: Engine) -> None:
    """Gates, learning and approval again for whatever the copy holds now."""
    _review(engine)
    rc, _, output = engine.run("check", SLUG)
    assert rc == 0, output
    _learning(engine)
    rc, status, _ = engine.run("status", SLUG)
    rc, _, output = _approve(engine, status["content_hash"], status["warnings_digest"])
    assert rc == 0, output


def _hidden_change(e: Engine) -> None:
    _git(e, "update-index", "--skip-worktree", "--", f"{ASSETS}/diagram.png")
    (e.blog / ASSETS / "diagram.png").write_bytes(PNG + b" hidden from git status")


def _deleted_asset(e: Engine) -> None:
    """Drop the logo and its reference, so the gates pass for a copy whose asset is gone from disk only."""
    (e.blog / ASSETS / "logo.svg").unlink()
    post = (e.blog / POST).read_text(encoding="utf-8")
    (e.blog / POST).write_text(post.replace("[![logo](../../assets/posts/first-post/logo.svg)](/about/)", "logo"),
                               encoding="utf-8")


@pytest.mark.parametrize("change", [
    lambda e: (e.blog / ASSETS / "diagram.png").write_bytes(PNG + b" edited after commit"),
    _deleted_asset,
    _hidden_change,
], ids=["modified", "deleted", "skip-worktree"])
def test_assets_must_match_head_byte_for_byte(engine: Engine, change) -> None:
    _checked(engine)
    _commit_assets(engine)
    change(engine)
    _reapprove(engine)
    rc, payload, output = engine.run("publish", SLUG)
    assert rc == 1 and _codes(payload) == ["assets_not_committed"], output
