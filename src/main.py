"""네 단계를 순서대로 한 번에 실행한다. (로컬 실행용. GitHub Actions는 단계별 Job으로 따로 실행한다)

    uv run src/main.py           실제 실행. 결과는 archive/
    uv run src/main.py --test    테스트 실행. 소량만 수집하고 결과는 archive-test/ (커밋되지 않음)
"""
import os
import sys

# 결과 폴더는 common을 불러올 때 정해지므로 다른 모듈보다 먼저 설정한다
if "--test" in sys.argv[1:]:
    os.environ["TEST_MODE"] = "true"

import collect
import evaluate
import summarize
import publish
from common import DATA_DIR, ROOT_DIR, TEST_MODE, read_records


def main():
    if TEST_MODE:
        print(f"[테스트 모드] 결과 폴더: {os.path.relpath(DATA_DIR, ROOT_DIR)}/")

    collect.main()
    if not any(record.get("title") for record in read_records("수집")):
        print("\n수집한 기사가 없어 이후 단계를 건너뜁니다.")
        return

    try:
        evaluate.main()
        summarize.main()
    finally:
        # 워크플로우와 같이, 앞 단계가 실패해도 그때까지의 결과는 발행한다
        publish.main()


if __name__ == "__main__":
    main()
