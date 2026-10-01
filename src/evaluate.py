import os
import re
import json

from common import append_record, append_error, read_records, load_prompt, fill_prompt, call_gemini, fail, fetch, clean_inline

BODY_MIN_LENGTH = 500
BODY_MAX_LENGTH = 4000
PREVIEW_LENGTH = 500
RANK_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "INTEGER"},
            "score": {"type": "INTEGER"},
            "reason": {"type": "STRING"},
            "title_ko": {"type": "STRING"},
        },
        "required": ["id", "score", "reason", "title_ko"],
    },
}
HANGUL = re.compile(r"[가-힣]")
BODY_SELECTORS = [
    '#article-view-content-div', '[itemprop="articleBody"]', '.articlebody', '.article-body',
    '.article_body', '.article-content', '.entry-content', '.post-content', '#articleBody',
    'article', 'main',
]


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


def rank_articles(news_list, pick_n, min_score):
    """후보 전체를 한 번의 호출로 비교해 중요도 순 목록을 받는다. 기준에 맞는 기사가 없으면 빈 목록.

    항목: (후보 순번, 점수, 선정 이유, 번역 제목). 국문 기사의 번역 제목은 빈 값.
    """
    prompt = fill_prompt(
        load_prompt("filter_prompt.txt"),
        pick_n=pick_n, min_score=min_score, articles=build_article_list(news_list),
        # 한 매체가 목록의 1/5을 넘지 않게 한다
        source_max=max(2, pick_n // 5),
    )
    result = call_gemini(prompt, schema=RANK_SCHEMA)

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

    ranked = []
    seen = set()
    for item in items:
        try:
            index = int(item["id"]) - 1
            score = min(10, max(0, int(item["score"])))
        except (KeyError, TypeError, ValueError):
            continue
        if index in seen or not (0 <= index < len(news_list)) or score < min_score:
            continue
        seen.add(index)
        title_ko = clean_inline(item.get("title_ko", ""), 150)
        # 국문 제목은 번역하지 않는다 (모델이 원문을 그대로 돌려준 경우 포함)
        if HANGUL.search(news_list[index].get("title", "")) or title_ko == news_list[index].get("title"):
            title_ko = ""
        ranked.append((index, score, clean_inline(item.get("reason", ""), 60), title_ko))
    # 점수가 같으면 모델이 준 순서를 유지한다
    ranked.sort(key=lambda entry: -entry[1])
    return ranked[:pick_n]


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
            append_record("필터링", [("LINK", news['link']), ("STATUS", "SKIP (Score: -)")])
    else:
        try:
            ranked = rank_articles(news_list, min(len(news_list), list_slots), min_score)
        except Exception as e:
            print(f" -> [평가 API 오류] {e}")
            append_error("필터링", "평가 오류", "전체 후보", f"선정 실패: {e}")
            fail(f"기사 선정에 실패했습니다: {e}")

        # 중요도 순으로 목록에 기록하고, 앞에서부터 본문을 수집해 요약 대상을 채운다
        listed = set()
        body_count = 0
        for index, score, reason, title_ko in ranked:
            news = news_list[index]
            listed.add(index)
            body_text = ""
            if body_count < summary_slots:
                print(f"\n[요약 후보 / 중요도 {score}점] {news['title']}")
                body_text = scrape_body(news['link'])
                if len(body_text) < BODY_MIN_LENGTH:
                    print(" -> [본문 수집 실패] 목록에만 남기고 다음 순위 기사로 넘어갑니다.")
                    append_error("필터링", "평가 오류", news['link'], "본문 스크래핑 실패")
                    body_text = ""
                else:
                    body_count += 1
            append_record("필터링", [
                ("TITLE", news['title']),
                ("TITLE_KO", title_ko),
                ("LINK", news['link']),
                ("PUBLISHED", news.get('published')),
                ("SOURCE", news.get('source')),
                ("TOPIC", news.get('topic')),
                ("SCORE", score),
                ("REASON", reason),
            ], block=("BODY", body_text) if body_text else None)

        for index, news in enumerate(news_list):
            if index not in listed:
                append_record("필터링", [("LINK", news['link']), ("STATUS", "SKIP (Score: -)")])
        if not ranked:
            print(f"\n -> 중요도 {min_score}점 이상인 기사가 없습니다.")
        print(f"\n -> 목록 선정: {len(listed)}건, 요약 대상: {body_count}건")
    print("--- 2. 평가 및 본문 수집 파이프라인 종료 ---")


if __name__ == "__main__":
    main()
