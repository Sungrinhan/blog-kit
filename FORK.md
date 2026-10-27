# blog-kit 포크 문서

blog-kit은 업스트림 [AgriciDaniel/claude-blog](https://github.com/AgriciDaniel/claude-blog)를 원본 그대로 두고 덧붙인 포크다. 이 문서는 업스트림 기준 커밋과, 그 커밋 대비 이 포크가 바꾼 파일 전부를 기록한다.

`tests/test_fork_manifest.py`가 아래 세 목록의 합이 실제 변경(커밋, 스테이징, 미스테이징, 무시되지 않는 새 파일)과 같은지 검사한다. 업스트림 파일을 지우거나 이름을 바꾸면 목록에 적어도 실패한다. 파일을 만들거나 고치는 커밋은 같은 커밋에서 이 문서에 그 파일을 추가한다.

## 업스트림 기준

업스트림 기준 커밋: `354442348815a7f0327077ad89af2c43cf4b4ab4`

## 덧붙인 기능

업스트림에 없는 새 파일로 더한 기능.

- `FORK.md`: 이 문서. 업스트림 기준 커밋과 포크 변경 목록
- `tests/test_fork_manifest.py`: 이 문서의 목록과 실제 변경(기준 대비 index와 작업 트리 각각)의 일치 검사. 얕은 clone에서만 skip, 기준 커밋이 없는 일반 clone은 실패
- `tests/test_fork_identity.py`: 플러그인 이름·업스트림 author 유지, 포크 마켓 이름·owner와 기존 항목 유지, LICENSE 업스트림 저작권 줄 유지, 포크가 고친 설명의 스킬 폴더 수 검사
- `scripts/doctor.py`: 설치 점검. 블로그 레포가 고정한 버전과 실제 실행된 엔진(공유 설정, `claude plugin list --json`, 설치 레지스트리, 태그 커밋), 실행 경로, Python 버전, 런타임 경로를 검사해 JSON으로 출력. 확인할 수 없으면 실패. 이 블로그 레포의 설치는 레지스트리 `projectPath`로 먼저 골라 다른 레포의 project 설치와 구분하고, 런타임 경로는 기본·활성 Claude 플러그인 폴더 밖이고 폴더(또는 아직 없음)여야 함. 다른 마켓의 같은 이름 플러그인은 user 범위이거나, 레지스트리가 이 블로그 레포에 둔 project·local 설치일 때만 활성으로 봄(어느 레포 것인지 모르면 확인 불가). 설정·레지스트리 파일은 symlink를 따르지 않고 4MB까지만 읽으며, symlink인 `--blog-repo`는 거부. 설치 신원 검사(a~f)는 `identity_checks()`로 다른 설치 스크립트(엔진 CLI)가 불러 쓸 수 있고, 이때 실행 경로 검사는 부른 스크립트의 경로로 판정. 검증기 사본 검사(i)는 블로그 레포의 `scripts/blogkit_verify.py`가 symlink 아닌 일반 파일이고 설치 경로의 정본과 바이트가 같으며 머리의 버전이 설치 버전인지 보고, 엔진 CLI도 `check_verifier_copy()`로 같은 검사를 쓴다
- `skills/blog-doctor/SKILL.md`: 설치된 설치 점검 스크립트를 실행하고 결과를 보여 주는 스킬(`/blog-doctor`). 라우터 명령 표에는 넣지 않음. 다른 스킬과 달리 `CLAUDE_BLOG_SCRIPTS_DIR` override를 따르지 않고 설치본의 스크립트만 실행한다(점검 (f)가 실행 위치를 확인하므로, override로 다른 사본을 돌리면 점검이 뜻을 잃는다)
- `tests/test_doctor.py`: 스텁 `claude`·`git`과 임시 블로그 레포·레지스트리로 설치 점검의 통과·실패 사례와 비밀 비노출 확인
- `adapters/astro/blogkit.py`: 엔진 CLI(stdlib). 하위 명령 `convert`·`check`·`status`·`approve`·`publish`·`sync-verifier`, 결과는 JSON이고 실패는 종료 코드 1. 모든 명령은 자기 실제 경로가 블로그 레포 밖이고 설치 신원(a~f)이 모두 ok일 때만 실행된다. `sync-verifier`는 사본 검사만 면제받아 설치본 정본을 블로그 레포 사본 위치로 원자적으로 복사하고(대상·폴더 symlink 거부), 다른 명령은 사본 일치(i)·블로그 레포가 git 최상위인지를 더 확인한다. 증거·공개본을 바꾸는 명령은 `inbox/<slug>/blogkit.lock`(프로세스 id·호스트)을 잡고, 같은 호스트에서 죽은 프로세스의 잠금만 치운다. 외부 명령 출력은 버리고 종료 코드와 블로그 레포 안 실제 파일의 `상대 경로:줄`만 남긴다. `convert`는 업스트림 형식 초안 `inbox/<slug>/draft/<slug>.md`에서 첫 공개본(`draft: true`)·글 전용 자산·새 발행 증거를 만든다. 입력 프론트매터는 제한된 부분집합(모르는 키·업스트림 별칭 키 거부)이고, 참조는 역슬래시·`//` 거부 → 외부 URL(업스트림 렌더러의 링크 안전 함수) → `/`·`#` 유지 → 초안 폴더 안 이미지 파일 참조(자산 폴더로 복사, 참조 재작성) 순으로 가르며, 남은 표식(업스트림 렌더러의 표식 패턴)은 실패다. 같은 slug의 글·자산·증거가 이미 있으면 거부한다. 반영은 `inbox/<slug>/` 아래 임시 폴더 준비 → journal → 대상 재확인 → 대상별 rename → 반영 완료 → 정리의 한 단위이고, 이동 중 처리 가능한 오류는 그 자리에서 되돌린다
- `tests/test_blogkit_cli.py`: 가짜 설치 경로·레지스트리·스텁 `claude`·`git`·임시 HOME·실제 git 블로그 레포로 엔진 CLI의 신원 거부, `sync-verifier`, 사본 검사, slug 잠금, 외부 출력 비노출, 첫 변환(매핑·참조 재작성·증거 형태)과 거부 시 무변경, 부트스트랩 스크립트로 파일 이동 실패를 주입한 되돌리기·반영 직전 대상 출현 확인
- `adapters/astro/blogkit_verify.py`: 발행 검증기 정본(stdlib). 블로그 레포는 바이트가 같은 사본을 빌드 전·배포 직전에 실행한다. 소스 모드는 공개 글(`draft: false`)마다 경로(`src/content/posts/<slug>.md`, mdx·하위 폴더 거부)·공개본 프론트매터 문법·글 전용 자산 규칙·내용 해시(`blogkit-content-v1`)·발행 증거 계약 1(지원 계약이면 과거 엔진 버전 증거도 통과)의 완전성을 확인하고, 문법 밖 파일은 `draft: true` 한 줄일 때만 초안으로 인정. `BLOGKIT_INCLUDE_DRAFTS`가 있으면 실패. 경로의 symlink 거부·읽기 크기 제한, 출력에 본문·URL 없음. 파일 머리의 `VERIFIER_VERSION`은 엔진 버전, `SELF_DIGEST`는 그 값을 0으로 바꾼 파일의 SHA-256이고(check_secrets가 내용 digest로 판정하는 `"sha256": "<hex>"` 모양) 실행마다 확인한다(파일을 고치면 `self_digest()`로 다시 계산해 새긴다)
- `tests/test_blogkit_verify.py`: 임시 블로그 레포로 검증기 소스 모드의 통과·실패 사례, 증거 계약 위반, 자기 digest, 해시 직렬화(손으로 만든 예시와 골든 벡터) 확인
- `tests/fixtures/blogkit/hash-vectors.json`: 내용 해시 골든 벡터. 한 번 생성해 고정한 값이라, 바뀌면 이미 발행한 글의 해시가 바뀐다는 뜻
- `tests/test_adapters_release_scope.py`: 업스트림 릴리스 검사기가 보지 않는 `adapters/`에 같은 금지 문자열·설치 스크립트 링크 검사를 적용
- `docs/BLOG-KIT-UPGRADE.md`: 블로그 레포 설정 예시와 설치·업그레이드·다운그레이드 순서(재시작 뒤 `sync-verifier` → 사본 커밋 → 설치 점검), uninstall 금지, 런타임 경로 첫 전환, 보존 확인
- `docs/BLOG-KIT-HUMAN-CHECK.md`: 두 기기 설치·전환 사람 검증 기록 양식과 단계별 기대 설치 점검 결과

## 포크 수정

업스트림 파일을 직접 고친 곳. 업스트림 병합 때 충돌 후보다.

- `.gitignore`: `/flow` 작업 폴더(`.scratch/`)와 외부 도구가 만드는 로컬 전용 Codex용 사본(`/AGENTS.md`, `/.agents/`) 제외
- `CLAUDE.md`: 에이전트 스킬 설정 절(이슈 트래커, triage 라벨, 도메인 문서) 추가. 스킬 폴더 수(33)·스크립트 수(23)와 구조도에 `blog-doctor`·`scripts/doctor.py`
- `.claude-plugin/marketplace.json`: 마켓 이름 `blog-kit`, owner를 포크 소유자로, `claude-blog` 설명의 스킬 폴더 수를 33개로. 기존 항목(동반 플러그인 포함)은 그대로
- `.claude-plugin/plugin.json`: 버전 `2.3.1-kit.N`, repository·homepage를 포크 저장소로, 설명의 스킬 폴더 수를 33개(`blog-doctor` 포함)로. 이름·author는 업스트림 그대로
- `.github/ISSUE_TEMPLATE/config.yml`: 토론·보안 제보·문서 안내 URL을 포크 저장소로
- `.github/SECURITY.md`: 보안 제보(Security Advisory) URL을 포크 저장소로
- `CHANGELOG.md`: 맨 위에 포크 절과 포크 릴리스 항목
- `CITATION.cff`: 버전 `2.3.1-kit.N`, 저장소를 포크로
- `LICENSE`: 업스트림 저작권 줄 아래 포크 저작권 줄
- `README.md`: 맨 위 포크 설치 안내 블록(절차서 링크 포함). 나머지 본문은 업스트림 그대로
- `pyproject.toml`: 버전 `2.3.1+kit.N`(PEP 440 로컬 버전), 프로젝트 URL을 포크로
- `scripts/validate_public_release.py`: 업스트림 author 상수와 포크 배포 상수 분리, 버전 대응 규칙 `python_version()`, 독립 설치 스크립트 표면을 필수 검사에서 제외(아래 "릴리스 검사 범위 밖")
- `skills/blog/SKILL.md`: 메타데이터 버전 `2.3.1-kit.N`. 라우팅 절에 라우팅하지 않는 포크 스킬 `blog-doctor` 한 줄
- `skills/blog-audio/SKILL.md`: 메타데이터 버전 `2.3.1-kit.N`
- `skills/blog-cluster/SKILL.md`: 메타데이터 버전 `2.3.1-kit.N`
- `skills/blog-flow/SKILL.md`: 메타데이터 버전 `2.3.1-kit.N`
- `skills/blog-google/SKILL.md`: 메타데이터 버전 `2.3.1-kit.N`
- `skills/blog-image/SKILL.md`: 메타데이터 버전 `2.3.1-kit.N`. MCP 설정 도우미와 설정 확인 스크립트 호출 안내를 `--project-dir "<blog repo>"`로, 쓰는 위치 설명을 `.mcp.json`·`~/.claude.json`으로
- `skills/blog-locale-audit/SKILL.md`: 메타데이터 버전 `2.3.1-kit.N`
- `skills/blog-localize/SKILL.md`: 메타데이터 버전 `2.3.1-kit.N`
- `skills/blog-multilingual/SKILL.md`: 메타데이터 버전 `2.3.1-kit.N`
- `skills/blog-notebooklm/SKILL.md`: 메타데이터 버전 `2.3.1-kit.N`
- `skills/blog-translate/SKILL.md`: 메타데이터 버전 `2.3.1-kit.N`
- `skills/blog-image/scripts/setup_image_mcp.py`: 대상 선택을 `--project-dir <블로그 레포>`(그 폴더의 `.mcp.json`)와 `--global`(사용자 범위 MCP를 실제로 읽는 `~/.claude.json`, `CLAUDE_CONFIG_DIR`가 있으면 그 폴더의 `.claude.json`. 다른 키·서버 보존, 쓰는 도중 Claude Code가 고치면 다시 읽어 우리 항목만 다시 적용) 둘 중 하나로. 설치본(기본·활성 설정 폴더의 플러그인 폴더 안)에서 둘 다 없으면 오류로 멈추고 아무것도 쓰지 않음, 빈 `--project-dir`도 오류, 개발 체크아웃은 자기 루트 자동 탐색만 허용하고 실패하면 전역으로 넘어가지 않음. 설정·확인·제거가 같은 규칙. 업스트림 `--project`는 대상 없음과 같은 뜻으로 유지
- `skills/blog-image/scripts/validate_image_setup.py`: 설정 확인도 설정 도우미의 대상 선택 규칙(`resolve_target`)을 그대로 써서 그 한 파일만 읽음(`--project-dir`·`--global`, 설치본에서 둘 다 없으면 오류). 플러그인 루트 `.mcp.json`과 예전 `~/.claude/settings.json`으로 넘어가지 않음
- `tests/test_skill_frontmatter.py`: 스킬 폴더 수 단언을 업스트림 32개 + `blog-doctor` = 33개로
- `tests/test_scripts_wave3_image.py`: 기존 전역 설정 테스트를 `~/.claude.json` 기대값과 새 함수 인자로. 임시 HOME·설치본 흉내로 대상 선택 사례(대상 없음·경로·전역·둘 다·자동 탐색 실패·성공, `--project`)를 설정·확인·제거와 설정 확인 스크립트에 대해 확인
- `tests/test_release_safeguards.py`: 검사기 픽스처를 포크 값으로, 마켓 이름 단언을 `blog-kit`으로. 업스트림 보안 checkout 안내 필수 테스트를 포크 보안 URL·설치 스크립트 제외·author 유지 테스트로 교체
- `tests/test_version_coherence.py`: 버전 표면 비교를 대응 규칙으로(`2.3.1-kit.N` ↔ `2.3.1+kit.N`, 접미사 없는 형식도 수용), 기준 버전을 plugin.json으로, 임시 폴더 픽스처로 일치·불일치 확인
- `uv.lock`: 패키지 버전 변경에 따른 재생성(`claude-blog` `2.3.1+kit.N`)

## 작업 파일

엔진 기능이 아니라 이 포크를 개발하는 데 쓰는 파일.

- `.claude/skills/flow/SKILL.md`: 마일스톤 작업 흐름 스킬(`/flow`)
- `docs/adr/0001-publish-evidence-by-content-hash.md`: 발행 조건을 내용 해시에 묶인 발행 증거로 판정한다는 결정 기록(M2b)
- `docs/agents/domain.md`: 에이전트용 도메인 문서 규칙
- `docs/agents/issue-tracker.md`: 에이전트용 이슈 트래커 규칙
- `docs/agents/triage-labels.md`: 에이전트용 triage 라벨 규칙
- `GLOSSARY.md`: 포크 용어집

## 업스트림 문서의 수량 문구

README·`docs/`·`.github/CONTRIBUTING.md`·`brain/` 등 업스트림 문서 본문의 스킬 폴더 수("32 skill directories", "31 sub-skills")와 스크립트 수는 업스트림 기준 그대로 둔다. 포크가 덧붙인 `blog-doctor`와 `scripts/doctor.py`는 이 문서와, 포크가 이미 고친 설명(플러그인 매니페스트, 마켓 목록, `CLAUDE.md`, 라우터 SKILL.md)에만 반영한다. 업스트림 병합 때 충돌을 줄이려는 것이다.

## 버전 체계

플러그인 매니페스트, 인용 파일, 라우터와 하위 스킬 메타데이터는 `<업스트림 버전>-kit.N`, Python 패키지 메타데이터는 같은 버전의 PEP 440 형식 `<업스트림 버전>+kit.N`을 쓴다. 둘의 대응은 `scripts/validate_public_release.py`의 `python_version()` 하나로 정의하고, 버전 일치 테스트와 릴리스 검사기가 함께 쓴다. 태그는 `v<플러그인 버전>`(예: `v2.3.1-kit.1`)이고 다시 지정하지 않는다.

## 릴리스 검사 범위 밖

포크는 블로그 레포 마켓 설치 하나만 지원한다. 아래 표면은 업스트림 값 그대로 두고, 릴리스 검사기의 필수 검사에서 뺀다.

- 독립 설치 스크립트(`install.sh`, `install.ps1`)의 raw URL, 내장 버전 문자열(업스트림 `2.3.1`), 저장소 owner 문자열: 포크에서 지원하지 않는 설치 경로이고, 고치면 README의 고정 해시와 업스트림 병합이 함께 흔들린다.
- README·CLAUDE.md·SECURITY.md의 설치 ref 안내(`CLAUDE_BLOG_REF`, `git checkout <commit-sha-or-release-tag>`): 위 독립 설치 경로의 안내다.
- 그대로 남는 검사: 비공개·제3자 raw URL과 마켓 slug 유출 금지, `v2.2.0` 설치 고정 금지. 업스트림 raw 설치 URL은 허용 목록에 남는다.

## 지원 설치 경로

- **지원**: 블로그 레포 마켓 설치 하나. 블로그 레포의 공유 설정 `.claude/settings.json`에 마켓 `Sungrinhan/blog-kit`을 `ref: v<버전>` 태그로 고정하고, 그 레포에서 `claude-blog@blog-kit`을 project 범위로 설치한다. 절차는 [`docs/BLOG-KIT-UPGRADE.md`](docs/BLOG-KIT-UPGRADE.md).
- **미지원**: 독립 설치 스크립트(`install.sh`, `install.ps1`, `uninstall.*`), 업스트림 마켓(`claude-blog@agricidaniel-blog`) 설치, user 범위 설치, 브랜치나 커밋 SHA로 고정한 마켓. 업스트림 문서의 설치 안내는 업스트림 본문 그대로 두지만 이 포크에서는 따르지 않는다.
- 태그는 `v<플러그인 버전>`이고, 한 번 push한 태그는 다시 지정하지 않는다. 고칠 것이 있으면 다음 `kit.N`으로 새 태그를 만든다.

## 업스트림 병합 절차

1. **기준 SHA 기록**: 가져올 업스트림 커밋 SHA를 정하고, 병합 전 이 문서의 "업스트림 기준 커밋"과 비교할 수 있게 적어 둔다.
2. **병합**: `git fetch upstream` 뒤 그 SHA를 병합한다. 충돌은 위 "포크 수정" 목록의 파일에서 난다. 목록의 이유를 보고 포크 의도를 유지하며 푼다.
3. **문서 갱신**: "업스트림 기준 커밋"을 새 SHA로 바꾸고, 병합으로 생기거나 없어진 포크 수정을 목록에 반영한다. 업스트림이 고친 파일을 포크가 더 고치지 않았다면 목록에 넣지 않는다.
4. **전체 자동 검증**: 깨끗한 작업 트리에서 pytest 전체, 스킬 검증, 산문 린트, 릴리스 검사기, `uv lock --check`, 플러그인 검증(예약 이름 오류 1건은 정상)이 모두 통과해야 한다.
5. **버전 증가**: 업스트림 버전이 바뀌었으면 `<새 업스트림 버전>-kit.1`, 그대로면 `kit.N`을 하나 올린다("버전 체계"). CHANGELOG 포크 절에 항목을 더한다.
6. **태그·push**: 사람 작업 티켓으로 그 커밋에 `v<버전>` 태그를 만들고 origin에 push한다.
7. **블로그 레포 ref 변경**: 블로그 레포 공유 설정의 `ref`를 새 태그로 바꿔 커밋하고, 절차서 3절 순서로 전환한다.
8. **두 기기 설치 점검**: 두 기기 모두 설치 점검이 전부 `ok`인지 확인하고 결과를 사람 작업 티켓에 남긴다.

**보안 수정은 릴리스 주기와 무관하게 바로 가져온다.** 업스트림 보안 수정(보안 권고, 취약점 수정 커밋)은 다른 업스트림 변경을 기다리지 않고 위 절차로 바로 병합하고 `kit.N`을 올려 배포한다.

## M2a 상태

- **구현 완료**: 커밋 A(`2.3.1-kit.1`)와 커밋 B(`2.3.1-kit.2`)가 있고, 각각 깨끗한 작업 트리에서 전체 자동 검증(위 병합 절차 4번과 같은 목록)이 통과했다.
- **사람 검증 대기**: 사람 작업 티켓(태그, push, 블로그 레포 설정, 두 기기 전환 확인)이 열려 있다. 이 동안 M2b를 시작할 수 있다.
- **검증 완료**: 두 기기 모두 네 단계(A 설치, B 업그레이드, A 다운그레이드, B 복귀) 설치 점검이 전부 `ok`이고, 매 단계 표식 파일 해시가 같으며 runner 오프라인 확인이 통과한 증거가 사람 작업 티켓에 있다.
- M2c의 최종 판정은 검증 완료된 ref로만 한다.

## 런타임 데이터 형식

M2a 동안 런타임 데이터(`CLAUDE_BLOG_RUNTIME_DIR` 아래 통합별 `.venv`·`data`, 그 안의 인증·라이브러리·브라우저 상태)의 형식과 폴더 구조를 바꾸지 않는다. 업스트림의 런타임 회귀 테스트(`tests/test_runtime_state.py`, `tests/test_runtime_fixes.py`, `tests/test_final_review_runtime_fixes.py`, `tests/test_notebooklm_wave3.py`)를 고치지 않고 유지하는 것이 이 규칙의 근거다. 그래서 A와 B 사이를 오가도 같은 런타임 폴더를 그대로 쓸 수 있다.
