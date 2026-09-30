import os
import re
import datetime

from common import CONFIG_DIR, NEWS_DIR, ROOT_DIR, read_records, run_date



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


def load_summarized_news():
    return by_score([news for news in read_records("요약") if news.get("title")])


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
        })

    summarized_links = {item["link"] for item in news_list}
    listed = []
    for rank, item in enumerate(listed_news, start=1):
        listed.append({
            "rank": rank,
            # 대괄호는 마크다운 링크 표기와 겹치므로 바꿔 쓴다
            "title": item["title"].replace("[", "(").replace("]", ")"),
            "link": item["link"],
            "topic": (item.get("topic") or "").split(",")[0].strip(),
            "source": item.get("source", ""),
            "published": item.get("published", ""),
            "score": item.get("score", ""),
            "summarized": item["link"] in summarized_links,
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


def main():
    print("--- 4. 마크다운 발행 파이프라인 시작 ---")
    news_list = load_summarized_news()
    save_to_markdown(news_list, load_listed_news())
    print("--- 4. 마크다운 발행 파이프라인 종료 ---")


if __name__ == "__main__":
    main()
