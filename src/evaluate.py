import os
import re
import csv
import json
import time
import datetime

from common import (
    append_record, append_error, read_records, load_prompt, fill_prompt, call_gemini, fail, fetch, clean_inline,
    is_retryable, get_log_path,
)
from collect import load_config, build_pattern, TOPIC_KEYWORDS

KST = datetime.timezone(datetime.timedelta(hours=9))
# 선정 호출이 일시 오류(503 과부하 등)로 실패했을 때 다시 보내기 전 기다리는 시간(초). 호출은 첫 호출을 포함해
# RANK_MAX_CALLS번까지이며 call_gemini 안에서는 다시 보내지 않는다 (재시도가 곱으로 늘지 않게)
RANK_RETRY_WAITS = (60, 300, 600)
RANK_MAX_CALLS = len(RANK_RETRY_WAITS) + 1
# Gemini에 보내기 전 사전 필터에서 매체 하나가 남길 수 있는 후보 수
PREFILTER_SOURCE_MAX = 20
BODY_MIN_LENGTH = 500
BODY_MAX_LENGTH = 4000
PREVIEW_LENGTH = 500
# 중요도 항목. Gemini가 항목마다 1~5점을 주고, 코드가 가중합으로 0~100 합계를 계산한다
CRITERIA = ("impact", "practical", "novelty", "certainty")
CRITERIA_KO = {"impact": "파급력", "practical": "활용도", "novelty": "새로움", "certainty": "확실성"}
RANK_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "INTEGER"},
            **{name: {"type": "INTEGER"} for name in CRITERIA},
            "dup_of": {"type": "INTEGER"},
            "exclude": {"type": "STRING"},
            "reason": {"type": "STRING"},
            "title_ko": {"type": "STRING"},
        },
        "required": ["id", *CRITERIA, "dup_of", "exclude", "reason", "title_ko"],
    },
}
SCORE_COLUMNS = ["실행", "결정", "합계", *[CRITERIA_KO[name] for name in CRITERIA], "같은 사건", "제외 사유", "선정 이유",
                 "출처", "발행", "주제", "제목", "번역 제목", "링크"]
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
if set(CRITERIA_WEIGHTS) != set(CRITERIA) or abs(sum(CRITERIA_WEIGHTS.values()) - 1) > 1e-6:
    raise ValueError(f"config/selection.yml: criteria_weights는 {', '.join(CRITERIA)}의 가중치이고 합이 1이어야 합니다.")


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


def topics_of(news):
    return [topic.strip() for topic in str(news.get("topic") or "").split(",") if topic.strip()]


def topic_weight(news):
    return sum(TOPIC_WEIGHTS.get(topic, 0) for topic in topics_of(news))


def topic_priority(news):
    """동점일 때 앞에 둘 순서: keywords.yml의 topics에 적힌 순서 (첫 주제 기준)"""
    order = list(TOPIC_KEYWORDS)
    topics = topics_of(news)
    return order.index(topics[0]) if topics and topics[0] in order else len(order)


def prefilter(news_list, limit):
    """Gemini에 보내기 전에 명백히 아닌 기사를 빼고 limit건 이하로 줄인다. (남긴 목록, [(기사, 사유)])

    제목 제외 패턴 -> 주제 가중치 합, 최신순으로 정렬 -> 매체별 상한 -> limit건.
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
    """항목별 1~5점을 0~100으로 바꿔 가중합한다 (실습 자료의 final_eval_score와 같은 방식)."""
    return round(sum(CRITERIA_WEIGHTS[name] * (values[name] - 1) / 4 * 100 for name in CRITERIA), 1)


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


def score_articles(news_list):
    """후보 전체를 한 번의 호출로 보내 기사마다 항목별 점수를 받는다. {후보 순번: 점수 정보}

    점수 정보: 항목별 점수(1~5), total(0~100), dup_of(같은 사건 다른 기사의 순번 또는 None), exclude, reason, title_ko
    """
    prompt = fill_prompt(load_prompt("filter_prompt.txt"), articles=build_article_list(news_list))
    items = parse_score_items(call_gemini(prompt, schema=RANK_SCHEMA, max_retries=0))

    scored = {}
    for item in items:
        try:
            index = int(item["id"]) - 1
            values = {name: min(5, max(1, int(item[name]))) for name in CRITERIA}
        except (KeyError, TypeError, ValueError):
            continue
        if index in scored or not (0 <= index < len(news_list)):
            continue
        try:
            dup_of = int(item.get("dup_of") or 0) - 1
        except (TypeError, ValueError):
            dup_of = -1
        title_ko = clean_inline(item.get("title_ko", ""), 150)
        # 국문 제목은 번역하지 않는다 (모델이 원문을 그대로 돌려준 경우 포함)
        if HANGUL.search(news_list[index].get("title", "")) or title_ko == news_list[index].get("title"):
            title_ko = ""
        scored[index] = {
            **values,
            "total": total_score(values),
            "dup_of": dup_of if dup_of != index and 0 <= dup_of < len(news_list) else None,
            "exclude": clean_inline(item.get("exclude", ""), 60),
            "reason": clean_inline(item.get("reason", ""), 60),
            "title_ko": title_ko,
        }
    if news_list and not scored:
        raise ValueError("점수를 받은 후보가 없습니다.")
    return scored


def score_with_retry(news_list):
    """선정은 하루 한 번뿐이라 실패하면 그날 리포트가 없다. 호출은 RANK_MAX_CALLS번까지.

    일시 오류(503 과부하 등)는 RANK_RETRY_WAITS만큼 기다렸다가, 응답 형식 오류는 바로 다시 보낸다.
    하루 요청 한도 초과, 잘못된 요청, 출력 한도 잘림은 다시 보내도 같으므로 바로 실패로 넘긴다.
    실패한 시도마다 [평가 오류] 줄을 남긴다 (통계의 호출 실패 횟수).
    """
    temporary_failures = 0
    for attempt in range(1, RANK_MAX_CALLS + 1):
        try:
            return score_articles(news_list)
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


def decide(news_list, scored, list_slots, list_n, min_score):
    """점수표로 목록에 올릴 기사를 정한다. (목록 순번 목록, {순번: 결정})

    같은 사건 묶음에서는 합계가 가장 높은 기사만 남기고, 제외 표시와 최소 점수 미달을 뺀 뒤
    합계, 주제 순서, 최신순으로 정렬해 매체별 상한을 지키며 list_slots건을 고른다.
    """
    decisions = {}
    # 같은 사건 묶음: dup_of로 이어진 기사들
    parent = {index: index for index in scored}

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for index, info in scored.items():
        if info["dup_of"] is not None and info["dup_of"] in scored:
            parent[find(index)] = find(info["dup_of"])
    groups = {}
    for index in scored:
        groups.setdefault(find(index), []).append(index)
    for members in groups.values():
        keeper = min(members, key=lambda index: (-scored[index]["total"], index))
        for index in members:
            if index != keeper:
                decisions[index] = "중복"

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
    candidates.sort(key=lambda index: (-scored[index]["total"], topic_priority(news_list[index])))

    # 한 매체가 목록의 1/5을 넘지 않게 한다
    source_max = max(2, list_n // 5)
    listed, per_source = [], {}
    for index in candidates:
        source = news_list[index].get("source") or "-"
        if len(listed) >= list_slots:
            decisions[index] = "순위 밖"
        elif per_source.get(source, 0) >= source_max:
            decisions[index] = "매체 상한"
        else:
            per_source[source] = per_source.get(source, 0) + 1
            listed.append(index)
            decisions[index] = "목록"
    for index in range(len(news_list)):
        decisions.setdefault(index, "점수 없음")
    return listed, decisions


def log_score(total):
    """로그와 리포트의 [SCORE]는 지난 기록과 같은 0~10 정수로 남긴다 (합계 0~100을 10으로 나눠 반올림)."""
    return int(total / 10 + 0.5)


def score_csv_path():
    return os.path.splitext(get_log_path("점수"))[0] + ".csv"


def write_score_csv(news_list, scored, decisions, dropped):
    """후보 전체의 점수표를 남긴다. 같은 날 다시 실행하면 아래에 이어 쓴다 (엑셀에서 열리게 UTF-8 BOM)."""
    path = score_csv_path()
    is_new = not os.path.exists(path)
    run_at = datetime.datetime.now(KST).strftime("%H:%M")
    rows = []
    order = sorted(range(len(news_list)), key=lambda index: -(scored.get(index) or {}).get("total", -1))
    for index in order:
        news, info = news_list[index], scored.get(index) or {}
        dup_of = info.get("dup_of")
        rows.append([
            run_at, decisions[index], info.get("total", ""), *[info.get(name, "") for name in CRITERIA],
            news_list[dup_of]["link"] if dup_of is not None else "", info.get("exclude", ""), info.get("reason", ""),
            news.get("source", ""), news.get("published", ""), news.get("topic", ""), news["title"],
            info.get("title_ko", ""), news["link"],
        ])
    for news, reason in dropped:
        rows.append([run_at, reason, "", *[""] * len(CRITERIA), "", "", "", news.get("source", ""),
                     news.get("published", ""), news.get("topic", ""), news["title"], "", news["link"]])
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
    min_score = get_int_env("MIN_SCORE", 4, minimum=0)
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
        print(f"사전 필터: {len(news_list)}건 -> {len(candidates)}건 (제외 {len(dropped)}건)")
        try:
            scored = score_with_retry(candidates)
        except Exception as e:
            print(f" -> [평가 API 오류] {e}")
            append_error("필터링", "평가 오류", "전체 후보", f"선정 실패: {e}")
            fail(f"기사 선정에 실패했습니다: {e}")
        listed, decisions = decide(candidates, scored, list_slots, list_n, min_score)

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
            ], block=("BODY", body_text) if body_text else None)

        for index, news in enumerate(candidates):
            if decisions[index] not in ("목록", "요약"):
                append_record("필터링", [("LINK", news['link']), ("STATUS", f"SKIP ({decisions[index]})")])
        for news, reason in dropped:
            append_record("필터링", [("LINK", news['link']), ("STATUS", f"SKIP ({reason})")])
        write_score_csv(candidates, scored, decisions, dropped)
        if not listed:
            print(f"\n -> 목록에 올릴 기사가 없습니다 (최소 합계 {min_score * 10}점).")
        print(f"\n -> 목록 선정: {len(listed)}건, 요약 대상: {body_count}건, 점수표: {os.path.basename(score_csv_path())}")
    print("--- 2. 평가 및 본문 수집 파이프라인 종료 ---")


if __name__ == "__main__":
    main()
