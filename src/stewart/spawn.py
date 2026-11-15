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
TIER_KEYS = ("backend", "profile", "budget", "max", "use", "default_for", "slim", "slim_keep", "slim_system",
             "manual", "max_usd_day", "max_usd_chat", "max_tokens_day", "tools", "tools_deny", "prompt",
             "think", "think_max_tokens", "think_max_tokens_first", "reasoning_effort", "reasoning_effort_at", "think_sampling", "thinking_token_budget", "reasoning_passback",
             "think_repeat_cut")
THINK_KEYS = ("think", "think_max_tokens", "think_max_tokens_first", "reasoning_effort", "reasoning_effort_at", "think_sampling",
              "thinking_token_budget", "reasoning_passback", "think_repeat_cut")


# 판 132: 등급의 기본 생각 깊이(reasoning_effort). 표·환경에 값이 없을 때만, 그 등급 백엔드의 대화 틀이 그 낱말을 받을 때만
# (backends.<이름>.effort_values 에 있을 때만) 싣는다. 목록이 없거나 그 낱말이 없으면 지금처럼 보내지 않고, 그 까닭을 보인다.
# 판 159: presto 기본은 low 를 대화 틀 자리(chat_template_kwargs)에 싣는다 — 잰 꼴(등급 칸 reasoning_effort = "low",
# reasoning_effort_at = "template") 그대로. 그 꼴은 effort_values 없이 쟀으므로 presto 는 목록이 없어도 싣는다
# (목록이 있는데 low 가 없으면 싣지 않는다). 바깥 백엔드(provider·바깥 주소)에는 싣지 않는다 — 그 칸을 모르는 API 가 많다.
# 되돌리기: 등급 칸에 reasoning_effort = "none"(보내지 않음) 또는 환경 STEWART_REASONING_EFFORT=none.
TIER_DEFAULT_EFFORT = {"moderato": "medium", "presto": "low"}
TIER_DEFAULT_EFFORT_AT = {"presto": "template"}     # 적지 않은 등급은 body(think.settings 기본)
TIER_DEFAULT_NO_LIST = ("presto",)                  # effort_values 가 없어도 싣는 등급(잰 꼴이 목록 없이였다)
EFFORT_OFF = ("", "none", "off")   # 등급 칸·환경에 적으면 노력 값을 보내지 않는다(서버 기본)
EFFORT_ENV = "STEWART_REASONING_EFFORT"


def default_effort(name, think: dict | None, backend) -> tuple[str | None, str]:
    """등급 기본 생각 깊이를 실을지 → (낱말 또는 None, 싣지 않은 까닭). 표에 reasoning_effort 를 적었으면 싣지 않는다(까닭 빈 글)."""
    want = TIER_DEFAULT_EFFORT.get(str(name or ""))
    if not want or "reasoning_effort" in (think or {}):
        return None, ""
    allowed = getattr(backend, "effort_values", None) if backend is not None else None
    bname = getattr(backend, "name", "") if backend is not None else ""
    if str(name) in TIER_DEFAULT_NO_LIST:   # 판 159
        ext = getattr(backend, "external", None)
        if backend is not None and (getattr(backend, "kind", "openai") == "anthropic" or (callable(ext) and ext())):
            return None, f"기본 {want} 을 싣지 않았다 — 백엔드 {bname} 는 바깥 API 다. 실으려면 등급 칸에 reasoning_effort 를 적는다"
        if allowed and want not in allowed:
            return None, f"기본 {want} 을 싣지 않았다 — 백엔드 {bname} 의 대화 틀이 받는 낱말({'·'.join(allowed)})에 {want} 이 없다"
        return want, ""
    if not allowed:
        return None, (f"기본 {want} 을 싣지 않았다 — 백엔드 {bname} 에 effort_values(대화 틀이 받는 낱말)가 없다. "
                      f"그 틀이 {want} 을 받으면 백엔드 칸에 effort_values 를 적는다")
    if want not in allowed:
        return None, f"기본 {want} 을 싣지 않았다 — 백엔드 {bname} 의 대화 틀이 받는 낱말({'·'.join(allowed)})에 {want} 이 없다"
    return want, ""


def effort_view(name, think: dict | None, backend, env=None) -> dict:
    """등급의 생각 깊이와 그 출처(표·기본·환경) — stewart tiers 표·허브 설정 시트가 보인다.
    → {"value": 낱말 또는 None(보내지 않음 = 서버 기본), "source": "env"|"env:off"|"tier"|"tier:off"|"default"|"", "note": 글}."""
    import re
    from .think import EFFORT_RX
    env = os.environ if env is None else env
    think = think or {}
    d, why = default_effort(name, think, backend)
    out = {"value": d, "source": "default" if d else "", "note": why}
    tv = think.get("reasoning_effort")
    if isinstance(tv, str) and tv.strip().lower() in EFFORT_OFF:
        out = {"value": None, "source": "tier:off", "note": ""}
    elif isinstance(tv, str) and re.match(EFFORT_RX, tv):
        out = {"value": tv, "source": "tier", "note": ""}
    v = env.get(EFFORT_ENV)
    if v is not None and v.strip().lower() in EFFORT_OFF:
        out = {"value": None, "source": "env:off", "note": out["note"] if out["source"] == "" else ""}
    elif v is not None and re.match(EFFORT_RX, v.strip().lower()):
        out = {"value": v.strip().lower(), "source": "env", "note": ""}
    allowed = getattr(backend, "effort_values", None) if backend is not None else None
    if out["value"] and allowed and out["value"] not in allowed:
        out = {"value": None, "source": out["source"],
               "note": f"{out['value']} 은 백엔드 {getattr(backend, 'name', '')} 가 받는 낱말({'·'.join(allowed)})이 아니라 보내지 않는다"}
    return out


def load_tiers(path=None, main: bool = False) -> dict:
    """backends 표에서 등급. 없으면 {}(등급 없이 지금 백엔드). slim 은 메인(--tier)으로 앉힐 때만 뜻이 있다 — 작업자 목록에도 남는다(판 115).
    판 116: 아는 칸이 하나라도 있으면 등급이다 — backend 를 안 적으면 기본 모델을 물려받는다. max 를 안 적으면 None(그 백엔드의 동시 수를 따른다).
    생각 칸(think·think_max_tokens·think_max_tokens_first·reasoning_effort·reasoning_effort_at·think_sampling·
    thinking_token_budget·reasoning_passback — 판 123)은 안 적으면 지금과 같다.
    main 인자는 예전 호출과 맞추려고 남긴다."""
    import tomllib
    from .config import ConfigError, backends_path
    try:   # 판 118.1: 백엔드 표가 아예 없으면(코드로 Backend 를 준 세션 등) 등급 없이 — 등급은 선택 기능
        data = tomllib.loads(Path(path or backends_path()).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, ConfigError):
        return {}
    out = {}
    for name, t in (data.get("tiers") or {}).items():
        if isinstance(t, dict) and any(k in t for k in TIER_KEYS):
            out[str(name)] = {"name": str(name), "backend": t.get("backend"), "profile": t.get("profile"), "budget": t.get("budget"),
                              "max": int(t["max"]) if t.get("max") else None, "use": " ".join(str(t.get("use") or "").split())[:80],
                              "default_for": t.get("default_for") or [], "slim": bool(t.get("slim")),
                              "slim_keep": t.get("slim_keep"), "slim_system": t.get("slim_system"),
                              "think": {k: t[k] for k in THINK_KEYS if k in t},
                              # 판 125: 사람만(바깥 등급의 기본) · 돈·토큰 상한 — tierview.is_manual·caps 가 읽는다
                              "manual": (bool(t["manual"]) if "manual" in t else None),
                              # 판 153: 도구 부분집합 — 그 자리 도구 가운데 이 이름들만 남긴다(더 주지 않는다). 없으면 그 자리 그대로
                              "tools": ([str(x) for x in t["tools"]] if isinstance(t.get("tools"), list) else None),
                              # 판 154: 도구 거부 목록 — 그 자리 도구에서 이 이름들을 뺀다(tools 와 함께 적으면 남긴 것에서 다시 뺀다)
                              "tools_deny": ([str(x) for x in t["tools_deny"]] if isinstance(t.get("tools_deny"), list) else None),
                              # 판 154: 지시문 절 켬끔(promptparts) — 표에 적은 그대로(검사는 promptparts.check)
                              **({"prompt": _prompt_paths(t["prompt"], path)} if "prompt" in t else {}),
                              # 판 154: 표에 적었지만 쓰지 않는 칸·값 — tier_notes 가 한 줄씩 알린다(조용히 무시하지 않게)
                              "unknown_keys": sorted(str(k) for k in t if k not in TIER_KEYS),
                              "bad_lists": sorted(k for k in ("tools", "tools_deny") if k in t and not isinstance(t[k], list)),
                              **{k: t[k] for k in ("max_usd_day", "max_usd_chat", "max_tokens_day") if k in t}}
    return out


def _prompt_paths(v, path) -> object:
    """판 154: prompt.filler_file 의 상대 경로는 백엔드 표가 있는 폴더 기준으로 푼다."""
    f = v.get("filler_file") if isinstance(v, dict) else None
    if not isinstance(f, str) or not f.strip() or Path(os.path.expanduser(f)).is_absolute():
        return v
    from .config import backends_path
    return {**v, "filler_file": str(Path(path or backends_path()).parent / f)}


def split_manual(tiers: dict, backends: dict | None = None) -> tuple[dict, set]:
    """판 125: 작업자·자동 분배가 쓸 등급과, 사람이 고를 때만 쓰는 등급(manual)을 가른다 → (자동 등급들, 사람만 이름들).
    manual 을 적었으면 그 값, 안 적었으면 그 등급의 백엔드가 바깥(Backend.external())일 때 사람만이다."""
    if backends is None:
        from .config import ConfigError, load_backends
        try:
            backends, _ = load_backends()
        except (OSError, ValueError, ConfigError):
            backends = {}
    from .tierview import is_manual
    auto, manual = {}, set()
    for k, t in tiers.items():
        if is_manual(t, backends.get(t.get("backend") or "")):
            manual.add(k)
        else:
            auto[k] = t
    return auto, manual


def tier_raw(t: dict, backend=None) -> dict:
    """프로필 raw["tier"] 에 싣는 꼴(엔진·chat·생각 손잡이가 같이 읽는다).
    판 132: 그 등급의 기본 생각 깊이(TIER_DEFAULT_EFFORT)를 표에 값이 없고 백엔드 틀이 받을 때 생각 칸에 싣는다(effort_default 표시).
    싣지 못하면 그 까닭(effort_default_skipped)을 남긴다. 환경 STEWART_REASONING_EFFORT 는 think.settings 에서 이긴다."""
    think = dict(t.get("think") or {})
    d, why = default_effort(t.get("name"), think, backend)
    if d:
        think.update(reasoning_effort=d, effort_default=True)
        at = TIER_DEFAULT_EFFORT_AT.get(str(t.get("name") or ""))
        if at and "reasoning_effort_at" not in think:   # 판 159: 표에 자리를 적었으면 그 자리
            think["reasoning_effort_at"] = at
    return {"name": t.get("name"), "budget": t.get("budget"), **({"think": think} if think else {}),
            **({"effort_default_skipped": why} if why else {}),
            **({"tools": list(t["tools"])} if isinstance(t.get("tools"), list) else {}),
            **({"tools_deny": list(t["tools_deny"])} if isinstance(t.get("tools_deny"), list) else {}),
            **({"prompt": _PP.check(t["prompt"])[0]} if _PP.check(t.get("prompt"))[0] else {})}


def narrow_tools(names, tier: dict | None):
    """판 153·154: 등급의 도구 칸으로 이름 목록을 줄인다(더 주지 않는다) — tools 가 있으면 그 이름만, tools_deny 가 있으면 그 이름을 뺀다.
    tier 는 load_tiers 꼴이나 tier_raw 꼴(둘 다 tools·tools_deny 칸). 칸이 없으면 그대로."""
    only, deny = (tier or {}).get("tools"), (tier or {}).get("tools_deny")
    keep = {str(x) for x in only} if isinstance(only, list) else None
    drop = {str(x) for x in deny} if isinstance(deny, list) else set()
    return [n for n in names if (keep is None or n in keep) and n not in drop]


from . import promptparts as _PP   # noqa: E402 — 판 154


def tier_notes(t: dict, manual: bool = False) -> list[str]:
    """판 154: 이 등급 칸 가운데 쓰이지 않는 것(조용히 무시하지 않게) — stewart tiers·설정 시트가 한 줄씩 보인다."""
    out = []
    if t.get("unknown_keys"):
        out.append(f"모르는 칸 {', '.join(t['unknown_keys'])} — 쓰지 않는다(아는 칸은 README '작업자 등급')")
    for k in t.get("bad_lists") or ():
        out.append(f'{k} 는 이름 목록(["read_file", …] 꼴)이어야 한다 — 이 값은 쓰지 않는다')
    if not t.get("slim") and (t.get("slim_keep") is not None or t.get("slim_system") is not None):
        out.append("slim = true 가 없어 slim_keep·slim_system 은 쓰지 않는다")
    if t.get("slim") and not manual:
        out.append("slim 은 메인 자리(허브 메인·chat·「＋ 새 대화」 코드 자리)에만 — 작업자로 띄울 때는 얇은 꼴을 쓰지 않는다")
    if t.get("slim") or t.get("profile"):
        out.append("profile·slim 은 대화(parlando) 자리에서는 쓰지 않는다 — 그 자리는 자기 지시문 그대로(백엔드·창·생각·도구 칸은 먹는다)")
    if manual and (t.get("default_for") or t.get("max")):
        out.append("사람만 등급이라 작업자로 띄우지 않는다 — default_for·max 는 쓰지 않는다")
    both = sorted(set(t.get("tools") or ()) & set(t.get("tools_deny") or ()))
    if both:
        out.append(f"{', '.join(both)} 가 tools 와 tools_deny 에 다 있다 — 빼는 쪽이 이긴다")
    pv, pn = _PP.check(t.get("prompt"))
    out += pn
    if pv.get("filler_file") and not Path(os.path.expanduser(pv["filler_file"])).is_file():
        out.append(f"prompt.filler_file 파일이 없다({Path(pv['filler_file']).name}) — 이 등급으로 앉히거나 띄우면 시작하지 않는다")
    chat_only = [k for k in ("place_hints", "nudges", "examples", "role", "board") if k in pv]
    if chat_only and not manual:
        out.append(f"prompt 의 {'·'.join(chat_only)} 는 메인 자리(메인·새 대화)에만 — 작업자 지시문에는 그 글·장치가 없다")
    if t.get("slim") and any(k in pv for k in ("role", "board", "engine_core")):
        out.append("slim 은 짧은 지시로 바꾸므로 prompt 의 role·board·engine_core 는 메인 자리에서 뜻이 없다(작업자에는 engine_core 만 먹는다)")
    if t.get("profile"):
        from .config import ConfigError, load_profile
        try:
            load_profile(str(t["profile"]))
        except (ConfigError, OSError, ValueError):
            out.append(f"profile '{t['profile']}' 를 찾거나 읽지 못한다 — 이 등급으로 앉히거나 띄우면 오류가 난다(stewart profiles 로 이름 확인)")
    return out


def unknown_note(tiers: dict, asked, manual: set | frozenset = frozenset()) -> str:
    """판 116: 모델이 없는 등급 이름을 냈으면 그 사실(조용히 바꿔 타지 않게). 판 125: 사람만 등급이면 그 까닭."""
    a = str(asked or "")
    if a in manual:
        return f"등급 '{a}' 는 사람이 대화를 열 때 고를 때만 쓴다(작업자로 부르지 않는다)"
    return f"모르는 등급 '{a[:30]}' — 있는 등급: {', '.join(tiers) or '없음'}" if a and a not in tiers else ""


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

        sem = self.tier_sem.get(str(args.get("tier") or ""), self.sem)   # 등급마다 따로 센다(서버 동시 수에 맞춰)
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
