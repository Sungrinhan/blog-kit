"""WR-07: every chart type renders as generated, with dark-mode muted text.

The chart generator used to color muted text (subtitle, source line, donut
total) through a CSS variable set by an inline `style` and switched by an SVG
`<style>`; the renderer strips both, so muted text lost its dark-mode color
(PAIN-POINTS 7). Muted text now carries the `chart-muted` class plus a light
`fill`, and the renderer's page CSS colors the class in light and dark mode.

Each of the seven supported types is generated through the chart CLI, placed
in a post and rendered through the renderer CLI: no element nests inside a
self-closing one, the presentation attributes the chart emits survive, and the
muted class is present.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest
from render_support import render_body, render_page

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "skills" / "blog-chart" / "scripts" / "generate_chart_svg.py"
VOID = {"br", "hr", "img"}
LEAF_SHAPES = {"line", "rect", "circle", "path", "polyline", "polygon"}
DATA = [{"label": "Alpha", "value": 3}, {"label": "Beta", "value": 5}, {"label": "Gamma", "value": 2}]
TYPES = ["horizontal_bar", "grouped_bar", "donut", "line", "lollipop", "area", "radar"]


def _config(chart_type: str) -> dict:
    config = {
        "type": chart_type,
        "title": f"{chart_type} chart",
        "subtitle": "Muted subtitle",
        "data": DATA,
        "source": "Example source",
        "source_url": "https://example.com/source",
    }
    if chart_type == "grouped_bar":
        config.pop("data")
        config["series"] = [{"name": "2025", "data": DATA}, {"name": "2026", "data": DATA}]
    return config


def _generate(tmp_path: Path, chart_type: str) -> str:
    source = tmp_path / f"{chart_type}.json"
    source.write_text(json.dumps(_config(chart_type)), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(CHART), "--input", str(source), "--root", str(tmp_path)],
        capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return result.stdout.decode("utf-8")


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.parents: set[tuple[str, str | None]] = set()

    def handle_starttag(self, tag, attrs):
        self.parents.add((tag, self.stack[-1] if self.stack else None))
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack and self.stack[-1] == tag, f"unbalanced </{tag}> in {self.stack}"
        self.stack.pop()


@pytest.mark.parametrize("chart_type", TYPES)
def test_generator_marks_muted_text_with_a_class_and_no_css_variable(tmp_path: Path, chart_type: str):
    figure = _generate(tmp_path, chart_type)
    assert "--chart-muted" not in figure and "var(" not in figure
    assert 'class="chart-muted" fill="#4b5563"' in figure
    assert ".chart-muted { fill: #d1d5db; }" in figure


@pytest.mark.parametrize("chart_type", TYPES)
def test_generated_chart_renders_unnested_with_attributes_and_muted_class(tmp_path: Path, chart_type: str):
    figure = _generate(tmp_path, chart_type)
    body = render_body(tmp_path, f"Intro paragraph.\n\n{figure}\n\nAfter the chart.\n", slug="chart")
    tree = _Tree()
    tree.feed(body)
    tree.close()
    assert tree.stack == [], tree.stack
    assert not any(parent in LEAF_SHAPES for _, parent in tree.parents), tree.parents
    assert ("p", None) in tree.parents
    assert 'class="chart-muted" fill="#4b5563"' in body
    assert "Source: Example source" in body
    assert 'aria-labelledby="' in body
    assert "<style" not in body and "style=" not in body
    # Every attribute the generator emits, except the stripped style=, survives.
    generated = Counter(n.lower() for n in re.findall(r'\s([A-Za-z][\w:-]*)="', figure))
    rendered = Counter(n.lower() for n in re.findall(r'\s([A-Za-z][\w:-]*)="', body))
    del generated["style"]
    missing = {name: count for name, count in generated.items() if rendered[name] < count}
    assert not missing, missing


def test_page_css_colors_the_muted_class_in_light_and_dark(tmp_path: Path):
    page = render_page(tmp_path, "Body paragraph.\n", slug="css")
    css = page[page.index("<style>"):page.index("</style>")]
    assert ".chart-muted{fill:#4b5563}" in css
    dark = css[css.index("@media (prefers-color-scheme:dark)"):]
    assert ".chart-muted{fill:#d1d5db}" in dark
