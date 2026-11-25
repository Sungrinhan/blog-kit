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

import importlib.util

import pytest
from render_support import SCRIPT, render_body

SPEC = importlib.util.spec_from_file_location("render_svg_attrs", SCRIPT)
RENDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RENDER)
SVG_TAGS = ("svg", "path", "rect", "circle", "line", "polyline", "polygon", "text", "tspan")
NON_PRESENTATION = {
    "viewbox", "width", "height", "xmlns", "x", "y", "dx", "dy", "x1", "x2", "y1", "y2",
    "cx", "cy", "r", "rx", "ry", "d", "points",
}


def _render_body(tmp_path, svg: str) -> str:
    return render_body(tmp_path, f"<figure>\n{svg}\n</figure>\n\nAfter.\n", slug="svg-attrs")


def test_every_allowed_svg_presentation_attribute_is_value_checked():
    """A presentation attribute added to an SVG allowlist entry but not to
    SVG_PRESENTATION_ATTRS would skip the value check (fail open)."""
    for tag in SVG_TAGS:
        for name in RENDER.ALLOWED_ATTRS[tag] - NON_PRESENTATION:
            assert name in RENDER.SVG_PRESENTATION_ATTRS, (tag, name)


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


DANGEROUS_VALUES = [
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
]


@pytest.mark.parametrize(
    ("attr", "tag"),
    [
        ("fill", "svg"), ("stroke", "svg"), ("fill", "text"), ("fill", "tspan"),
        ("font-size", "text"), ("font-weight", "text"), ("text-anchor", "text"),
        ("opacity", "line"), ("stroke", "line"), ("stroke-width", "line"),
        ("stroke-linecap", "polyline"), ("stroke-linejoin", "polyline"),
    ],
)
def test_dangerous_presentation_values_are_dropped(tmp_path, attr: str, tag: str):
    """Every variant of every dangerous value goes in one render per attribute.

    One safe value rides along, so the attribute is proven allowed on that
    element and is removed by the value check, not by the allowlist."""
    variants = ["1"] + [v.replace("<", "&lt;").replace('"', "&quot;") for v in DANGEROUS_VALUES]
    if tag == "svg":
        svg = "\n".join(f'<svg viewBox="0 0 10 10" {attr}="{v}"></svg>' for v in variants)
    else:
        inner = "".join(f'<{tag} {attr}="{v}">T</{tag}>' for v in variants)
        svg = f'<svg viewBox="0 0 10 10">{inner}</svg>'
    body = _render_body(tmp_path, svg)
    assert body.count(f'{attr}="') == 1 and f'{attr}="1"' in body, body
    assert body.count(f"<{tag}") >= len(variants)


def test_safe_values_with_css_variables_are_kept(tmp_path: Path):
    svg = (
        '<svg viewBox="0 0 10 10">'
        '<text x="1" y="1" fill="var(--chart-muted, currentColor)">Muted</text></svg>'
    )
    body = _render_body(tmp_path, svg)
    assert 'fill="var(--chart-muted, currentColor)"' in body
