"""WR-06: the body sanitizer keeps chart presentation and aria attributes and
drops dangerous presentation values.

The sanitizer used to strip `opacity`, `font-weight`, `stroke-linecap`,
`stroke-linejoin` and `aria-labelledby`, so grid lines turned solid and a chart
lost its accessible name (PAIN-POINTS 7). Those attributes, plus
`aria-describedby` and `aria-hidden`, are now kept. Every SVG presentation
value, the upstream `fill` and `stroke` included, is decoded, lowercased and
stripped of whitespace, then dropped if it holds `javascript:`, `url(`,
`expression(`, `<` or a backslash. `<style>` elements and `style` attributes
are still removed.

Tests render a real markdown file through the CLI and check the HTML body.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "blog_render.py"
FRONTMATTER = (
    "---\ntitle: Svg Attrs Post\ndescription: A short description of the post.\n"
    "date: 2026-11-25\nauthor: Alex Rivera\ncanonical: https://example.com/blog/svg-attrs/\n"
    "slug: svg-attrs\n---\n"
)
START = "<!--bs-body:start-->"
END = "<!--bs-body:end-->"


def _render_body(tmp_path: Path, svg: str) -> str:
    source = tmp_path / "svg-attrs.md"
    source.write_bytes((FRONTMATTER + f"<figure>\n{svg}\n</figure>\n\nAfter.\n").encode("utf-8"))
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--md", str(source), "--out-dir", str(tmp_path), "--pdf-engine", "none"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    page = (tmp_path / "svg-attrs.html").read_text(encoding="utf-8")
    return page[page.index(START) + len(START):page.rindex(END)]


def test_chart_presentation_and_aria_attributes_survive(tmp_path: Path):
    svg = (
        '<svg viewBox="0 0 200 100" role="img" aria-labelledby="t d" aria-describedby="d">'
        '<title id="t">Latency</title><desc id="d">p99 by service</desc>'
        '<line x1="0" y1="20" x2="200" y2="20" stroke="currentColor" stroke-width="1" opacity="0.12"></line>'
        '<path d="M0 0L10 10" fill="#38bdf8" opacity="0.2"></path>'
        '<polygon points="0,0 5,5 0,5" fill="none" stroke="#38bdf8" stroke-width="2" opacity="0.5"></polygon>'
        '<polyline points="0,0 5,5" fill="none" stroke="#f97316" stroke-width="3" '
        'stroke-linecap="round" stroke-linejoin="round"></polyline>'
        '<text x="10" y="40" fill="currentColor" font-size="12" font-weight="700" opacity="0.8">142 ms</text>'
        '<g aria-hidden="true"><rect x="0" y="0" width="5" height="5" fill="#000"></rect></g>'
        "</svg>"
    )
    body = _render_body(tmp_path, svg)
    for kept in (
        'aria-labelledby="t d"',
        'aria-describedby="d"',
        'opacity="0.12"',
        'opacity="0.2"',
        'opacity="0.5"',
        'stroke-linecap="round"',
        'stroke-linejoin="round"',
        'font-weight="700"',
        'opacity="0.8"',
        'aria-hidden="true"',
        ">142 ms</text>",
    ):
        assert kept in body, kept


def test_style_element_and_style_attribute_are_still_removed(tmp_path: Path):
    svg = (
        '<svg viewBox="0 0 10 10" style="--chart-muted: red">'
        "<style>svg { display: none }</style>"
        '<text x="1" y="1" style="fill: red">Label</text></svg>'
    )
    body = _render_body(tmp_path, svg)
    assert "<style" not in body and "style=" not in body
    assert "display: none" not in body
    assert ">Label</text>" in body


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "java\tscript:alert(1)",
        " java script : alert(1)",
        "url(#evil)",
        "URL (https://example.com/x.svg#a)",
        "&#117;rl(#evil)",
        "&amp;#117;rl(#evil)",
        "expression(alert(1))",
        "a<b",
        "\\75 rl(#evil)",
    ],
)
@pytest.mark.parametrize("attr", ["fill", "stroke", "opacity", "font-weight", "stroke-linecap"])
def test_dangerous_presentation_values_are_dropped(tmp_path: Path, attr: str, value: str):
    escaped = value.replace("<", "&lt;").replace('"', "&quot;")
    tag = "polyline" if attr == "stroke-linecap" else "text"
    close = "</polyline>" if tag == "polyline" else "T</text>"
    svg = f'<svg viewBox="0 0 10 10"><{tag} x="1" y="1" points="0,0 1,1" {attr}="{escaped}">{close}</svg>'
    body = _render_body(tmp_path, svg)
    assert f"{attr}=" not in body, body
    assert f"<{tag}" in body


def test_safe_values_with_css_variables_are_kept(tmp_path: Path):
    svg = (
        '<svg viewBox="0 0 10 10">'
        '<text x="1" y="1" fill="var(--chart-muted, currentColor)">Muted</text></svg>'
    )
    body = _render_body(tmp_path, svg)
    assert 'fill="var(--chart-muted, currentColor)"' in body
