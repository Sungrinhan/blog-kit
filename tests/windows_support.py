"""Shared helpers for the Windows regression tests (fork).

Creating a symlink on Windows needs a privilege or Developer Mode. Locally a
test that needs one skips with the reason; when `CI` is set it fails instead,
so a runner without the capability cannot hide the link tests behind skips.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def make_symlink(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        if os.environ.get("CI"):
            pytest.fail(f"CI runner cannot create symlinks: {exc!r}")
        pytest.skip(f"no symlink privilege on this machine: {exc!r}")
