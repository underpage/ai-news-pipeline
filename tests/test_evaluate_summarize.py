import json

import pytest

import common
import evaluate
import summarize


def add_candidates(count, start=1):
    for number in range(start, start + count):
        common.append_record("수집", [
            ("TITLE", f"기사 {number}"), ("LINK", f"http://a/{number}"), ("PUBLISHED", "2026-10-01 08:00 KST"),
            ("SOURCE", f"매체 {number}"), ("TOPIC", "에이전트"),
        ], block=("SUMMARY", f"미리보기 {number}"))


def score_item(number, impact=5, practical=5, novelty=5, certainty=5, **overrides):
    item = {"id": number, "impact": impact, "practical": practical, "novelty": novelty, "certainty": certainty,
            "dup_of": 0, "exclude": "", "reason": f"이유 {number}", "title_ko": f"번역 {number}"}
    item.update(overrides)
    return item


def score_response(*items):
    return json.dumps(list(items), ensure_ascii=False)


def news(number, **fields):
    return {"title": f"t{number}", "link": f"l{number}", "source": f"S{number}", "published": "2026-10-01 08:00 KST",
            "topic": "에이전트", **fields}


def test_total_score_scale():
    lowest = {name: 1 for name in evaluate.CRITERIA}
    highest = {name: 5 for name in evaluate.CRITERIA}
    assert evaluate.total_score(lowest) == 0
    assert evaluate.total_score(highest) == 100
    # 파급력만 5점이면 파급력 가중치만큼
    assert evaluate.total_score({**lowest, "impact": 5}) == evaluate.CRITERIA_WEIGHTS["impact"] * 100


def test_score_articles_validates_response(monkeypatch):
    news_list = [news(n) for n in range(1, 6)]
    wrapped = json.dumps({"items": [
        score_item(1, reason="[a](http://b) 이유", dup_of=1), score_item(2, impact=9, practical=0, dup_of=1),
        score_item(99), score_item(1, impact=1), {"id": 3, "impact": "x"}, score_item(4, dup_of=77),
    ]}, ensure_ascii=False)
    monkeypatch.setattr(evaluate, "call_gemini", lambda prompt, schema=None, max_retries=None: wrapped)
    scored = evaluate.score_articles(news_list)

    # 범위 밖 ID, 같은 ID 두 번째, 점수가 숫자가 아닌 항목은 버린다
    assert sorted(scored) == [0, 1, 3]
    assert scored[0]["reason"] == "a 이유" and scored[0]["total"] == 100
    # 자기 자신이나 범위 밖을 가리키는 dup_of는 무시한다
    assert scored[0]["dup_of"] is None and scored[3]["dup_of"] is None
    assert scored[1]["dup_of"] == 0
    # 점수는 1~5로 맞춘다
    assert scored[1]["impact"] == 5 and scored[1]["practical"] == 1


def test_score_articles_requires_some_scores(monkeypatch):
    monkeypatch.setattr(evaluate, "call_gemini", lambda prompt, schema=None, max_retries=None: "[]")
    with pytest.raises(ValueError):
        evaluate.score_articles([news(1)])


def test_score_articles_keeps_translated_title_only_for_foreign_titles(monkeypatch):
    news_list = [news(1, title="OpenAI ships agents"), news(2, title="오픈AI 에이전트 공개"), news(3, title="Same title")]
    response = score_response(score_item(1, title_ko="오픈AI, 에이전트 출시"), score_item(2, title_ko="오픈AI 에이전트 공개"),
                              score_item(3, title_ko="Same title"))
    monkeypatch.setattr(evaluate, "call_gemini", lambda prompt, schema=None, max_retries=None: response)
    scored = evaluate.score_articles(news_list)
    assert [scored[index]["title_ko"] for index in range(3)] == ["오픈AI, 에이전트 출시", "", ""]


def test_prompt_contains_all_candidates_with_labels(monkeypatch):
    seen = {}

    def fake(prompt, schema=None, max_retries=None):
        seen["prompt"], seen["max_retries"] = prompt, max_retries
        return score_response(score_item(1))
    monkeypatch.setattr(evaluate, "call_gemini", fake)
    evaluate.score_articles([{"title": "제목 {body}", "link": "l", "source": "S", "published": "P", "topic": "T", "summary": "요약"}])
    assert "[ID: 1]\n제목: 제목 {body}\n출처: S | 발행: P | 주제: T\n미리보기: 요약" in seen["prompt"]
    assert "{articles}" not in seen["prompt"]
    # 호출 횟수는 선정 단계가 관리한다
    assert seen["max_retries"] == 0


def test_prefilter_drops_patterns_and_caps(monkeypatch):
    monkeypatch.setattr(evaluate, "PREFILTER_SOURCE_MAX", 2)
    news_list = [
        news(1, title="AI 보안 세미나 개최 안내"),
        news(2, topic="일반", published="2026-10-01 12:00 KST"),
        news(3, topic="에이전트, 보안", published="2026-10-01 07:00 KST"),
        news(4, published="2026-10-01 09:00 KST", source="A"),
        news(5, published="2026-10-01 10:00 KST", source="A"),
        news(6, published="2026-10-01 11:00 KST", source="A"),
        news(7, title="Award-winning agent", source="B"),
    ]
    kept, dropped = evaluate.prefilter(news_list, limit=3)

    # 주제 가중치 합이 큰 기사부터, 같으면 최근 기사부터
    assert [item["link"] for item in kept] == ["l3", "l6", "l5"]
    assert dict((item["link"], reason) for item, reason in dropped) == {
        "l1": "사전 제외: 행사", "l7": "사전 제외: 수상", "l4": "사전 제외: 매체 상한", "l2": "사전 제외: 후보 상한",
    }


def test_decide_handles_duplicates_exclusions_and_caps():
    news_list = [news(1), news(2), news(3, source="S1"), news(4), news(5), news(6, topic="보안"), news(7)]
    totals = {0: 90, 1: 95, 2: 80, 3: 70, 4: 30, 5: 70, 6: 60}
    scored = {index: {"total": total, "dup_of": None, "exclude": ""} for index, total in totals.items()}
    scored[0]["dup_of"] = 1          # 1번은 2번과 같은 사건 (2번 합계가 더 높음)
    scored[6]["exclude"] = "홍보"
    listed, decisions = evaluate.decide(news_list, scored, list_slots=3, list_n=5, min_score=4)

    # 매체 상한은 max(2, LIST_N // 5) = 2. 3번은 1번과 매체가 같지만 1번이 중복으로 빠져 상한에 걸리지 않음
    # 합계가 같은 4번(에이전트)과 6번(보안)은 주제 순서로 4번이 앞
    assert listed == [1, 2, 3]
    assert decisions == {0: "중복", 1: "목록", 2: "목록", 3: "목록", 4: "기준 미달", 5: "순위 밖", 6: "제외"}


def test_decide_applies_source_cap():
    news_list = [news(n, source="S") for n in range(1, 5)]
    scored = {index: {"total": 90 - index, "dup_of": None, "exclude": ""} for index in range(4)}
    listed, decisions = evaluate.decide(news_list, scored, list_slots=4, list_n=5, min_score=0)
    assert listed == [0, 1] and decisions[2] == decisions[3] == "매체 상한"


def test_cut_at_sentence():
    text = "가나다. " * 1000
    cut = evaluate.cut_at_sentence(text, 4000)
    assert len(cut) <= 4000 and cut.endswith(".")


def read_score_csv():
    import csv
    with open(evaluate.score_csv_path(), encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def test_evaluate_fills_list_and_summary_slots_across_reruns(monkeypatch):
    # LIST_N=5, TOP_N=3 (conftest)
    add_candidates(8)
    # 합계: 1번 100점부터 10점씩 내려감, 5번과 6번은 같은 점수
    response = score_response(*[score_item(n, impact=max(1, 6 - n)) for n in range(1, 7)])
    monkeypatch.setattr(evaluate, "call_gemini", lambda prompt, schema=None, max_retries=None: response)
    # 2위 기사는 본문을 가져오지 못한다
    monkeypatch.setattr(evaluate, "scrape_body", lambda url: "" if url == "http://a/2" else "본문 " * 200)
    evaluate.main()

    records = common.read_records("필터링")
    listed = [record for record in records if record.get("title")]
    assert [record["link"] for record in listed] == [f"http://a/{n}" for n in range(1, 6)]
    assert [record["link"] for record in listed if record.get("body")] == ["http://a/1", "http://a/3", "http://a/4"]
    # [SCORE]는 지난 기록과 같은 0~10
    assert [record["score"] for record in listed] == ["10", "9", "8", "7", "6"]
    # 후보 제목이 국문이라 번역 제목은 기록하지 않는다
    assert not any(record.get("title_ko") for record in listed)
    assert len([record for record in records if not record.get("title")]) == 3

    rows = read_score_csv()
    assert rows[0] == evaluate.SCORE_COLUMNS
    decisions = {row[-1]: row[1] for row in rows[1:]}
    assert decisions["http://a/1"] == "요약" and decisions["http://a/2"] == "목록"
    assert decisions["http://a/6"] == "순위 밖" and decisions["http://a/7"] == "점수 없음"
    assert len(rows) == 9

    # 같은 날 다시 실행해도 목록, 요약 건수가 늘지 않는다
    add_candidates(2, start=9)
    evaluate.main()
    assert len([record for record in common.read_records("필터링") if record.get("title")]) == 5


def test_evaluate_records_prefilter_drops(monkeypatch):
    common.append_record("수집", [("TITLE", "AI 웨비나 참가자 모집"), ("LINK", "http://a/x"), ("SOURCE", "매체"),
                                  ("TOPIC", "에이전트")], block=("SUMMARY", "-"))
    add_candidates(1)
    monkeypatch.setattr(evaluate, "call_gemini", lambda prompt, schema=None, max_retries=None: score_response(score_item(1)))
    monkeypatch.setattr(evaluate, "scrape_body", lambda url: "")
    evaluate.main()

    statuses = {record["link"]: record.get("status") for record in common.read_records("필터링")}
    assert statuses["http://a/x"] == "SKIP (사전 제외: 행사)"
    assert {row[-1]: row[1] for row in read_score_csv()[1:]}["http://a/x"] == "사전 제외: 행사"


def test_evaluate_failure_fails_the_job(monkeypatch):
    add_candidates(2)

    def boom(prompt, schema=None, max_retries=None):
        raise RuntimeError("429 quota")
    monkeypatch.setattr(evaluate, "call_gemini", boom)
    with pytest.raises(SystemExit):
        evaluate.main()
    assert common.read_errors("필터링")[0][0] == "전체 후보"


def test_evaluate_retries_temporary_rank_failure(monkeypatch):
    add_candidates(2)
    calls = []

    def flaky(prompt, schema=None, max_retries=None):
        calls.append(prompt)
        if len(calls) == 1:
            raise RuntimeError("503 UNAVAILABLE")
        return score_response(score_item(1))
    monkeypatch.setattr(evaluate, "call_gemini", flaky)
    monkeypatch.setattr(evaluate, "scrape_body", lambda url: "")
    evaluate.main()

    # 첫 시도의 실패 줄은 남고, 다시 시도한 선정 결과가 기록된다
    assert len(calls) == 2
    assert [target for target, _ in common.read_errors("필터링")].count("전체 후보") == 1
    assert any(record.get("title") for record in common.read_records("필터링"))


def test_evaluate_stops_at_call_limit(monkeypatch):
    add_candidates(2)
    calls = []

    def bad_format(prompt, schema=None, max_retries=None):
        calls.append(prompt)
        return "그냥 텍스트"
    monkeypatch.setattr(evaluate, "call_gemini", bad_format)
    with pytest.raises(SystemExit):
        evaluate.main()
    # 형식 오류도 다시 보내지만 상한을 넘지 않는다
    assert len(calls) == evaluate.RANK_MAX_CALLS


class BadRequest(Exception):
    code = 400


@pytest.mark.parametrize("error", [
    RuntimeError("429 GenerateRequestsPerDayPerProjectPerModel-FreeTier"),
    BadRequest("400"),
])
def test_evaluate_does_not_retry_permanent_rank_failure(monkeypatch, error):
    add_candidates(2)
    calls = []

    def boom(prompt, schema=None, max_retries=None):
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
