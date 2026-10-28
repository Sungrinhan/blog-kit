"""scripts/confidential_check.py through its subprocess interface.

Each test points HOME at a temp folder holding a fake term list
(~/.config/blog-kit/confidential.txt; the script has no path option) and
scans files under a temp root. The JSON output and exit code are the
interface. The planted terms and the lines that hold them must never show in
stdout or stderr.

Stdlib + pytest only. No network.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "confidential_check.py"
# Fake terms only. Never real ones.
TERM_A = "가짜사내코드명"
TERM_B = "Fooproject"
SECRET_LINE_TEXT = "이 줄에는 가짜사내코드명이 들어 있다"


class World:
    def __init__(self, tmp: Path) -> None:
        self.home = tmp / "home"
        self.list_path = self.home / ".config" / "blog-kit" / "confidential.txt"
        self.root = tmp / "blog"
        self.root.mkdir(parents=True)

    def terms(self, text: str | bytes) -> None:
        self.list_path.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode("utf-8") if isinstance(text, str) else text
        self.list_path.write_bytes(data)

    def write(self, relative: str, text: str | bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
        return path

    def run(self, *args: str) -> tuple[int, dict, str]:
        env = {k: v for k, v in os.environ.items()}
        env["HOME"] = str(self.home)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True, text=True, env=env, cwd=self.root, timeout=60,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = {}
        return result.returncode, payload, result.stdout + result.stderr


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _no_leak(output: str) -> None:
    for secret in (TERM_A, TERM_B, TERM_B.casefold(), SECRET_LINE_TEXT):
        assert secret not in output


# ------------------------------------------------------------------ term list state


def test_a_list_with_terms_is_ok(world: World) -> None:
    world.terms(f"# a comment\n\n  {TERM_A}  \n{TERM_B}\n")
    rc, payload, output = world.run("list")
    assert (rc, payload["status"], payload["list"]) == (0, "pass", "ok"), output
    _no_leak(output)


def _symlinked(w: World) -> None:
    real = w.home / "elsewhere.txt"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text(TERM_A + "\n", encoding="utf-8")
    w.list_path.parent.mkdir(parents=True, exist_ok=True)
    w.list_path.symlink_to(real)


def _folder(w: World) -> None:
    w.list_path.mkdir(parents=True)


LIST_FAILURES = [
    ("missing", lambda w: None, "list_missing"),
    ("symlink", _symlinked, "list_symlink"),
    ("folder", _folder, "list_not_regular"),
    ("FIFO", lambda w: (w.list_path.parent.mkdir(parents=True), os.mkfifo(w.list_path)), "list_not_regular"),
    ("over 256KB", lambda w: w.terms(TERM_A + "\n" + "x" * (256 * 1024)), "list_too_large"),
    ("not utf-8", lambda w: w.terms(b"\xff\xfe" + TERM_A.encode("utf-16-le")), "list_not_utf8"),
    ("only comments", lambda w: w.terms("# nothing here\n\n   \n# " + TERM_A + "\n"), "list_empty"),
]


@pytest.mark.parametrize("setup,code", [case[1:] for case in LIST_FAILURES], ids=[case[0] for case in LIST_FAILURES])
def test_an_unusable_list_blocks(world: World, setup, code: str) -> None:
    setup(world)
    rc, payload, output = world.run("list")
    assert (rc, payload["status"], payload["list"]) == (1, "block", code), output
    _no_leak(output)


# ------------------------------------------------------------------ scan: terms block

POST = "src/content/posts/first-post.md"
SVG = "src/assets/posts/first-post/diagram.svg"
PNG = "src/assets/posts/first-post/photo.png"


def _terms(w: World) -> None:
    # Stored decomposed (NFD) and in another case than the text uses.
    w.terms(f"# fake terms\n{unicodedata.normalize('NFD', TERM_A)}\n{TERM_B.upper()}\n")


def test_terms_block_with_category_file_and_line_only(world: World) -> None:
    _terms(world)
    world.write(POST, f"---\ntitle: \"x\"\n---\n\nclean line\n{SECRET_LINE_TEXT}\nthe {TERM_B.lower()} build\n")
    world.write(SVG, f"<svg><text>{TERM_B}</text></svg>\n")
    rc, payload, output = world.run("scan", "--root", str(world.root), POST, SVG)
    assert (rc, payload["status"], payload["list"]) == (1, "block", "ok"), output
    assert payload["blocks"] == [
        {"category": "confidential_term", "file": POST, "line": 6},
        {"category": "confidential_term", "file": POST, "line": 7},
        {"category": "confidential_term", "file": SVG, "line": 1},
    ]
    assert payload["errors"] == []
    _no_leak(output)


def test_a_clean_file_passes(world: World) -> None:
    _terms(world)
    world.write(POST, "---\ntitle: \"x\"\n---\n\nnothing to see\n")
    rc, payload, output = world.run("scan", "--root", str(world.root), POST)
    assert (rc, payload["status"], payload["blocks"], payload["warnings"]) == (0, "pass", [], []), output


def test_an_unusable_list_blocks_the_scan_without_reading_files(world: World) -> None:
    world.write(POST, f"{SECRET_LINE_TEXT}\n")
    rc, payload, output = world.run("scan", "--root", str(world.root), POST)
    assert (rc, payload["status"], payload["list"], payload["blocks"]) == (1, "block", "list_missing", []), output
    _no_leak(output)


# ------------------------------------------------------------------ scan: could not check


def _outside(w: World) -> str:
    outside = w.root.parent / "outside.md"
    outside.write_text(SECRET_LINE_TEXT + "\n", encoding="utf-8")
    return str(outside)


def _dotdot(w: World) -> str:
    _outside(w)
    return "../outside.md"


def _symlink(w: World) -> str:
    target = Path(_outside(w))
    (w.root / "linked.md").symlink_to(target)
    return "linked.md"


def _symlinked_folder(w: World) -> str:
    real = w.root.parent / "real-folder"
    real.mkdir()
    (real / "post.md").write_text(SECRET_LINE_TEXT + "\n", encoding="utf-8")
    (w.root / "folder").symlink_to(real)
    return "folder/post.md"


def _file(relative: str, data: bytes):
    def setup(w: World) -> str:
        w.write(relative, data)
        return relative
    return setup


UNCHECKABLE = [
    ("missing", lambda w: "gone.md", "missing_file", "gone.md"),
    ("not utf-8", _file("bad.md", b"ok\n\xff\xfe broken\n"), "not_utf8", "bad.md"),
    ("md over 1MB", _file("big.md", b"x" * (1024 * 1024 + 1)), "file_too_large", "big.md"),
    ("unsupported type", _file("notes.txt", b"fake notes\n"), "unsupported_type", "notes.txt"),
    ("folder", lambda w: (w.root / "dir.md").mkdir() or "dir.md", "not_a_regular_file", "dir.md"),
    ("FIFO", lambda w: os.mkfifo(w.root / "pipe.md") or "pipe.md", "not_a_regular_file", "pipe.md"),
    ("absolute outside the root", _outside, "outside_root", ""),
    ("parent traversal", _dotdot, "outside_root", ""),
    ("symlinked file", _symlink, "symlink_refused", ""),
    ("symlinked folder", _symlinked_folder, "symlink_refused", ""),
]


@pytest.mark.parametrize("setup,code,file", [case[1:] for case in UNCHECKABLE], ids=[case[0] for case in UNCHECKABLE])
def test_a_file_that_cannot_be_checked_blocks(world: World, setup, code: str, file: str) -> None:
    _terms(world)
    argument = setup(world)
    rc, payload, output = world.run("scan", "--root", str(world.root), argument)
    assert (rc, payload["status"], payload["errors"]) == (1, "block", [{"code": code, "file": file}]), output
    assert payload["blocks"] == []
    _no_leak(output)


def test_binary_images_are_skipped_and_listed(world: World) -> None:
    _terms(world)
    world.write(PNG, b"\x89PNG\r\n\x1a\n" + TERM_A.encode("utf-8"))
    rc, payload, output = world.run("scan", "--root", str(world.root), PNG)
    assert (rc, payload["status"], payload["skipped"], payload["blocks"]) == (0, "pass", [PNG], []), output


# ------------------------------------------------------------------ scan: heuristics warn

HEURISTIC_LINES = [
    ("private_ip", "서버는 10.1.2.3 과 192.168.0.10, 172.20.1.1 에 있다"),
    ("internal_host", "빌드는 build.corp 와 api.internal 을 거친다"),
    ("internal_host", "printer.local 과 nas.lan"),
    ("email", "문의는 someone@example.com"),
    ("issue_key", "작업 ABC-123 에서 고쳤다"),
    ("local_path", "파일은 /Users/someone/notes.txt 에 있다"),
    ("local_path", "설정은 C:\\Users\\someone 아래"),
    ("local_path", "메모는 ~/notes 에"),
    ("number_unit", "전환율이 30% 올랐다"),
    ("number_unit", "비용 500원, 사용자 3만, 팀 12명"),
    ("number_unit", "응답 250ms, 처리량 1200 TPS 와 900 QPS"),
    ("number_unit", "오류 5건, 속도 2배"),
]
CLEAN_LINES = [
    "공개 DNS 8.8.8.8 과 https://example.com/a 그리고 /posts/other/",
    "버전 v1.2 와 2026-01-02 날짜",
]


def test_heuristics_warn_with_category_and_line(world: World) -> None:
    _terms(world)
    body = "\n".join(text for _, text in HEURISTIC_LINES) + "\n" + "\n".join(CLEAN_LINES) + "\n"
    world.write(POST, body)
    rc, payload, output = world.run("scan", "--root", str(world.root), POST)
    assert (rc, payload["status"], payload["blocks"]) == (0, "pass", []), output
    expected = [{"category": category, "file": POST, "line": number}
                for number, (category, _) in enumerate(HEURISTIC_LINES, 1)]
    assert payload["warnings"] == expected
    for _, text in HEURISTIC_LINES:
        assert text not in output


def test_one_line_can_warn_in_several_categories(world: World) -> None:
    _terms(world)
    world.write(POST, "ops@build.corp 에서 10.0.0.1 로 30% 줄였다\n")
    rc, payload, output = world.run("scan", "--root", str(world.root), POST)
    assert rc == 0, output
    assert [w["category"] for w in payload["warnings"]] == ["private_ip", "internal_host", "email", "number_unit"]


# ------------------------------------------------------------------ scan: --slug equals the file list


def _public_copy(w: World) -> None:
    _terms(w)
    w.write(POST, f"---\ntitle: \"x\"\n---\n\n{SECRET_LINE_TEXT}\n서버 10.1.2.3\n")
    w.write(SVG, f"<svg>\n<text>{TERM_B}</text>\n<text>30%</text>\n</svg>\n")
    w.write(PNG, b"\x89PNG\r\n\x1a\nfake")
    w.write("src/assets/posts/first-post/a-chart.svg", "<svg><text>clean</text></svg>\n")


def _result(payload: dict) -> dict:
    return {key: payload[key] for key in ("status", "list", "blocks", "warnings", "skipped", "errors")}


def test_slug_input_gives_the_same_result_as_the_file_list(world: World) -> None:
    _public_copy(world)
    rc_slug, by_slug, output = world.run("scan", "--root", str(world.root), "--slug", "first-post")
    assert rc_slug == 1, output
    _no_leak(output)
    files = [POST, "src/assets/posts/first-post/a-chart.svg", SVG, PNG]
    rc_files, by_files, output = world.run("scan", "--root", str(world.root), *files)
    assert rc_files == 1, output
    assert _result(by_slug) == _result(by_files)
    assert by_slug["blocks"] == [
        {"category": "confidential_term", "file": POST, "line": 5},
        {"category": "confidential_term", "file": SVG, "line": 2},
    ]
    assert by_slug["skipped"] == [PNG]


def test_slug_input_without_an_asset_folder_scans_the_post(world: World) -> None:
    _terms(world)
    world.write(POST, "clean\n")
    rc, payload, output = world.run("scan", "--root", str(world.root), "--slug", "first-post")
    assert (rc, payload["status"], payload["errors"]) == (0, "pass", []), output


@pytest.mark.parametrize("setup,code", [
    (lambda w: None, "missing_file"),
    (lambda w: w.write("src/assets/posts/first-post/notes.txt", "x\n") and w.write(POST, "clean\n"),
     "unsupported_type"),
    (lambda w: (w.root / "src" / "assets").mkdir(parents=True) or
     (w.root / "src" / "assets" / "posts").symlink_to(w.root.parent) or w.write(POST, "clean\n"), "symlink_refused"),
], ids=["missing post", "unsupported asset", "symlinked asset parent"])
def test_slug_input_that_cannot_be_checked_blocks(world: World, setup, code: str) -> None:
    _terms(world)
    setup(world)
    rc, payload, output = world.run("scan", "--root", str(world.root), "--slug", "first-post")
    assert (rc, payload["status"]) == (1, "block"), output
    assert [error["code"] for error in payload["errors"]] == [code]


@pytest.mark.parametrize("arguments", [
    ["--slug", "Bad_Slug"], ["--slug", "first-post", POST], [],
], ids=["invalid slug", "slug and files", "nothing to scan"])
def test_scan_arguments_are_checked(world: World, arguments: list[str]) -> None:
    _terms(world)
    world.write(POST, "clean\n")
    rc, payload, output = world.run("scan", "--root", str(world.root), *arguments)
    assert (rc, payload["status"]) == (1, "block"), output
    assert [error["code"] for error in payload["errors"]] == ["arguments_invalid"]


# ------------------------------------------------------------------ scan: unexpected failures

RAISE_BOOTSTRAP = '''\\
"""Test bootstrap: make line normalization raise with a planted secret in the message."""
import runpy
import sys
import unicodedata

script, secret = sys.argv[1], sys.argv[2]
real = unicodedata.normalize


def normalize(form, text):
    if "line" in text or "줄" in text:
        raise RuntimeError(secret + " " + text)
    return real(form, text)


unicodedata.normalize = normalize
sys.argv = [script, *sys.argv[3:]]
runpy.run_path(script, run_name="__main__")
'''


def test_an_unexpected_failure_blocks_without_printing_anything_from_it(world: World, tmp_path: Path) -> None:
    _terms(world)
    world.write(POST, f"clean line\n{SECRET_LINE_TEXT}\n")
    bootstrap = tmp_path / "raise.py"
    bootstrap.write_text(RAISE_BOOTSTRAP)
    env = {**os.environ, "HOME": str(world.home)}
    result = subprocess.run(
        [sys.executable, str(bootstrap), str(SCRIPT), TERM_B, "scan", "--root", str(world.root), POST],
        capture_output=True, text=True, env=env, cwd=world.root, timeout=60,
    )
    payload = json.loads(result.stdout)
    assert (result.returncode, payload["status"], payload["errors"]) == (1, "block", [{"code": "check_failed"}])
    _no_leak(result.stdout + result.stderr)
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("line", ["x" * 900_000, "1" * 900_000, "a." * 450_000, "a@" * 450_000],
                         ids=["letters", "digits", "dotted", "at signs"])
def test_a_long_line_is_scanned_in_linear_time(world: World, line: str) -> None:
    import time
    _terms(world)
    world.write(POST, line + "\n")
    started = time.monotonic()
    rc, payload, output = world.run("scan", "--root", str(world.root), POST)
    assert rc in (0, 1) and payload["errors"] == [], output
    assert time.monotonic() - started < 10
