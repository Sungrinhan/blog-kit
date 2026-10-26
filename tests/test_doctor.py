"""S1: the install doctor (scripts/doctor.py) through its CLI.

Each test builds a temp world: a blog repo with shared and per-device settings,
an installed plugin copy (the doctor itself is copied into its installPath),
an installed-plugins registry, and stub `claude` and `git` executables that
imitate the real 2.1.x `claude plugin list --json` shape (no commit SHA) and
`git ls-remote`. The doctor's JSON output and exit code are the interface.

Stdlib + pytest only. No network.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCTOR = ROOT / "scripts" / "doctor.py"
RUNTIME_PATHS = ROOT / "skills" / "blog-google" / "scripts" / "runtime_paths.py"

PLUGIN_ID = "claude-blog@blog-kit"
VERSION = "2.3.1-kit.1"
TAG = f"v{VERSION}"
COMMIT = "c" * 40
TAG_OBJECT = "a" * 40
# A fake credential-shaped value planted in stub output, settings and the
# environment. The doctor must never print it.
PLANTED_VALUE = "sk-" + "Z" * 30
CHECK_IDS = [
    "a_shared_settings", "b_cli_install", "c_registry", "d_version",
    "e_commit", "f_exec_path", "g_python", "h_runtime_dir",
]


class World:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.home = tmp / "home"
        self.cache = self.home / ".claude" / "plugins" / "cache"
        self.install = self.cache / "blog-kit" / "claude-blog" / VERSION
        self.blog = tmp / "blog"
        self.runtime = tmp / "runtime"
        self.bin = tmp / "bin"
        for folder in (self.install / "scripts", self.blog / ".claude", self.runtime, self.bin):
            folder.mkdir(parents=True)
        self.script = self.install / "scripts" / "doctor.py"
        self.script.write_bytes(DOCTOR.read_bytes())
        runtime_copy = self.install / "skills" / "blog-google" / "scripts" / "runtime_paths.py"
        runtime_copy.parent.mkdir(parents=True)
        runtime_copy.write_bytes(RUNTIME_PATHS.read_bytes())
        self.registry = self.home / ".claude" / "plugins" / "installed_plugins.json"

        self.shared = {
            "extraKnownMarketplaces": {
                "blog-kit": {"source": {"source": "github", "repo": "Sungrinhan/blog-kit", "ref": TAG}}
            },
            "enabledPlugins": {PLUGIN_ID: True},
            "permissions": {"allow": [f"Bash(echo {PLANTED_VALUE})"]},
        }
        self.local = {"env": {"CLAUDE_BLOG_RUNTIME_DIR": str(self.runtime), "OTHER_TOKEN": PLANTED_VALUE}}
        self.cli = [
            {
                "id": PLUGIN_ID, "version": VERSION, "scope": "project", "enabled": True,
                "installPath": str(self.install), "installedAt": "2026-10-25T00:00:00.000Z",
                "lastUpdated": "2026-10-25T00:00:00.000Z", "projectEnabled": True,
            },
            {
                "id": "figma@claude-plugins-official", "version": "2.2.157", "scope": "user",
                "enabled": True, "installPath": str(self.cache / "figma"),
                "mcpServers": {"figma": {"env": {"FIGMA_TOKEN": PLANTED_VALUE}}},
                "projectEnabled": False,
            },
        ]
        self.registry_data = {
            "version": 2,
            "plugins": {
                PLUGIN_ID: [{
                    "scope": "project", "projectPath": str(self.blog),
                    "installPath": str(self.install), "version": VERSION,
                    "installedAt": "2026-10-25T00:00:00.000Z",
                    "lastUpdated": "2026-10-25T00:00:00.000Z", "gitCommitSha": COMMIT,
                }]
            },
        }
        self.ls_remote = f"{COMMIT}\trefs/tags/{TAG}\n"
        self.claude_rc = 0
        self.git_rc = 0
        self.claude_sleep = 0
        self.env = {"CLAUDE_BLOG_RUNTIME_DIR": str(self.runtime), "LEAKY_SECRET": PLANTED_VALUE}
        self.blog_arg: Path | None = None
        self.shared_real: Path | None = None
        self.registry_real: Path | None = None

    def _stub(self, name: str, out: Path, rc: int, sleep: int) -> Path:
        calls = self.tmp / f"{name}-calls.txt"
        stub = self.bin / name
        stub.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$(pwd -P)" "$*" >> "{calls}"\n'
            f"sleep {sleep}\n"
            f'cat "{out}"\n'
            f'echo "stderr {PLANTED_VALUE}" >&2\n'
            f"exit {rc}\n"
        )
        stub.chmod(0o755)
        return stub

    def run(self, script: Path | None = None) -> tuple[int, dict, str]:
        shared = self.blog / ".claude" / "settings.json"
        if self.shared is None:
            shared.unlink(missing_ok=True)
        elif self.shared_real is not None:
            self.shared_real.write_text(json.dumps(self.shared))
            shared.unlink(missing_ok=True)
            shared.symlink_to(self.shared_real)
        else:
            shared.write_text(json.dumps(self.shared))
        if self.local is not None:
            (self.blog / ".claude" / "settings.local.json").write_text(json.dumps(self.local))
        (self.registry_real or self.registry).write_text(json.dumps(self.registry_data))
        cli_out = self.tmp / "claude-out.json"
        cli_out.write_text(self.cli if isinstance(self.cli, str) else json.dumps(self.cli))
        git_out = self.tmp / "git-out.txt"
        git_out.write_text(self.ls_remote)
        claude = self._stub("claude", cli_out, self.claude_rc, self.claude_sleep)
        git = self._stub("git", git_out, self.git_rc, 0)
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "CLAUDE_BLOG_RUNTIME_DIR")}
        env.update(HOME=str(self.home), **self.env)
        result = subprocess.run(
            [sys.executable, str(script or self.script), "--blog-repo", str(self.blog_arg or self.blog),
             "--claude", str(claude), "--git", str(git), "--timeout", "2"],
            capture_output=True, text=True, env=env, cwd=self.tmp, timeout=60,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = {}
        return result.returncode, payload, result.stdout + result.stderr

    def calls(self, name: str) -> list[str]:
        path = self.tmp / f"{name}-calls.txt"
        return path.read_text().splitlines() if path.exists() else []


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _statuses(payload: dict) -> dict[str, str]:
    return {check["id"]: check["status"] for check in payload.get("checks", [])}


def test_matching_install_passes_every_check(world: World) -> None:
    rc, payload, _ = world.run()
    assert _statuses(payload) == {check: "ok" for check in CHECK_IDS}
    assert payload["status"] == "ok"
    assert rc == 0
    assert world.calls("claude")[:2] == [str(world.blog.resolve()), "plugin list --json"]
    assert world.calls("git")[1] == (
        f"ls-remote https://github.com/Sungrinhan/blog-kit.git refs/tags/{TAG} refs/tags/{TAG}^{{}}"
    )


def _ours(w: World) -> dict:
    return w.cli[0]


def _entry(w: World) -> dict:
    return w.registry_data["plugins"][PLUGIN_ID][0]


def _set_runtime(w: World, value: str) -> None:
    w.local["env"]["CLAUDE_BLOG_RUNTIME_DIR"] = value
    w.env["CLAUDE_BLOG_RUNTIME_DIR"] = value


def _symlink_into_cache(w: World) -> None:
    target = w.cache / "data"
    target.mkdir()
    link = w.tmp / "runtime-link"
    link.symlink_to(target)
    _set_runtime(w, str(link))


def _runtime_is_a_file(w: World) -> None:
    path = w.tmp / "runtime-file"
    path.write_text("not a folder\n")
    _set_runtime(w, str(path))


def _version(w: World, version: str) -> None:
    _ours(w)["version"] = version
    _entry(w)["version"] = version


def _registry_symlink(w: World) -> None:
    w.registry_real = w.tmp / "registry-real.json"
    w.registry.symlink_to(w.registry_real)


def _oversize_local(w: World) -> None:
    w.local["env"]["PADDING"] = "x" * (5 * 1024 * 1024)


OTHER_SHA = "d" * 40
TAG_REF = f"refs/tags/{TAG}"
def _other_project(w: World, shared_cache: bool) -> None:
    """The same plugin installed at project scope in another repo too."""
    other_install = w.install if shared_cache else w.cache / "blog-kit" / "claude-blog" / "2.3.1-kit.0"
    w.cli.append(dict(_ours(w), installPath=str(other_install)))
    w.registry_data["plugins"][PLUGIN_ID].append(
        dict(_entry(w), projectPath=str(w.tmp / "other-repo"), installPath=str(other_install))
    )


UPSTREAM_ID = "claude-blog@agricidaniel-blog"


def _upstream_project(w: World, project: Path | None) -> None:
    """The upstream plugin installed at project scope; project=None leaves no registry entry."""
    install = w.cache / "agricidaniel-blog" / "claude-blog" / "2.3.1"
    w.cli.append({"id": UPSTREAM_ID, "version": "2.3.1", "scope": "project", "enabled": True,
                  "installPath": str(install), "projectEnabled": True})
    if project is not None:
        w.registry_data["plugins"][UPSTREAM_ID] = [{
            "scope": "project", "projectPath": str(project), "installPath": str(install),
            "version": "2.3.1",
        }]


PASSING = {
    "upstream plugin installed at project scope only in another repo": lambda w: _upstream_project(
        w, w.tmp / "other-repo"),
    "another repo has its own project install": lambda w: _other_project(w, shared_cache=False),
    "another repo shares the same cached install": lambda w: _other_project(w, shared_cache=True),
    "annotated tag (two lines) compares the peeled commit": lambda w: setattr(
        w, "ls_remote", f"{TAG_OBJECT}\t{TAG_REF}\n{COMMIT}\t{TAG_REF}^{{}}\n"
    ),
    "claude-blog from another marketplace installed but disabled": lambda w: w.cli.append(
        {"id": "claude-blog@agricidaniel-blog", "version": "2.3.1", "scope": "user",
         "enabled": False, "installPath": "/elsewhere", "projectEnabled": False}
    ),
}
FAILING = {
    # (mutation, check id, expected status)
    "shared settings missing": (lambda w: setattr(w, "shared", None), "a_shared_settings", "fail"),
    "plugin not enabled in shared settings": (
        lambda w: w.shared.update(enabledPlugins={}), "a_shared_settings", "fail"),
    "marketplace points elsewhere": (
        lambda w: w.shared["extraKnownMarketplaces"]["blog-kit"]["source"].update(repo="AgriciDaniel/claude-blog"),
        "a_shared_settings", "fail"),
    "ref is not a version tag": (
        lambda w: w.shared["extraKnownMarketplaces"]["blog-kit"]["source"].update(ref="main"),
        "a_shared_settings", "fail"),
    "version mismatch": (lambda w: _version(w, "2.3.1-kit.2"), "d_version", "fail"),
    "commit mismatch": (lambda w: setattr(w, "ls_remote", f"{OTHER_SHA}\t{TAG_REF}\n"), "e_commit", "fail"),
    "annotated tag object is not the installed commit": (
        lambda w: (setattr(w, "ls_remote", f"{COMMIT}\t{TAG_REF}\n{OTHER_SHA}\t{TAG_REF}^{{}}\n")),
        "e_commit", "fail"),
    "tag missing": (lambda w: setattr(w, "ls_remote", ""), "e_commit", "fail"),
    "same ref twice": (
        lambda w: setattr(w, "ls_remote", f"{COMMIT}\t{TAG_REF}\n{COMMIT}\t{TAG_REF}\n"), "e_commit", "fail"),
    "unexpected ref": (
        lambda w: setattr(w, "ls_remote", f"{COMMIT}\t{TAG_REF}\n{COMMIT}\trefs/tags/{TAG}0\n"),
        "e_commit", "fail"),
    "only the peeled ref": (lambda w: setattr(w, "ls_remote", f"{COMMIT}\t{TAG_REF}^{{}}\n"), "e_commit", "fail"),
    "malformed SHA from ls-remote": (lambda w: setattr(w, "ls_remote", f"{COMMIT[:39]}\t{TAG_REF}\n"), "e_commit", "fail"),
    "malformed registry SHA": (lambda w: _entry(w).update(gitCommitSha="HEAD"), "e_commit", "fail"),
    "registry has no commit SHA": (lambda w: _entry(w).pop("gitCommitSha"), "e_commit", "unknown"),
    "git ls-remote fails": (lambda w: setattr(w, "git_rc", 128), "e_commit", "unknown"),
    "CLI scope is user": (lambda w: _ours(w).update(scope="user"), "b_cli_install", "fail"),
    "registry scope is user": (lambda w: _entry(w).update(scope="user"), "c_registry", "fail"),
    "registry projectPath is another repo": (
        lambda w: _entry(w).update(projectPath=str(w.tmp)), "c_registry", "fail"),
    "plugin disabled in CLI": (lambda w: _ours(w).update(enabled=False), "b_cli_install", "fail"),
    "same name from another marketplace active": (
        lambda w: w.cli.append({"id": "claude-blog@agricidaniel-blog", "version": "2.3.1", "scope": "user",
                                "enabled": True, "installPath": "/elsewhere", "projectEnabled": False}),
        "b_cli_install", "fail"),
    "upstream plugin installed at project scope in this blog repo": (
        lambda w: _upstream_project(w, w.blog), "b_cli_install", "fail"),
    "project-scope upstream plugin that no registry entry places": (
        lambda w: _upstream_project(w, None), "b_cli_install", "unknown"),
    "CLI install has no registry entry": (
        lambda w: _entry(w).update(installPath=str(w.tmp / "other")), "c_registry", "fail"),
    "registry entry duplicated": (
        lambda w: w.registry_data["plugins"][PLUGIN_ID].append(dict(_entry(w))), "c_registry", "fail"),
    "CLI entries for this install disagree": (
        lambda w: w.cli.append(dict(_ours(w), enabled=False)), "b_cli_install", "unknown"),
    "registry has two entries for this blog repo": (
        lambda w: w.registry_data["plugins"][PLUGIN_ID].append(
            dict(_entry(w), installPath=str(w.tmp / "second"))), "c_registry", "fail"),
    "registry and CLI versions differ": (lambda w: _entry(w).update(version="2.3.1-kit.2"), "c_registry", "fail"),
    "registry missing": (lambda w: setattr(w, "registry", w.tmp / "nowhere.json"), "c_registry", "unknown"),
    "claude exits nonzero": (lambda w: setattr(w, "claude_rc", 1), "b_cli_install", "unknown"),
    "claude times out": (lambda w: setattr(w, "claude_sleep", 5), "b_cli_install", "unknown"),
    "claude prints invalid JSON": (lambda w: setattr(w, "cli", f"oops {PLANTED_VALUE}"), "b_cli_install", "unknown"),
    "claude entry missing installPath": (lambda w: _ours(w).pop("installPath"), "b_cli_install", "unknown"),
    "registry is a symlink": (_registry_symlink, "c_registry", "unknown"),
    "per-device settings larger than the size cap": (_oversize_local, "h_runtime_dir", "unknown"),
    "runtime dir not in per-device settings": (lambda w: setattr(w, "local", None), "h_runtime_dir", "fail"),
    "runtime dir not in process environment": (
        lambda w: w.env.pop("CLAUDE_BLOG_RUNTIME_DIR"), "h_runtime_dir", "fail"),
    "runtime dir values differ": (
        lambda w: w.env.update(CLAUDE_BLOG_RUNTIME_DIR=str(w.tmp / "elsewhere")), "h_runtime_dir", "fail"),
    "runtime dir relative": (lambda w: _set_runtime(w, "runtime"), "h_runtime_dir", "fail"),
    "runtime dir with parent traversal": (
        lambda w: _set_runtime(w, str(w.tmp / "x" / ".." / "runtime")), "h_runtime_dir", "fail"),
    "runtime dir inside the plugin cache": (
        lambda w: _set_runtime(w, str(w.cache / "data")), "h_runtime_dir", "fail"),
    "runtime dir reaches the cache through a symlink": (_symlink_into_cache, "h_runtime_dir", "fail"),
    "runtime dir is an existing regular file": (_runtime_is_a_file, "h_runtime_dir", "fail"),
}


@pytest.mark.parametrize("case", sorted(PASSING))
def test_variations_that_still_pass(world: World, case: str) -> None:
    PASSING[case](world)
    rc, payload, _ = world.run()
    assert _statuses(payload) == {check: "ok" for check in CHECK_IDS}
    assert rc == 0


@pytest.mark.parametrize("case", sorted(FAILING))
def test_each_failure_is_reported_and_exits_1(world: World, case: str) -> None:
    mutate, check_id, expected = FAILING[case]
    mutate(world)
    rc, payload, _ = world.run()
    assert _statuses(payload)[check_id] == expected
    assert payload["status"] == "fail"
    assert rc == 1


def test_running_outside_the_install_path_fails_the_exec_path_check(world: World) -> None:
    rc, payload, _ = world.run(script=DOCTOR)
    statuses = _statuses(payload)
    assert statuses.pop("f_exec_path") == "fail"
    assert set(statuses.values()) == {"ok"}
    assert rc == 1


@pytest.mark.parametrize(
    "case", ["claude prints invalid JSON", "runtime dir values differ", "commit mismatch",
             "claude exits nonzero", "runtime dir inside the plugin cache"],
)
def test_no_secret_reaches_stdout_or_stderr(world: World, case: str) -> None:
    rc, _, output = world.run()
    assert rc == 0 and PLANTED_VALUE not in output
    fresh = World(world.tmp / "again")
    FAILING[case][0](fresh)
    rc, _, output = fresh.run()
    assert rc == 1 and PLANTED_VALUE not in output


def test_shared_settings_symlink_is_not_followed(world: World) -> None:
    world.shared_real = world.tmp / "settings-real.json"
    rc, payload, _ = world.run()
    assert _statuses(payload)["a_shared_settings"] == "unknown"
    assert "symlink" in {c["id"]: c for c in payload["checks"]}["a_shared_settings"]["reason"]
    assert rc == 1


def test_blog_repo_argument_that_is_a_symlink_is_refused(world: World) -> None:
    world.blog_arg = world.tmp / "blog-link"
    world.blog_arg.symlink_to(world.blog)
    rc, payload, _ = world.run()
    assert rc == 2
    assert payload["status"] == "error" and "symlink" in payload["error"]
    assert world.calls("claude") == []


IDENTITY_IDS = CHECK_IDS[:6]
# Another installed script (like the engine CLI) loads the doctor from its
# install root and asks for the install identity checks only, naming itself as
# the script whose path must sit inside the install path.
IDENTITY_CALLER = """\
import importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("doctor", Path(DOCTOR_PATH))
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)
args = doctor.build_parser().parse_args(sys.argv[1:])
checks = doctor.identity_checks(
    blog=Path(args.blog_repo), claude=args.claude, git=args.git,
    registry=doctor.default_registry(), timeout=args.timeout, script=Path(__file__),
)
print(json.dumps({"checks": checks}))
"""


def _identity_caller(w: World, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    caller = folder / "caller.py"
    caller.write_text(IDENTITY_CALLER.replace("DOCTOR_PATH", repr(str(w.script))))
    return caller


def test_another_installed_script_can_run_the_identity_checks(world: World) -> None:
    rc, payload, output = world.run(script=_identity_caller(world, world.install / "adapters" / "astro"))
    assert rc == 0, output
    assert _statuses(payload) == {check: "ok" for check in IDENTITY_IDS}
    assert PLANTED_VALUE not in output


def test_identity_checks_judge_the_calling_script_path(world: World) -> None:
    rc, payload, output = world.run(script=_identity_caller(world, world.tmp / "dev-checkout"))
    assert rc == 0, output
    statuses = _statuses(payload)
    assert statuses["f_exec_path"] == "fail"
    assert all(statuses[c] == "ok" for c in IDENTITY_IDS[:5])
