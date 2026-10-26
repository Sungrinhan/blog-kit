"""Fork identity of the plugin manifest and the marketplace catalog (spec S2).

blog-kit keeps the upstream plugin name and author, and distributes through
its own marketplace (`claude-blog@blog-kit`) owned by the fork owner. The
existing catalog entries, including the optional companion, stay as they are.
LICENSE keeps the upstream copyright line and adds one fork line under it.
Fork-owned descriptions state the shipped skill directory count; upstream
document bodies keep the upstream count (FORK.md).

Stdlib + pytest only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str) -> dict:
    return json.loads((ROOT / ".claude-plugin" / name).read_text(encoding="utf-8"))


def test_plugin_keeps_upstream_name_and_author_with_fork_repository() -> None:
    plugin = _load("plugin.json")
    assert plugin["name"] == "claude-blog"
    assert plugin["author"] == {"name": "AgriciDaniel", "url": "https://github.com/AgriciDaniel"}
    assert plugin["repository"] == "https://github.com/Sungrinhan/blog-kit"
    assert plugin["homepage"] == "https://github.com/Sungrinhan/blog-kit"


def test_marketplace_is_the_fork_marketplace_with_existing_entries() -> None:
    marketplace = _load("marketplace.json")
    assert marketplace["name"] == "blog-kit"
    assert marketplace["owner"] == {"name": "Sungrinhan"}
    entries = {entry["name"]: entry for entry in marketplace["plugins"]}
    assert list(entries) == ["claude-blog", "blog-studio"]
    assert entries["claude-blog"]["source"] == "./"
    assert entries["blog-studio"]["source"] == "./plugins/blog-studio"
    assert entries["blog-studio"]["version"] == "0.2.0"


def test_license_keeps_the_upstream_copyright_line_above_the_fork_line() -> None:
    lines = (ROOT / "LICENSE").read_text(encoding="utf-8").splitlines()
    copyrights = [line for line in lines if line.startswith("Copyright")]
    assert copyrights == [
        "Copyright (c) 2025-2026 AgriciDaniel",
        "Copyright (c) 2026 Sungrinhan (blog-kit fork)",
    ]
    assert lines[0] == "MIT License"


def _skill_dirs() -> int:
    return len(list((ROOT / "skills").glob("*/SKILL.md")))


@pytest.mark.parametrize("surface", ["plugin.json", "marketplace.json", "CLAUDE.md"])
def test_fork_owned_descriptions_state_the_shipped_skill_count(surface: str) -> None:
    if surface == "plugin.json":
        text = _load("plugin.json")["description"]
    elif surface == "marketplace.json":
        entries = {entry["name"]: entry for entry in _load("marketplace.json")["plugins"]}
        text = entries["claude-blog"]["description"]
    else:
        text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    counts = re.findall(r"(\d+) skill directories", text)
    assert counts and set(counts) == {str(_skill_dirs())}
    assert "blog-doctor" in text


def test_router_names_the_fork_skill_it_does_not_route() -> None:
    text = (ROOT / "skills" / "blog" / "SKILL.md").read_text(encoding="utf-8")
    assert "31 sub-skill directories plus this orchestrator" in text
    assert "`blog-doctor`" in text
