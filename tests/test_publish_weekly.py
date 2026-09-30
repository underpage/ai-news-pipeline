import os
import csv
import json
import datetime

import common
import publish
import weekly


def add_target(number, score, summary=None, model="gemini-2.5-flash"):
    link = f"http://a/{number}"
    common.append_record("필터링", [("TITLE", f"기사 {number}"), ("LINK", link), ("SCORE", score), ("TOPIC", "보안")],
                         block=("BODY", "본문"))
    if summary:
        common.append_record("요약", [("TITLE", f"기사 {number}"), ("LINK", link), ("SCORE", score), ("MODEL", model)],
                             block=("SUMMARY", summary))
    return link


KOREAN_SUMMARY = "**[국문 3줄 요약]**\n- 가.\n- 나.\n- 다.\n\n#긍정"


def test_report_marks_every_summary_status():
    add_target(1, 9, KOREAN_SUMMARY)
    add_target(2, 8, KOREAN_SUMMARY, model="manual")
    failed = add_target(3, 7)
    unavailable = add_target(4, 6)
    add_target(5, 5)  # 시도하지 못함
    common.append_error("요약", "요약 오류", failed, "요약 실패: 503")
    common.append_error("요약", "요약 오류", unavailable, "요약 불가: 로그인 요구")

    publish.main()
    report = open(os.path.join(common.NEWS_DIR, "2026", "10", "2026-10-01.md"), encoding="utf-8").read()

    assert report.startswith("# 2026-10-01\n")
    order = [line for line in report.splitlines() if line.startswith("## ")]
    assert order[:5] == [f"## {n}. 기사 {n}" for n in range(1, 6)]
    for marker in ("[수동 요약]", "[요약 실패]", "[요약 불가]", "[요약 대기]"):
        assert report.count(marker) == 1
    assert "- **태그**: 긍정" in report
    assert "{{" not in report


def test_tone_tag_only_on_its_own_line():
    _, _, summary_ko, tone = publish.parse_summary_blocks("**[국문 3줄 요약]**\n- 이 소식은 #긍정 평가.\n- 둘.\n- 셋.\n\n#부정")
    assert summary_ko[0] == "이 소식은 #긍정 평가." and tone == "#부정"


def test_stats_row_is_replaced_on_rerun():
    add_target(1, 9, KOREAN_SUMMARY)
    failed = add_target(2, 8)
    common.append_error("요약", "요약 오류", failed, "요약 실패: 503")
    common.append_error("수집", "수집 오류", "http://feed", "429")

    publish.main()
    publish.main()
    path = os.path.join(common.DATA_DIR, "stats", "2026.csv")
    rows = list(csv.DictReader(open(path, encoding="utf-8", newline="")))
    assert len(rows) == 1
    row = rows[0]
    assert (row["date"], row["summary_target"], row["summary_ok"], row["summary_failed"], row["feeds_failed"]) == \
        ("2026-10-01", "2", "1", "1", "1")


def test_weekly_report(monkeypatch):
    for day in ("2026-09-28", "2026-10-04"):
        monkeypatch.setenv("RUN_DATE", day)
        common.append_record("요약", [("TITLE", f"{day} 기사"), ("LINK", f"http://w/{day}"), ("SCORE", 9 if day.endswith("28") else 7)],
                             block=("SUMMARY", KOREAN_SUMMARY))
    monkeypatch.setenv("RUN_DATE", "2026-10-05")  # 다음 주 월요일 = 다른 주
    common.append_record("요약", [("TITLE", "다음 주"), ("LINK", "http://w/next"), ("SCORE", 10)], block=("SUMMARY", KOREAN_SUMMARY))

    monday = weekly.previous_week_monday(datetime.date(2026, 10, 7))
    assert monday == datetime.date(2026, 9, 28) == weekly.parse_week("2026-W40")
    articles = weekly.load_week_articles(monday)
    assert [article["link"] for article in articles] == ["http://w/2026-09-28", "http://w/2026-10-04"]

    monkeypatch.setattr(weekly, "call_gemini", lambda prompt, schema=None: json.dumps(
        {"overview": "개요.", "trends": [{"title": "흐름", "description": "설명.", "article_ids": [2, 1, 99]},
                                        {"title": "근거 없음", "description": "x", "article_ids": []}]}, ensure_ascii=False))
    overview, trends = weekly.find_trends(articles)
    assert [trend["title"] for trend in trends] == ["흐름"]
    assert [article["id"] for article in trends[0]["articles"]] == [2, 1]
    content = weekly.render_weekly("2026-W40", monday, overview, trends, articles)
    assert content.startswith("# 2026-W40 (2026-09-28 ~ 2026-10-04)")
    assert "## 이번 주 기사 (2건)" in content
