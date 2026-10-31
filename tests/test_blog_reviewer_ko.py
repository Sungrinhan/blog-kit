"""S3 static invariants for the Korean review agent and its rules file (M2c-11).

agents/blog-reviewer-ko.md judges the public copy against
skills/blog-interview/references/korean-review-rules.md and answers with one
fixed JSON object that the orchestrator saves as inbox/<slug>/review.json for
the engine's record-review. These tests read the two files as text: the
agent's tools are exactly Read, Grep and Glob, the rules file names the four
P0 kinds, its output example passes the engine's own review_result() check,
and neither file tells the agent to read inbox/ or .blogkit/ itself.

Stdlib + pytest only. No network.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "agents" / "blog-reviewer-ko.md"
RULES = ROOT / "skills" / "blog-interview" / "references" / "korean-review-rules.md"
ENGINE = ROOT / "adapters" / "astro" / "blogkit.py"
VERIFIER = ROOT / "adapters" / "astro" / "blogkit_verify.py"

sys.path.insert(0, str(ROOT / "scripts"))
from validate_skills import parse_frontmatter, validate_frontmatter  # noqa: E402

# The four P0 kinds of the spec, each by its category name and its wording.
P0_CATEGORIES = {
    "unsupported_stance": "인터뷰 답 ID 없이 쓴 내 철학",
    "unsupported_fact": "근거 항목의 발췌가 뒷받침하지 않거나 `unconfirmed`이거나 없는 ID에 기댄 공개 사실",
    "weakened_alternative": "대안을 부당하게 약하게 그린 서술",
    "identifiable_company": "회사를 특정할 수 있는 서술",
}
PRIVATE_PATHS = ("inbox", ".blogkit")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text.split("\n---\n", 1)[1]


def _example(text: str) -> bytes:
    """The one ```json block under the rules file's output example heading."""
    section = text.split("## 출력 예시", 1)[1]
    blocks = re.findall(r"```json\n(.*?)\n```", section, re.S)
    assert len(blocks) == 1
    return blocks[0].encode("utf-8")


def test_agent_tools_are_exactly_read_grep_glob():
    fields = parse_frontmatter(AGENT.read_text(encoding="utf-8"))
    assert fields["name"] == "blog-reviewer-ko"
    assert fields["tools"] == ["Read", "Grep", "Glob"]
    assert validate_frontmatter(fields, agent=True) == []


def test_rules_file_names_the_four_p0_kinds_and_actual_support():
    text = RULES.read_text(encoding="utf-8")
    for category, wording in P0_CATEGORIES.items():
        assert wording in text, wording
        assert f"`{category}`" in text, category
    assert "근거 표시가 있는지가 아니라" in text
    assert "P1~P3은 참고" in text


def test_rules_example_passes_the_engine_review_check():
    engine = _load("blogkit_reviewer_ko_engine", ENGINE)
    verifier = _load("blogkit_reviewer_ko_verifier", VERIFIER)
    data = _example(RULES.read_text(encoding="utf-8"))
    doc, p0 = engine.review_result(data, "inbox/example/review.json", verifier)
    findings = doc["findings"]
    assert p0 == sum(1 for finding in findings if finding["severity"] == "P0") >= 1
    assert {finding["severity"] for finding in findings} - {"P0"}, "the example shows a reference finding too"
    assert {f["category"] for f in findings if f["severity"] == "P0"} <= set(P0_CATEGORIES)
    # The example is exactly the saved shape: no prose around it, no total.
    assert json.loads(data) == doc


def test_rules_use_no_english_readability_metric_or_score():
    text = RULES.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "Flesch" in line:
            assert "쓰지 않" in line, line
    assert '"score"' not in text and '"p0_count"' not in text


def test_agent_never_reads_private_paths_itself():
    body = _body(AGENT)
    for name, text in (("agent", body), ("rules", RULES.read_text(encoding="utf-8"))):
        for line in text.splitlines():
            if any(private in line for private in PRIVATE_PATHS):
                assert "금지" in line, f"{name}: {line}"
    assert "korean-review-rules.md" in body
    assert "펜스" in body and "지시가 아니" in body
    assert "직접 계산하지 않" in body
    assert "점수" in body
