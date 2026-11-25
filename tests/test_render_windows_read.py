"""WR-02: the renderer reads its markdown source without os.O_NOFOLLOW.

Windows has no O_NOFOLLOW, so `blog_render.py` used to fail before reading
anything (PAIN-POINTS 3). On such platforms the source is now checked by
identity: lstat before open (refusing symlinks and reparse points), fstat after
open, and lstat again; the three device/inode pairs must match. POSIX keeps the
O_NOFOLLOW open.

The no-O_NOFOLLOW branch is tested on every platform by importing the module and
removing `os.O_NOFOLLOW` with monkeypatch (a subprocess would not see the patch).
The CLI test runs the real platform branch, so on the Windows CI job it is the
shim-free render itself.
"""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from windows_support import make_symlink

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "blog_render.py"
SPEC = importlib.util.spec_from_file_location("render_windows_read", SCRIPT)
RENDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RENDER)

SOURCE = (
    "---\ntitle: Windows Read\ndescription: A short description of the post.\n"
    "date: 2026-11-25\nauthor: Alex Rivera\nslug: windows-read\n---\n\n"
    "본문 문단입니다.\n"
)
REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@pytest.fixture
def no_nofollow(monkeypatch):
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "windows-read.md"
    path.write_bytes(SOURCE.encode("utf-8"))
    return path


def _fake_stat(real, **changes):
    fields = {name: getattr(real, name) for name in ("st_mode", "st_dev", "st_ino", "st_size")}
    fields["st_file_attributes"] = getattr(real, "st_file_attributes", 0)
    fields.update(changes)
    return SimpleNamespace(**fields)


def test_cli_renders_html_on_this_platform(source, tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--md", str(source), "--out-dir", str(tmp_path), "--pdf-engine", "none"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    page = (tmp_path / "windows-read.html").read_text(encoding="utf-8")
    assert "본문 문단입니다." in page


def test_without_nofollow_reads_a_regular_file(no_nofollow, source):
    assert RENDER._read_md_bytes_safely(source) == SOURCE.encode("utf-8")


def test_without_nofollow_refuses_a_symlink(no_nofollow, source, tmp_path):
    link = tmp_path / "link.md"
    make_symlink(source, link)

    with pytest.raises(ValueError, match="refusing to follow symlink"):
        RENDER._read_md_bytes_safely(link)


def test_without_nofollow_refuses_a_reparse_point(no_nofollow, source, monkeypatch):
    real_lstat = os.lstat

    def lstat(path, *args, **kwargs):
        return _fake_stat(real_lstat(path), st_file_attributes=REPARSE)

    monkeypatch.setattr(os, "lstat", lstat)
    with pytest.raises(ValueError, match="refusing to follow symlink"):
        RENDER._read_md_bytes_safely(source)


def test_without_nofollow_refuses_a_swap_between_check_and_open(no_nofollow, source, tmp_path, monkeypatch):
    other = tmp_path / "other.md"
    other.write_bytes(b"swapped in")
    real_open = os.open

    def open_after_swap(path, flags, *args, **kwargs):
        os.replace(other, path)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", open_after_swap)
    with pytest.raises(ValueError, match="refusing to follow symlink"):
        RENDER._read_md_bytes_safely(source)


@pytest.mark.parametrize("call", [1, 2])
def test_without_nofollow_refuses_mismatched_lstat_ids(no_nofollow, source, monkeypatch, call):
    real_lstat = os.lstat
    calls = []

    def lstat(path, *args, **kwargs):
        calls.append(path)
        st = real_lstat(path, *args, **kwargs)
        return _fake_stat(st, st_ino=st.st_ino + 1) if len(calls) == call else st

    monkeypatch.setattr(os, "lstat", lstat)
    with pytest.raises(ValueError, match="refusing to follow symlink"):
        RENDER._read_md_bytes_safely(source)


def test_without_nofollow_refuses_a_link_after_open(no_nofollow, source, monkeypatch):
    real_lstat = os.lstat
    calls = []

    def lstat(path, *args, **kwargs):
        calls.append(path)
        st = real_lstat(path, *args, **kwargs)
        if len(calls) == 2:
            return _fake_stat(st, st_mode=stat.S_IFLNK | 0o777)
        return st

    monkeypatch.setattr(os, "lstat", lstat)
    with pytest.raises(ValueError, match="refusing to follow symlink"):
        RENDER._read_md_bytes_safely(source)


def test_without_nofollow_refuses_a_non_regular_path_after_open(no_nofollow, source, monkeypatch):
    real_lstat = os.lstat
    calls = []

    def lstat(path, *args, **kwargs):
        calls.append(path)
        st = real_lstat(path, *args, **kwargs)
        if len(calls) == 2:
            return _fake_stat(st, st_mode=stat.S_IFDIR | 0o755)
        return st

    monkeypatch.setattr(os, "lstat", lstat)
    with pytest.raises(ValueError, match="not a regular file"):
        RENDER._read_md_bytes_safely(source)


def test_without_nofollow_refuses_mismatched_fstat_id(no_nofollow, source, monkeypatch):
    real_fstat = os.fstat

    def fstat(fd):
        st = real_fstat(fd)
        return _fake_stat(st, st_dev=st.st_dev + 1)

    monkeypatch.setattr(os, "fstat", fstat)
    with pytest.raises(ValueError, match="refusing to follow symlink"):
        RENDER._read_md_bytes_safely(source)


def test_without_nofollow_refuses_a_directory(no_nofollow, tmp_path):
    folder = tmp_path / "folder.md"
    folder.mkdir()

    with pytest.raises(ValueError, match="not a regular file"):
        RENDER._read_md_bytes_safely(folder)


def test_without_nofollow_keeps_the_size_cap(no_nofollow, source, monkeypatch):
    monkeypatch.setattr(RENDER, "MAX_MD_BYTES", 8)

    with pytest.raises(ValueError, match="exceeds 8 bytes"):
        RENDER._read_md_bytes_safely(source)


def test_cli_refuses_a_symlinked_source_on_this_platform(source, tmp_path):
    link = tmp_path / "link.md"
    make_symlink(source, link)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--md", str(link), "--out-dir", str(out_dir), "--pdf-engine", "none"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode != 0
    assert "refusing to follow symlink" in result.stderr
    assert not list(out_dir.glob("*.html"))
