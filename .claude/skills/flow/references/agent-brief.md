# /flow 4단계 서브에이전트 공통 지침

레포: /Users/linee/Documents/blog-kit (claude-blog 포크 블로그 엔진). 연결 레포: ~/Documents/devlog (블로그 사이트). 맡은 티켓 번호와 그 티켓의 요점은 오케스트레이터가 프롬프트로 준다.

## 읽을 것 (작업 전에)
- 스펙: `.scratch/<slug>/spec-<N>.md` (GitHub 스펙 이슈 #N과 같음). 특히 Implementation Decisions와 Testing Decisions(승인된 시험 이음새)
- 결정 기록: `.scratch/<slug>/decisions.md`
- 맡은 티켓: `gh issue view <번호> --repo Sungrinhan/blog-kit`
- `CLAUDE.md`의 Development Rules, `.github/CONTRIBUTING.md`의 Code Style·Security expectations, `FORK.md`(특히 "업스트림 스크립트 규칙의 포크 예외")
- 기존 선례: `adapters/astro/blogkit.py`(엔진 CLI), `adapters/astro/blogkit_verify.py`, `scripts/confidential_check.py`, `scripts/load_blogkit_context.py`, `tests/test_blogkit.py`(엔진 하네스), `tests/test_blogkit_skills.py`(스킬 정적 검사)

## 방법
- TDD: 실패하는 테스트를 먼저 쓰고 red 확인 → 구현 → green. 테스트는 외부 동작만 본다(CLI 인자·입력 파일 → JSON·종료 코드·쓰인 파일). 처음부터 통과하는 테스트는 그 테스트가 지키는 코드를 일부러 깨서 red를 확인한 뒤 원복한다(원복은 cmp나 git diff로 확인).
- 새 입력 경로(파일·폴더 인자, 읽는 설정·데이터 파일)는 symlink 거부(경로 구성 요소 포함), 일반 파일만, 크기 상한, 엄격한 UTF-8을 두고 행동 테스트로 확인한다. 본문을 훑는 정규식·스캐너에는 크기 상한의 최악 입력(긴 한 줄, 닫히지 않는 표식, 반복 접두어)으로 시간 제한 테스트를 둔다.
- 기존 코드의 이름·주석 밀도·관용구를 따른다. stdlib만. 출력은 JSON 하나와 고정 오류 코드(포크 관례).
- 파일을 만들거나 고치면 같은 변경 안에서 `FORK.md` 목록(덧붙인 기능 / 포크 수정 / 작업 파일)에 등록하거나 설명을 갱신한다. `tests/test_fork_manifest.py`가 검사한다.
- 업스트림 파일은 FORK.md의 현재 마일스톤 시작 커밋 이후 허용 목록 안에서만 고친다. `tests/test_adapters_release_scope.py`가 검사한다.
- `adapters/astro/blogkit_verify.py`를 고치면 `SELF_DIGEST`를 다시 새긴다(파일 안 `self_digest()` 방식, FORK.md 설명 참고).
- 산문(SKILL.md, references, docs, FORK.md)에는 em dash·en dash·` -- `를 쓰지 않는다(`python scripts/lint_prose.py`).
- 한국어 SKILL.md의 크기는 `tests/test_blogkit_skills.py`의 크기 테스트로 확인한다. 업스트림 `validate_skills.py`의 단어 수 검사는 한국어를 덜 센다.
- 테스트 실행: `export PATH=$PWD/.venv/bin:$PATH` 뒤 관련 테스트 파일만 `python -m pytest tests/<파일> -q`. 전체 스위트(15~20분)는 돌리지 않아도 된다. 끝나면 관련 파일과 `tests/test_fork_manifest.py`, `tests/test_adapters_release_scope.py`, 그리고 `python scripts/validate_skills.py --root .`, `python scripts/lint_prose.py`, `python scripts/check_secrets.py`를 돌린다.
- 임시 파일은 세션 scratchpad에 둔다(`/tmp`가 아님).

## 금지
- git commit·push·tag, gh 이슈 쓰기(생성·댓글·닫기), upstream 쓰기. 커밋은 오케스트레이터가 한다.
- `~/.config/blog-kit/confidential.txt`를 읽거나 출력하지 않는다. 테스트는 `HOME`을 임시 폴더로 돌려 가짜 금칙어를 쓴다.
- devlog의 `inbox/` 실제 내용, 말투·원칙·카드·회사 정보를 코드·테스트·문서에 넣지 않는다. fixture는 가짜 예시.
- `~/.claude/plugins/cache/…`를 고치지 않는다. `.env*`, 키 파일을 읽지 않는다.
- 맡은 티켓 범위 밖 구현을 하지 않는다. 스펙과 다르게 해야 할 것 같거나 막히면 멈추고 보고한다.
- 테스트는 실제 네트워크·실제 Codex를 쓰지 않는다.

## 보고 (마지막 메시지)
- 바꾼 파일 목록과 각 변경 한 줄
- 추가한 테스트와 red 확인 방법
- 돌린 명령과 결과(통과 수)
- 스펙과 다르게 한 점, 이월할 것, 불확실한 점
