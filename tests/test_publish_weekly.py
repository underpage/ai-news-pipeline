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


def test_news_list_excludes_articles_in_summary_section():
    add_target(1, 9, KOREAN_SUMMARY)
    failed = add_target(2, 8)
    common.append_error("요약", "요약 오류", failed, "요약 실패: 503")
    # 목록에만 오른 기사 (본문 없음)
    for number, score in ((3, 7), (4, 6)):
        common.append_record("필터링", [("TITLE", f"기사 {number}"), ("LINK", f"http://a/{number}"), ("SCORE", score)])

    publish.main()
    report = open(os.path.join(common.NEWS_DIR, "2026", "10", "2026-10-01.md"), encoding="utf-8").read()
    news_list = report.split("## 더 읽어볼 기사")[1]
    assert news_list.startswith(" (2건)")
    # 요약 기사와 요약 실패 기사는 목록에 다시 나오지 않고, 순위는 전체 기준으로 이어진다
    assert "http://a/1" not in news_list and "http://a/2" not in news_list
    assert "3. [기사 3](http://a/3)" in news_list and "4. [기사 4](http://a/4)" in news_list
    assert "★" not in report


def test_translated_titles_from_selection_step():
    # 요약에 실패한 기사와 목록 기사는 선정 단계의 번역 제목을 쓴다
    failed = "http://a/1"
    common.append_record("필터링", [("TITLE", "Agents ship"), ("TITLE_KO", "에이전트 출시"), ("LINK", failed), ("SCORE", 9)],
                         block=("BODY", "본문"))
    common.append_error("요약", "요약 오류", failed, "요약 실패: 429")
    common.append_record("필터링", [("TITLE", "Patch now"), ("TITLE_KO", "[긴급] 지금 패치하라"), ("LINK", "http://a/2"),
                                   ("SCORE", 8), ("TOPIC", "보안, 개발"), ("SOURCE", "SecurityWeek"),
                                   ("PUBLISHED", "2026-10-01 09:00 KST")])
    common.append_record("필터링", [("TITLE", "국문 기사"), ("LINK", "http://a/3"), ("SCORE", 7), ("SOURCE", "AI타임스")])

    publish.main()
    report = open(os.path.join(common.NEWS_DIR, "2026", "10", "2026-10-01.md"), encoding="utf-8").read()
    assert "- **번역 제목**: 에이전트 출시" in report
    # 목록은 한 줄: 번역 제목이 원문 제목을 대신하고, 발행일시는 싣지 않는다
    assert ("2. \\[번역\\] [(긴급) 지금 패치하라](http://a/2) (보안) - SecurityWeek\n"
            "3. [국문 기사](http://a/3) - AI타임스\n") in report
    assert "Patch now" not in report and "09:00 KST" not in report


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


def write_totals(day, totals):
    folder = os.path.join(common.LOGS_DIR, day[:4], day[5:7])
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"{day}-점수.csv"), "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["결정", "합계", "링크"])
        writer.writerows(["요약", total, link] for link, total in totals.items())


def test_weekly_report(monkeypatch):
    for day, score, title_ko in (("2026-09-28", 9, ""), ("2026-10-01", 9, "번역된 제목"), ("2026-10-04", 7, "")):
        monkeypatch.setenv("RUN_DATE", day)
        summary = (f"**[기사 제목 번역]**\n{title_ko}\n\n" if title_ko else "") + KOREAN_SUMMARY
        common.append_record("요약", [("TITLE", f"{day} 기사"), ("LINK", f"http://w/{day}"), ("SCORE", score),
                                     ("SOURCE", "매체"), ("TOPIC", "보안, 에이전트")], block=("SUMMARY", summary))
    monkeypatch.setenv("RUN_DATE", "2026-10-05")  # 다음 주 월요일 = 다른 주
    common.append_record("요약", [("TITLE", "다음 주"), ("LINK", "http://w/next"), ("SCORE", 10)], block=("SUMMARY", KOREAN_SUMMARY))
    # 같은 [SCORE] 9라도 합계가 높은 10-01 기사가 먼저. 점수표가 없는 날은 [SCORE] x 10
    write_totals("2026-09-28", {"http://w/2026-09-28": 86.7})
    write_totals("2026-10-01", {"http://w/2026-10-01": 93.3})

    monday = weekly.previous_week_monday(datetime.date(2026, 10, 7))
    assert monday == datetime.date(2026, 9, 28) == weekly.parse_week("2026-W40")
    articles = weekly.load_week_articles(monday)
    assert [article["link"] for article in articles] == ["http://w/2026-10-01", "http://w/2026-09-28", "http://w/2026-10-04"]
    assert [article["total"] for article in articles] == [93.3, 86.7, 70]

    monkeypatch.setattr(weekly, "call_gemini", lambda prompt, schema=None: json.dumps(
        {"overview": "개요.", "trends": [{"title": "흐름", "description": "설명.", "article_ids": [2, 1, 99]},
                                        {"title": "근거 없음", "description": "x", "article_ids": []}]}, ensure_ascii=False))
    overview, trends = weekly.find_trends(articles)
    assert [trend["title"] for trend in trends] == ["흐름"]
    assert [article["id"] for article in trends[0]["articles"]] == [2, 1]
    content = weekly.render_weekly("2026-W40", monday, overview, trends, articles)
    assert content.startswith("# 2026-W40 (2026-09-28 ~ 2026-10-04)")
    assert "## 이번 주 기사 (3건)" in content
    # 일간 리포트의 "더 읽어볼 기사"와 같은 형식에 날짜. 번역 제목이 있으면 \[번역\] 표시, 주제는 첫 번째만
    assert "1. \\[번역\\] [번역된 제목](http://w/2026-10-01) (보안) - 매체, 2026-10-01\n\n" in content
    assert "2. [2026-09-28 기사](http://w/2026-09-28) (보안) - 매체, 2026-09-28\n" in content

    # 연도 폴더에 저장하고 목록도 연도 폴더에
    monkeypatch.setattr(weekly.sys, "argv", ["weekly.py", "2026-W40"])
    weekly.main()
    assert os.path.exists(os.path.join(weekly.WEEKLY_DIR, "2026", "2026-W40.md"))
    index = open(os.path.join(weekly.WEEKLY_DIR, "2026", "index.md"), encoding="utf-8").read()
    assert index.startswith("# 2026 주간 AI 뉴스") and "(./2026-W40.md)" in index
