"""시험 패키지. 가짜 백엔드 주소로 /v1/models(서버 문맥 길이)를 묻지 않게 끈다(시험이 직접 켜서 본다)."""
import os

os.environ.setdefault("STEWART_SERVED_CONTEXT", "0")
# 기억: chat·허브 기본 켬(판 50)이지만 시험 묶음은 끈다(지시문·도구 목록이 그대로이게). 기억 시험은 직접 켠다
os.environ.setdefault("STEWART_MEMORY", "0")
os.environ.setdefault("STEWART_BOARD_QUIET", "0")
os.environ.setdefault("STEWART_WEB_FETCH", "0")
os.environ.setdefault("STEWART_DOC_SEARCH", "0")
os.environ.setdefault("STEWART_SUMMARY", "0")       # 요약 접기 자동(판 65)은 시험 묶음에서 끈다(가짜 서버 대본이 흐트러지지 않게)    # 실사용 기본 켬인 doc_search(판 64)도 시험 묶음에서 끈다     # 실사용 기본 켬인 웹 읽기(판 61)는 시험 묶음에서 끈다(도구 목록 그대로) — 웹 시험은 직접 켠다   # 현황판 밤 조용(23:00~07:30)은 시험 시각과 상관없게 끈다(test_u39 가 따로 켠다)
# 시험은 저장소의 예시 백엔드를 쓴다(개발 체크아웃의 로컬 backends.toml·사용자 설정에 기대지 않게)
os.environ.setdefault("STEWART_BACKENDS", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                       "backends.example.toml"))
# 프로젝트 구성(projcfg): 사용자 전역 폴더(~/.config/stewart·~/.claude)를 읽지 않게 없는 폴더를 가리킨다(시험이 직접 바꿔 본다)
os.environ.setdefault("STEWART_CLAUDE_HOME", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_no_claude_home"))
os.environ.setdefault("STEWART_USER_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_no_user_dir"))
# 사용자 기본 자리(~/.local/state/stewart·~/.local/share·~/.cache·~/.config/stewart 의 profiles·skills)를 타지 않게
# 시험 한 번마다 새 임시 폴더로 돌린다. 시험이 직접 값을 주면 그 값이 이긴다(setdefault).
import tempfile as _tempfile

_SANDBOX = _tempfile.mkdtemp(prefix="stewart-tests-")
for _k, _sub in (("XDG_STATE_HOME", "state"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"),
                 ("STEWART_RUNS_DIR", "state/stewart/runs"), ("STEWART_MEMORY_DIR", "data/stewart/memory"),
                 ("STEWART_SKILLS_DIR", "config/stewart/skills"), ("STEWART_PROFILES_DIR", "config/stewart/profiles"),
                 ("STEWART_PRUNE_ROOT", "cache/stewart"), ("STEWART_CLOUD_DIR", "cloud")):
    os.environ.setdefault(_k, os.path.join(_SANDBOX, _sub))
import atexit as _atexit
import shutil as _shutil

_atexit.register(_shutil.rmtree, _SANDBOX, True)
import urllib.request  # noqa: E402

# 127.0.0.1 시험 서버로 가는 요청: 환경·시스템 프록시를 타지 않는다(macOS urllib 은 시스템 프록시를 따른다)
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))
