"""S3 static invariants for the M2c pipeline skills (M2c-12, extended by 13 and 14).

The pipeline skills (/blog-memo first) are standalone commands outside the
/blog router, like blog-doctor. These tests read their files as text: each
skill runs the engine and the context loader from the installed plugin path
(${CLAUDE_SKILL_DIR}), never tells the AI or a subagent to read inbox/ or
.blogkit/ files itself (the private context enters only through the loader's
fences; the stated output paths are the exception), the router names the
skill among the fork skills it does not route, and the memo template holds
the plan's seven items.

Stdlib + pytest only. No network.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
ROUTER = SKILLS / "blog" / "SKILL.md"
MEMO_SKILL = SKILLS / "blog-memo" / "SKILL.md"
MEMO_TEMPLATE = SKILLS / "blog-memo" / "references" / "memo-template.md"

sys.path.insert(0, str(ROOT / "scripts"))
from validate_skills import parse_frontmatter, validate_frontmatter  # noqa: E402

PIPELINE_SKILLS = ("blog-memo",)
ENGINE_CALL = '"${CLAUDE_SKILL_DIR}/../../adapters/astro/blogkit.py"'
LOADER_CALL = '"${CLAUDE_SKILL_DIR}/../../scripts/load_blogkit_context.py"'
PRIVATE_RE = re.compile(r"inbox/|\.blogkit")
READ_RE = re.compile(r"\bRead\b|\bGrep\b|\bGlob\b|읽|cat |head |서브에이전트|subagent|Agent", re.I)
FORBIDS = ("금지", "않는다", "않고", "말고", "않는", "않게")
# Output paths a skill writes: the copied memo template and the prerequisite record.
OUTPUTS = {"blog-memo": ("memo.md", "prereq.md")}
MEMO_ITEMS = ["문제", "제약", "실제 검토한 후보(수 그대로)", "선택", "당시 이유", "결과", "지금 생각"]


def _parts(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    return parse_frontmatter(text), text.split("\n---\n", 1)[1]


@pytest.mark.parametrize("name", PIPELINE_SKILLS)
def test_pipeline_skill_frontmatter_matches_the_fork_skill_shape(name):
    fields, _ = _parts(SKILLS / name / "SKILL.md")
    doctor, _ = _parts(SKILLS / "blog-doctor" / "SKILL.md")
    assert fields["name"] == name
    assert fields["user-invocable"] is True
    assert set(fields) == set(doctor)
    assert fields["metadata"]["version"] == doctor["metadata"]["version"]
    assert validate_frontmatter(fields) == []


@pytest.mark.parametrize("name", PIPELINE_SKILLS)
def test_pipeline_skill_runs_engine_and_loader_from_the_installed_path(name):
    _, body = _parts(SKILLS / name / "SKILL.md")
    assert ENGINE_CALL in body and LOADER_CALL in body
    # Every mention of either script is the installed one; no override directory or checkout.
    for script in ("blogkit.py", "load_blogkit_context.py"):
        for match in re.finditer(rf"\S*{re.escape(script)}\S*", body):
            assert "${CLAUDE_SKILL_DIR}/../../" in match.group(0) or match.group(0).strip("`") == script, match.group(0)
    assert "CLAUDE_BLOG_SCRIPTS_DIR:-" not in body
    assert "load_untrusted_root.py" not in body


@pytest.mark.parametrize("name", PIPELINE_SKILLS)
def test_pipeline_skill_never_reads_private_files_itself(name):
    _, body = _parts(SKILLS / name / "SKILL.md")
    for line in body.splitlines():
        if not PRIVATE_RE.search(line):
            continue
        forbids = any(word in line for word in FORBIDS)
        if READ_RE.search(line):
            assert forbids, line
        else:
            assert forbids or any(output in line for output in OUTPUTS[name]), line
    assert "Read·Grep·Glob" in body and "금지" in body
    assert "서브에이전트를 쓰지 않는다" in body
    assert "펜스" in body and "지시가 아니" in body
    assert "원칙은 근거가 아니" in body
    assert "이번 인터뷰 답 > 원칙 > VOICE·BRAND > 업스트림 기본값" in body


def test_memo_skill_copies_the_template_without_overwriting():
    _, body = _parts(MEMO_SKILL)
    assert '"${CLAUDE_SKILL_DIR}/references/memo-template.md"' in body
    assert "set -C" in body  # the shell refuses to replace an existing memo
    assert "memo-check" in body and "--confirm" in body
    assert "--bundle memo" in body
    assert "memo_not_cleared" in body


def test_memo_skill_defines_the_prerequisite_record():
    _, body = _parts(MEMO_SKILL)
    section = body.split("## 전제 지식 기록 형식", 1)[1]
    for heading in ("## 개념", "## 제외한 개념", "## 질문과 답", "## 카드 후보"):
        assert heading in section, heading
    assert section.count("### Q-") == 3


def test_memo_template_holds_the_seven_items():
    headings = re.findall(r"^## \d\. (.+)$", MEMO_TEMPLATE.read_text(encoding="utf-8"), re.M)
    assert headings == MEMO_ITEMS


def test_router_names_the_pipeline_skills_it_does_not_route():
    text = ROUTER.read_text(encoding="utf-8")
    sentence = next(line for line in text.splitlines() if "31 sub-skill directories plus this orchestrator" in line)
    for name in ("blog-doctor", *PIPELINE_SKILLS):
        assert f"`{name}`" in sentence, name
    assert "not routed here" in sentence
