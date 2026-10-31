"""S3 static invariants for the M2c pipeline skills (M2c-12, 13 and 14).

The pipeline skills (/blog-memo, /blog-interview, /blog-learn) are standalone commands
outside the /blog router, like blog-doctor. These tests read their files as
text: each skill runs the engine and the context loader from the installed
plugin path (${CLAUDE_SKILL_DIR}), never tells the AI or a subagent to read
inbox/ or .blogkit/ files itself (the private context enters only through the
loader's fences; the stated output paths are the exception), the router names
the skill among the fork skills it does not route, the memo template holds
the plan's seven items, the decision-record template keeps the upstream
template headings and the plan's nine items, and /blog-interview stops at the
third P0 failure and never calls the upstream delivery contract. /blog-learn
asks at least two questions (a concept and a counterargument one), names the
four verdicts, writes proposals in the engine's format (its examples pass the
engine's own parse_proposal() and learning_result()), and only shows the
person the approve and accept-proposal commands, never running them.

Stdlib + pytest only. No network.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
ROUTER = SKILLS / "blog" / "SKILL.md"
MEMO_SKILL = SKILLS / "blog-memo" / "SKILL.md"
MEMO_TEMPLATE = SKILLS / "blog-memo" / "references" / "memo-template.md"
INTERVIEW_SKILL = SKILLS / "blog-interview" / "SKILL.md"
INTERVIEW_REFS = SKILLS / "blog-interview" / "references"
DECISION_RECORD = INTERVIEW_REFS / "decision-record.md"
LEARN_SKILL = SKILLS / "blog-learn" / "SKILL.md"
LEARN_FORMATS = SKILLS / "blog-learn" / "references" / "learning-formats.md"
ENGINE = ROOT / "adapters" / "astro" / "blogkit.py"
VERIFIER = ROOT / "adapters" / "astro" / "blogkit_verify.py"

sys.path.insert(0, str(ROOT / "scripts"))
from validate_skills import parse_frontmatter, validate_frontmatter  # noqa: E402

PIPELINE_SKILLS = ("blog-memo", "blog-interview", "blog-learn")
ENGINE_CALL = '"${CLAUDE_SKILL_DIR}/../../adapters/astro/blogkit.py"'
LOADER_CALL = '"${CLAUDE_SKILL_DIR}/../../scripts/load_blogkit_context.py"'
PRIVATE_RE = re.compile(r"inbox/|\.blogkit")
READ_RE = re.compile(r"\bRead\b|\bGrep\b|\bGlob\b|읽|cat |head |서브에이전트|subagent|Agent", re.I)
FORBIDS = ("금지", "않는다", "않고", "말고", "않는", "않게")
# Output paths a skill writes: the copied memo template and the prerequisite record; the
# interview answers, the claims file, the saved review result and the draft folder; the learning
# result, the appended interview answer and the new proposal files.
OUTPUTS = {"blog-memo": ("memo.md", "prereq.md"),
           "blog-interview": ("interview.md", "claims.json", "review.json", "/draft/"),
           "blog-learn": ("learning.json", "interview.md", ".blogkit/pending")}
MEMO_ITEMS = ["문제", "제약", "실제 검토한 후보(수 그대로)", "선택", "당시 이유", "결과", "지금 생각"]
DECISION_ITEMS = ["맥락", "실제 검토 후보", "나중에 찾은 대안(별도 표시)", "기준", "선택과 당시 이유", "장단점",
                  "사후 평가(당시와 분리)", "다른 방향", "내 원칙"]
# The upstream delivery contract and the analyzer, which this path never runs.
UPSTREAM_DELIVERY = ("blog_preflight.py", "analyze_blog.py", "--init-review-nonce", "blog-delivery-contract",
                     "generate_hero.py", "blog_render.py", "quality_gate.py")


def _parts(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    return parse_frontmatter(text), text.split("\n---\n", 1)[1]


def _skill_texts(name: str) -> list[tuple[str, str]]:
    """The skill body and its own reference files other than templates, as (label, text)."""
    _, body = _parts(SKILLS / name / "SKILL.md")
    texts = [("SKILL.md", body)]
    refs = SKILLS / name / "references"
    skip = {"memo-template.md", "decision-record.md", "korean-review-rules.md"}
    for path in sorted(refs.glob("*.md")) if refs.is_dir() else ():
        if path.name not in skip:
            texts.append((path.name, path.read_text(encoding="utf-8")))
    return texts


@pytest.mark.parametrize("name", PIPELINE_SKILLS)
def test_pipeline_skill_frontmatter_matches_the_fork_skill_shape(name):
    fields, _ = _parts(SKILLS / name / "SKILL.md")
    doctor, _ = _parts(SKILLS / "blog-doctor" / "SKILL.md")
    assert fields["name"] == name
    assert fields["user-invocable"] is True
    assert set(fields) == set(doctor)
    assert fields["metadata"]["version"] == doctor["metadata"]["version"]
    assert validate_frontmatter(fields) == []


def _token_estimate(text: str) -> int:
    """A conservative token count: one per Hangul syllable, one per four other non-space characters."""
    hangul = len(re.findall(r"[\uac00-\ud7a3]", text))
    other = re.sub(r"[\uac00-\ud7a3]|\s", "", text)
    return hangul + len(other) // 4


@pytest.mark.parametrize("name", PIPELINE_SKILLS)
def test_pipeline_skill_stays_within_the_skill_size_rule(name):
    # CLAUDE.md: SKILL.md under 500 lines / 5000 tokens; new reference files under 200 lines.
    # validate_skills counts whitespace words, which undercounts Korean, so this uses the estimate above.
    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 500
    assert _token_estimate(text) <= 5000, _token_estimate(text)
    for path in sorted((SKILLS / name / "references").glob("*.md")):
        assert len(path.read_text(encoding="utf-8").splitlines()) < 200, path.name


@pytest.mark.parametrize("name", PIPELINE_SKILLS)
def test_pipeline_skill_runs_engine_and_loader_from_the_installed_path(name):
    _, body = _parts(SKILLS / name / "SKILL.md")
    assert ENGINE_CALL in body and LOADER_CALL in body
    # Every mention of either script, in the skill or its references, is the installed one;
    # no override directory or checkout.
    for label, text in _skill_texts(name):
        for script in ("blogkit.py", "load_blogkit_context.py"):
            for match in re.finditer(rf"\S*{re.escape(script)}\S*", text):
                assert ("${CLAUDE_SKILL_DIR}/../../" in match.group(0)
                        or match.group(0).strip("`") == script), (label, match.group(0))
        assert "CLAUDE_BLOG_SCRIPTS_DIR:-" not in text, label
        assert "load_untrusted_root.py" not in text, label


@pytest.mark.parametrize("name", PIPELINE_SKILLS)
def test_pipeline_skill_never_reads_private_files_itself(name):
    _, body = _parts(SKILLS / name / "SKILL.md")
    for label, text in _skill_texts(name):
        for line in text.splitlines():
            if not PRIVATE_RE.search(line):
                continue
            forbids = any(word in line for word in FORBIDS)
            if READ_RE.search(line):
                assert forbids, (label, line)
            else:
                assert forbids or any(output in line for output in OUTPUTS[name]), (label, line)
    assert "Read·Grep·Glob" in body and "금지" in body
    assert "펜스" in body and "지시가 아니" in body
    assert "원칙은 근거가 아니" in body
    assert "이번 인터뷰 답 > 원칙 > VOICE·BRAND > 업스트림 기본값" in body


def test_memo_skill_uses_no_subagent():
    _, body = _parts(MEMO_SKILL)
    assert "서브에이전트를 쓰지 않는다" in body


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


def test_decision_record_template_keeps_the_upstream_headings_and_nine_items():
    text = DECISION_RECORD.read_text(encoding="utf-8")
    assert text.startswith("# Template: ")
    assert re.search(r"^## When to Use", text, re.M)
    assert re.search(r"^## Section-by-Section", text, re.M)
    section = text.split("## Section-by-Section", 1)[1]
    assert re.findall(r"^### \d\. (.+)$", section, re.M) == DECISION_ITEMS
    # The plan's public-copy rules travel with the template.
    for rule in ("검토 중인 가설", "대표 이미지", "VOICE", "실제 측정값"):
        assert rule in text, rule


def test_interview_skill_hands_subagents_only_public_paths():
    texts = "\n".join(text for _, text in _skill_texts("blog-interview"))
    for agent in ("blog-researcher", "blog-writer", "blog-reviewer-ko"):
        assert f"`{agent}`" in texts, agent
    assert "공개본·템플릿·참고 파일" in texts  # the only read paths a subagent gets
    assert "초안 폴더" in texts  # the only output path
    assert '"${CLAUDE_SKILL_DIR}/references/decision-record.md"' in texts
    assert '"${CLAUDE_SKILL_DIR}/references/korean-review-rules.md"' in texts
    assert '"${CLAUDE_SKILL_DIR}/../blog/references"' in texts  # blog_reference_root
    assert '"${CLAUDE_SKILL_DIR}/../blog/templates"' in texts  # blog_template_root


def test_interview_skill_runs_the_steps_through_the_engine_and_loader():
    texts = "\n".join(text for _, text in _skill_texts("blog-interview"))
    for bundle in ("interview", "draft", "review", "cross-review"):
        assert re.search(rf"--bundle {re.escape(bundle)}(?![\w-])", texts), bundle
    for command in ("convert", "status", "record-review", "check", "cross-review"):
        assert re.search(rf'blogkit\.py" {re.escape(command)} "<slug>"', texts), command
    for code in ("memo_not_cleared", "review_stale", "review_inputs_changed", "codex_missing"):
        assert code in texts, code
    assert "인터뷰 답은 채팅으로 바로 AI에 들어가니 일반화해서 답하라" in texts
    assert "열린 질문" in texts and "스틸맨" in texts
    assert "/blog-learn <slug>" in texts


def test_interview_research_step_never_loads_the_draft_bundle():
    # Step 4 merges earlier claims from the review bundle (interview answers and claims only),
    # and on a first run (no claims file) calls no loader at all.
    _, body = _parts(INTERVIEW_SKILL)
    research = body.split("## ④ 리서치", 1)[1].split("\n## ", 1)[0]
    assert "--bundle draft" not in research
    assert re.search(r"--bundle review(?![\w-])", research)
    assert "첫 실행" in research and "이어서 하는 실행" in research
    contracts = (INTERVIEW_REFS / "interview-contracts.md").read_text(encoding="utf-8")
    assert "④에서는 review 묶음" in contracts


def test_interview_skill_stops_at_the_third_p0_failure():
    texts = "\n".join(text for _, text in _skill_texts("blog-interview"))
    assert "세 번째 P0 실패" in texts
    assert "해시가 바뀌어도" in texts
    assert "남은 P0 목록" in texts


def test_interview_skill_never_calls_the_upstream_delivery_contract():
    texts = "\n".join(text for _, text in _skill_texts("blog-interview"))
    for name in UPSTREAM_DELIVERY:
        assert name not in texts, name
    assert re.search(r"(?<![\w-])review\.md", texts) is None  # the upstream review file
    assert re.search(r"`blog-reviewer`", texts) is None  # only the Korean reviewer
    assert "업스트림 전달 계약" in texts and "부르지 않는다" in texts


# ---------------------------------------------------------------- /blog-learn (M2c-14)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _fenced(text: str, lang: str) -> list[str]:
    return re.findall(rf"^```{lang}\n(.*?)\n```$", text, re.S | re.M)


def _fill(text: str) -> str:
    """The example placeholders an actual run replaces, with fake values of the right shape."""
    return (text.replace("<status 출력의 content_hash>", "0" * 64).replace("<YYYY-MM-DD>", "2026-10-30")
            .replace("<slug>", "example-post"))


def test_learn_skill_runs_the_steps_through_the_engine_and_loader():
    _, body = _parts(LEARN_SKILL)
    for bundle in ("review", "proposal"):
        assert re.search(rf"--bundle {re.escape(bundle)}(?![\w-])", body), bundle
    for command in ("status", "record-learning"):
        assert re.search(rf'blogkit\.py" {re.escape(command)} "<slug>"', body), command
    for code in ("learning_stale", "learning_prerequisite_missing", "review_inputs_changed",
                 "learning_inputs_changed", "approval_missing"):
        assert code in body, code
    assert "학습 결과의 답은 인터뷰 근거로 쓰지 않" in body


def test_learn_skill_asks_at_least_two_questions_and_names_the_four_verdicts():
    _, body = _parts(LEARN_SKILL)
    assert "최소 2개" in body and "개념" in body and "반론" in body
    for verdict, wording in (("match", "일치"), ("factual_error", "사실 오류"), ("misconception", "오개념"),
                             ("stance_changed", "입장 변경 반영")):
        assert f"`{verdict}`" in body and wording in body, verdict
    assert "다시 묻" in body  # a misconception is explained and asked again
    assert "⑤부터" in body  # a factual error or a changed stance goes back to the draft
    assert "새 답 ID" in body and "옛 답" in body


def test_learn_skill_learning_example_passes_the_engine_check():
    engine = _load("blogkit_learn_engine", ENGINE)
    verifier = _load("blogkit_learn_verifier", VERIFIER)
    _, body = _parts(LEARN_SKILL)
    assert '"${CLAUDE_SKILL_DIR}/references/learning-formats.md"' in body
    formats = LEARN_FORMATS.read_text(encoding="utf-8")
    section = formats.split("## 학습 결과 형식", 1)[1].split("\n## ", 1)[0]
    blocks = _fenced(section, "json")
    assert len(blocks) == 1
    data = _fill(blocks[0]).encode("utf-8")
    doc, passed = engine.learning_result(data, "inbox/example-post/learning.json", verifier)
    assert passed is True
    assert {q["kind"] for q in doc["questions"]} == {"concept", "counter"}
    assert json.loads(data) == doc


def test_learn_skill_proposal_examples_pass_the_engine_parser():
    engine = _load("blogkit_learn_engine", ENGINE)
    verifier = _load("blogkit_learn_verifier", VERIFIER)
    _, body = _parts(LEARN_SKILL)
    formats = LEARN_FORMATS.read_text(encoding="utf-8")
    section = formats.split("## 제안 파일 형식", 1)[1].split("\n## ", 1)[0]
    kinds = {}
    for block in _fenced(section, "markdown"):
        kind, fields = engine.parse_proposal(_fill(block).encode("utf-8") + b"\n", ".blogkit/pending/x.md", verifier)
        kinds[kind] = fields
    assert set(kinds) == {"principle", "card"}
    for kind, fields in kinds.items():
        assert tuple(fields) == engine.PROPOSAL_FIELDS[kind], kind
    assert kinds["card"]["source_url"].startswith("https://")
    assert "^[a-z0-9][a-z0-9-]{0,63}$" in body  # the proposal id grammar


def test_learn_skill_shows_but_never_runs_approve_or_accept_proposal():
    _, body = _parts(LEARN_SKILL)
    texts = "\n".join(text for _, text in _skill_texts("blog-learn"))
    for block in _fenced(texts, "bash"):
        assert " approve " not in block and "accept-proposal" not in block, block
    shown = "\n".join(_fenced(body, "text"))
    assert re.search(r'blogkit\.py" approve "<slug>" --blog-repo "<blog repo>" --hash "<content_hash>" '
                     r'--warnings "<warnings_digest>"', shown)
    assert re.search(r'blogkit\.py" accept-proposal "<ID>" --blog-repo "<blog repo>" --digest "<digest>"', shown)
    assert "accept-proposal <ID> --digest <digest>" in body
    assert "이 스킬은 `approve`와 `accept-proposal`을 실행하지 않는다" in body
    assert "set -C" in texts  # a proposal file is created, never written over
    assert "sha256" in texts


def test_learn_skill_never_calls_the_upstream_delivery_contract():
    texts = "\n".join(text for _, text in _skill_texts("blog-learn"))
    for name in UPSTREAM_DELIVERY:
        assert name not in texts, name
    assert re.search(r"(?<![\w-])review\.md", texts) is None
    assert re.search(r"`blog-reviewer`", texts) is None
    assert "업스트림 전달 계약" in texts and "부르지 않는다" in texts
