"""Shared helpers for the Windows regression tests (fork).

Creating a symlink on Windows needs a privilege or Developer Mode. Locally a
test that needs one skips with the reason; when `CI` is set it fails instead,
so a runner without the capability cannot hide the link tests behind skips.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def fake_stat(real, **changes):
    """Copy the stat fields the Windows branches read, with some replaced."""
    fields = {name: getattr(real, name) for name in ("st_mode", "st_dev", "st_ino", "st_size")}
    fields["st_file_attributes"] = getattr(real, "st_file_attributes", 0)
    fields.update(changes)
    return SimpleNamespace(**fields)


def make_symlink(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        if os.environ.get("CI"):
            pytest.fail(f"CI runner cannot create symlinks: {exc!r}")
        pytest.skip(f"no symlink privilege on this machine: {exc!r}")
