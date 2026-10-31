# /blog-learn 세부 형식

`/blog-learn` 스킬이 쓰는 파일 형식과 저장 셸이다. 꺾쇠(`<...>`) 자리는 실행 때 실제 값으로 바꾼다. 쓴 파일을 다시 열어 확인하지 않는다. 형식 확인은 엔진(`record-learning`, `accept-proposal`)이 한다.

## 학습 결과 저장

```bash
BLOG="<blog repo>"
SLUG="<slug>"
LEARNING="$BLOG/inbox/$SLUG/learning.json"
DIR="$(dirname "$LEARNING")"
if [ -L "$(dirname "$DIR")" ] || [ -L "$DIR" ] || [ -L "$LEARNING" ]; then echo "symlink_refused"; exit 1; fi
TMP="$DIR/.learning.json.tmp"
rm -f "$TMP" && ( set -C; cat > "$TMP" <<'BLOGKIT_EOF'
<JSON 전체>
BLOGKIT_EOF
) && mv -f "$TMP" "$LEARNING" && echo "learning_saved"
```

구분자 줄은 따옴표로 감싸 내용이 셸에서 풀리지 않게 한다. 내용에 구분자와 같은 줄이 있으면 다른 구분자를 쓴다. 쓴 파일을 다시 열어 확인하지 않는다.

## 학습 결과 형식

엔진 `record-learning`이 받는 형식이다. 최상위 키는 정확히 `content_hash`와 `questions` 둘이다. 질문은 2~20개, ID는 겹치지 않고(영문자나 숫자로 시작, 영문자·숫자·`.`·`_`·`-`, 최대 64자), 각 질문은 정확히 `id`·`kind`·`question`·`answer`·`verdict` 다섯 키다. `kind`는 `concept`·`counter`이고 둘 다 1개 이상 있어야 한다. `question`·`answer`는 빈 값이 아니다. 합계나 `passed` 같은 다른 키를 더하지 않는다.

```json
{
  "content_hash": "<status 출력의 content_hash>",
  "questions": [
    {"id": "L-1", "kind": "concept", "question": "<핵심 개념 질문>", "answer": "<작성자의 마지막 답, 일반화한 그대로>", "verdict": "match"},
    {"id": "L-2", "kind": "counter", "question": "<반론과 질문>", "answer": "<작성자의 답>", "verdict": "stance_changed"}
  ]
}
```

## 인터뷰 답 덧붙이기

`/blog-interview`의 인터뷰 답 형식과 같다. 덧붙이기만 하고 옛 답을 지우거나 고치지 않는다. 번호는 review 묶음 인터뷰 답 펜스의 가장 큰 `I-<번호>` 다음부터 잇는다(6자리를 넘지 않음). 질문 종류는 `입장 변경`이고 `- 이전 답: I-<번호>` 줄을 둔다. 답은 학습 확인의 답을 옮기지 않고, 따로 물어 받은 일반화한 문장이다.

```bash
BLOG="<blog repo>"
SLUG="<slug>"
INTERVIEW="$BLOG/inbox/$SLUG/interview.md"
DIR="$(dirname "$INTERVIEW")"
if [ -L "$(dirname "$DIR")" ] || [ -L "$DIR" ] || [ -L "$INTERVIEW" ]; then echo "symlink_refused"; exit 1; fi
if [ ! -e "$INTERVIEW" ]; then echo "interview_missing"; exit 1; fi
cat >> "$INTERVIEW" <<'BLOGKIT_EOF'

## 실행 <YYYY-MM-DD>

### I-<번호> 입장 변경
- 결론: <어느 결정에 대한 답인지, 일반화한 한 줄>
- 이전 답: I-<번호>
- 질문: <바뀐 입장을 묻는 질문>
- 답: <작성자의 답, 일반화한 그대로>

### 열린 질문
- 없음
BLOGKIT_EOF
echo "interview_appended"
```

## 제안 파일 쓰기

```bash
BLOG="<blog repo>"
ID="<제안 ID>"
PENDING="$BLOG/.blogkit/pending"
KIT="$(dirname "$PENDING")"
case "$ID" in *[!a-z0-9-]*|-*|"") echo "proposal_id_invalid"; exit 1;; esac
if [ "${#ID}" -gt 64 ]; then echo "proposal_id_invalid"; exit 1; fi
if [ -L "$KIT" ] || [ -L "$PENDING" ] || [ -L "$PENDING/$ID.md" ]; then echo "symlink_refused"; exit 1; fi
if [ ! -d "$KIT" ]; then echo "blogkit_folder_missing"; exit 1; fi
for TARGET in "$PENDING/$ID.md" "$KIT/principles/$ID.md" "$KIT/cards/$ID.md"; do
  if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then echo "proposal_id_taken"; exit 1; fi
done
mkdir -p "$PENDING" && ( set -C; cat > "$PENDING/$ID.md" <<'BLOGKIT_EOF'
<제안 파일 전체>
BLOGKIT_EOF
) && shasum -a 256 "$PENDING/$ID.md" | cut -d ' ' -f 1
```

## 제안 파일 형식

엔진 `accept-proposal`이 받는 형식이다. 첫 줄과 머리의 끝이 `---` 줄이고, 머리의 줄마다 `key: value`(콜론 뒤 공백 하나, 빈 값 금지, 따옴표 없이 그대로, 한 줄)다. 키는 그 종류의 키만 모두 쓰고 더하거나 빼지 않는다. 머리 뒤는 자유 마크다운이다.

원칙(`interview_ids`는 쉼표로 이은 서로 다른 `I-<번호>`, `checked`는 실제 날짜):

```markdown
---
kind: principle
statement: <인터뷰 답의 원문 진술 한 줄>
interview_ids: I-3, I-7
checked: <YYYY-MM-DD>
applies_when: <적용 조건>
exceptions: <예외>
relation: 없음
---

- 처음 나온 글: <slug>
```

카드(`source_url`은 호스트가 있는 `https://` 공식 출처, `first_post`는 slug):

```markdown
---
kind: card
description: <개념의 일반적인 설명 한 줄>
source_url: https://docs.example.org/concept
first_post: <slug>
checked: <YYYY-MM-DD>
---

- 카드 후보: 전제 지식 기록의 C-<번호>
```
