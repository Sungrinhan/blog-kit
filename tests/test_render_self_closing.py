"""WR-05: self-closing tags in the body stay closed elements after rendering.

The body sanitizer used to emit `<line .../>` as a bare `<line ...>`, so every
sibling after it nested inside the line and Gate 3 failed on the chart
(PAIN-POINTS 6). A self-closing tag now goes through start-tag then end-tag
handling: a non-void tag comes out as `<tag ...></tag>`, a void tag (`br`,
`hr`, `img`) without a closing tag, and `<table/>` gets the scroll wrapper like
any table.

Tests render a real markdown file through the CLI and check the HTML body.
"""

from __future__ import annotations

from render_support import VOID_TAGS as VOID
from render_support import element_tree as _tree
from render_support import render_body


def _render_body(tmp_path, body: str) -> str:
    return render_body(tmp_path, body, slug="self-closing")


def test_consecutive_svg_self_closing_siblings_do_not_nest(tmp_path: Path):
    svg = (
        '<svg viewBox="0 0 100 50" role="img" aria-label="Chart">'
        '<line x1="0" y1="10" x2="100" y2="10" stroke="#ccc"/>'
        '<line x1="0" y1="20" x2="100" y2="20" stroke="#ccc"/>'
        '<rect x="0" y="30" width="40" height="10" fill="#0f766e"/>'
        '<circle cx="80" cy="35" r="4" fill="#0f766e"/>'
        '<text x="50" y="48" fill="currentColor">42 ms</text>'
        "</svg>"
    )
    body = _render_body(tmp_path, f"<figure>\n{svg}\n</figure>\n\nAfter the chart.\n")
    assert '<line x1="0" y1="10" x2="100" y2="10" stroke="#ccc"></line>' in body
    assert '<rect x="0" y="30" width="40" height="10" fill="#0f766e"></rect>' in body
    parents = _tree(body).parents
    for child in ("line", "rect", "circle", "text"):
        assert (child, "svg") in parents, child
    assert not any(parent in {"line", "rect", "circle"} for _, parent in parents)
    assert "<p>After the chart.</p>" in body


def test_html_self_closing_div_is_closed(tmp_path: Path):
    body = _render_body(tmp_path, '<div class="spacer"/>\n<p>Next paragraph.</p>\n')
    assert '<div class="spacer"></div>' in body
    assert ("p", None) in _tree(body).parents


def test_void_tags_have_no_closing_tag(tmp_path: Path):
    body = _render_body(
        tmp_path,
        '<p>Line one<br/>line two</p>\n\n<hr/>\n\n<p><img src="https://example.com/a.png" alt="A"/></p>\n',
    )
    for tag in VOID:
        assert f"<{tag}" in body, tag
        assert f"</{tag}>" not in body, tag
    _tree(body)


def test_self_closing_table_gets_scroll_wrapper(tmp_path: Path):
    body = _render_body(tmp_path, "<table/>\n\n<p>After.</p>\n")
    wrapped = '<div class="table-wrap" role="region" aria-label="Table" tabindex="0"><table></table></div>'
    assert wrapped in body, body
    _tree(body)


def test_self_closing_dropped_tags_leave_no_trace(tmp_path: Path):
    body = _render_body(tmp_path, '<p>Before<script src="x.js"/> middle<style/> after</p>\n')
    assert "<script" not in body and "<style" not in body
    assert "Before" in body and "middle" in body and "after" in body
    _tree(body)


def test_self_closing_inside_dropped_content_keeps_drop_balanced(tmp_path: Path):
    body = _render_body(
        tmp_path,
        "<p>Start<iframe/> one<object/> two</p>\n\n"
        "<template><div/><script/>hidden</template>\n\nVisible after.\n",
    )
    assert "<iframe" not in body and "<object" not in body and "<template" not in body
    assert "one" in body and "two" in body
    assert "hidden" not in body
    assert "<p>Visible after.</p>" in body
    _tree(body)
