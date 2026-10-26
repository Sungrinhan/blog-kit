"""S5: the fork's adapters/ folder gets the same release-surface scan as upstream folders.

scripts/validate_public_release.py scans skills/, agents/, scripts/ and the
other upstream distribution folders for private repository URLs, private
marketplace slugs and raw installer links. It does not know adapters/, which
the fork adds and ships in the plugin, so this test applies the same patterns
there without patching the upstream checker.

Stdlib + pytest only.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADAPTERS = ROOT / "adapters"


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
