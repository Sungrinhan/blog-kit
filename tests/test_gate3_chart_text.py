"""WR-07: Gate 3 does not count a chart's <title>/<desc> as hidden text.

An SVG's <title> and <desc> are its accessible name and description; a
browser never draws them, so Gate 3's hidden-text check used to count them
and block a post whose chart had a long description (PAIN-POINTS 7). They are
now skipped. Hidden text outside an SVG is still caught.

Uses the golden draft (tests/fixtures/golden_draft) with a generated chart
appended to the markdown and rendered through the real renderer, then runs
Gate 3 in the installed Chromium. Skips when Patchright or its Chromium is
missing (the ubuntu CI test job installs both).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "golden_draft"
SLUG = "core-web-vitals-thresholds"
CHART = ROOT / "skills" / "blog-chart" / "scripts" / "generate_chart_svg.py"
MINIMAL_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)
LONG_DESC = (
    "Horizontal bar chart of p99 latency by service: checkout 142 ms, search 96 ms, "
    "catalog 61 ms, profile 44 ms. Checkout is the slowest at more than three times profile."
)


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def preflight():
    return _load("gate3_chart_preflight", "blog_preflight.py")


@pytest.fixture(scope="module")
def renderer():
    return _load("gate3_chart_renderer", "blog_render.py")


@pytest.fixture(autouse=True)
def offline_probes(preflight, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_BLOG_REVIEW_STATE_DIR", str(tmp_path / "review-state"))
    monkeypatch.setattr(preflight, "_safe_http_url", lambda _url: (True, None))
    monkeypatch.setattr(preflight, "_http_head", lambda _url: 200)


def _require_browser():
    browser_api = pytest.importorskip("patchright.sync_api")
    with browser_api.sync_playwright() as runtime:
        if not Path(runtime.chromium.executable_path).is_file():
            pytest.skip("requires matching installed Chromium; no browser download in unit tests")


def _chart(tmp_path: Path) -> str:
    config = {
        "type": "horizontal_bar",
        "title": "p99 latency by service",
        "subtitle": "Milliseconds, lower is better",
        "description": LONG_DESC,
        "data": [
            {"label": "checkout", "value": 142},
            {"label": "search", "value": 96},
            {"label": "catalog", "value": 61},
            {"label": "profile", "value": 44},
        ],
        "source": "Internal load test",
    }
    source = tmp_path / "chart.json"
    source.write_text(json.dumps(config), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(CHART), "--input", str(source), "--root", str(tmp_path)],
        capture_output=True, check=True,
    )
    return result.stdout.decode("utf-8")


def _draft_with(tmp_path: Path, renderer, extra_markdown: str) -> Path:
    draft = tmp_path / "draft"
    shutil.copytree(FIXTURE, draft)
    source = draft / f"{SLUG}.md"
    source.write_bytes(source.read_bytes() + ("\n" + extra_markdown + "\n").encode("utf-8"))
    renderer._render_html(source, draft, "hero.webp")
    (draft / f"{SLUG}.pdf").write_bytes(MINIMAL_PDF)
    return draft


def test_chart_title_and_desc_are_not_hidden_text(preflight, renderer, tmp_path):
    _require_browser()
    figure = _chart(tmp_path)
    assert LONG_DESC in figure and len(LONG_DESC) > 100
    draft = _draft_with(tmp_path, renderer, figure)
    result = preflight.gate_3_visual_verification(draft, SLUG)
    assert result["passed"], result["violations"]
    assert result["per_viewport"]["desktop"]["result"]["hiddenText"]["chars"] == 0
    assert not any("not rendered" in w for w in result["warnings"])


def test_hidden_text_outside_the_chart_is_still_blocked(preflight, renderer, tmp_path):
    _require_browser()
    figure = _chart(tmp_path)
    draft = _draft_with(tmp_path, renderer, figure)
    html = draft / f"{SLUG}.html"
    stuffed = "cheap seo services best seo agency buy backlinks " * 3
    text = html.read_text(encoding="utf-8")
    # Inserted after rendering on purpose: the sanitizer would drop style=.
    text = text.replace("<!--bs-body:end-->", f'<p id="kw" style="display:none">{stuffed}</p><!--bs-body:end-->', 1)
    html.write_text(text, encoding="utf-8")
    result = preflight.gate_3_visual_verification(draft, SLUG)
    assert not result["passed"]
    assert any("characters of article text" in v and "kw" in v for v in result["violations"]), result["violations"]
