import types

import pytest

import common


def test_record_round_trip_keeps_block_intact():
    body = "첫 줄\n---\n[LINK] http://fake/in-body\n[TITLE] 가짜 제목\n끝"
    common.append_record("필터링", [("TITLE", "제목"), ("LINK", "http://a/1"), ("SCORE", 8)], block=("BODY", body))
    common.append_record("필터링", [("LINK", "http://a/2"), ("STATUS", "SKIP (Score: -)")])

    records = common.read_records("필터링")
    assert [record["link"] for record in records] == ["http://a/1", "http://a/2"]
    assert records[0]["title"] == "제목"
    assert records[0]["score"] == "8"
    # 본문 속의 '---'와 태그처럼 보이는 줄은 레코드를 나누지 않는다
    assert "[LINK] http://fake/in-body" in records[0]["body"]
    assert "- - -" in records[0]["body"]


def test_error_lines_are_single_line_and_ignored_inside_blocks():
    common.append_record("요약", [("TITLE", "t"), ("LINK", "http://a/1")], block=("SUMMARY", "[요약 오류] http://x - 본문 속 글"))
    common.append_error("요약", "요약 오류", "http://a/2", "여러 줄\n[LINK] http://fake\n오류")

    assert common.read_errors("요약") == [("http://a/2", "여러 줄 [LINK] http://fake 오류")]
    # 오류 메시지 안의 [LINK]가 가짜 레코드를 만들지 않는다
    assert [record["link"] for record in common.read_records("요약")] == ["http://a/1"]


def test_run_date_decides_log_path(monkeypatch):
    monkeypatch.setenv("RUN_DATE", "2026-12-31")
    assert common.get_log_path("수집").endswith("logs/2026/12/2026-12-31-수집.txt")


def test_fill_prompt_does_not_substitute_twice():
    filled = common.fill_prompt("A {title} B {body} {other} {\"k\": 1}", title="T {body}", body="X")
    assert filled == "A T {body} B X {other} {\"k\": 1}"


def test_clean_inline_removes_links_and_tags():
    assert common.clean_inline("에이전트 [공개](http://x.y) <b>tool_use</b> C#\n둘째 줄") == "에이전트 공개 btool_use/b C# 둘째 줄"
    assert common.clean_inline("가" * 10, 5) == "가" * 5 + "…"


def test_retry_wait_grows_and_follows_server_hint():
    first = common.get_retry_wait(1, Exception("x"))
    second = common.get_retry_wait(2, Exception("x"))
    assert common.GEMINI_RETRY_BASE <= first <= common.GEMINI_RETRY_BASE * 1.1
    assert second >= first
    assert common.get_retry_wait(1, Exception("Please retry in 500s.")) >= 501


def fake_client(monkeypatch, behavior):
    calls = []

    def generate_content(**kwargs):
        calls.append(kwargs)
        return behavior(len(calls))

    monkeypatch.setattr(common, "_client", types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate_content)))
    return calls


def test_call_gemini_retries_temporary_errors(monkeypatch):
    def behavior(count):
        if count < 3:
            raise RuntimeError("503 UNAVAILABLE")
        return types.SimpleNamespace(text=" ok ", candidates=[])
    calls = fake_client(monkeypatch, behavior)
    assert common.call_gemini("p", schema={"type": "OBJECT"}) == "ok"
    assert len(calls) == 3
    assert calls[0]["config"]["temperature"] == 0
    assert calls[0]["config"]["response_mime_type"] == "application/json"


def test_call_gemini_without_retries_calls_once(monkeypatch):
    def behavior(count):
        raise RuntimeError("503 UNAVAILABLE")
    calls = fake_client(monkeypatch, behavior)
    with pytest.raises(RuntimeError):
        common.call_gemini("p", max_retries=0)
    assert len(calls) == 1


def test_call_gemini_stops_on_daily_quota(monkeypatch):
    def behavior(count):
        raise RuntimeError("429 RESOURCE_EXHAUSTED quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier")
    calls = fake_client(monkeypatch, behavior)
    with pytest.raises(RuntimeError):
        common.call_gemini("p")
    assert len(calls) == 1


def test_call_gemini_stops_on_bad_request(monkeypatch):
    class BadRequest(Exception):
        code = 400
    def behavior(count):
        raise BadRequest("400")
    calls = fake_client(monkeypatch, behavior)
    with pytest.raises(BadRequest):
        common.call_gemini("p")
    assert len(calls) == 1


def test_call_gemini_rejects_truncated_response(monkeypatch):
    monkeypatch.setattr(common, "GEMINI_MAX_RETRIES", 0)
    fake_client(monkeypatch, lambda count: types.SimpleNamespace(
        text='[{"id": 1', candidates=[types.SimpleNamespace(finish_reason="FinishReason.MAX_TOKENS")]))
    with pytest.raises(ValueError, match="잘렸"):
        common.call_gemini("p")
