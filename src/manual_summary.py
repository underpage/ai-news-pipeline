"""요약에 실패한 기사를 Gemini 없이 다시 요약해 채워 넣는다. (로컬 전용)

    uv run src/manual_summary.py export [날짜]    요약 실패 기사를 작업 파일로 내보냄
    uv run src/manual_summary.py import [날짜]    채운 작업 파일을 검증해 요약 로그에 넣고 리포트를 다시 만듦

날짜(YYYY-MM-DD)를 생략하면 오늘. 작업 파일은 summary-work/날짜.json (커밋 제외).
작업 파일의 각 항목에 있는 prompt를 아무 LLM(또는 사람)에게 주고, 받은 JSON을 answer에 넣은 뒤 import 한다.
answer는 기사 하나의 객체({"lang": ..., "title_ko": ..., ...})나 그 객체 하나를 담은 배열(프롬프트가 요구하는 형식) 모두 받는다.
Gemini로 다시 시도하려면 이 스크립트 대신 RUN_DATE=날짜로 summarize.py와 publish.py를 차례로 실행한다.
"""
import os
import sys
import json

WORK_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "summary-work")


def usage():
    print(__doc__)
    sys.exit(1)


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("export", "import"):
        usage()
    if len(args) > 1:
        # 날짜는 common을 불러오기 전에 정해야 로그 경로에 반영된다
        os.environ["RUN_DATE"] = args[1]

    from common import append_record, read_records, load_prompt, fill_prompt, run_date
    import summarize
    import publish

    work_file = os.path.join(WORK_DIR, f"{run_date():%Y-%m-%d}.json")

    if args[0] == "export":
        summarized = {record["link"] for record in read_records("요약")}
        targets = [record for record in read_records("필터링")
                   if record.get("title") and record.get("body") and record["link"] not in summarized]
        if not targets:
            print("요약이 빠진 기사가 없습니다.")
            return
        template = load_prompt("summary_prompt.txt")
        items = [{
            "link": record["link"],
            "title": record["title"],
            # 자동 요약과 같은 프롬프트에 기사 하나만 넣는다
            "prompt": fill_prompt(template, articles=summarize.build_articles([record])),
            "answer": None,
        } for record in targets]
        os.makedirs(WORK_DIR, exist_ok=True)
        with open(work_file, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        print(f"{len(items)}건을 {os.path.relpath(work_file, os.path.dirname(WORK_DIR))}에 내보냈습니다. 각 항목의 answer를 채운 뒤 import 하세요.")
        return

    if not os.path.exists(work_file):
        print(f"작업 파일이 없습니다: {os.path.relpath(work_file, os.path.dirname(WORK_DIR))} (먼저 export)")
        sys.exit(1)
    with open(work_file, "r", encoding="utf-8") as f:
        items = json.load(f)

    targets = {record["link"]: record for record in read_records("필터링") if record.get("body")}
    summarized = {record["link"] for record in read_records("요약")}
    added = 0
    for item in items:
        if not item.get("answer") or item["link"] in summarized:
            continue
        target = targets.get(item["link"])
        if not target:
            print(f" -> 요약 대상이 아닌 기사라 건너뜀: {item['title']}")
            continue
        answer = item["answer"]
        if isinstance(answer, str):
            try:
                answer = json.loads(answer)
            except ValueError:
                pass
        if isinstance(answer, list):
            # 프롬프트대로 배열로 받은 경우 첫 객체를 쓴다 (기사 하나만 넣었으므로)
            answer = answer[0] if answer else {}
        try:
            # Gemini 응답과 같은 검증을 거친다 (3줄, 언어, 논조 값)
            summary = summarize.parse_summary(answer)
        except Exception as e:
            print(f" -> 형식 오류로 건너뜀: {item['title']} ({e})")
            continue
        append_record("요약", [
            ("TITLE", target["title"]),
            ("LINK", target["link"]),
            ("PUBLISHED", target.get("published")),
            ("SOURCE", target.get("source")),
            ("TOPIC", target.get("topic")),
            ("SCORE", target.get("score")),
            ("REASON", target.get("reason")),
            ("MODEL", "manual"),
        ], block=("SUMMARY", summary))
        added += 1

    print(f"{added}건을 요약 로그에 추가했습니다.")
    if added:
        publish.main()


if __name__ == "__main__":
    main()
