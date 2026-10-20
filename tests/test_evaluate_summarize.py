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


def score_item(number, impact=10, relevance=5, novelty=3, certainty=3, **overrides):
    item = {"id": number, "impact": impact, "relevance": relevance, "novelty": novelty, "certainty": certainty,
            "dup_of": 0, "exclude": "", "reason": f"이유 {number}", "title_ko": f"번역 {number}"}
    item.update(overrides)
    return item


def score_response(*items):
    return json.dumps(list(items), ensure_ascii=False)


def news(number, **fields):
    return {"title": f"t{number}", "link": f"l{number}", "source": f"S{number}", "published": "2026-10-01 08:00 KST",
            "topic": "에이전트", **fields}


def test_total_score_scale():
    highest = dict(evaluate.CRITERIA_MAX)
    no_penalty = {"impact": 1, "relevance": 1, "novelty": 3, "certainty": 3}
    assert evaluate.total_score(highest) == 100
    assert evaluate.total_score(no_penalty) == 0
    # 파급력만 최고점이면 파급력 가중치만큼. 새로움, 근거는 합계에 더하지 않는다
    assert evaluate.total_score({**no_penalty, "impact": 10}) == evaluate.CRITERIA_WEIGHTS["impact"] * 100
    # 새로움, 근거가 1점이면 감점하고, 합계는 0 아래로 내려가지 않는다
    penalty = evaluate.PENALTIES["novelty"] + evaluate.PENALTIES["certainty"]
    assert evaluate.total_score({**highest, "novelty": 1, "certainty": 1}) == 100 - penalty
    assert evaluate.total_score({"impact": 1, "relevance": 1, "novelty": 1, "certainty": 1}) == 0


def test_exclude_reason_accepts_only_defined_reasons():
    assert evaluate.exclude_reason("홍보") == "홍보"
    assert evaluate.exclude_reason("홍보성 보도자료") == "홍보"
    assert evaluate.exclude_reason("규제") == ""
    assert evaluate.exclude_reason(None) == ""


def test_score_articles_validates_response(monkeypatch):
    news_list = [news(n) for n in range(1, 6)]
    wrapped = json.dumps({"items": [
        score_item(1, reason="[a](http://b) 이유", dup_of=1), score_item(2, impact=99, relevance=0, dup_of=1),
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
    # 점수는 항목별 범위(파급력 1~10, 관련성 1~5)로 맞춘다
    assert scored[1]["impact"] == 10 and scored[1]["relevance"] == 1


def test_score_articles_reads_previous_report_marks(monkeypatch):
    previous = [{"title": "Old patch", "title_ko": "지난 패치", "link": "p1"}, {"title": "지난 사고", "link": "p2"}]
    seen = {}

    def fake(prompt, schema=None, max_retries=None):
        seen["prompt"] = prompt
        return score_response(score_item(1, prev_of=2), score_item(2, prev_of=3), score_item(3, prev_of="x"))
    monkeypatch.setattr(evaluate, "call_gemini", fake)
    scored = evaluate.score_articles([news(1), news(2), news(3)], previous)

    assert "[P1] Old patch / 지난 패치\n[P2] 지난 사고" in seen["prompt"]
    # 범위 밖이나 숫자가 아닌 prev_of는 무시한다
    assert [scored[index]["prev_of"] for index in range(3)] == [1, None, None]


def test_score_articles_without_previous_report(monkeypatch):
    seen = {}

    def fake(prompt, schema=None, max_retries=None):
        seen["prompt"] = prompt
        return score_response(score_item(1, prev_of=1))
    monkeypatch.setattr(evaluate, "call_gemini", fake)
    scored = evaluate.score_articles([news(1)])
    assert "<지난리포트>\n(없음)\n</지난리포트>" in seen["prompt"] and "{previous}" not in seen["prompt"]
    assert scored[0]["prev_of"] is None


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


def scored_entry(total, impact=5, dup_of=None, exclude=""):
    return {"total": total, "impact": impact, "dup_of": dup_of, "exclude": exclude}


def test_decide_handles_duplicates_exclusions_and_caps():
    news_list = [news(1), news(2), news(3, source="S1"), news(4), news(5), news(6, topic="보안"), news(7)]
    totals = {0: 90, 1: 95, 2: 80, 3: 70, 4: 30, 5: 70, 6: 60}
    scored = {index: scored_entry(total) for index, total in totals.items()}
    scored[0]["dup_of"] = 1          # 1번은 2번과 같은 사건 (2번 합계가 더 높음)
    scored[6]["exclude"] = "홍보"
    scored[5]["impact"] = 8          # 합계가 같은 4번과 6번 중 6번의 파급력이 더 높음
    listed, decisions = evaluate.decide(news_list, scored, list_slots=3, list_n=5, min_score=4)

    # 매체 상한은 max(2, LIST_N // 5) = 2. 3번은 1번과 매체가 같지만 1번이 중복으로 빠져 상한에 걸리지 않음
    # 합계가 같으면 주제 순서가 아니라 파급력으로 가른다 (6번 보안 기사가 앞)
    assert listed == [1, 2, 5]
    assert decisions == {0: "중복", 1: "목록", 2: "목록", 3: "순위 밖", 4: "기준 미달", 5: "목록", 6: "제외"}
    # 같은 사건을 다룬 기사 수를 남긴다
    assert scored[1]["coverage"] == 2 and scored[2]["coverage"] == 1


def test_decide_drops_events_already_reported():
    news_list = [news(n) for n in range(1, 5)]
    scored = {index: scored_entry(90 - index) for index in range(4)}
    scored[1]["dup_of"] = 0          # 1번(기준), 2번은 같은 사건. 지난 보도 표시는 합계가 낮은 2번에만 있음
    scored[1]["prev_of"] = 0
    scored[2]["prev_of"] = 3
    listed, decisions = evaluate.decide(news_list, scored, list_slots=4, list_n=5, min_score=0)
    # 묶음의 한 기사라도 지난 목록과 같은 사건이면 묶음 전체가 빠진다
    assert listed == [3]
    assert decisions == {0: "지난 보도", 1: "중복", 2: "지난 보도", 3: "목록"}


def test_decide_breaks_ties_by_coverage():
    news_list = [news(n) for n in range(1, 5)]
    scored = {0: scored_entry(80), 1: scored_entry(80), 2: scored_entry(50, dup_of=1), 3: scored_entry(40, dup_of=1)}
    listed, _ = evaluate.decide(news_list, scored, list_slots=1, list_n=5, min_score=0)
    # 합계, 파급력이 같으면 여러 기사가 다룬 사건(2번)이 먼저
    assert listed == [1]


def test_decide_applies_source_cap():
    news_list = [news(n, source="S") for n in range(1, 5)]
    scored = {index: scored_entry(90 - index) for index in range(4)}
    listed, decisions = evaluate.decide(news_list, scored, list_slots=4, list_n=5, min_score=0)
    assert listed == [0, 1] and decisions[2] == decisions[3] == "매체 상한"
    # 상한은 목록 건수의 1/4 (12건이면 3건)
    listed, decisions = evaluate.decide(news_list, scored, list_slots=4, list_n=12, min_score=0)
    assert listed == [0, 1, 2] and decisions[3] == "매체 상한"


def test_decide_caps_one_field_in_summary_slots(monkeypatch):
    monkeypatch.setattr(evaluate, "FIELD_CAP", {"max": 2, "exempt_impact": 9, "min_total": 70})
    news_list = [news(n) for n in range(1, 9)]
    totals = [95, 88, 86, 84, 82, 80, 75, 60]
    fields = ["보안", "보안", "보안", "보안", "AI 모델", "신기술", "AI 모델", "AI 에이전트"]
    scored = {index: {**scored_entry(total), "field": field} for index, (total, field) in enumerate(zip(totals, fields))}
    # 면제는 합계가 아니라 파급력으로 정한다. 4번째 보안(84점)은 파급력 9라 면제, 95점이어도 파급력 5면 면제 아님
    # (AI와 무관한 보안 사태는 관련성이 4까지라 파급력 9여도 합계 83.3)
    scored[3]["impact"] = 9
    listed, decisions = evaluate.decide(news_list, scored, list_slots=8, list_n=40, min_score=0, top_slots=5)
    # 보안이 2건 차면 3번째 보안(86점)은 뒤로, 면제 기사는 넘어도 들어감. 70점 미만(60점)은 끌어올리지 않고
    # 남은 자리는 상한 없이 합계 순으로 채운다
    assert listed[:5] == [0, 1, 3, 4, 5]
    assert listed[5:] == [2, 6, 7]
    # 목록에 오르는 기사는 그대로
    assert set(decisions.values()) == {"목록"}
    # 요약 자리가 없거나 상한 설정이 없으면 합계 순 그대로
    assert evaluate.decide(news_list, scored, list_slots=8, list_n=40, min_score=0)[0] == list(range(8))
    monkeypatch.setattr(evaluate, "FIELD_CAP", {})
    assert evaluate.decide(news_list, scored, list_slots=8, list_n=40, min_score=0, top_slots=5)[0] == list(range(8))


def test_decide_fills_summary_slots_without_cap_below_min_total(monkeypatch):
    monkeypatch.setattr(evaluate, "FIELD_CAP", {"max": 1, "exempt_impact": 9, "min_total": 70})
    news_list = [news(n) for n in range(1, 5)]
    scored = {index: {**scored_entry(total), "field": "보안"} for index, total in enumerate([85, 80, 78, 50])}
    scored[3]["field"] = "AI 모델"
    listed, _ = evaluate.decide(news_list, scored, list_slots=4, list_n=40, min_score=0, top_slots=3)
    # 70점 이상인 다른 분야 기사가 없으면 상한에 걸린 기사로 합계 순으로 채운다 (50점 기사가 앞서지 않음)
    assert listed == [0, 1, 2, 3]


def test_score_articles_reads_field(monkeypatch):
    response = score_response(score_item(1, field="보안"), score_item(2, field="정책"), score_item(3))
    seen = {}

    def fake(prompt, schema=None, max_retries=None):
        seen["schema"] = schema
        return response
    monkeypatch.setattr(evaluate, "call_gemini", fake)
    scored = evaluate.score_articles([news(1), news(2), news(3)])
    # 정해진 분야가 아니거나 빠지면 기타
    assert [scored[index]["field"] for index in range(3)] == ["보안", "기타", "기타"]
    assert seen["schema"]["items"]["properties"]["field"]["enum"] == list(evaluate.FIELDS)


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
    # 합계(관련성 5 고정, 파급력만 다름): 1번 100, 2번 86.7, 3번 80, 4번 66.7, 5번과 6번 60 (같은 점수)
    impacts = {1: 10, 2: 8, 3: 7, 4: 5, 5: 4, 6: 4}
    response = score_response(*[score_item(n, impact=impacts[n]) for n in range(1, 7)])
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
    decisions = {row[-2]: row[1] for row in rows[1:]}
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
    assert {row[-2]: row[1] for row in read_score_csv()[1:]}["http://a/x"] == "사전 제외: 행사"


def test_evaluate_failure_fails_the_job(monkeypatch):
    add_candidates(2)

    def boom(prompt, schema=None, max_retries=None):
        raise RuntimeError("429 quota")
    monkeypatch.setattr(evaluate, "call_gemini", boom)
    with pytest.raises(SystemExit):
        evaluate.main()
    assert common.read_errors("필터링")[0][0] == "전체 후보"


def test_evaluate_sends_previous_report_and_drops_same_event(monkeypatch):
    # 전날 목록에 오른 기사 1건, 같은 날짜가 아닌 4일 전 기사 1건(PREV_REPORT_DAYS 밖)
    for date, title in (("2026-09-30", "어제 실린 패치"), ("2026-09-27", "오래된 기사")):
        monkeypatch.setenv("RUN_DATE", date)
        common.append_record("필터링", [("TITLE", title), ("LINK", f"http://old/{date}")])
    monkeypatch.setenv("RUN_DATE", "2026-10-01")
    add_candidates(3)
    seen = {}

    def fake(prompt, schema=None, max_retries=None):
        seen["prompt"] = prompt
        return score_response(score_item(1, prev_of=1), score_item(2), score_item(3))
    monkeypatch.setattr(evaluate, "call_gemini", fake)
    monkeypatch.setattr(evaluate, "scrape_body", lambda url: "")
    evaluate.main()

    assert "[P1] 어제 실린 패치" in seen["prompt"] and "오래된 기사" not in seen["prompt"]
    listed = [record["link"] for record in common.read_records("필터링") if record.get("title")]
    assert listed == ["http://a/2", "http://a/3"]
    rows = read_score_csv()
    assert rows[0][-1] == "지난 보도"
    row = {row[-2]: row for row in rows[1:]}["http://a/1"]
    assert row[1] == "지난 보도" and row[-1] == "http://old/2026-09-30"


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
    common.TruncatedResponse("Gemini 응답이 출력 한도에서 잘렸습니다."),
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


def test_summary_schema_writes_english_before_korean():
    # 실제 SDK 형식으로 읽혀야 하고, 국문이 영문의 번역이 되도록 summary_en이 summary_ko보다 앞에 와야 한다
    from google.genai import types
    schema = types.Schema.model_validate(summarize.SUMMARY_SCHEMA)
    order = schema.items.property_ordering
    assert order.index("summary_en") < order.index("summary_ko")
    assert set(order) == set(summarize.SUMMARY_SCHEMA["items"]["properties"])


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
    waits = []
    monkeypatch.setattr(summarize.time, "sleep", waits.append)

    def fake(prompt, schema=None, max_retries=None):
        calls.append(prompt)
        raise RuntimeError("503 UNAVAILABLE")
    monkeypatch.setattr(summarize, "call_gemini", fake)
    with pytest.raises(SystemExit):
        summarize.main()

    # 재시도를 모두 합쳐도 상한을 넘지 않는다
    assert len(calls) == summarize.SUMMARY_MAX_CALLS
    # 일시 오류는 SUMMARY_RETRY_WAITS 순서로 기다리고, 마지막 호출 뒤에는 기다리지 않는다
    assert waits == list(summarize.SUMMARY_RETRY_WAITS[:summarize.SUMMARY_MAX_CALLS - 1])
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
