"""WR-03: chart file writes without dir_fd, and UTF-8/LF chart stdout.

Windows has no dir_fd support, so `generate_chart_svg.py --output` used to fail
opening the parent directory (PAIN-POINTS 4), and chart HTML sent to stdout
followed the console code page with CRLF (PAIN-POINTS 5). Without dir_fd the
writer now refuses a linked parent, creates the temp file exclusively beside the
target, and compares the parent's lstat device/inode right before and right
after the replace.

The no-dir_fd branch is tested on every platform by importing the module and
emptying `os.supports_dir_fd` with monkeypatch (a subprocess would not see the
patch). The CLI tests run the real platform branch, so on the Windows CI job
they are the shim-free chart write and stdout themselves.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from windows_support import REPARSE, fake_stat, make_symlink

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "blog-chart" / "scripts" / "generate_chart_svg.py"
SPEC = importlib.util.spec_from_file_location("chart_windows_write", SCRIPT)
CHART = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHART)

KOREAN = "지연 시간 분포"
CONFIG = {
    "type": "horizontal_bar",
    "title": KOREAN,
    "data": [{"label": "첫째", "value": 3}, {"label": "둘째", "value": 5}],
}
TEXT = f"<figure>{KOREAN}</figure>\n"


@pytest.fixture
def no_dir_fd(monkeypatch):
    monkeypatch.setattr(os, "supports_dir_fd", set())


@pytest.fixture
def chart_root(tmp_path):
    (tmp_path / "chart.json").write_text(json.dumps(CONFIG, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _leftovers(folder):
    return sorted(p.name for p in folder.iterdir() if p.name.endswith(".tmp"))


def _patch_parent_lstat(monkeypatch, parent, call, **changes):
    """Change the parent's lstat result on its `call`-th lstat (1-based)."""
    real_lstat = os.lstat
    calls = []

    def lstat(path, *args, **kwargs):
        st = real_lstat(path, *args, **kwargs)
        if Path(path) == parent:
            calls.append(path)
            if len(calls) == call:
                return fake_stat(st, **changes)
        return st

    monkeypatch.setattr(os, "lstat", lstat)


@pytest.mark.parametrize("missing", ["open", "rename", "unlink"])
def test_one_missing_dir_fd_function_selects_the_identity_path(tmp_path, monkeypatch, missing):
    # os.replace shares renameat with os.rename; CPython registers only rename.
    # Only the identity path lstats the parent, so a reparse-point answer there
    # shows which path ran.
    full = {os.open, os.rename, os.unlink}
    monkeypatch.setattr(os, "supports_dir_fd", full - {getattr(os, missing)})
    _patch_parent_lstat(monkeypatch, tmp_path, 1, st_file_attributes=REPARSE)

    with pytest.raises(CHART.ChartError, match="Refusing linked or non-directory output directory"):
        CHART.write_text_atomic_no_follow(tmp_path / "chart.html", TEXT)
    assert list(tmp_path.iterdir()) == []


def test_without_dir_fd_writes_utf8_and_leaves_no_temp(no_dir_fd, tmp_path):
    target = tmp_path / "chart.html"
    target.write_text("old", encoding="utf-8")

    CHART.write_text_atomic_no_follow(target, TEXT)

    assert target.read_bytes() == TEXT.encode("utf-8")
    assert _leftovers(tmp_path) == []


def test_without_dir_fd_refuses_a_symlinked_parent(no_dir_fd, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    make_symlink(real, linked)

    with pytest.raises(CHART.ChartError, match="Refusing linked or non-directory output directory"):
        CHART.write_text_atomic_no_follow(linked / "chart.html", TEXT)
    assert list(real.iterdir()) == []


def test_without_dir_fd_refuses_a_reparse_point_parent(no_dir_fd, tmp_path, monkeypatch):
    _patch_parent_lstat(monkeypatch, tmp_path, 1, st_file_attributes=REPARSE)

    with pytest.raises(CHART.ChartError, match="Refusing linked or non-directory output directory"):
        CHART.write_text_atomic_no_follow(tmp_path / "chart.html", TEXT)
    assert list(tmp_path.iterdir()) == []


def test_without_dir_fd_refuses_a_parent_swap_before_replace(no_dir_fd, tmp_path, monkeypatch):
    target = tmp_path / "chart.html"
    _patch_parent_lstat(monkeypatch, tmp_path, 2, st_ino=os.lstat(tmp_path).st_ino + 1)

    with pytest.raises(CHART.ChartError, match="changed before replace"):
        CHART.write_text_atomic_no_follow(target, TEXT)
    assert not target.exists()
    assert _leftovers(tmp_path) == []


def test_without_dir_fd_refuses_a_parent_turned_link_before_replace(no_dir_fd, tmp_path, monkeypatch):
    target = tmp_path / "chart.html"
    _patch_parent_lstat(monkeypatch, tmp_path, 2, st_mode=stat.S_IFLNK | 0o777)

    with pytest.raises(CHART.ChartError, match="changed before replace"):
        CHART.write_text_atomic_no_follow(target, TEXT)
    assert not target.exists()
    assert _leftovers(tmp_path) == []


def test_without_dir_fd_reports_a_parent_swap_after_replace(no_dir_fd, tmp_path, monkeypatch):
    _patch_parent_lstat(monkeypatch, tmp_path, 3, st_dev=os.lstat(tmp_path).st_dev + 1)

    with pytest.raises(CHART.ChartError, match="changed after replace"):
        CHART.write_text_atomic_no_follow(tmp_path / "chart.html", TEXT)
    assert _leftovers(tmp_path) == []


def test_without_dir_fd_removes_the_temp_file_when_replace_fails(no_dir_fd, tmp_path, monkeypatch):
    def failing_replace(src, dst, *args, **kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="replace failed"):
        CHART.write_text_atomic_no_follow(tmp_path / "chart.html", TEXT)
    assert _leftovers(tmp_path) == []


def test_without_dir_fd_cli_writes_the_chart(no_dir_fd, chart_root, capsys):
    code = CHART.main(["--root", str(chart_root), "--input", "chart.json", "--output", "out/chart.html", "--json"])

    assert code == 0, capsys.readouterr()
    assert json.loads(capsys.readouterr().out)["output"] == "out/chart.html"
    assert KOREAN in (chart_root / "out" / "chart.html").read_bytes().decode("utf-8")


def test_without_dir_fd_cli_still_rejects_an_output_outside_root(no_dir_fd, chart_root, capsys):
    outside = chart_root.parent / f"{chart_root.name}-outside.html"

    code = CHART.main(["--root", str(chart_root), "--input", "chart.json", "--output", str(outside), "--json"])

    assert code == 1
    assert "escapes root" in json.loads(capsys.readouterr().out)["error"]
    assert not outside.exists()


def _run_cli(chart_root, *extra, io_encoding):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    if io_encoding:
        env["PYTHONIOENCODING"] = io_encoding
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(chart_root), "--input", "chart.json", *extra],
        capture_output=True, env=env,
    )


def test_cli_writes_the_chart_file_on_this_platform(chart_root):
    result = _run_cli(chart_root, "--output", "chart.html", "--json", io_encoding=None)

    assert result.returncode == 0, result.stderr
    written = (chart_root / "chart.html").read_bytes()
    assert KOREAN in written.decode("utf-8")
    assert b"\r" not in written
    assert written.endswith(b"</figure>\n")
    assert _leftovers(chart_root) == []


@pytest.mark.parametrize("io_encoding", [None, "cp1252"])
def test_cli_stdout_is_utf8_with_lf(chart_root, io_encoding):
    result = _run_cli(chart_root, io_encoding=io_encoding)

    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert b"\r" not in out
    text = out.decode("utf-8")
    assert text.startswith('<figure class="blog-chart">')
    assert KOREAN in text
    assert text.endswith("</figure>\n")


def test_cli_json_stdout_keeps_its_format(chart_root):
    result = _run_cli(chart_root, "--json", io_encoding="cp1252")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert set(payload) == {"status", "type", "bytes", "content"}
    assert KOREAN in payload["content"]
