"""Windows CI base: the runner capabilities the Windows regression tests rely on.

The `windows-tests` CI job runs only the files listed in
tests/windows/pytest-targets.txt (`python -m pytest @tests/windows/pytest-targets.txt`).
The renderer and chart-writer tests on that list create symlinks and compare
device/inode IDs, so this file checks first that the runner can do both.

Creating a symlink on Windows needs a privilege or Developer Mode. Locally the
symlink test skips with the reason; when `CI` is set it fails instead, so a
runner without the capability cannot hide the link tests behind skips.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from windows_support import make_symlink

ROOT = Path(__file__).resolve().parent.parent
TARGETS = ROOT / "tests" / "windows" / "pytest-targets.txt"


def test_runner_creates_file_symlink(tmp_path):
    target = tmp_path / "target.md"
    target.write_text("body", encoding="utf-8")
    link = tmp_path / "link.md"

    make_symlink(target, link)

    assert stat.S_ISLNK(os.lstat(link).st_mode)
    assert link.is_symlink()
    assert link.read_text(encoding="utf-8") == "body"


def test_lstat_and_fstat_give_matching_nonzero_ids(tmp_path):
    path = tmp_path / "file.md"
    path.write_text("body", encoding="utf-8")

    before = os.lstat(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        opened = os.fstat(fd)
    finally:
        os.close(fd)

    assert before.st_dev != 0 and before.st_ino != 0
    assert (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino)


def test_windows_target_list_names_existing_test_files():
    lines = TARGETS.read_text(encoding="utf-8").splitlines()

    # pytest reads an @file one argument per line, with no comment syntax.
    assert lines, "target list is empty"
    assert all(line.strip() == line and line for line in lines), lines
    assert len(lines) == len(set(lines)), "duplicate target"
    for line in lines:
        assert line.startswith("tests/") and line.endswith(".py"), line
        assert (ROOT / line).is_file(), f"missing target: {line}"
    assert "tests/test_windows_runner_capability.py" in lines
