"""시험 패키지. 가짜 백엔드 주소로 /v1/models(서버 문맥 길이)를 묻지 않게 끈다(시험이 직접 켜서 본다)."""
import os

os.environ.setdefault("STEWART_SERVED_CONTEXT", "0")
# 기억: chat·허브 기본 켬(판 50)이지만 시험 묶음은 끈다(지시문·도구 목록이 그대로이게). 기억 시험은 직접 켠다
os.environ.setdefault("STEWART_MEMORY", "0")
os.environ.setdefault("STEWART_SEARXNG_MIN_INTERVAL", "0")   # 판 146: 검색 요청 간격(3.2초)을 시험에서 기다리지 않게(간격 시험은 값을 직접 준다)
os.environ.setdefault("STEWART_BACKEND_RETRIES", "0")   # 백엔드 다시 보내기 대기가 시험을 늦추지 않게(test_u59 만 푼다)
os.environ.setdefault("STEWART_BOARD_QUIET", "0")
os.environ.setdefault("STEWART_WEB_FETCH", "0")
os.environ.setdefault("STEWART_DOC_SEARCH", "0")
os.environ.setdefault("STEWART_SUMMARY", "0")
os.environ.setdefault("STEWART_BUILTIN_SKILLS", "0")   # 내장 스킬(판 70)도 시험 묶음에서 끈다(지시문·도구 목록 그대로) — 스킬 시험은 직접 켠다       # 요약 접기 자동(판 65)은 시험 묶음에서 끈다(가짜 서버 대본이 흐트러지지 않게)    # 실사용 기본 켬인 doc_search(판 64)도 시험 묶음에서 끈다     # 실사용 기본 켬인 웹 읽기(판 61)는 시험 묶음에서 끈다(도구 목록 그대로) — 웹 시험은 직접 켠다   # 현황판 밤 조용(23:00~07:30)은 시험 시각과 상관없게 끈다(test_u39 가 따로 켠다)
# 시험은 저장소의 예시 백엔드를 쓴다(개발 체크아웃의 로컬 backends.toml·사용자 설정에 기대지 않게)
os.environ.setdefault("STEWART_BACKENDS", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                       "backends.example.toml"))
# 프로젝트 구성(projcfg): 사용자 전역 폴더(~/.config/stewart·~/.claude)를 읽지 않게 없는 폴더를 가리킨다(시험이 직접 바꿔 본다)
os.environ.setdefault("STEWART_CLAUDE_HOME", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_no_claude_home"))
os.environ.setdefault("STEWART_USER_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_no_user_dir"))
# 사용자 기본 자리(~/.local/state/stewart·~/.local/share·~/.cache·~/.config/stewart 의 profiles·skills)를 타지 않게
# 시험 한 번마다 새 임시 폴더로 돌린다. 시험이 직접 값을 주면 그 값이 이긴다(setdefault).
import socketserver as _socketserver
import tempfile as _tempfile

# 판 146: 시험 안 서버(가짜 모델·허브 화면)의 serve_forever 기본 poll 간격 0.5초가 닫을 때마다 shutdown 을 0.5초씩 붙잡았다
# (시험 하나에 서버 셋이면 1초 넘게) — 시험 프로세스에서만 0.05초로. 제품 허브의 기본값은 그대로다.
_socketserver.BaseServer.serve_forever.__defaults__ = (0.05,)

_SANDBOX = _tempfile.mkdtemp(prefix="stewart-tests-")
for _k, _sub in (("XDG_STATE_HOME", "state"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"),
                 ("STEWART_RUNS_DIR", "state/stewart/runs"), ("STEWART_MEMORY_DIR", "data/stewart/memory"),
                 ("STEWART_SKILLS_DIR", "config/stewart/skills"), ("STEWART_PROFILES_DIR", "config/stewart/profiles"),
                 ("STEWART_PRUNE_ROOT", "cache/stewart"), ("STEWART_CLOUD_DIR", "cloud"),
                 ("STEWART_TALK_DIR", "data/stewart/talk")):   # 판 137: 대화 자리 폴더(~/.local/share/stewart/talk 고정 기본값)도
    os.environ.setdefault(_k, os.path.join(_SANDBOX, _sub))
import atexit as _atexit
import shutil as _shutil

_atexit.register(_shutil.rmtree, _SANDBOX, True)


def _sweep_stale(root: str, me: str, older_s: float = 6 * 3600) -> int:
    """강제 종료로 남은 앞 시험의 임시 폴더를 지운다(판 87). 이 시험 꾸러미가 만든 것(.pid 표지)만, 그 pid 가 죽었고
    6시간 넘게 안 바뀐 것만 — 다른 시험이 쓰는 중인 폴더나 표지 없는 폴더는 건드리지 않는다. 지운 수."""
    import time as _time
    n = 0
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    for nm in names:
        d = os.path.join(root, nm)
        if not nm.startswith("stewart-tests-") or d == me:
            continue
        mark = os.path.join(d, ".pid")
        try:
            pid = int(open(mark).read().strip())
            if _time.time() - os.path.getmtime(mark) < older_s:
                continue
        except (OSError, ValueError):
            continue
        try:
            os.kill(pid, 0)
            continue                     # 아직 살아 있다
        except ProcessLookupError:
            pass
        except OSError:
            continue                     # 살았는지 모른다(권한) — 두기
        _shutil.rmtree(d, True)
        n += 1
    return n


try:
    with open(os.path.join(_SANDBOX, ".pid"), "w") as _f:
        _f.write(str(os.getpid()))
    _sweep_stale(os.path.dirname(_SANDBOX), _SANDBOX)
except OSError:
    pass
import urllib.request  # noqa: E402

# 127.0.0.1 시험 서버로 가는 요청: 환경·시스템 프록시를 타지 않는다(macOS urllib 은 시스템 프록시를 따른다)
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 시험 하나 감시(판 67·68): 공개 트리 시험이 가끔 30분 제한까지 멈췄고, 시험이 띄운 셸(새 세션)이 남아 고아가 됐다.
# - 시험 하나가 STEWART_TEST_TIMEOUT 초(기본 240)를 넘으면: 모든 스레드 스택을 찍고, 남은 stewart 셸·허브 작업자를 묶음째 끝낸 뒤 실패로 끝낸다
#   (감시 스레드. 그 스레드도 못 돌면 faulthandler 가 +60초에 스택만 찍고 끝낸다). 0 이면 끔
# - 시험이 끝났는데 그 시험이 띄운 셸·작업자가 살아 있으면 묶음째 끝내고 그 시험을 실패로 적는다(STEWART_TEST_LEAKS=warn 이면 경고만)
import faulthandler as _fh  # noqa: E402
import threading as _th  # noqa: E402
import unittest as _ut  # noqa: E402

_TEST_TIMEOUT = float(os.environ.get("STEWART_TEST_TIMEOUT") or 240)
_LEAKS = os.environ.get("STEWART_TEST_LEAKS") or "fail"


def _live_children():
    """살아 있는 stewart 셸(Shell)·셸 백그라운드 작업·허브 작업자 [(설명, 끝내기)]."""
    out = []
    try:
        from stewart import osdep as _od, shell as _sh
        for p in list(_sh.SPAWNED):
            if p.poll() is None:
                def _stop(p=p):
                    _od.kill_group(p, True)
                    try:
                        p.wait(timeout=5)
                    except Exception:
                        pass
                out.append((f"셸 pid {p.pid}", _stop))
    except Exception:
        pass
    try:
        from stewart import hub as _hb, osdep as _od2
        for p in list(_hb.WORKERS):
            if p.poll() is None:
                def _stop2(p=p):
                    _od2.kill_group(p, True)
                    try:
                        p.wait(timeout=5)
                    except Exception:
                        pass
                out.append((f"허브 작업자 pid {p.pid}", _stop2))
    except Exception:
        pass
    return out


def _reap_all():
    for _d, stop in _live_children():
        try:
            stop()
        except Exception:
            pass


if not getattr(_ut.TestCase, "_stewart_watchdog", False):
    _orig_run = _ut.TestCase.run

    def _run_with_watchdog(self, result=None):
        before = {d for d, _ in _live_children()}
        done = _th.Event()
        if _TEST_TIMEOUT > 0:
            def _watch(test=self):
                if done.wait(_TEST_TIMEOUT):
                    return
                import sys as _sys
                _sys.stderr.write(f"\n[시험 감시] {test.id()} 가 {_TEST_TIMEOUT:.0f}초를 넘었다 — 스택을 찍고 남은 셸·작업자를 끝낸 뒤 끝낸다\n")
                _fh.dump_traceback(all_threads=True)
                _reap_all()
                os._exit(3)
            _th.Thread(target=_watch, name="stewart-test-watchdog", daemon=True).start()
            _fh.dump_traceback_later(_TEST_TIMEOUT + 60, exit=True)
        try:
            return _orig_run(self, result)
        finally:
            done.set()
            if _TEST_TIMEOUT > 0:
                _fh.cancel_dump_traceback_later()
            left = [(d, stop) for d, stop in _live_children() if d not in before]
            if left:
                for _d, stop in left:
                    try:
                        stop()
                    except Exception:
                        pass
                msg = "시험이 끝났는데 띄운 것이 남았다(끝냈다): " + ", ".join(d for d, _ in left) + " — 시험에서 close 를 부르거나 addCleanup 으로 닫는다"
                if _LEAKS == "fail" and result is not None:
                    result.addFailure(self, (AssertionError, AssertionError(msg), None))
                else:
                    import sys as _sys
                    _sys.stderr.write(f"\n[시험 감시] {self.id()}: {msg}\n")
    _ut.TestCase.run = _run_with_watchdog
    _ut.TestCase._stewart_watchdog = True
    _atexit.register(_reap_all)   # 시험 묶음 끝에도 남은 것을 묶음째(atexit 는 정상 끝에서만 — 시간 끊김은 위 감시가)
    # 끝낼 때(남은 일반 스레드를 기다리는 자리)에서 멈추는 것도 잡는다: 그 자리에 들어서면 120초 감시를 건다
    try:
        _th._register_atexit(lambda: _fh.dump_traceback_later(120, exit=True))
    except Exception:
        pass
