---
name: flow
description: blog-kit(claude-blog 포크 블로그 엔진) 레포에서 마일스톤 하나를 mattpocock 스킬 6단계(grill-with-docs → to-spec → to-tickets → implement → code-review → retro)로 순서대로 진행하고, 스펙 발행 전과 구현 직후에 /codex 교차 검증을 넣는다. 단계마다 결과를 보여 주고 사용자 승인 뒤에만 다음 단계로 넘어가며, 단계를 건너뛰거나 합치지 않는다. 트래커(GitHub 이슈) 쓰기·커밋은 승인 뒤에만 하고, 이 스킬 안에서는 push·PR·머지·태그·릴리스를 하지 않으며 upstream(AgriciDaniel/claude-blog)에는 어떤 쓰기도 하지 않는다.
argument-hint: [시작 단계 1~6] <마일스톤(M2 등) 또는 기능 설명 | 스펙·티켓 경로>
allowed-tools: [Skill, Read, Grep, Glob, Bash, Edit, Write, Agent]
---

# /flow — blog-kit 마일스톤: 질문 → 스펙 → 티켓 → 구현 → 리뷰 → 회고

## 절대 규칙 (위반 금지)
1. **단계 전환은 사용자 승인으로만.** 단계가 끝나면 산출물 요약과 "다음: N단계 ○○로 갈까요?"를 보여 주고 턴을 끝낸다. 승인한 다음 턴에만 다음 단계를 시작한다. "이어서 진행하겠습니다" 같은 자문자답은 승인이 아니다.
2. **단계를 건너뛰거나 합치지 않는다.** 사용자가 시작 단계를 지정하면 거기서 시작하되, 앞 단계 산출물(스펙·티켓)이 있는지 먼저 확인하고 없으면 알린다.
3. **트래커 쓰기는 승인 뒤에만.** GitHub 이슈의 생성·수정·라벨·댓글·닫기는 본문 전문을 보여 준 턴에 하지 않고, 승인한 다음 턴에만 한다. 상위(parent) 이슈를 닫거나 고치지 않는다. `.scratch/` 안의 상태 파일·초안·승인 대기 파일은 트래커가 아니라 로컬 작업 파일이다.
4. **커밋은 `/commit` 스킬 규칙으로만**(계획 제시 → 승인 → 실행. 한국어 Conventional Commits, Claude 서명 없음). 이 스킬 안에서는 `git push`(origin·upstream 모두), `gh pr create`, `gh pr merge`, `git merge`, `--amend`, `--no-verify`, `git tag`, `gh release`를 쓰지 않는다. push·태그·릴리스는 /flow가 끝난 뒤 별도 작업으로 다룬다.
5. **upstream에 쓰지 않는다.** `upstream` 원격(AgriciDaniel/claude-blog)으로 push·PR·이슈·댓글을 하지 않는다. upstream 변경을 가져오는 것(`git fetch upstream`)만 허용하고, 병합은 사용자 승인 뒤 별도 작업으로 한다.
6. **엔진에 개인 정보를 넣지 않는다.** 말투(voice)·원칙·학습 카드·회사 금칙어·devlog 글과 inbox 내용은 이 레포 코드·테스트 픽스처·문서에 넣지 않는다. 필요하면 가짜 예시를 만든다. 금칙어 목록 파일(`~/.config/blog-kit/confidential.txt`)을 읽어 출력하지 않는다.
7. **설치된 플러그인 캐시(`~/.claude/plugins/cache/…`)를 고치지 않는다.** 수정은 이 레포에서만.
8. **비밀을 내보내지 않는다.** `.env*`, `*.pem`, `*.key`, `credentials*`, 토큰·키 문자열을 스펙·티켓·리뷰 서브에이전트·Codex에 넘기지 않는다. 넘기기 전 검사는 아래 "비밀 검사"를 따른다.
9. **단계 방법은 원본 SKILL.md가 정한다.** 이 파일은 순서·관문·상태와 이 레포의 값만 정한다. 원본 내용을 기억으로 대신하지 않고, 단계를 시작할 때마다 읽는다.
10. **리뷰 지적은 보고만 한다.** 사용자가 고른 항목만 4단계 방식(TDD)으로 고친다.
11. **Codex 검증은 `/codex` 스킬로만, 정해진 두 곳에서만**(2단계 발행 전 스펙 초안, 4단계 끝 `diff BASE..HEAD`). 2단계 초안 지적은 `/codex`대로 바로 반영한다. 4단계 지적은 보고 → 사용자가 고른 것만 TDD로 고친다. 사용자가 정한 결정(계획의 K·G 결정, grill 답)과 충돌하는 지적은 반영하지 않고 보고한다.

## 이 레포의 값
| 항목 | 값 |
|---|---|
| 계획 원본 | 새 블로그 개발 계획 아티팩트 https://claude.ai/artifact/Vn8nDoncYuCd7CMHooFT86 — 1단계 전에 Artifact `read`로 읽고, 해당 마일스톤 행·관련 절(결정 K/G, 3·4·7절)만 범위로 삼는다 |
| 트래커 | GitHub Issues (`Sungrinhan/blog-kit`, `gh` CLI). 규칙은 `docs/agents/issue-tracker.md`, 라벨은 `docs/agents/triage-labels.md`. 상태 파일·스펙 초안·승인 대기 파일은 `.scratch/<slug>/`에 로컬로 둔다(커밋 안 함) |
| 테스트 환경 | `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'` (없으면 이것이 첫 티켓) |
| 테스트 | `.venv/bin/python -m pytest tests/`. 최저 지원 Python: `PATH=$PWD/.venv/bin:$PATH uv run --isolated --python 3.11 --extra dev pytest tests/`(CI는 push 때만 3.11~3.14로 돌므로, 버전 티켓의 A 확정 전에 한 번 돌린다) |
| 검증 | `export PATH=$PWD/.venv/bin:$PATH` 뒤 7종(detect-secrets와 uv가 .venv에 있다): `python scripts/validate_skills.py --root .`, `python scripts/lint_prose.py`, `python scripts/validate_public_release.py --root .`, `python scripts/consistency_check.py --root .`, `python scripts/check_secrets.py`, `uv lock --check`, `claude plugin validate .` (플러그인 이름 `claude-blog`을 유지하므로 매니페스트마다 "is reserved" 오류 1건, 마켓·플러그인 합쳐 2건이 알려진 정상). 셸은 zsh라 `$VAR`의 파일 목록이 단어로 나뉘지 않는다: 경로를 직접 적거나 xargs로 넘긴다 |
| 연결 레포 | 블로그 사이트 `~/Documents/devlog` (Sungrinhan/devlog). 엔진 설치 설정·발행 검증 같은 사이트 쪽 작업이 있다 |

## 두 레포에 걸친 티켓
- 티켓에 devlog 쪽 변경이 있으면 티켓 본문에 "devlog 변경"을 따로 적는다.
- devlog 변경은 devlog에서 `/commit` 승인 절차로 커밋하고, devlog의 `CLAUDE.md` 규칙을 따른다(예: 의존성 변경 시 `npx -y npm@10.9.2 ci` 확인).
- 상태 파일에 `DEVLOG_BASE`(시작 SHA)를 함께 기록하고, 4단계 Codex는 blog-kit `diff BASE..HEAD`와 devlog `diff DEVLOG_BASE..HEAD`를 각각 검증한다.

## 단계 표

플러그인 경로: `M=$(ls -d ~/.claude/plugins/cache/claude-plugins-official/mattpocock-skills/*/ | sort -V | tail -1)`

| 단계 | 원본(읽고 따른다) | flow가 더하는 것 | 완료 조건 |
|---|---|---|---|
| 1 grill-with-docs | `$M/skills/engineering/grill-with-docs/SKILL.md` (그 지시대로 Skill `mattpocock-skills:grilling`, `mattpocock-skills:domain-modeling` 호출) | 계획 아티팩트의 해당 마일스톤을 먼저 읽음 | frontier가 비고 사용자가 공유된 이해를 확인함. 일찍 끝내면 "미해결 결정 있음"과 목록을 상태 파일에 |
| 2 to-spec | `$M/skills/engineering/to-spec/SKILL.md` | 초안을 `.scratch/<slug>/spec-draft.md`에 쓰고 Skill `codex`로 검증 → 반영 | 시험 이음새(seam) 확인 → 본문 승인 → 다음 턴에 GitHub 이슈로 발행 |
| 3 to-tickets | `$M/skills/engineering/to-tickets/SKILL.md` | 두 레포 티켓 표시 | 분할 승인 → 다음 턴에 발행 |
| 4 implement | `$M/skills/engineering/implement/SKILL.md` (TDD는 Skill `mattpocock-skills:tdd`, 막히면 `mattpocock-skills:diagnosing-bugs`) | 버전 전용 릴리스 티켓은 보류. 나머지 티켓 커밋 뒤 Skill `codex`에 레포별 diff | 티켓마다 커밋 승인, Codex 반영분 커밋 승인 |
| 5 code-review | Skill `mattpocock-skills:code-review` (내장 `code-review`가 아님) | 고정점 = 상태 파일 `BASE`, 스펙 출처 = 상태 파일 `spec` | 고칠 항목 선택 → 고친 뒤 커밋 승인 |
| 6 retro | `$M/skills/engineering/retro/SKILL.md` | 계획 아티팩트에 반영할 변경을 후보로 | 적용할 후보 선택 → 파일 수정은 승인 다음 턴 |

## 상태 파일
`.scratch/<slug>/flow.md`. 세션이 끊기면 이 파일로 이어 간다. 상태가 바뀔 때마다(단계 완료, 승인 요청, 승인 실행) 갱신한다.
```
# flow: <마일스톤·기능 이름>
BASE: <blog-kit 시작 커밋 SHA>
DEVLOG_BASE: <devlog 시작 커밋 SHA | 해당 없음>
tracker: github
spec: <발행된 스펙 이슈 번호 #N>
step: <현재 단계 1~6>
pending: <승인 기다리는 동작 한 줄 | 없음>
pending_ref: <.scratch/<slug>/pending/<id>.md | 없음>
tickets: done=[01,02] current=03
codex: 2단계 승인(N회) | 미해결 <요약>; 4단계 …
1단계: 완료 — <산출물·미해결 결정>
...
```
- 승인을 요청할 때는 실행할 정확한 내용(본문, 스테이징할 파일 목록, 실행할 명령)을 `pending_ref`에 저장하고, 사용자에게 보여 주는 것도 그 파일 내용이다. 보여 주기 전에 비밀 검사를 한다.
- `pending`이 있을 때 사용자의 이번 입력에 따라:
  - **명시적 승인** → `pending_ref`에 적힌 그대로만 실행한다.
  - **수정 요청** → 실행하지 않는다. `pending_ref`를 고쳐 다시 보여 주고 턴을 끝낸다.
  - **거절·취소** → 실행하지 않는다. `pending`·`pending_ref`를 비우고(파일도 지운다) "취소: <동작>"을 적는다.
  - **그 밖의 입력** → 실행하지 않는다. 입력에 답하고 `pending_ref` 내용을 다시 보여 준 뒤 턴을 끝낸다.
- 실행과 부분 실패: 항목을 순서대로 실행하고 하나 끝날 때마다 결과(커밋 SHA 등)를 `완료:`로 적는다. 모두 성공하면 `pending`·`pending_ref`를 비운다. 중간에 실패하면 멈추고 `pending`을 `<동작> (부분 완료)`로 바꾸고 원인을 적은 뒤 보고한다. 명령을 파이프로 이을 때는 `set -o pipefail`로 실패를 놓치지 않는다.
- `pending_ref`가 없거나 비었으면 실행하지 않는다. 다시 작성해 승인받는다.
- 상태 파일은 커밋하지 않는다. `.scratch/`가 `.gitignore`에 없으면 사용자에게 알린다(직접 고치지 않는다).

## 비밀 검사
리뷰 서브에이전트나 Codex에 넘기기 전, 그리고 트래커 쓰기의 `pending_ref`를 보여 주기 전에 실행한다. 하나라도 걸리면 멈추고 알린다. 범위 `<R>`은 실제로 넘기는 것과 같게 한다.
- 파일 이름: `git diff --name-only <R> | grep -E '(^|/)\.env|\.pem$|\.key$|credentials'`
- 본문: `git diff <R> | grep -nE 'BEGIN [A-Z ]*PRIVATE KEY|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}'`
- 파일 대상(스펙 초안, `pending_ref`, 재검토용 `target.diff`)은 같은 본문 패턴으로 그 파일을 검사한다.

## 작업 순서

### 0단계 · 준비 (처음 호출할 때)
1. 인자를 읽는다. 마일스톤·기능이 없으면 물어본다. 상태 파일이 이미 있으면 `pending` → `step` 순서로 이어 간다.
2. 멈출 조건. 하나라도 걸리면 알리고 멈춘다.
   - `$M`이 없거나 단계 표의 원본 SKILL.md 중 하나라도 없음
   - `docs/agents/issue-tracker.md`가 없음 → "`/setup-matt-pocock-skills`를 먼저 실행해 주세요(트래커는 GitHub)"
   - `git remote get-url origin`이 `Sungrinhan/blog-kit`이 아님
   - `gh repo set-default --view`가 `Sungrinhan/blog-kit`이 아님 → `gh repo set-default Sungrinhan/blog-kit`을 실행하고 다시 확인한다(기기마다 따로 설정됨. 설정이 없으면 `gh`가 upstream을 대상으로 잡는다)
   - `git status --short`가 깨끗하지 않음 → 그대로 진행할지 묻는다
3. 시작 단계별 전제를 확인하고 `BASE`(와 devlog를 건드리는 마일스톤이면 `DEVLOG_BASE`)를 정한다.
   - 1~4단계 시작: `BASE`는 `git rev-parse HEAD`
   - 5단계 시작: 기존 상태 파일의 `BASE` 또는 사용자가 준 고정점과 스펙 출처가 필요하다. `git diff BASE...HEAD`가 비면 멈춘다.
   - 6단계 시작: 회고할 세션이 필요하다.
4. 상태 파일에 적고 해당 단계를 시작한다.

### 단계마다 공통
- 시작할 때 원본 SKILL.md를 읽는다(규칙 9).
- 끝나면 상태 파일을 갱신하고, 요약과 다음 단계 질문을 보여 준 뒤 **턴을 끝낸다**(규칙 1).
- 임시 worktree·clone은 scratchpad의 새 경로로 만들고, 그 안에서도 되돌리기·삭제 명령(`git reset --hard`, `rm -rf`) 대신 새 경로를 하나 더 만든다. 만든 임시 경로는 상태 파일에 적어 마무리에서 보고하고, 정리는 승인 뒤 `git worktree remove`로 한다. 정리 명령이 훅에 보류되면 다시 시도하지 않고 멈춰 보고한다.

### 단계별 추가 지시 (원본과 다르게 하는 것만)
- **1단계**: 계획 아티팩트를 읽고 해당 마일스톤의 내용·완료 기준·관련 결정을 grill의 출발점으로 쓴다. 계획의 결정(K·G)은 다시 묻지 않는다.
- **2단계**: 초안 → 비밀 검사 → Skill `codex`(초안 경로) → 반영 → 본문 전문과 Codex 결과를 보여 주고 `pending`에 적는다 → 승인한 다음 턴에 GitHub 이슈로 발행하고 `spec`에 이슈 번호를 적는다. 발행 뒤 `spec-draft.md`는 지운다.
- **3단계**: 분할 승인과 발행 승인은 서로 다른 턴이다. devlog 변경이 있는 티켓은 표시한다.
- **4단계**:
  - 티켓을 시작하기 전에 스펙의 Testing Decisions(승인된 seam)를 확인한다. 없거나 모호하면 테스트를 쓰기 전에 묻는다.
  - 티켓을 시작할 때 `CLAUDE.md`의 Development Rules와 `.github/CONTRIBUTING.md`의 스크립트·보안 규칙을 읽는다. 새 입력 경로(파일·폴더 인자, 읽는 설정 파일)에는 크기 제한과 symlink 거부를 두고 행동 테스트로 확인한다. 본문·파일 내용을 훑는 정규식과 스캐너에는 크기 상한의 최악 입력(긴 한 줄, 닫히지 않는 표식, 반복 접두어)으로 시간 제한 테스트를 둔다.
  - 구현 중 발견한 범위 밖 문제는 스펙 문장·표준 근거와 대조한다. 스펙이 요구하면 그 티켓에서 구현하고, 아니면 상태 파일의 `이월:` 목록에 적어 마무리에서 보고한다.
  - 버전만 올리는 릴리스 티켓은 보류한다. 4단계 Codex와 5단계 선택 수정이 모두 커밋된 뒤 A(버전을 올리기 직전 커밋) 확정·전체 검증 → 버전 커밋 → 깨끗한 트리에서 전체 검증 → 연결 레포 사본이 있으면 커밋된 정본과 cmp 일치를 확인하고 연결 레포에서 따로 커밋 → 최종 SHA를 상태 파일에 적는다. 버전 커밋에는 버전 문자열을 담은 파일(버전 표면, 포크 파일의 버전 상수와 그에 따라 바뀌는 자체 digest)과 그 버전의 CHANGELOG 항목만 넣는다. 다운그레이드 확인용 B를 따로 두는 마일스톤이면 B에는 버전 표면과 CHANGELOG 한 줄만 넣는다.
  - 원본 끝의 "code-review를 불러 리뷰하라"와 "현재 브랜치에 커밋하라"는 따르지 않는다.
  - 티켓 하나마다 `실패하는 테스트 → 통과(테스트 명령) → /commit 계획 제시 → 승인 → 커밋`. 커밋되면 `tickets`를 갱신한다. 처음부터 통과한 테스트(앞 조각 구현이 이미 막는 경우)는 그 테스트가 지키는 코드를 깨는 변이로 red를 확인하고 원복한다(원복은 cmp로 원본과 일치 확인). 살아남은 변이는 테스트를 보강할 신호다.
  - frontier(다음 티켓)는 상태 파일의 `tickets`가 기준이다. 티켓 이슈의 상태(닫기·댓글)는 바꾸지 않고, 마무리에서 한꺼번에 제안한다.
  - 모든 티켓이 커밋되면 레포별 비밀 검사 → Skill `codex`에 blog-kit `diff BASE..HEAD`(devlog 변경이 있으면 devlog `diff DEVLOG_BASE..HEAD`도 따로). 프롬프트에 표준 출처(`CLAUDE.md` Development Rules, `.github/CONTRIBUTING.md`)와 `FORK.md`의 승인된 예외를 넘겨 적용 여부도 보게 한다. 회차마다 지적 보고 → 사용자가 고른 것만 실패하는 테스트 → 고침 → 통과 → 재검토.
  - 루프가 끝나면 반영분을 `/commit` 승인 절차로 커밋한다.
- **5단계**: 리뷰 호출에 고정점(`BASE`)과 스펙 출처(`spec`)를 함께 넘긴다. 호출 전에 비밀 검사(`BASE...HEAD`). 선택 수정 커밋이 끝나면(고칠 항목이 없어도) 보류한 버전 전용 티켓을 4단계 규칙대로 커밋한다.
- **6단계**: 후보 제시까지만. 계획 아티팩트 갱신이 필요하면 후보로 낸다. 고른 후보의 수정은 변경 내용을 보여 준 다음 턴에 한다.

### 마무리
- 상태 파일 경로, 만든 티켓과 커밋 목록(레포별), 남은 리뷰·Codex 지적을 보고한다.
- 트래커에 반영할 것(완료된 티켓 이슈 닫기·댓글)이 있으면 목록으로 제안하고, 승인한 다음 턴에만 실행한다(규칙 3).
- push·태그·릴리스가 필요하면 목록으로 제안만 하고, 승인한 다음 턴에 별도 작업으로 한다(규칙 4).

## 예시
- 좋은 예: `/flow M2` → 0단계 준비 → 계획 아티팩트의 M2 행을 읽고 1단계 질문 시작
- 좋은 예: `/flow 4 #12` → 스펙 이슈와 하위 티켓 이슈를 확인한 뒤 구현부터
- 좋은 예: devlog의 `.claude/settings.json` 티켓 → devlog에서 `/commit` 승인 후 커밋, 상태 파일에 devlog SHA 기록
- 나쁜 예: 티켓을 끝내고 `git push origin main` ← 규칙 4 위반
- 나쁜 예: upstream에 버그 PR을 올림 ← 규칙 5 위반
- 나쁜 예: 한국어 채점 테스트 픽스처로 devlog의 실제 인터뷰 답변을 복사 ← 규칙 6 위반(가짜 예시를 만든다)
- 나쁜 예: 스펙 승인을 받은 턴에 티켓까지 발행 ← 규칙 1·3 위반
- 나쁜 예: 티켓을 끝낼 때마다 GitHub 이슈를 바로 닫음 ← 규칙 3 위반
- 나쁜 예: 리뷰 지적을 전부 바로 수정 ← 규칙 10 위반
