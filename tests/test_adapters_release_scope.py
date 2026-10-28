"""S5: the fork's adapters/ folder gets the same release-surface scan as upstream folders.

scripts/validate_public_release.py scans skills/, agents/, scripts/ and the
other upstream distribution folders for private repository URLs, private
marketplace slugs and raw installer links. It does not know adapters/, which
the fork adds and ships in the plugin, so this test applies the same patterns
there without patching the upstream checker.

M2b adds its features without patching upstream: since the commit M2b started
from (recorded in FORK.md), an upstream file (one present at FORK.md's
upstream base commit) may change only in its version lines, unless it is
CHANGELOG.md or the developer document CLAUDE.md. A clone without either
commit skips that check.

Stdlib + pytest only. No network: fixtures are local temp git repos.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ADAPTERS = ROOT / "adapters"
BASE_RE = re.compile(r"업스트림 기준 커밋: `([0-9a-f]{40})`")
M2B_BASE_RE = re.compile(r"M2b 시작 커밋: `([0-9a-f]{40})`")
WHOLE_FILE_EDITS = frozenset({"CHANGELOG.md", "CLAUDE.md"})
VERSION_RE = re.compile(r"\d+\.\d+\.\d+[-+]kit\.\d+")


def _checker():
    spec = importlib.util.spec_from_file_location(
        "release_checker_for_adapters", ROOT / "scripts" / "validate_public_release.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _findings(checker, root: Path) -> list[tuple[str, str]]:
    found = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file() or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(root).as_posix()
        for kind, pattern in checker.FORBIDDEN_PATTERNS.items():
            if pattern.search(text):
                found.append((kind, relative))
        if checker.RAW_INSTALLER_RE.search(text):
            found.append(("raw_installer_url", relative))
    return found


def test_adapters_carry_no_private_or_installer_references() -> None:
    checker = _checker()
    assert ADAPTERS.is_dir()
    assert _findings(checker, ADAPTERS) == []


def test_the_scan_detects_a_planted_private_reference(tmp_path: Path) -> None:
    checker = _checker()
    planted = tmp_path / "astro" / "x.py"
    planted.parent.mkdir()
    planted.write_text("URL = 'https://github.com/AI-Marketing-Hub/claude-blog'\n", encoding="utf-8")
    assert ("private_repository_url", "astro/x.py") in _findings(checker, tmp_path)


# ------------------------------------------------------------------ M2b upstream scope

def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def upstream_scope_problems(root: Path, upstream_base: str, since: str) -> list[str]:
    """Upstream files the working tree changed since `since` in more than their version lines."""
    problems = []
    for path in sorted(p for p in _git(root, "diff", "--name-only", "-z", since).split("\0") if p):
        upstream = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{upstream_base}:{path}"],
                                  capture_output=True, check=False).returncode == 0
        if path in WHOLE_FILE_EDITS or not upstream:
            continue
        lines = _git(root, "diff", "-a", "-U0", since, "--", path).splitlines()
        edits = [line for line in lines if line[:1] in "+-" and not line.startswith(("+++ ", "--- "))]
        if not edits or any(not VERSION_RE.search(line) for line in edits):
            problems.append(f"upstream file changed beyond its version line: {path}")
    return problems


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """A temp repo whose first commit plays both the upstream base and the milestone start."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for key in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{key}_NAME", "t")
        monkeypatch.setenv(f"GIT_{key}_EMAIL", "t@example.invalid")
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "plugin.json").write_text('{\n  "name": "x",\n  "version": "2.3.1-kit.2"\n}\n')
    (root / "CHANGELOG.md").write_text("# Changelog\n")
    (root / "CLAUDE.md").write_text("# Developer notes\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "upstream")
    return root, _git(root, "rev-parse", "HEAD").strip()


def test_version_lines_changelog_developer_docs_and_new_files_stay_in_scope(repo) -> None:
    root, base = repo
    (root / "plugin.json").write_text('{\n  "name": "x",\n  "version": "2.3.1-kit.3"\n}\n')
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## 2.3.1-kit.3\n- new\n")
    (root / "CLAUDE.md").write_text("# Developer notes\n\n- adapters/astro/blogkit.py\n")
    (root / "added.py").write_text("print('fork addition')\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "milestone")
    assert upstream_scope_problems(root, base, base) == []


def test_an_upstream_edit_beyond_the_version_line_is_out_of_scope(repo) -> None:
    root, base = repo
    (root / "plugin.json").write_text('{\n  "name": "y",\n  "version": "2.3.1-kit.3"\n}\n')
    assert upstream_scope_problems(root, base, base) == ["upstream file changed beyond its version line: plugin.json"]


def test_a_deleted_upstream_file_is_out_of_scope(repo) -> None:
    root, base = repo
    (root / "plugin.json").unlink()
    assert upstream_scope_problems(root, base, base) == ["upstream file changed beyond its version line: plugin.json"]


def test_m2b_changes_upstream_files_only_in_version_surfaces_changelog_and_developer_docs() -> None:
    text = (ROOT / "FORK.md").read_text(encoding="utf-8")
    base, start = BASE_RE.search(text), M2B_BASE_RE.search(text)
    assert base and start, "FORK.md has no upstream base or M2b start commit line"
    for commit in (base.group(1), start.group(1)):
        probe = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{commit}^{{commit}}"],
                               capture_output=True, check=False)
        if probe.returncode != 0:
            pytest.skip(f"commit {commit} is not in this clone")
    assert upstream_scope_problems(ROOT, base.group(1), start.group(1)) == []
