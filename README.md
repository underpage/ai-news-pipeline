# AI 뉴스 요약 파이프라인

매일 아침 지난 24시간 뉴스를 수집, 기준에 따라 중요한 기사를 골라 중요도 순으로 나열하고 상위 n건을 3줄 요약

- **수집**: 실행 시점 기준 지난 24시간에 발행된 AI 기사를 수집해 로그에 기록
- **추출**: 후보 전체를 생성형 AI가 서로 비교해 추출 기준과 중요도 기준에 따라 목록에 올릴 기사 선정
- **요약**: 선정한 기사 중 상위 n건의 본문을 3줄로 요약하고 감성 태그 추가
- **발행**: 요약 기사와 뉴스 목록(중요도 순)을 마크다운 리포트로 저장 후 저장소에 자동 커밋


## 한눈에 보기

| 항목 | 내용 |
|---|---|
| 주제 | AI |
| 키워드 | 에이전트, 개발, 모델, 보안, 신기술, 산업, 규제 (국문·영문, 우선순위 순) |
| 실행 주기 | 매일 09:00 KST (cron `0 0 * * *`, UTC 기준) |
| 수집 범위 | 실행 시점 기준 지난 24시간에 발행된 기사 |
| 수집 건수 | 하루 상한은 `COLLECT_MAX` |
| 목록 건수 | `LIST_N`건까지, 중요도 `MIN_SCORE`점 이상만 |
| 요약 건수 | 목록 상위 `TOP_N`건 |
| 자동화 도구 | GitHub Actions (단계별 Job으로 분리) |
| AI 모델 (추출·요약) | Gemini API (`GEMINI_MODEL`로 지정) |
| 뉴스 소스 | 국내·해외 매체의 RSS (`config/feeds.yml`) |


## 파이프라인 구조

```text
[GitHub Actions 스케줄러]  매일 09:00 KST / 수동 실행(workflow_dispatch)
        │
        ▼
1. Collect    src/collect.py    RSS 수집 → 지난 24시간 → AI 기사 → 링크 중복 제거 → 상한 적용
        │                       archive/logs/YYYY/MM/YYYY-MM-DD-수집.txt
        ▼                       (0건이면 이후 단계 전체 건너뜀)
2. Evaluate   src/evaluate.py   후보 전체를 Gemini 한 번 호출로 비교 → 목록 선정 → 상위 기사 본문 수집
        │                       archive/logs/YYYY/MM/YYYY-MM-DD-필터링.txt
        ▼
3. Summarize  src/summarize.py  상위 기사 3줄 요약(영문·국문) + 감성 태그
        │                       archive/logs/YYYY/MM/YYYY-MM-DD-요약.txt
        ▼
4. Publish    src/publish.py    요약 기사 + 뉴스 목록 리포트 생성 → git commit & push
                                archive/news/YYYY/MM/YYYY-MM-DD.md, index.md
```

- 단계 간 데이터는 `archive/logs/YYYY/MM/` 폴더의 텍스트 파일로 전달
- 로그와 리포트는 같은 폴더 체계를 쓰고 날짜는 파일명으로 구분
- Job마다 러너가 달라 `archive/logs/`는 artifact로 다음 Job에 전달
- 마지막 Publish Job에서 `archive/logs/`와 리포트를 함께 커밋
- Publish 단계에서 그날의 단계별 성공·실패 건수를 `archive/stats/YYYY.csv`에 하루 한 줄로 기록
- Gemini 호출은 하루에 추출 한 번 + 요약 기사 수만큼


## 폴더 구조

```text
.
├── .github/workflows/
│   ├── ai-news-pipeline.yml  # 일일 실행 (매일 09:00)
│   ├── weekly-summary.yml    # 주간 요약 (매주 월요일 10:00)
│   ├── check-run.yml         # 실행 누락 확인 (매일 12:00)
│   └── test.yml              # 자동 테스트 (코드 변경 시)
├── config/
│   ├── feeds.yml         # 뉴스 소스(RSS 피드) 목록
│   ├── keywords.yml      # AI 용어, 주제별 키워드
│   ├── report.md.j2      # 일일 리포트 형식 템플릿
│   └── weekly.md.j2      # 주간 리포트 형식 템플릿
├── src/
│   ├── main.py           # 아래 네 단계를 순서대로 한 번에 실행 (로컬용)
│   ├── manual_summary.py # 요약 실패 기사를 Gemini 없이 다시 요약해 채움 (로컬용)
│   ├── common.py         # 공용 모듈: 로그 읽기/쓰기, Gemini 호출(간격 조절·백오프). 직접 실행하지 않음
│   ├── collect.py        # 1. RSS 수집, 기간·AI 기사 필터, 주제 분류, 중복 제거
│   ├── evaluate.py       # 2. 목록 선정, 상위 기사 본문 수집
│   ├── summarize.py      # 3. 요약, 감성 태그
│   ├── publish.py        # 4. 마크다운 리포트 생성, 통계 기록
│   ├── weekly.py         # 주간 요약 (지난주 요약 기사로 흐름 정리)
│   └── prompts/
│       ├── filter_prompt.txt    # 추출 기준, 중요도 기준
│       ├── summary_prompt.txt   # 요약/번역·감성 태그 프롬프트
│       └── weekly_prompt.txt    # 주간 흐름 정리 프롬프트
├── tests/                # 자동 테스트 (Gemini·매체 요청 없이 실행)
├── archive/              # 실행 결과 (매일 누적, 자동 커밋)
│   ├── stats/YYYY.csv    # 연도별 실행 통계 (하루 한 줄)
│   ├── logs/YYYY/MM/     # 연월별 단계 로그 (파일명이 날짜)
│   ├── news/YYYY/MM/     # 연월별 리포트 (파일명이 날짜)
│   └── weekly/YYYY-Www.md # 주간 리포트
├── archive-test/         # 테스트 실행 결과 (커밋 제외)
├── summary-work/         # 수동 요약 작업 파일 (커밋 제외)
├── pyproject.toml        # 의존성 정의
├── uv.lock               # 의존성 잠금 파일
├── .env                  # 환경 변수 (커밋 금지)
├── AGENT.md              # 환경 변수, 상세 동작, 작업 규칙 (LLM 에이전트용)
└── README.md
```


## 실행 방법

### GitHub Actions 실행

- 매일 09:00 KST에 자동 실행
- 수동 실행은 **Actions → AI News Pipeline → Run workflow**

| 수동 실행 입력 | 기본값 | 설명 |
|---|---|---|
| `test_mode` | `false` | `true`면 소량만 수집해 전체 흐름을 검증. 결과는 커밋하지 않고 `test-report`로 내려받기만 가능 |
| `collect_limit` | 워크플로우에 정의 | 테스트 모드에서 수집할 최대 기사 수 |

### 로컬 실행

```bash
uv sync

# 한 번에 실행
uv run src/main.py

# 테스트 실행 (소량만 수집, 결과는 archive-test/)
uv run src/main.py --test

# 단계별로 실행
uv run src/collect.py
uv run src/evaluate.py
uv run src/summarize.py
uv run src/publish.py
```

- `.env`에 `GEMINI_API_KEY` 필요
- 결과는 `archive/`에 기록됨 (GitHub에서 실행할 때와 같은 위치)
- `--test`로 실행하면 결과가 `archive-test/`에 따로 쌓이고 커밋되지 않음. 실제 기록과 중복 이력에 영향 없음
- 단계별로 테스트할 때는 명령 앞에 `TEST_MODE=true`를 붙임

**주간 요약**

```bash
uv run src/weekly.py            # 지난주
uv run src/weekly.py 2026-W40   # 지정한 주
```

**자동 테스트**

```bash
uv run pytest                   # Gemini와 매체 사이트에 요청하지 않음
```

**요약 실패 기사 다시 요약 (Gemini 없이)**

```bash
uv run src/manual_summary.py export 2026-10-01   # summary-work/2026-10-01.json 생성
# 각 항목의 prompt를 아무 LLM에 주고, 받은 JSON을 answer에 넣음
uv run src/manual_summary.py import 2026-10-01   # 검증 후 요약 로그에 넣고 리포트 다시 생성
```


## 선정 기준

- 기준은 `src/prompts/filter_prompt.txt`에 정의, 이 파일을 통해 기준 변경
- 선정 관점: AI·보안·신기술·AI 에이전트의 현재 상태와 방향을 파악하는 데 중요한가

**제외**

- AI, 보안, 신기술 어느 쪽과도 관련 없는 기사
- 홍보성 보도자료, 광고, 행사 안내, 인사·수상 소식
- 주가·투자 전망, 추측, 의견 위주 칼럼
- 같은 사건의 중복 기사 (국문·영문 포함, 1건만 선정)
- 한 매체에 쏠린 기사 (매체당 상한 초과분)
- 중요도가 `MIN_SCORE` 미만인 기사

**중요도** (항목별 점수를 더해 높은 순으로 나열, 배점은 프롬프트에 정의)

- 기술적 파급력
- 실무 활용도
- 새로움
- 근거의 확실성


## 리포트 구성

- 저장 위치: `archive/news/YYYY/MM/YYYY-MM-DD.md`, 월별 목록은 같은 폴더의 `index.md`
- **요약 기사**: 중요도 순, 3줄 요약과 감성 태그(긍정/부정/중립). 요약하지 못해도 목록에서 빼지 않고 `[요약 실패]`, `[요약 불가]`, `[요약 대기]`로 표시. 수동으로 채운 요약은 `[수동 요약]`
- **더 읽어볼 기사**: 선정한 기사 중 위에서 요약하지 않은 나머지를 중요도 순으로 제목과 원문 링크 (순위는 전체 기준으로 이어짐). 영문 기사는 번역 제목도 표시
- 국문 기사는 국문 요약만, 영문 기사는 번역 제목과 영문/국문 요약
- **주간 리포트**: 매주 월요일 지난주 요약 기사로 개요와 주요 흐름을 정리 (`archive/weekly/`)


## 설정

- 뉴스 소스: `config/feeds.yml` (매체 추가·삭제는 이 파일만 수정)
- 키워드: `config/keywords.yml` (AI 용어, 주제별 키워드와 우선순위)
- 리포트 형식: `config/report.md.j2` (항목 순서, 문구, 표기를 이 파일에서 수정)
- 선정/요약 기준: `src/prompts/*.txt`
- 환경 변수, 단계별 상세 동작, 실패 처리, 알려진 한계: [AGENT.md](./AGENT.md)


## 제약 사항

- RSS 피드가 없는 사이트를 강제 크롤링해 저작권을 침해하는 방식 금지
- 무한 루프에 빠지는 트리거 설정 금지 (불필요한 API 호출 비용 발생)