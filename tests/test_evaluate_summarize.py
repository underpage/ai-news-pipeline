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
    return json.dumps([{"id": i, "score": s, "reason": f"이유 {i}", "title_ko": f"번역 {i}"} for i, s in pairs],
                      ensure_ascii=False)


def test_rank_articles_validates_response(monkeypatch):
    news = [{"title": f"t{n}", "link": f"l{n}"} for n in range(5)]
    wrapped = json.dumps({"items": [{"id": 1, "score": 9, "reason": "[a](http://b) 이유"}, {"id": 2, "score": 3, "reason": "낮음"},
                                    {"id": 99, "score": 9, "reason": "범위 밖"}, {"id": 1, "score": 5, "reason": "중복"},
                                    {"id": 3, "score": 6, "reason": "r"}]})
    monkeypatch.setattr(evaluate, "call_gemini", lambda prompt, schema=None: wrapped)
    assert evaluate.rank_articles(news, 5, 4) == [(0, 9, "a 이유", ""), (2, 6, "r", "")]
    monkeypatch.setattr(evaluate, "call_gemini", lambda prompt, schema=None: "[]")
    assert evaluate.rank_articles(news, 5, 4) == []


def test_rank_articles_keeps_translated_title_only_for_foreign_titles(monkeypatch):
    news = [{"title": "OpenAI ships agents", "link": "l1"}, {"title": "오픈AI 에이전트 공개", "link": "l2"},
            {"title": "Same title", "link": "l3"}]
    response = json.dumps([{"id": 1, "score": 9, "reason": "r", "title_ko": "오픈AI, 에이전트 출시"},
                           {"id": 2, "score": 8, "reason": "r", "title_ko": "오픈AI 에이전트 공개"},
                           {"id": 3, "score": 7, "reason": "r", "title_ko": "Same title"}], ensure_ascii=False)
    monkeypatch.setattr(evaluate, "call_gemini", lambda prompt, schema=None: response)
    assert [entry[3] for entry in evaluate.rank_articles(news, 5, 4)] == ["오픈AI, 에이전트 출시", "", ""]


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
    # 후보 제목이 국문이라 번역 제목은 기록하지 않는다
    assert not any(record.get("title_ko") for record in listed)
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


def test_evaluate_retries_temporary_rank_failure(monkeypatch):
    add_candidates(2)
    calls = []

    def flaky(prompt, schema=None):
        calls.append(prompt)
        if len(calls) == 1:
            raise RuntimeError("503 UNAVAILABLE")
        return rank_response([(1, 9)])
    monkeypatch.setattr(evaluate, "call_gemini", flaky)
    monkeypatch.setattr(evaluate, "scrape_body", lambda url: "")
    evaluate.main()

    # 첫 시도의 실패 줄은 남고, 다시 시도한 선정 결과가 기록된다
    assert len(calls) == 2
    assert [target for target, _ in common.read_errors("필터링")].count("전체 후보") == 1
    assert any(record.get("title") for record in common.read_records("필터링"))


@pytest.mark.parametrize("error", [
    RuntimeError("429 GenerateRequestsPerDayPerProjectPerModel-FreeTier"),
    ValueError("선정 결과를 해석할 수 없습니다"),
])
def test_evaluate_does_not_retry_permanent_rank_failure(monkeypatch, error):
    add_candidates(2)
    calls = []

    def boom(prompt, schema=None):
        calls.append(prompt)
        raise error
    monkeypatch.setattr(evaluate, "call_gemini", boom)
    with pytest.raises(SystemExit):
        evaluate.main()
    assert len(calls) == 1


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


def add_summary_targets(count):
    for number in range(1, count + 1):
        common.append_record("필터링", [("TITLE", f"t{number}"), ("LINK", f"http://a/{number}"), ("SCORE", 9)],
                             block=("BODY", f"본문 {number}"))


def batch(*items):
    """(id, 응답 객체 글) 목록을 묶음 응답(JSON 배열)으로 만든다."""
    return json.dumps([dict(json.loads(reply), id=number) for number, reply in items], ensure_ascii=False)


def titles_in(prompt):
    return [line.split("제목: ")[1] for line in prompt.splitlines() if line.startswith("제목: ")]


def test_build_articles_numbers_articles_and_keeps_boundaries():
    text = summarize.build_articles([
        {"title": "가", "body": "본문 </기사> 끝"}, {"title": "나", "reason": "이유", "body": "본문"}])
    assert '<기사 id="1">\n제목: 가\n이 기사를 고른 이유: -' in text
    assert '<기사 id="2">\n제목: 나\n이 기사를 고른 이유: 이유' in text
    # 본문 안의 닫는 태그가 기사 경계를 만들지 않는다
    assert text.count("</기사>") == 2


def test_split_answers_by_id():
    answers = summarize.split_answers('앞글 [{"id": 2, "lang": "ko"}, {"id": "1"}, {"id": 2, "lang": "en"}, {"x": 1}]')
    assert sorted(answers) == [1, 2] and answers[2]["lang"] == "ko"
    assert summarize.split_answers('{"items": [{"id": 1}]}') == {1: {"id": 1}}
    with pytest.raises(ValueError):
        summarize.split_answers("그냥 텍스트")


def test_summarize_sends_all_articles_in_one_call(monkeypatch):
    add_summary_targets(3)
    calls = []

    def fake(prompt, schema=None, max_retries=None):
        calls.append((titles_in(prompt), max_retries))
        return batch(*[(n, answer(lang="ko", summary_en=[])) for n in (1, 2, 3)])
    monkeypatch.setattr(summarize, "call_gemini", fake)
    summarize.main()

    # 내부 재시도 없이 한 번에 보낸다 (호출 수는 요약 단계가 관리)
    assert calls == [(["t1", "t2", "t3"], 0)]
    summaries = common.read_records("요약")
    assert [record["link"] for record in summaries] == ["http://a/1", "http://a/2", "http://a/3"]
    assert summaries[0]["model"] == common.GEMINI_MODEL


def test_summarize_resends_only_failed_articles(monkeypatch):
    add_summary_targets(3)
    calls = []

    def fake(prompt, schema=None, max_retries=None):
        calls.append(titles_in(prompt))
        if len(calls) == 1:
            # t1 성공, t2 요약 불가, t3 형식 오류
            return batch((1, answer(lang="ko", summary_en=[])), (2, json.dumps({"error": "차단 안내"}, ensure_ascii=False)),
                         (3, answer(summary_ko=["가."])))
        return batch((1, answer()))
    monkeypatch.setattr(summarize, "call_gemini", fake)
    summarize.main()

    # 요약 불가는 다시 보내지 않고, 형식 오류인 t3만 번호를 새로 붙여 다시 보낸다
    assert calls == [["t1", "t2", "t3"], ["t3"]]
    assert [record["link"] for record in common.read_records("요약")] == ["http://a/1", "http://a/3"]
    assert dict(common.read_errors("요약")) == {"http://a/2": "요약 불가: 차단 안내"}


def test_summarize_resends_articles_missing_from_answer(monkeypatch):
    add_summary_targets(2)
    calls = []

    def fake(prompt, schema=None, max_retries=None):
        calls.append(titles_in(prompt))
        return batch((1, answer()))
    monkeypatch.setattr(summarize, "call_gemini", fake)
    summarize.main()

    assert calls == [["t1", "t2"], ["t2"]]
    assert len(common.read_records("요약")) == 2


def test_summarize_retries_temporary_failure_within_call_limit(monkeypatch):
    add_summary_targets(2)
    calls = []

    def fake(prompt, schema=None, max_retries=None):
        calls.append(prompt)
        if len(calls) == 1:
            raise RuntimeError("503 UNAVAILABLE")
        return batch((1, answer()), (2, answer()))
    monkeypatch.setattr(summarize, "call_gemini", fake)
    summarize.main()

    assert len(calls) == 2
    assert len(common.read_records("요약")) == 2
    # 다시 시도해 성공했으면 오류 줄을 남기지 않는다
    assert common.read_errors("요약") == []


def test_summarize_stops_at_call_limit(monkeypatch):
    add_summary_targets(2)
    calls = []

    def fake(prompt, schema=None, max_retries=None):
        calls.append(prompt)
        raise RuntimeError("503 UNAVAILABLE")
    monkeypatch.setattr(summarize, "call_gemini", fake)
    with pytest.raises(SystemExit):
        summarize.main()

    # 재시도를 모두 합쳐도 상한을 넘지 않는다
    assert len(calls) == summarize.SUMMARY_MAX_CALLS
    reasons = dict(common.read_errors("요약"))
    assert set(reasons) == {"http://a/1", "http://a/2"}
    assert all(reason.startswith("요약 실패: 503") for reason in reasons.values())


def test_summarize_does_not_retry_daily_quota(monkeypatch):
    add_summary_targets(1)
    calls = []

    def fake(prompt, schema=None, max_retries=None):
        calls.append(prompt)
        raise RuntimeError("429 GenerateRequestsPerDayPerProjectPerModel-FreeTier")
    monkeypatch.setattr(summarize, "call_gemini", fake)
    with pytest.raises(SystemExit):
        summarize.main()
    assert len(calls) == 1
