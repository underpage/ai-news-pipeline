import os
import glob
import html
import calendar
import email.utils
import datetime
import re

from common import LOGS_DIR, TEST_MODE, get_log_path, append_record, append_error, read_records, read_records_from, fetch, fail

KST = datetime.timezone(datetime.timedelta(hours=9))
WINDOW_HOURS = 24
# 실행이 어제보다 늦게 시작해도 그 사이 기사를 놓치지 않도록 수집 기간에 더하는 여유(시간)
WINDOW_GRACE_HOURS = 2
SUMMARY_LIMIT = 500
# 주제 키워드에 걸리지 않은 AI 기사에 붙이는 주제
DEFAULT_TOPIC = "일반"

CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config")


def load_config(name):
    import yaml
    with open(os.path.join(CONFIG_DIR, name), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# 뉴스 소스와 키워드는 코드가 아니라 config/ 의 설정 파일에서 읽는다
# FEEDS: [{name, url, naive_kst?}] / naive_kst는 시간대 표기 없이 한국 시간으로 적는 피드
FEEDS = load_config("feeds.yml")["feeds"]
# 수집 조건: AI 용어가 하나 이상 있어야 한다. 주제 키워드는 분류에 쓰고, 적힌 순서가 우선순위다
_keywords = load_config("keywords.yml")
AI_TERMS = _keywords["ai_terms"]
TOPIC_KEYWORDS = _keywords["topics"]


def check_config():
    """설정 파일을 잘못 고쳤을 때 조용히 틀린 결과를 내지 않도록 시작할 때 확인한다."""
    def is_text_list(value):
        return isinstance(value, list) and value and all(isinstance(item, str) and item.strip() for item in value)

    if not isinstance(FEEDS, list) or not FEEDS:
        raise ValueError("config/feeds.yml: feeds 목록이 비어 있습니다.")
    for feed in FEEDS:
        if not isinstance(feed, dict) or not isinstance(feed.get("name"), str) or not isinstance(feed.get("url"), str):
            raise ValueError(f"config/feeds.yml: name과 url이 필요합니다: {feed}")
        if not isinstance(feed.get("naive_kst", False), bool):
            raise ValueError(f"config/feeds.yml: naive_kst는 true 또는 false여야 합니다 (따옴표 없이): {feed['name']}")
    if not is_text_list(AI_TERMS):
        raise ValueError("config/keywords.yml: ai_terms는 비어 있지 않은 문자열 목록이어야 합니다.")
    if not isinstance(TOPIC_KEYWORDS, dict) or not TOPIC_KEYWORDS:
        raise ValueError("config/keywords.yml: topics가 비어 있습니다.")
    for topic, terms in TOPIC_KEYWORDS.items():
        if not is_text_list(terms):
            raise ValueError(f"config/keywords.yml: 주제 '{topic}'의 키워드는 비어 있지 않은 문자열 목록이어야 합니다.")


check_config()


def build_pattern(terms):
    parts = []
    for term in terms:
        escaped = re.escape(term)
        if term.isascii():
            # 영문은 단어 단위로 비교한다 ("said"가 "ai"에 걸리지 않도록)
            parts.append(rf"(?<![A-Za-z]){escaped}(?:s|es)?(?![A-Za-z])")
        else:
            parts.append(escaped)
    return re.compile("|".join(parts), re.IGNORECASE)


AI_PATTERN = build_pattern(AI_TERMS)
TOPIC_PATTERNS = {topic: build_pattern(terms) for topic, terms in TOPIC_KEYWORDS.items()}


def is_test_mode():
    # COLLECT_TEST_MODE는 수집 건수만 줄이고(워크플로우의 수동 실행용), TEST_MODE는 결과 폴더까지 분리한다
    return TEST_MODE or os.environ.get("COLLECT_TEST_MODE", "false").lower() in {"1", "true", "yes", "y"}


def get_test_limit():
    if not is_test_mode():
        return 0
    raw_limit = os.environ.get("COLLECT_LIMIT", "2")
    try:
        return max(1, int(raw_limit))
    except ValueError:
        return 2


def init_log(log_type):
    filename = get_log_path(log_type)
    with open(filename, 'a', encoding='utf-8') as f:
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{log_type} 파이프라인 실행] {now_str}\n---\n")


def load_history_from_logs():
    history = set()
    if not os.path.exists(LOGS_DIR):
        return history
    for filepath in glob.glob(os.path.join(LOGS_DIR, "**", "*.txt"), recursive=True):
        # 본문 안에 적힌 링크가 이력에 섞이지 않도록 레코드 단위로 읽는다
        for record in read_records_from(filepath):
            history.add(record["link"])
    return history


# 날짜 문자열 끝의 시간대 표기 (+0900, +09:00, Z, GMT, KST 등)
TIMEZONE_PATTERN = re.compile(r"([+-]\d{2}:?\d{2}|Z|[A-Z]{2,4})\s*$")


def parse_kst_label(raw):
    """"... KST"로 끝나는 날짜. 피드 파서가 KST를 알아보지 못하므로 직접 읽는다."""
    text = raw[:-3].strip()
    for date_format in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
        try:
            return datetime.datetime.strptime(text, date_format).replace(tzinfo=KST)
        except ValueError:
            pass
    try:
        return email.utils.parsedate_to_datetime(text).replace(tzinfo=KST)
    except (TypeError, ValueError):
        return None


def get_published_time(entry, naive_kst):
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    raw = str(getattr(entry, "published", "") or getattr(entry, "updated", "")).strip()
    if raw.upper().endswith("KST"):
        published = parse_kst_label(raw)
        return published.astimezone(datetime.timezone.utc) if published else None
    if parsed:
        published = datetime.datetime.fromtimestamp(calendar.timegm(parsed), datetime.timezone.utc)
    else:
        # feedparser가 읽지 못하는 형식 (예: 시간대 없는 "Wed, 30 Sep 2026 18:22:00")
        try:
            published = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if published.tzinfo is None:
            published = published.replace(tzinfo=datetime.timezone.utc)
        published = published.astimezone(datetime.timezone.utc)
    # 시간대 표기가 없는 한국 시간은 UTC로 읽히므로 9시간을 뺀다. 표기가 있으면 보정하지 않는다
    if naive_kst and not TIMEZONE_PATTERN.search(raw):
        published -= datetime.timedelta(hours=9)
    return published


def clean_text(text):
    text = re.sub(r'<[^>]+>', ' ', text or "")
    # 엔티티를 풀면서 되살아난 태그 기호도 지운다
    text = html.unescape(text).replace("<", " ").replace(">", " ")
    return " ".join(text.split())


def get_collect_max():
    try:
        return max(1, int(os.environ.get("COLLECT_MAX", "300")))
    except ValueError:
        return 300


def match_topics(title, summary_text):
    """AI 기사가 아니면 빈 목록, AI 기사면 일치한 주제 목록(없으면 기본 주제)을 돌려준다."""
    text = f"{title} {summary_text}"
    if not AI_PATTERN.search(text):
        return []
    topics = [topic for topic, pattern in TOPIC_PATTERNS.items() if pattern.search(text)]
    return topics or [DEFAULT_TOPIC]


def topic_priority(topics):
    order = list(TOPIC_KEYWORDS)
    return order.index(topics[0]) if topics[0] in order else len(order)


def main():
    print("--- 1. 수집 파이프라인 시작 ---")
    import feedparser

    init_log("수집")
    history_links = load_history_from_logs()
    candidates = []
    failed_feeds = 0

    now = datetime.datetime.now(datetime.timezone.utc)
    window_start = now - datetime.timedelta(hours=WINDOW_HOURS + WINDOW_GRACE_HOURS)
    print(f"수집 기간: {window_start.astimezone(KST):%Y-%m-%d %H:%M} ~ {now.astimezone(KST):%Y-%m-%d %H:%M} KST")

    for feed_info in FEEDS:
        url = feed_info["url"]
        print(f"\n[URL 파싱 중] {feed_info['name']} - {url}")
        try:
            response = fetch(url)
            feed = feedparser.parse(response.content)
            if not feed.entries and feed.bozo:
                # 주소는 응답했지만 피드가 아닌 내용(차단 안내 페이지 등)
                raise ValueError(f"피드 형식이 아닙니다: {feed.bozo_exception}")

            print(f" -> 파싱 성공: 총 {len(feed.entries)}개의 기사 발견")

            recent_count = 0
            matched_count = 0
            undated_count = 0
            for entry in feed.entries:
                title = clean_text(getattr(entry, "title", ""))
                link = str(getattr(entry, "link", "")).strip()
                published = get_published_time(entry, feed_info.get("naive_kst", False))
                if not published:
                    undated_count += 1
                    continue
                if not title or not re.match(r"https?://\S+$", link):
                    continue
                if not (window_start <= published <= now):
                    continue
                recent_count += 1

                summary_text = clean_text(getattr(entry, "summary", getattr(entry, "description", "")))
                topics = match_topics(title, summary_text)
                if not topics or link in history_links:
                    continue

                matched_count += 1
                history_links.add(link)
                candidates.append({
                    "title": title,
                    "link": link,
                    "published": published,
                    "source": feed_info["name"],
                    "topics": topics,
                    "summary": summary_text[:SUMMARY_LIMIT],
                })
            print(f" -> 최근 {WINDOW_HOURS}시간 기사: {recent_count}개, AI 기사: {matched_count}개")
            if undated_count:
                print(f" -> 발행일시를 읽지 못해 제외한 기사: {undated_count}개")
        except Exception as e:
            print(f" -> RSS 파싱 에러: {e}")
            append_error("수집", "수집 오류", url, str(e))
            failed_feeds += 1

    if failed_feeds == len(FEEDS):
        fail("모든 RSS 피드 수집에 실패했습니다.")

    # 상한은 하루 기준이다. 같은 날 다시 실행하면 이미 수집한 건수만큼 줄인다
    collected_today = sum(1 for record in read_records("수집") if record.get("title"))
    limit = max(0, (get_test_limit() or get_collect_max()) - collected_today)
    # 상한을 넘으면 주제 우선순위가 높고 최근인 기사부터 남긴다
    candidates.sort(key=lambda news: news["published"], reverse=True)
    candidates.sort(key=lambda news: topic_priority(news["topics"]))
    if len(candidates) > limit:
        print(f"\n -> 후보 {len(candidates)}개 중 상한 {limit}개만 남깁니다.")
        candidates = candidates[:limit]

    for news in candidates:
        append_record("수집", [
            ("TITLE", news["title"]),
            ("LINK", news["link"]),
            ("PUBLISHED", f"{news['published'].astimezone(KST):%Y-%m-%d %H:%M} KST"),
            ("SOURCE", news["source"]),
            ("TOPIC", ", ".join(news["topics"])),
        ], block=("SUMMARY", news["summary"]))

    if not candidates and collected_today:
        print(f"\n -> 새로 수집한 기사는 없습니다. (오늘 이미 {collected_today}건 수집)")
    elif not candidates:
        with open(get_log_path("수집"), 'a', encoding='utf-8') as f:
            f.write("[STATUS] NO_ARTICLES\n")
            f.write("---\n")
        print("\n -> 조건에 맞는 기사가 0건이라 후속 단계는 중단됩니다.")
    else:
        print(f"\n -> 총 수집 기사 수: {len(candidates)}개")
    print("\n--- 1. 수집 파이프라인 종료 ---")


if __name__ == "__main__":
    main()
