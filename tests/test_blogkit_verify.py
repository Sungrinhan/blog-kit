"""S2: the publish verifier (adapters/astro/blogkit_verify.py) in source mode.

Each test builds a temp blog repo (posts, post-only assets, publish evidence)
and runs the verifier as a subprocess. Its JSON output and exit code are the
interface. The content hash bytes are pinned by golden vectors in
tests/fixtures/blogkit/hash-vectors.json plus one worked example built from
the spec by hand.

Stdlib + pytest only. No network.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VERIFIER = ROOT / "adapters" / "astro" / "blogkit_verify.py"
VECTORS = ROOT / "tests" / "fixtures" / "blogkit" / "hash-vectors.json"
# Planted in post bodies. The verifier must never print post text.
PLANTED = "PLANTED-CONFIDENTIAL-7Q"
ENGINE = "2.3.1-kit.2"


def _module():
    spec = importlib.util.spec_from_file_location("blogkit_verify_under_test", VERIFIER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V = _module()


def _digest(ids) -> str:
    # Independent of the verifier: the spec's sorted, newline-joined UTF-8 list.
    return hashlib.sha256("\n".join(sorted(set(ids))).encode("utf-8")).hexdigest()


def post_text(draft: str = "false", body: str = "", extra: str = "") -> str:
    return (
        "---\n"
        'title: "A post"\n'
        'description: "x"\n'
        'pubDate: "2026-10-26"\n'
        'tags: ["astro"]\n'
        + extra
        + f"draft: {draft}\n"
        + "---\n\n"
        + (body or f"Body with {PLANTED}.\n")
    )


class Blog:
    def __init__(self, tmp: Path) -> None:
        self.root = tmp / "blog"
        (self.root / "src" / "content" / "posts").mkdir(parents=True)
        self.env = {k: v for k, v in os.environ.items() if k != "BLOGKIT_INCLUDE_DRAFTS"}

    def path(self, relative: str) -> Path:
        return self.root / relative

    def write(self, relative: str, data: str | bytes) -> Path:
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def post(self, slug: str, text: str | None = None, assets: dict[str, bytes] | None = None) -> None:
        self.write(f"src/content/posts/{slug}.md", text if text is not None else post_text())
        for name, data in (assets or {}).items():
            self.write(f"src/assets/posts/{slug}/{name}", data)

    def evidence(self, slug: str, warnings: dict[int, list[str]] | None = None, **change) -> dict:
        warnings = warnings or {}
        current = V.content_hash(self.root, slug)
        records = [
            {"kind": "gate", "gate": n, "passed": True, "warnings": warnings.get(n, []),
             "content_hash": current, "engine_version": ENGINE}
            for n in range(1, 7)
        ]
        records += [
            {"kind": "review", "p0": 0, "content_hash": current, "engine_version": ENGINE},
            {"kind": "learning", "passed": True, "content_hash": current, "engine_version": ENGINE},
            {"kind": "approval", "content_hash": current, "engine_version": ENGINE,
             "warnings_digest": _digest(w for ws in warnings.values() for w in ws)},
        ]
        evidence = {
            "contract": 1, "slug": slug, "post_path": f"src/content/posts/{slug}.md",
            "hash_algo": "blogkit-content-v1", "content_hash": current, "engine_version": ENGINE,
            "public_copy": {"post_path": f"src/content/posts/{slug}.md", "post_digest": "0" * 64,
                            "assets": [], "engine_version": ENGINE},
            "records": records,
        }
        evidence.update(change)
        self.save(slug, evidence)
        return evidence

    def save(self, slug: str, evidence) -> None:
        self.write(f"inbox/{slug}/publish-evidence.json", json.dumps(evidence))

    def run(self, verifier: Path = VERIFIER, *extra: str) -> tuple[int, dict, str]:
        result = subprocess.run(
            [sys.executable, str(verifier), "--repo", str(self.root), *extra],
            capture_output=True, text=True, env=self.env, timeout=60,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = {}
        return result.returncode, payload, result.stdout + result.stderr


@pytest.fixture
def blog(tmp_path: Path) -> Blog:
    return Blog(tmp_path)


def _codes(payload: dict) -> list[str]:
    return [error["code"] for error in payload.get("errors", [])]


LEGACY_DRAFT = (
    "---\ntitle: 'Markdown Style Guide'\ndescription: 'sample'\npubDate: 'Jun 19 2024'\n"
    "heroImage: '../../assets/blog-placeholder-1.jpg'\ndraft: true\n---\n\nSample.\n"
)


# ------------------------------------------------------------------ passing


def test_public_post_with_complete_evidence_passes(blog: Blog) -> None:
    blog.post("first-post", assets={"chart.svg": b"<svg></svg>", "photo.png": b"\x89PNG"})
    blog.evidence("first-post", warnings={5: ["5:number:src/content/posts/first-post.md:3"]})
    rc, payload, output = blog.run()
    assert (rc, payload["status"], payload["public"], payload["errors"]) == (0, "ok", ["first-post"], [])
    assert PLANTED not in output


def test_drafts_need_no_evidence(blog: Blog) -> None:
    blog.post("draft-post", post_text(draft="true"))
    blog.write("src/content/posts/markdown-style-guide.md", LEGACY_DRAFT)
    blog.write("src/content/posts/notes/idea.mdx", LEGACY_DRAFT)
    rc, payload, _ = blog.run()
    assert (rc, payload["public"], payload["errors"]) == (0, [], [])


def test_empty_repo_passes(blog: Blog) -> None:
    rc, payload, _ = blog.run()
    assert (rc, payload["status"]) == (0, "ok")


def test_evidence_from_an_older_engine_under_a_supported_contract_passes(blog: Blog) -> None:
    blog.post("old-post")
    evidence = blog.evidence("old-post", engine_version="2.3.1-kit.1")
    for record in evidence["records"]:
        record["engine_version"] = "2.3.1-kit.1"
    blog.save("old-post", evidence)
    rc, payload, _ = blog.run()
    assert (rc, payload["public"]) == (0, ["old-post"])


def test_publishing_flips_only_the_draft_line_and_keeps_the_hash(blog: Blog) -> None:
    blog.post("flip", post_text(draft="true"))
    draft_hash = V.content_hash(blog.root, "flip")
    blog.post("flip", post_text(draft="false"))
    assert V.content_hash(blog.root, "flip") == draft_hash


# ------------------------------------------------------------------ failing


def _no_evidence(b: Blog) -> None:
    b.post("p")


def _body_changed(b: Blog) -> None:
    b.post("p")
    b.evidence("p")
    b.post("p", post_text(body="Edited after approval.\n"))


def _frontmatter_changed(b: Blog) -> None:
    b.post("p")
    b.evidence("p")
    b.post("p", post_text().replace("2026-10-26", "2026-10-27"))


def _asset_changed(b: Blog) -> None:
    b.post("p", assets={"a.png": b"one"})
    b.evidence("p")
    b.write("src/assets/posts/p/a.png", b"two")


def _asset_added(b: Blog) -> None:
    b.post("p")
    b.evidence("p")
    b.write("src/assets/posts/p/new.png", b"x")


def _unsupported_contract(b: Blog) -> None:
    b.post("p")
    b.evidence("p", contract=2)


def _public_mdx(b: Blog) -> None:
    b.write("src/content/posts/p.mdx", post_text())


def _public_in_subfolder(b: Blog) -> None:
    b.write("src/content/posts/series/p.md", post_text())


def _grammar_violation(b: Blog) -> None:
    b.post("p", post_text(extra="heroImage: \"../../assets/x.jpg\"\n"))
    b.write("inbox/p/publish-evidence.json", "{}")


def _crlf(b: Blog) -> None:
    b.post("p", post_text().replace("\n", "\r\n"))


def _bom(b: Blog) -> None:
    b.post("p", b"\xef\xbb\xbf" + post_text().encode())


def _draft_shaped_bypass(b: Blog) -> None:
    b.write("src/content/posts/p.md", LEGACY_DRAFT.replace("draft: true\n", "draft: true\n'draft': false\n"))


def _yaml_draft_false(b: Blog) -> None:
    b.write("src/content/posts/p.md", LEGACY_DRAFT.replace("draft: true", "draft: False"))


def _symlinked_post(b: Blog) -> None:
    real = b.write("elsewhere/p.md", post_text())
    b.path("src/content/posts/p.md").symlink_to(real)


def _symlinked_asset(b: Blog) -> None:
    b.post("p")
    real = b.write("elsewhere/a.png", b"x")
    b.path("src/assets/posts/p").mkdir(parents=True)
    b.path("src/assets/posts/p/a.png").symlink_to(real)


def _symlinked_posts_folder(b: Blog) -> None:
    posts = b.path("src/content/posts")
    posts.rmdir()
    real = b.root / "real-posts"
    real.mkdir()
    posts.symlink_to(real)


def _oversize_post(b: Blog) -> None:
    b.post("p", post_text(body="x" * (1024 * 1024)))


def _bad_asset_name(b: Blog) -> None:
    b.post("p", assets={"Photo.PNG": b"x"})


def _hidden_asset(b: Blog) -> None:
    b.post("p", assets={".DS_Store": b"x"})


def _asset_type(b: Blog) -> None:
    b.post("p", assets={"run.js": b"x"})


def _bad_slug(b: Blog) -> None:
    b.write("src/content/posts/Bad_Slug.md", post_text())


def _preview_switch(b: Blog) -> None:
    b.env["BLOGKIT_INCLUDE_DRAFTS"] = "1"


def _evidence_symlink(b: Blog) -> None:
    b.post("p")
    b.evidence("p")
    real = b.root / "evidence-real"
    b.path("inbox/p").rename(real)
    b.path("inbox/p").symlink_to(real)


def _evidence_fifo(b: Blog) -> None:
    b.post("p")
    b.evidence("p")
    b.path("inbox/p/publish-evidence.json").unlink()
    os.mkfifo(b.path("inbox/p/publish-evidence.json"))


FAILING = {
    "no evidence": (_no_evidence, "missing_file"),
    "body changed after approval": (_body_changed, "content_hash_mismatch"),
    "frontmatter changed after approval": (_frontmatter_changed, "content_hash_mismatch"),
    "asset changed after approval": (_asset_changed, "content_hash_mismatch"),
    "asset added after approval": (_asset_added, "content_hash_mismatch"),
    "unsupported contract": (_unsupported_contract, "evidence_contract_unsupported"),
    "public mdx": (_public_mdx, "public_mdx_not_supported"),
    "public post in a subfolder": (_public_in_subfolder, "public_post_in_subfolder"),
    "frontmatter outside the grammar": (_grammar_violation, "draft_status_unclear"),
    "CRLF line endings": (_crlf, "frontmatter_missing"),
    "BOM": (_bom, "bom_not_allowed"),
    "draft-shaped key next to draft: true": (_draft_shaped_bypass, "draft_status_unclear"),
    "YAML spelling of false": (_yaml_draft_false, "draft_status_unclear"),
    "symlinked post": (_symlinked_post, "symlink_refused"),
    "symlinked asset": (_symlinked_asset, "symlink_refused"),
    "symlinked posts folder": (_symlinked_posts_folder, "symlink_refused"),
    "oversize post": (_oversize_post, "file_too_large"),
    "asset name with capitals": (_bad_asset_name, "asset_name_invalid"),
    "hidden asset": (_hidden_asset, "asset_name_invalid"),
    "asset type not allowed": (_asset_type, "asset_type_not_allowed"),
    "invalid slug": (_bad_slug, "slug_invalid"),
    "preview switch set": (_preview_switch, "preview_drafts_enabled"),
    "evidence folder is a symlink": (_evidence_symlink, "symlink_refused"),
    "evidence is a FIFO": (_evidence_fifo, "not_a_regular_file"),
}


@pytest.mark.parametrize("case", sorted(FAILING))
def test_each_failure_is_reported_and_exits_1(blog: Blog, case: str) -> None:
    setup, code = FAILING[case]
    setup(blog)
    rc, payload, output = blog.run()
    assert rc == 1 and payload["status"] == "fail"
    assert code in _codes(payload), payload
    assert PLANTED not in output


# ------------------------------------------------------------------ evidence contract


def _drop_field(e: dict) -> None:
    del e["engine_version"]


def _wrong_type(e: dict) -> None:
    e["contract"] = "1"


def _bool_contract(e: dict) -> None:
    e["contract"] = True


def _unknown_field(e: dict) -> None:
    e["note"] = "x"


def _duplicate_record(e: dict) -> None:
    e["records"].append(dict(e["records"][0]))


def _gate_missing(e: dict) -> None:
    e["records"] = [r for r in e["records"] if r.get("gate") != 4]


def _gate_not_passed(e: dict) -> None:
    e["records"][2]["passed"] = False


def _negative_p0(e: dict) -> None:
    next(r for r in e["records"] if r["kind"] == "review")["p0"] = -1


def _p0_found(e: dict) -> None:
    next(r for r in e["records"] if r["kind"] == "review")["p0"] = 1


def _learning_failed(e: dict) -> None:
    next(r for r in e["records"] if r["kind"] == "learning")["passed"] = False


def _approval_missing(e: dict) -> None:
    e["records"] = [r for r in e["records"] if r["kind"] != "approval"]


def _warnings_digest_mismatch(e: dict) -> None:
    next(r for r in e["records"] if r["kind"] == "approval")["warnings_digest"] = _digest(["other"])


def _record_hash_mismatch(e: dict) -> None:
    e["records"][0]["content_hash"] = "f" * 64


def _record_unknown_kind(e: dict) -> None:
    e["records"].append({"kind": "manual", "content_hash": "0" * 64, "engine_version": ENGINE})


def _gate_out_of_range(e: dict) -> None:
    e["records"][0]["gate"] = 7


def _wrong_post(e: dict) -> None:
    e["slug"] = "other"


def _kind_of_wrong_type(e: dict) -> None:
    e["records"].append({"kind": [], "content_hash": "0" * 64, "engine_version": ENGINE})


CONTRACT = {
    "field missing": (_drop_field, "evidence_fields"),
    "wrong type": (_wrong_type, "evidence_contract_invalid"),
    "boolean contract": (_bool_contract, "evidence_contract_invalid"),
    "unknown field": (_unknown_field, "evidence_fields"),
    "duplicate record": (_duplicate_record, "evidence_duplicate_record"),
    "gate missing": (_gate_missing, "gate_4_missing"),
    "gate not passed": (_gate_not_passed, "gate_3_not_passed"),
    "negative p0": (_negative_p0, "evidence_record_type"),
    "p0 found": (_p0_found, "review_has_p0"),
    "learning failed": (_learning_failed, "learning_not_passed"),
    "approval missing": (_approval_missing, "approval_missing"),
    "warnings digest mismatch": (_warnings_digest_mismatch, "approval_warnings_mismatch"),
    "record bound to another hash": (_record_hash_mismatch, "record_hash_mismatch"),
    "unknown record kind": (_record_unknown_kind, "evidence_record_kind"),
    "gate number out of range": (_gate_out_of_range, "evidence_record_type"),
    "evidence for another post": (_wrong_post, "evidence_wrong_post"),
    "record kind of the wrong type": (_kind_of_wrong_type, "evidence_record_kind"),
}


@pytest.mark.parametrize("case", sorted(CONTRACT))
def test_evidence_contract_violations_fail(blog: Blog, case: str) -> None:
    blog.post("p")
    evidence = blog.evidence("p", warnings={5: ["5:email:src/content/posts/p.md:9"]})
    change, code = CONTRACT[case]
    change(evidence)
    blog.save("p", evidence)
    rc, payload, _ = blog.run()
    assert rc == 1 and code in _codes(payload), payload


def test_evidence_that_is_not_json_fails(blog: Blog) -> None:
    blog.post("p")
    blog.write("inbox/p/publish-evidence.json", "not json")
    rc, payload, _ = blog.run()
    assert rc == 1 and _codes(payload) == ["evidence_not_json"]


# ------------------------------------------------------------------ self digest and header


def test_a_hand_edited_copy_fails_its_self_check(blog: Blog, tmp_path: Path) -> None:
    copy = tmp_path / "scripts" / "blogkit_verify.py"
    copy.parent.mkdir()
    copy.write_bytes(VERIFIER.read_bytes().replace(b"MAX_POST_BYTES = 1024 * 1024", b"MAX_POST_BYTES = 9 * 1024 * 1024"))
    rc, payload, _ = blog.run(copy)
    assert rc == 1 and _codes(payload) == ["self_digest_mismatch"]


def test_a_byte_identical_copy_passes_its_self_check(blog: Blog, tmp_path: Path) -> None:
    copy = tmp_path / "scripts" / "blogkit_verify.py"
    copy.parent.mkdir()
    copy.write_bytes(VERIFIER.read_bytes())
    rc, _, _ = blog.run(copy)
    assert rc == 0


def test_the_canonical_file_carries_its_digest_and_the_engine_version() -> None:
    data = VERIFIER.read_bytes()
    assert V.check_self(VERIFIER) is None, f"restamp SELF_DIGEST to {V.self_digest(data)}"
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert V.VERIFIER_VERSION == plugin["version"]


def test_a_symlinked_repo_argument_is_refused(blog: Blog, tmp_path: Path) -> None:
    link = tmp_path / "blog-link"
    link.symlink_to(blog.root)
    result = subprocess.run([sys.executable, str(VERIFIER), "--repo", str(link)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 2


# ------------------------------------------------------------------ content hash bytes


def test_worked_example_matches_the_spec_serialization() -> None:
    post = b"---\ntitle: \"t\"\ndraft: false\n---\nhi\n"
    hashed = b"---\ntitle: \"t\"\ndraft: <omitted>\n---\nhi\n"
    path = "src/content/posts/x.md"
    expected = hashlib.sha256(
        b"blogkit-content-v1\n"
        + b"post\n" + b"22\n" + path.encode() + b"\n" + str(len(hashed)).encode() + b"\n" + hashed + b"\n"
        + b"asset\n" + b"24\n" + b"src/assets/posts/x/a.png" + b"\n" + b"1\n" + b"A" + b"\n"
    ).hexdigest()
    assert V.hash_parts(path, post, {"src/assets/posts/x/a.png": b"A"}) == expected


def _decode(value: str) -> bytes:
    return value.encode("utf-8")


@pytest.mark.parametrize("case", [c["name"] for c in json.loads(VECTORS.read_text(encoding="utf-8"))["cases"]])
def test_golden_hash_vectors(case: str) -> None:
    vector = next(c for c in json.loads(VECTORS.read_text(encoding="utf-8"))["cases"] if c["name"] == case)
    assets = {path: _decode(data) for path, data in vector.get("assets", {}).items()}
    post = _decode(vector["post"])
    if "error" in vector:
        with pytest.raises(V.VerifyError) as caught:
            V.hash_parts(vector["post_path"], post, assets)
        assert caught.value.code == vector["error"]
    else:
        assert V.hash_parts(vector["post_path"], post, assets) == vector["content_sha256"]


# ------------------------------------------------------------------ deploy output (--dist)

SITE = "https://blog.example.test"


class Dist:
    """A built site folder shaped like the Astro output the deploy command checks."""

    def __init__(self, blog: Blog, slugs: list[str] | None = None) -> None:
        self.root = blog.root / "dist"
        self.slugs = list(slugs or [])
        for relative in ("index.html", "about/index.html", "posts/index.html", "favicon.svg", "_astro/site.css"):
            self.write(relative, "<p>page</p>")
        for slug in self.slugs:
            self.write(f"posts/{slug}/index.html", f"<p>{PLANTED}</p>")
        self.feeds()

    def write(self, relative: str, data: str | bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def feeds(self, extra_urls: list[str] = (), sitemaps: list[str] | None = None,
              extra_links: list[str] = ()) -> None:
        pages = ["/", "/about/", "/posts/"] + [f"/posts/{slug}/" for slug in self.slugs]
        urls = [SITE + page for page in pages] + list(extra_urls)
        links = [f"{SITE}/posts/{slug}/" for slug in self.slugs] + list(extra_links)
        items = "".join(f"<item><title>t</title><link>{link}</link><guid isPermaLink=\"true\">"
                        f"{link}</guid></item>" for link in links)
        self.write("rss.xml", f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title>'
                              f"<link>{SITE}/</link>{items}</channel></rss>")
        index = "".join(f"<sitemap><loc>{loc}</loc></sitemap>" for loc in (sitemaps or [f"{SITE}/sitemap-0.xml"]))
        self.write("sitemap-index.xml", '<?xml version="1.0" encoding="UTF-8"?>'
                   f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{index}</sitemapindex>')
        locs = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
        self.write("sitemap-0.xml", '<?xml version="1.0" encoding="UTF-8"?>'
                   f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>')


def _published(blog: Blog, slug: str = "first-post") -> Dist:
    blog.post(slug)
    blog.evidence(slug)
    return Dist(blog, [slug])


def run_dist(blog: Blog, dist: Path | str, *extra: str) -> tuple[int, dict, str]:
    return blog.run(VERIFIER, "--dist", str(dist), *extra)


def test_deploy_output_matching_the_verified_posts_passes(blog: Blog) -> None:
    dist = _published(blog)
    rc, payload, output = run_dist(blog, dist.root)
    assert (rc, payload["status"], payload["mode"], payload["public"], payload["errors"]) == (
        0, "ok", "dist", ["first-post"], [])
    assert PLANTED not in output and SITE not in output


def test_deploy_output_with_no_posts_passes(blog: Blog) -> None:
    dist = Dist(blog)
    rc, payload, _ = run_dist(blog, dist.root)
    assert (rc, payload["public"], payload["errors"]) == (0, [], [])


def _extra_post(b: Blog) -> Dist:
    dist = _published(b)
    dist.write("posts/ghost-post/index.html", "<p>draft leaked</p>")
    return dist


def _post_missing_from_output(b: Blog) -> Dist:
    b.post("second-post")
    b.evidence("second-post")
    return _published(b)


def _nested_post_page(b: Blog) -> Dist:
    dist = _published(b)
    dist.write("posts/first-post/part/index.html", "<p>x</p>")
    return dist


def _flat_post_page(b: Blog) -> Dist:
    dist = _published(b)
    dist.write("posts/ghost-post.html", "<p>x</p>")
    return dist


def _draft_built_without_evidence(b: Blog) -> Dist:
    b.post("draft-post", post_text(draft="true"))
    return Dist(b, ["draft-post"])


def _rss_links_unknown_post(b: Blog) -> Dist:
    dist = _published(b)
    dist.feeds(extra_links=[f"{SITE}/posts/ghost-post/"])
    return dist


def _sitemap_lists_unknown_post(b: Blog) -> Dist:
    dist = _published(b)
    dist.feeds(extra_urls=[f"{SITE}/posts/ghost-post/"])
    return dist


def _sitemap_url_without_page(b: Blog) -> Dist:
    dist = _published(b)
    (dist.root / "about/index.html").unlink()
    return dist


def _post_page_outside_posts(where: str, path: str):
    """A page built at another route and listed in a feed: every URL but /, /about/ and /posts/ is a post URL."""
    def setup(b: Blog) -> Dist:
        dist = _published(b)
        dist.write(path.strip("/") + "/index.html", "<p>draft leaked</p>")
        dist.feeds(**{where: [SITE + path]})
        return dist
    return setup


def _with_file(relative: str):
    def setup(b: Blog) -> Dist:
        dist = _published(b)
        dist.write(relative, "{}")
        return dist
    return setup


PRIVATE_OUTPUTS = {
    "inbox folder": "inbox/first-post/state.md",
    "nested inbox folder": "_astro/inbox/notes.txt",
    "engine folder": ".blogkit/journal.json",
    "evidence file": "assets/publish-evidence.json",
    "draft source": "drafts/first-post.md",
    "draft mdx source": "drafts/first-post.mdx",
    "review record": "review.md",
    "claims record": "first-post/claims.json",
    "approval record": "approval.json",
}


def _symlinked_output_folder(b: Blog) -> Dist:
    dist = _published(b)
    real = dist.root / "real-posts"
    (dist.root / "posts").rename(real)
    (dist.root / "posts").symlink_to(real.name)
    return dist


def _symlinked_output_file(b: Blog) -> Dist:
    dist = _published(b)
    real = b.write("elsewhere/site.css", "x")
    (dist.root / "_astro" / "site.css").unlink()
    (dist.root / "_astro" / "site.css").symlink_to(real)
    return dist


def _fifo_in_output(b: Blog) -> Dist:
    dist = _published(b)
    os.mkfifo(dist.root / "_astro" / "pipe")
    return dist


def _oversize_page(b: Blog) -> Dist:
    dist = _published(b)
    dist.write("about/index.html", b"x" * (5 * 1024 * 1024 + 1))
    return dist


def _oversize_sitemap(b: Blog) -> Dist:
    dist = _published(b)
    dist.feeds(extra_urls=[f"{SITE}/" + "a" * (5 * 1024 * 1024)])
    return dist


def _too_many_files(b: Blog) -> Dist:
    dist = _published(b)
    folder = dist.root / "_astro" / "many"
    folder.mkdir()
    for n in range(20001):
        (folder / f"{n}.js").touch()
    return dist


def _xml_with(declaration: str):
    def setup(b: Blog) -> Dist:
        dist = _published(b)
        text = (dist.root / "rss.xml").read_text(encoding="utf-8")
        dist.write("rss.xml", text.replace("?>", "?>" + declaration, 1))
        return dist
    return setup


def _xml_encoded(encoding: str):
    """The feed re-encoded (UTF-16 needs no ASCII "<!DOCTYPE" bytes) with a DOCTYPE and an entity."""
    def setup(b: Blog) -> Dist:
        dist = _published(b)
        text = (dist.root / "rss.xml").read_text(encoding="utf-8")
        text = text.replace('encoding="UTF-8"?>', 'encoding="UTF-16"?><!DOCTYPE rss [<!ENTITY e "x">]>', 1)
        dist.write("rss.xml", text.replace("<title>t</title>", "<title>&e;</title>", 1).encode(encoding))
        return dist
    return setup


def _xml_declared(encoding: str):
    def setup(b: Blog) -> Dist:
        dist = _published(b)
        text = (dist.root / "rss.xml").read_text(encoding="utf-8")
        dist.write("rss.xml", text.replace('encoding="UTF-8"', f'encoding="{encoding}"', 1))
        return dist
    return setup


def _feed_url(where: str, url: str):
    def setup(b: Blog) -> Dist:
        dist = _published(b)
        dist.feeds(**{where: [url]})
        return dist
    return setup


def _sitemap_at(loc: str, nested: bool = False):
    def setup(b: Blog) -> Dist:
        dist = _published(b)
        sitemap = (dist.root / "sitemap-0.xml").read_bytes()
        dist.feeds(sitemaps=[f"{SITE}/sitemap-0.xml", SITE + loc])
        target = loc.lstrip("/")
        if nested:
            dist.write(target, (dist.root / "sitemap-index.xml").read_bytes())
        elif not (dist.root / target).exists():
            dist.write(target, sitemap)
        return dist
    return setup


def _unreadable_feed(b: Blog) -> Dist:
    dist = _published(b)
    (dist.root / "rss.xml").chmod(0)
    return dist


def _without(relative: str, posts: bool = True):
    def setup(b: Blog) -> Dist:
        dist = _published(b) if posts else Dist(b)
        (dist.root / relative).unlink()
        return dist
    return setup


REQUIRED_OUTPUTS = ("index.html", "posts/index.html", "rss.xml", "sitemap-index.xml", "sitemap-0.xml")


DIST_FAILING = {
    "extra post in the output": (_extra_post, "dist_post_without_evidence"),
    "verified post missing from the output": (_post_missing_from_output, "dist_post_missing"),
    "nested page under a post": (_nested_post_page, "dist_post_path_invalid"),
    "flat page under posts": (_flat_post_page, "dist_post_path_invalid"),
    "draft built into the output": (_draft_built_without_evidence, "dist_post_without_evidence"),
    "RSS links a post without evidence": (_rss_links_unknown_post, "dist_feed_post_without_evidence"),
    "sitemap lists a post without evidence": (_sitemap_lists_unknown_post, "dist_feed_post_without_evidence"),
    "sitemap URL with no page": (_sitemap_url_without_page, "dist_url_target_missing"),
    "sitemap lists a page at another route": (_post_page_outside_posts("extra_urls", "/blog/ghost-post/"),
                                              "dist_feed_post_without_evidence"),
    "RSS links a page at another route": (_post_page_outside_posts("extra_links", "/blog/ghost-post/"),
                                          "dist_feed_post_without_evidence"),
    "sitemap lists a top-level page": (_post_page_outside_posts("extra_urls", "/ghost-post/"),
                                       "dist_feed_post_without_evidence"),
    **{f"output holds {name}": (_with_file(path), "dist_private_file") for name, path in PRIVATE_OUTPUTS.items()},
    "symlinked folder in the output": (_symlinked_output_folder, "symlink_refused"),
    "symlinked file in the output": (_symlinked_output_file, "symlink_refused"),
    "FIFO in the output": (_fifo_in_output, "not_a_regular_file"),
    "oversize page": (_oversize_page, "file_too_large"),
    "oversize sitemap": (_oversize_sitemap, "file_too_large"),
    "too many files": (_too_many_files, "too_many_entries"),
    "XML with a DOCTYPE": (_xml_with('<!DOCTYPE rss [<!ENTITY e "x">]>'), "dist_xml_doctype"),
    "XML with a lowercase doctype": (_xml_with("<!doctype rss>"), "dist_xml_doctype"),
    "UTF-16 XML with a DOCTYPE": (_xml_encoded("utf-16"), "dist_xml_encoding"),
    "UTF-16 XML without a byte order mark": (_xml_encoded("utf-16-le"), "dist_xml_encoding"),
    "XML declaring another encoding": (_xml_declared("ISO-8859-1"), "dist_xml_encoding"),
    "sitemap URL on another origin": (_feed_url("extra_urls", "https://other.example.test/about/"), "dist_url_origin"),
    "sitemap URL on another scheme": (_feed_url("extra_urls", "http://blog.example.test/about/"), "dist_url_origin"),
    "RSS link on another origin": (_feed_url("extra_links", "https://other.example.test/posts/first-post/"),
                                   "dist_url_origin"),
    "relative RSS link": (_feed_url("extra_links", "/posts/first-post/"), "dist_url_origin"),
    "URL with credentials": (_feed_url("extra_urls", "https://user@blog.example.test/about/"), "dist_url_origin"),
    "URL escaping the output": (_feed_url("extra_urls", f"{SITE}/posts/../../../etc/"), "dist_url_path_invalid"),
    "encoded URL escaping the output": (_feed_url("extra_urls", f"{SITE}/%2e%2e/%2e%2e/etc/"),
                                        "dist_url_path_invalid"),
    "sub-sitemap in a folder": (_sitemap_at("/posts/sitemap-1.xml"), "dist_sitemap_not_allowed"),
    "sub-sitemap with a non-numeric name": (_sitemap_at("/sitemap-extra.xml"), "dist_sitemap_not_allowed"),
    "sitemap index pointing at the feed": (_sitemap_at("/rss.xml"), "dist_sitemap_not_allowed"),
    "nested sitemap index": (_sitemap_at("/sitemap-1.xml", nested=True), "dist_sitemap_not_allowed"),
    "unreadable feed": (_unreadable_feed, "unreadable_file"),
    **{f"{name} missing": (_without(name), "missing_file") for name in REQUIRED_OUTPUTS},
    **{f"{name} missing with no posts": (_without(name, posts=False), "missing_file") for name in REQUIRED_OUTPUTS},
}


def test_an_absolute_output_path_through_a_symlinked_folder_is_refused(blog: Blog, tmp_path: Path) -> None:
    _published(blog)
    link = tmp_path / "blog-link"
    link.symlink_to(blog.root)
    rc, payload, _ = run_dist(blog, link / "dist")
    assert rc == 1 and _codes(payload) == ["symlink_refused"], payload


@pytest.mark.parametrize("case", sorted(DIST_FAILING))
def test_each_deploy_output_failure_is_reported_and_exits_1(blog: Blog, case: str) -> None:
    setup, code = DIST_FAILING[case]
    if case == "unreadable feed" and os.geteuid() == 0:
        pytest.skip("root reads files whatever their mode")
    dist = setup(blog)
    rc, payload, output = run_dist(blog, dist.root)
    assert rc == 1 and payload["status"] == "fail"
    assert code in _codes(payload), payload
    assert PLANTED not in output and SITE not in output


def test_a_symlinked_output_folder_is_refused(blog: Blog, tmp_path: Path) -> None:
    dist = _published(blog)
    link = tmp_path / "dist-link"
    link.symlink_to(dist.root)
    rc, payload, _ = run_dist(blog, link)
    assert rc == 1 and _codes(payload) == ["symlink_refused"]


def test_a_missing_output_folder_fails(blog: Blog) -> None:
    rc, payload, _ = run_dist(blog, blog.root / "dist")
    assert rc == 1 and _codes(payload) == ["dist_missing"]


def test_a_symlink_on_a_relative_output_path_is_refused(blog: Blog, tmp_path: Path) -> None:
    dist = _published(blog)
    (blog.root / "site").symlink_to(blog.root)
    result = subprocess.run([sys.executable, str(VERIFIER), "--dist", "site/dist"], cwd=blog.root,
                            capture_output=True, text=True, env=blog.env, timeout=60)
    assert result.returncode == 1 and _codes(json.loads(result.stdout)) == ["symlink_refused"]
    assert run_dist(blog, dist.root)[0] == 0


# ------------------------------------------------------------------ strict CI mode (--ci)


@pytest.mark.parametrize("value", ["", "0", "1"])
def test_ci_mode_refuses_the_preview_switch_in_any_form(blog: Blog, value: str) -> None:
    dist = _published(blog)
    blog.env.update(CI="true", WORKERS_CI="1", BLOGKIT_INCLUDE_DRAFTS=value)
    rc, payload, _ = run_dist(blog, dist.root, "--ci")
    assert rc == 1 and _codes(payload) == ["preview_drafts_enabled"]
    assert payload["ci"] is True


def test_ci_mode_passes_a_clean_deploy_output(blog: Blog) -> None:
    dist = _published(blog)
    blog.env.update(CI="true", WORKERS_CI="1")
    rc, payload, _ = run_dist(blog, dist.root, "--ci")
    assert (rc, payload["ci"], payload["errors"]) == (0, True, [])
