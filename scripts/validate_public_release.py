#!/usr/bin/env python3
"""Validate a prepared public claude-blog release worktree.

This read-only check prevents private raw URLs, private marketplace slugs, and
membership-only install instructions from leaking into the public mirror.
Run it against the prepared public worktree, never as a validator for the
private source tree.

Fork (blog-kit): distribution values (repository, owner, marketplace, version)
are the fork's, while the plugin author stays upstream. The standalone
installers are unsupported in the fork and keep upstream values, so their raw
URLs, embedded versions, owner strings, and ref guidance are not required here.
Anti-leak checks (private URLs, third-party raw URLs, the v2.2.0 pin) still
cover every surface. See FORK.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Upstream values the fork keeps: the plugin author and the standalone installers.
UPSTREAM_AUTHOR = "AgriciDaniel"
UPSTREAM_AUTHOR_URL = "https://github.com/AgriciDaniel"
UPSTREAM_VERSION = "2.3.1"
UPSTREAM_RAW_INSTALL_SH = (
    "https://raw.githubusercontent.com/AgriciDaniel/claude-blog/main/install.sh"
)
UPSTREAM_RAW_INSTALL_PS1 = (
    "https://raw.githubusercontent.com/AgriciDaniel/claude-blog/main/install.ps1"
)
UPSTREAM_PINNED_INSTALL_SH = (
    f"https://raw.githubusercontent.com/AgriciDaniel/claude-blog/"
    f"v{UPSTREAM_VERSION}/install.sh"
)
# Fork distribution values.
PUBLIC_REPOSITORY = "https://github.com/Sungrinhan/blog-kit"
PUBLIC_VERSION = "2.3.1-kit.2"
PUBLIC_PLUGIN_IDENTIFIER = "claude-blog"
PUBLIC_OWNER = "Sungrinhan"
PUBLIC_MARKETPLACE = "blog-kit"
PUBLIC_SLUG = f"{PUBLIC_PLUGIN_IDENTIFIER}@{PUBLIC_MARKETPLACE}"
PUBLIC_DISCUSSIONS = f"{PUBLIC_REPOSITORY}/discussions"
PUBLIC_SECURITY_ADVISORY = f"{PUBLIC_REPOSITORY}/security/advisories/new"
PUBLIC_DOCUMENTATION = f"{PUBLIC_REPOSITORY}/tree/main/docs"
PREOWNERSHIP_INSTALLER_PIN_RE = re.compile(
    r"(?:git checkout\s+|CLAUDE_BLOG_REF\s*=\s*[\"']?|/claude-blog/)v2\.2\.0(?:/install\.(?:sh|ps1))?\b"
)
SURFACES = (
    "README.md",
    "CLAUDE.md",
    "CITATION.cff",
    "pyproject.toml",
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/SECURITY.md",
    "install.sh",
    "install.ps1",
    "uninstall.sh",
    "uninstall.ps1",
    "skills/blog/SKILL.md",
)
FORBIDDEN_PATTERNS = {
    "private_raw_url": re.compile(
        r"raw\.githubusercontent\.com/AI-Marketing-Hub/claude-blog",
        re.IGNORECASE,
    ),
    "private_repository_url": re.compile(
        r"github\.com/AI-Marketing-Hub/claude-blog",
        re.IGNORECASE,
    ),
    "private_marketplace_slug": re.compile(
        r"claude-blog@ai-marketing-hub-claude-blog",
        re.IGNORECASE,
    ),
    "obsolete_public_slug": re.compile(
        r"claude-blog@agricidaniel-claude-blog",
        re.IGNORECASE,
    ),
    "private_auth_instruction": re.compile(
        r"(?:authenticated\s+`?gh auth login`?|GitHub PAT).{0,100}"
        r"(?:AI-Marketing-Hub|private|org)",
        re.IGNORECASE | re.DOTALL,
    ),
    "private_membership_instruction": re.compile(
        r"(?:requires?\s+(?:an?\s+)?(?:Pro\s+)?membership|"
        r"account\s+is\s+not\s+in\s+the\s+org)",
        re.IGNORECASE,
    ),
}
RAW_INSTALLER_RE = re.compile(
    r"https://raw\.githubusercontent\.com/[^/\s]+/[^/\s]+/"
    r"[^/\s]+/install\.(?:sh|ps1)",
    re.IGNORECASE,
)
FORK_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)-kit\.(\d+)")


def python_version(plugin_version: str) -> str:
    """Return the Python package form of a plugin version.

    Fork releases are 2.3.1-kit.N on plugin surfaces and 2.3.1+kit.N in
    Python metadata (a PEP 440 local version). Any other version, including
    upstream versions without the suffix, is the same string on both.
    """
    match = FORK_VERSION_RE.fullmatch(plugin_version)
    if match is None:
        return plugin_version
    return f"{match.group(1)}+kit.{match.group(2)}"


def validate(root: Path) -> dict:
    root = root.resolve()
    errors: list[dict] = []
    contents: dict[str, str] = {}
    for relative in SURFACES:
        path = root / relative
        if not path.is_file():
            errors.append({"kind": "missing_release_surface", "file": relative})
            continue
        contents[relative] = path.read_text(encoding="utf-8")

    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for path in docs_dir.rglob("*.md"):
            relative = path.relative_to(root).as_posix()
            contents.setdefault(relative, path.read_text(encoding="utf-8"))
            if path.name.startswith("PUBLIC-BACKLOG-TRIAGE-"):
                errors.append({"kind": "private_triage_note", "file": relative})

    # Inspect maintained execution and instruction surfaces as well as the
    # canonical release metadata. Historical raw captures and test fixtures
    # are evidence, and are intentionally outside this distribution check.
    for directory, patterns in {
        "skills": ("*.md", "*.py", "*.json", "*.txt"),
        "agents": ("*.md",),
        "scripts": ("*.py", "*.ps1"),
        "data": ("*.json",),
        ".github/workflows": ("*.yml", "*.yaml"),
    }.items():
        for pattern in patterns:
            for path in (root / directory).rglob(pattern):
                if path.is_symlink() or any(part in {".venv", "__pycache__"} for part in path.parts):
                    continue
                relative = path.relative_to(root).as_posix()
                contents.setdefault(relative, path.read_text(encoding="utf-8"))

    for relative, text in contents.items():
        if relative == "scripts/validate_public_release.py":
            # The detector's own forbidden-pattern definitions contain the
            # strings they must reject elsewhere. Keep scanning its runtime
            # code, excluding only this declarative pattern table.
            text = re.sub(
                r"(?ms)^FORBIDDEN_PATTERNS = \{.*?(?=^RAW_INSTALLER_RE =)",
                "", text,
            )
        for kind, pattern in FORBIDDEN_PATTERNS.items():
            if kind == "private_repository_url" and relative.startswith("docs/"):
                # Attribution and migration history may legitimately mention
                # the private repository. Canonical distribution surfaces may
                # not identify it as the public package.
                continue
            if pattern.search(text):
                errors.append({"kind": kind, "file": relative})

    readme = contents.get("README.md", "")
    if PUBLIC_SLUG not in readme:
        errors.append(
            {
                "kind": "missing_public_marketplace_slug",
                "file": "README.md",
                "expected": PUBLIC_SLUG,
            }
        )
    if PUBLIC_REPOSITORY not in readme:
        errors.append(
            {
                "kind": "missing_public_repository_url",
                "file": "README.md",
                "expected": PUBLIC_REPOSITORY,
            }
        )
    version_fragments = {
        "CITATION.cff": (f"version: {PUBLIC_VERSION}",),
        "pyproject.toml": (f'version = "{python_version(PUBLIC_VERSION)}"',),
        ".claude-plugin/plugin.json": (f'"version": "{PUBLIC_VERSION}"',),
        "skills/blog/SKILL.md": (f'version: "{PUBLIC_VERSION}"',),
    }
    for relative, expected_fragments in version_fragments.items():
        text = contents.get(relative, "")
        for expected in expected_fragments:
            if expected not in text:
                errors.append(
                    {
                        "kind": "invalid_public_release_version",
                        "file": relative,
                        "expected": expected,
                    }
                )

    # v2.2.0 predates the ownership-aware installer, and its uninstall.sh runs
    # an unguarded recursive delete. Install documentation must not pin to it.
    for relative in ("README.md", "CLAUDE.md", "docs/INSTALLATION.md", ".github/SECURITY.md"):
        for match in PREOWNERSHIP_INSTALLER_PIN_RE.finditer(contents.get(relative, "")):
            errors.append(
                {
                    "kind": "unsafe_preownership_installer_pin",
                    "file": relative,
                    "actual": match.group(0),
                }
            )

    citation = contents.get("CITATION.cff", "")
    # SECURITY.md must route private reports to the fork and must never tell
    # users to run the v2.2.0 installer (checked above).
    security = contents.get(".github/SECURITY.md", "")
    if PUBLIC_SECURITY_ADVISORY not in security:
        errors.append(
            {
                "kind": "invalid_public_security_routing",
                "file": ".github/SECURITY.md",
                "expected": PUBLIC_SECURITY_ADVISORY,
            }
        )

    expected_citation_repository = f'repository-code: "{PUBLIC_REPOSITORY}"'
    if expected_citation_repository not in citation:
        errors.append(
            {
                "kind": "invalid_public_citation_repository",
                "file": "CITATION.cff",
                "expected": expected_citation_repository,
            }
        )

    issue_config = contents.get(".github/ISSUE_TEMPLATE/config.yml", "")
    for expected in (
        PUBLIC_DISCUSSIONS,
        PUBLIC_SECURITY_ADVISORY,
        PUBLIC_DOCUMENTATION,
    ):
        if expected not in issue_config:
            errors.append(
                {
                    "kind": "invalid_public_issue_routing",
                    "file": ".github/ISSUE_TEMPLATE/config.yml",
                    "expected": expected,
                }
            )

    for relative, text in contents.items():
        for found in RAW_INSTALLER_RE.findall(text):
            if found not in {
                UPSTREAM_RAW_INSTALL_SH,
                UPSTREAM_RAW_INSTALL_PS1,
                UPSTREAM_PINNED_INSTALL_SH,
            }:
                errors.append(
                    {
                        "kind": "wrong_public_raw_installer_url",
                        "file": relative,
                        "actual": found,
                    }
                )

    pyproject = contents.get("pyproject.toml", "")
    canonical_fields = ("homepage", "repository", "issues", "documentation", "changelog")
    for field in canonical_fields:
        pattern = re.compile(
            rf"^{field}\s*=\s*[\"']{re.escape(PUBLIC_REPOSITORY)}(?:[^\"']*)[\"']",
            re.MULTILINE,
        )
        if not pattern.search(pyproject):
            errors.append(
                {
                    "kind": "invalid_public_canonical",
                    "file": "pyproject.toml",
                    "field": field,
                }
            )

    plugin_text = contents.get(".claude-plugin/plugin.json", "")
    try:
        plugin = json.loads(plugin_text)
    except json.JSONDecodeError:
        errors.append(
            {"kind": "invalid_json", "file": ".claude-plugin/plugin.json"}
        )
    else:
        expected_plugin = {
            "name": PUBLIC_PLUGIN_IDENTIFIER,
            "homepage": PUBLIC_REPOSITORY,
            "repository": PUBLIC_REPOSITORY,
        }
        for field, expected in expected_plugin.items():
            if plugin.get(field) != expected:
                errors.append(
                    {
                        "kind": "invalid_public_plugin_ownership",
                        "file": ".claude-plugin/plugin.json",
                        "field": field,
                        "expected": expected,
                    }
                )
        author = plugin.get("author")
        if not isinstance(author, dict) or author.get("name") != UPSTREAM_AUTHOR:
            errors.append(
                {
                    "kind": "invalid_public_plugin_ownership",
                    "file": ".claude-plugin/plugin.json",
                    "field": "author.name",
                    "expected": UPSTREAM_AUTHOR,
                }
            )
        if not isinstance(author, dict) or author.get("url") != UPSTREAM_AUTHOR_URL:
            errors.append(
                {
                    "kind": "invalid_public_plugin_ownership",
                    "file": ".claude-plugin/plugin.json",
                    "field": "author.url",
                    "expected": UPSTREAM_AUTHOR_URL,
                }
            )

    marketplace_text = contents.get(".claude-plugin/marketplace.json", "")
    try:
        marketplace = json.loads(marketplace_text)
    except json.JSONDecodeError:
        errors.append(
            {"kind": "invalid_json", "file": ".claude-plugin/marketplace.json"}
        )
    else:
        owner = marketplace.get("owner")
        plugins = marketplace.get("plugins")
        if marketplace.get("name") != PUBLIC_MARKETPLACE:
            errors.append(
                {
                    "kind": "invalid_public_marketplace_ownership",
                    "file": ".claude-plugin/marketplace.json",
                    "field": "name",
                    "expected": PUBLIC_MARKETPLACE,
                }
            )
        if not isinstance(owner, dict) or owner.get("name") != PUBLIC_OWNER:
            errors.append(
                {
                    "kind": "invalid_public_marketplace_ownership",
                    "file": ".claude-plugin/marketplace.json",
                    "field": "owner.name",
                    "expected": PUBLIC_OWNER,
                }
            )
        if (
            not isinstance(plugins, list)
            or not any(
                isinstance(entry, dict)
                and entry.get("name") == PUBLIC_PLUGIN_IDENTIFIER
                and entry.get("source") == "./"
                for entry in plugins
            )
        ):
            errors.append(
                {
                    "kind": "invalid_public_marketplace_plugin",
                    "file": ".claude-plugin/marketplace.json",
                }
            )

    return {
        "status": "pass" if not errors else "fail",
        "root": str(root),
        "errors": errors,
        "summary": {"errors": len(errors), "surfaces_checked": len(contents)},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a prepared public claude-blog release worktree."
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--compact", action="store_true", help="Emit compact JSON.")
    args = parser.parse_args(argv)
    report = validate(args.root)
    print(json.dumps(report, indent=None if args.compact else 2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
