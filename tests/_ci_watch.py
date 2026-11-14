"""CI 전용 시험 시간 재기·멈춤 감시(판 147.2). tests/__init__.py 가 GITHUB_ACTIONS=true 일 때만 부른다.

- 시험마다 걸린 시간(unittest.TestCase.run 을 감싼다).
- 감시 스레드: 상한(초)이 지나도 안 끝나면 ::error 줄로 지금 도는 시험·스레드 스택 요약·느린 시험을 찍고 3 으로 끝낸다.
- 끝까지 가면 ::notice 줄로 느린 시험 위 20개와 합.
- 글에는 기계 경로를 넣지 않는다(파일은 이름만).
"""

import atexit
import os
import sys
import threading
import time
import traceback
import unittest

T0 = time.monotonic()
TIMES: dict = {}
NOW: dict = {"id": "", "t": 0.0}
_ORIG = unittest.TestCase.run


STACK: list = []   # 판 147.6: 시험 안에서 또 시험을 돌리는 꼴(겹침)에서도 바깥 시험의 시간이 그대로이게


def _run(self, result=None):
    tid, t0 = self.id(), time.monotonic()
    STACK.append((tid, t0))
    NOW.update(id=tid, t=t0)
    try:
        return _ORIG(self, result)
    finally:
        STACK.pop()
        TIMES[tid] = round(time.monotonic() - t0, 2)
        NOW.update(id=STACK[-1][0] if STACK else "", t=STACK[-1][1] if STACK else 0.0)


def _esc(s: str) -> str:
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _emit(kind: str, title: str, lines: list, per: int = 12) -> None:
    """한 주석이 길면 잘리므로 per 줄씩 나눠 여러 줄로."""
    for k in range(0, max(1, len(lines)), per):
        part = lines[k:k + per]
        n = f" ({k // per + 1})" if len(lines) > per else ""
        sys.stdout.write(f"::{kind} title={_esc(title + n)}::{_esc(chr(10).join(part))}\n")
    sys.stdout.flush()


def slow(n: int) -> list:
    return [f"{v:7.2f}s {k}" for k, v in sorted(TIMES.items(), key=lambda x: -x[1])[:n]]


def stacks() -> list:
    names = {t.ident: t.name for t in threading.enumerate()}
    out = []
    for ident, frame in sys._current_frames().items():
        fs = traceback.extract_stack(frame)[-4:]
        where = " <- ".join(f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}" for f in reversed(fs))
        out.append(f"[{names.get(ident, ident)}] {where}")
    return out


def _watch(limit: float) -> None:
    time.sleep(limit)
    cur = NOW["id"] or "(시험 사이)"
    ran = round(time.monotonic() - NOW["t"], 1) if NOW["t"] else 0
    _emit("error", f"시험 묶음이 {int(limit)}초 안에 안 끝났다 — 지금: {cur}",
          [f"지금 도는 시험: {cur} ({ran}초째)", f"끝난 시험 {len(TIMES)}개, 합 {round(sum(TIMES.values()), 1)}초"])
    _emit("error", "스레드 스택(위가 가장 안쪽)", stacks())
    _emit("error", "그때까지 느린 시험", slow(15))
    os._exit(3)


def children() -> list:
    """끝날 때 남은 이 프로세스의 자식(명령 이름만) — 출력 관을 붙잡고 남아 작업이 안 끝나는 꼴을 가르려고."""
    import subprocess
    try:
        out = subprocess.run(["ps", "-o", "pid=,ppid=,etime=,comm="], capture_output=True, text=True, timeout=10).stdout
    except Exception as e:   # noqa: BLE001
        return [f"ps 못 함: {type(e).__name__}"]
    me = str(os.getpid())
    rows = []
    for ln in out.splitlines():
        parts = ln.split(None, 3)
        if len(parts) == 4 and parts[1] == me:
            rows.append(f"pid {parts[0]} {parts[2]} {os.path.basename(parts[3])}")
    return rows or ["남은 자식 없음"]


def _done() -> None:
    _emit("notice", f"시험 시간 — {len(TIMES)}개, 합 {round(sum(TIMES.values()), 1)}초, 벽시계 {round(time.monotonic() - T0, 1)}초", slow(20))
    _emit("notice", "끝날 때 남은 자식 프로세스", children())


def install(limit: int) -> None:
    if os.environ.get("STEWART_CI_WATCH_PID"):   # 판 147.6: 시험이 띄운 하위 시험 프로세스에는 붙이지 않는다(겹쳐 세고 겹쳐 찍던 것)
        return
    os.environ["STEWART_CI_WATCH_PID"] = str(os.getpid())
    unittest.TestCase.run = _run
    threading.Thread(target=_watch, args=(float(limit),), daemon=True, name="ci-watch").start()
    atexit.register(_done)
