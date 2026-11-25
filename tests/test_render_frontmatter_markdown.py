"""WR-08: inline frontmatter arrays, and table text with braces.

`tags: ["a", "b"]` (the form the blog-write skill documents) used to reach
JSON-LD keywords as the literal string `["a", "b"]` and the kicker as
`["A", "B"]` (PAIN-POINTS 8). The list keys `tags` and `author_same_as` now
read an inline array: a JSON string array first, else simple comma-separated
items without quotes; an empty or non-string item, or neither form, fails the
render. Other keys keep a bracketed value as a string; block lists are
unchanged.

python-markdown's `attr_list` (part of `extra`) ate a trailing `{리뷰}` in a
table cell as an attribute list (PAIN-POINTS 13). The renderer now loads
`extra`'s other members without it; tables, fenced code, footnotes,
abbreviations, definition lists and markdown inside HTML still render.

Tests render through the CLI and read the page or body HTML.
"""

from __future__ import annotations

import json
import re

import pytest
from render_support import render, render_body, render_page

BODY = "Body paragraph.\n"


def _jsonld(page: str) -> dict:
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        data = json.loads(block)
        if data.get("@type") == "BlogPosting":
            return data
    raise AssertionError("no BlogPosting JSON-LD")


def _kicker(page: str) -> str:
    return re.search(r'<p class="kicker">(.*?)</p>', page).group(1)


@pytest.mark.parametrize(
    "line",
    ['tags: ["seo", "core web vitals"]', "tags: [seo, core web vitals]"],
)
def test_inline_tags_become_keywords_and_kicker(tmp_path, line):
    page = render_page(tmp_path, BODY, slug="tags", extra_frontmatter=line + "\n")
    assert _jsonld(page)["keywords"] == "seo, core web vitals"
    assert _kicker(page) == "Seo"


def test_inline_tags_match_block_list(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    inline = render_page(tmp_path / "a", BODY, slug="tags", extra_frontmatter='tags: ["seo", "speed"]\n')
    block = render_page(tmp_path / "b", BODY, slug="tags", extra_frontmatter="tags:\n  - seo\n  - speed\n")
    assert _jsonld(inline)["keywords"] == _jsonld(block)["keywords"] == "seo, speed"
    assert _kicker(inline) == _kicker(block) == "Seo"


@pytest.mark.parametrize(
    "line",
    ['tags: ["seo", ""]', "tags: [seo, , speed]", 'tags: ["seo", 3]', 'tags: ["seo", "spe"ed"]', "tags: [a, b'c]",
     "tags: ['seo', 'speed']"],
)
def test_bad_inline_tags_fail_the_render(tmp_path, line):
    (tmp_path / "out").mkdir()
    result = render(tmp_path / "out", BODY, slug="tags", extra_frontmatter=line + "\n")
    assert result.returncode != 0
    assert "frontmatter tags" in result.stderr
    assert not (tmp_path / "out" / "tags.html").exists()


def test_other_keys_keep_bracketed_value_as_string(tmp_path):
    page = render_page(tmp_path, BODY, slug="other", extra_frontmatter="kicker: [Draft] notes\nsite_name: [Lab]\n")
    assert _kicker(page) == "[Draft] notes"


def test_string_tags_unchanged(tmp_path):
    page = render_page(tmp_path, BODY, slug="plain", extra_frontmatter="tags: seo\n")
    assert _jsonld(page)["keywords"] == "seo"


def test_trailing_braces_in_table_cell_stay(tmp_path):
    table = "| 항목 | 상태 |\n|---|---|\n| 초안 | 검토 중 {리뷰} |\n| 제목 {draft} | ok |\n"
    body = render_body(tmp_path, table, slug="braces")
    assert "검토 중 {리뷰}" in body
    assert "제목 {draft}" in body


def test_heading_braces_stay_text(tmp_path):
    body = render_body(tmp_path, "## Plan {#custom .cls}\n\nText.\n", slug="heading")
    assert "Plan {#custom .cls}" in body
    assert 'id="custom"' not in body


def test_other_extra_syntax_still_renders(tmp_path):
    source = (
        "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
        "```python\nprint('hi')\n```\n\n"
        "A claim.[^1]\n\n[^1]: The footnote.\n\n"
        "The HTML spec.\n\n*[HTML]: Hyper Text Markup Language\n\n"
        "Term\n:   Definition text.\n\n"
        '<div markdown="1">\n**bold inside**\n</div>\n'
    )
    body = render_body(tmp_path, source, slug="extra")
    assert "<table>" in body and "<td>1</td>" in body
    assert '<code class="language-python">' in body
    assert 'class="footnote' in body and "The footnote." in body
    assert "<abbr>HTML</abbr>" in body  # the sanitizer keeps <abbr> but not its title (upstream)
    assert "<dl>" in body and "<dd>Definition text.</dd>" in body
    assert "<strong>bold inside</strong>" in body
