"""한 주 동안 요약한 기사를 모아 주간 리포트를 만든다. Gemini 호출은 주에 한 번.

    uv run src/weekly.py              지난주 (실행 날짜 기준 직전 월~일)
    uv run src/weekly.py 2026-W40     지정한 주 (ISO 주 번호)

결과: archive/weekly/YYYY-Www.md, 목록은 archive/weekly/index.md
"""
import os
import re
import sys
import json
import datetime

from common import (
    DATA_DIR, LOGS_DIR, CONFIG_DIR, ROOT_DIR, read_records_from, load_prompt, fill_prompt, call_gemini,
    clean_inline, fail, run_date,
)
from publish import parse_summary_blocks, update_index, ensure_dir

WEEKLY_DIR = os.path.join(DATA_DIR, "weekly")
WEEKLY_TEMPLATE = "weekly.md.j2"
# 주간 리포트에 담을 흐름의 최대 개수
WEEKLY_TRENDS = int(os.environ.get("WEEKLY_TRENDS", "5"))

TREND_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "overview": {"type": "STRING"},
        "trends": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "title": {"type": "STRING"},
                    "description": {"type": "STRING"},
                    "article_ids": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                },
                "required": ["title", "description", "article_ids"],
            },
        },
    },
    "required": ["overview", "trends"],
}


def parse_week(text):
    """'2026-W40' → 그 주 월요일"""
    match = re.fullmatch(r"(\d{4})-W(\d{1,2})", text.strip())
    if not match:
        raise ValueError(f"주는 YYYY-Www 형식이어야 합니다: {text}")
    return datetime.date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)


def previous_week_monday(today):
    return today - datetime.timedelta(days=today.weekday() + 7)


def load_week_articles(monday):
    """그 주 월~일의 요약 로그에서 기사를 모아 중요도 순으로 돌려준다."""
    articles = []
    for offset in range(7):
        day = monday + datetime.timedelta(days=offset)
        path = os.path.join(LOGS_DIR, f"{day:%Y}", f"{day:%m}", f"{day:%Y-%m-%d}-요약.txt")
        for record in read_records_from(path):
            if not record.get("title"):
                continue
            title_ko, _, summary_ko, tone = parse_summary_blocks(record.get("summary", ""))
            if not summary_ko:
                continue
            try:
                score = int(record.get("score", 0))
            except ValueError:
                score = 0
            articles.append({
                "date": f"{day:%Y-%m-%d}",
                "title": record["title"],
                "title_ko": title_ko,
                "link": record["link"],
                "source": record.get("source", ""),
                "topic": record.get("topic", ""),
                "score": score,
                "reason": record.get("reason", ""),
                "tone": tone.lstrip("#"),
                "summary_ko": summary_ko,
            })
    articles.sort(key=lambda article: -article["score"])
    for number, article in enumerate(articles, start=1):
        article["id"] = number
    return articles


def build_article_list(articles):
    blocks = []
    for article in articles:
        blocks.append("\n".join([
            f"[ID: {article['id']}]",
            f"제목: {article['title_ko'] or article['title']}",
            f"날짜: {article['date']} | 주제: {article['topic'] or '-'} | 중요도: {article['score']}",
            "요약: " + " ".join(article["summary_ko"]),
        ]))
    return "\n\n".join(blocks)


def find_trends(articles):
    prompt = fill_prompt(load_prompt("weekly_prompt.txt"), max_trends=WEEKLY_TRENDS, articles=build_article_list(articles))
    result = call_gemini(prompt, schema=TREND_SCHEMA)
    try:
        data = json.loads(result)
    except ValueError:
        match = re.search(r"\{.*\}", result, re.DOTALL)
        if not match:
            raise ValueError(f"주간 요약 결과를 해석할 수 없습니다: {result[:200]}")
        data = json.loads(match.group(0))

    by_id = {article["id"]: article for article in articles}
    trends = []
    for trend in data.get("trends") or []:
        if not isinstance(trend, dict):
            continue
        related = []
        for article_id in trend.get("article_ids") or []:
            try:
                article = by_id.get(int(article_id))
            except (TypeError, ValueError):
                article = None
            if article and article not in related:
                related.append(article)
        title = clean_inline(trend.get("title", ""), 80)
        if title and related:
            trends.append({"title": title, "description": clean_inline(trend.get("description", ""), 600), "articles": related})
    overview = clean_inline(data.get("overview", ""), 1000)
    if not overview and not trends:
        raise ValueError("주간 요약 결과가 비어 있습니다.")
    return overview, trends[:WEEKLY_TRENDS]


def render_weekly(week, monday, overview, trends, articles):
    import jinja2
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(CONFIG_DIR),
        trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
    )
    return env.get_template(WEEKLY_TEMPLATE).render(
        week=week,
        start=f"{monday:%Y-%m-%d}",
        end=f"{monday + datetime.timedelta(days=6):%Y-%m-%d}",
        overview=overview,
        trends=trends,
        articles=articles,
    )


def main():
    print("--- 주간 요약 시작 ---")
    monday = parse_week(sys.argv[1]) if len(sys.argv) > 1 else previous_week_monday(run_date().date())
    iso_year, iso_week, _ = monday.isocalendar()
    week = f"{iso_year}-W{iso_week:02d}"

    articles = load_week_articles(monday)
    print(f"{week} ({monday:%m-%d} ~ {monday + datetime.timedelta(days=6):%m-%d}): 요약 기사 {len(articles)}건")
    if not articles:
        print(" -> 요약한 기사가 없어 주간 리포트를 만들지 않습니다.")
        return

    try:
        overview, trends = find_trends(articles)
    except Exception as e:
        fail(f"주간 요약에 실패했습니다: {e}")

    content = render_weekly(week, monday, overview, trends, articles)
    ensure_dir(WEEKLY_DIR)
    path = os.path.join(WEEKLY_DIR, f"{week}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content.rstrip("\n") + "\n")
    update_index(os.path.join(WEEKLY_DIR, "index.md"), "주간 AI 뉴스", f"{week}.md", "주별 AI 뉴스 흐름 요약입니다.")
    print(f"{os.path.relpath(path, ROOT_DIR)} 생성 완료 (흐름 {len(trends)}개, 기사 {len(articles)}건)")
    print("--- 주간 요약 종료 ---")


if __name__ == "__main__":
    main()
