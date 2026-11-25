"""WR-09: footer site link, author url/sameAs, hero fetchpriority.

The footer "Published at" link was always the canonical's origin, so a post
on a GitHub project page (`/dev-notes/`) linked the account's root site
(PAIN-POINTS 20). Frontmatter `site_url` now sets it. `author_url` and
`author_same_as` fill JSON-LD author.url and author.sameAs, and the hero image
always carries fetchpriority="high" (PAIN-POINTS 21, first half).

URL rules: http(s), a hostname, no user info; `site_url` also refuses a query
or fragment and keeps a trailing slash as given. A bad value fails the render.

Render tests go through the CLI; the gate tests render the golden draft and
run Gate 5 (no browser) and Gate 3 (Chromium, ubuntu CI).
"""

from __future__ import annotations

import json
import re

import pytest
from gate_support import PREFLIGHT, SLUG, golden_draft, offline_probes, require_browser  # noqa: F401
from render_support import render, render_page

BODY = "Body paragraph.\n"
SITE = "https://sungrin.example.io/dev-notes/"
SAME_AS = ["https://github.com/example-author", "https://www.linkedin.com/in/example-author"]


def _blogposting(page: str) -> dict:
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        data = json.loads(block)
        if data.get("@type") == "BlogPosting":
            return data
    raise AssertionError("no BlogPosting JSON-LD")


def _footer_href(page: str) -> str:
    return re.search(r'Published at <a href="([^"]*)" data-bs-generated="1"', page).group(1)


def test_site_url_sets_the_footer_link_as_given(tmp_path):
    page = render_page(tmp_path, BODY, slug="site", extra_frontmatter=f"site_url: {SITE}\n")
    assert _footer_href(page) == SITE
    no_slash = render_page(tmp_path, BODY, slug="site", extra_frontmatter="site_url: https://a.example/notes\n")
    assert _footer_href(no_slash) == "https://a.example/notes"


def test_without_site_url_the_footer_link_is_the_canonical_origin(tmp_path):
    page = render_page(tmp_path, BODY, slug="site")
    assert _footer_href(page) == "https://example.com"


def test_author_url_and_same_as_fill_the_person(tmp_path):
    extra = (
        "author_url: https://example.com/about/\n"
        f"author_same_as: {json.dumps(SAME_AS)}\n"
    )
    person = _blogposting(render_page(tmp_path, BODY, slug="person", extra_frontmatter=extra))["author"]
    assert person == {
        "@type": "Person", "name": "Alex Rivera",
        "url": "https://example.com/about/", "sameAs": SAME_AS,
    }


def test_block_list_same_as(tmp_path):
    extra = "author_same_as:\n" + "".join(f"  - {url}\n" for url in SAME_AS)
    person = _blogposting(render_page(tmp_path, BODY, slug="person", extra_frontmatter=extra))["author"]
    assert person["sameAs"] == SAME_AS


def test_existing_blogposting_fields_are_unchanged(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    plain = _blogposting(render_page(tmp_path / "a", BODY, slug="post"))
    extra = f"site_url: {SITE}\nauthor_url: https://example.com/about/\nauthor_same_as: [{SAME_AS[0]}]\n"
    rich = _blogposting(render_page(tmp_path / "b", BODY, slug="post", extra_frontmatter=extra))
    assert plain["author"] == {"@type": "Person", "name": "Alex Rivera"}
    for key, value in plain.items():
        if key not in {"author", "publisher"}:
            assert rich[key] == value, key


def test_hero_image_has_high_fetch_priority(tmp_path):
    page = render_page(tmp_path, BODY, slug="hero")
    assert re.search(r'<figure class="hero"><img [^>]*fetchpriority="high"', page)


@pytest.mark.parametrize("key", ["site_url", "author_url", "author_same_as"])
@pytest.mark.parametrize(
    "value",
    ["example.com/notes", "ftp://example.com/", "https://", "https://user@example.com/",
     "https://user:pw@example.com/", "javascript:alert(1)", "/relative/path"],
)
def test_bad_urls_fail_the_render(tmp_path, key, value):
    result = render(tmp_path, BODY, slug="bad", extra_frontmatter=f"{key}: {value}\n")
    assert result.returncode != 0, (key, value)
    assert f"frontmatter {key}" in result.stderr
    assert not (tmp_path / "bad.html").exists()


@pytest.mark.parametrize("value", ["https://a.example/notes?x=1", "https://a.example/notes#top", "https://a.example/?"])
def test_site_url_refuses_query_and_fragment(tmp_path, value):
    result = render(tmp_path, BODY, slug="bad", extra_frontmatter=f"site_url: {value}\n")
    assert result.returncode != 0
    assert "query or fragment" in result.stderr


def test_author_url_may_carry_a_query(tmp_path):
    result = render(tmp_path, BODY, slug="ok", extra_frontmatter="author_url: https://a.example/u?id=1\n")
    assert result.returncode == 0, result.stderr


NEW_KEYS = (
    f"site_url: {SITE}\n"
    "author_url: https://example.com/about/\n"
    f"author_same_as: {json.dumps(SAME_AS)}\n"
)


def test_gate_5_passes_with_the_new_keys(tmp_path):
    draft = golden_draft(tmp_path, frontmatter=NEW_KEYS)
    page = (draft / f"{SLUG}.html").read_text(encoding="utf-8")
    assert _footer_href(page) == SITE and _blogposting(page)["author"]["sameAs"] == SAME_AS
    result = PREFLIGHT.gate_5_asset_link_integrity(draft, SLUG)
    assert result["passed"], result["violations"]


def test_gate_3_passes_with_the_new_keys(tmp_path):
    require_browser()
    draft = golden_draft(tmp_path, frontmatter=NEW_KEYS)
    result = PREFLIGHT.gate_3_visual_verification(draft, SLUG)
    assert result["passed"], result["violations"]
