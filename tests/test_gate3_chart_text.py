"""WR-07: Gate 3 does not count a chart's <title>/<desc> as hidden text.

An SVG's <title> and <desc> are its accessible name and description; a
browser never draws them, so Gate 3's hidden-text check used to count them
and block a post whose chart had a long description (PAIN-POINTS 7). They are
now skipped. Hidden text outside an SVG is still caught.

Uses the golden draft with a generated chart appended to the markdown and
rendered through the real renderer, then runs Gate 3 in the installed
Chromium (skipped without it; the ubuntu CI test job installs it).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from gate_support import PREFLIGHT, ROOT, SLUG, golden_draft, offline_probes, require_browser  # noqa: F401

CHART = ROOT / "skills" / "blog-chart" / "scripts" / "generate_chart_svg.py"
LONG_DESC = (
    "Horizontal bar chart of p99 latency by service: checkout 142 ms, search 96 ms, "
    "catalog 61 ms, profile 44 ms. Checkout is the slowest at more than three times profile."
)


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


def test_chart_title_and_desc_are_not_hidden_text(tmp_path):
    require_browser()
    figure = _chart(tmp_path)
    assert LONG_DESC in figure and len(LONG_DESC) > 100
    draft = golden_draft(tmp_path, markdown=figure)
    result = PREFLIGHT.gate_3_visual_verification(draft, SLUG)
    assert result["passed"], result["violations"]
    assert result["per_viewport"]["desktop"]["result"]["hiddenText"]["chars"] == 0
    assert not any("not rendered" in w for w in result["warnings"])


def test_hidden_text_outside_the_chart_is_still_blocked(tmp_path):
    require_browser()
    draft = golden_draft(tmp_path, markdown=_chart(tmp_path))
    html = draft / f"{SLUG}.html"
    stuffed = "cheap seo services best seo agency buy backlinks " * 3
    text = html.read_text(encoding="utf-8")
    # Inserted after rendering on purpose: the sanitizer would drop style=.
    text = text.replace("<!--bs-body:end-->", f'<p id="kw" style="display:none">{stuffed}</p><!--bs-body:end-->', 1)
    html.write_text(text, encoding="utf-8")
    result = PREFLIGHT.gate_3_visual_verification(draft, SLUG)
    assert not result["passed"]
    assert any("characters of article text" in v and "kw" in v for v in result["violations"]), result["violations"]
