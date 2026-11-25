"""Shared helpers for the fork's delivery-gate regression tests (WR tickets).

`golden_draft` copies tests/fixtures/golden_draft (a draft that passes the
strict gates), appends frontmatter lines and markdown, renders it through the
real renderer CLI (no PDF engine) and adds a minimal PDF, so a test can run Gate 3 or Gate 5 on
it. `offline_probes` is a fixture: import it into a test module to give every
test no network and a private review-state directory (an import, not
tests/conftest.py, because that file is upstream and the fork does not edit
it; importing modules mark it `# noqa: F401`). `require_browser`
skips when Patchright or its Chromium is missing (the ubuntu CI test job
installs both).
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "golden_draft"
SLUG = "core-web-vitals-thresholds"
MINIMAL_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


def load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PREFLIGHT = load_script("wr_gate_preflight", "blog_preflight.py")
RENDER_CLI = ROOT / "scripts" / "blog_render.py"


@pytest.fixture(autouse=True)
def offline_probes(monkeypatch, tmp_path):
    """No real network and a private review-state directory for every test."""
    monkeypatch.setenv("CLAUDE_BLOG_REVIEW_STATE_DIR", str(tmp_path / "review-state"))
    monkeypatch.setattr(PREFLIGHT, "_safe_http_url", lambda _url: (True, None))
    monkeypatch.setattr(PREFLIGHT, "_http_head", lambda _url: 200)


def require_browser() -> None:
    browser_api = pytest.importorskip("patchright.sync_api")
    with browser_api.sync_playwright() as runtime:
        if not Path(runtime.chromium.executable_path).is_file():
            pytest.skip("requires matching installed Chromium; no browser download in unit tests")


def golden_draft(
    tmp_path: Path, frontmatter: str = "", markdown: str = "", fixture: Path = FIXTURE, slug: str = SLUG,
) -> Path:
    """Render a fixture draft (the golden draft by default) with extra
    frontmatter lines and markdown. A fixture without its own hero gets the
    golden draft's hero.webp."""
    draft = tmp_path / "draft"
    shutil.copytree(fixture, draft)
    if not (draft / "hero.webp").exists():
        shutil.copy2(FIXTURE / "hero.webp", draft / "hero.webp")
    source = draft / f"{slug}.md"
    text = source.read_bytes().decode("utf-8")
    if frontmatter:
        head, sep, rest = text.partition("\n---\n")
        text = head + "\n" + frontmatter.rstrip("\n") + sep + rest
    if markdown:
        text += "\n" + markdown + "\n"
    source.write_bytes(text.encode("utf-8"))
    result = subprocess.run(
        [sys.executable, str(RENDER_CLI), "--md", str(source), "--out-dir", str(draft), "--pdf-engine", "none"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    (draft / f"{slug}.pdf").write_bytes(MINIMAL_PDF)
    return draft


def page_viewports(result: dict) -> dict:
    """Gate 3's per-viewport page results; the desktop-dark entry holds only
    background colors and has no `result`."""
    return {name: entry["result"] for name, entry in result["per_viewport"].items() if "result" in entry}
