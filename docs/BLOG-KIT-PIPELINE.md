# blog-kit 글쓰기 파이프라인 사용 순서

blog-kit(포크)의 M2c 파이프라인은 요약 메모에서 승인 직전까지를 세 독립 명령(`/blog-memo`, `/blog-interview`, `/blog-learn`)과 엔진 명령으로 잇는다. 설치는 [`BLOG-KIT-UPGRADE.md`](BLOG-KIT-UPGRADE.md), 포크 범위와 알려진 한계는 [`FORK.md`](../FORK.md)에 있다.

아래 `<blog repo>`는 블로그 레포 루트, `<installPath>`는 설치된 플러그인 폴더, `<slug>`는 글의 slug다. 엔진 명령은 모두 설치본에서 실행한다.

```bash
E="<installPath>/adapters/astro/blogkit.py"
```

## 1. 사용 순서

1. **`/blog-memo <slug>`** (첫 실행): `inbox/<slug>/memo.md`가 없을 때만 질문형 빈 양식을 만든다. 회사 글감 메모는 AI 세션 밖에서 직접 쓴다. 회사 정보가 없는 글감은 요청하면 AI 초안을 받아 고친다.
2. **`memo-check`** (사람, 터미널): 메모의 기밀 검사 통과를 상태 파일에 기록한다. 휴리스틱 경고가 있으면 출력의 `warnings_digest`로 확인한다. 메모를 고치면 다시 돌린다.

   ```bash
   python3 "$E" memo-check "<slug>" --blog-repo "<blog repo>"
   python3 "$E" memo-check "<slug>" --blog-repo "<blog repo>" --confirm "<warnings_digest>"
   ```

3. **`/blog-memo <slug>`** (다시 실행): 통과한 메모로 전제 지식 점검을 하고 전제 지식 기록 `inbox/<slug>/prereq.md`를 쓴다.
4. **`/blog-interview <slug>`**: 인터뷰, 리서치, 초안과 `convert`, 한국어 검수와 `record-review`·`check`, `cross-review`까지 간다. 끝은 검수를 통과한 공개본(`draft: true`)이다.
5. **`/blog-learn <slug>`**: 학습 확인과 `record-learning` 뒤, `status`의 빠진 것이 `approval_missing` 하나인지 보이고 원칙·카드 제안을 `.blogkit/pending/<ID>.md`에 쓴다.
6. **`approve`** (사람, 터미널): `status`가 보인 내용 해시와 경고 digest로 승인한다.

   ```bash
   python3 "$E" status "<slug>" --blog-repo "<blog repo>"
   python3 "$E" approve "<slug>" --blog-repo "<blog repo>" --hash "<content_hash>" --warnings "<warnings_digest>"
   ```

7. **`publish`** (사람, 터미널): `draft` 줄을 바꾸고 글과 발행 증거 두 경로만 커밋한다. 글 전용 자산 폴더는 그 전에 같은 바이트로 커밋돼 있어야 하고, 다른 것이 스테이징돼 있으면 거부한다.

   ```bash
   python3 "$E" publish "<slug>" --blog-repo "<blog repo>"
   ```

- **제안 반영** (사람, 터미널, 승인과 별개): 제안 파일을 읽고 `/blog-learn`이 보인 digest로 하나씩 옮긴다. 반영하지 않을 제안은 지운다.

  ```bash
  python3 "$E" accept-proposal "<ID>" --blog-repo "<blog repo>" --digest "<digest>"
  ```

- 공개본은 직접 고치지 않는다. 고칠 것은 초안 `inbox/<slug>/draft/<slug>.md`에서 고치고 `/blog-interview`의 초안 단계부터 다시 간다.

## 2. 프로필 최소판 `.blogkit/profile.yaml`

사람이 직접 쓰는 선택 파일이다(16 KB 이하, UTF-8). 맥락 로더가 금칙어 검사 뒤 읽는다. 아래는 가짜 예시이고 모든 키가 기본값이다.

```yaml
# blog-kit 프로필 (최소판)
language: ko
audience: 한국어 개발자
default_template: decision-record
time_budget_minutes: 20
```

| 키 | 값 | 기본값 |
|---|---|---|
| `language` | 지금은 `ko`만 | `ko` |
| `audience` | 한 줄 문자열 | `한국어 개발자` |
| `default_template` | 지금은 `decision-record`만 | `decision-record` |
| `time_budget_minutes` | 양의 정수 | `20` |

- 형식은 줄마다 `key: value`(콜론 뒤 공백 하나)다. `#`로 시작하는 줄과 빈 줄은 무시하고, 따옴표를 처리하지 않는다(값은 그대로 문자열).
- 빠진 키는 기본값을 쓴다. 파일이 없으면 기본값과 `profile_missing` 경고로 진행한다.
- 중복 키, 모르는 키, 빈 값, 형식에 맞지 않는 줄, 잘못된 숫자, 허용 밖 값은 줄 번호와 함께 `profile_invalid`로 멈춘다. 금칙어가 있으면 `required_file_skipped`로 멈춘다.
- 태그 규칙과 프로필 생성 명령은 아직 없다(M5).
