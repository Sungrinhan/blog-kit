"""S4: one post through the engine CLI and a real Astro build of the blog site.

The site repo's committed tree (its HEAD) is copied into the S1 harness's temp
blog repo, with the site's node_modules linked in. A fixture post with a local
PNG, an SVG asset, a link to another page and an anchor in the same post goes
through convert, check (real `astro check` and real draft preview build),
approve and publish, with the review record written through record-review and
the learning record through record-learning. Then the real `npm run build` (prebuild runs
the site's verifier copy) and the copy's `--ci --dist dist` must pass, the
built page's images, links and anchor must resolve inside dist, and removing
the evidence must fail both.

Local only: BLOGKIT_SITE_REPO names the site repo (a git work tree with
node_modules installed). Without it, or without node and npx, the test is
skipped; the CI pytest job skips it.

    BLOGKIT_SITE_REPO=<site repo> python -m pytest tests/test_blogkit_astro_e2e.py
"""

from __future__ import annotations

import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import zlib
from html.parser import HTMLParser
from pathlib import Path

import pytest

from test_blogkit import EVIDENCE, REAL_GIT, SLUG, Engine, _commit_assets, _git, _learning, _no_leak, _review, _terms_file

SITE = os.environ.get("BLOGKIT_SITE_REPO", "")
BUILD_TIMEOUT = 900

pytestmark = pytest.mark.skipif(
    not SITE or not (Path(SITE) / "node_modules").is_dir() or not shutil.which("node") or not shutil.which("npx"),
    reason="S4 needs BLOGKIT_SITE_REPO (a site repo with node_modules), node and npx",
)


def _png() -> bytes:
    """A valid 2x2 RGB PNG, so the real image pipeline can read it."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\x00" + b"\x33\x66\x99" * 2 for _ in range(2))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16"/></svg>\n'
DRAFT = (
    "---\n"
    'title: "가짜 종단 글"\n'
    'description: "실제 Astro 빌드로 확인하는 가짜 글"\n'
    'date: "2026-01-02"\n'
    'tags: ["fake"]\n'
    "---\n"
    "\n"
    "## Section One\n"
    "\n"
    "![A tiny diagram](images/diagram.png)\n"
    "\n"
    "The [about page](/about/) and [this section](#section-one).\n"
    "\n"
    "![Logo](logo.svg)\n"
)


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[str] = []
        self.links: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "img" and values.get("src"):
            self.images.append(values["src"])
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])


def _site(engine: Engine) -> None:
    """The site's committed tree as the blog repo's first commit, node_modules linked."""
    archive = subprocess.run([REAL_GIT, "-C", SITE, "archive", "--format=tar", "HEAD"],
                             capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(engine.blog, filter="data")
    assert engine.copy.read_bytes() == engine.canonical.read_bytes(), "the site's verifier copy is not this engine's"
    (engine.blog / "node_modules").symlink_to(Path(SITE).resolve() / "node_modules")
    (engine.blog / ".git" / "info" / "exclude").write_text("node_modules\n.claude/\n")
    _git(engine, "add", "--", ".")
    _git(engine, "-c", "user.email=tester@example.invalid", "-c", "user.name=Tester", "commit", "-q", "-m", "site")


def _draft(engine: Engine) -> None:
    folder = engine.blog / "inbox" / SLUG / "draft"
    (folder / "images").mkdir(parents=True)
    (folder / "images" / "diagram.png").write_bytes(_png())
    (folder / "logo.svg").write_bytes(SVG)
    (folder / f"{SLUG}.md").write_text(DRAFT, encoding="utf-8")


def _site_run(engine: Engine, *command: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "BLOGKIT_INCLUDE_DRAFTS"}
    return subprocess.run(list(command), cwd=engine.blog, capture_output=True, text=True, env=env,
                          timeout=BUILD_TIMEOUT)


def _verify_dist(engine: Engine) -> subprocess.CompletedProcess:
    return _site_run(engine, sys.executable, "scripts/blogkit_verify.py", "--ci", "--dist", "dist")


def _resolves(dist: Path, url: str) -> bool:
    path = url.split("#")[0].split("?")[0]
    target = dist / path.lstrip("/")
    return target.is_file() or (target / "index.html").is_file()


def test_a_published_post_passes_the_real_build_and_deploy_check(tmp_path: Path) -> None:
    engine = Engine(tmp_path)
    engine.tools = []  # the real node and npx
    engine.timeout = BUILD_TIMEOUT
    _site(engine)
    _draft(engine)
    _terms_file(engine)
    outputs = []
    rc, _, output = engine.run("convert", SLUG)
    outputs.append(output)
    assert rc == 0, output
    _review(engine)
    rc, payload, output = engine.run("check", SLUG)
    outputs.append(output)
    assert rc == 0, output
    _learning(engine)
    rc, status, output = engine.run("status", SLUG)
    outputs.append(output)
    rc, _, output = engine.run("approve", SLUG, "--hash", status["content_hash"], "--warnings", status["warnings_digest"])
    outputs.append(output)
    assert rc == 0, output
    _commit_assets(engine)
    rc, payload, output = engine.run("publish", SLUG)
    outputs.append(output)
    assert (rc, payload["errors"]) == (0, []), output
    for output in outputs:
        _no_leak(output)
    assert not (engine.bin / "npx").exists()
    assert "/_astro/" in (engine.blog / ".blogkit-preview" / "posts" / SLUG / "index.html").read_text(encoding="utf-8")

    build = _site_run(engine, "npm", "run", "build")
    assert build.returncode == 0, build.stdout[-2000:] + build.stderr[-2000:]
    check = _verify_dist(engine)
    report = json.loads(check.stdout)
    assert (check.returncode, report["status"], report["public"], report["errors"]) == (0, "ok", [SLUG], []), check.stdout

    dist = engine.blog / "dist"
    page = _Page()
    page.feed((dist / "posts" / SLUG / "index.html").read_text(encoding="utf-8"))
    local = [src for src in page.images if src.startswith("/")]
    # Astro renames assets and may re-encode the PNG (webp), so match by name.
    assert any(src.startswith("/_astro/diagram.") for src in local), page.images
    assert any(src.startswith("/_astro/logo.") for src in local), page.images
    assert all(_resolves(dist, src) for src in local), page.images
    assert "/about/" in page.links and _resolves(dist, "/about/")
    assert "#section-one" in page.links and "section-one" in page.ids

    (engine.blog / EVIDENCE).unlink()
    check = _verify_dist(engine)
    assert check.returncode == 1 and json.loads(check.stdout)["status"] == "fail", check.stdout
    build = _site_run(engine, "npm", "run", "build")
    assert build.returncode != 0
