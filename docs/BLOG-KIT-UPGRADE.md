# blog-kit 설치·업그레이드·다운그레이드 절차

blog-kit(포크)이 지원하는 설치는 하나다: 블로그 레포의 공유 설정에 마켓 `Sungrinhan/blog-kit`을 릴리스 태그로 고정하고, 그 레포에서 `claude-blog@blog-kit`을 project 범위로 설치한다. 업스트림 마켓 설치, user 범위 설치, 독립 설치 스크립트(`install.sh`, `install.ps1`)는 지원하지 않는다. 지원 범위와 병합 절차는 [`FORK.md`](../FORK.md)에 있다.

아래 `<blog repo>`는 블로그 레포 루트, `<version>`은 플러그인 버전(예: `2.3.1-kit.1`), `<runtime root>`는 기기별 런타임 폴더의 절대 경로다.

## 1. 블로그 레포 공유 설정 (커밋한다)

`<blog repo>/.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "blog-kit": {
      "source": {
        "source": "github",
        "repo": "Sungrinhan/blog-kit",
        "ref": "v<version>"
      }
    }
  },
  "enabledPlugins": {
    "claude-blog@blog-kit": true
  }
}
```

- `ref`는 항상 `v<version>` 태그다. 브랜치(`main`)나 커밋 SHA를 쓰지 않는다. 태그는 다시 지정하지 않는다.
- 이 파일의 `ref`가 블로그 레포가 고정한 엔진 버전의 유일한 출처다.

## 2. 기기별 제외 설정 (커밋하지 않는다)

`<blog repo>/.claude/settings.local.json` (블로그 레포의 `.gitignore`가 이미 제외한다):

```json
{
  "env": {
    "CLAUDE_BLOG_RUNTIME_DIR": "<runtime root>"
  }
}
```

- `<runtime root>`는 절대 경로이고, `~`, `$VAR`, `..`을 쓰지 않는다.
- Claude 플러그인 폴더(`~/.claude/plugins`, `CLAUDE_CONFIG_DIR`가 있으면 그 아래 `plugins`) 밖에 둔다. 플러그인 캐시는 버전마다 폴더가 바뀌고, 플러그인 영구 데이터는 uninstall 때 지워질 수 있다.
- 예: `/Users/<you>/.local/share/blog-kit/runtime`. 이 폴더 아래에 통합별(`blog-audio`, `blog-google`, `blog-notebooklm`) `.venv`와 `data`가 생긴다.
- 기기마다 따로 정한다. 설정을 바꾸면 Claude Code를 다시 시작해야 실행 환경에 들어간다.

## 3. 설치·업그레이드·다운그레이드 순서

설치, 업그레이드(A에서 B), 다운그레이드(B에서 A)는 모두 같은 순서다. 바뀌는 것은 1번의 `ref`뿐이다.

1. **ref 변경 커밋**: 블로그 레포 공유 설정의 `ref`를 목표 태그로 바꾸고 블로그 레포에 커밋한다.
2. **마켓 갱신**: 블로그 레포에서 `claude plugin marketplace update blog-kit`.
3. **재설치**: 처음이면 `claude plugin install claude-blog@blog-kit --scope project`, 이미 설치되어 있으면 `claude plugin update claude-blog@blog-kit --scope project`. 설치 점검의 `d_version`이 목표 버전이 아니면 `claude plugin install claude-blog@blog-kit --scope project`를 다시 실행한다.
4. **세션 재시작**: 블로그 레포에서 Claude Code를 완전히 끄고 다시 연다.
5. **검증기 사본 동기화**: 설치본의 엔진 CLI로 블로그 레포의 검증기 사본(`<blog repo>/scripts/blogkit_verify.py`)을 새 버전의 정본으로 바꾼다. 설치 신원(a~f)이 모두 맞아야 실행되고, 사본이 어긋난 상태에서도 실행된다.

   ```bash
   python3 "<installPath>/adapters/astro/blogkit.py" sync-verifier --blog-repo "<blog repo>"
   ```

6. **사본 커밋**: 바뀐 `<blog repo>/scripts/blogkit_verify.py`를 블로그 레포에 커밋한다. 엔진은 커밋하지 않는다. 엔진 CLI가 없는 버전(`2.3.1-kit.2` 이하)으로 바꿀 때는 5·6번이 없다.
7. **설치 점검**: `/blog-doctor`를 실행하거나, 설치본의 스크립트를 직접 실행한다.

   ```bash
   python3 "<installPath>/scripts/doctor.py" --blog-repo "<blog repo>"
   ```

   전부 `ok`이고 종료 코드 0이어야 한다. `d_version`은 목표 버전, `e_commit`의 `commit`과 `tag_commit`은 목표 태그의 커밋이다. `i_verifier_copy`만 `fail`이면 5번부터 다시 한다. 그 밖의 검사가 `fail` 또는 `unknown`이면 그 이유를 고친 뒤 3번부터 다시 한다.

되돌리기(다운그레이드)도 위 순서 그대로, 1번에서 이전 태그로 바꾼다.

이 순서의 명령은 사람 검증(두 기기 설치·전환 확인)에서 실제 동작을 확인한다. 동작이 다르면 이 문서를 고친다.

## 4. uninstall 금지

전환에 `claude plugin uninstall`을 쓰지 않는다. uninstall은 플러그인 영구 데이터를 지울 수 있다. 꼭 필요하면 먼저 다음을 백업한다.

- 런타임 폴더 `<runtime root>` 전체
- 블로그 레포의 프로젝트 MCP 설정 `<blog repo>/.mcp.json`
- 플러그인 영구 데이터 폴더(`~/.claude/plugins/data/` 아래 이 플러그인 폴더). 업스트림 권장 런타임 위치가 여기 있다

## 5. 런타임 경로 첫 전환

기존 런타임은 경로를 바꿔도 상태를 복사하지 않는다. `CLAUDE_BLOG_RUNTIME_DIR`를 처음 정하거나 바꾸기 전에 다음을 한다.

1. **실제로 쓰던 위치를 확인한다.** 후보는 셋이다.
   - 기존 환경 변수 override: 셸 설정이나 다른 Claude 설정에 이미 `CLAUDE_BLOG_RUNTIME_DIR`가 있으면 그 값의 `<root>/<integration>/`
   - 플러그인 영구 데이터 아래 업스트림 권장 폴더: `~/.claude/plugins/data/<이 플러그인 폴더>/claude-blog-runtime/<integration>/`
   - 설치된 스킬 폴더 안 기본 위치: `<installPath>/skills/<integration>/` 아래 `.venv`, `data`
2. **통합별 `data`를 복사한다.** 찾은 위치의 `data` 폴더(인증, 라이브러리, 브라우저 상태)를 `<runtime root>/<integration>/data`로 복사한다. 통합마다 따로 한다.
3. **`.venv`는 옮기지 않는다.** 안의 절대 경로가 이전 위치를 가리킨다. 새 위치에서 각 통합의 명시적 setup으로 다시 만든다.

   ```bash
   CLAUDE_BLOG_RUNTIME_DIR="<runtime root>" python3 "<installPath>/skills/<integration>/scripts/setup_environment.py"
   ```

4. **원본과 백업을 남겨 둔다.** 새 위치에서 runner 오프라인 확인(6절)이 통과한 뒤까지 원래 위치와 백업을 지우지 않는다.
5. 기존 상태가 없으면 새 경로만 만든다.

## 6. 보존 확인

전환이 런타임 데이터를 건드리지 않았는지 전환마다 기록한다.

- **표식 파일**: 첫 설치 직후 `<runtime root>/blog-kit-marker.txt`에 비어 있지 않은 가짜 내용(예: `blog-kit marker 1`)을 둔다. 실제 비밀이나 개인 정보를 넣지 않는다. 전환마다 `shasum -a 256 "<runtime root>/blog-kit-marker.txt"`의 결과가 처음과 같은지 기록한다.
- **runner 오프라인 확인**: 통합마다 네트워크 없이 도는 확인이 통과하는지 기록한다. `setup_environment.py --check --json`은 폴더와 의존성 표식만 보고, `run.py --help`는 가상환경을 보기 전에 끝나므로 그것만으로는 부족하다. 아래처럼 `run.py`에 실제 스크립트를 넘겨 새 위치의 가상환경 Python이 그 스크립트의 도움말을 실행하는지(종료 코드 0) 확인한다. 가상환경이 없거나 의존성이 맞지 않으면 runner가 설치하지 않고 실패한다.

  ```bash
  R="<runtime root>"; I="<installPath>/skills"
  CLAUDE_BLOG_RUNTIME_DIR="$R" python3 "$I/blog-audio/scripts/setup_environment.py" --check --json
  CLAUDE_BLOG_RUNTIME_DIR="$R" python3 "$I/blog-audio/scripts/run.py" generate_audio.py --help
  CLAUDE_BLOG_RUNTIME_DIR="$R" python3 "$I/blog-google/scripts/setup_environment.py" --check --json
  CLAUDE_BLOG_RUNTIME_DIR="$R" python3 "$I/blog-google/scripts/run.py" google_auth.py --help
  CLAUDE_BLOG_RUNTIME_DIR="$R" python3 "$I/blog-notebooklm/scripts/setup_environment.py" --check --json
  CLAUDE_BLOG_RUNTIME_DIR="$R" python3 "$I/blog-notebooklm/scripts/run.py" notebook_manager.py --help
  ```

  쓰지 않는 통합(setup을 한 적 없는 통합)은 "setup 안 함"으로 기록한다.

기록 양식은 [`BLOG-KIT-HUMAN-CHECK.md`](BLOG-KIT-HUMAN-CHECK.md)에 있다.
