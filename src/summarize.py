import re
import json
import time

from common import (
    GEMINI_MODEL, append_record, append_error, read_records, load_prompt, fill_prompt, call_gemini, fail, clean_inline,
    is_retryable, get_retry_wait,
)

TONES = ("긍정", "부정", "중립")
LANGS = ("ko", "en", "other")
# 요약 단계 전체의 Gemini 호출 상한. 첫 요청과 모든 재요청(일시 오류, 형식 오류, 응답에서 빠진 기사)을 합친 횟수
SUMMARY_MAX_CALLS = 5
SUMMARY_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "INTEGER"},
            "lang": {"type": "STRING", "enum": list(LANGS)},
            "title_ko": {"type": "STRING"},
            "summary_ko": {"type": "ARRAY", "items": {"type": "STRING"}},
            "summary_en": {"type": "ARRAY", "items": {"type": "STRING"}},
            "tone": {"type": "STRING", "enum": list(TONES)},
            "error": {"type": "STRING"},
        },
        "required": ["id", "lang", "title_ko", "summary_ko", "tone"],
    },
}


class NotSummarizable(Exception):
    """모델이 본문을 요약할 수 없다고 답한 경우. 다시 요청해도 같으므로 재요청하지 않는다."""


def parse_summary(result):
    """기사 하나의 응답(JSON 글 또는 객체)을 검증해 요약 로그에 기록할 텍스트로 바꾼다."""
    if isinstance(result, dict):
        data = result
    else:
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


def build_articles(news_list):
    """요약할 기사들을 번호를 붙인 <기사> 블록으로 이어 붙인다. 번호는 응답의 id와 맞춰 보는 데 쓴다."""
    blocks = []
    for number, news in enumerate(news_list, start=1):
        # 본문에 닫는 태그가 있으면 기사 경계가 흐려지므로 바꿔 쓴다
        body = str(news.get("body") or "").replace("</기사", "</ 기사")
        blocks.append("\n".join([
            f'<기사 id="{number}">',
            f"제목: {news['title']}",
            f"이 기사를 고른 이유: {news.get('reason') or '-'}",
            "",
            "본문:",
            body,
            "</기사>",
        ]))
    return "\n\n".join(blocks)


def split_answers(result):
    """묶음 응답(JSON 배열)을 {id: 기사별 응답} 으로 나눈다. 같은 id가 여러 번 오면 처음 것만 쓴다."""
    try:
        items = json.loads(result)
    except ValueError:
        match = re.search(r'\[.*\]', result, re.DOTALL)
        if not match:
            raise ValueError(f"요약 결과를 해석할 수 없습니다: {result[:200]}")
        items = json.loads(match.group(0))
    if isinstance(items, dict):
        # 배열을 객체로 한 번 감싸서 준 경우
        items = next((value for value in items.values() if isinstance(value, list)), None)
    if not isinstance(items, list):
        raise ValueError("요약 결과가 JSON 배열이 아닙니다.")
    answers = {}
    for item in items:
        try:
            number = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        answers.setdefault(number, item)
    return answers


def record_summary(news, summary):
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


def summarize_all(targets):
    """대상 기사를 한 번의 호출로 요약하고, 실패한 기사만 모아 다시 요청한다.

    호출은 단계 전체에서 SUMMARY_MAX_CALLS번까지 (call_gemini 안의 재시도 없음). 성공 건수를 돌려준다.
    """
    pending = list(targets)
    reasons = {}
    succeeded = 0
    calls = 0
    temporary_failures = 0
    while pending and calls < SUMMARY_MAX_CALLS:
        calls += 1
        print(f"\n요약 요청 {calls}/{SUMMARY_MAX_CALLS}: {len(pending)}건")
        prompt = fill_prompt(load_prompt("summary_prompt.txt"), articles=build_articles(pending))
        try:
            answers = split_answers(call_gemini(prompt, schema=SUMMARY_SCHEMA, max_retries=0))
        except Exception as e:
            print(f" -> [요약 호출 오류] {str(e)[:200]}")
            for news in pending:
                reasons[news['link']] = f"요약 실패: {e}"
            if not is_retryable(e):
                # 하루 요청 한도 초과, 잘못된 요청은 다시 보내도 같다
                break
            if not isinstance(e, ValueError) and calls < SUMMARY_MAX_CALLS:
                # 서버 과부하 같은 일시 오류는 점점 길게 기다린 뒤 다시 보낸다
                temporary_failures += 1
                time.sleep(get_retry_wait(temporary_failures, e))
            continue

        still = []
        for number, news in enumerate(pending, start=1):
            if number not in answers:
                print(f" -> [응답에 없음] {news['title']}")
                reasons[news['link']] = "요약 실패: 응답에 이 기사가 없습니다."
                still.append(news)
                continue
            try:
                summary = parse_summary(answers[number])
            except NotSummarizable as e:
                # 본문이 기사가 아니라 모델이 요약을 거절한 경우 (로그인 요구, 차단 안내 등)
                print(f" -> [요약 불가] {news['title']}: {e}")
                append_error("요약", "요약 오류", news['link'], f"요약 불가: {e}")
                continue
            except ValueError as e:
                print(f" -> [요약 형식 오류] {news['title']}: {e}")
                reasons[news['link']] = f"요약 실패: {e}"
                still.append(news)
                continue
            record_summary(news, summary)
            succeeded += 1
            print(f" -> [요약 완료] {news['title']}")
        pending = still

    # 상한까지 요청해도 요약하지 못한 기사는 마지막 사유를 남긴다
    for news in pending:
        append_error("요약", "요약 오류", news['link'], reasons.get(news['link'], "요약 실패: 시도하지 못했습니다."))
    return succeeded


def main():
    print("--- 3. 요약 파이프라인 시작 ---")
    # 필터링 로그는 중요도 순으로 기록되어 있으므로 그 순서대로 요약한다
    news_list = [news for news in read_records("필터링") if news.get('title') and news.get('body')]
    summarized_links = {record['link'] for record in read_records("요약")}

    targets = [news for news in news_list if news['link'] not in summarized_links]
    succeeded = summarize_all(targets) if targets else 0

    print(f"\n -> 요약 성공: {succeeded}건, 실패: {len(targets) - succeeded}건")
    print("--- 3. 요약 파이프라인 종료 ---")
    if targets and succeeded == 0:
        fail(f"요약 대상 {len(targets)}건이 모두 실패했습니다.")


if __name__ == "__main__":
    main()
