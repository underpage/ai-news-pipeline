import json

import pytest

import common
import evaluate
import summarize


def add_candidates(count):
    for number in range(1, count + 1):
        common.append_record("수집", [
            ("TITLE", f"기사 {number}"), ("LINK", f"http://a/{number}"), ("PUBLISHED", "2026-10-01 08:00 KST"),
            ("SOURCE", "매체"), ("TOPIC", "에이전트"),
        ], block=("SUMMARY", f"미리보기 {number}"))


def rank_response(pairs):
    return json.dumps([{"id": i, "score": s, "reason": f"이유 {i}"} for i, s in pairs], ensure_ascii=False)


def test_rank_articles_validates_response(monkeypatch):
    news = [{"title": f"t{n}", "link": f"l{n}"} for n in range(5)]
    wrapped = json.dumps({"items": [{"id": 1, "score": 9, "reason": "[a](http://b) 이유"}, {"id": 2, "score": 3, "reason": "낮음"},
                                    {"id": 99, "score": 9, "reason": "범위 밖"}, {"id": 1, "score": 5, "reason": "중복"},
                                    {"id": 3, "score": 6, "reason": "r"}]})
    monkeypatch.setattr(evaluate, "call_gemini", lambda prompt, schema=None: wrapped)
    assert evaluate.rank_articles(news, 5, 4) == [(0, 9, "a 이유"), (2, 6, "r")]
    monkeypatch.setattr(evaluate, "call_gemini", lambda prompt, schema=None: "[]")
    assert evaluate.rank_articles(news, 5, 4) == []


def test_prompt_contains_all_candidates_with_labels(monkeypatch):
    seen = {}
    def fake(prompt, schema=None):
        seen["prompt"] = prompt
        return "[]"
    monkeypatch.setattr(evaluate, "call_gemini", fake)
    evaluate.rank_articles([{"title": "제목 {body}", "link": "l", "source": "S", "published": "P", "topic": "T", "summary": "요약"}], 5, 4)
    assert "[ID: 1]\n제목: 제목 {body}\n출처: S | 발행: P | 주제: T\n미리보기: 요약" in seen["prompt"]
    assert "{pick_n}" not in seen["prompt"] and "{source_max}" not in seen["prompt"]


def test_cut_at_sentence():
    text = "가나다. " * 1000
    cut = evaluate.cut_at_sentence(text, 4000)
    assert len(cut) <= 4000 and cut.endswith(".")


def test_evaluate_fills_list_and_summary_slots_across_reruns(monkeypatch):
    # LIST_N=5, TOP_N=3 (conftest)
    add_candidates(8)
    monkeypatch.setattr(evaluate, "call_gemini", lambda prompt, schema=None: rank_response([(n, 10 - n) for n in range(1, 7)]))
    # 2위 기사는 본문을 가져오지 못한다
    monkeypatch.setattr(evaluate, "scrape_body", lambda url: "" if url == "http://a/2" else "본문 " * 200)
    evaluate.main()

    records = common.read_records("필터링")
    listed = [record for record in records if record.get("title")]
    assert [record["link"] for record in listed] == [f"http://a/{n}" for n in range(1, 6)]
    assert [record["link"] for record in listed if record.get("body")] == ["http://a/1", "http://a/3", "http://a/4"]
    assert len([record for record in records if not record.get("title")]) == 3

    # 같은 날 다시 실행해도 목록·요약 건수가 늘지 않는다
    add_candidates(10)
    evaluate.main()
    assert len([record for record in common.read_records("필터링") if record.get("title")]) == 5


def test_evaluate_failure_fails_the_job(monkeypatch):
    add_candidates(2)
    def boom(prompt, schema=None):
        raise RuntimeError("429 quota")
    monkeypatch.setattr(evaluate, "call_gemini", boom)
    with pytest.raises(SystemExit):
        evaluate.main()
    assert common.read_errors("필터링")[0][0] == "전체 후보"


def answer(**overrides):
    data = {"lang": "en", "title_ko": "번역 제목", "summary_ko": ["가.", "나.", "다."], "summary_en": ["a.", "b.", "c."], "tone": "중립"}
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def test_parse_summary_by_language():
    assert summarize.parse_summary(answer()).startswith("**[기사 제목 번역]**\n번역 제목\n\n**[영문 3줄 요약 (Original)]**")
    korean = summarize.parse_summary(answer(lang="ko", summary_en=[]))
    assert korean.startswith("**[국문 3줄 요약]**") and korean.endswith("#중립")
    other = summarize.parse_summary(answer(lang="other", summary_en=[]))
    assert other.startswith("**[기사 제목 번역]**") and "영문" not in other


@pytest.mark.parametrize("bad, message", [
    (answer(summary_ko=["가.", "나."]), "3줄"),
    (answer(summary_en=[]), "영문"),
    (answer(tone="애매"), "논조"),
    (answer(lang="ko", summary_en=[], summary_ko=["...", "...", "..."]), "3줄"),
    ("그냥 텍스트", "해석"),
])
def test_parse_summary_rejects_bad_answers(bad, message):
    with pytest.raises(ValueError, match=message):
        summarize.parse_summary(bad)


def test_model_refusal_is_not_summarizable():
    with pytest.raises(summarize.NotSummarizable):
        summarize.parse_summary(json.dumps({"error": "로그인 요구 페이지"}, ensure_ascii=False))


def test_summarize_records_model_and_failure_kinds(monkeypatch):
    for number, url in enumerate(["http://a/1", "http://a/2", "http://a/3"], start=1):
        common.append_record("필터링", [("TITLE", f"t{number}"), ("LINK", url), ("SCORE", 9)], block=("BODY", "본문"))
    replies = {"t1": answer(lang="ko", summary_en=[]), "t2": json.dumps({"error": "차단 안내"}, ensure_ascii=False)}

    def fake(prompt, schema=None):
        for title, reply in replies.items():
            if f"제목: {title}\n" in prompt:
                return reply
        raise RuntimeError("503")
    monkeypatch.setattr(summarize, "call_gemini", fake)
    summarize.main()

    summaries = common.read_records("요약")
    assert [record["link"] for record in summaries] == ["http://a/1"]
    assert summaries[0]["model"] == common.GEMINI_MODEL
    reasons = dict(common.read_errors("요약"))
    assert reasons["http://a/2"].startswith("요약 불가")
    assert reasons["http://a/3"].startswith("요약 실패")
