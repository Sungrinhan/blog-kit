# blog-kit 포크 문서

blog-kit은 업스트림 [AgriciDaniel/claude-blog](https://github.com/AgriciDaniel/claude-blog)를 원본 그대로 두고 덧붙인 포크다. 이 문서는 업스트림 기준 커밋과, 그 커밋 대비 이 포크가 바꾼 파일 전부를 기록한다.

`tests/test_fork_manifest.py`가 아래 세 목록의 합이 실제 변경(커밋, 스테이징, 미스테이징, 무시되지 않는 새 파일)과 같은지 검사한다. 업스트림 파일을 지우거나 이름을 바꾸면 목록에 적어도 실패한다. 파일을 만들거나 고치는 커밋은 같은 커밋에서 이 문서에 그 파일을 추가한다.

## 업스트림 기준

업스트림 기준 커밋: `354442348815a7f0327077ad89af2c43cf4b4ab4`

## 덧붙인 기능

업스트림에 없는 새 파일로 더한 기능.

- `FORK.md`: 이 문서. 업스트림 기준 커밋과 포크 변경 목록
- `tests/test_fork_manifest.py`: 이 문서의 목록과 실제 변경의 일치 검사

## 포크 수정

업스트림 파일을 직접 고친 곳. 업스트림 병합 때 충돌 후보다.

- `.gitignore`: `/flow` 작업 폴더(`.scratch/`)와 외부 도구가 만드는 로컬 전용 Codex용 사본(`/AGENTS.md`, `/.agents/`) 제외
- `CLAUDE.md`: 에이전트 스킬 설정 절(이슈 트래커, triage 라벨, 도메인 문서) 추가

## 작업 파일

엔진 기능이 아니라 이 포크를 개발하는 데 쓰는 파일.

- `.claude/skills/flow/SKILL.md`: 마일스톤 작업 흐름 스킬(`/flow`)
- `docs/agents/domain.md`: 에이전트용 도메인 문서 규칙
- `docs/agents/issue-tracker.md`: 에이전트용 이슈 트래커 규칙
- `docs/agents/triage-labels.md`: 에이전트용 triage 라벨 규칙
- `GLOSSARY.md`: 포크 용어집
