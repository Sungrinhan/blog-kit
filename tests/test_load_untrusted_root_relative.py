"""WR-04: the root-context loader resolves a relative path against --root.

`load_untrusted_root.py --root <folder> BRAND.md` used to resolve BRAND.md
against cwd, so the same command failed unless it ran from the blog folder
(PAIN-POINTS 18). With --root, a relative path is now resolved against the
root; the root containment check, the basename allowlist and the symlink
refusal run unchanged afterwards. Without --root a relative path still means
cwd, and an absolute path is used as given.

CLI tests only: each runs the loader with an explicit cwd outside the root.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from windows_support import make_symlink

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "scripts" / "load_untrusted_root.py"


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HELPER), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
        check=False,
    )


def _project(tmp_path: Path) -> tuple[Path, Path]:
    """A project root holding BRAND.md and an unrelated cwd beside it."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "BRAND.md").write_text("audience: developers\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    return project, elsewhere


def test_relative_path_resolves_against_root_from_another_cwd(tmp_path: Path):
    project, elsewhere = _project(tmp_path)
    result = _run(["--root", str(project), "--json", "BRAND.md"], cwd=elsewhere)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert Path(payload["path"]) == project.resolve() / "BRAND.md"
    assert "audience: developers" in payload["fenced"]


def test_relative_root_and_relative_path(tmp_path: Path):
    project, _ = _project(tmp_path)
    result = _run(["--root", "project", "BRAND.md"], cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "audience: developers" in result.stdout


def test_relative_path_escaping_root_is_refused(tmp_path: Path):
    project, elsewhere = _project(tmp_path)
    (tmp_path / "BRAND.md").write_text("outside\n", encoding="utf-8")
    nested = project / "docs"
    nested.mkdir()
    for hostile in ("../BRAND.md", "docs/../../BRAND.md"):
        result = _run(["--root", str(project), hostile], cwd=elsewhere)
        assert result.returncode != 0, hostile
        assert "outside project root" in result.stderr.lower(), hostile
        assert "outside" not in result.stdout


def test_cwd_file_is_not_read_when_root_is_given(tmp_path: Path):
    project, elsewhere = _project(tmp_path)
    (elsewhere / "VOICE.md").write_text("cwd voice\n", encoding="utf-8")
    result = _run(["--root", str(project), "VOICE.md"], cwd=elsewhere)
    assert result.returncode != 0
    assert "not found" in result.stderr.lower()
    assert "cwd voice" not in result.stdout


def test_without_root_relative_path_still_means_cwd(tmp_path: Path):
    project, _ = _project(tmp_path)
    result = _run(["BRAND.md"], cwd=project)
    assert result.returncode == 0, result.stderr
    assert "audience: developers" in result.stdout


def test_without_root_cwd_outside_project_is_not_found(tmp_path: Path):
    _, elsewhere = _project(tmp_path)
    result = _run(["BRAND.md"], cwd=elsewhere)
    assert result.returncode != 0
    assert "not found" in result.stderr.lower()


def test_absolute_path_with_root_is_unchanged(tmp_path: Path):
    project, elsewhere = _project(tmp_path)
    inside = _run(["--root", str(project), str(project / "BRAND.md")], cwd=elsewhere)
    assert inside.returncode == 0, inside.stderr
    (tmp_path / "BRAND.md").write_text("outside\n", encoding="utf-8")
    outside = _run(["--root", str(project), str(tmp_path / "BRAND.md")], cwd=elsewhere)
    assert outside.returncode != 0
    assert "outside project root" in outside.stderr.lower()


def test_basename_allowlist_applies_to_root_relative_path(tmp_path: Path):
    project, elsewhere = _project(tmp_path)
    (project / "notes.md").write_text("notes\n", encoding="utf-8")
    result = _run(["--root", str(project), "notes.md"], cwd=elsewhere)
    assert result.returncode != 0
    assert "not in allowlist" in result.stderr.lower()


def test_symlink_refused_for_root_relative_path(tmp_path: Path):
    project, elsewhere = _project(tmp_path)
    real = project / "real.md"
    real.write_text("linked\n", encoding="utf-8")
    make_symlink(real, project / "VOICE.md")
    result = _run(["--root", str(project), "VOICE.md"], cwd=elsewhere)
    assert result.returncode != 0
    assert "symlink" in result.stderr.lower()
    assert "linked" not in result.stdout
