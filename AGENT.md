# AGENT.md

- LLM 에이전트용 문서로 개요, 구조, 실행 방법, 선정 기준은 [README.md](./README.md)
- 이 문서에는 코드만 읽어서는 알 수 없는 규칙, 이유, 연동 관계만 기록함


## 작업 규칙

- 선정 기준이나 매체 조정은 리포트 목적(README "선정 기준"의 선정 관점)에 맞는지로 판단. 프롬프트에 사용자 개인 정보 금지
- README는 사람용으로 뼈대만 유지, 상세 내용은 이 문서. 예외로 점수 산정 방식은 README "선정 기준"에만 자세히 쓰고, 이 문서에는 코드 쪽 주의만. 두 문서에 같은 내용 중복 금지
- README 상단(제목 ~ 실행 방법)은 사용자가 직접 관리. 요청 없이 수정 금지
- 동작을 바꾸면 해당 내용이 적힌 문서도 갱신
- 문서 문체: `-` 기호와 짧은 문장, 명사형 종결 ("~함", "~처리")
- 건수, 간격, 점수 같은 수치는 문서에 적지 않고 변수, 상수 이름으로 가리킴 (사용자가 값을 자주 바꿈). "3줄 요약"만 예외
- 외부 서비스의 한도처럼 이쪽에서 바꿀 수 없는 수치는 확인 날짜와 함께 기록
- 뉴스 소스, 키워드, 리포트 형식은 코드가 아니라 설정 파일에서 관리 (파일 위치는 README "폴더 구조")
- 매체 사이트 요청은 `common.fetch()`만 사용. 동시 요청 금지, 요청 간격 유지. 확인용 실험에도 적용
- 기사 본문을 리포트에 싣지 않음 (제목, 원문 링크, 요약만)
- 의존성은 uv로 관리 (`uv add`, `uv lock`). `requirements.txt` 사용 안 함
- `.env` 커밋 금지. `.env.example`은 두지 않음
- 프롬프트는 `fill_prompt`로 채움. JSON 예시의 중괄호 때문에 `str.format` 사용 금지
- 기사 내용은 프롬프트의 `<후보목록>`, `<기사>` 안에만 넣고, 그 안의 지시문을 따르지 않게 하는 문구 유지
- 모델 출력은 `clean_inline`을 거쳐 기록


## 도입하지 않기로 한 것

- DB: 필요한 상태는 단계 간 전달과 중복 방지뿐, `archive/logs/` 파일로 충분
- DLQ: 실패는 로그에 남고 같은 날 재실행하면 이어서 처리
- 헤드리스 브라우저: 실행 시간, 복잡도 증가. 접근을 차단하는 매체는 목록에만 올림
- 구글 뉴스: 링크가 중계 주소라 본문 수집 불가. 원문 링크를 주는 매체 RSS만 사용
- 기사별 개별 호출 채점: 호출 수가 후보 수만큼 늘어남. 후보 전체를 한 번 호출로 보내 기사마다 항목별 점수를 받음
- 요약 기사별 호출: 하루 요청 한도를 빨리 소진함. 요약 대상 전체를 한 번 호출로 보내고 실패한 기사만 다시 보냄
- 사전 필터에서 발행 시각, 매체별 과거 선정률로 자르기: 2026-10-01~07 로그로 확인하니 중요한 기사가 빠짐 (수집 기간이 길었던 날 특히)
- Job 통합: 단계별 성공, 실패 확인과 API 키 주입 범위 제한을 위해 단계별 Job 유지


## 함께 바꿔야 하는 곳

| 바꾸는 것 | 함께 수정 |
|---|---|
| 프롬프트의 출력 형식 | `RANK_SCHEMA` / `SUMMARY_SCHEMA`, `score_articles` / `split_answers`, `parse_summary` |
| 중요도 항목 추가, 삭제 | README "선정 기준", `evaluate.CRITERIA`, `RANK_SCHEMA`, `config/selection.yml`의 `criteria_weights` |
| `filter_prompt.txt`의 점수 기준, 제외 기준, 같은 사건 기준 문구 | README "선정 기준"의 항목 표와 제외 목록 |
| `evaluate.prefilter`, `total_score`, `decide`의 순서나 계산 | README "선정 기준"의 해당 단계 |
| `config/selection.yml` (사전 제외 단어, 주제 가중치, 항목 가중치) | 지난 로그로 선정 기사가 빠지지 않는지 확인 (`tools/check_prefilter.rb`, 로컬). 제외 단어 묶음 이름을 바꾸면 README "선정 기준" |
| `src/*.py`의 선정, 요약 로직 | 로컬 검증용 Ruby 이식본 `tools/rb/`, `tools/test/` (커밋 안 함, 인수인계 참고) |
| `[SUMMARY]` 블록의 소제목, `#논조` 표기 (`summarize.parse_summary`) | `publish.parse_summary_blocks` |
| 요약 응답 형식 (`summarize.parse_summary`) | `manual_summary.py`도 같은 함수로 검증하므로 작업 파일의 `answer` 형식 안내(모듈 설명) |
| 주간 리포트에 넘기는 값 (`weekly.render_weekly`) | `config/weekly.md.j2` 맨 위 주석 |
| 템플릿에 넘기는 값 (`publish.render_report`) | `config/report.md.j2` 맨 위 주석. 정의되지 않은 이름은 오류 (`StrictUndefined`) |
| 환경 변수 추가, 기본값 변경 | 코드 기본값과 워크플로우 `env:` 양쪽, 아래 표 |
| 오류 줄 형식 (`[... 오류] 대상 - 사유`), 본문 수집 실패 사유 문구 | `common.read_errors`, `publish.collect_stats` |
| 로그 태그, 파일명 | 워크플로우의 `^\[TITLE\] ` 개수 세기와 `${RUN_DATE}-수집.txt` 경로 |
| 블록 태그 추가 | `common.BLOCK_TAGS` |
| 단계 추가, 실행 순서, 실패 시 흐름 | 워크플로우의 Job과 `src/main.py` 양쪽 (같은 흐름을 따로 구현) |
| 결과 폴더 이름 (`archive`, `archive-test`) | `common.DATA_DIR`, `.gitignore`, 워크플로우의 artifact 경로, 수집 건수 확인, `git add archive/`, README "폴더 구조" |
| `config/feeds.yml`, `config/keywords.yml`의 항목 구조 | `collect.check_config` |
| 주제 우선순위 | `config/keywords.yml`의 `topics` 순서, README "한눈에 보기" |


## 테스트

- 자동 테스트: `uv run pytest` (`tests/`). Gemini와 매체 요청은 가짜 함수로 바꾸고, 결과 폴더는 `PIPELINE_DATA_DIR`로 임시 폴더를 씀. 코드, 설정을 push하면 `test.yml`이 같은 테스트를 실행
- 동작을 바꾸면 해당 테스트도 함께 고치거나 추가
- 로컬에 python이 없을 때: 선정, 요약 로직을 Ruby로 옮긴 `tools/rb/`와 같은 경우의 테스트 `tools/test/`로 확인 (`ruby tools/test/test_evaluate.rb`, `test_summarize.rb`). Python을 바꾸면 Ruby도 맞출 것
- 실제 실행 테스트는 테스트 모드로 (실행 방법은 README "로컬 실행")
- 테스트를 처음부터 다시 하려면 `archive-test/`를 지우고 실행 (같은 날 재실행은 이어서 처리하므로)
- 결과 경로는 실행 위치가 아니라 `src/`의 상위 폴더 기준
- 테스트 모드의 수집 건수는 `COLLECT_LIMIT`
- 실제 Gemini 테스트는 하루 요청 한도를 같이 씀. 자동 실행 몫을 남겨 둘 것. 한도의 하루는 태평양 시간 자정에 초기화되므로 한국 오후(16~17시) 이후의 호출은 다음 날 09:00 실행과 같은 한도를 씀 (Gemini API rate-limits 문서, 2026-10-09 확인)
- 실제 호출 검증: 지금 선정 방식(사전 필터 + 항목별 점수)은 자동 실행에서 아직 성공한 적 없음. 2026-10-08 자동 실행은 응답 잘림으로 실패해 로컬에서 복구. `PREFILTER_MAX` 규모 호출이 `GEMINI_THINKING_BUDGET` 기본값으로 통과하는지 다음 자동 실행에서 확인. 선정 품질은 아직 판단 전 ("알려진 한계" 참고)


## 환경 변수

| 이름 | 사용 위치 | 설명 |
|---|---|---|
| `GEMINI_API_KEY` | common | 필수. GitHub에서는 Secret |
| `GEMINI_MODEL` | common | 선정, 요약 모델 |
| `GEMINI_MIN_INTERVAL` | common | 호출 사이 최소 간격(초). 무료 요금제의 분당 한도에 맞춘 값, 유료면 0 |
| `GEMINI_MAX_RETRIES` | common | `call_gemini` 안의 백오프 재시도 횟수 (주간 요약만 사용) |
| `GEMINI_RETRY_BASE` | common | 백오프 첫 대기(초). 이후 2배씩 |
| `GEMINI_TIMEOUT` | common | 응답 대기 상한(초) |
| `GEMINI_MAX_OUTPUT_TOKENS` | common | 응답 최대 길이(토큰). 기본값은 gemini-2.5-flash의 출력 상한 (2026-10-09 확인) |
| `GEMINI_THINKING_BUDGET` | common | 생각 토큰 상한. 0이면 끔, 음수면 설정을 보내지 않음(모델 기본 동적 사고). 모델을 바꾸면 함께 확인 |
| `RUN_DATE` | common | 로그, 리포트 파일명 날짜 (YYYY-MM-DD). 없으면 로컬 시간 |
| `REQUEST_INTERVAL` | common | 매체 사이트 요청 간격(초) |
| `COLLECT_MAX` | collect | 하루 수집 상한 |
| `TEST_MODE` | common, collect | 테스트 모드. 결과 폴더를 `archive-test/`로 바꾸고 수집 건수를 `COLLECT_LIMIT`로 제한 |
| `PIPELINE_DATA_DIR` | common | 결과 폴더를 지정한 경로로 바꿈. 자동 테스트 전용 |
| `WEEKLY_TRENDS` | weekly | 주간 리포트의 최대 흐름 수 |
| `COLLECT_TEST_MODE`, `COLLECT_LIMIT` | collect | 수집 건수만 제한 (결과 폴더는 그대로). 워크플로우의 수동 테스트 실행에서 사용 |
| `LIST_N` | evaluate | 목록 건수 |
| `MIN_SCORE` | evaluate | 목록에 올릴 최소 중요도 (0~10) |
| `PREFILTER_MAX` | evaluate | 사전 필터 뒤 Gemini에 보낼 최대 후보 수 |
| `TOP_N` | evaluate | 요약 건수 |

- 현재 값은 워크플로우 `env:`(GitHub 기본값), `.env`(로컬), 코드 기본값에서 확인. 워크플로우 `env:`에 없는 변수는 코드 기본값 사용
- 로컬은 `.env` (이미 설정된 환경 변수는 덮어쓰지 않음). GitHub은 Secret(`GEMINI_API_KEY`)과, 워크플로우 `env:`에 `vars.`로 적힌 변수만 Variable로 바꿀 수 있음. 나머지(`GEMINI_MAX_RETRIES`, `GEMINI_RETRY_BASE`, `GEMINI_TIMEOUT` 등)는 코드 기본값


## 단계별 동작

공통: `COLLECT_MAX`, `LIST_N`, `TOP_N`은 하루 기준. 같은 날 재실행하면 Collect부터 다시 돌아 새 기사만 추가하고, 이미 기록된 건수만큼 자리를 줄여 이어서 처리

### 1. Collect (`src/collect.py`)

- 수집 조건 (모두 충족): 발행일시가 수집 기간(아래) 안, 링크가 `http(s)://`, 제목 또는 RSS 요약에 `ai_terms` 포함(또는 `standalone_topics`에 적힌 주제의 키워드 포함), 중복 이력에 없는 링크
- `standalone_topics`(보안, 신기술)는 AI 용어가 없어도 수집. 이 주제의 국문 키워드는 부분 문자열로 비교하므로 일상어와 겹치는 단어(`공격`, `유출` 등)는 넣지 않음
- 중복 이력: 모든 날짜의 필터링 로그(선정 단계를 거친 기사)와 오늘 수집 로그. 수집만 되고 선정을 거치지 못한 기사는 이력에 넣지 않음 (선정이 실패한 날의 후보가 다음 실행에서 다시 수집되도록)
- 수집 기간: 기본은 지난 `WINDOW_HOURS` + `WINDOW_GRACE_HOURS`. 직전 실행이 그보다 앞이면 "직전 실행 시각 - `WINDOW_GRACE_HOURS`"부터 (`get_window_start`). 최대 `WINDOW_MAX_HOURS`
  - 이유: 실행 시각이 GitHub 예약 지연으로 날마다 달라 고정 기간이면 실행 간격이 기간보다 길 때 빈 시간이 생김
  - 직전 실행: 오늘 이전에 필터링 로그에 `[LINK]` 레코드가 있는 마지막 날(선정까지 끝난 날)의 수집 로그 첫 `[수집 파이프라인 실행]` 시각. 선정이 실패한 날은 그날 후보가 중복 이력에 없으므로 건너뜀
  - `[수집 파이프라인 실행]` 시각은 한국 시간으로 기록 (실행 환경 시간대와 무관)
  - `WINDOW_GRACE_HOURS`는 발행 후 피드에 늦게 올라오는 기사 대비. 겹친 기사는 링크 중복 제거로 걸러짐
- 발행일시를 읽지 못한 기사는 제외 (피드별 건수 출력)
- 시간대: `naive_kst: true` 피드는 시간대 표기 없는 한국 시간이라 9시간 보정. 날짜 문자열에 시간대 표기가 있으면 보정 안 함. `KST`로 끝나는 날짜는 직접 해석
- 키워드 일치: 영문은 앞뒤가 영문자가 아닐 때만 (복수형 `s`, `es` 허용), 국문은 부분 문자열
- 제목 중복: 제목이 거의 같은 기사(`TITLE_SIMILARITY` 이상)는 먼저 나온 것만 남김. 설정 파일에서 앞에 적힌 매체가 우선. 같은 언어끼리만 걸러지고, 국문, 영문으로 나뉜 같은 사건은 선정 단계가 거름
- 주제: `topics`에 적힌 순서가 우선순위, 일치 없으면 `일반`. 상한 초과 시 주제 우선순위 -> 최근 순으로 남김
- 중복 이력은 레코드 단위로 읽음 (본문 안에 적힌 `[LINK]` 줄은 무시)
- 실패: 피드 하나 실패는 재시도 없이 `[수집 오류]` 기록 후 계속 (피드 형식이 아닌 응답 포함). 전부 실패하면 `fail()`
- 새 기사가 없고 그날 수집 이력도 없으면 `[STATUS] NO_ARTICLES` 기록

### 2. Evaluate (`src/evaluate.py`)

- 점수 산정 방식(사전 필터, 항목별 점수, 합계, 목록 결정)은 README "선정 기준". 아래는 코드 쪽 주의와 이유만
- 대상: 오늘 수집 로그 중 오늘 필터링 로그에 링크가 없는 기사
- 함수: 사전 필터 `prefilter`, 점수 `score_articles`(Gemini 한 번), 합계 `total_score`, 결정 `decide`. `prefilter`와 `decide`는 호출 없음
- 항목별 점수 + 코드 가중합으로 바꾼 이유 (LLM-as-a-Judge의 `final_eval_score` 방식): 이전 방식(모델이 30건을 골라 총점 0~10)은 점수가 9~10에 몰려 상위 기사가 사실상 모델 나열 순서로 정해졌음
- 응답 검증: 범위 밖 id, 같은 id 두 번째, 점수가 숫자가 아닌 항목은 버림. 점수는 1~5로 맞춤. 자기 자신이나 범위 밖을 가리키는 `dup_of`는 무시. 점수를 하나도 못 받으면 형식 오류
- 기록: 목록 기사는 필터링 로그에 `[SCORE]`(README "합계 계산"의 N/10 값, `log_score`). 예전 로그, 리포트, 주간 요약과 같은 0~10 척도를 유지하기 위함. 나머지 후보는 `[STATUS] SKIP (결정)`
- 번역 제목(`title_ko`)을 점수 응답에서 함께 받아 `[TITLE_KO]`로 기록. 호출을 늘리지 않고 요약하지 않는 목록 기사의 제목을 번역하기 위함. 원문 제목에 한글이 있거나 원문과 같으면 버림
- 본문: `trafilatura` -> `BODY_MIN_LENGTH` 미만이면 `BODY_SELECTORS`로 보완 -> `BODY_MAX_LENGTH`까지, 문장 끝에서 자름. `BODY_MIN_LENGTH` 미만이면 `[평가 오류]` 기록
- 호출 상한: 첫 호출 포함 `RANK_MAX_CALLS`번 (`score_with_retry`). 선정은 하루 한 번이라 실패하면 그날 리포트가 없음
  - 일시 오류(503 과부하 등): `RANK_RETRY_WAITS`만큼 기다렸다 다시 (2026-10-06 503으로 그날 선정 실패)
  - 응답 형식 오류(해석 실패, 점수 0건): 바로 다시
  - 다시 시도하지 않는 실패: 하루 요청 한도 초과, 잘못된 요청, 잘린 응답 (`common.is_retryable`). 2026-10-08 잘린 응답을 4번 다시 보내 한도만 씀
  - 실패한 시도마다 `[평가 오류]` 줄을 남김 (통계 `rank_failed_calls`). 나중에 성공하면 `rank_ok`는 1. 끝내 실패하면 `fail()`
  - 기다리는 시간과 호출 시간이 Evaluate Job의 `timeout-minutes` 안에 들어와야 함

### 3. Summarize (`src/summarize.py`)

- 대상: 오늘 필터링 로그에서 `[BODY]`가 있고 오늘 요약 로그에 없는 기사. 전체를 한 번 호출로 보냄 (`summarize_all`, 기사마다 `<기사 id="번호">` 블록)
- 응답: 기사별 객체의 JSON 배열, `id`로 기사와 맞춤 (`split_answers`). 프롬프트에 기사끼리 내용을 섞지 말라는 규칙 유지
- 검증: `summary_ko` 3줄, `lang`이 `en`이면 `summary_en`도 3줄, 말줄임표만 있는 줄은 불인정. 기사 단위로 검사해 맞는 기사는 기록
- 다시 요청: 형식 오류, 응답에 빠진 기사, 호출 오류인 기사만 모아 번호를 새로 붙여 다시 보냄. 요약 단계의 호출은 첫 호출 포함 `SUMMARY_MAX_CALLS`번까지
  - 일시 오류(503 과부하 등)는 `get_retry_wait`만큼 점점 길게 기다렸다 다시. 형식 오류는 바로 다시
  - 다시 시도하지 않는 실패: 요약 불가, 하루 요청 한도 초과, 잘못된 요청, 잘린 응답 (`common.is_retryable`)
- `error`가 있으면 요약 불가로 처리, 재요청 없음. `error`를 필수 키로 바꾸지 말 것 ("없음" 같은 값이 요약 불가로 오인됨)
- 선정 이유(`{reason}`)는 초점 참고용. 본문에 없는 내용을 가져오지 않게 하는 프롬프트 문구 유지
- 국문 문장은 해라체("~했다", "~한다")로 통일. 프롬프트에 규칙이 없으면 기사마다 합니다체가 섞여 나옴 (요약, 주간 요약 프롬프트 모두)
- `[SUMMARY]` 블록 형식
  - 영문: `**[기사 제목 번역]**`, `**[영문 3줄 요약 (Original)]**`, `**[국문 3줄 요약 (Translated)]**`, `#논조`
  - 국문: `**[국문 3줄 요약]**`, `#논조`
  - 그 밖: `**[기사 제목 번역]**`, `**[국문 3줄 요약]**`, `#논조`
- 실패: 요약 불가는 바로, 나머지는 상한까지 요청해도 요약하지 못한 기사만 마지막 사유로 `[요약 오류]` 기록 (다른 기사로 보충하지 않음). 다시 시도해 성공한 기사는 오류 줄을 남기지 않음. 전부 실패하면 `fail()`
- 리포트의 요약 상태 표시(README "리포트 구성")는 `summary_status`가 정함: 요약 실패는 호출 실패, 요약 불가는 오류 사유가 `요약 불가:`로 시작, 요약 대기는 시도하지 못함
- 요약 로그의 `[MODEL]`에 요약한 모델을 기록. `manual`이면 수동 요약으로 표시
- 상태 표시는 `[...]` 형식 유지. 블로그(Jekyll)가 `{{ }}`를 Liquid 문법으로 해석해 지워 버리므로 중괄호 표기 금지
- 다시 요약: Gemini로는 `RUN_DATE=날짜`로 summarize -> publish 재실행. Gemini 없이는 `manual_summary.py export` -> 작업 파일의 `answer` 채움 -> `import` (같은 `parse_summary` 검증을 거침). 작업 파일의 prompt는 자동 요약과 같은 프롬프트에 기사 하나만 넣은 것이고, `answer`는 객체나 객체 하나짜리 배열 모두 받음

### 4. Publish (`src/publish.py`)

- 요약 기사는 오늘 요약 대상(본문 있는 상위 기사), "더 읽어볼 기사" 목록은 오늘 필터링 로그의 `[TITLE]` 있는 기사 중 요약 기사를 뺀 나머지 (요약 실패 기사도 위에 나오므로 목록에서 제외). 둘 다 `[SCORE]` 내림차순으로 재정렬 (재실행으로 뒤에 추가된 기사 반영)
- 리포트 `archive/news/YYYY/MM/YYYY-MM-DD.md`는 매번 새로 씀. 요약과 목록이 모두 없으면 만들지 않음
- 중요도 옆 문장은 요약이 아니라 선정 이유(`reason`)
- 번역 제목: 요약 기사는 요약 응답의 `**[기사 제목 번역]**`이 우선, 요약이 없으면 필터링 로그의 `[TITLE_KO]`. 목록 기사는 `[TITLE_KO]`만 사용 (2026-10-01 이전 로그에는 없음)
- "더 읽어볼 기사"는 기사당 한 줄: `\[번역\] [번역 제목](링크) (첫 주제) - 출처`. 번역 제목이 있으면 원문 제목 대신 싣고, 국문 기사는 원문 제목만. 발행일시는 싣지 않음
  - `[번역]`의 대괄호는 `\`로 막음. 그대로 두면 Jekyll(kramdown)이 바로 뒤 `[제목]`과 묶어 참조 링크로 해석할 수 있음
  - 제목 안의 대괄호는 링크 표기와 겹치므로 소괄호로 바꿈 (원문, 번역 제목 모두)
- 논조는 태그만 있는 줄일 때만 인식 (예전 로그의 `(논조 태그: #중립)` 포함)
- `index.md`: 없으면 생성, 있으면 `### 리포트 목록` 아래에 링크 추가 (중복 추가 안 함)


## Gemini 호출 (`common.call_gemini`)

- `temperature` 0 고정. `schema`를 주면 JSON 응답 강제
- 출력 한도 `GEMINI_MAX_OUTPUT_TOKENS`, 생각 토큰 상한 `GEMINI_THINKING_BUDGET`. 생각 토큰도 출력 한도에 들어감
  - 2026-10-08 선정(후보 200건)이 4번 모두 잘림. 같은 후보로 재현: 제한 없음은 생각 50,303 + 응답 15,219에서 143건째 잘림. 0(끔)은 응답 21,868로 성공. 8192는 생각 8,188 + 응답 21,981로 성공(121초)
  - 끄면 점수가 크게 달라짐(제한 없음과 상위 15건 겹침 6, 8192는 10). 그래서 끄지 않고 상한만 둠
- 빈 응답은 오류(`ValueError`), 출력 한도에서 잘린 응답은 `TruncatedResponse`
- 재시도하지 않는 경우: `NO_RETRY_CODES`(잘못된 요청, 인증 실패 등), 하루 요청 한도 초과(오류 메시지의 `PerDay`), 잘린 응답(같은 요청은 또 잘림)
- 백오프: `GEMINI_RETRY_BASE`초에서 시작해 매번 2배, 지터 추가. 오류 메시지의 `retry in Ns`가 더 길면 그 값
- 하루 호출 수: 보통 선정 한 번 + 요약 한 번. 최악은 선정 `RANK_MAX_CALLS`번 + 요약 `SUMMARY_MAX_CALLS`번. 무료 요금제는 하루 요청 한도가 있어(2026-09-30 확인 당시 모델당 20회) 재실행과 재시도까지 합쳐 이 안에 들어와야 함
- `max_retries`: 기본은 `GEMINI_MAX_RETRIES`. 선정, 요약은 0으로 불러 단계에서 전체 호출 수를 관리 (재시도가 곱으로 늘지 않게)


## 로그 형식

- 파일: `archive/logs/YYYY/MM/YYYY-MM-DD-{수집|필터링|요약}.txt`
- 점수표: `archive/logs/YYYY/MM/YYYY-MM-DD-점수.csv` (Evaluate, UTF-8 BOM). 후보마다 한 줄 (열은 `evaluate.SCORE_COLUMNS`): 실행 시각, 결정(값은 README "선정 기준"의 "기록과 기준 변경"), 합계, 항목별 점수, 같은 사건 링크, 제외 사유, 선정 이유, 출처, 발행, 주제, 제목, 번역 제목, 링크. 같은 날 재실행하면 이어 씀
- 통계: `archive/stats/YYYY.csv` (연도별 파일). Publish가 그날의 세 로그를 세어 하루 한 줄로 기록 (단계별 성공, 실패 건수, 실패한 매체). 같은 날 재실행하면 그날 줄을 바꿔 씀. 수집 0건으로 Publish가 돌지 않은 날에는 줄이 없음
- 현황 화면은 원본 로그가 아니라 연도별 통계 파일만 읽으면 되도록 유지. 모든 해의 열 구성은 같게 유지하고, 열을 추가할 때는 맨 뒤에 (`publish.STATS_COLUMNS`)
- 레코드는 `---` 한 줄로 구분. 한 줄 태그는 `[TAG] 값`
- 블록 태그 `[SUMMARY]`, `[BODY]`는 다음 줄부터 `---` 전까지. 블록 안의 `---` 줄은 `- - -`로 바꿔 기록
- `read_records`는 `[LINK]`가 있는 레코드만 반환. 키는 태그의 소문자

```text
[TITLE] 기사 제목
[TITLE_KO] 번역 제목     (필터링 로그, 국문 기사는 없음)
[LINK] https://...
[PUBLISHED] 2026-09-30 18:22 KST
[SOURCE] 매체 이름
[TOPIC] 에이전트, 개발
[SCORE] 9            (필터링, 요약 로그)
[REASON] 선정 이유    (필터링, 요약 로그)
[MODEL] 모델 이름     (요약 로그. 수동 요약은 manual)
[SUMMARY]            (수집: RSS 요약 / 요약: 검증한 요약)
...
[BODY]               (필터링: 요약 대상 기사의 본문)
...
---
```

| 그 밖의 레코드 | 로그 | 의미 |
|---|---|---|
| `[수집 파이프라인 실행] 시각` | 수집 | 실행 시작 표시 (한국 시간). 다음 실행의 수집 기간 기준 |
| `[수집 오류] URL - 사유` | 수집 | 피드 요청, 파싱 실패 |
| `[STATUS] NO_ARTICLES` | 수집 | 조건에 맞는 기사 0건 |
| `[LINK]` + `[STATUS] SKIP (결정)` | 필터링 | 목록에 오르지 않은 기사. 결정은 점수표와 같음 (2026-10-08 이전은 `SKIP (Score: -)`). 같은 날 재실행에서 목록이 이미 찼으면 `SKIP (목록 마감)` (점수표에는 없음) |
| `[평가 오류] 대상 - 사유` | 필터링 | 선정 호출 실패 또는 본문 수집 실패 |
| `[요약 오류] 링크 - 사유` | 요약 | 호출 실패, 형식 오류, 요약 불가 |

- 2026-09-30까지의 로그는 예전 형식 (`[TOPIC]`, `[SCORE]` 없음. `[SOURCE]`는 09-29부터 일부 있음)
- 2026-06-29 ~ 09-30 리포트는 구글 뉴스 기반 이전 방식. 건수가 일정하지 않고 중요도 순이 아님


## 주간 요약 (`src/weekly.py`)

- 대상: 지정한 ISO 주(기본은 실행 날짜 기준 지난주 월~일)의 요약 로그. 요약이 있는 기사만
- 새로 기사를 읽지 않고, 이미 만든 국문 요약과 점수만 모아 Gemini 한 번 호출 (`weekly_prompt.txt`, `TREND_SCHEMA`)
- 응답 검증: 존재하지 않는 기사 ID는 버리고, 근거 기사가 없는 흐름은 제외
- 결과: `archive/weekly/YYYY-Www.md` (`config/weekly.md.j2`), 목록 `archive/weekly/index.md`
- 요약 기사가 없는 주는 만들지 않음. 호출 실패는 `fail()`


## 워크플로우 (`.github/workflows/ai-news-pipeline.yml`)

- 워크플로우 네 개: 일일 실행(`ai-news-pipeline.yml`), 주간 요약(`weekly-summary.yml`), 실행 누락 확인(`check-run.yml`), 자동 테스트(`test.yml`)
- 일일 실행과 주간 요약은 같은 `concurrency` 그룹이라 겹치면 차례로 실행 (커밋 충돌 방지)
- 실행 누락 확인: 그날 통계 줄이 `archive/stats/YYYY.csv`에 없으면 실패 처리해 GitHub 알림 발송. 수집 0건인 날도 줄이 없어 알림이 감 (드문 경우라 허용)
- 자동 테스트는 코드, 설정, 테스트, 의존성, 워크플로우가 바뀐 push에만 실행 (매일의 `archive/` 자동 커밋에는 실행 안 함)

- Job 간 전달: `archive/logs/`를 artifact로 (`logs-collect` -> `logs-evaluate` -> `logs-summarize`). 업로드는 모두 `overwrite: true` (실패한 Job만 재실행 가능). Evaluate와 Summarize의 업로드는 `if: always()`라 스크립트가 실패해도 로그를 넘김
- 실행 날짜는 Collect Job이 한 번 정해 `RUN_DATE`로 전달 (자정 전후에도 같은 날짜 파일). `TZ=Asia/Seoul`
- Collect가 `[TITLE]` 개수를 세어 `has_articles` 출력. `false`이거나 Collect가 실패하면 이후 Job 전부 건너뜀 (Publish 포함, 그날 로그는 커밋되지 않음)
- Evaluate가 실패하면 Summarize는 건너뜀. Publish는 `always()`로 실행해 그때까지의 결과와 로그 커밋
- Publish는 artifact를 collect -> evaluate -> summarize 순으로 받아 나중 것이 덮어씀
- Publish는 실행 시작 커밋이 아니라 그 시점의 `main`을 checkout (`ref: main`). Re-run은 시작 커밋을 다시 받으므로, 선정 실패 날 1차 Publish가 커밋한 로그, 통계와 `git pull --rebase`에서 충돌했음 (2026-10-06). 단, Re-run은 시작 커밋의 workflow 파일을 쓰므로 이 수정 전에 시작된 실행에는 적용 안 됨
- `needs` 컨텍스트는 직접 의존하는 Job만 담으므로, `if:`에서 참조하는 Job은 `needs:`에 명시
- 수동 실행의 `test_mode`는 수집 건수만 줄이고, Publish에서 커밋을 건너뛰고 리포트를 `test-report` artifact로 올림 (저장소에 남지 않음)
- 기본 권한 `contents: read`, Publish만 `contents: write`. API 키는 Evaluate, Summarize에만 주입
- 커밋은 변경이 있을 때만, `git pull --rebase` 후 push
- Job 실패 시 GitHub 알림 메일 발송


## 알려진 한계

| 한계 | 개선 방향 |
|---|---|
| 대량 후보 일괄 채점 품질 미검증 (목록 중간 기사를 놓칠 수 있음) | 주제별 예선 후 결선 |
| 선정 입력이 후보 수 x 미리보기 길이만큼 커져 분당 토큰 한도 초과 가능 | `PREVIEW_LENGTH` 축소 |
| 무료 요금제의 하루 요청 한도. 같은 날 여러 번 실행하면 요약 도중 막힘 | 재실행 횟수 관리, `TOP_N` 조정, 유료 전환 |
| 주제 분류가 넓음 (제목, RSS 요약에 단어만 있어도 분류) | 키워드 정비, 제목 위주 매칭 |
| 일부 매체 본문 수집 불가 (OpenAI 등 HTTP 403) | RSS에 담긴 본문 활용 |
| 요약 실패 시 빈자리 미보충 (요약이 `TOP_N`보다 적어질 수 있음) | 다음 순위 기사로 보충 |
| 선정 실패한 날의 후보 중 다음 실행의 수집 기간을 벗어난 기사는 유실 | 같은 날 수동 재실행 |
| VentureBeat 수집 실패 (HTTP 429 잦음) | 대체 피드 확보 |
| 일부 매체가 robots.txt에 AI 수집 봇 지목 | 해당 매체는 본문 수집 없이 목록만 |
| 요약 대상 본문이 필터링 로그로 공개 저장소에 커밋됨 | 커밋 전 `[BODY]` 제거 |
| 블로그 연동 없음 | 블로그 저장소로 push |
