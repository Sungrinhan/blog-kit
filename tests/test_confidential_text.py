"""The shared functions of scripts/confidential_check.py that other modules import.

The engine and the context loader load the script from the install path (like
the engine's _load) and call scan_text on bytes they already read, and
list_digest / terms_digest to bind a record to the term list. Each test points
HOME at a temp folder holding a fake term list. The return values are the
interface: categories and line numbers only, never a term or a line.

Stdlib + pytest only. No network.
"""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "confidential_check.py"
# Fake terms only. Never real ones.
TERM_A = "가짜사내코드명"
TERM_B = "Fooproject"
SECRET_LINE_TEXT = "이 줄에는 가짜사내코드명이 들어 있다"


def _load():
    spec = importlib.util.spec_from_file_location("blogkit_confidential_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    (home / ".config" / "blog-kit").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


def _terms(home: Path, text: str) -> None:
    (home / ".config" / "blog-kit" / "confidential.txt").write_bytes(text.encode("utf-8"))


def _no_leak(value) -> None:
    dumped = json.dumps(value, ensure_ascii=False)
    for secret in (TERM_A, TERM_B, TERM_B.casefold(), SECRET_LINE_TEXT):
        assert secret not in dumped


# ------------------------------------------------------------------ scan_text


def test_scan_text_reports_categories_and_lines_only(home: Path) -> None:
    _terms(home, f"{TERM_A}\n{TERM_B}\n")
    checker = _load()
    data = f"clean line\n{SECRET_LINE_TEXT}\nhost db.internal\n{TERM_B.upper()} at 10.0.0.1\n".encode()
    result = checker.scan_text(data, checker.load_terms())
    assert result == {
        "blocks": [{"category": "confidential_term", "line": 2}, {"category": "confidential_term", "line": 4}],
        "warnings": [{"category": "internal_host", "line": 3}, {"category": "private_ip", "line": 4}],
    }
    _no_leak(result)


def test_scan_text_takes_any_text_the_caller_names(home: Path) -> None:
    _terms(home, f"{TERM_B}\n")
    checker = _load()
    for data in (b'{"name": "fooproject"}\n', b"name: FOOPROJECT\n", f"{TERM_B}\n"):
        result = checker.scan_text(data, checker.load_terms())
        assert result["blocks"] == [{"category": "confidential_term", "line": 1}]
        _no_leak(result)


def test_scan_text_matches_nfd_input_against_an_nfc_term(home: Path) -> None:
    import unicodedata
    _terms(home, f"{TERM_A}\n")
    checker = _load()
    data = unicodedata.normalize("NFD", SECRET_LINE_TEXT).encode("utf-8")
    assert checker.scan_text(data, checker.load_terms())["blocks"] == [{"category": "confidential_term", "line": 1}]


def test_scan_text_refuses_bytes_that_are_not_utf8(home: Path) -> None:
    _terms(home, f"{TERM_A}\n")
    checker = _load()
    with pytest.raises(checker.Blocked) as caught:
        checker.scan_text(b"\xff\xfe" + TERM_A.encode("utf-8"), checker.load_terms())
    assert caught.value.code == "not_utf8"
    assert TERM_A not in str(caught.value)


@pytest.mark.parametrize("unit", ["x", "1", "a.", "a@", "A-", "~/"],
                         ids=["letters", "digits", "dotted", "at signs", "hyphens", "home paths"])
def test_scan_text_takes_linear_time_on_a_long_line_at_the_size_cap(home: Path, unit: str) -> None:
    _terms(home, f"{TERM_A}\n{TERM_B}\n")
    checker = _load()
    cap = checker.TEXT_CAPS[".md"]
    data = (unit * (cap // len(unit)))[:cap].encode("utf-8")
    terms = checker.load_terms()
    started = time.monotonic()
    checker.scan_text(data, terms)
    assert time.monotonic() - started < 10


# ------------------------------------------------------------------ list digest


def test_the_list_digest_ignores_comments_blank_lines_order_case_and_duplicates(home: Path) -> None:
    _terms(home, f"{TERM_A}\n{TERM_B}\n")
    checker = _load()
    first = checker.list_digest()
    _terms(home, f"# comment\n\n  {TERM_B.upper()}  \n{TERM_A}\n{TERM_B}\n\n")
    second = checker.list_digest()
    assert first == second
    assert len(first) == 64 and all(ch in "0123456789abcdef" for ch in first)
    _no_leak(first)


def test_the_list_digest_is_the_sha256_of_the_sorted_normalized_terms(home: Path) -> None:
    import hashlib
    _terms(home, f"{TERM_B}\n{TERM_A}\n")
    checker = _load()
    expected = hashlib.sha256("\n".join(sorted({TERM_A, TERM_B.casefold()})).encode("utf-8")).hexdigest()
    assert checker.list_digest() == expected
    assert checker.terms_digest(checker.load_terms()) == expected


@pytest.mark.parametrize("change", [f"{TERM_A}\nfooproject2\n", f"{TERM_A}\n", f"{TERM_A}\n{TERM_B}\nextra\n"],
                         ids=["changed", "removed", "added"])
def test_the_list_digest_changes_when_a_term_changes(home: Path, change: str) -> None:
    _terms(home, f"{TERM_A}\n{TERM_B}\n")
    checker = _load()
    before = checker.list_digest()
    _terms(home, change)
    assert checker.list_digest() != before


@pytest.mark.parametrize("text", [None, "", "# only a comment\n\n"], ids=["missing", "empty", "comments only"])
def test_a_missing_or_empty_list_blocks_instead_of_a_digest(home: Path, text: str | None) -> None:
    if text is not None:
        _terms(home, text)
    checker = _load()
    with pytest.raises(checker.Blocked) as caught:
        checker.list_digest()
    assert caught.value.code == ("list_missing" if text is None else "list_empty")
    with pytest.raises(checker.Blocked):
        checker.terms_digest([])
