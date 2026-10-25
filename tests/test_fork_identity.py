"""Fork identity of the plugin manifest and the marketplace catalog (spec S2).

blog-kit keeps the upstream plugin name and author, and distributes through
its own marketplace (`claude-blog@blog-kit`) owned by the fork owner. The
existing catalog entries, including the optional companion, stay as they are.

Stdlib + pytest only.
"""

from __future__ import annotations

import json
from pathlib import Path

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
