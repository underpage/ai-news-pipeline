# AGENT.md

- LLM 에이전트용 문서. 개요, 구조, 실행 방법, 선정 기준(점수 산정)은 [README.md](./README.md)
- 코드만 읽어서는 알 수 없는 규칙, 이유, 연동 관계만 기록함. 경위와 수치는 git 기록


## 작업 규칙

- 선정 기준이나 매체 조정은 리포트 목적(README "선정 기준"의 선정 관점)에 맞는지로 판단. 프롬프트에 사용자 개인 정보 금지
- 점수 산정 방식은 README "선정 기준"에만 자세히, 이 문서에는 코드 쪽 주의만. 두 문서에 같은 내용 중복 금지
- README 상단(제목 ~ 실행 방법)은 사용자가 직접 관리. 요청 없이 수정 금지
- 동작을 바꾸면 해당 내용이 적힌 문서도 갱신
- 문서 문체: `-` 기호와 짧은 문장, 명사형 종결. 수치는 적지 않고 변수, 상수 이름으로 가리킴("3줄 요약"만 예외). 바꿀 수 없는 외부 한도는 확인 날짜와 함께
- 뉴스 소스, 키워드, 리포트 형식은 설정 파일에서 관리 (위치는 README "폴더 구조")
- 매체 사이트 요청은 `common.fetch()`만 사용. 동시 요청 금지, 요청 간격 유지 (확인용 실험 포함)
- 기사 본문을 리포트에 싣지 않음 (제목, 원문 링크, 요약만)
- 의존성은 uv (`uv add`, `uv lock`). `requirements.txt`, `.env.example` 두지 않음. `.env` 커밋 금지
- 프롬프트는 `fill_prompt`로 채움 (JSON 예시의 중괄호 때문에 `str.format` 금지)
- 기사 내용은 프롬프트의 `<후보목록>`, `<지난리포트>`, `<기사>`, `<기사목록>`(주간) 안에만 넣고, 그 안의 지시를 따르지 않게 하는 문구 유지
- 모델 출력은 `clean_inline`을 거쳐 기록


## 도입하지 않기로 한 것

- DB, DLQ: 상태는 `archive/logs/` 파일로 충분. 실패는 로그에 남고 같은 날 재실행하면 이어서 처리
- 헤드리스 브라우저: 실행 시간, 복잡도 증가. 접근 차단 매체는 목록에만
- 구글 뉴스: 링크가 중계 주소라 본문 수집 불가
- 기사별 개별 호출(채점, 요약): 하루 요청 한도를 빨리 소진. 전체를 한 번 호출로 보냄
- 사전 필터에서 발행 시각, 매체별 과거 선정률로 자르기: 중요한 기사가 빠짐 (2026-10-01~07 로그로 확인)
- Job 통합: 단계별 성공, 실패 확인과 API 키 주입 범위 제한을 위해 단계별 Job 유지


## 함께 바꿔야 하는 곳

| 바꾸는 것 | 함께 수정 |
|---|---|
| 프롬프트의 출력 형식 | `RANK_SCHEMA` / `SUMMARY_SCHEMA`, `score_articles` / `split_answers`, `parse_summary` |
| 중요도 항목, 점수 범위 | README "선정 기준", `evaluate.CRITERIA`, `CRITERIA_MAX`, `CRITERIA_KO`, `WEIGHTED_CRITERIA`, `config/selection.yml`의 `criteria_weights`, `penalties`, `@흥미기사/feedback_server.rb`(점수표 열 이름) |
| 제외 사유 이름 | `filter_prompt.txt`의 [제외 표시], `evaluate.EXCLUDE_REASONS`, README "선정 기준" |
| 대표 분야 이름 | `filter_prompt.txt`의 분야 설명과 예시, `evaluate.FIELDS`(`RANK_SCHEMA`의 enum), `config/selection.yml`의 `field_cap` 주석, README "선정 기준" |
| `filter_prompt.txt`의 점수, 제외, 같은 사건, 지난 보도, 대표 분야 기준 문구 | README "선정 기준"의 항목 표와 제외 목록 |
| `evaluate.prefilter`, `total_score`, `decide`의 순서나 계산 | README "선정 기준"의 해당 단계 |
| `config/selection.yml` | 지난 로그로 선정 기사가 빠지지 않는지 확인 (`tools/check_prefilter.py`). 제외 단어 묶음 이름은 README "선정 기준" |
| `[SUMMARY]` 소제목, `#논조` 표기 (`summarize.parse_summary`) | `publish.parse_summary_blocks` |
| 요약 응답 형식 | `manual_summary.py`의 `answer` 형식 안내 (같은 `parse_summary`로 검증) |
| 템플릿에 넘기는 값 (`publish.render_report`, `weekly.render_weekly`) | `config/report.md.j2`, `config/weekly.md.j2` 맨 위 주석 (`StrictUndefined`) |
| 환경 변수 추가, 기본값 변경 | 코드 기본값과 워크플로우 `env:` 양쪽, 아래 표 |
| 오류 줄 형식, 본문 수집 실패 사유 문구 | `common.read_errors`, `publish.collect_stats` |
| 로그 태그, 파일명 | 워크플로우의 `^\[TITLE\] ` 개수 세기와 `${RUN_DATE}-수집.txt` 경로 |
| 블록 태그 추가 | `common.BLOCK_TAGS` |
| 단계 추가, 실행 순서, 실패 시 흐름 | 워크플로우의 Job과 `src/main.py` 양쪽 (같은 흐름을 따로 구현) |
| 결과 폴더 이름 (`archive`, `archive-test`) | `common.DATA_DIR`, `.gitignore`, 워크플로우의 artifact 경로, 수집 건수 확인 경로, `git add archive/`, README "폴더 구조" |
| `config/feeds.yml`, `config/keywords.yml`의 항목 구조 | `collect.check_config` |
| 주제 우선순위 | `config/keywords.yml`의 `topics` 순서, README "한눈에 보기" |


## 테스트

- `uv run --locked pytest` (`tests/`). 로컬에서는 `--python 3.13` 지정 (uv 기본 3.15에는 pyyaml wheel이 없어 설치 실패, 2026-10-16 확인). Gemini, 매체 요청은 가짜 함수, 결과 폴더는 `PIPELINE_DATA_DIR`. push하면 `test.yml`이 실행. 동작을 바꾸면 테스트도 함께
- 실제 실행 테스트는 테스트 모드 (README "로컬 실행"). 결과는 `archive-test/`, 처음부터 다시 하려면 지우고 실행. 결과 경로는 `src/`의 상위 폴더 기준
- 실제 Gemini 호출은 자동 실행과 하루 요청 한도를 같이 씀. 자동 실행 몫을 남길 것. 한도는 태평양 시간 자정에 초기화되므로 한국 오후(16~17시) 이후의 호출은 다음 날 자동 실행과 같은 한도를 씀 (Gemini rate-limits 문서 2026-10-09 확인)


## 환경 변수

| 이름 | 사용 위치 | 설명 |
|---|---|---|
| `GEMINI_API_KEY` | common | 필수. GitHub에서는 Secret |
| `GEMINI_MODEL` | common | 선정, 요약 모델. gemini-2.5-flash는 새 사용자에게 막혀(2026-10-10 테스트 키로 확인) 기본값을 gemini-3.8-flash로 |
| `GEMINI_MIN_INTERVAL` | common | 호출 사이 최소 간격(초). 무료 요금제의 분당 한도 기준, 유료면 0 |
| `GEMINI_MAX_RETRIES` | common | `call_gemini` 안의 백오프 재시도 횟수. 주간 요약만 사용 |
| `GEMINI_RETRY_BASE` | common | 백오프 첫 대기(초). 주간 요약만 사용 |
| `GEMINI_TIMEOUT` | common | 응답 대기 상한(초) |
| `GEMINI_MAX_OUTPUT_TOKENS` | common | 응답 최대 길이(토큰). gemini-2.5-flash의 출력 상한(2026-10-09 확인), 3.8-flash에서도 동작 확인(2026-10-12) |
| `GEMINI_THINKING_BUDGET` | common | 생각 토큰 상한. 0이면 끔, 음수면 설정을 보내지 않음. 모델을 바꾸면 함께 확인 |
| `RUN_DATE` | common | 로그, 리포트 파일명 날짜 (YYYY-MM-DD). 없으면 로컬 시간 |
| `REQUEST_INTERVAL` | common | 매체 사이트 요청 간격(초) |
| `TEST_MODE` | common, collect | 결과 폴더를 `archive-test/`로, 수집을 `COLLECT_LIMIT`건으로 |
| `COLLECT_TEST_MODE`, `COLLECT_LIMIT` | collect | 수집 건수만 제한 (결과 폴더는 그대로). 워크플로우 수동 테스트 실행용 |
| `PIPELINE_DATA_DIR` | common | 결과 폴더 경로. 자동 테스트 전용 |
| `COLLECT_MAX` | collect | 하루 수집 상한 |
| `PREFILTER_MAX` | evaluate | 사전 필터 뒤 Gemini에 보낼 최대 후보 수 |
| `LIST_N`, `TOP_N`, `MIN_SCORE` | evaluate | 목록 건수, 요약 건수, 목록에 올릴 최소 중요도(0~10) |
| `WEEKLY_TRENDS` | weekly | 주간 리포트의 최대 흐름 수 |

- 현재 값은 워크플로우 `env:`, `.env`(로컬), 코드 기본값 순으로 확인. `.env`는 이미 설정된 환경 변수를 덮어쓰지 않음
- GitHub에서 바꿀 수 있는 것은 Secret과 워크플로우 `env:`에 `vars.`로 적힌 변수뿐. 나머지는 코드 기본값


## 단계별 동작

- `COLLECT_MAX`, `LIST_N`, `TOP_N`은 하루 기준. 같은 날 재실행하면 Collect부터 다시 돌아 새 기사만 추가하고, 기록된 건수만큼 자리를 줄여 이어서 처리

### 1. Collect (`src/collect.py`)

- 수집 기간: 기본 기간보다 직전 실행이 앞이면 직전 실행 시각까지 넓힘 (`get_window_start`, 상한 `WINDOW_MAX_HOURS`). GitHub 예약 지연으로 실행 간격이 날마다 달라 고정 기간이면 빈 시간이 생기기 때문
  - 직전 실행: 선정까지 끝난 마지막 날(필터링 로그에 `[LINK]`가 있는 날)의 수집 로그 첫 `[수집 파이프라인 실행]` 시각 (한국 시간으로 기록)
- 중복 이력: 모든 날의 필터링 로그와 오늘 수집 로그. 수집만 되고 선정을 못 거친 기사는 넣지 않음 (선정 실패한 날의 후보를 다음 실행이 다시 수집하도록). 레코드 단위로 읽음 (본문 안의 `[LINK]` 줄 무시)
- `standalone_topics`(보안, 신기술)는 AI 용어 없이도 수집. 국문 키워드는 부분 문자열 비교라 일상어(`공격`, `유출` 등) 금지
- 제목 중복 제거는 같은 언어끼리만. 국문, 영문으로 나뉜 같은 사건은 선정 단계가 거름
- 실패: 피드 하나는 `[수집 오류]` 기록 후 계속, 전부 실패하면 `fail()`

### 2. Evaluate (`src/evaluate.py`)

- 점수 산정은 README "선정 기준". 함수: `prefilter`, `score_articles`(Gemini 한 번), `total_score`, `decide`, `order_top`(요약 자리 분야 상한)
- 모델은 항목별 점수만, 합계와 순위는 코드가 정함. 모델에 총점을 맡기면 9~10에 몰려 순서가 모델 나열 순서로 정해졌기 때문
- 응답 검증: 범위 밖 id, 중복 id, 숫자가 아닌 점수는 버림. 점수는 `CRITERIA_MAX` 범위로 맞춤. 잘못된 `dup_of`, `prev_of`는 무시. `FIELDS` 밖의 `field`는 기타. 제외는 `EXCLUDE_REASONS`의 이름만 인정. 점수를 하나도 못 받으면 형식 오류
- 지난 목록(`load_previous_listed`): 오늘(재실행)과 지난 `PREV_REPORT_DAYS`일 필터링 로그의 목록 기사 제목만 프롬프트에 넣음. 링크가 아니라 사건으로 비교해야 해서 모델에 맡김(다음 날 다른 매체 기사는 링크도 제목도 다름). 선정 실패로 목록이 없는 날은 건너뜀
- 필터링 로그의 `[SCORE]`는 합계를 0~10으로 바꾼 값 (`log_score`). 예전 로그, 리포트, 주간 요약과 척도를 맞추기 위함
- 번역 제목(`title_ko`)은 점수 응답에서 함께 받음 (요약하지 않는 목록 기사 제목 번역에 호출을 늘리지 않기 위함)
- 본문이 `BODY_MIN_LENGTH` 미만이면 `[평가 오류]`
- 호출 상한 `RANK_MAX_CALLS` (`score_with_retry`). 선정은 하루 한 번이라 실패하면 그날 리포트가 없음
  - 일시 오류(503 등)는 `RANK_RETRY_WAITS`만큼 기다렸다 다시, 형식 오류는 바로 다시
  - 다시 보내지 않음: 하루 한도 초과, 잘못된 요청, 잘린 응답 (`common.is_retryable`. 같은 요청은 또 잘려 한도만 씀)
  - 기다림과 호출 시간이 Evaluate Job의 `timeout-minutes` 안에 들어와야 함

### 3. Summarize (`src/summarize.py`)

- 대상 전체를 한 번 호출로 보내고(`<기사 id="번호">` 블록), 응답은 `id`로 맞춤. 기사끼리 내용을 섞지 말라는 프롬프트 규칙 유지
- 영문 기사의 국문 요약은 영문 요약의 줄별 번역. `SUMMARY_SCHEMA`의 `property_ordering`으로 `summary_en`을 먼저 쓰게 함 (고정하지 않으면 모델이 국문을 따로 써서 내용이 달라짐. 스키마 키 순서만으로는 효과 없음)
- 검증은 기사 단위 (3줄, 말줄임표만 있는 줄 불인정). 형식 오류, 빠진 기사, 호출 오류인 기사만 다시 보냄 (`SUMMARY_MAX_CALLS`까지)
- 일시 오류(503 등)는 `SUMMARY_RETRY_WAITS`만큼 기다렸다 다시. 짧은 백오프로는 3.8-flash 과부하가 풀리기 전에 상한을 다 써서 요약 전체가 실패함(2026-10-14, 10-15). 기다림과 호출 시간이 Summarize Job의 `timeout-minutes` 안에 들어와야 함
- `error`가 있으면 요약 불가로 재요청 없음. `error`를 필수 키로 바꾸지 말 것 ("없음" 같은 값이 요약 불가로 오인됨)
- 국문은 해라체. 프롬프트에 규칙이 없으면 합니다체가 섞임 (요약, 주간 요약 모두)
- 선정 이유(`reason`)는 초점 참고용. 본문에 없는 내용을 가져오지 않게 하는 문구 유지
- 요약 로그 `[MODEL]`에 모델 기록, 수동 요약은 `manual`
- 상태 표시는 `[...]` 형식. 블로그(Jekyll)가 `{{ }}`를 Liquid 문법으로 지우므로 중괄호 금지
- Gemini 없이 다시 요약: `manual_summary.py export` -> 작업 파일의 `answer` 채움 -> `import` (같은 `parse_summary` 검증)

### 4. Publish (`src/publish.py`)

- 리포트는 매번 새로 씀. 요약 기사와 "더 읽어볼 기사" 모두 `[SCORE]` 내림차순 (재실행으로 추가된 기사 반영)
- 번역 제목: 요약 기사는 요약 응답의 제목 번역이 우선, 없으면 `[TITLE_KO]`
- "더 읽어볼 기사"의 `\[번역\]`은 대괄호를 막음 (Jekyll kramdown이 뒤의 `[제목]`과 묶어 참조 링크로 해석). 제목 안의 대괄호는 소괄호로
- `index.md`는 `### 리포트 목록` 아래에 링크 추가 (중복 추가 안 함)


## Gemini 호출 (`common.call_gemini`)

- `temperature` 0. `schema`를 주면 JSON 응답 강제
- 생각 토큰도 출력 한도에 들어감. 제한하지 않으면 선정 응답이 잘리고(2026-10-08), 끄면 점수가 크게 달라져 상한(`GEMINI_THINKING_BUDGET`)만 둠
- 빈 응답은 `ValueError`, 잘린 응답은 `TruncatedResponse`
- 재시도하지 않음: `NO_RETRY_CODES`, 하루 한도 초과(오류 메시지의 `PerDay`), 잘린 응답. 백오프는 오류 메시지의 `retry in Ns`가 더 길면 그 값
- 선정, 요약은 `max_retries=0`으로 불러 단계에서 전체 호출 수를 관리 (재시도가 곱으로 늘지 않게)
- 하루 호출: 보통 선정 1 + 요약 1, 최악은 `RANK_MAX_CALLS` + `SUMMARY_MAX_CALLS`. 무료 하루 한도(모델당 20회, 2026-09-30 확인) 안에 재실행까지 들어와야 함


## 로그 형식

- 파일: `archive/logs/YYYY/MM/YYYY-MM-DD-{수집|필터링|요약}.txt`
- 점수표: 같은 폴더의 `YYYY-MM-DD-점수.csv` (UTF-8 BOM, 열은 `evaluate.SCORE_COLUMNS`, 결정 값은 README "선정 기준"). 같은 날 재실행하면 이어 씀. 2026-10-12 이전 파일은 열이 다르고, 2026-10-20까지는 "분야", "지난 보도" 열이 없음
- 통계: `archive/stats/YYYY.csv`, Publish가 하루 한 줄 (같은 날 재실행이면 바꿔 씀). 현황 화면은 이 파일만 읽음. 열 추가는 맨 뒤에만 (`publish.STATS_COLUMNS`)
- 레코드는 `---` 한 줄로 구분, 한 줄 태그는 `[TAG] 값`. 블록 태그 `[SUMMARY]`, `[BODY]`는 다음 줄부터 `---` 전까지 (블록 안의 `---`는 `- - -`로)
- `read_records`는 `[LINK]`가 있는 레코드만, 키는 태그의 소문자

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
[BODY]               (필터링: 요약 대상 기사의 본문)
---
```

| 그 밖의 레코드 | 로그 | 의미 |
|---|---|---|
| `[수집 파이프라인 실행] 시각` | 수집 | 실행 시작 (한국 시간). 다음 실행의 수집 기간 기준 |
| `[수집 오류] URL - 사유` | 수집 | 피드 요청, 파싱 실패 |
| `[STATUS] NO_ARTICLES` | 수집 | 조건에 맞는 기사 0건 |
| `[LINK]` + `[STATUS] SKIP (결정)` | 필터링 | 목록에 오르지 않은 기사. 같은 날 재실행에서 목록이 찼으면 `SKIP (목록 마감)` |
| `[평가 오류] 대상 - 사유` | 필터링 | 선정 호출 실패 또는 본문 수집 실패 |
| `[요약 오류] 링크 - 사유` | 요약 | 호출 실패, 형식 오류, 요약 불가 |

- 2026-09-30까지의 로그는 예전 형식(`[TOPIC]`, `[SCORE]` 없음), 2026-06-29 ~ 09-30 리포트는 구글 뉴스 기반 이전 방식


## 주간 요약 (`src/weekly.py`)

- 지난주(ISO 주) 요약 로그의 국문 요약과 점수만 모아 Gemini 한 번 호출. 기사를 새로 읽지 않음
- 없는 기사 ID는 버리고 근거 기사가 없는 흐름은 제외. 요약 기사가 없는 주는 만들지 않음
- 기사 정렬은 그날 점수표의 합계(0\~100). `[SCORE]`(0\~10 정수)는 9와 8에 몰려 순서가 날짜순처럼 됐음. 점수표가 없으면 `[SCORE]` x 10
- 저장은 ISO 주 연도 폴더 `archive/weekly/YYYY/`, 목록도 그 폴더의 `index.md`. 지난 주간 리포트의 기사 목록만 다시 만들 때는 `tools/weekly_relist.py` (로컬, 개요와 흐름은 그대로)


## 워크플로우

- 일일 실행과 주간 요약은 같은 `concurrency` 그룹 (커밋 충돌 방지)
- 실행 확인과 자동 재실행(`check-run.yml`, 12:00과 16:00 KST, 실제로는 몇 시간 늦음): 그날 통계 줄이 없거나, 선정 성공 0(`rank_ok`), 요약 전부 실패(`summary_ok` 0), 요약 미처리(`summary_pending`)면 파이프라인을 `workflow_dispatch`로 다시 실행. Gemini 503 과부하는 몇 시간 뒤 풀리는 일이 많아서
  - 다시 돌면 같은 날 실행으로 이어짐: 수집은 첫 실행 시각부터, 선정은 남은 목록 칸만, 요약은 아직 안 된 기사만
  - 파이프라인이 실행 중이거나 대기 중이면 건너뜀. 하루 `workflow_dispatch` 실행이 `MAX_RERUNS`(2)번 이상이거나 23시(`LAST_HOUR`) 이후면 재실행 없이 실패 처리해 알림(수동 복구 필요). 수집 0건인 날도 다시 돎(허용)
  - 요약 일부만 실패(`summary_failed`, 본문 문제 등)는 다시 돌리지 않음
- 자동 테스트는 코드, 설정, 테스트, 의존성, 워크플로우가 바뀐 push에만
- Job 간 전달은 `archive/logs/` artifact (`overwrite: true`는 실패한 Job만 재실행할 수 있게 하기 위함, Evaluate와 Summarize 업로드는 `if: always()`). Publish는 collect -> evaluate -> summarize 순으로 받아 덮어씀
- 실행 날짜는 Collect가 정해 `RUN_DATE`로 전달 (자정 전후에도 같은 파일). `TZ=Asia/Seoul`
- Collect의 `has_articles`가 `false`이거나 Collect가 실패하면 이후 Job 전부 건너뜀. Evaluate가 실패하면 Summarize는 건너뛰고 Publish는 `always()`로 그때까지의 결과를 커밋
- Publish는 그 시점의 `main`을 checkout (`ref: main`). Re-run은 시작 커밋의 workflow 파일과 (Publish 외 Job은) 코드로 돌아 그 뒤의 수정이 적용 안 됨
- `if:`에서 참조하는 Job은 `needs:`에 명시 (`needs`는 직접 의존 Job만 담음)
- 수동 실행의 `test_mode`는 커밋하지 않고 리포트를 `test-report` artifact로
- 권한은 기본 `contents: read`, Publish만 `write`. API 키는 Evaluate, Summarize에만. 커밋은 변경이 있을 때만 `git pull --rebase` 후 push


## 알려진 한계

| 한계 | 개선 방향 |
|---|---|
| 대량 후보 일괄 채점 품질 미검증 (목록 중간 기사를 놓칠 수 있음) | 주제별 예선 후 결선 |
| 선정 입력이 후보 수 x 미리보기 길이만큼 커져 분당 토큰 한도 초과 가능 | `PREVIEW_LENGTH` 축소 |
| 무료 요금제의 하루 요청 한도 | 재실행 횟수 관리, `TOP_N` 조정, 유료 전환 |
| 주제 분류가 넓음 (제목, RSS 요약에 단어만 있어도 분류) | 키워드 정비, 제목 위주 매칭 |
| 일부 매체 본문 수집 불가 (OpenAI 등 HTTP 403) | RSS에 담긴 본문 활용 |
| 요약 실패 시 빈자리 미보충 | 다음 순위 기사로 보충 |
| 선정 실패한 날의 후보 중 다음 실행의 수집 기간을 벗어난 기사는 유실 | 같은 날 수동 재실행 |
| VentureBeat 수집 실패 (HTTP 429 잦음) | 대체 피드 확보 |
| 일부 매체가 robots.txt에 AI 수집 봇 지목 | 해당 매체는 본문 수집 없이 목록만 |
| 요약 대상 본문이 필터링 로그로 공개 저장소에 커밋됨 | 커밋 전 `[BODY]` 제거 |
