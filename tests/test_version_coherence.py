"""Version-coherence regression test.

Asserts that the project version is the same string across all four
canonical surfaces: pyproject.toml, plugin.json, CITATION.cff, and the
orchestrator skills/blog/SKILL.md frontmatter.

Added v1.8.5 (6TH-AUDIT-010): the v1.8.4 release added the same check
as a CI YAML heredoc. Moving the check into pytest gives local devs the
same signal CI produces, so `pytest` before push catches the drift.

Stdlib + pytest only.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _read_pyproject_version(root: Path = ROOT) -> str | None:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else None


def _read_plugin_version(root: Path = ROOT) -> str | None:
    with open(root / ".claude-plugin" / "plugin.json", encoding="utf-8") as f:
        return json.load(f).get("version")


def _read_citation_version(root: Path = ROOT) -> str | None:
    """Strip optional YAML single/double quotes (v1.8.6: 7TH-AUDIT-011)."""
    text = (root / "CITATION.cff").read_text(encoding="utf-8")
    m = re.search(r'^version:\s*[\'"]?([0-9][^\'"\s]+)[\'"]?', text, re.MULTILINE)
    return m.group(1) if m else None


def _read_skill_md_version(root: Path = ROOT) -> str | None:
    text = (root / "skills" / "blog" / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r'^\s*version:\s*"?([^"\s]+)"?', text, re.MULTILINE)
    return m.group(1) if m else None


def _release_module():
    spec = importlib.util.spec_from_file_location(
        "release_version_rule", ROOT / "scripts" / "validate_public_release.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def version_surface_problems(root: Path = ROOT) -> list[str]:
    """Compare every surface with the plugin version.

    Fork: the Python package carries the PEP 440 form of the plugin version
    (2.3.1-kit.N becomes 2.3.1+kit.N), mapped by the release validator's
    single rule. Upstream versions without the suffix map to themselves.
    """
    plugin = _read_plugin_version(root)
    versions = {
        "pyproject.toml": _read_pyproject_version(root),
        "CITATION.cff": _read_citation_version(root),
        "skills/blog/SKILL.md": _read_skill_md_version(root),
    }
    if plugin is None:
        return ["version field could not be extracted from: .claude-plugin/plugin.json"]
    missing = [k for k, v in versions.items() if v is None]
    if missing:
        return [f"version field could not be extracted from: {missing}"]
    expected = {k: plugin for k in versions}
    expected["pyproject.toml"] = _release_module().python_version(plugin)
    return [
        f"{k}: {versions[k]!r} does not match plugin.json {plugin!r} (expected {expected[k]!r})"
        for k in versions
        if versions[k] != expected[k]
    ]


def test_all_version_surfaces_aligned() -> None:
    """The same version must appear in pyproject.toml (in its Python form),
    plugin.json, CITATION.cff, and skills/blog/SKILL.md metadata.

    v1.8.4 (5TH-AUDIT-001) found skills/blog/SKILL.md frozen at "1.8.0"
    while every other surface had moved through 1.8.1, 1.8.2, 1.8.3.
    This test prevents that recurring.
    """
    problems = version_surface_problems()
    assert not problems, "version surfaces disagree:\n" + "\n".join(f"  {p}" for p in problems)


def _version_fixture(root: Path, plugin: str, python: str) -> Path:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / "skills" / "blog").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": plugin}))
    (root / "pyproject.toml").write_text(f'[project]\nversion = "{python}"\n')
    (root / "CITATION.cff").write_text(f"version: {plugin}\n")
    (root / "skills" / "blog" / "SKILL.md").write_text(f'---\nmetadata:\n  version: "{plugin}"\n---\n')
    return root


@pytest.mark.parametrize(
    ("plugin", "python"),
    [("2.3.1-kit.1", "2.3.1+kit.1"), ("2.3.1-kit.12", "2.3.1+kit.12"), ("2.3.1", "2.3.1")],
)
def test_fork_and_upstream_version_forms_align(tmp_path: Path, plugin: str, python: str) -> None:
    assert version_surface_problems(_version_fixture(tmp_path, plugin, python)) == []


@pytest.mark.parametrize(
    ("plugin", "python"),
    [
        ("2.3.1-kit.1", "2.3.1+kit.2"),
        ("2.3.1-kit.1", "2.3.1-kit.1"),
        ("2.3.1-kit.1", "2.3.1"),
        ("2.3.1", "2.3.1+kit.1"),
    ],
)
def test_mismatched_fork_versions_fail(tmp_path: Path, plugin: str, python: str) -> None:
    problems = version_surface_problems(_version_fixture(tmp_path, plugin, python))
    assert [p.split(":")[0] for p in problems] == ["pyproject.toml"]


def test_version_string_matches_semver_pattern() -> None:
    """The shared version must be valid semver (X.Y.Z optional pre-release).

    Fork: read from plugin.json; pyproject.toml holds its Python form.
    """
    version = _read_plugin_version()
    assert version is not None
    assert re.match(r"^\d+\.\d+\.\d+(-\S+)?$", version), (
        f"version {version!r} does not match semver pattern X.Y.Z[-pre]"
    )


def test_all_sub_skill_versions_match_project_version() -> None:
    """v1.8.6 (7TH-AUDIT-003): every skills/*/SKILL.md `version:` field
    must equal the project version. Pre-v1.8.6, 10 sub-skill SKILL.md
    files had stale per-skill versions (1.0.0, 1.4.0, 1.7.0) that were
    never bumped through the v1.8.x cycle. The version-coherence test
    only covered the orchestrator, missing all 10 sub-skill surfaces.
    """
    target = _read_plugin_version()
    assert target is not None
    skill_md_files = sorted((ROOT / "skills").glob("*/SKILL.md"))
    assert len(skill_md_files) >= 30, (
        f"unexpectedly few SKILL.md files: {len(skill_md_files)}"
    )
    mismatches = []
    for f in skill_md_files:
        text = f.read_text(encoding="utf-8")
        # Match the YAML frontmatter version field; tolerate quote styles.
        m = re.search(
            r'^\s+version:\s*[\'"]?([0-9][^\'"\s]+)[\'"]?',
            text, re.MULTILINE,
        )
        if m is None:
            # Some SKILL.md files may not declare metadata.version at all;
            # that is acceptable. We only flag drift, not absence.
            continue
        if m.group(1) != target:
            mismatches.append((f.relative_to(ROOT), m.group(1)))
    assert not mismatches, (
        f"sub-skill SKILL.md versions disagree with plugin.json "
        f"({target}):\n"
        + "\n".join(f"  {p}: {v}" for p, v in mismatches)
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
