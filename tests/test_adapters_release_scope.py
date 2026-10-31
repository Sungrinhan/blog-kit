"""S5: the fork's adapters/ folder gets the same release-surface scan as upstream folders.

scripts/validate_public_release.py scans skills/, agents/, scripts/ and the
other upstream distribution folders for private repository URLs, private
marketplace slugs and raw installer links. It does not know adapters/, which
the fork adds and ships in the plugin, so this test applies the same patterns
there without patching the upstream checker.

Milestones add their features without patching upstream. An upstream file
(one present at FORK.md's upstream base commit) may change only in its version
lines, except for a fixed set of exact paths per milestone range:
- M2b, from its start commit to the M2c start commit (both recorded in
  FORK.md): CHANGELOG.md and the developer document CLAUDE.md.
- M2c, from its start commit to the working tree: the plugin and marketplace
  manifests, CLAUDE.md, CHANGELOG.md, the router skills/blog/SKILL.md (fork
  skill sentence) and tests/test_skill_frontmatter.py (count expectations).
A clone without a recorded commit skips that range's check.

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
M2C_BASE_RE = re.compile(r"M2c 시작 커밋: `([0-9a-f]{40})`")
WHOLE_FILE_EDITS = frozenset({"CHANGELOG.md", "CLAUDE.md"})
M2C_WHOLE_FILE_EDITS = frozenset({
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "CLAUDE.md",
    "CHANGELOG.md",
    "skills/blog/SKILL.md",
    "tests/test_skill_frontmatter.py",
})
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


# ------------------------------------------------------------------ upstream scope per milestone

def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def upstream_scope_problems(root: Path, upstream_base: str, since: str, until: str | None = None,
                            whole_file_edits: frozenset[str] = WHOLE_FILE_EDITS) -> list[str]:
    """Upstream files changed from `since` to `until` (default: the working tree) beyond their version lines.

    Paths in `whole_file_edits` are exact repo-relative paths that may change freely in that range.
    """
    span = (since,) if until is None else (since, until)
    problems = []
    for path in sorted(p for p in _git(root, "diff", "--name-only", "-z", *span).split("\0") if p):
        upstream = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{upstream_base}:{path}"],
                                  capture_output=True, check=False).returncode == 0
        if path in whole_file_edits or not upstream:
            continue
        lines = _git(root, "diff", "-a", "-U0", *span, "--", path).splitlines()
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


# ------------------------------------------------------------------ M2c upstream scope

M2C_FIXTURE_FILES = (
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "CLAUDE.md",
    "CHANGELOG.md",
    "skills/blog/SKILL.md",
    "tests/test_skill_frontmatter.py",
    "skills/blog-write/SKILL.md",
    "skills/blog/references/notes.md",
    "docs/CLAUDE.md",
)


@pytest.fixture
def m2c_repo(repo) -> tuple[Path, str, str]:
    """The upstream base, then an M2c start commit that adds the M2c-scoped upstream paths upstream."""
    root, _ = repo
    for relative in M2C_FIXTURE_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(f"# {relative}\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "upstream with skills")
    base = _git(root, "rev-parse", "HEAD").strip()
    (root / "fork.md").write_text("fork notes\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "m2c start")
    return root, base, _git(root, "rev-parse", "HEAD").strip()


def test_m2c_allows_edits_beyond_version_lines_only_in_its_six_upstream_paths(m2c_repo) -> None:
    root, base, m2c = m2c_repo
    for relative in M2C_WHOLE_FILE_EDITS:
        with (root / relative).open("a", encoding="utf-8") as handle:
            handle.write("- fork skill sentence\n")
    assert len(M2C_WHOLE_FILE_EDITS) == 6
    assert upstream_scope_problems(root, base, m2c, whole_file_edits=M2C_WHOLE_FILE_EDITS) == []


@pytest.mark.parametrize("relative", ["skills/blog-write/SKILL.md", "skills/blog/references/notes.md",
                                      "docs/CLAUDE.md", "plugin.json"])
def test_m2c_rejects_edits_beyond_version_lines_elsewhere(m2c_repo, relative: str) -> None:
    root, base, m2c = m2c_repo
    with (root / relative).open("a", encoding="utf-8") as handle:
        handle.write("- out of scope\n")
    assert upstream_scope_problems(root, base, m2c, whole_file_edits=M2C_WHOLE_FILE_EDITS) == [
        f"upstream file changed beyond its version line: {relative}"]


def test_m2c_still_allows_version_line_edits_elsewhere(m2c_repo) -> None:
    root, base, m2c = m2c_repo
    (root / "plugin.json").write_text('{\n  "name": "x",\n  "version": "2.3.1-kit.4"\n}\n')
    assert upstream_scope_problems(root, base, m2c, whole_file_edits=M2C_WHOLE_FILE_EDITS) == []


def test_m2b_range_ends_at_the_m2c_start_and_ignores_later_changes(m2c_repo) -> None:
    root, base, m2c = m2c_repo
    (root / "skills" / "blog-write" / "SKILL.md").write_text("# rewritten after M2c started\n")
    _git(root, "commit", "-q", "-am", "m2c work")
    (root / "plugin.json").write_text('{\n  "name": "changed in the working tree"\n}\n')
    assert upstream_scope_problems(root, base, base, until=m2c) == []
    assert upstream_scope_problems(root, base, m2c, whole_file_edits=M2C_WHOLE_FILE_EDITS) == [
        "upstream file changed beyond its version line: plugin.json",
        "upstream file changed beyond its version line: skills/blog-write/SKILL.md",
    ]


def test_m2b_range_still_catches_edits_made_before_the_m2c_start(m2c_repo) -> None:
    root, base, m2c = m2c_repo
    (root / "skills" / "blog-write" / "SKILL.md").write_text("# rewritten during M2b\n")
    _git(root, "commit", "-q", "-am", "m2b work")
    m2c_later = _git(root, "rev-parse", "HEAD").strip()
    assert upstream_scope_problems(root, base, m2c, until=m2c_later) == [
        "upstream file changed beyond its version line: skills/blog-write/SKILL.md"]


def _fork_commits(*patterns: re.Pattern[str]) -> list[str]:
    text = (ROOT / "FORK.md").read_text(encoding="utf-8")
    commits = []
    for pattern in patterns:
        found = pattern.search(text)
        assert found, f"FORK.md has no line matching {pattern.pattern}"
        commits.append(found.group(1))
    for commit in commits:
        probe = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{commit}^{{commit}}"],
                               capture_output=True, check=False)
        if probe.returncode != 0:
            pytest.skip(f"commit {commit} is not in this clone")
    return commits


def test_m2b_changes_upstream_files_only_in_version_surfaces_changelog_and_developer_docs() -> None:
    base, m2b, m2c = _fork_commits(BASE_RE, M2B_BASE_RE, M2C_BASE_RE)
    assert upstream_scope_problems(ROOT, base, m2b, until=m2c) == []


def test_m2c_changes_upstream_files_only_in_version_surfaces_and_its_six_paths() -> None:
    base, m2c = _fork_commits(BASE_RE, M2C_BASE_RE)
    assert upstream_scope_problems(ROOT, base, m2c, whole_file_edits=M2C_WHOLE_FILE_EDITS) == []
