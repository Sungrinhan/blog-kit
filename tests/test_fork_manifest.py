"""S4: the fork document lists exactly what this fork changed upstream.

FORK.md records the upstream base commit and three lists (additions, fork
patches, work files). Every path that differs from the base in the working
tree (committed, staged, unstaged, or untracked but not ignored) must appear
in exactly one list, and nothing else may. Upstream files must never be
deleted or renamed. A clone without the base commit skips the check.

Stdlib + pytest only. No network: fixtures are local temp git repos.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FORK_DOC = "FORK.md"
SECTIONS = ("덧붙인 기능", "포크 수정", "작업 파일")
BASE_RE = re.compile(r"업스트림 기준 커밋: `([0-9a-f]{40})`")
ENTRY_RE = re.compile(r"^- `([^`]+)`")


class BaseUnavailable(Exception):
    """The upstream base commit is not in this clone (for example, a shallow clone)."""


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=True,
    ).stdout


def _fork_doc(root: Path) -> tuple[str, dict[str, list[str]]]:
    text = (root / FORK_DOC).read_text(encoding="utf-8")
    match = BASE_RE.search(text)
    assert match, f"{FORK_DOC} has no upstream base commit line"
    lists: dict[str, list[str]] = {name: [] for name in SECTIONS}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            current = heading if heading in lists else None
            continue
        entry = ENTRY_RE.match(line)
        if current and entry:
            lists[current].append(entry.group(1))
    return match.group(1), lists


def _changes(root: Path, base: str) -> tuple[set[str], dict[str, str]]:
    """Return (paths changed since base, upstream path -> deletion or rename problem)."""
    fields = _git(root, "diff", "--name-status", "-M", "-z", base).split("\0")
    changed: set[str] = set()
    removed: dict[str, str] = {}
    i = 0
    while i < len(fields) - 1:
        status, path = fields[i], fields[i + 1]
        if status.startswith("R"):
            removed[path] = f"upstream file renamed: {path} -> {fields[i + 2]}"
            i += 3
            continue
        if status == "D":
            removed[path] = f"upstream file deleted: {path}"
        else:
            changed.add(path)
        i += 2
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    changed.update(p for p in untracked.split("\0") if p)
    return changed, removed


def fork_manifest_problems(root: Path) -> list[str]:
    base, lists = _fork_doc(root)
    probe = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{base}^{{commit}}"],
        capture_output=True, check=False,
    )
    if probe.returncode != 0:
        raise BaseUnavailable(base)
    listed = [path for paths in lists.values() for path in paths]
    problems = [f"listed twice: {p}" for p in sorted({p for p in listed if listed.count(p) > 1})]
    changed, removed = _changes(root, base)
    problems += [removed[p] for p in sorted(removed)]
    problems += [f"changed but not listed: {p}" for p in sorted(changed - set(listed))]
    problems += [f"listed but unchanged: {p}" for p in sorted(set(listed) - changed - set(removed))]
    return problems


def test_fork_document_matches_this_checkout() -> None:
    try:
        problems = fork_manifest_problems(ROOT)
    except BaseUnavailable as exc:
        pytest.skip(f"upstream base commit {exc} is not in this clone")
    assert problems == []


def _fork_doc_text(base: str, added=(), patched=(), work=()) -> str:
    def block(paths) -> str:
        return "".join(f"- `{p}`: reason\n" for p in paths)
    return (
        f"# fork\n\n업스트림 기준 커밋: `{base}`\n\n"
        f"## 덧붙인 기능\n\n{block(('FORK.md', *added))}\n"
        f"## 포크 수정\n\n{block(patched)}\n"
        f"## 작업 파일\n\n{block(work)}"
    )


@pytest.fixture
def upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """A temp repo whose first commit plays the upstream base."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for key in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{key}_NAME", "t")
        monkeypatch.setenv(f"GIT_{key}_EMAIL", "t@example.invalid")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "keep.txt").write_text("keep\n")
    (repo / "upstream.txt").write_text("upstream\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "upstream")
    return repo, _git(repo, "rev-parse", "HEAD").strip()


def test_deleting_an_upstream_file_fails_even_when_listed(upstream) -> None:
    repo, base = upstream
    (repo / "upstream.txt").unlink()
    (repo / FORK_DOC).write_text(_fork_doc_text(base, patched=["upstream.txt"]))
    assert fork_manifest_problems(repo) == ["upstream file deleted: upstream.txt"]


def test_renaming_an_upstream_file_fails_even_when_listed(upstream) -> None:
    repo, base = upstream
    _git(repo, "mv", "upstream.txt", "moved.txt")
    (repo / FORK_DOC).write_text(_fork_doc_text(base, patched=["upstream.txt"]))
    assert fork_manifest_problems(repo) == ["upstream file renamed: upstream.txt -> moved.txt"]


def test_unlisted_new_file_and_unlisted_upstream_edit_fail(upstream) -> None:
    repo, base = upstream
    (repo / FORK_DOC).write_text(_fork_doc_text(base))
    assert fork_manifest_problems(repo) == []
    (repo / "new.txt").write_text("new\n")
    (repo / "upstream.txt").write_text("patched\n")
    assert fork_manifest_problems(repo) == [
        "changed but not listed: new.txt",
        "changed but not listed: upstream.txt",
    ]


def test_staged_and_committed_changes_count_and_ignored_files_do_not(upstream) -> None:
    repo, base = upstream
    (repo / ".gitignore").write_text("/local-only.md\n")
    (repo / "local-only.md").write_text("ignored\n")
    (repo / "committed.txt").write_text("c\n")
    _git(repo, "add", ".gitignore", "committed.txt")
    _git(repo, "commit", "-q", "-m", "fork")
    (repo / "staged.txt").write_text("s\n")
    _git(repo, "add", "staged.txt")
    (repo / FORK_DOC).write_text(
        _fork_doc_text(base, added=["committed.txt", "staged.txt"], work=[".gitignore"])
    )
    assert fork_manifest_problems(repo) == []


def test_listing_a_future_file_fails(upstream) -> None:
    repo, base = upstream
    (repo / FORK_DOC).write_text(_fork_doc_text(base, added=["not-yet.py"]))
    assert fork_manifest_problems(repo) == ["listed but unchanged: not-yet.py"]


def test_clone_without_the_base_commit_is_reported_unavailable(upstream, tmp_path) -> None:
    repo, base = upstream
    (repo / FORK_DOC).write_text(_fork_doc_text(base))
    _git(repo, "add", FORK_DOC)
    _git(repo, "commit", "-q", "-m", "fork doc")
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{repo}", str(shallow)],
        check=True, capture_output=True,
    )
    with pytest.raises(BaseUnavailable):
        fork_manifest_problems(shallow)


def test_codex_local_copies_are_ignored() -> None:
    ignored = _git(ROOT, "check-ignore", "--no-index", "AGENTS.md", ".agents/skills/flow/SKILL.md")
    assert ignored.split() == ["AGENTS.md", ".agents/skills/flow/SKILL.md"]
