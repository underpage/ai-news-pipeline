import datetime
import types

import feedparser
import pytest

import collect
import common


def entry(raw_date):
    feed = feedparser.parse(
        f"<rss><channel><item><title>t</title><link>http://a/b</link><pubDate>{raw_date}</pubDate></item></channel></rss>")
    return feed.entries[0]


@pytest.mark.parametrize("raw, naive_kst", [
    ("2026-09-30 18:22:00", True),
    ("Wed, 30 Sep 2026 18:22:00 +0900", True),
    ("Wed, 30 Sep 2026 09:22:00 GMT", True),
    ("2026-09-30T18:22:00+09:00", False),
    ("2026-09-30 18:22:00 KST", False),
    ("Wed, 30 Sep 2026 18:22:00 KST", False),
    ("Wed, 30 Sep 2026 18:22:00", True),
])
def test_published_time_is_read_as_kst(raw, naive_kst):
    published = collect.get_published_time(entry(raw), naive_kst)
    assert published.astimezone(collect.KST).strftime("%m-%d %H:%M") == "09-30 18:22"


def test_unreadable_date_is_none():
    assert collect.get_published_time(entry("garbage"), True) is None


@pytest.mark.parametrize("title, expected", [
    ("OpenAI launches agent SDK", ["에이전트", "개발"]),
    ("New ransomware group hits hospitals", ["보안"]),
    ("삼성, 2나노 반도체 양산 돌입", ["신기술"]),
    ("축구 대표팀 공격수 부상", []),
    ("핵심 인재 유출 우려", []),
    ("Apple announces new iPhone color", []),
    ("He said the email was fine", []),
])
def test_topics_and_collection_condition(title, expected):
    assert collect.match_topics(title, "") == expected


def test_ai_article_without_topic_gets_default_topic():
    assert collect.match_topics("AI 업계 소식", "") == [collect.DEFAULT_TOPIC]


def test_similar_titles_are_removed_keeping_the_first():
    candidates = [{"title": title} for title in [
        "AI Coding Agents Exposed 13,000 Internal Images on GitHub",
        "AI coding agents exposed 13,000 internal images on GitHub - The Hacker News",
        "오픈AI, 데브데이서 에이전트 '닷' 공개",
        "오픈AI, 데브데이에서 에이전트 ‘닷’ 공개",
        "OpenAI unveils Dots agent at DevDay",
    ]]
    kept = [news["title"] for news in collect.remove_similar_titles(candidates, known_titles=["OpenAI unveils Dots agent at DevDay"])]
    assert kept == ["AI Coding Agents Exposed 13,000 Internal Images on GitHub", "오픈AI, 데브데이서 에이전트 '닷' 공개"]


def test_config_check_rejects_bad_values(monkeypatch):
    monkeypatch.setattr(collect, "AI_TERMS", [])
    with pytest.raises(ValueError, match="ai_terms"):
        collect.check_config()
    monkeypatch.setattr(collect, "AI_TERMS", ["AI"])
    monkeypatch.setattr(collect, "FEEDS", [{"name": "F", "url": "u", "naive_kst": "false"}])
    with pytest.raises(ValueError, match="naive_kst"):
        collect.check_config()


def test_history_skips_articles_that_were_never_evaluated(monkeypatch):
    # 어제: 수집만 되고 선정이 실패함 / 그제: 선정까지 끝남
    monkeypatch.setenv("RUN_DATE", "2026-09-29")
    common.append_record("수집", [("TITLE", "C"), ("LINK", "http://x/C")])
    common.append_record("필터링", [("TITLE", "C"), ("LINK", "http://x/C"), ("SCORE", 8)])
    monkeypatch.setenv("RUN_DATE", "2026-09-30")
    common.append_record("수집", [("TITLE", "A"), ("LINK", "http://x/A")])
    common.append_error("필터링", "평가 오류", "전체 후보", "429")

    monkeypatch.setenv("RUN_DATE", "2026-10-01")
    assert collect.load_history_from_logs() == {"http://x/C"}
    common.append_record("수집", [("TITLE", "A"), ("LINK", "http://x/A")])
    assert collect.load_history_from_logs() == {"http://x/C", "http://x/A"}


def fake_feed(titles, hours_ago=1):
    now = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours_ago)
    items = "".join(
        f"<item><title>{title}</title><link>http://site/{index}</link>"
        f"<pubDate>{now:%a, %d %b %Y %H:%M:%S} GMT</pubDate><description>OpenAI model</description></item>"
        for index, title in enumerate(titles))
    return f"<rss><channel><title>f</title>{items}</channel></rss>".encode()


def test_daily_limit_counts_earlier_runs(monkeypatch, capsys):
    monkeypatch.setenv("COLLECT_MAX", "3")
    monkeypatch.setattr(collect, "FEEDS", [{"name": "F", "url": "http://feed"}])
    titles = ["OpenAI ships agent SDK", "Anthropic adds tool use", "Google releases Gemini update",
              "Meta opens Llama weights", "Microsoft launches Copilot agents"]
    content = {"xml": fake_feed(titles)}
    monkeypatch.setattr(collect, "fetch", lambda url, timeout=15: types.SimpleNamespace(content=content["xml"]))

    collect.main()
    collect.main()
    titles = [record["title"] for record in common.read_records("수집") if record.get("title")]
    assert len(titles) == 3
    assert "NO_ARTICLES" not in open(common.get_log_path("수집"), encoding="utf-8").read()


def test_all_feeds_failing_fails_the_job(monkeypatch):
    monkeypatch.setattr(collect, "FEEDS", [{"name": "F", "url": "http://feed"}])
    monkeypatch.setattr(collect, "fetch", lambda url, timeout=15: types.SimpleNamespace(content=b"<!doctype html><html>blocked</html>"))
    with pytest.raises(SystemExit):
        collect.main()
    assert common.read_errors("수집")[0][0] == "http://feed"
