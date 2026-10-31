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
- `adapters/astro/blogkit.py`: 엔진 CLI(stdlib). 하위 명령 `convert`·`check`·`status`·`approve`·`publish`·`memo-check`·`record-review`·`record-learning`·`cross-review`·`accept-proposal`·`sync-verifier`, 결과는 JSON이고 실패는 종료 코드 1. 모든 명령은 자기 실제 경로가 블로그 레포 밖이고 설치 신원(a~f)이 모두 ok일 때만 실행된다. `sync-verifier`는 사본 검사만 면제받아 설치본 정본을 블로그 레포 사본 위치로 원자적으로 복사하고(대상·폴더 symlink 거부), 다른 명령은 사본 일치(i)·블로그 레포가 git 최상위인지를 더 확인한다. 증거·공개본을 바꾸는 명령은 `inbox/<slug>/blogkit.lock`(프로세스 id·호스트)에 배타 flock을 잡고, 그 안에서 같은 호스트의 죽은 프로세스 잠금만 판정해 치운다(해제할 때는 아직 자기 파일일 때만 지운다). 외부 명령 출력은 버리고 종료 코드와 블로그 레포 안 실제 파일의 `상대 경로:줄`만 남긴다. `convert`는 업스트림 형식 초안 `inbox/<slug>/draft/<slug>.md`에서 공개본(`draft: true`)·글 전용 자산·발행 증거를 만들거나 바꾼다. 입력 프론트매터는 제한된 부분집합(모르는 키·업스트림 별칭 키 거부)이고, 참조는 역슬래시·`//` 거부 → 외부 URL(업스트림 렌더러의 링크 안전 함수) → `/`·`#` 유지 → 초안 폴더 안 이미지 파일 참조(자산 폴더로 복사, 참조 재작성) 순으로 가르며, 남은 표식(업스트림 렌더러의 표식 패턴)은 실패다. 발행된 글은 항상 거부하고, 기존 글은 초안이면서 글·자산이 증거의 `public_copy` 기록과 정확히 같을 때만 덮어쓴다(기록 없는 파일·손으로 고친 공개본·소유 불명 자산은 보존하고 실패). 내용 해시가 바뀌면 `records`를 새로 시작하고, 같으면 유지한다. 반영은 `inbox/<slug>/` 아래 임시 폴더 준비 → journal → 대상이 시작 때 상태 그대로인지 재확인 → 대상별 백업·rename → 반영 완료 → 정리의 한 단위이고, 이동 중 처리 가능한 오류는 그 자리에서 되돌린다. 중간에 멈춰 임시 폴더·백업·journal이 남으면 그 slug의 다른 명령은 거부하고, 다음 `convert`가 대상·백업·임시 위치의 실제 파일 digest를 journal과 대조해 반영 완료 전이면 되돌리고(멱등) 뒤면 정리만 한다. 원래 파일 유실·모르는 파일(대상 자리, 임시·백업 폴더 어디든)·convert가 쓰지 않는 경로를 적은 journal이면 아무것도 바꾸지 않고 멈춘다. `check`는 발행 게이트 1~4를 실행해 증거에 게이트별로 기록한다: 1 도구(Python 3.11+, PATH의 `node`·`npx`, git 최상위, 검증기 사본, 기밀 검사 스크립트의 금칙어 목록 상태), 2 본문 안전성(코드·autolink 밖 raw HTML·인라인 SVG와 자산 SVG 파일을 업스트림 렌더러의 태그·속성·href/src 스킴 허용 목록으로 검사하고 고치지 않고 차단, raw HTML 블록 안의 백틱은 CommonMark대로 코드로 보지 않음, autolink 스킴도 같은 허용 규칙, 마크다운 참조는 convert 규칙, 파일 참조는 그 글 자산 폴더의 이미지만), 3 `npx --no-install astro check` 종료 코드, 4 현재 해시의 검수 기록 P0 0개와 검수 입력 일치, 5 기밀 검사 스크립트로 글·자산 파일 검사(금칙어 차단, 휴리스틱은 경고 ID), 6 `BLOGKIT_INCLUDE_DRAFTS=1`로 `npx --no-install astro build --outDir .blogkit-preview`(블로그 레포 `.gitignore`가 무시해야 하고 먼저 비움)를 돌려 글 페이지 존재·로컬 이미지 해석·글의 사이트 내부 링크와 앵커를 확인하고, 외부 URL(링크·이미지·autolink)은 링크 검사 모듈(초안 폴더의 `external-links.allowed`)로 보며 일시 오류는 경고. 경고 ID는 `<게이트>:<분류>:<파일>:<줄>[:<URL digest>]`. 시작할 때 내용 해시를 고정하고 기록 직전에 다시 계산해 다르면 아무것도 기록하지 않는다. 증거의 해시가 다르면 `records`를 새로 시작하고 `public_copy`는 둔다. `status`는 현재·증거 해시, 현재 해시의 게이트 기록, 경고 ID와 digest, 빠진 기록을 보여 준다. `approve --hash --warnings`는 전체 해시와 최신 게이트 경고 집합 digest가 현재와 같고 게이트 1~6·검수·학습 확인이 통과일 때만 승인 기록을 더한다. `publish`는 해시 재계산·증거 완전성을 확인하고, 글 전용 자산 폴더의 모든 파일이 HEAD에 같은 바이트로 있는지(`git ls-tree`의 blob과 `git hash-object` 비교, skip-worktree로 숨긴 변경도 잡음)와 글·증거 말고 스테이징된 것이 없는지 본 뒤 `draft` 줄을 원자적으로 바꾸고, 확인한 바이트를 blob으로 올려 두 경로만 `publish: <slug>`로 커밋한 뒤 HEAD의 blob과 대조한다. 커밋은 별도 index(HEAD + 두 blob)에서 `pre-commit`·`commit-msg` 훅을 돌린 뒤 `commit-tree`와 `update-ref`(HEAD가 그대로일 때만)로 만들고, 훅이 다른 것을 stage하면 HEAD를 바꾸지 않고 거부한다. 글 교체는 글을 옆으로 옮겨 확인한 바이트와 같을 때만 새 파일을 링크로 넣어, 그 사이 사람이 고친 내용은 되돌려 두고 멈춘다. 교체가 실패하면 글을 제자리로 돌리고, 중단돼 남은 교체(옮겨 둔 글만 있음, 글이 옮겨 둔 원본이나 그 바뀐 사본과 같음; 그 밖이나 읽을 수 없는 백업은 보존하고 거부)와 HEAD를 옮긴 뒤 못 맞춘 index는 다음 `publish`가 파일을 보고 마저 정리한다. 별도 index와 메시지 파일은 실행마다 git 폴더 안 새 임시 폴더에 둔다. 커밋이 실패하면 다시 실행해 커밋만 이어서 하고 reset하지 않는다. 상태 파일 `inbox/<slug>/state.md`는 사람이 읽는 본문과 엔진만 쓰는 블록(`<!-- blogkit-state v1 -->`, ```` ```json ```` 펜스 하나, `<!-- /blogkit-state -->`)으로 나뉘고, 블록 JSON은 `version: 1`과 `memo`·`review_inputs`·`learning_inputs`·`cross_review` 네 항목(단계 전에는 `null`, 뒤에는 고정 필드)만 받는다. 표식 중복·JSON 깨짐·중복 키·모르는 키나 필드·UTF-8 아님은 `state_invalid`로 멈추고 파일을 다시 쓰지 않으며, 그 밖에는 블록 밖 본문을 바이트 그대로 두고 64KB 상한·symlink 거부·원자적 저장으로 쓴다(없으면 네 항목 `null`로 만듦). 읽기·갱신은 `read_state()`·`update_state()`로 다음 명령이 쓴다. `memo-check <slug> [--confirm <digest>]`는 slug 잠금 안에서 메모(일반 파일, symlink 구성 요소 거부, 256KB, 엄격한 UTF-8)를 한 번 읽어 기밀 검사 공통 함수 `scan_text`와 메모 digest를 같은 바이트로 계산하고(금칙어 목록은 한 번 불러 검사와 목록 digest에 같이 씀), 금칙어는 `memo_blocked`(blocked로 기록), 휴리스틱 경고는 경고 묶음 digest(정렬한 `분류:파일:줄` ID의 SHA-256)를 `--confirm`으로 받아야 확인되며 다른 digest는 `confirm_mismatch`로 아무것도 쓰지 않는다. 기록은 메모 digest·경고 묶음 digest·검사기 버전(엔진 버전)·목록 digest·결과·확인 여부(경고가 없으면 true)이고, 메모·검사기 버전·목록 중 하나라도 바뀌면 무효다. 이 판정은 공개 함수 `memo_cleared()` 하나이고 맥락 로더도 같은 함수를 쓴다. 저장 직전 메모를 다시 읽어 바뀌었으면 기록하지 않는다. 출력은 메모 경로·줄·분류와 digest뿐. `record-review <slug>`는 slug 잠금 안에서 검수 결과 `review.json`·인터뷰 답 `interview.md`·근거 파일 `claims.json`(각각 일반 파일, symlink 구성 요소 거부, 256KB, 엄격한 UTF-8, 중복 키 없는 JSON)을 한 번씩 읽어 그 바이트로 검사와 digest를 계산한다. 근거 파일은 `{"claims": [...]}`이고 항목은 500개 이하, id가 서로 다르며, `fact`(id, kind, location, claim, http(s) url, excerpt, status `confirmed`·`unconfirmed`)나 `stance`(id, kind, location, claim, interview_ids)다. 검수 결과는 정확히 `content_hash`·`findings`(500개 이하, id 중복 없음, severity P0~P3, category, location, 선택 claim_ids·interview_ids)·`inputs`(interview_digest, claims_digest)이고 합계를 포함한 모르는 필드는 `review_invalid`다. P0는 엔진이 센다. 다른 해시면 `review_stale`, 입력 digest가 다르면 `review_inputs_changed`, 파일이 없으면 `review_file_missing`·`interview_missing`·`claims_missing`이고 거부하면 증거와 상태 파일은 그대로다. 같은 입력과 같은 결과면 아무것도 바꾸지 않는다(학습·승인 유지). 그 밖에는 저장 직전 내용 해시와 세 digest를 다시 확인하고(바뀌면 기록 안 함) 증거에서 게이트 4·학습·승인 기록을 지우고 M2b 형식 검수 기록을 넣어 원자적으로 저장한 뒤 상태 블록 `review_inputs`를 저장한다. `check`(게이트 4 실패)·`status`(빠진 것에 `review_inputs_changed`)·`approve`(`review_inputs_changed`)는 현재 인터뷰 답·근거 파일·검수 결과 digest가 현재 해시의 `review_inputs`와 다르면 막고, 이 판정은 공개 함수 `review_inputs_match()` 하나다(두 저장 사이에 멈춘 경우도 여기서 막히고 `record-review` 재실행이 맞춘다). `record-learning <slug>`는 slug 잠금 안에서 학습 결과 `learning.json`(같은 읽기 규칙, 64KB)을 한 번 읽어 그 바이트로 검사와 digest를 계산한다. 학습 결과는 정확히 `content_hash`·`questions`(2~20개, id 중복 없음, 각각 id·kind·question·answer·verdict, 질문·답은 빈 값 거부)이고, kind는 `concept`(핵심 개념)·`counter`(반론)로 둘 다 1개 이상, verdict는 `match`(일치)·`factual_error`(사실 오류)·`misconception`(오개념)·`stance_changed`(입장 변경 반영)다. 모르는 필드·형식 오류는 `learning_invalid`, 다른 해시는 `learning_stale`, 파일이 없으면 `learning_file_missing`이고 거부하면 증거와 상태 파일은 그대로다. 현재 해시에 게이트 1~6 통과와 P0 0개 검수 기록이 없으면 `learning_prerequisite_missing`, 검수 입력이 바뀌었으면 `review_inputs_changed`다. 통과는 엔진이 계산하고(모든 판정이 `match`나 `stance_changed`), 아니면 `passed:false`로 기록한다. 증거에 같은 학습 기록이 실제로 있고 상태 블록 `learning_inputs`의 digest도 같을 때만 아무것도 바꾸지 않고(승인 유지, 재검수로 지워진 기록은 같은 결과로 다시 씀), 그 밖에는 저장 직전 내용 해시·학습 결과 digest·검수 입력을 다시 확인하고(바뀌면 기록 안 함) 증거에서 학습·승인 기록을 지우고 M2b 형식 학습 기록을 넣어 저장한 뒤 `learning_inputs`를 저장한다. `status`(빠진 것에 `learning_inputs_changed`)·`approve`(`learning_inputs_changed`)는 학습 기록이 있는데 현재 학습 결과 digest가 현재 해시의 `learning_inputs`와 다르면 막고, 이 판정은 공개 함수 `learning_inputs_match()` 하나다(두 저장 사이에 멈춘 경우도 여기서 막히고 재실행이 맞춘다). `cross-review <slug>`는 slug 잠금 안에서 먼저 이전 결과 `inbox/<slug>/cross-review.md`를 지우고(symlink 거부), 공개본·그 글 자산·근거 파일 `claims.json`(256KB)을 검증기 읽기 규칙으로 한 번씩 읽어, 글·텍스트 자산(기밀 검사의 텍스트 종류)·근거 파일을 금칙어 목록을 한 번 불러 `scan_text`로 검사한다. 하나라도 걸리면 Codex를 부르지 않고 blocked로 기록한 뒤 `cross_review_blocked`(경로·줄·분류만)로 끝낸다. 내용 해시는 같은 바이트로 계산한다. Codex는 PATH의 `codex`, 그다음 `CODEX_APP_PATHS`(macOS 앱 번들 안 CLI) 순서로 `--version`이 0으로 끝나는 첫 번째이고, 없으면 `codex_missing`으로 skipped(재시도 없음). 검사를 통과한 바이트와 이미지 자산 바이트만 `tempfile.mkdtemp`의 새 폴더(글·자산은 레포 경로 그대로, 근거 파일은 `claims.json`)에 쓰고, 그 폴더에서 `codex exec -s read-only --skip-git-repo-check --ignore-user-config --ignore-rules --ephemeral -C <폴더> -o <폴더>/cross-review-result.md <고정 프롬프트>`를 자기 세션으로 실행한다(stdin 비움, stdout·stderr 버림, `CODEX_TIMEOUT` 10분, 끝나면 세션째 종료). 프롬프트는 엔진 상수 `CROSS_REVIEW_PROMPT`(P0 네 종류와 공개본 규칙, 마지막 줄 `VERDICT: OK`·`VERDICT: ISSUES`). 종료 코드와 결과 파일만 보고, 시간 초과 `codex_timeout`·비정상 종료 `codex_failed`·마지막 줄이 판정이 아니거나 UTF-8 아님 `codex_no_verdict`는 한 번 더 시도한 뒤 skipped로 기록한다. 실패한 시도의 결과 파일은 지우고 저장하지 않으며, 임시 폴더는 어느 경로로 끝나도 지운다. done이면 결과를 256KB(문자 경계)로 잘라 `cross-review.md`에 원자적으로 저장한다. 저장 전에 내용 해시를 다시 계산해 바뀌었으면 아무것도 기록하지 않는다. 상태 블록 `cross_review`에 내용 해시·status(done·skipped·blocked)·이유 코드를 남기고, 출력은 그 status와 이유 코드뿐이다. 발행 증거는 건드리지 않는다. `accept-proposal <ID> --digest <digest>`(사람이 실행)는 slug가 아닌 제안 ID를 받아 slug 검사 전에 문법(`^[a-z0-9][a-z0-9-]{0,63}$`)을 따로 검사하고(`proposal_id_invalid`), 블로그 레포 단위 잠금 `.blogkit/blogkit.lock`(slug 잠금과 같은 방식, `blog_locked`·`blog_lock_unreadable`, `.blogkit`이 없으면 `blogkit_folder_missing`) 안에서 돈다. 제안 `.blogkit/pending/<ID>.md`를 한 번 읽어(일반 파일, symlink 구성 요소 거부, 64KB) 같은 바이트로 digest 비교(`proposal_digest_mismatch`)와 파싱을 한다. 제안 형식(M2c-14 `/blog-learn`이 그대로 씀): 첫 줄 `---`, 다음 `---` 줄까지가 머리이고 머리의 줄마다 `key: value`(콜론 뒤 공백 하나, 빈 값 금지, 따옴표 없이 그대로), 머리 뒤는 자유 마크다운이다. 원칙(`kind: principle`)은 `statement`(원문 진술)·`interview_ids`(쉼표로 이은 서로 다른 `I-<번호>`)·`checked`(실제 날짜 YYYY-MM-DD)·`applies_when`(적용 조건)·`exceptions`(예외)·`relation`(이전 원칙과의 관계, 없으면 "없음"), 카드(`kind: card`)는 `description`(설명)·`source_url`(호스트가 있는 `https://` 공식 출처)·`first_post`(처음 나온 글의 slug)·`checked`가 모두 필수다. 첫 줄이 `---`가 아님(BOM 포함)·닫히지 않는 머리·형식에 맞지 않는 줄·빈 값·중복 키·그 종류에 없는 키는 줄 번호와 함께 `proposal_invalid`, 빠진 키는 `proposal_field_missing`, 잘못된 값은 `proposal_field_invalid`(둘 다 `field` 이름만), UTF-8 아님은 `proposal_not_utf8`이다. 목적지는 `kind`가 정한 `.blogkit/principles`·`.blogkit/cards`의 직계 `<ID>.md`뿐이고(폴더가 없으면 만들고, 폴더·목적지 symlink와 파일인 폴더는 거부), 먼저 그 ID의 남은 점 임시 파일 `.<ID>.md.tmp`를 지운다. 목적지가 있으면 내용이 같아도 `proposal_target_exists`(`same: true|false`만)로 멈추고 자동 복구는 없다(등록 뒤 삭제 전 중단도 이렇게 보이고 사람이 제안을 지운다). 폴더에 맥락 로더 상한인 200개 노트나 1000개 항목이 이미 있으면 `proposal_kind_full`. 그 밖에는 같은 폴더의 `.<ID>.md.tmp`에 바이트를 다 쓰고 fsync한 뒤 덮어쓰지 않는 `os.link`로 목적지를 등록하고 임시 파일, 그다음 제안을 지운다. 쓰는 도중 멈추면 점 임시 파일만 남고 맥락 로더는 점 파일을 읽지 않는다. 어떤 거부든 제안은 남고, 출력은 ID·종류·목적지 경로·digest·남은 임시 파일 삭제 여부뿐이다
- `tests/test_blogkit.py`: 가짜 설치 경로·레지스트리·스텁 `claude`·`git`·임시 HOME·실제 git 블로그 레포로 엔진 CLI의 신원 거부, `sync-verifier`, 사본 검사, slug 잠금, 외부 출력 비노출, 첫 변환(매핑·참조 재작성·증거 형태)과 거부 시 무변경, 부트스트랩 스크립트로 파일 이동 실패를 주입한 되돌리기·반영 직전 대상 출현 확인, 재변환 덮어쓰기 규칙, 프로세스를 죽여 확인한 중단 복구, 스텁 `node`·`npx`(미리보기 산출물 생성 포함)와 가짜 DNS·HTTP 부트스트랩으로 발행 게이트 1~6·`status`·검사 중 내용 변경, 변환부터 발행·검증기 통과까지의 전 과정, 승인·발행 거부와 승인 뒤 한 바이트 변경, 커밋 실패 뒤 재실행, 모든 하위 명령의 출력 비노출 확인, `memo-check`의 금칙어 차단·경고 확인과 잘못된 digest 거부·메모 1바이트·목록·검사기 버전 변경 시 무효·출력과 상태 파일의 금칙어 비노출·메모 symlink(경로 구성 요소 포함)·크기 초과·UTF-8 아님·폴더 거부·저장 직전 메모 변경·잠금 중 거부, `record-review`의 P0 엔진 집계와 입력 digest 기록·검수 결과와 근거 파일의 형식 오류(파싱 실패, 중복 키, 모르는 필드와 합계 필드, 잘못된 타입, ID 중복, 개수 상한)·`review_stale`·`review_inputs_changed`·`review_file_missing`·symlink·크기 초과 거부 시 증거와 상태 파일 불변·같은 입력 재실행 무변경(학습·승인 유지)·다른 검수 결과가 게이트 4·학습·승인을 지움·`record-review` 없이 써 넣은 검수 기록 무효·기록 뒤 인터뷰 답·근거 파일·검수 결과 변경이 `check`·`status`·`approve`에서 막힘·저장 직전 입력이나 내용 변경 시 기록 안 함·증거와 상태 저장 사이 중단 뒤 막힘과 재실행 복구·잠금 중 거부·출력 비노출·최악 입력 시간 제한, `record-learning`의 엔진 계산 통과와 digest 기록·학습 결과 형식 오류(질문 2개 미만, 개념·반론 질문 없음, ID 누락·중복, 빈 답, 모르는 판정·종류·필드, 20개 초과, 파싱 실패)·`learning_stale`·`learning_file_missing`·symlink·크기 초과 거부 시 증거와 상태 파일 불변·검수 전이나 게이트 실패 상태의 학습 `learning_prerequisite_missing`·검수 입력 변경 시 `review_inputs_changed`·`passed:false` 학습으로 승인 실패·같은 입력 무변경(승인 유지)·답만 바뀌고 `passed`가 같아도 승인 지움·학습 결과 파일만 바꾼 뒤와 `record-learning` 없이 써 넣은 학습 기록에서 `status`·`approve`의 `learning_inputs_changed`·같은 해시 재검수 뒤 같은 학습 결과로 기록 복구·증거와 상태 저장 사이 중단 뒤 막힘과 재실행 복구·저장 직전 학습 결과·검수 입력·내용 변경 시 기록 안 함·잠금 중 거부·출력 비노출·최악 입력 시간 제한, 자동 층 완료 경로(`memo-check` 경고 확인부터 `cross-review`·`record-learning`까지 뒤 `status`가 승인 하나만 빠짐), PATH의 스텁 `codex`(받은 인자·작업 폴더 경로와 파일 digest 기록, 성공·판정 없음·비정상 종료·시간 초과·실행 중 공개본 수정·큰 결과, 양쪽 출력에 가짜 비밀)와 가짜 앱 번들 경로(부트스트랩이 엔진 모듈의 `CODEX_APP_PATHS`·`CODEX_TIMEOUT`을 테스트 값으로 바꿔 실제 Codex는 실행되지 않음)로 `cross-review`의 시작 때 이전 결과 삭제·작업 폴더에 공개본·자산·근거 파일만·네 플래그·글·SVG·근거 파일의 가짜 금칙어 → 미실행·blocked·미설치(PATH·앱 번들 모두 없음, `--version` 실패)·앱 번들 사용·실패·판정 없음·시간 초과 → 재시도 1회 뒤 skipped·재시도 성공 시 그 결과만·256KB 자름·실행 중 내용 변경 시 기록 안 함·읽을 수 없는 입력·결과 경로 symlink·잠금·증거 불변·출력 비노출·끝난 뒤 임시 폴더 없음, 상태 파일 최초 생성 시 네 항목 `null`·블록 손상·표식 중복·스키마 위반 시 `state_invalid`와 본문 보존·블록 밖 본문 바이트 보존·최악 입력(닫히지 않는 긴 줄, 깊은 중첩, 반복 접두어) 시간 제한, `accept-proposal`의 원칙·카드 정상 이동·digest 불일치(제안이 바뀐 경우 포함)·원칙·카드 필수 필드 각각 누락·카드 URL 없음·http·호스트 없음·잘못된 종류·인터뷰 ID·날짜·첫 글 slug·빈 값·중복 키·모르는 키와 다른 종류의 키·머리 없음·닫히지 않는 머리·BOM·UTF-8 아님·64KB 초과·제안 없음·폴더인 제안 → 거부하고 블로그 레포 무변경, ID 문법 위반(`../x`, pending으로 돌아오는 `..`, 절대 경로, 대문자, 65자, 점, `.md` 접미사; 각각 경로로 이으면 통과할 가짜 제안을 둠) 거부와 64자 허용, 목적지 존재(같은 내용·다른 내용) → `proposal_target_exists`와 `same`·제안 유지, 같은 ID의 다른 종류 목적지는 무관, 종류별 200개 상한(점 파일·규칙 밖 이름은 세지 않음), `_crash` 부트스트랩(`os.link`·`os.fsync`도 감쌈)으로 링크 뒤 중단 → 재실행이 `proposal_target_exists`, 쓰는 도중 중단(fsync 전·후, 링크 전) → 점 임시 파일만 남고 맥락 로더 subprocess(draft 묶음)가 무시하며 다음 실행이 지우고 이동, 다른 ID의 임시 파일은 둠, 목적지·끊긴 목적지 링크·종류 폴더·제안·pending·`.blogkit` symlink와 파일인 종류 폴더 거부, 블로그 레포 잠금 중 거부와 죽은 프로세스 잠금 정리, `.blogkit` 없음, 제안 파서 최악 입력(닫히지 않는 긴 줄, 반복 키, 반복 표식, 긴 URL·ID 목록, 콜론 연속) 시간 제한(실제 네트워크 없음)
- `tests/test_blogkit_astro_e2e.py`: S4. `BLOGKIT_SITE_REPO`가 가리키는 블로그 레포의 HEAD 트리를 위 하네스의 임시 레포로 복사하고 `node_modules`를 연결해, 로컬 PNG·SVG 자산·다른 페이지 링크·같은 글 앵커가 있는 가짜 글을 실제 `astro check`·미리보기 빌드로(검수·학습 기록은 엔진 `record-review`·`record-learning`으로) 발행까지 보낸 뒤 실제 `npm run build`(prebuild 포함)와 사본의 `--ci --dist dist` 통과, 산출 HTML의 이미지·링크·앵커 해석, 증거를 지우면 둘 다 실패함을 확인. 변수나 `node`·`npx`가 없으면 skip(CI의 pytest 잡은 skip)
- `adapters/astro/blogkit_verify.py`: 발행 검증기 정본(stdlib). 블로그 레포는 바이트가 같은 사본을 빌드 전·배포 직전에 실행한다. 소스 모드는 공개 글(`draft: false`)마다 경로(`src/content/posts/<slug>.md`, mdx·하위 폴더 거부)·공개본 프론트매터 문법·글 전용 자산 규칙·내용 해시(`blogkit-content-v1`)·발행 증거 계약 1(지원 계약이면 과거 엔진 버전 증거도 통과)의 완전성을 확인하고, 문법 밖 파일은 `draft: true` 한 줄일 때만 초안으로 인정. `BLOGKIT_INCLUDE_DRAFTS`가 있으면 실패. 경로의 symlink 거부·읽기 크기 제한(일반 파일만, FIFO는 기다리지 않고 거부), 출력에 본문·URL 없음. `--dist <폴더>`는 소스 모드 뒤 배포 산출물을 본다: 경로 전체와 폴더 안의 symlink 거부, 일반 파일·폴더만, 개수·크기 제한, 필수 산출물, 글 페이지 slug 집합이 검증된 공개 글과 같음, RSS·사이트맵 URL의 단일 origin과 파일 대응, UTF-8이 아니거나 DOCTYPE·엔티티 선언이 있는 XML 거부, 비공개 파일 없음(inbox·`.blogkit` 폴더, `INBOX_FILES`의 모든 고정 이름, `review.md`·`approval.json`, md·mdx). 일반 파일 읽기는 연 fd를 감싸기 전에 판정해 폴더도 `not_a_regular_file`로 거부한다. `--ci`는 미리보기 변수가 빈 값이어도 실패. inbox 경로 헬퍼(`inbox_folder`·`inbox_file`·`evidence_path_for`·`draft_folder_for`·`draft_path_for`)는 slug 규칙(`valid_slug`)을 어기면 `slug_invalid`, 모르는 종류면 `inbox_file_unknown`이고, `inbox/<slug>/`의 고정 파일 이름(증거와 M2c 계약 표의 메모·상태·전제 지식·인터뷰·근거·검수·학습·교차 검토)은 `INBOX_FILES` 한 곳에 둔다. 엔진의 inbox 경로는 모두 이 헬퍼에서 얻는다. 파일 머리의 `VERIFIER_VERSION`은 엔진 버전, `SELF_DIGEST`는 그 값을 0으로 바꾼 파일의 SHA-256이고(check_secrets가 내용 digest로 판정하는 `"sha256": "<hex>"` 모양) 실행마다 확인한다(파일을 고치면 `self_digest()`로 다시 계산해 새긴다)
- `tests/test_blogkit_verify.py`: 임시 블로그 레포로 검증기 소스 모드의 통과·실패 사례, 증거 계약 위반, 자기 digest, 해시 직렬화(손으로 만든 예시와 골든 벡터), 배포 산출물의 비공개 이름(M2c inbox 파일 이름 포함), inbox 경로 헬퍼(새 inbox 파일 경로, slug 규칙 거부, 모르는 종류 거부) 확인
- `adapters/astro/blogkit_links.py`: 발행 게이트 6이 쓸 외부 링크 검사 모듈(stdlib). 설치본의 업스트림 `scripts/blog_preflight.py`(수정 없음)의 URL 형식·해석된 주소 전부 공개·연결 대상 고정·프록시 없음·자동 리다이렉트 금지·시간 제한·1바이트 읽기를 그대로 쓰고, 그 위에 자격 증명 URL 거부, 홉마다 같은 검사를 하는 리다이렉트 최대 5홉, 403·405의 HEAD 뒤 범위 GET, 결과 구분(ok, 403·405를 업스트림 형식 `external-links.allowed`에 정확히 적힌 URL만 allowed, mailto는 요청 없이 skipped, 차단은 정책 거부·영구 오류·일시 오류), 일시 오류(시간 초과·연결·DNS 실패·429·5xx)만 재시도 2회 뒤 경고를 더한다. 예외 목록 파일은 symlink 거부·64KB·UTF-8, 잘못된 항목은 줄 번호와 함께 거부. 잘못된 URL·리졸버 예외·예상 못 한 예외는 정책 거부로 끝나고, 결과는 URL을 SHA-256 앞 16자리로만 가리킨다
- `tests/test_blogkit_links.py`: S3. 가짜 resolver(`socket.getaddrinfo` 대체)·opener로 홉별 검증·최종 404·410, 사설·메타데이터·loopback 주소와 그쪽 리다이렉트, 자격 증명, 5홉 경계, 일시 오류 재시도와 회복, 정책·영구 오류 재시도 없음, 403·405 예외 목록, mailto, 잘못된 URL, 예외 목록 파일 규칙, 결과에 URL 원문 없음 확인. 네트워크 없음
- `tests/fixtures/blogkit/hash-vectors.json`: 내용 해시 골든 벡터. 한 번 생성해 고정한 값이라, 바뀌면 이미 발행한 글의 해시가 바뀐다는 뜻
- `tests/test_adapters_release_scope.py`: 업스트림 릴리스 검사기가 보지 않는 `adapters/`에 같은 금지 문자열·설치 스크립트 링크 검사를 적용하고, 업스트림 파일 변경 범위를 마일스톤 구간별로 확인한다. M2b 구간(M2b 시작 커밋 → M2c 시작 커밋)은 버전 줄·`CHANGELOG.md`·개발자 문서(`CLAUDE.md`)만, M2c 구간(M2c 시작 커밋 → 작업 트리)은 버전 줄과 정확한 경로 여섯 개(`.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `CLAUDE.md`, `CHANGELOG.md`, `skills/blog/SKILL.md`, `tests/test_skill_frontmatter.py`)만 허용한다. 임시 git 레포로 허용·거부 사례와 M2b 구간이 M2c 이후 변경에 영향받지 않음을 확인(기록된 커밋이 없는 clone은 그 구간을 건너뜀)
- `scripts/confidential_check.py`: 기밀 검사(stdlib). 금칙어 목록은 기기별 `~/.config/blog-kit/confidential.txt` 하나(경로 옵션·환경 변수 없음, symlink 거부, 일반 파일, 256KB, UTF-8 strict, `#` 줄·빈 줄 무시, 유효 항목 0개면 차단). `list`는 목록 상태만, `scan`은 파일 목록 또는 `--slug`(공개본 글과 자산 폴더, 경로·slug 규칙은 설치본 검증기에서)를 루트 안에서만(symlink 구성 요소 거부) 읽는다. md(1MB)·svg(10MB)는 NFC+casefold 부분 문자열 일치로 차단, 바이너리 이미지는 건너뛰고 목록만, 그 밖의 형식·읽기·디코딩 실패·예외는 검사 불능으로 차단. 휴리스틱(사설 IP·내부 호스트 접미사·이메일·이슈 키·로컬 경로·단위 붙은 숫자)은 경고만, 긴 줄에서도 선형 시간. 출력은 분류·파일·줄 번호뿐이고 금칙어·일치 원문·예외 메시지는 내지 않는다. 다른 모듈이 설치 경로에서 불러 쓰는 공통 함수: `scan_text(data, terms)`는 호출자가 이미 읽고 텍스트로 정한 바이트(엄격한 UTF-8)나 문자열을 확장자와 상관없이 검사해 금칙어 일치(차단)와 휴리스틱 경고를 분류·줄 번호로만 돌려준다(`scan`도 파일마다 이 함수를 쓴다). `terms_digest(terms)`·`list_digest()`는 정규화한 단어(NFC, casefold, 중복 제거, 정렬)를 줄바꿈으로 이은 바이트의 SHA-256 16진수 64자이고, 주석·빈 줄·순서·대소문자만 다르면 같다. 목록이 없거나 비면 digest 대신 차단
- `tests/test_confidential_check.py`: 임시 HOME의 가짜 금칙어로 목록 상태·일치·휴리스틱·검사 불능·slug와 파일 목록 동등·예외·긴 줄 시간 확인, 모든 출력에 가짜 금칙어·원문 없음
- `tests/test_confidential_text.py`: 기밀 검사 공통 함수를 모듈로 불러 확인. 임시 HOME의 가짜 금칙어로 `scan_text`의 분류·줄 번호 결과(md 밖 json·yaml 바이트 포함, NFD 입력, UTF-8 아닌 바이트 차단), 결과에 가짜 금칙어·원문 없음, 크기 상한(1MB) 긴 한 줄의 시간 제한, 목록 digest의 동등(주석·빈 줄·순서·대소문자·중복)·차이(단어 변경·삭제·추가)·없거나 빈 목록 차단
- `scripts/load_blogkit_context.py`: M2c 맥락 로더(stdlib). 호출자는 묶음 이름(`memo`·`interview`·`draft`·`review`·`proposal`·`cross-review`, 스펙의 단계별 입력 범위 표)과 slug만 고르고 경로는 주지 않는다. 허용 파일(루트 VOICE·BRAND·DISCOURSE, `.blogkit/profile.yaml`, `.blogkit/principles`·`.blogkit/cards`의 직계 `<ID>.md`, 검증기 `inbox_file`의 메모·전제 지식 기록·인터뷰 답·근거 파일·교차 검토 결과)만 한 번 읽어 같은 바이트로 금칙어 검사(기밀 검사의 `scan_text`)를 하고, 업스트림 로더의 펜스 함수로 감싸 내보낸다. 금칙어·BOM·UTF-8 아님은 `skipped`에 경로와 이유 코드만, 메모는 상태 파일 기록이 엔진 `memo_cleared`로 통과할 때만. symlink·일반 파일 아님은 멈추고, 파일별 상한·종류별 200개·전체 2 MB·450개를 넘으면 `context_too_large`로 아무것도 내보내지 않는다. 묶음에 필수인 파일을 건너뛰면 `required_file_skipped`. 프로필 최소판(`language`·`audience`·`default_template`·`time_budget_minutes`) 엄격 파싱, 없으면 기본값과 `profile_missing` 경고. 출력은 머리말(우선순위, 원칙은 근거가 아님, 카드 용도), 파일마다 라벨 줄과 펜스, 맨 끝 요약 표식 줄과 JSON(내보낸 바이트의 digest, 건너뛴 파일, 프로필 값, 경고). 교차 검토 묶음은 상태 블록 `cross_review`가 done이고 그 내용 해시가 설치된 검증기의 `content_hash()`로 계산한 현재 해시와 같을 때만 결과를 읽어 금칙어 검사·펜스를 거쳐 내보내고, 아니면 읽지 않고 `cross_review_not_done`(skipped·blocked·null·상태 파일 없음)·`cross_review_stale`(다른 해시)로, done인데 결과 파일이 없으면 `cross_review_missing`으로 건너뛴다(선택 단계라 필수 파일 없음). 상태 파일 손상·해시를 계산할 수 없는 공개본은 멈춘다
- `tests/test_load_blogkit_context.py`: S2. 임시 블로그 레포와 임시 HOME의 가짜 금칙어로 묶음별 허용 파일, 허용 밖·하위 폴더·점 파일 무시, symlink(파일·폴더·블로그 레포), 파일별·전체 크기·종류별 개수 상한에서 아무것도 내보내지 않음, 요약 digest와 내보낸 바이트 일치, 금칙어 파일 건너뜀과 출력 비노출, 메모 통과 조건(상태 없음·기록 없음·digest 불일치·미확인·차단·검사기 버전·목록 변경), 가짜 닫는 표식과 가짜 요약으로 펜스 탈출 불가, 프로필 정상·없음·오류, 필수 파일 누락, 교차 검토 묶음(done·해시 일치 → 내보냄, 공개본 수정·다른 해시 → `cross_review_stale`, skipped·blocked·null·상태 파일 없음 → `cross_review_not_done`, 결과 파일 없음, 금칙어, 상한, 상태 손상·공개본 없음·결과 symlink는 멈춤), 최악 입력 시간 제한 확인. 메모 통과 기록은 엔진의 `update_state`와 digest 규칙으로 준비
- `docs/BLOG-KIT-UPGRADE.md`: 블로그 레포 설정 예시와 설치·업그레이드·다운그레이드 순서(재시작 뒤 `sync-verifier` → 사본 커밋 → 설치 점검), uninstall 금지, 런타임 경로 첫 전환, 보존 확인
- `docs/BLOG-KIT-HUMAN-CHECK.md`: 두 기기 설치·전환 사람 검증 기록 양식과 단계별 기대 설치 점검 결과

## 포크 수정

업스트림 파일을 직접 고친 곳. 업스트림 병합 때 충돌 후보다.

- `.gitignore`: `/flow` 작업 폴더(`.scratch/`)와 외부 도구가 만드는 로컬 전용 Codex용 사본(`/AGENTS.md`, `/.agents/`) 제외
- `CLAUDE.md`: 에이전트 스킬 설정 절(이슈 트래커, triage 라벨, 도메인 문서) 추가. 스킬 폴더 수(33)·스크립트 수(24)와 구조도에 `blog-doctor`·`scripts/doctor.py`·`scripts/confidential_check.py`
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

## M2b 상태

M2b 시작 커밋: `6eefc10772f9d81dff88b957dab04352b3bb0e1a` (이 커밋 이후 업스트림 파일 변경 범위를 `tests/test_adapters_release_scope.py`가 확인한다)

- **구현 완료**: 엔진 커밋과 블로그 레포의 빌드 연결 커밋(prebuild 검증, 미리보기 스크립트, 검증기 사본)이 있고, 전체 자동 검증이 통과했으며, S4(`tests/test_blogkit_astro_e2e.py`)가 로컬에서 실제로 실행돼 통과했다.
- **사람 검증 대기**: 사람 작업 티켓(`v2.3.1-kit.3` 태그와 push, 블로그 레포의 ref 고정, Cloudflare 배포·미리보기 명령 변경, 일부러 실패시킨 브랜치로 배포가 멈추는지 확인, 두 기기 설치 점검)이 열려 있다.
- **검증 완료**: 위 확인 결과가 사람 작업 티켓에 있다.
- M2b 동안 실제 글 발행은 차단 상태가 정상이다. 검수(발행 게이트 4)와 학습 확인 기록을 만드는 쪽은 M2c이므로, 실제 글은 증거를 완성할 수 없다. 테스트만 그 기록을 직접 써 넣는다.

## M2c 상태

M2c 시작 커밋: `1d1fcfe6bf7a2ae9f5d8c5d5604f1c6f7a463c7a` (이 커밋 이후 업스트림 파일 변경 범위를 `tests/test_adapters_release_scope.py`가 확인하고, M2b 구간은 이 커밋에서 끝난다)

## 발행 조건의 알려진 한계

발행 게이트와 검증기가 막지 않는 것이다. 모두 의도한 범위다.

- **위조**: 같은 사용자 권한으로 도는 AI나 사람이 발행 증거 파일을 직접 써 넣는 것은 막지 않는다. 증거는 우회를 실수로 하기 어렵게 만드는 기록이지 서명이 아니다.
- **검수 입력 연결**: 인터뷰 답·근거 파일·검수 결과 digest와 상태 블록 `review_inputs`의 대조는 엔진의 `check`·`record-learning`·`status`·`approve`에만 있고, 학습 결과 digest와 `learning_inputs`의 대조는 `status`·`approve`에만 있다. 엔진 `publish`와 사이트 빌드 검증기는 발행 증거만 보고 이 연결을 다시 보지 않는다(증거 형식과 검증기 판정은 M2b 그대로).
- **해시 밖의 것**: 내용 해시는 공개본 글 파일(`draft` 줄 제외)과 그 글 전용 자산 폴더만 묶는다. 사이트 코드(레이아웃·컴포넌트·설정), 다른 글이나 공용 자산, 외부 URL이 가리키는 내용은 승인 뒤에 바뀌어도 해시가 그대로다.
- **바이너리 이미지 내용**: 기밀 검사는 md·svg 텍스트만 읽는다. PNG·JPEG 등의 픽셀과 메타데이터(EXIF 등) 안의 정보는 보지 않는다.
- **요청 수 제한(429)**: 외부 링크 검사는 HTTP 429를 시간 초과·연결 실패·5xx와 같은 일시 오류로 보고, 재시도 뒤에도 남으면 차단 대신 경고 ID로 남긴다. 그 링크가 실제로 살아 있는지는 승인할 때 경고 digest로 사람이 확인한다.
- **금칙어 변형**: 금칙어 일치는 NFC 정규화·casefold 뒤 줄 단위 부분 문자열이다. 띄어쓰기·구두점을 바꾸거나 줄을 나눈 금칙어, 다른 표기(로마자·약어)는 잡지 못한다. 휴리스틱은 경고일 뿐이다.

## 업스트림 스크립트 규칙의 포크 예외

`.github/CONTRIBUTING.md`의 스크립트 규칙 중 아래 셋은 포크가 덧붙인 스크립트(`adapters/astro/blogkit.py`·`blogkit_verify.py`·`blogkit_links.py`, `scripts/doctor.py`, `scripts/confidential_check.py`)에 적용하지 않는다.

- **입력 경로 헬퍼**: `scripts/discourse_research.py`의 `_validate_input_path`를 쓰지 않고, 모듈마다 symlink 거부·일반 파일 확인·크기 상한 읽기를 직접 한다(`O_NOFOLLOW`·`O_NONBLOCK`으로 열어 일반 파일만 상한까지 읽는다). 검증기는 블로그 레포에 바이트 사본으로 들어가 엔진 밖 모듈을 불러올 수 없고, 나머지는 FIFO에서 멈추지 않아야 하는데 헬퍼는 경로만 확인하고 여는 것은 호출자에게 맡겨 확인과 읽기 사이가 열려 있다(헬퍼 docstring도 새 호출자에게 `_read_safely`를 권한다).
- **`--format json|markdown`**: 출력은 엔진·검증기·사이트 빌드가 읽는 JSON 계약 하나이고, 사람이 읽는 형식은 스킬이 그 JSON으로 만든다.
- **좁은 예외 처리**: 외부 모듈(기밀 검사, 링크 검사)이나 설치 점검의 실패를 "검사 불능"으로 바꾸는 자리에서만 `except Exception`을 쓰고, 그 자리마다 실패가 통과가 되지 않고 오류 문구를 출력하지 않는다는 이유를 주석에 적는다.

**새 루트 파일과 업스트림 로더 허용 목록**: `.github/CONTRIBUTING.md`의 보안 기대 사항은 새 프로젝트 루트 파일을 읽으려면 `scripts/load_untrusted_root.py`의 `ALLOWED_BASENAMES`에 이름을 더하고, 그 행동 테스트와 `SECURITY.md`를 함께 고치라고 한다. 포크의 맥락 로더(`scripts/load_blogkit_context.py`)는 이 규칙의 예외다. 업스트림 로더·업스트림 테스트·`SECURITY.md`는 고치지 않고(업스트림 병합 충돌을 늘리지 않는다), 업스트림 로더의 `generate_nonce`·`fence_content`·`scan_for_injection`을 설치 경로에서 불러 같은 펜스를 쓴다(손으로 만든 펜스 없음). 행동 테스트는 `tests/test_load_blogkit_context.py`(S2)가 맡는다. 맥락 로더도 위 세 예외(입력 경로 헬퍼, `--format`, 좁은 예외 처리)를 따르되, 출력은 펜스 본문들과 맨 끝 요약 JSON 하나다(본문을 AI 문맥에 그대로 넣기 위해 JSON 문자열로 감싸지 않는다).

## 런타임 데이터 형식

M2a 동안 런타임 데이터(`CLAUDE_BLOG_RUNTIME_DIR` 아래 통합별 `.venv`·`data`, 그 안의 인증·라이브러리·브라우저 상태)의 형식과 폴더 구조를 바꾸지 않는다. 업스트림의 런타임 회귀 테스트(`tests/test_runtime_state.py`, `tests/test_runtime_fixes.py`, `tests/test_final_review_runtime_fixes.py`, `tests/test_notebooklm_wave3.py`)를 고치지 않고 유지하는 것이 이 규칙의 근거다. 그래서 A와 B 사이를 오가도 같은 런타임 폴더를 그대로 쓸 수 있다.
