import os
import re
import sys
import time
import random
import datetime

try:
    # 로컬 실행용: 저장소 루트의 .env를 읽는다. 이미 설정된 환경 변수는 덮어쓰지 않는다
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 코드·설정(src, config, .env)과 실행 결과(data)를 분리한다. 결과는 저장소 루트의 archive/ 아래에 쌓인다
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 테스트 모드(TEST_MODE=true)의 결과는 archive-test/ 에 따로 쌓아 실제 기록과 섞이지 않게 한다
TEST_MODE = os.environ.get("TEST_MODE", "false").lower() in {"1", "true", "yes", "y"}
DATA_DIR = os.path.join(ROOT_DIR, "archive-test" if TEST_MODE else "archive")
# 자동 테스트(pytest)는 결과 폴더를 임시 폴더로 바꿔 실제 기록을 건드리지 않는다
if os.environ.get("PIPELINE_DATA_DIR"):
    DATA_DIR = os.environ["PIPELINE_DATA_DIR"]
LOGS_DIR = os.path.join(DATA_DIR, "logs")
NEWS_DIR = os.path.join(DATA_DIR, "news")
CONFIG_DIR = os.path.join(ROOT_DIR, "config")
BLOCK_TAGS = ("SUMMARY", "BODY")

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
# 호출 사이에 두는 최소 간격(초). 무료 한도(분당 5회)에 맞춘 값이며 유료 요금제에서는 0으로 낮춘다
GEMINI_MIN_INTERVAL = float(os.environ.get("GEMINI_MIN_INTERVAL", "13"))
# 응답이 멈췄을 때 끊는 시간(초)
GEMINI_TIMEOUT = float(os.environ.get("GEMINI_TIMEOUT", "300"))
# 다시 시도해도 결과가 같은 오류 (잘못된 요청, 인증 실패 등)
NO_RETRY_CODES = {400, 401, 403, 404}
# 지수 백오프: 실패할 때마다 대기 시간을 2배로 늘려 재시도한다 (기본 30초 → 60초 → 120초)
GEMINI_MAX_RETRIES = int(os.environ.get("GEMINI_MAX_RETRIES", "3"))
GEMINI_RETRY_BASE = float(os.environ.get("GEMINI_RETRY_BASE", "30"))

# 매체 사이트에 보내는 요청 사이의 최소 간격(초). 요청을 한꺼번에 보내지 않고 시간차를 둔다
REQUEST_INTERVAL = float(os.environ.get("REQUEST_INTERVAL", "2"))
REQUEST_HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

_client = None
_last_call = 0.0
_last_request = 0.0


def run_date():
    """로그와 리포트 파일명에 쓰는 날짜. RUN_DATE(YYYY-MM-DD)가 있으면 그 값을 쓴다.
    워크플로우의 Job들이 자정 전후로 나뉘어 실행돼도 같은 날짜 파일을 보게 하기 위함이다."""
    raw = os.environ.get("RUN_DATE", "").strip()
    if raw:
        try:
            return datetime.datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            pass
    return datetime.datetime.now()


def get_log_path(log_type):
    now = run_date()
    log_dir = os.path.join(LOGS_DIR, now.strftime("%Y"), now.strftime("%m"))
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, f"{now.strftime('%Y-%m-%d')}-{log_type}.txt")


def append_record(log_type, fields, block=None):
    """fields: [(태그, 값), ...] / block: (태그, 여러 줄 텍스트)"""
    with open(get_log_path(log_type), 'a', encoding='utf-8') as f:
        for tag, value in fields:
            if value in (None, ""):
                continue
            f.write(f"[{tag}] {' '.join(str(value).split())}\n")
        if block:
            tag, text = block
            # 레코드 구분자와 같은 줄이 본문에 있으면 레코드가 잘리므로 바꿔 쓴다
            lines = ["- - -" if line.strip() == "---" else line for line in str(text).splitlines()]
            f.write(f"[{tag}]\n" + "\n".join(lines) + "\n")
        f.write("---\n")


def append_error(log_type, tag, target, error_msg):
    with open(get_log_path(log_type), 'a', encoding='utf-8') as f:
        # 오류 메시지의 줄바꿈이 가짜 레코드를 만들지 않도록 한 줄로 적는다
        f.write(f"[{tag}] {' '.join(str(target).split())} - {' '.join(str(error_msg).split())}\n")
        f.write("---\n")


def read_errors(log_type):
    """오늘 로그의 오류 줄을 (대상, 사유) 목록으로 읽는다. 본문·요약 블록 안의 글은 보지 않는다."""
    errors = []
    filename = get_log_path(log_type)
    if not os.path.exists(filename):
        return errors
    in_block = False
    with open(filename, 'r', encoding='utf-8') as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            if line.strip() == "---":
                in_block = False
            elif in_block:
                continue
            elif line.strip().strip("[]") in BLOCK_TAGS and line.strip().startswith("["):
                in_block = True
            else:
                match = re.match(r"\[(?:수집|평가|요약) 오류\] (.+?) - (.*)$", line)
                if match:
                    errors.append((match.group(1), match.group(2)))
    return errors


def read_records(log_type):
    """오늘 로그에서 [LINK]가 있는 레코드만 읽는다. 키는 태그의 소문자."""
    return read_records_from(get_log_path(log_type))


def read_records_from(filename):
    records = []
    if not os.path.exists(filename):
        return records

    current = {}
    block_tag = None
    block_lines = []
    with open(filename, 'r', encoding='utf-8') as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            if line.strip() == "---":
                if block_tag:
                    current[block_tag.lower()] = "\n".join(block_lines).strip()
                if "link" in current:
                    records.append(current)
                current = {}
                block_tag = None
                block_lines = []
            elif block_tag:
                block_lines.append(line)
            elif line.strip().strip("[]") in BLOCK_TAGS and line.strip().startswith("["):
                block_tag = line.strip().strip("[]")
            elif line.startswith("[") and "] " in line:
                tag, value = line[1:].split("] ", 1)
                current[tag.lower()] = value.strip()
    return records


def fill_prompt(template, **values):
    """{이름} 자리표시자를 한 번에 채운다. 채워 넣은 글 안의 중괄호는 다시 치환하지 않는다."""
    return re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


def load_prompt(name):
    prompt_path = os.path.join(os.path.dirname(__file__), "prompts", name)
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise ValueError(f"{name} 파일을 찾을 수 없습니다.")


def fetch(url, timeout=15):
    """RSS 피드와 기사 페이지를 가져온다. 요청은 항상 하나씩, 간격을 두고 보낸다."""
    global _last_request
    import requests
    time.sleep(max(0.0, _last_request + REQUEST_INTERVAL - time.time()))
    try:
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=timeout)
    finally:
        _last_request = time.time()
    response.raise_for_status()
    return response


def clean_inline(text, limit=0):
    """모델이 쓴 글을 리포트에 넣기 전에 한 줄 평문으로 정리한다 (줄바꿈, 마크다운 링크, HTML 태그 기호 제거)."""
    text = " ".join(str(text or "").split())
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[<>\[\]]", "", text).strip()
    if limit and len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def fail(message):
    """단계 전체가 실패했을 때 호출한다. Actions 화면에 오류로 표시하고 Job을 실패 처리한다."""
    print(f"::error::{message}")
    sys.exit(1)


def get_retry_wait(attempt, error):
    """attempt번째 재시도 전 대기 시간(초). 서버가 알려 준 대기 시간이 더 길면 그 값을 따른다."""
    wait = GEMINI_RETRY_BASE * (2 ** (attempt - 1))
    match = re.search(r"retry in ([\d.]+)s", str(error), re.IGNORECASE)
    if match:
        wait = max(wait, float(match.group(1)) + 1)
    # 지터: 같은 시각에 재시도가 몰리지 않도록 최대 10%를 더한다
    return wait * (1 + random.uniform(0, 0.1))


def is_retryable(error):
    """다시 시도하면 풀릴 수 있는 오류인지. 잘못된 요청·인증 실패와 하루 요청 한도 초과는 기다려도 같다."""
    return getattr(error, "code", None) not in NO_RETRY_CODES and "PerDay" not in str(error)


def call_gemini(prompt, schema=None):
    """schema를 주면 그 구조의 JSON으로 응답을 강제한다."""
    global _client, _last_call
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("Gemini API 키 미설정")
        import google.genai as genai
        _client = genai.Client(api_key=api_key, http_options={"timeout": int(GEMINI_TIMEOUT * 1000)})

    # 같은 입력에 같은 결과가 나오도록 무작위성을 끈다
    config = {"temperature": 0}
    if schema:
        config["response_mime_type"] = "application/json"
        config["response_schema"] = schema
    last_error = None
    for attempt in range(GEMINI_MAX_RETRIES + 1):
        if attempt > 0:
            wait = get_retry_wait(attempt, last_error)
            print(f" -> Gemini 호출 실패, {wait:.0f}초 후 재시도 ({attempt}/{GEMINI_MAX_RETRIES}): {str(last_error)[:200]}")
            time.sleep(wait)
        else:
            time.sleep(max(0.0, _last_call + GEMINI_MIN_INTERVAL - time.time()))
        try:
            _last_call = time.time()
            response = _client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=config
            )
            if not response.text:
                # 차단되었거나 출력 한도를 넘겨 글이 없는 응답
                raise ValueError("Gemini가 빈 응답을 반환했습니다.")
            candidates = getattr(response, "candidates", None) or []
            finish_reason = str(getattr(candidates[0], "finish_reason", "")) if candidates else ""
            if "MAX_TOKENS" in finish_reason:
                raise ValueError("Gemini 응답이 출력 한도에서 잘렸습니다.")
            return response.text.strip()
        except Exception as e:
            last_error = e
            if not is_retryable(e):
                break
    raise last_error
