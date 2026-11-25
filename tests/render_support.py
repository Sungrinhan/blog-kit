"""Shared helper for the fork's renderer regression tests (WR tickets).

`render_body` writes a markdown post with fixed frontmatter, runs the real
renderer CLI on it (no PDF), and returns the body HTML between the bs-body
markers, so tests check only the CLI's outside behaviour.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "blog_render.py"
START = "<!--bs-body:start-->"
END = "<!--bs-body:end-->"


def frontmatter(slug: str, extra: str = "", canonical: bool = True) -> str:
    canonical_line = f"canonical: https://example.com/blog/{slug}/\n" if canonical else ""
    return (
        f"---\ntitle: Fixture Post\ndescription: A short description of the post.\n"
        f"date: 2026-11-25\nauthor: Alex Rivera\n{canonical_line}"
        f"slug: {slug}\n{extra}---\n"
    )


def render(
    tmp_path: Path, body: str, slug: str = "fixture", extra_frontmatter: str = "", canonical: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Render `body` under fixed frontmatter; return the finished process."""
    source = tmp_path / f"{slug}.md"
    source.write_bytes((frontmatter(slug, extra_frontmatter, canonical) + body).encode("utf-8"))
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--md", str(source), "--out-dir", str(tmp_path), "--pdf-engine", "none"],
        capture_output=True, text=True, encoding="utf-8",
    )


def render_page(
    tmp_path: Path, body: str, slug: str = "fixture", extra_frontmatter: str = "", canonical: bool = True,
) -> str:
    """Render and return the whole HTML page; fail the test on a render error."""
    result = render(tmp_path, body, slug, extra_frontmatter, canonical)
    assert result.returncode == 0, result.stderr
    return (tmp_path / f"{slug}.html").read_text(encoding="utf-8")


def render_body(tmp_path: Path, body: str, slug: str = "fixture", extra_frontmatter: str = "") -> str:
    """Render and return only the body HTML between the bs-body markers."""
    page = render_page(tmp_path, body, slug, extra_frontmatter)
    return page[page.index(START) + len(START):page.rindex(END)]


def jsonld_blocks(page: str) -> list[dict]:
    """Every JSON-LD block of a rendered page, in document order."""
    return [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)]


VOID_TAGS = {"br", "hr", "img"}


class ElementTree(HTMLParser):
    """Record each element's parent as a browser-like parser nests them.

    Fails on an unbalanced end tag or a self-closing tag, which the
    renderer's sanitized output never contains."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.parents: set[tuple[str, str | None]] = set()

    def handle_starttag(self, tag, attrs):
        self.parents.add((tag, self.stack[-1] if self.stack else None))
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        raise AssertionError(f"self-closing tag left in output: {tag}")

    def handle_endtag(self, tag):
        assert self.stack and self.stack[-1] == tag, f"unbalanced </{tag}> in {self.stack}"
        self.stack.pop()


def element_tree(html: str) -> ElementTree:
    """Parse body HTML; fail if any element is left unclosed."""
    tree = ElementTree()
    tree.feed(html)
    tree.close()
    assert tree.stack == [], f"unclosed elements: {tree.stack}"
    return tree
