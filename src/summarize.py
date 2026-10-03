import re
import json
import time

from common import (
    GEMINI_MODEL, append_record, append_error, read_records, load_prompt, fill_prompt, call_gemini, fail, clean_inline,
    is_retryable,
)

TONES = ("긍정", "부정", "중립")
LANGS = ("ko", "en", "other")
# 형식이 맞지 않는 응답을 받았을 때 다시 요청하는 횟수
FORMAT_RETRIES = 1
# 일시 오류로 실패한 기사를 다시 시도하기 전, 첫 실패부터 최소한 기다리는 시간(초)
RETRY_PASS_DELAY = 300
SUMMARY_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "lang": {"type": "STRING", "enum": list(LANGS)},
        "title_ko": {"type": "STRING"},
        "summary_ko": {"type": "ARRAY", "items": {"type": "STRING"}},
        "summary_en": {"type": "ARRAY", "items": {"type": "STRING"}},
        "tone": {"type": "STRING", "enum": list(TONES)},
        "error": {"type": "STRING"},
    },
    "required": ["lang", "title_ko", "summary_ko", "tone"],
}


class NotSummarizable(Exception):
    """모델이 본문을 요약할 수 없다고 답한 경우. 다시 요청해도 같으므로 재요청하지 않는다."""


def parse_summary(result):
    """JSON 응답을 검증해 요약 로그에 기록할 텍스트로 바꾼다."""
    try:
        data = json.loads(result)
    except ValueError:
        match = re.search(r'\{.*\}', result, re.DOTALL)
        if not match:
            raise ValueError(f"요약 결과를 해석할 수 없습니다: {result[:200]}")
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("요약 결과가 JSON 객체가 아닙니다.")
    if str(data.get("error") or "").strip():
        raise NotSummarizable(clean_inline(data["error"], 100))

    def to_lines(value):
        if not isinstance(value, list):
            return []
        lines = [clean_inline(line, 300) for line in value]
        # 예시의 말줄임표를 그대로 돌려준 경우는 요약으로 보지 않는다
        return [line for line in lines if line.strip(".… ")]

    lang = str(data.get("lang", "")).strip().lower()
    title_ko = clean_inline(data.get("title_ko", ""), 150)
    summary_ko = to_lines(data.get("summary_ko"))
    summary_en = to_lines(data.get("summary_en"))
    tone = str(data.get("tone", "")).strip().lstrip("#")

    if lang not in LANGS:
        raise ValueError(f"알 수 없는 언어 값입니다: {lang}")
    if not title_ko:
        raise ValueError("한국어 제목이 없습니다.")
    if len(summary_ko) != 3:
        raise ValueError(f"국문 요약이 3줄이 아닙니다. ({len(summary_ko)}줄)")
    if lang == "en" and len(summary_en) != 3:
        raise ValueError(f"영문 요약이 3줄이 아닙니다. ({len(summary_en)}줄)")
    if tone not in TONES:
        raise ValueError(f"알 수 없는 논조 값입니다: {tone}")

    lines = []
    if lang != "ko":
        # 국문이 아닌 기사는 번역 제목을 함께 둔다
        lines += ["**[기사 제목 번역]**", title_ko, ""]
    if lang == "en":
        # 영문 기사만 영문 요약을 함께 둔다
        lines += ["**[영문 3줄 요약 (Original)]**"]
        lines += [f"- {line}" for line in summary_en]
        lines += ["", "**[국문 3줄 요약 (Translated)]**"]
    else:
        lines += ["**[국문 3줄 요약]**"]
    lines += [f"- {line}" for line in summary_ko]
    lines += ["", f"#{tone}"]
    return "\n".join(lines)


def generate_summary(title, reason, body_text):
    prompt = fill_prompt(load_prompt("summary_prompt.txt"), title=title, reason=reason or "-", body=body_text)

    last_error = None
    for attempt in range(FORMAT_RETRIES + 1):
        result = call_gemini(prompt, schema=SUMMARY_SCHEMA)
        try:
            return parse_summary(result)
        except ValueError as e:
            last_error = e
            print(f" -> 요약 형식 오류: {e}")
    raise last_error


def is_temporary(error):
    """나중에 다시 시도할 만한 실패인지. 요약 거절과 형식 오류는 다시 요청해도 결과가 같다고 본다."""
    return not isinstance(error, (NotSummarizable, ValueError)) and is_retryable(error)


def summarize_one(news):
    """기사 하나를 요약해 기록한다. 실패하면 오류 줄을 남기고 그 예외를, 성공하면 None을 돌려준다."""
    print(f"\n요약 중: {news['title']}")
    try:
        summary = generate_summary(news['title'], news.get('reason'), news['body'])
    except NotSummarizable as e:
        # 본문이 기사가 아니라 모델이 요약을 거절한 경우 (로그인 요구, 차단 안내 등)
        print(f" -> [요약 불가] {e}")
        append_error("요약", "요약 오류", news['link'], f"요약 불가: {e}")
        return e
    except Exception as e:
        print(f" -> [요약 오류] {e}")
        append_error("요약", "요약 오류", news['link'], f"요약 실패: {e}")
        return e
    append_record("요약", [
        ("TITLE", news['title']),
        ("LINK", news['link']),
        ("PUBLISHED", news.get('published')),
        ("SOURCE", news.get('source')),
        ("TOPIC", news.get('topic')),
        ("SCORE", news.get('score')),
        ("REASON", news.get('reason')),
        ("MODEL", GEMINI_MODEL),
    ], block=("SUMMARY", summary))
    return None


def main():
    print("--- 3. 요약 파이프라인 시작 ---")
    # 필터링 로그는 중요도 순으로 기록되어 있으므로 그 순서대로 요약한다
    news_list = [news for news in read_records("필터링") if news.get('title') and news.get('body')]
    summarized_links = {record['link'] for record in read_records("요약")}

    targets = [news for news in news_list if news['link'] not in summarized_links]
    failed = []
    for news in targets:
        error = summarize_one(news)
        if error:
            failed.append((news, error, time.time()))

    # 서버 과부하(503) 같은 일시 오류는 몇 분 뒤 풀리는 경우가 많아, 나머지 기사를 다 처리한 뒤 한 번 더 시도한다
    retry = [(news, failed_at) for news, error, failed_at in failed if is_temporary(error)]
    if retry:
        time.sleep(max(0.0, retry[0][1] + RETRY_PASS_DELAY - time.time()))
        print(f"\n일시 오류로 실패한 {len(retry)}건 다시 요약")
        recovered = {news['link'] for news, _ in retry if summarize_one(news) is None}
        failed = [item for item in failed if item[0]['link'] not in recovered]

    print(f"\n -> 요약 성공: {len(targets) - len(failed)}건, 실패: {len(failed)}건")
    print("--- 3. 요약 파이프라인 종료 ---")
    if targets and len(failed) == len(targets):
        fail(f"요약 대상 {len(targets)}건이 모두 실패했습니다.")


if __name__ == "__main__":
    main()
