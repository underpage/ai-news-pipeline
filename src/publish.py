import os
import re
import datetime

from common import CONFIG_DIR, DATA_DIR, NEWS_DIR, ROOT_DIR, read_errors, read_records, run_date



def ensure_dir(path):
    if path and not os.path.exists(path):
        os.makedirs(path)


def by_score(news_list):
    # 같은 날 다시 실행해 기사가 뒤에 추가돼도 중요도 순을 유지한다. 점수가 같으면 기록된 순서
    def score(news):
        try:
            return int(news.get("score", 0))
        except ValueError:
            return 0
    return sorted(news_list, key=lambda news: -score(news))


def load_summary_targets():
    """요약 대상 기사(본문을 가져온 상위 기사)와 그 요약. 요약에 실패한 기사도 빼지 않고 상태만 표시한다."""
    targets = [record for record in read_records("필터링") if record.get("title") and record.get("body")]
    summaries = {record["link"]: record for record in read_records("요약") if record.get("title")}
    errors = read_errors("요약")
    failed_links = {target for target, _ in errors}
    unavailable_links = {target for target, reason in errors if reason.startswith("요약 불가")}

    items = []
    for target in targets:
        summary = summaries.get(target["link"])
        if summary:
            items.append({**target, **summary, "summary_status": "ok"})
        else:
            # unavailable: 본문이 기사가 아니라 요약할 수 없음 / failed: 요약 호출이 실패함
            # pending: 요약을 시도하지 못함 (실행 중단 등)
            if target["link"] in unavailable_links:
                status = "unavailable"
            elif target["link"] in failed_links:
                status = "failed"
            else:
                status = "pending"
            items.append({**target, "summary": "", "summary_status": status})
    target_links = {target["link"] for target in targets}
    items += [{**summary, "summary_status": "ok"} for link, summary in summaries.items() if link not in target_links]
    return by_score(items)


def load_listed_news():
    # 필터링 로그에서 목록으로 선정된 기사
    return by_score([news for news in read_records("필터링") if news.get("title")])


REPORT_TEMPLATE = "report.md.j2"


def render_report(report_date, news_list, listed_news):
    """리포트 모양은 코드가 아니라 config/report.md.j2 템플릿이 정한다. 여기서는 값만 준비한다."""
    import jinja2

    articles = []
    for rank, item in enumerate(news_list, start=1):
        translated_title, eng_sum, kor_sum, tone_tag = parse_summary_blocks(item.get("summary", ""))
        articles.append({
            "rank": rank,
            "title": item["title"],
            "title_ko": translated_title,
            "published": item.get("published", ""),
            "topic": item.get("topic", ""),
            "score": item.get("score", ""),
            "reason": item.get("reason", ""),
            "tone": tone_tag.lstrip("#"),
            "source": item.get("source", ""),
            "link": item["link"],
            "summary_original": eng_sum,
            "summary_ko": kor_sum,
            "summary_raw": item.get("summary", ""),
            "summary_status": item.get("summary_status", "ok"),
            # manual: Gemini가 아닌 방법으로 채운 요약 (manual_summary.py)
            "summary_source": "manual" if item.get("model") == "manual" else "gemini",
        })

    # "더 읽어볼 기사" 목록에는 위에서 다룬 요약 기사(요약 실패 포함)를 빼고 나머지만 싣는다. 순위는 전체 목록 기준
    covered_links = {item["link"] for item in news_list}
    listed = []
    for rank, item in enumerate(listed_news, start=1):
        if item["link"] in covered_links:
            continue
        listed.append({
            "rank": rank,
            # 대괄호는 마크다운 링크 표기와 겹치므로 바꿔 쓴다
            "title": item["title"].replace("[", "(").replace("]", ")"),
            "link": item["link"],
            "topic": (item.get("topic") or "").split(",")[0].strip(),
            "source": item.get("source", ""),
            "published": item.get("published", ""),
            "score": item.get("score", ""),
        })

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(CONFIG_DIR),
        trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True,
        # 템플릿에 없는 이름을 쓰면 빈칸으로 넘어가지 않고 오류가 나게 한다
        undefined=jinja2.StrictUndefined,
    )
    return env.get_template(REPORT_TEMPLATE).render(date=report_date, articles=articles, listed=listed)


def write_report(path, report_date, news_list, listed_news):
    ensure_dir(os.path.dirname(path))
    content = render_report(report_date, news_list, listed_news)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content.rstrip("\n") + "\n")


def parse_summary_blocks(summary_text):
    lines = [line.strip() for line in summary_text.splitlines() if line.strip()]
    
    translated_title = ""
    english_summary = []
    korean_summary = []
    tone_tag = ""
    
    current_section = None
    
    for line in lines:
        if line.startswith("**[기사 제목 번역]**"):
            current_section = "translated_title"
            continue
        elif line.startswith("**[영문 3줄 요약"):
            current_section = "english_summary"
            continue
        elif line.startswith("**[국문 3줄 요약"):
            current_section = "korean_summary"
            continue
            
        # 태그만 있는 줄일 때만 논조 태그로 본다. 요약 문장에 같은 글자가 있어도 문장은 남긴다
        # (예전 로그의 "(논조 태그: #중립)" 형식 포함)
        tag_match = re.fullmatch(r"\(?(?:논조 태그:\s*)?(#긍정|#부정|#중립)\)?", line)
        if tag_match:
            tone_tag = tag_match.group(1)
            continue
            
        if current_section == "translated_title":
            translated_title += line + " "
        elif current_section == "english_summary":
            if line.startswith("-"):
                english_summary.append(line.lstrip("-").strip())
        elif current_section == "korean_summary":
            if line.startswith("-"):
                korean_summary.append(line.lstrip("-").strip())
                
    return translated_title.strip(), english_summary, korean_summary, tone_tag


def update_index(index_path, page_title, link_path, intro_text):
    ensure_dir(os.path.dirname(index_path))
    if not os.path.exists(index_path):
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(f"# {page_title}\n\n")
            f.write(f"{intro_text}\n\n")
            f.write("### 리포트 목록\n\n")
            f.write(f"- [{os.path.basename(link_path)} 보기](./{link_path})\n")
        return

    with open(index_path, "r", encoding="utf-8") as f:
        content = f.read()

    if link_path not in content:
        marker = "### 리포트 목록\n\n"
        if marker not in content:
            content += f"\n{marker}"
        prefix, suffix = content.split(marker, 1)
        new_content = prefix + marker + f"- [{os.path.basename(link_path)} 보기](./{link_path})\n" + suffix
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(new_content)


def save_to_markdown(news_list, candidates):
    now = run_date()
    today_text = now.strftime("%Y-%m-%d")
    month_text = now.strftime("%Y-%m")
    report_name = f"{today_text}.md"
    folder_name = os.path.join(NEWS_DIR, now.strftime("%Y"), now.strftime("%m"))

    unique_news = []
    seen_links = set()
    for item in news_list:
        link = item.get("link")
        if not link or link in seen_links:
            continue
        seen_links.add(link)
        unique_news.append(item)

    if not unique_news and not candidates:
        print("발행할 새 기사가 없습니다.")
        return

    folder_report_path = os.path.join(folder_name, report_name)
    folder_index_path = os.path.join(folder_name, "index.md")

    write_report(folder_report_path, today_text, unique_news, candidates)
    update_index(
        folder_index_path,
        f"{month_text} AI 뉴스 리포트",
        report_name,
        "월별 AI 뉴스 리포트 목록입니다."
    )

    print(f"{os.path.relpath(folder_report_path, ROOT_DIR)} 생성 완료 (요약 {len(unique_news)}건, 목록 {len(candidates)}건)")


STATS_DIR = os.path.join(DATA_DIR, "stats")
STATS_COLUMNS = [
    "date", "updated", "feeds_ok", "feeds_failed", "collected", "listed", "skipped",
    "rank_ok", "rank_failed_calls", "body_ok", "body_failed",
    "summary_target", "summary_ok", "summary_failed", "summary_pending", "failed_feeds",
]


def collect_stats():
    """그날 로그에서 단계별 성공·실패 건수를 센다."""
    from collect import FEEDS

    collected = [record for record in read_records("수집") if record.get("title")]
    evaluated = read_records("필터링")
    listed = [record for record in evaluated if record.get("title")]
    with_body = {record["link"] for record in listed if record.get("body")}
    summarized = {record["link"] for record in read_records("요약") if record.get("title")}

    failed_feeds = sorted({target for target, _ in read_errors("수집")})
    evaluate_errors = read_errors("필터링")
    rank_failures = [reason for target, reason in evaluate_errors if target == "전체 후보"]
    # 재실행으로 나중에 성공한 기사는 실패에서 뺀다
    body_failures = {target for target, reason in evaluate_errors if "본문" in reason} - with_body
    summary_failures = {target for target, _ in read_errors("요약")} - summarized

    return {
        "date": f"{run_date():%Y-%m-%d}",
        "updated": f"{datetime.datetime.now():%Y-%m-%d %H:%M}",
        "feeds_ok": len(FEEDS) - len(failed_feeds),
        "feeds_failed": len(failed_feeds),
        "collected": len(collected),
        "listed": len(listed),
        "skipped": len(evaluated) - len(listed),
        # 선정 단계를 거친 기록이 있으면 성공. 호출이 실패해 기록이 없으면 0
        "rank_ok": 1 if evaluated else 0,
        "rank_failed_calls": len(rank_failures),
        "body_ok": len(with_body),
        "body_failed": len(body_failures),
        "summary_target": len(with_body),
        "summary_ok": len(summarized & with_body),
        "summary_failed": len(summary_failures),
        "summary_pending": len(with_body - summarized - summary_failures),
        "failed_feeds": ";".join(failed_feeds),
    }


def write_stats():
    """하루에 한 줄씩 쌓이는 연도별 통계 파일을 갱신한다. 같은 날 다시 실행하면 그날 줄을 바꿔 쓴다.

    파일을 연도마다 나누는 것은 매번 파일 전체를 다시 쓰기 때문이다. 쓰다가 문제가 생겨도 그해만 영향을 받는다.
    """
    import csv

    today = collect_stats()
    stats_file = os.path.join(STATS_DIR, f"{run_date():%Y}.csv")
    rows = {}
    if os.path.exists(stats_file):
        with open(stats_file, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("date"):
                    rows[row["date"]] = row
    rows[today["date"]] = today

    ensure_dir(STATS_DIR)
    with open(stats_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=STATS_COLUMNS, extrasaction="ignore", restval="")
        writer.writeheader()
        for date in sorted(rows):
            writer.writerow(rows[date])

    print(f"[통계] 수집 매체 성공 {today['feeds_ok']} / 실패 {today['feeds_failed']}, 수집 기사 {today['collected']}건")
    print(f"[통계] 선정 {'성공' if today['rank_ok'] else '실패'} (목록 {today['listed']}건, 미선정 {today['skipped']}건, 호출 실패 {today['rank_failed_calls']}회)")
    print(f"[통계] 본문 수집 성공 {today['body_ok']} / 실패 {today['body_failed']}")
    print(f"[통계] 요약 대상 {today['summary_target']}건 중 성공 {today['summary_ok']} / 실패 {today['summary_failed']} / 미처리 {today['summary_pending']}")
    print(f"[통계] {os.path.relpath(stats_file, ROOT_DIR)} 갱신")


def main():
    print("--- 4. 마크다운 발행 파이프라인 시작 ---")
    news_list = load_summary_targets()
    save_to_markdown(news_list, load_listed_news())
    write_stats()
    print("--- 4. 마크다운 발행 파이프라인 종료 ---")


if __name__ == "__main__":
    main()
