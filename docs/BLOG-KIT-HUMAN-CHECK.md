# blog-kit 사람 검증 기록 양식

두 기기에서 커밋 A로 설치하고, B로 업그레이드하고, A로 다운그레이드하고, B로 돌아가며 매번 설치 점검을 한다. 절차는 [`BLOG-KIT-UPGRADE.md`](BLOG-KIT-UPGRADE.md)를 따른다. 이 양식을 복사해 채우고 사람 작업 티켓에 댓글로 남긴다.

공개 이슈에 남기므로 설치 점검 JSON은 그대로 붙인다(출력이 허용된 필드만 담는다). 환경 변수, 설정 파일의 다른 내용, 토큰은 붙이지 않는다.

## 대상 커밋

| 항목 | A | B |
|---|---|---|
| 플러그인 버전 | `2.3.1-kit.1` | `2.3.1-kit.2` |
| 태그 이름 | `v2.3.1-kit.1` | `v2.3.1-kit.2` |
| 커밋 SHA (40자) | | |
| 태그 push 확인 (`git ls-remote origin refs/tags/<태그>`) | | |

## 블로그 레포 설정 전문

ref A일 때 `<blog repo>/.claude/settings.json` 전문:

```json
```

ref B일 때 전문:

```json
```

기기별 `.claude/settings.local.json`은 `CLAUDE_BLOG_RUNTIME_DIR` 한 줄만 적는다(값은 기기마다 다르다).

## 기기별 기록

기기마다 아래 표 하나를 채운다. 단계는 1 A 설치, 2 B 업그레이드, 3 A 다운그레이드, 4 B 복귀다.

### 기기 1: `<기기 이름>`

| 항목 | 1 A 설치 | 2 B 업그레이드 | 3 A 다운그레이드 | 4 B 복귀 |
|---|---|---|---|---|
| 블로그 레포 ref 변경 커밋 SHA | | | | |
| 마켓 갱신 결과 (`claude plugin marketplace update blog-kit`) | | | | |
| 재설치 명령과 결과 | | | | |
| 세션 재시작 | | | | |
| 설치 점검 결과 (status, 종료 코드) | | | | |
| 표식 파일 SHA-256 | | | | |
| runner 오프라인 확인 (audio, google, notebooklm) | | | | |

설치 점검 JSON (단계별):

```json
```

### 기기 2: `<기기 이름>`

(기기 1과 같은 표와 JSON 칸)

## 단계별 기대 설치 점검 결과

모든 단계에서 `status`는 `ok`, 종료 코드는 0, 여덟 검사(`a_shared_settings`부터 `h_runtime_dir`까지)가 모두 `ok`다. 단계마다 달라지는 값은 다음과 같다.

| 단계 | `a_shared_settings.ref` | `d_version.version` | `e_commit.commit` = `tag_commit` |
|---|---|---|---|
| 1 A 설치 | `v2.3.1-kit.1` | `2.3.1-kit.1` | A의 SHA |
| 2 B 업그레이드 | `v2.3.1-kit.2` | `2.3.1-kit.2` | B의 SHA |
| 3 A 다운그레이드 | `v2.3.1-kit.1` | `2.3.1-kit.1` | A의 SHA |
| 4 B 복귀 | `v2.3.1-kit.2` | `2.3.1-kit.2` | B의 SHA |

`h_runtime_dir.runtime_dir`는 기기마다 같은 값이 네 단계 내내 유지되어야 한다. 표식 파일 해시는 1단계에서 기록한 값과 네 단계 모두 같아야 하고, runner 오프라인 확인은 네 단계 모두 통과해야 한다.
