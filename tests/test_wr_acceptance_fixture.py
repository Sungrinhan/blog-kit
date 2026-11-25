"""WR-11: one short post that carries every WR renderer fix through the gates.

tests/fixtures/wr_acceptance/read-cache-p99.md holds a chart straight from the
chart generator (self-closing tags as generated) with value labels added by
hand, a table whose cells end in `{...}`, inline `tags` and
`author_same_as` arrays, and the new keys `site_url` and `author_url`. It
renders through the real renderer; the HTML is checked for the fixes, Gate 5
runs without a browser, and Gate 3 runs in Chromium (the ubuntu CI test job;
skipped without it): it must pass with no hidden text and a consistent
JSON-LD.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from gate_support import MINIMAL_PDF, PREFLIGHT, RENDERER, ROOT, offline_probes, require_browser  # noqa: F401
from render_support import END, START, element_tree

FIXTURE = ROOT / "tests" / "fixtures" / "wr_acceptance"
SLUG = "read-cache-p99"
HAND_LABELS = ("420", "180", "310", "150", "260", "240")


@pytest.fixture
def draft(tmp_path: Path) -> Path:
    target = tmp_path / "draft"
    shutil.copytree(FIXTURE, target)
    RENDERER._render_html(target / f"{SLUG}.md", target, "hero.webp")
    (target / f"{SLUG}.pdf").write_bytes(MINIMAL_PDF)
    return target


def _page(draft: Path) -> str:
    return (draft / f"{SLUG}.html").read_text(encoding="utf-8")


def _blocks(page: str) -> list[dict]:
    return [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)]


def test_fixture_source_keeps_the_generator_output_as_is():
    source = (FIXTURE / f"{SLUG}.md").read_text(encoding="utf-8")
    assert source.count(" />") >= 10, "chart self-closing tags must stay as generated"
    assert 'class="chart-muted"' in source and "{hit}" in source
    assert 'tags: ["' in source and 'author_same_as: ["' in source


def test_rendered_fixture_carries_every_fix(draft):
    page = _page(draft)
    body = page[page.index(START) + len(START):page.rindex(END)]
    tree = element_tree(body)
    assert not any(parent in {"line", "rect", "circle", "path"} for _, parent in tree.parents)
    for label in HAND_LABELS:
        assert re.search(rf'font-weight="700" fill="currentColor">{label}</text>', body), label
    assert body.count('opacity="0.08"') == 5
    assert body.count('class="chart-muted" fill="#4b5563"') == 2
    assert 'aria-labelledby="p99-latency-before-and-after-caching-title' in body
    for cell in ("{hit}", "{warm}", "{external}"):
        assert cell in body, cell
    post, crumbs = _blocks(page)
    assert post["keywords"] == "performance, caching, latency"
    assert post["author"]["url"] == "https://notes.example.org/dev-notes/about/"
    assert len(post["author"]["sameAs"]) == 2
    assert post["publisher"] == {
        "@type": "Organization", "name": "Example Dev Notes", "url": "https://notes.example.org/dev-notes/",
    }
    assert crumbs["@type"] == "BreadcrumbList"
    assert 'Published at <a href="https://notes.example.org/dev-notes/"' in page
    assert 'fetchpriority="high"' in page
    assert re.search(r'<p class="kicker">Performance</p>', page)


def test_fixture_passes_gate_5(draft):
    result = PREFLIGHT.gate_5_asset_link_integrity(draft, SLUG)
    assert result["passed"], result["violations"]


def test_fixture_passes_gate_3(draft):
    require_browser()
    result = PREFLIGHT.gate_3_visual_verification(draft, SLUG)
    assert result["passed"], result["violations"]
    for viewport in result["per_viewport"].values():
        assert viewport["result"]["hiddenText"]["chars"] == 0
    assert not any("not rendered" in w for w in result["warnings"])
