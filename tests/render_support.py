"""Shared helper for the fork's renderer regression tests (WR tickets).

`render_body` writes a markdown post with fixed frontmatter, runs the real
renderer CLI on it (no PDF), and returns the body HTML between the bs-body
markers, so tests check only the CLI's outside behaviour.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "blog_render.py"
START = "<!--bs-body:start-->"
END = "<!--bs-body:end-->"


def frontmatter(slug: str, extra: str = "") -> str:
    return (
        f"---\ntitle: Fixture Post\ndescription: A short description of the post.\n"
        f"date: 2026-11-25\nauthor: Alex Rivera\ncanonical: https://example.com/blog/{slug}/\n"
        f"slug: {slug}\n{extra}---\n"
    )


def render(tmp_path: Path, body: str, slug: str = "fixture", extra_frontmatter: str = "") -> subprocess.CompletedProcess[str]:
    """Render `body` under fixed frontmatter; return the finished process."""
    source = tmp_path / f"{slug}.md"
    source.write_bytes((frontmatter(slug, extra_frontmatter) + body).encode("utf-8"))
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--md", str(source), "--out-dir", str(tmp_path), "--pdf-engine", "none"],
        capture_output=True, text=True, encoding="utf-8",
    )


def render_page(tmp_path: Path, body: str, slug: str = "fixture", extra_frontmatter: str = "") -> str:
    """Render and return the whole HTML page; fail the test on a render error."""
    result = render(tmp_path, body, slug, extra_frontmatter)
    assert result.returncode == 0, result.stderr
    return (tmp_path / f"{slug}.html").read_text(encoding="utf-8")


def render_body(tmp_path: Path, body: str, slug: str = "fixture", extra_frontmatter: str = "") -> str:
    """Render and return only the body HTML between the bs-body markers."""
    page = render_page(tmp_path, body, slug, extra_frontmatter)
    return page[page.index(START) + len(START):page.rindex(END)]
