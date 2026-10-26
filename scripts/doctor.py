#!/usr/bin/env python3
"""Install doctor for the blog-kit fork of claude-blog.

Run it in (or point it at) the blog repo. It checks that the engine actually
running is the release the blog repo pins, and the runtime prerequisites:

a  shared settings (.claude/settings.json) enable claude-blog@blog-kit from the
   fork marketplace pinned to a v<version> tag
b  `claude plugin list --json`, run in the blog repo, shows exactly one enabled
   project-scope install, and no claude-blog from another marketplace is active
   here (user scope, or a project or local install the registry places in this
   blog repo; one it places in no repo is unknown)
c  the installed-plugins registry holds exactly one project-scope entry whose
   projectPath is this blog repo, it connects to the CLI install by id, scope
   and installPath, and its version agrees with the CLI. Installs of the same
   plugin in other repos (listed by the CLI too) are told apart this way
d  the installed version equals the pinned tag without its leading v
e  the registry commit SHA equals the commit the tag points to on the fork
   (`git ls-remote` of the exact tag ref and its peeled ref)
f  this script runs from inside the install path (so a development checkout
   fails this check on purpose). Checks a to f are the install identity;
   another installed script (the engine CLI) runs them through
   identity_checks(), which judges the calling script's path instead
g  Python is 3.11 or newer
h  CLAUDE_BLOG_RUNTIME_DIR is set in .claude/settings.local.json and in this
   process environment, the two agree, the value passes the runtime path rules
   (absolute, resolved, no parent traversal), and it resolves outside the
   Claude plugin folder. The doctor never sets or repairs it.
i  the blog repo's verifier copy (scripts/blogkit_verify.py) is a regular
   file, not a symlink, byte-identical to the canonical verifier in the
   install path (adapters/astro/blogkit_verify.py), and its VERIFIER_VERSION
   header names the installed version. The engine CLI runs the same check
   through check_verifier_copy(); its sync-verifier command repairs the copy.

Each check is ok, fail or unknown. Unknown (could not verify: a command failed,
timed out, or returned malformed data) counts as a failure, and any check that
is not ok exits 1. A --blog-repo that is a symlink is refused (exit 2), and
settings and registry files that are symlinks or larger than 4 MB are not
read (unknown). Output is one JSON object limited to check ids, statuses,
short reasons, versions, SHAs, scope, enabled and the blog repo, install and
runtime paths. It never prints environment variables, settings or registry
contents, MCP definitions, or external command output. Nothing is written.

Usage:
    python3 doctor.py [--blog-repo PATH] [--claude PATH] [--git PATH]
                      [--registry PATH] [--timeout SECONDS]
"""

from __future__ import annotations

import argparse
import errno
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

PLUGIN_NAME = "claude-blog"
MARKETPLACE = "blog-kit"
PLUGIN_ID = f"{PLUGIN_NAME}@{MARKETPLACE}"
FORK_REPO = "Sungrinhan/blog-kit"
FORK_GIT_URL = f"https://github.com/{FORK_REPO}.git"
FORK_URLS = {f"https://github.com/{FORK_REPO}", FORK_GIT_URL}
RUNTIME_VAR = "CLAUDE_BLOG_RUNTIME_DIR"
TAG_RE = re.compile(r"v(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)")
VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?")
SHA_RE = re.compile(r"[0-9a-f]{40}")
MAX_JSON_BYTES = 4 * 1024 * 1024  # settings and registry files are small (DoS guard)
VERIFIER_COPY = "scripts/blogkit_verify.py"
VERIFIER_CANONICAL = "adapters/astro/blogkit_verify.py"
MAX_VERIFIER_BYTES = 1024 * 1024
VERIFIER_VERSION_RE = re.compile(rb'^VERIFIER_VERSION = "([^"\n]*)"$', re.MULTILINE)
RUNTIME_PATHS = (
    Path(__file__).resolve().parent.parent / "skills" / "blog-google" / "scripts" / "runtime_paths.py"
)


class Fail(Exception):
    """The check ran and the install does not meet it."""


class Unknown(Exception):
    """The check could not be verified; counted as a failure."""


def claude_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _plugin_folders() -> list[Path]:
    """The default and the active Claude plugin folders (they differ under CLAUDE_CONFIG_DIR)."""
    folders = {(Path.home() / ".claude" / "plugins").resolve(), (claude_config_dir() / "plugins").resolve()}
    return sorted(folders)


def _same_path(a: str, b: str) -> bool:
    return Path(a).resolve() == Path(b).resolve()


def _read_json(path: Path, missing: Exception, label: str):
    """Read a small JSON file without following a symlink (CWE-59) or reading past the cap."""
    if path.is_symlink():
        raise Unknown(f"{label} is a symlink; not followed")
    if not path.is_file():
        raise missing
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise Unknown(f"{label} is a symlink; not followed") from None
        raise Unknown(f"{label} is not readable JSON") from None
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise Unknown(f"{label} is larger than {MAX_JSON_BYTES} bytes")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise Unknown(f"{label} is not readable JSON") from None


def _read_capped(path: Path, missing: Exception, label: str, cap: int) -> bytes:
    """Read a regular file without following a symlink (CWE-59) or reading past cap."""
    if path.is_symlink():
        raise Fail(f"{label} is a symlink")
    if path.is_dir():
        raise Fail(f"{label} is not a regular file")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        raise missing from None
    except OSError:
        raise Unknown(f"{label} is not readable") from None
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise Fail(f"{label} is not a regular file")
        data = handle.read(cap + 1)
    if len(data) > cap:
        raise Fail(f"{label} is larger than {cap} bytes")
    return data


def check_verifier_copy(blog: Path, install: Path, version: str) -> dict:
    """Check i: the blog repo's verifier copy matches the install's canonical and version."""
    canonical = _read_capped(
        install / VERIFIER_CANONICAL, Unknown("canonical verifier not found in the install path"),
        "canonical verifier", MAX_VERIFIER_BYTES,
    )
    copy = _read_capped(
        blog / VERIFIER_COPY, Fail(f"verifier copy {VERIFIER_COPY} not found"), "verifier copy",
        MAX_VERIFIER_BYTES,
    )
    if copy != canonical:
        raise Fail("verifier copy differs from the installed canonical; run sync-verifier")
    headers = VERIFIER_VERSION_RE.findall(copy)
    if len(headers) != 1 or headers[0].decode("utf-8", "replace") != version:
        raise Fail("verifier copy header does not name the installed version")
    return {"verifier_copy": VERIFIER_COPY}


def _run(command: list[str], cwd: Path, timeout: float, label: str) -> str:
    """Run an external command; any failure is Unknown and its output stays private."""
    try:
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except FileNotFoundError:
        raise Unknown(f"{label}: executable not found") from None
    except subprocess.TimeoutExpired:
        raise Unknown(f"{label}: timed out after {timeout:g}s") from None
    except OSError:
        raise Unknown(f"{label}: could not run") from None
    if result.returncode != 0:
        raise Unknown(f"{label}: exited with code {result.returncode}")
    return result.stdout


IDENTITY_CHECKS = (
    "a_shared_settings", "b_cli_install", "c_registry", "d_version", "e_commit", "f_exec_path",
)
ALL_CHECKS = IDENTITY_CHECKS + ("g_python", "h_runtime_dir", "i_verifier_copy")


def default_registry() -> Path:
    return claude_config_dir() / "plugins" / "installed_plugins.json"


class Doctor:
    def __init__(
        self, blog: Path, claude: str, git: str, registry: Path, timeout: float,
        script: Path | None = None,
    ) -> None:
        self.blog = blog
        self.script = (script or Path(__file__)).resolve()
        self.claude = claude
        self.git = git
        self.registry = registry
        self.timeout = timeout
        self.ref: str | None = None
        self.cli: dict | None = None
        self.entry: dict | None = None
        self._registry_cache: dict | None = None
        self._registry_data: dict | None = None

    def check_a_shared_settings(self) -> dict:
        settings = _read_json(
            self.blog / ".claude" / "settings.json",
            Fail("shared settings .claude/settings.json not found"),
            "shared settings",
        )
        if not isinstance(settings, dict):
            raise Unknown("shared settings is not a JSON object")
        enabled = settings.get("enabledPlugins")
        if not isinstance(enabled, dict) or enabled.get(PLUGIN_ID) is not True:
            raise Fail(f"{PLUGIN_ID} is not enabled in shared settings")
        markets = settings.get("extraKnownMarketplaces")
        market = markets.get(MARKETPLACE) if isinstance(markets, dict) else None
        source = market.get("source") if isinstance(market, dict) else None
        if not isinstance(source, dict):
            raise Fail(f"marketplace {MARKETPLACE} is not declared in shared settings")
        kind = source.get("source")
        if not (
            (kind == "github" and source.get("repo") == FORK_REPO)
            or (kind == "git" and source.get("url") in FORK_URLS)
        ):
            raise Fail(f"marketplace {MARKETPLACE} does not point at {FORK_REPO}")
        ref = source.get("ref")
        if not isinstance(ref, str) or not TAG_RE.fullmatch(ref):
            raise Fail(f"marketplace {MARKETPLACE} ref is not a v<version> tag")
        self.ref = ref
        return {"ref": ref}

    def check_b_cli_install(self) -> dict:
        out = _run([self.claude, "plugin", "list", "--json"], self.blog, self.timeout, "claude plugin list")
        try:
            entries = json.loads(out)
        except json.JSONDecodeError:
            raise Unknown("claude plugin list: output is not JSON") from None
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            raise Unknown("claude plugin list: output is not a list of plugins")
        ours = []
        for entry in entries:
            plugin_id = entry.get("id")
            if not isinstance(plugin_id, str):
                raise Unknown("claude plugin list: an entry has no id")
            name, _, market = plugin_id.partition("@")
            if name != PLUGIN_NAME:
                continue
            if not isinstance(entry.get("enabled"), bool):
                raise Unknown(f"claude plugin list: {PLUGIN_NAME} entry has no enabled field")
            if market != MARKETPLACE:
                active = entry["enabled"] or entry.get("projectEnabled") is True
                if active and self._active_here(plugin_id, entry):
                    raise Fail(f"{PLUGIN_NAME} from another marketplace is active here")
                continue
            for field in ("version", "scope", "installPath"):
                if not isinstance(entry.get(field), str):
                    raise Unknown(f"claude plugin list: {PLUGIN_ID} entry has no {field}")
            if entry["scope"] == "project":
                ours.append(entry)
        if not ours:
            raise Fail(f"no project-scope {PLUGIN_ID} install in this blog repo")
        if len(ours) > 1:
            # The CLI also lists other repos' project installs. Keep the ones
            # that share this blog repo's registry install path.
            try:
                install = self._registry_entry()["installPath"]
            except Fail:
                raise Unknown("several project-scope installs and none is this blog repo's") from None
            ours = [e for e in ours if _same_path(e["installPath"], install)]
            if not ours:
                raise Fail(f"no project-scope {PLUGIN_ID} install connects to this blog repo")
            distinct = {(e["version"], e["enabled"], str(Path(e["installPath"]).resolve())) for e in ours}
            if len(distinct) > 1:
                raise Unknown("CLI entries for this blog repo's install disagree")
        entry = ours[0]
        fields = {"scope": entry["scope"], "enabled": entry["enabled"], "install_path": entry["installPath"]}
        if VERSION_RE.fullmatch(entry["version"]):
            fields["version"] = entry["version"]
        if entry["enabled"] is not True:
            raise _with(Fail(f"{PLUGIN_ID} is installed but not enabled"), fields)
        self.cli = entry
        return fields

    def _active_here(self, plugin_id: str, entry: dict) -> bool:
        """Whether an active same-name plugin from another marketplace applies to this blog repo.

        A user-scope install applies everywhere. The CLI also lists other
        repos' project and local installs, so those count only when the
        registry places one in this blog repo.
        """
        if entry.get("scope") not in ("project", "local"):
            return True
        entries = self._registry_list(plugin_id)
        if any(
            e.get("scope") in ("project", "local") and isinstance(e.get("projectPath"), str)
            and _same_path(e["projectPath"], str(self.blog))
            for e in entries
        ):
            return True
        install = entry.get("installPath")
        if isinstance(install, str) and any(
            isinstance(e.get("installPath"), str) and _same_path(e["installPath"], install)
            for e in entries
        ):
            return False
        raise Unknown(f"cannot tell which repo the {plugin_id} {entry['scope']} install belongs to")

    def _registry_list(self, plugin_id: str) -> list[dict]:
        """The registry's install entries for one plugin id (empty when it has none)."""
        if self._registry_data is None:
            data = _read_json(self.registry, Unknown("installed-plugins registry not found"), "registry")
            plugins = data.get("plugins") if isinstance(data, dict) else None
            if not isinstance(plugins, dict):
                raise Unknown("registry has no plugin list")
            self._registry_data = plugins
        entries = self._registry_data.get(plugin_id, [])
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            raise Unknown("registry has no plugin list")
        return entries

    def _registry_entry(self) -> dict:
        """This blog repo's project-scope registry entry for the plugin."""
        if self._registry_cache is None:
            entries = self._registry_list(PLUGIN_ID)
            mine = []
            for e in entries:
                if e.get("scope") != "project":
                    continue
                for field in ("projectPath", "installPath", "version"):
                    if not isinstance(e.get(field), str):
                        raise Unknown(f"registry project entry has no {field}")
                if _same_path(e["projectPath"], str(self.blog)):
                    mine.append(e)
            if not mine:
                raise Fail("no project-scope registry entry for this blog repo")
            if len(mine) > 1:
                raise Fail("more than one registry entry for this blog repo")
            self._registry_cache = mine[0]
        return self._registry_cache

    def check_c_registry(self) -> dict:
        if self.cli is None:
            raise Unknown("depends on b_cli_install")
        entry = self._registry_entry()
        if not _same_path(entry["installPath"], self.cli["installPath"]):
            raise Fail("registry entry and CLI install do not connect (installPath)")
        if entry["version"] != self.cli["version"]:
            raise Fail("registry and CLI disagree on the installed version")
        self.entry = entry
        return {"scope": "project"}

    def check_d_version(self) -> dict:
        if self.ref is None or self.cli is None:
            raise Unknown("depends on a_shared_settings and b_cli_install")
        pinned = self.ref[1:]
        installed = self.cli["version"]
        if installed != pinned:
            fields = {"pinned_version": pinned}
            if VERSION_RE.fullmatch(installed):
                fields["version"] = installed
            raise _with(Fail("installed version is not the pinned version"), fields)
        return {"version": installed, "pinned_version": pinned}

    def check_e_commit(self) -> dict:
        if self.ref is None or self.entry is None:
            raise Unknown("depends on a_shared_settings and c_registry")
        installed = self.entry.get("gitCommitSha")
        if not isinstance(installed, str):
            raise Unknown("registry entry has no gitCommitSha")
        if not SHA_RE.fullmatch(installed):
            raise Fail("registry gitCommitSha is not a 40-character SHA")
        tag_ref = f"refs/tags/{self.ref}"
        peeled_ref = f"{tag_ref}^{{}}"
        out = _run(
            [self.git, "ls-remote", FORK_GIT_URL, tag_ref, peeled_ref],
            self.blog, self.timeout, "git ls-remote",
        )
        found: dict[str, str] = {}
        for line in out.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) != 2:
                raise Fail("git ls-remote returned an unexpected line")
            sha, ref = parts
            if ref not in (tag_ref, peeled_ref):
                raise Fail("git ls-remote returned an unexpected ref")
            if ref in found:
                raise Fail("git ls-remote returned the same ref twice")
            if not SHA_RE.fullmatch(sha):
                raise Fail("git ls-remote returned a malformed SHA")
            found[ref] = sha
        if tag_ref not in found:
            raise Fail(f"tag {self.ref} not found on {FORK_REPO}")
        tagged = found.get(peeled_ref, found[tag_ref])
        fields = {"commit": installed, "tag_commit": tagged}
        if installed != tagged:
            raise _with(Fail("installed commit is not the commit the tag points to"), fields)
        return fields

    def check_f_exec_path(self) -> dict:
        if self.cli is None:
            raise Unknown("depends on b_cli_install")
        install = Path(self.cli["installPath"]).resolve()
        if not self.script.is_relative_to(install):
            raise Fail("doctor is not running from the install path")
        return {"install_path": str(install)}

    def check_g_python(self) -> dict:
        version = ".".join(str(part) for part in sys.version_info[:3])
        if sys.version_info < (3, 11):
            raise _with(Fail("Python 3.11 or newer is required"), {"python": version})
        return {"python": version}

    def check_h_runtime_dir(self) -> dict:
        local = _read_json(
            self.blog / ".claude" / "settings.local.json",
            Fail(f"{RUNTIME_VAR} is not set in .claude/settings.local.json"),
            "per-device settings",
        )
        env = local.get("env") if isinstance(local, dict) else None
        configured = env.get(RUNTIME_VAR) if isinstance(env, dict) else None
        if not isinstance(configured, str):
            raise Fail(f"{RUNTIME_VAR} is not set in .claude/settings.local.json")
        inherited = os.environ.get(RUNTIME_VAR)
        if inherited is None:
            raise Fail(f"{RUNTIME_VAR} is not in this process environment; restart Claude Code")
        if inherited != configured:
            raise Fail(f"{RUNTIME_VAR} in settings and in the process environment differ")
        if not RUNTIME_PATHS.is_file():
            raise Unknown("runtime path rules (runtime_paths.py) not found")
        spec = importlib.util.spec_from_file_location("blog_kit_runtime_paths", RUNTIME_PATHS)
        rules = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rules)
        try:
            rules.validate_absolute_override(configured)
        except ValueError:
            raise Fail(
                f"{RUNTIME_VAR} must be an absolute resolved path without parent traversal"
            ) from None
        resolved = Path(configured).resolve()
        if resolved.exists() and not resolved.is_dir():
            raise Fail(f"{RUNTIME_VAR} exists but is not a folder")
        for plugins in _plugin_folders():
            if resolved.is_relative_to(plugins):
                raise Fail(f"{RUNTIME_VAR} resolves inside the Claude plugin folder")
        return {"runtime_dir": str(resolved)}

    def check_i_verifier_copy(self) -> dict:
        if self.cli is None:
            raise Unknown("depends on b_cli_install")
        return check_verifier_copy(self.blog, Path(self.cli["installPath"]).resolve(), self.cli["version"])

    def run(self, check_ids: tuple[str, ...] = ALL_CHECKS) -> list[dict]:
        results = []
        for check_id in check_ids:
            check = getattr(self, f"check_{check_id}")
            try:
                fields, status, reason = check(), "ok", "ok"
            except Fail as exc:
                fields, status, reason = getattr(exc, "fields", {}), "fail", str(exc)
            except Unknown as exc:
                fields, status, reason = getattr(exc, "fields", {}), "unknown", str(exc)
            except Exception as exc:  # never let a traceback print private data
                fields, status, reason = {}, "unknown", f"unexpected {type(exc).__name__}"
            results.append({"id": check_id, "status": status, "reason": reason, **fields})
        return results


def identity_checks(
    blog: Path, claude: str | None, git: str | None, registry: Path, timeout: float, script: Path,
) -> list[dict]:
    """Run checks a to f for another installed script, judging that script's own path."""
    doctor = Doctor(
        blog=blog.resolve(),
        claude=claude or shutil.which("claude") or "claude",
        git=git or shutil.which("git") or "git",
        registry=registry,
        timeout=timeout,
        script=script,
    )
    return doctor.run(IDENTITY_CHECKS)


def _with(exc: Exception, fields: dict) -> Exception:
    exc.fields = fields
    return exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check that the blog repo runs the pinned blog-kit release and its runtime prerequisites."
    )
    parser.add_argument("--blog-repo", default=None, help="Blog repo folder (default: current folder)")
    parser.add_argument("--claude", default=None, help="claude executable (default: from PATH)")
    parser.add_argument("--git", default=None, help="git executable (default: from PATH)")
    parser.add_argument(
        "--registry", default=None,
        help="installed_plugins.json (default: <Claude config folder>/plugins/installed_plugins.json)",
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="Seconds per external command (default 20)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.blog_repo and Path(args.blog_repo).is_symlink():
        print(json.dumps({"status": "error", "error": "--blog-repo is a symlink; pass the real folder"}, indent=2))
        return 2
    blog = Path(args.blog_repo or Path.cwd()).resolve()
    registry = Path(args.registry) if args.registry else default_registry()
    doctor = Doctor(
        blog=blog,
        claude=args.claude or shutil.which("claude") or "claude",
        git=args.git or shutil.which("git") or "git",
        registry=registry,
        timeout=args.timeout,
    )
    checks = doctor.run()
    ok = all(check["status"] == "ok" for check in checks)
    print(json.dumps({"status": "ok" if ok else "fail", "blog_repo": str(blog), "checks": checks}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
