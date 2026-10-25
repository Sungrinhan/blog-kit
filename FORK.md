# blog-kit 포크 문서

blog-kit은 업스트림 [AgriciDaniel/claude-blog](https://github.com/AgriciDaniel/claude-blog)를 원본 그대로 두고 덧붙인 포크다. 이 문서는 업스트림 기준 커밋과, 그 커밋 대비 이 포크가 바꾼 파일 전부를 기록한다.

`tests/test_fork_manifest.py`가 아래 세 목록의 합이 실제 변경(커밋, 스테이징, 미스테이징, 무시되지 않는 새 파일)과 같은지 검사한다. 업스트림 파일을 지우거나 이름을 바꾸면 목록에 적어도 실패한다. 파일을 만들거나 고치는 커밋은 같은 커밋에서 이 문서에 그 파일을 추가한다.

## 업스트림 기준

업스트림 기준 커밋: `354442348815a7f0327077ad89af2c43cf4b4ab4`

## 덧붙인 기능

업스트림에 없는 새 파일로 더한 기능.

- `FORK.md`: 이 문서. 업스트림 기준 커밋과 포크 변경 목록
- `tests/test_fork_manifest.py`: 이 문서의 목록과 실제 변경의 일치 검사
- `tests/test_fork_identity.py`: 플러그인 이름·업스트림 author 유지, 포크 마켓 이름·owner와 기존 항목 유지 검사

## 포크 수정

업스트림 파일을 직접 고친 곳. 업스트림 병합 때 충돌 후보다.

- `.gitignore`: `/flow` 작업 폴더(`.scratch/`)와 외부 도구가 만드는 로컬 전용 Codex용 사본(`/AGENTS.md`, `/.agents/`) 제외
- `CLAUDE.md`: 에이전트 스킬 설정 절(이슈 트래커, triage 라벨, 도메인 문서) 추가
- `.claude-plugin/marketplace.json`: 마켓 이름 `blog-kit`, owner를 포크 소유자로. 기존 항목(동반 플러그인 포함)은 그대로
- `.claude-plugin/plugin.json`: 버전 `2.3.1-kit.1`, repository·homepage를 포크 저장소로. 이름·author는 업스트림 그대로
- `.github/ISSUE_TEMPLATE/config.yml`: 토론·보안 제보·문서 안내 URL을 포크 저장소로
- `.github/SECURITY.md`: 보안 제보(Security Advisory) URL을 포크 저장소로
- `CHANGELOG.md`: 맨 위에 포크 절과 포크 릴리스 항목
- `CITATION.cff`: 버전 `2.3.1-kit.1`, 저장소를 포크로
- `LICENSE`: 업스트림 저작권 줄 아래 포크 저작권 줄
- `README.md`: 맨 위 포크 설치 안내 블록. 나머지 본문은 업스트림 그대로
- `pyproject.toml`: 버전 `2.3.1+kit.1`(PEP 440 로컬 버전), 프로젝트 URL을 포크로
- `scripts/validate_public_release.py`: 업스트림 author 상수와 포크 배포 상수 분리, 버전 대응 규칙 `python_version()`, 독립 설치 스크립트 표면을 필수 검사에서 제외(아래 "릴리스 검사 범위 밖")
- `skills/blog/SKILL.md`: 메타데이터 버전 `2.3.1-kit.1`
- `skills/blog-audio/SKILL.md`: 메타데이터 버전 `2.3.1-kit.1`
- `skills/blog-cluster/SKILL.md`: 메타데이터 버전 `2.3.1-kit.1`
- `skills/blog-flow/SKILL.md`: 메타데이터 버전 `2.3.1-kit.1`
- `skills/blog-google/SKILL.md`: 메타데이터 버전 `2.3.1-kit.1`
- `skills/blog-image/SKILL.md`: 메타데이터 버전 `2.3.1-kit.1`
- `skills/blog-locale-audit/SKILL.md`: 메타데이터 버전 `2.3.1-kit.1`
- `skills/blog-localize/SKILL.md`: 메타데이터 버전 `2.3.1-kit.1`
- `skills/blog-multilingual/SKILL.md`: 메타데이터 버전 `2.3.1-kit.1`
- `skills/blog-notebooklm/SKILL.md`: 메타데이터 버전 `2.3.1-kit.1`
- `skills/blog-translate/SKILL.md`: 메타데이터 버전 `2.3.1-kit.1`
- `tests/test_release_safeguards.py`: 검사기 픽스처를 포크 값으로, 마켓 이름 단언을 `blog-kit`으로. 업스트림 보안 checkout 안내 필수 테스트를 포크 보안 URL·설치 스크립트 제외·author 유지 테스트로 교체
- `tests/test_version_coherence.py`: 버전 표면 비교를 대응 규칙으로(`2.3.1-kit.N` ↔ `2.3.1+kit.N`, 접미사 없는 형식도 수용), 기준 버전을 plugin.json으로, 임시 폴더 픽스처로 일치·불일치 확인
- `uv.lock`: 패키지 버전 변경에 따른 재생성(`claude-blog` `2.3.1+kit.1`)

## 작업 파일

엔진 기능이 아니라 이 포크를 개발하는 데 쓰는 파일.

- `.claude/skills/flow/SKILL.md`: 마일스톤 작업 흐름 스킬(`/flow`)
- `docs/agents/domain.md`: 에이전트용 도메인 문서 규칙
- `docs/agents/issue-tracker.md`: 에이전트용 이슈 트래커 규칙
- `docs/agents/triage-labels.md`: 에이전트용 triage 라벨 규칙
- `GLOSSARY.md`: 포크 용어집

## 버전 체계

플러그인 매니페스트, 인용 파일, 라우터와 하위 스킬 메타데이터는 `<업스트림 버전>-kit.N`, Python 패키지 메타데이터는 같은 버전의 PEP 440 형식 `<업스트림 버전>+kit.N`을 쓴다. 둘의 대응은 `scripts/validate_public_release.py`의 `python_version()` 하나로 정의하고, 버전 일치 테스트와 릴리스 검사기가 함께 쓴다. 태그는 `v<플러그인 버전>`(예: `v2.3.1-kit.1`)이고 다시 지정하지 않는다.

## 릴리스 검사 범위 밖

포크는 블로그 레포 마켓 설치 하나만 지원한다. 아래 표면은 업스트림 값 그대로 두고, 릴리스 검사기의 필수 검사에서 뺀다.

- 독립 설치 스크립트(`install.sh`, `install.ps1`)의 raw URL, 내장 버전 문자열(업스트림 `2.3.1`), 저장소 owner 문자열: 포크에서 지원하지 않는 설치 경로이고, 고치면 README의 고정 해시와 업스트림 병합이 함께 흔들린다.
- README·CLAUDE.md·SECURITY.md의 설치 ref 안내(`CLAUDE_BLOG_REF`, `git checkout <commit-sha-or-release-tag>`): 위 독립 설치 경로의 안내다.
- 그대로 남는 검사: 비공개·제3자 raw URL과 마켓 slug 유출 금지, `v2.2.0` 설치 고정 금지. 업스트림 raw 설치 URL은 허용 목록에 남는다.
