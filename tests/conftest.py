"""모든 테스트의 공통 준비.

- 결과 폴더를 임시 폴더로 바꿔 실제 archive/를 건드리지 않는다 (src 모듈을 불러오기 전에 설정해야 함)
- Gemini와 매체 사이트에는 요청하지 않는다. 필요한 테스트에서 가짜 함수로 바꿔 쓴다
"""
import os
import sys
import shutil
import tempfile

DATA_DIR = tempfile.mkdtemp(prefix="pipeline-test-")
os.environ["PIPELINE_DATA_DIR"] = DATA_DIR
# .env의 값이 테스트 결과를 바꾸지 않도록 미리 정해 둔다 (.env는 이미 설정된 값을 덮어쓰지 않음)
os.environ.update({
    "GEMINI_API_KEY": "test-key",
    "GEMINI_MIN_INTERVAL": "0",
    "TOP_N": "3",
    "LIST_N": "5",
    "MIN_SCORE": "4",
    "COLLECT_MAX": "300",
    "TEST_MODE": "false",
    "COLLECT_TEST_MODE": "false",
})
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def clean_data_dir(monkeypatch):
    """테스트마다 빈 결과 폴더와 고정된 날짜로 시작한다."""
    shutil.rmtree(DATA_DIR, ignore_errors=True)
    os.makedirs(DATA_DIR)
    monkeypatch.setenv("RUN_DATE", "2026-10-01")
    import common
    # 재시도 대기와 호출 간격 때문에 테스트가 느려지지 않게 한다
    monkeypatch.setattr(common.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(common, "GEMINI_MIN_INTERVAL", 0)
    yield


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(DATA_DIR, ignore_errors=True)
