import os
import re
import csv
import json
import time
import datetime

from common import (
    append_record, append_error, read_records, load_prompt, fill_prompt, call_gemini, fail, fetch, clean_inline,
    is_retryable, get_log_path, read_records_from, run_date, LOGS_DIR,
)
from collect import load_config, build_pattern

KST = datetime.timezone(datetime.timedelta(hours=9))
# 선정 호출이 일시 오류(503 과부하 등)로 실패했을 때 다시 보내기 전 기다리는 시간(초). 호출은 첫 호출을 포함해
# RANK_MAX_CALLS번까지이며 call_gemini 안에서는 다시 보내지 않는다 (재시도가 곱으로 늘지 않게)
RANK_RETRY_WAITS = (60, 300, 600)
RANK_MAX_CALLS = len(RANK_RETRY_WAITS) + 1
# 선정 프롬프트에 함께 넣는 지난 목록의 날짜 수 (오늘 이미 올린 목록은 따로 함께 넣음). 같은 날 후보끼리의 dup_of만으로는
# 다음 날 다른 매체가 쓴 같은 사건이 다시 상위권에 오름 (10-15, 10-16 목록 30건 중 5건씩, 상위 10 중 2건씩)
PREV_REPORT_DAYS = 3
# Gemini에 보내기 전 사전 필터에서 매체 하나가 남길 수 있는 후보 수
PREFILTER_SOURCE_MAX = 20
BODY_MIN_LENGTH = 500
BODY_MAX_LENGTH = 4000
PREVIEW_LENGTH = 500
# 중요도 항목과 점수 범위(1~최댓값). Gemini가 항목마다 점수를 주고, 코드가 합계 0~100을 계산한다.
# 합계에는 criteria_weights에 적힌 항목(파급력, 관련성)만 들어가고, 새로움과 근거는 가장 낮은 단계일 때 감점에만 쓴다
CRITERIA = ("impact", "relevance", "novelty", "certainty")
CRITERIA_MAX = {"impact": 10, "relevance": 5, "novelty": 3, "certainty": 3}
CRITERIA_KO = {"impact": "파급력", "relevance": "관련성", "novelty": "새로움", "certainty": "근거"}
WEIGHTED_CRITERIA = ("impact", "relevance")
# 제외 사유. 모델이 이 밖의 이름을 쓰면 제외로 보지 않는다 (정의 밖 사유가 거부권이 되지 않게)
EXCLUDE_REASONS = ("관련 없음", "홍보", "행사 안내", "인사", "주가", "추측")
# 대표 분야. 모델이 기사마다 하나 고르고, 요약 자리의 분야 상한(field_cap)에 쓴다. 이 밖의 값은 "기타"
FIELDS = ("AI 모델", "AI 에이전트", "보안", "신기술", "기타")
RANK_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "INTEGER"},
            **{name: {"type": "INTEGER"} for name in CRITERIA},
            "dup_of": {"type": "INTEGER"},
            "prev_of": {"type": "INTEGER"},
            "story_of": {"type": "INTEGER"},
            "story_prev": {"type": "INTEGER"},
            "field": {"type": "STRING", "enum": list(FIELDS)},
            "exclude": {"type": "STRING"},
            "reason": {"type": "STRING"},
            "title_ko": {"type": "STRING"},
        },
        "required": ["id", *CRITERIA, "dup_of", "prev_of", "story_of", "story_prev", "field", "exclude", "reason", "title_ko"],
    },
}
# 같은 사안(story_of, story_prev) 열은 가운데에 둔다. 링크, 지난 보도가 맨 끝인 형식은 그대로
SCORE_COLUMNS = ["실행", "결정", "합계", *[CRITERIA_KO[name] for name in CRITERIA], "같은 사건", "같은 사건 수",
                 "같은 사안", "같은 사안 수", "지난 사안", "제외 사유",
                 "선정 이유", "분야", "출처", "발행", "주제", "제목", "번역 제목", "링크", "지난 보도"]
# 분야 균형에서 상한, 최소 건수를 따지는 분야 (기타는 상한 없음)
BALANCED_FIELDS = ("AI 모델", "AI 에이전트", "보안", "신기술")
HANGUL = re.compile(r"[가-힣]")
BODY_SELECTORS = [
    '#article-view-content-div', '[itemprop="articleBody"]', '.articlebody', '.article-body',
    '.article_body', '.article-content', '.entry-content', '.post-content', '#articleBody',
    'article', 'main',
]

_selection = load_config("selection.yml")
EXCLUDE_PATTERNS = {name: build_pattern(terms) for name, terms in (_selection.get("exclude_title") or {}).items()}
TOPIC_WEIGHTS = _selection.get("topic_weights") or {}
CRITERIA_WEIGHTS = _selection.get("criteria_weights") or {}
FIELD_CAP = _selection.get("field_cap") or {}
STORY_CAP = _selection.get("story_cap") or {}
if FIELD_CAP and not all(isinstance(FIELD_CAP.get(key), (int, float)) for key in ("max", "exempt_impact", "min_total")):
    raise ValueError("config/selection.yml: field_cap에는 max, exempt_impact, min_total을 숫자로 적어야 합니다.")
if not all(isinstance(value, (int, float)) for value in list(FIELD_CAP.values()) + list(STORY_CAP.values())):
    raise ValueError("config/selection.yml: field_cap, story_cap의 값은 숫자여야 합니다.")
if set(CRITERIA_WEIGHTS) != set(WEIGHTED_CRITERIA) or abs(sum(CRITERIA_WEIGHTS.values()) - 1) > 1e-6:
    raise ValueError(f"config/selection.yml: criteria_weights는 {', '.join(WEIGHTED_CRITERIA)}의 가중치이고 합이 1이어야 합니다.")


def load_penalties(raw):
    """penalties를 {항목: {단계: 감점}}으로 읽는다. 예전 형식(novelty: 10)은 1점일 때의 감점 {1: 10}"""
    if not set(raw) <= set(CRITERIA) - set(WEIGHTED_CRITERIA):
        raise ValueError("config/selection.yml: penalties에는 novelty, certainty만 적을 수 있습니다.")
    penalties = {}
    for name, value in raw.items():
        stages = value if isinstance(value, dict) else {1: value}
        try:
            penalties[name] = {int(stage): float(points) for stage, points in stages.items()}
        except (TypeError, ValueError):
            raise ValueError(f"config/selection.yml: penalties.{name}은 숫자나 {{단계: 감점}}이어야 합니다.")
    return penalties


PENALTIES = load_penalties(_selection.get("penalties") or {})


def get_int_env(name, default, minimum=1):
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def build_article_list(news_list):
    # 후보마다 같은 순서와 이름표로 적어 모델이 각 항목을 헷갈리지 않게 한다
    blocks = []
    for number, news in enumerate(news_list, start=1):
        lines = [
            f"[ID: {number}]",
            f"제목: {news['title']}",
            f"출처: {news.get('source', '-')} | 발행: {news.get('published', '-')} | 주제: {news.get('topic', '-')}",
            f"미리보기: {news['summary'][:PREVIEW_LENGTH] if news.get('summary') else '-'}",
        ]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def load_previous_listed(days):
    """오늘(재실행 때 이미 올린 목록)과 지난 days일 필터링 로그에서 목록에 오른 기사. 최근 날짜 먼저"""
    today = run_date()
    previous = []
    for offset in range(days + 1):
        day = today - datetime.timedelta(days=offset)
        path = os.path.join(LOGS_DIR, day.strftime("%Y"), day.strftime("%m"), f"{day:%Y-%m-%d}-필터링.txt")
        previous += [record for record in read_records_from(path) if record.get("title")]
    return previous


def build_previous_list(previous):
    # 국문, 영문으로 나뉜 같은 사건도 알아보게 번역 제목을 함께 적는다
    if not previous:
        return "(없음)"
    lines = []
    for number, record in enumerate(previous, start=1):
        title_ko = f" / {record['title_ko']}" if record.get("title_ko") else ""
        lines.append(f"[P{number}] {record['title']}{title_ko}")
    return "\n".join(lines)


def topics_of(news):
    return [topic.strip() for topic in str(news.get("topic") or "").split(",") if topic.strip()]


def topic_weight(news):
    # 합이 아니라 가장 큰 값. 합으로 정렬하면 키워드 태그가 여러 개 붙은 기사가 늘 앞서서, 후보가 넘치는 날
    # 태그가 하나인 기사는 채점도 받지 못하고 빠졌다 (10-28 104건, 태그가 잘 붙는 보안 기사가 유리)
    return max((TOPIC_WEIGHTS.get(topic, 0) for topic in topics_of(news)), default=0)


def prefilter(news_list, limit):
    """Gemini에 보내기 전에 명백히 아닌 기사를 빼고 limit건 이하로 줄인다. (남긴 목록, [(기사, 사유)])

    제목 제외 패턴 -> 주제 가중치(태그 중 가장 큰 값), 최신순으로 정렬 -> 매체별 상한 -> limit건.
    중요도는 판단하지 않는다. 지난 로그로 확인했을 때 발행 시각이나 매체별 과거 선정률로 자르면 중요한 기사가 빠졌다.
    """
    kept, dropped = [], []
    for news in news_list:
        category = next((name for name, pattern in EXCLUDE_PATTERNS.items() if pattern.search(news["title"])), None)
        if category:
            dropped.append((news, f"사전 제외: {category}"))
        else:
            kept.append(news)
    kept.sort(key=lambda news: news.get("published") or "", reverse=True)
    kept.sort(key=lambda news: -topic_weight(news))

    result, per_source = [], {}
    for news in kept:
        source = news.get("source") or "-"
        per_source[source] = per_source.get(source, 0) + 1
        if per_source[source] > PREFILTER_SOURCE_MAX:
            dropped.append((news, "사전 제외: 매체 상한"))
        elif len(result) >= limit:
            dropped.append((news, "사전 제외: 후보 상한"))
        else:
            result.append(news)
    return result, dropped


def total_score(values):
    """합계 0~100. 파급력, 관련성을 각각 0~100으로 바꿔 가중합하고, 새로움, 근거가 penalties에 적힌 단계면 감점한다."""
    weighted = sum(CRITERIA_WEIGHTS[name] * (values[name] - 1) / (CRITERIA_MAX[name] - 1) * 100 for name in WEIGHTED_CRITERIA)
    penalty = sum(stages.get(values[name], 0) for name, stages in PENALTIES.items())
    return round(max(0, weighted - penalty), 1)


def exclude_reason(value):
    """모델이 적은 제외 표시를 정해진 사유 이름으로 맞춘다. 정해진 사유가 아니면 빈 문자열 (제외 아님)."""
    text = clean_inline(value or "", 60)
    return next((reason for reason in EXCLUDE_REASONS if reason in text), "")


def parse_score_items(result):
    try:
        items = json.loads(result)
    except ValueError:
        match = re.search(r'\[.*\]', result, re.DOTALL)
        if not match:
            raise ValueError(f"선정 결과를 해석할 수 없습니다: {result[:200]}")
        items = json.loads(match.group(0))
    if isinstance(items, dict):
        # 배열을 객체로 한 번 감싸서 준 경우
        items = next((value for value in items.values() if isinstance(value, list)), None)
    if not isinstance(items, list):
        raise ValueError("선정 결과가 JSON 배열이 아닙니다.")
    return items


def score_articles(news_list, previous=()):
    """후보 전체를 한 번의 호출로 보내 기사마다 항목별 점수를 받는다. {후보 순번: 점수 정보}

    점수 정보: 항목별 점수(1~CRITERIA_MAX), total(0~100), dup_of(같은 사건 다른 기사의 순번 또는 None),
    prev_of(같은 사건을 다룬 지난 목록 기사의 previous 순번 또는 None), story_of(같은 사안 기준 기사의 순번 또는 None),
    story_prev(같은 사안을 다룬 지난 목록 기사의 previous 순번 또는 None), field(FIELDS 중 하나), exclude, reason, title_ko
    """

    def number(item, key):
        # 0이나 빈 값은 해당 없음(-1). 숫자가 아니면 무시
        try:
            return int(item.get(key) or 0) - 1
        except (TypeError, ValueError):
            return -1

    prompt = fill_prompt(load_prompt("filter_prompt.txt"), articles=build_article_list(news_list),
                         previous=build_previous_list(previous))
    items = parse_score_items(call_gemini(prompt, schema=RANK_SCHEMA, max_retries=0))

    scored = {}
    for item in items:
        try:
            index = int(item["id"]) - 1
            values = {name: min(CRITERIA_MAX[name], max(1, int(item[name]))) for name in CRITERIA}
        except (KeyError, TypeError, ValueError):
            continue
        if index in scored or not (0 <= index < len(news_list)):
            continue
        dup_of, prev_of = number(item, "dup_of"), number(item, "prev_of")
        story_of, story_prev = number(item, "story_of"), number(item, "story_prev")
        title_ko = clean_inline(item.get("title_ko", ""), 150)
        # 국문 제목은 번역하지 않는다 (모델이 원문을 그대로 돌려준 경우 포함)
        if HANGUL.search(news_list[index].get("title", "")) or title_ko == news_list[index].get("title"):
            title_ko = ""
        scored[index] = {
            **values,
            "total": total_score(values),
            "dup_of": dup_of if dup_of != index and 0 <= dup_of < len(news_list) else None,
            "prev_of": prev_of if 0 <= prev_of < len(previous) else None,
            "story_of": story_of if story_of != index and 0 <= story_of < len(news_list) else None,
            "story_prev": story_prev if 0 <= story_prev < len(previous) else None,
            "field": item.get("field") if item.get("field") in FIELDS else "기타",
            "exclude": exclude_reason(item.get("exclude")),
            "reason": clean_inline(item.get("reason", ""), 60),
            "title_ko": title_ko,
        }
    if news_list and not scored:
        raise ValueError("점수를 받은 후보가 없습니다.")
    return scored


def score_with_retry(news_list, previous=()):
    """선정은 하루 한 번뿐이라 실패하면 그날 리포트가 없다. 호출은 RANK_MAX_CALLS번까지.

    일시 오류(503 과부하 등)는 RANK_RETRY_WAITS만큼 기다렸다가, 응답 형식 오류는 바로 다시 보낸다.
    하루 요청 한도 초과, 잘못된 요청, 출력 한도 잘림은 다시 보내도 같으므로 바로 실패로 넘긴다.
    실패한 시도마다 [평가 오류] 줄을 남긴다 (통계의 호출 실패 횟수).
    """
    temporary_failures = 0
    for attempt in range(1, RANK_MAX_CALLS + 1):
        try:
            return score_articles(news_list, previous)
        except Exception as e:
            if attempt == RANK_MAX_CALLS or not is_retryable(e):
                raise
            append_error("필터링", "평가 오류", "전체 후보", f"선정 실패(다시 시도): {e}")
            if isinstance(e, ValueError):
                print(f" -> [선정 응답 형식 오류] 바로 다시 선정 ({attempt}/{RANK_MAX_CALLS}): {str(e)[:200]}")
                continue
            wait = RANK_RETRY_WAITS[min(temporary_failures, len(RANK_RETRY_WAITS) - 1)]
            temporary_failures += 1
            print(f" -> [평가 API 일시 오류] {wait}초 후 다시 선정 ({attempt}/{RANK_MAX_CALLS}): {str(e)[:200]}")
            time.sleep(wait)


def sort_key(scored, index):
    """목록 순서. 합계, 파급력, 새로움, 근거, 같은 사건을 다룬 기사 수 순 (그다음은 미리 정렬해 둔 최신순)"""
    info = scored[index]
    return (-info["total"], -info["impact"], -info.get("novelty", 3), -info.get("certainty", 3), -info.get("coverage", 1))


def order_top(listed, scored, top_slots):
    """목록 위쪽 top_slots건(요약 자리)을 분야 균형과 같은 사안 상한에 맞게 고른다. 목록에 오르는 기사는 그대로.

    1. 네 분야(기타 제외)마다 그 분야 최고 기사가 floor_total 이상이면 1건씩 먼저 넣는다
    2. 합계 순으로 보며 분야 상한(max) 안이거나 파급력이 exempt_impact 이상인 기사를 넣는다. 면제는 같은 사안에서 한 번,
       지난 리포트에 실린 사안의 후속(story_prev)은 면제 없음. 합계가 min_total 미만이면 상한 때문에 끌어올리지 않는다
    3. 남은 자리는 상한 없이 합계 순으로 채운다
    어느 단계에서든 같은 사안은 story_cap.summary건까지. 고른 기사는 합계 순으로 다시 놓는다 (리포트 번호가 합계 순).
    점수 기준만 고쳐서는 한 분야가 하루 상위 10건 중 9건을 차지한 날이 있었음 (2026-10-20 보안). 면제를 합계가 아니라
    파급력으로 정하는 이유: AI와 무관한 보안은 관련성이 4까지라 파급력 9여도 합계 83.3
    """
    if not FIELD_CAP or top_slots <= 0:
        return listed
    story_max = STORY_CAP.get("summary")
    top, counts, stories, exempt_used = [], {}, {}, set()

    def field(index):
        return scored[index].get("field") or "기타"

    def story(index):
        return scored[index].get("story", index)

    def story_ok(index):
        return not story_max or stories.get(story(index), 0) < story_max

    def add(index):
        top.append(index)
        counts[field(index)] = counts.get(field(index), 0) + 1
        stories[story(index)] = stories.get(story(index), 0) + 1

    floor = FIELD_CAP.get("floor_total")
    if floor is not None:
        for name in BALANCED_FIELDS:
            best = next((index for index in listed if field(index) == name and story_ok(index)), None)
            if best is not None and scored[best]["total"] >= floor and len(top) < top_slots:
                add(best)
    for index in listed:
        info = scored[index]
        if len(top) >= top_slots or info["total"] < FIELD_CAP["min_total"]:
            break
        if index in top or not story_ok(index):
            continue
        exempt = (info["impact"] >= FIELD_CAP["exempt_impact"] and not info.get("story_prev_group")
                  and story(index) not in exempt_used)
        if counts.get(field(index), 0) < FIELD_CAP["max"]:
            add(index)
        elif exempt:
            exempt_used.add(story(index))
            add(index)
    for index in listed:
        if len(top) >= top_slots:
            break
        if index not in top and story_ok(index):
            add(index)
    position = {index: number for number, index in enumerate(listed)}
    top.sort(key=lambda index: position[index])
    return top + [index for index in listed if index not in top]


def group_by(scored, links):
    """links(순번 -> 이어지는 순번들)로 이어진 무리. {순번: 무리 대표 순번}"""
    parent = {index: index for index in scored}

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for index in scored:
        for other in links(index):
            if other is not None and other in scored:
                parent[find(index)] = find(other)
    return {index: find(index) for index in scored}


def decide(news_list, scored, list_slots, list_n, min_score, top_slots=0):
    """점수표로 목록에 올릴 기사를 정한다. (목록 순번 목록, {순번: 결정})

    1. 같은 사안 무리(story_of와 dup_of로 이어진 기사들): 무리의 한 기사라도 지난 목록과 같은 사안(story_prev)이나
       같은 사건(prev_of)이면 후속 보도로 보고 새로움 3을 2로 낮춰 합계를 다시 계산한다 (penalties)
    2. 같은 사건 묶음(dup_of)에서는 제외 표시가 없는 기사 중 합계가 가장 높은 기사만 남긴다. 묶음의 한 기사라도
       지난 목록과 같은 사건이면 묶음 전체를 뺀다
    3. 제외 표시와 최소 점수 미달을 빼고 sort_key 순으로 정렬해 list_slots건을 고른다. 매체는 목록의 1/4,
       같은 사안은 story_cap.list건까지. 합계가 field_cap.list_min_total 이상인 구간에서는 한 분야가
       field_cap.list_max건까지(파급력 exempt_impact 이상은 예외)이고, 구간이 끝나면 상한에 걸린 기사도 합계 순으로 다시 본다.
       같은 합계 안에서는 지금까지 목록에 적게 뽑힌 분야의 기사를 먼저 고른다 (합계가 다른 기사끼리는 순서를 바꾸지 않음)
    4. 위쪽 top_slots건(요약 자리)을 order_top으로 고른다
    같은 사건 기사 수는 scored[순번]["coverage"], 같은 사안 무리는 "story", "story_size", "story_prev_group"에 남긴다.
    """
    decisions = {}
    story_of = group_by(scored, lambda index: (scored[index].get("story_of"), scored[index].get("dup_of")))
    story_members = {}
    for index, root in story_of.items():
        story_members.setdefault(root, []).append(index)
    for root, members in story_members.items():
        reported = any(scored[index].get("story_prev") is not None or scored[index].get("prev_of") is not None
                       for index in members)
        for index in members:
            info = scored[index]
            info["story"], info["story_size"], info["story_prev_group"] = root, len(members), reported
            if reported and info.get("novelty") == 3:
                info["novelty"] = 2
                info["total"] = total_score({name: info[name] for name in CRITERIA})

    event_of = group_by(scored, lambda index: (scored[index]["dup_of"],))
    groups = {}
    for index, root in event_of.items():
        groups.setdefault(root, []).append(index)
    for members in groups.values():
        for index in members:
            scored[index]["coverage"] = len(members)
        # 홍보 기사가 합계로 대표가 되면 같은 사건의 정상 기사까지 빠지므로 제외 표시가 없는 기사를 먼저 남긴다
        keeper = min(members, key=lambda index: (bool(scored[index]["exclude"]), -scored[index]["total"], index))
        for index in members:
            if index != keeper:
                decisions[index] = "중복"
        if any(scored[index].get("prev_of") is not None for index in members):
            decisions[keeper] = "지난 보도"

    candidates = []
    for index, info in scored.items():
        if index in decisions:
            continue
        if info["exclude"]:
            decisions[index] = "제외"
        elif info["total"] < min_score * 10:
            decisions[index] = "기준 미달"
        else:
            candidates.append(index)
    candidates.sort(key=lambda index: news_list[index].get("published") or "", reverse=True)
    candidates.sort(key=lambda index: sort_key(scored, index))

    # 한 매체가 목록의 1/4을 넘지 않게 한다. 1/5(30건 중 6건)일 때 15위 수준 기사가 빠지고 63점 기사가 들어갔음 (10-15, 10-16)
    source_max = max(2, list_n // 4)
    story_max = STORY_CAP.get("list")
    field_max, field_floor = FIELD_CAP.get("list_max"), FIELD_CAP.get("list_min_total", 0)
    exempt_impact = FIELD_CAP.get("exempt_impact", CRITERIA_MAX["impact"] + 1)
    listed, per_source, per_story, per_field = [], {}, {}, {}

    def field_of(index):
        return scored[index].get("field") or "기타"

    def try_add(index, field_cap):
        """목록에 넣거나 결정(매체 상한, 같은 사안, 분야 상한)을 남긴다. 분야 상한에 걸리면 False (뒤에서 다시 봄)"""
        info, source, field = scored[index], news_list[index].get("source") or "-", field_of(index)
        story = info.get("story", index)
        if per_source.get(source, 0) >= source_max:
            decisions[index] = "매체 상한"
        elif story_max and per_story.get(story, 0) >= story_max:
            decisions[index] = "같은 사안"
        elif (field_cap and field_max and field in BALANCED_FIELDS and per_field.get(field, 0) >= field_max
              and info["impact"] < exempt_impact):
            decisions[index] = "분야 상한"
            return False
        else:
            per_source[source] = per_source.get(source, 0) + 1
            per_story[story] = per_story.get(story, 0) + 1
            per_field[field] = per_field.get(field, 0) + 1
            listed.append(index)
            decisions[index] = "목록"
        return True

    def fill(pool, field_cap):
        """pool(정렬된 순번)을 합계가 같은 무리마다 적게 뽑힌 분야부터 넣는다. 분야 상한에 걸린 순번들을 돌려준다"""
        deferred, start = [], 0
        while start < len(pool) and len(listed) < list_slots:
            end = start
            while end < len(pool) and scored[pool[end]]["total"] == scored[pool[start]]["total"]:
                end += 1
            same = pool[start:end]
            while same and len(listed) < list_slots:
                pick = min(same, key=lambda index: (per_field.get(field_of(index), 0), same.index(index)))
                same.remove(pick)
                if not try_add(pick, field_cap):
                    deferred.append(pick)
            start = end
        return deferred

    upper = [index for index in candidates if scored[index]["total"] >= field_floor]
    deferred = fill(upper, field_cap=True)
    order = {index: number for number, index in enumerate(candidates)}
    rest = sorted(deferred + [index for index in candidates if index not in upper], key=lambda index: order[index])
    fill(rest, field_cap=False)
    for index in candidates:
        if decisions.get(index) not in ("목록", "매체 상한", "같은 사안", "분야 상한"):
            decisions[index] = "순위 밖"
    for index in range(len(news_list)):
        decisions.setdefault(index, "점수 없음")
    return order_top(listed, scored, top_slots), decisions


def log_score(total):
    """로그와 리포트의 [SCORE]는 지난 기록과 같은 0~10 정수로 남긴다 (합계 0~100을 10으로 나눠 반올림)."""
    return int(total / 10 + 0.5)


def score_csv_path():
    return os.path.splitext(get_log_path("점수"))[0] + ".csv"


def write_score_csv(news_list, scored, decisions, dropped, previous=()):
    """후보 전체의 점수표를 남긴다. 같은 날 다시 실행하면 아래에 이어 쓴다 (엑셀에서 열리게 UTF-8 BOM)."""
    path = score_csv_path()
    is_new = not os.path.exists(path)
    run_at = datetime.datetime.now(KST).strftime("%H:%M")
    rows = []
    order = sorted(range(len(news_list)), key=lambda index: -(scored.get(index) or {}).get("total", -1))
    for index in order:
        news, info = news_list[index], scored.get(index) or {}
        dup_of, prev_of = info.get("dup_of"), info.get("prev_of")
        # 같은 사안: 무리 대표 기사의 링크 (무리가 2건 이상이고 자신이 대표가 아닐 때)
        story, story_prev = info.get("story", index), info.get("story_prev")
        rows.append([
            run_at, decisions[index], info.get("total", ""), *[info.get(name, "") for name in CRITERIA],
            news_list[dup_of]["link"] if dup_of is not None else "", info.get("coverage", ""),
            news_list[story]["link"] if info.get("story_size", 1) > 1 and story != index else "", info.get("story_size", ""),
            previous[story_prev]["link"] if story_prev is not None else "", info.get("exclude", ""),
            info.get("reason", ""), info.get("field", ""),
            news.get("source", ""), news.get("published", ""), news.get("topic", ""), news["title"],
            info.get("title_ko", ""), news["link"], previous[prev_of]["link"] if prev_of is not None else "",
        ])
    for news, reason in dropped:
        rows.append([run_at, reason, "", *[""] * len(CRITERIA), "", "", "", "", "", "", "", "", news.get("source", ""),
                     news.get("published", ""), news.get("topic", ""), news["title"], "", news["link"], ""])
    with open(path, "a", encoding="utf-8-sig" if is_new else "utf-8", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(SCORE_COLUMNS)
        writer.writerows(rows)


def extract_with_selectors(html_content):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_content, 'html.parser')
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "figure", "noscript"]):
        tag.decompose()

    best = ""
    for selector in BODY_SELECTORS:
        for node in soup.select(selector):
            paragraphs = [p.get_text(' ', strip=True) for p in node.find_all('p')]
            text = '\n'.join(p for p in paragraphs if len(p) >= 40)
            if len(text) < 300:
                # <p> 없이 줄바꿈으로만 구성된 본문
                text = '\n'.join(line.strip() for line in node.get_text('\n').splitlines() if len(line.strip()) >= 40)
            if len(text) >= BODY_MIN_LENGTH:
                return text
            if len(text) > len(best):
                best = text
    return best


def cut_at_sentence(text, limit):
    """limit 이내에서 문장이나 문단이 끝나는 곳까지만 남긴다."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind("\n"), cut.rfind(". "), cut.rfind("다. "), cut.rfind("? "), cut.rfind("! "))
    # 끊을 곳이 너무 앞이면 그대로 자른다
    return cut[:end + 1].rstrip() if end > limit * 0.7 else cut


def scrape_body(url):
    try:
        response = fetch(url, timeout=10)

        # 본문 추출 전용 라이브러리를 먼저 쓰고, 부족하면 선택자 기반 추출로 보완한다
        text = ""
        try:
            import trafilatura
            text = trafilatura.extract(response.content, include_comments=False, include_tables=False) or ""
        except Exception as e:
            print(f" -> trafilatura 추출 실패: {e}")
        if len(text) < BODY_MIN_LENGTH:
            fallback = extract_with_selectors(response.content)
            if len(fallback) > len(text):
                text = fallback
        return cut_at_sentence(text, BODY_MAX_LENGTH)
    except Exception as e:
        print(f"웹 스크래핑 실패 ({url}): {e}")
        return ""


def main():
    print("--- 2. 평가 및 본문 수집 파이프라인 시작 ---")
    list_n = get_int_env("LIST_N", 30)
    top_n = get_int_env("TOP_N", 10)
    min_score = get_int_env("MIN_SCORE", 5, minimum=0)
    prefilter_max = get_int_env("PREFILTER_MAX", 200)

    evaluated = read_records("필터링")
    evaluated_links = {record['link'] for record in evaluated}
    listed_today = sum(1 for record in evaluated if record.get('title'))
    summary_targets_today = sum(1 for record in evaluated if record.get('body'))

    news_list = [news for news in read_records("수집") if news['link'] not in evaluated_links and news.get('title')]
    list_slots = list_n - listed_today
    summary_slots = top_n - summary_targets_today
    print(f"평가 대상: {len(news_list)}건 / 목록 {list_n}건 (오늘 이미 {listed_today}건), 요약 {top_n}건 (오늘 이미 {summary_targets_today}건)")

    if not news_list:
        print(" -> 새로 평가할 기사가 없습니다.")
    elif list_slots <= 0:
        print(" -> 오늘 목록 건수를 이미 채웠습니다.")
        for news in news_list:
            append_record("필터링", [("LINK", news['link']), ("STATUS", "SKIP (목록 마감)")])
    else:
        candidates, dropped = prefilter(news_list, prefilter_max)
        previous = load_previous_listed(PREV_REPORT_DAYS)
        print(f"사전 필터: {len(news_list)}건 -> {len(candidates)}건 (제외 {len(dropped)}건), 지난 목록 {len(previous)}건")
        try:
            scored = score_with_retry(candidates, previous)
        except Exception as e:
            print(f" -> [평가 API 오류] {e}")
            append_error("필터링", "평가 오류", "전체 후보", f"선정 실패: {e}")
            fail(f"기사 선정에 실패했습니다: {e}")
        listed, decisions = decide(candidates, scored, list_slots, list_n, min_score, top_slots=summary_slots)

        # 합계 순으로 목록에 기록하고, 앞에서부터 본문을 수집해 요약 대상을 채운다
        body_count = 0
        for index in listed:
            news, info = candidates[index], scored[index]
            body_text = ""
            if body_count < summary_slots:
                print(f"\n[요약 후보 / 합계 {info['total']}점] {news['title']}")
                body_text = scrape_body(news['link'])
                if len(body_text) < BODY_MIN_LENGTH:
                    print(" -> [본문 수집 실패] 목록에만 남기고 다음 순위 기사로 넘어갑니다.")
                    append_error("필터링", "평가 오류", news['link'], "본문 스크래핑 실패")
                    body_text = ""
                else:
                    body_count += 1
                    decisions[index] = "요약"
            append_record("필터링", [
                ("TITLE", news['title']),
                ("TITLE_KO", info["title_ko"]),
                ("LINK", news['link']),
                ("PUBLISHED", news.get('published')),
                ("SOURCE", news.get('source')),
                ("TOPIC", news.get('topic')),
                ("SCORE", log_score(info["total"])),
                ("REASON", info["reason"]),
                # 대표 분야. 리포트의 "더 읽어볼 기사"에 키워드 태그 대신 표시
                ("FIELD", info["field"]),
            ], block=("BODY", body_text) if body_text else None)

        for index, news in enumerate(candidates):
            if decisions[index] not in ("목록", "요약"):
                append_record("필터링", [("LINK", news['link']), ("STATUS", f"SKIP ({decisions[index]})")])
        for news, reason in dropped:
            append_record("필터링", [("LINK", news['link']), ("STATUS", f"SKIP ({reason})")])
        write_score_csv(candidates, scored, decisions, dropped, previous)
        if not listed:
            print(f"\n -> 목록에 올릴 기사가 없습니다 (최소 합계 {min_score * 10}점).")
        print(f"\n -> 목록 선정: {len(listed)}건, 요약 대상: {body_count}건, 점수표: {os.path.basename(score_csv_path())}")
    print("--- 2. 평가 및 본문 수집 파이프라인 종료 ---")


if __name__ == "__main__":
    main()
