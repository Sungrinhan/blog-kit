"""WR-10: publisher Organization and BreadcrumbList when site_url is set.

Reviews and SEO checks docked every post for a missing publisher and
breadcrumb (PAIN-POINTS 21, second half). With frontmatter `site_url` the
BlogPosting gains a publisher Organization (name: site_name, else the
site_url host; url: site_url) and a second JSON-LD block holds a
BreadcrumbList (1: the site, 2: the post title and canonical; without a
canonical item 2 has no `item`). Without `site_url` neither appears, so no
site identity is invented.

Render tests go through the CLI; the gate tests render the golden draft and
run Gate 5 (no browser) and Gate 3 (Chromium, ubuntu CI).
"""

from __future__ import annotations

import json
import re

from gate_support import PREFLIGHT, SLUG, golden_draft, offline_probes, require_browser  # noqa: F401
from render_support import jsonld_blocks, render_page

BODY = "Body paragraph.\n"
SITE = "https://sungrin.example.io/dev-notes/"


_blocks = jsonld_blocks


def _by_type(page: str, kind: str) -> list[dict]:
    return [block for block in _blocks(page) if block.get("@type") == kind]


def test_site_url_adds_publisher_with_site_name(tmp_path):
    page = render_page(tmp_path, BODY, slug="pub", extra_frontmatter=f"site_url: {SITE}\nsite_name: Dev Notes\n")
    (post,) = _by_type(page, "BlogPosting")
    assert post["publisher"] == {"@type": "Organization", "name": "Dev Notes", "url": SITE}


def test_publisher_name_falls_back_to_the_site_url_host(tmp_path):
    page = render_page(tmp_path, BODY, slug="pub", extra_frontmatter=f"site_url: {SITE}\n")
    (post,) = _by_type(page, "BlogPosting")
    assert post["publisher"]["name"] == "sungrin.example.io"


def test_site_url_adds_a_separate_breadcrumb_block(tmp_path):
    page = render_page(tmp_path, BODY, slug="crumb", extra_frontmatter=f"site_url: {SITE}\nsite_name: Dev Notes\n")
    blocks = _blocks(page)
    assert [b["@type"] for b in blocks] == ["BlogPosting", "BreadcrumbList"]
    (crumbs,) = _by_type(page, "BreadcrumbList")
    assert crumbs["@context"] == "https://schema.org"
    assert crumbs["itemListElement"] == [
        {"@type": "ListItem", "position": 1, "name": "Dev Notes", "item": SITE},
        {"@type": "ListItem", "position": 2, "name": "Fixture Post", "item": "https://example.com/blog/crumb/"},
    ]


def test_breadcrumb_without_canonical_omits_the_post_item(tmp_path):
    page = render_page(tmp_path, BODY, slug="nocanon", extra_frontmatter=f"site_url: {SITE}\n", canonical=False)
    (crumbs,) = _by_type(page, "BreadcrumbList")
    assert crumbs["itemListElement"][0]["name"] == "sungrin.example.io"
    assert crumbs["itemListElement"][1] == {"@type": "ListItem", "position": 2, "name": "Fixture Post"}


def test_without_site_url_there_is_no_publisher_or_breadcrumb(tmp_path):
    page = render_page(tmp_path, BODY, slug="none", extra_frontmatter="site_name: Dev Notes\n")
    assert [b["@type"] for b in _blocks(page)] == ["BlogPosting"]
    assert "publisher" not in _by_type(page, "BlogPosting")[0]


def test_breadcrumb_json_cannot_break_out_of_its_script(tmp_path):
    page = render_page(
        tmp_path, BODY, slug="xss",
        extra_frontmatter=f"site_url: {SITE}\nsite_name: Notes</script><script>alert(1)</script>\n",
    )
    # Only "</script>" can end a script element; it must appear escaped.
    assert "</script><script>alert(1)" not in page
    assert "<\\/script><script>alert(1)<\\/script>" in page
    assert _by_type(page, "BreadcrumbList")[0]["itemListElement"][0]["name"].startswith("Notes</script>")


SITE_KEYS = f"site_url: {SITE}\n"


def test_gate_5_accepts_both_blocks(tmp_path):
    draft = golden_draft(tmp_path, frontmatter=SITE_KEYS)
    page = (draft / f"{SLUG}.html").read_text(encoding="utf-8")
    assert [b["@type"] for b in _blocks(page)] == ["BlogPosting", "BreadcrumbList"]
    result = PREFLIGHT.gate_5_asset_link_integrity(draft, SLUG)
    assert result["passed"], result["violations"]


def test_gate_3_accepts_both_blocks(tmp_path):
    require_browser()
    draft = golden_draft(tmp_path, frontmatter=SITE_KEYS)
    result = PREFLIGHT.gate_3_visual_verification(draft, SLUG)
    assert result["passed"], result["violations"]
