"""동시 작업자(판 108, SW-43 ②): 메인이 일을 쪼개 작업자에게 맡기고(바로 돌아옴) 자기 몫을 계속 한다. 다 같이 느려져도 된다.

도구 하나 `workers(action, …)`(문맥 값을 줄이려고 하나로 묶음):
  spawn(task, tools?, kind?, max_steps?)  작업자를 띄우고 바로 돌아온다 → {"id": "w1"}
  wait(ids?, timeout_s?)                   그 작업자들(없으면 전부)이 끝날 때까지 기다렸다 결과를 돌려준다
  collect()                                기다리지 않고 지금까지 끝난 것만 거둔다
  cancel(id)                               더는 기다리지 않는다(도는 걸음은 그 걸음 끝까지 간다 — 결과는 버린다)
  list()                                   지금 작업자 상태
- 작업자 = engine.run 한 번(새 문맥, 이 대화를 못 본다). 도구는 delegate 와 같은 규칙(적지 않으면 읽기 도구만, 깊이 1).
  사람에게 묻지 않는다 — 승인이 필요한 행동은 하지 않고 결과에 적는다(동시에 여러 카드가 뜨지 않게).
- 동시 상한: STEWART_SPAWN_MAX(없으면 백엔드 concurrency − 1, 최소 1, 최대 4) — 로컬 llama-serve(--parallel 1)면 1.
  넘게 띄우면 줄을 서서 자리가 나면 돈다. 메인 차례당 작업자 걸음 합은 delegate 와 같은 예산.
- 메인 걸음 사이: 끝난 작업자가 있으면 "[엔진] 작업자 wN 끝 — workers(action='collect')" 를 끼운다.
  메인이 답으로 끝내려는데 아직 도는 작업자가 있으면 차례에 한 번 "wait 로 기다렸다 모아 답하라"를 돌려준다.
- 끄기: STEWART_SPAWN=0(도구가 안 붙는다). chat·허브만(엔진 평가 길에는 없다).
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

NAME = "workers"
WAIT_MAX_S = 1800
DEFINITION = {"type": "function", "function": {
    "name": NAME,
    "description": ("동시 작업자: spawn 은 띄우고 바로 돌아온다 — 네 몫을 계속 하다가 wait(또는 collect)로 모아 답한다. "
                    "작업자는 이 대화를 못 보니 task 에 목표·범위·돌려받을 것을 다 적는다. tools 를 안 적으면 읽기 도구만."),
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["spawn", "wait", "collect", "cancel", "list"]},
        "task": {"type": "string"},
        "tools": {"type": "array", "items": {"type": "string"}},
        "kind": {"type": "string"},
        "ids": {"type": "array", "items": {"type": "string"}},
        "id": {"type": "string"},
        "timeout_s": {"type": "integer"},
    }, "required": ["action"]},
}}


# ── 작업자 등급(판 110): backends 표의 [tiers.<이름>] — backend·profile·budget·max·use(모델에게 보일 한 줄) ──
def load_tiers(path=None, main: bool = False) -> dict:
    """backends 표에서 등급. 없으면 {}(등급 없이 지금 백엔드). slim 은 메인(--tier)으로 앉힐 때만 뜻이 있다 — 작업자 목록에도 남는다(판 115).
    main 인자는 예전 호출과 맞추려고 남긴다."""
    import tomllib
    from .config import backends_path
    try:
        data = tomllib.loads(Path(path or backends_path()).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    out = {}
    for name, t in (data.get("tiers") or {}).items():
        if isinstance(t, dict) and (t.get("backend") or t.get("profile")):
            out[str(name)] = {"backend": t.get("backend"), "profile": t.get("profile"), "budget": t.get("budget"),
                              "max": int(t.get("max") or 1), "use": " ".join(str(t.get("use") or "").split())[:80],
                              "default_for": t.get("default_for") or [], "slim": bool(t.get("slim")),
                              "slim_keep": t.get("slim_keep"), "slim_system": t.get("slim_system")}
    return out


def pick_tier(tiers: dict, args: dict) -> str | None:
    """모델이 tier 를 안 고르면 실행기 규칙: 도구를 안 적은 읽기 일은 default_for 에 "read" 가 있는 등급, 쓰기는 "write"."""
    t = str(args.get("tier") or "")
    if t in tiers:
        return t
    want = "write" if args.get("tools") else "read"
    return next((k for k, v in tiers.items() if want in v["default_for"]), None)


def definition(tiers: dict) -> dict:
    """등급이 있으면 tier 칸과 한 줄 쓰임새만 더한다(문맥 값 최소)."""
    if not tiers:
        return DEFINITION
    d = json.loads(json.dumps(DEFINITION))
    d["function"]["parameters"]["properties"]["tier"] = {
        "type": "string", "enum": list(tiers), "description": " · ".join(f"{k}: {v['use']}" for k, v in tiers.items() if v["use"])[:300]}
    return d


def enabled(env=None) -> bool:
    return (os.environ if env is None else env).get("STEWART_SPAWN", "1") != "0"


def max_workers(backend, env=None) -> int:
    v = (os.environ if env is None else env).get("STEWART_SPAWN_MAX")
    if v and v.isdigit() and int(v) > 0:
        return min(8, int(v))
    return max(1, min(4, int(getattr(backend, "concurrency", 1) or 1) - 1))


class Pool:
    """대화 하나의 작업자들. run(args) → (ok, 글)은 실 안에서 부른다(DG.run_child 를 감싼 것)."""

    def __init__(self, run, limit: int, halted=None, tier_limits: dict | None = None):
        self.run, self.halted = run, halted or (lambda: False)
        self.sem = threading.Semaphore(max(1, limit))
        self.tier_sem = {k: threading.Semaphore(max(1, int(v))) for k, v in (tier_limits or {}).items()}
        self.limit = limit
        self.lock = threading.Lock()
        self.w: dict[str, dict] = {}
        self.n = 0

    def spawn(self, args: dict) -> dict:
        with self.lock:
            self.n += 1
            wid = f"w{self.n}"
            rec = {"id": wid, "task": str(args.get("task") or "")[:200], "tier": str(args.get("tier") or ""), "status": "queued", "result": None, "ok": None,
                   "started": None, "ended": None, "collected": False, "noticed": False, "cancelled": False}
            self.w[wid] = rec

        sem = self.tier_sem.get(str(args.get("tier") or ""), self.sem)   # 등급마다 따로 센다(예: 로컬 9B 는 1)
        rec["tier"] = str(args.get("tier") or "")

        def go():
            with sem:
                if rec["cancelled"]:
                    rec.update(status="cancelled", ended=time.monotonic())
                    return
                rec.update(status="running", started=time.monotonic())
                try:
                    ok, text = self.run(dict(args), wid)
                except BaseException as e:   # noqa: BLE001 — 작업자 실패가 메인을 멈추지 않게
                    ok, text = False, f"작업자가 실패했다: {type(e).__name__}: {str(e)[:300]}"
                rec.update(ok=ok, result=text, ended=time.monotonic(), status="cancelled" if rec["cancelled"] else "done")
        threading.Thread(target=go, daemon=True, name=f"spawn-{wid}").start()
        return rec

    def running(self) -> list[str]:
        return [k for k, r in self.w.items() if r["status"] in ("queued", "running")]

    def fresh_done(self) -> list[str]:
        """끝났는데 메인이 아직 모르는 작업자(걸음 사이 알림용 — 한 번만)."""
        out = []
        with self.lock:
            for k, r in self.w.items():
                if r["status"] == "done" and not r["collected"] and not r["noticed"]:
                    r["noticed"] = True
                    out.append(k)
        return out

    def _take(self, ids) -> list[dict]:
        out = []
        for k in ids:
            r = self.w.get(k)
            if r and r["status"] in ("done", "cancelled") and not r["collected"]:
                r["collected"] = True
                out.append(r)
        return out

    def collect(self) -> list[dict]:
        return self._take(list(self.w))

    def wait(self, ids=None, timeout_s: float = 600) -> tuple[list[dict], list[str]]:
        want = [k for k in (ids or list(self.w)) if k in self.w]
        end = time.monotonic() + max(1.0, min(float(timeout_s or 600), WAIT_MAX_S))
        while time.monotonic() < end:
            if all(self.w[k]["status"] in ("done", "cancelled") for k in want):
                break
            if self.halted():
                raise KeyboardInterrupt
            time.sleep(0.2)
        return self._take(want), [k for k in want if self.w[k]["status"] in ("queued", "running")]

    def cancel(self, wid: str) -> bool:
        r = self.w.get(wid)
        if r is None or r["status"] in ("done", "cancelled"):
            return False
        r["cancelled"] = True
        if r["status"] == "queued":
            r["status"] = "cancelled"
        return True

    def cancel_all(self) -> int:
        return sum(self.cancel(k) for k in list(self.w))

    def listing(self) -> str:
        now = time.monotonic()
        rows = []
        for k, r in self.w.items():
            el = (r["ended"] or now) - r["started"] if r["started"] else 0
            rows.append(f"{k} {r['status']}{' · 결과 안 거둠' if r['status'] == 'done' and not r['collected'] else ''} · {el:.0f}초 · {r['task'][:60]}")
        return "\n".join(rows) or "작업자 없음"


def results_text(rs: list[dict], still: list[str] | None = None) -> str:
    parts = [f"── 작업자 {r['id']}" + (f" · {r['tier']}" if r.get("tier") else "") + f" ({'취소' if r['status'] == 'cancelled' else '끝'})\n{r['result'] or '(결과 없음)'}" for r in rs]
    if still:
        parts.append(f"아직 도는 작업자: {', '.join(still)} — 더 기다리려면 wait, 필요 없으면 cancel")
    return "\n\n".join(parts) or "거둘 결과가 없다(끝난 작업자 없음)"
