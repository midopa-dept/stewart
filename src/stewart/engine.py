"""엔진: 모델 호출 → 툴콜 실행 → 결과 전달 반복."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from datetime import datetime

from . import __version__, bounds as BND, hooks as HK, memfile, memory as MEM, selfcheck as SC, think as TH, tools as T
from .backend import BackendError, chat, is_context_overflow, sync_context
from .compact import Compactor, est_tokens, mask_roll, repeat_stats, reply_reserve, settings as compact_settings
from .check import CheckContext, ToolCall, load_check
from .config import Backend, Profile
from .record import Recorder
from .resume import prepare, save_messages
from . import modes as M
from . import facts as FX
from . import reflect as RF
from . import cutoff as CO
from . import images as IMG
from . import manager as MG
from . import guard as G
from . import clarify
from . import envinfo
from . import skills as SK
from . import delegate as DG
from . import config_guard as CG
from . import components as CM
from .checkpoint import Checkpoints
from .schema import validate
from .osdep import run_command
from .shell import tool_env

REPEAT_NUDGE = 3  # 같은 호출이 이만큼 실패하면 결과에 경고를 붙인다
REPEAT_STOP = 6   # 이만큼이면 중단(repeated_failure)
LANGUAGE_RETRIES = 2  # 한국어가 아닌 최종 보고를 다시 요청하는 최대 횟수
_HANGUL = re.compile(r"[\uac00-\ud7a3]")
_LATIN = re.compile(r"[A-Za-z]")


def hangul_ratio(text: str) -> float:
    """코드 블록·`인라인 코드`를 뺀 글자 중 한글 비율(한글/(한글+라틴)). 글자가 없으면 1."""
    t = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    t = re.sub(r"`[^`]*`", " ", t)
    h, l = len(_HANGUL.findall(t)), len(_LATIN.findall(t))
    return 1.0 if h + l == 0 else h / (h + l)


def _strings_of(obj, keys: set[str]):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and isinstance(v, str):
                yield v
            else:
                yield from _strings_of(v, keys)
    elif isinstance(obj, list):
        for x in obj:
            yield from _strings_of(x, keys)


def _language_problem(profile, content: str) -> str | None:
    """한국어 보고 강제. 문제가 있으면 모델에 줄 지시문을, 없으면 None."""
    if profile.language != "ko":
        return None
    if profile.output_format == "free":
        if hangul_ratio(content) < 0.3:
            return "최종 보고가 한국어가 아니다. 같은 내용을 한국어로 다시 써라(코드·파일 이름·명령은 그대로). 도구는 부르지 마라."
        return None
    if not profile.language_fields:
        return None
    try:
        obj = _extract_json(content)
    except ValueError:
        return None  # 형식 검사에서 다룬다
    bad = [v for v in _strings_of(obj, set(profile.language_fields)) if hangul_ratio(v) < 0.3]
    if bad:
        return (f"{', '.join(profile.language_fields)} 필드는 한국어로 써야 한다(한국어가 아닌 것 {len(bad)}개, 예: {bad[0][:80]!r}). "
                "같은 JSON을 그 필드만 한국어로 고쳐 다시 출력하라.")
    return None
NUDGE_TOOLCALL = ("방금 응답에 도구 호출처럼 보이는 글이 있지만 도구 호출로 인식되지 않았다. "
                  "도구를 쓰려면 정해진 tool call 형식으로 호출하고, 끝났으면 도구 호출 없이 최종 답만 써라.")


def _git_rev() -> str:
    from .config import REPO_ROOT
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, encoding="utf-8",
                           errors="replace", timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _extract_json(text: str):
    """최종 답에서 JSON 하나를 꺼낸다(코드 펜스·앞뒤 말 허용)."""
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    for open_, close in (("{", "}"), ("[", "]")):
        i, j = t.find(open_), t.rfind(close)
        if i != -1 and j > i:
            try:
                return json.loads(t[i : j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("JSON을 찾지 못했다")


LENGTH_NOTE = ("[엔진] 생각이 단계당 생성 상한에 닿아 답이 비었다. 더 생각하지 말고 지금 아는 것으로 "
               "바로 도구를 부르거나 최종 답을 써라.")


def empty_length(resp: dict) -> bool:
    """상한(max_tokens)에 닿아 끝났는데 본문·툴콜이 없다(생각만 하다 잘림)."""
    ch = (resp.get("choices") or [{}])[0]
    m = ch.get("message") or {}
    return ch.get("finish_reason") == "length" and not (m.get("content") or "").strip() and not m.get("tool_calls")


def _outside_check(bd, env, name: str, args: dict, parent: dict) -> tuple[str | None, str | None]:
    """(거부 사유 | None, 'write'|'read'|None). parent 에 ask_outside 가 있으면(chat 의 작업자) 사람에게 묻는다."""
    paths = [p for p in [bd.tool_write(name, args)] if p]
    if name == "run_shell":
        cwd = getattr(env.shell, "cwd", None) if env.shell is not None else None
        paths += [p for p in bd.shell_outside(str(args.get("command") or ""), cwd) if p not in paths]
    if paths:
        ask = parent.get("ask_outside")
        return (ask(name, args, paths, "write") if ask else BND.RUN_DENY.format(path=", ".join(paths))), "write"
    r = bd.tool_read(name, args)
    if r:
        ask = parent.get("ask_outside")
        if bd.read_outside == "ask" and ask:
            return ask(name, args, [r], "read"), "read"
        return BND.READ_DENY.format(path=r), "read"
    return None, None


def _broken(st: dict, rec, step: int, kind: str, raw: str, **extra) -> None:
    """파서가 버린 깨진 툴콜: 원문 앞 2,000자를 사건 broken_toolcall 로(form: args_json·in_content·server_parse)."""
    st["broken_toolcalls"][kind] = st["broken_toolcalls"].get(kind, 0) + 1
    rec.event("broken_toolcall", step=step, form=kind, raw=(raw or "")[:2000], **extra)


def _error_kind(name: str, tc: dict, result: str) -> str:
    if tc.get("parse_error"):
        return "schema"
    if tc.get("denied"):
        return "denied"
    if tc.get("hook_blocked"):
        return "hook_blocked"
    r = result or ""
    if r.startswith(("인자가 빠졌다", "모르는 인자")):
        return "schema"
    if r.startswith("없는 도구") or "모르는 도구" in r[:40]:
        return "unknown_tool"
    if r.startswith("[시간 초과]") or "시간 초과" in r[:60]:
        return "timeout"
    return "exec_fail"


def _mark_call(st: dict, tc: dict, name: str, ok: bool, result: str) -> None:
    import hashlib
    if not ok:
        k = tc["error_kind"] = _error_kind(name, tc, result)
        st["tool_error_kinds"][k] = st["tool_error_kinds"].get(k, 0) + 1
    call = hashlib.sha256((name + "\0" + tc.get("raw_arguments", "")).encode("utf-8", "replace")).hexdigest()[:16]
    res = hashlib.sha256((result or "").encode("utf-8", "replace")).hexdigest()[:16]
    tc["call_sha"], tc["result_sha"] = call, res
    seen = st["call_results"].setdefault(call, set())
    if seen:
        if res in seen:
            st["same_call_same_result"] += 1
        else:
            st["same_call_diff_result"] += 1
    seen.add(res)


def _looks_like_toolcall(text: str, names: list[str]) -> bool:
    if not text:
        return False
    if re.search(r"<\|?tool_call|tool_code|\"function\"\s*:|\"name\"\s*:\s*\"(" + "|".join(names) + r")\"", text):
        return True
    return bool(re.search(r"\b(" + "|".join(names) + r")\s*[({]", text))


def compact_mode(env=None) -> dict | None:
    """평가 손잡이(판 66, 압축 방식 비교 SW-18). 주지 않으면 None — 평가 길 동작은 예전 그대로.
    STEWART_COMPACT_MODE = off | mask | mask+sum | sum | mask-roll, STEWART_COMPACT_WINDOW = 문턱을 잴 창(토큰, 서빙 창은 그대로)."""
    env = os.environ if env is None else env
    m = env.get("STEWART_COMPACT_MODE")
    w = env.get("STEWART_COMPACT_WINDOW")
    if not m and not w:
        return None
    src, bad = [], []
    mode = "mask+sum"
    if m:
        if m in ("off", "mask", "mask+sum", "sum", "mask-roll"):
            mode, _ = m, src.append("STEWART_COMPACT_MODE")
        else:
            bad.append("STEWART_COMPACT_MODE")
    window = None
    if w:
        if w.isdigit() and int(w) >= 1000:
            window, _ = int(w), src.append("STEWART_COMPACT_WINDOW")
        else:
            bad.append("STEWART_COMPACT_WINDOW")
    ratio = {"off": 0, "mask": 0.5, "mask+sum": 0.5, "sum": None, "mask-roll": 0}[mode]
    return {"mode": mode, "window": window, "ratio": ratio, "env": src, "ignored": bad}


def _fold_summary(messages: list[dict], backend, rec, cstate: dict, step: int, mode: str, last: int) -> dict | None:
    """평가 길 요약 접기: 시스템·과제(앞 2개)와 최근 10단계는 남기고 그 사이를 요약 한 쪽(사람 자리)으로. 원문은 기록 folds/."""
    from .compact import ask_summary, summary_text
    idx = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    if len(idx) <= 10:
        return None
    cut = idx[-10]
    span = messages[2:cut]
    if len(span) < 3:
        return None
    try:
        summ = ask_summary(chat, backend, summary_text(span))
    except Exception as e:
        rec.event("summarize_fail", step=step, mode=mode, error=f"{type(e).__name__}: {e}"[:300])
        return None
    if not summ:
        return None
    cstate["folds"] += 1
    try:
        fd = rec.dir / "folds"
        fd.mkdir(parents=True, exist_ok=True)
        (fd / f"{cstate['folds']:03d}.json").write_text(json.dumps({"step": step, "mode": mode, "summary": summ, "messages": span},
                                                                   ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    before = len(messages)
    messages[2:cut] = [{"role": "user", "content": "[엔진] 앞 과정 요약 — 문맥을 줄이려고 엔진이 앞 단계들을 접었다. 과제의 지시·금지는 그대로 지킨다. "
                                                  "실패 기록의 길은 되풀이하지 않는다.\n\n" + summ}]
    info = {"mode": mode, "step": step, "nth": cstate["folds"], "folded_messages": cut - 2, "before_messages": before,
            "after_messages": len(messages), "before_tokens": last, "summary_chars": len(summ)}
    rec.event("summarize", **info)
    return info


def web_settings() -> dict:
    """사용자 설정 [web](hub.toml — STEWART_HUB_CONFIG 또는 $XDG_CONFIG_HOME/stewart/hub.toml): backend·url·fetch. 없으면 {}."""
    try:
        import tomllib
        from .forge import config_path
        p = config_path()
        if p.is_file():
            w = tomllib.loads(p.read_text(encoding="utf-8")).get("web") or {}
            return w if isinstance(w, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


def web_search_config(profile: Profile, interactive: bool = False) -> dict | None:
    """웹 도구 설정(dict) 또는 None(둘 다 꺼짐).
    - 검색(web_search): 도구 목록에 있고 주소가 있어야 — 환경 변수(url_env, 기본 STEWART_SEARXNG_URL) → 프로필 [web_search] url
      → 사용자 설정 [web] url. [web] backend = "none" 이면 끔.
    - 읽기(web_fetch): 검색이 켜졌으면 켬. 검색이 없어도 실사용(chat·허브, interactive)은 [web] fetch(기본 true)면 켬 —
      평가 길(run·bench)은 지금처럼 주소가 있을 때만(도구 목록이 바뀌지 않게). STEWART_WEB_FETCH=0|1 이 이긴다.
    돌려주는 dict: url(""=검색 꺼짐)·fetch·why(꺼진 까닭)·max_*·timeout_s·categories."""
    if "web_search" not in profile.tools and "web_fetch" not in profile.tools and profile.tools != ["*"]:
        return None
    w = profile.web_search
    ws = web_settings() if interactive else {}   # 사용자 설정은 실사용(chat·허브)에만 — 평가 길은 환경 변수·프로필 그대로
    url = os.environ.get(w.get("url_env", "STEWART_SEARXNG_URL"), "") or w.get("url") or ws.get("url") or ""
    why = ""
    if str(ws.get("backend") or "searxng") == "none":
        url, why = "", "설정 [web] backend = none"
    elif "web_search" not in profile.tools and profile.tools != ["*"]:
        url = ""
    elif not url:
        why = "검색 주소 없음(STEWART_SEARXNG_URL 또는 hub.toml [web] url)"
    fx = os.environ.get("STEWART_WEB_FETCH")
    fetch_wanted = "web_fetch" in profile.tools or profile.tools == ["*"]
    fetch = fetch_wanted and (fx == "1" if fx in ("0", "1") else (bool(url) or (interactive and ws.get("fetch", True) is not False)))
    if not url and not fetch:
        return None
    per = w.get("max_per_file")          # 파일마다 상한은 프로필이 적었을 때만(media)
    return {"url": url, "fetch": fetch, "why": why, "max_per_file": int(per) if per else None,
            "max_total": int(w.get("max_total", T.WEB_MAX_TOTAL)), "max_results": int(w.get("max_results", 5)),
            "timeout_s": float(w.get("timeout_s", 20)), "categories": w.get("categories", "")}


def web_search_enabled(profile: Profile) -> int:
    """켜져 있으면 파일당 검색 상한, 꺼져 있으면 0."""
    c = web_search_config(profile)
    return (c["max_per_file"] or c["max_total"]) if c and c["url"] else 0


def memory_for(profile: Profile, interactive: bool = False) -> tuple[str, list[dict]]:
    """프로젝트 기억 파일(memfile). 프로필 memory_files=[] 이면 끔."""
    names = memfile.DEFAULT_NAMES if profile.memory_files is None else tuple(profile.memory_files)
    if not names:
        return "", []
    from . import projcfg as PC
    return memfile.collect(profile.workdir, names, profile.memory_max_chars or memfile.DEFAULT_MAX_CHARS,
                           fl=PC.flags(profile, interactive) if profile.memory_files is None else PC.Flags())


def run(profile: Profile, backend: Backend, task: str, rec: Recorder, max_steps: int | None = None,
        history: list[dict] | None = None, resumed_from: str | None = None, mode: str | None = None,
        parent: dict | None = None, images: list | None = None) -> dict:
    """history 가 있으면(--continue·--resume) 그 대화 뒤에 task 를 새 사람 입력으로 붙여 이어 간다.
    mode: auto(기본) 또는 plan(읽기 도구만, 계획을 최종 답으로). 사람에게 묻는 edits·ask 는 run 에서 쓸 수 없다.
    parent: 작업자(delegate)로 돌 때 부모가 주는 것 — cps(체크포인트 공유), env(웹 상한·기록 공유),
    ask(name, args) → 거부 사유|None(chat 부모의 확인 질문; 있으면 edits·ask 모드를 쓸 수 있다), on_call(name, args)(화면 줄)."""
    parent = parent or {}
    task0 = task   # 색인에는 엔진이 붙이기 전의 과제 글
    depth = 1 if parent else 0
    exec_mode = M.check_mode(mode or profile.mode or "auto", interactive=bool(parent.get("ask")))
    web = web_search_config(profile)
    from .shell import hide_env
    hide_env(backend.api_key_env)          # 백엔드 키 변수는 모델 셸 환경에서 뺀다(판 69)
    base_names = [t for t in DG.other_tools(clarify.other_tools(profile.tools)) if t != SK.NAME]
    tool_defs = T.definitions(base_names) if base_names else []
    if profile.tools and clarify.wanted(profile.tools, profile.output_format):
        tool_defs.append(clarify.DEFINITION)   # 되묻기(ask_user): 부르면 질문을 최종 출력으로 두고 끝낸다
    if profile.tools and DG.wanted(profile.tools, depth):
        tool_defs.append(DG.DEFINITION)   # 작업자에게 맡기기(작업자는 다시 맡기지 못한다)
    # 스킬: 부모는 도구가 있으면 켠다. 작업자는 부모가 load_skill 을 넘겨줬을 때만
    skill_on = bool(profile.tools) and profile.skills is not False and (depth == 0 or SK.NAME in profile.tools)
    from . import projcfg as PC
    pfl = PC.flags(profile, interactive=False)   # 무인 실행: Claude Code 꼴 호환은 기본 끔(정본은 그대로)
    from . import allowrules as AR
    own_rules = AR.RuleSet(profile.workdir, compat=pfl.compat)   # 부모(chat)가 규칙을 넘기지 않을 때(stewart run)

    def own_rule(args: dict, deny_only: bool) -> tuple[str | None, str]:
        cwd = getattr(env.shell, "cwd", None) if env.shell is not None else None
        ctx = AR.Ctx(profile.workdir, Path(cwd or profile.workdir), env.is_secret, allow_redirect=exec_mode == "edits")
        r = own_rules.decide(str(args.get("command") or ""), ctx)
        if r.effect not in ("allow", "deny") or (deny_only and r.effect != "deny"):
            return None, ""
        comps.fire("allow_rule", st["steps"], "shell_confirm",   # comps·st 는 아래에서 만들고, 부를 때는 있다
                   "allow" if r.effect == "allow" else "block", rule=":".join(r.rule_ids[:3]), source=":".join(r.sources),
                   pieces_ok=r.effect == "allow")
        if r.effect == "allow":
            return "allow", ""
        return "deny", f"셸 규칙(deny {r.rule_ids[0]})이 이 명령을 막았다. 다른 방법을 쓰거나 사용자에게 물어라."
    skill_list = SK.discover(profile.workdir, pfl, builtin=SK.builtin_on(False)) if skill_on else []   # 실행 시작 때 한 번
    if skill_list:
        tool_defs.append(SK.DEFINITION)
    if not web:  # 검색 주소가 없으면 웹 도구(web_search·web_fetch)를 도구 목록에서 뺀다
        tool_defs = [d for d in tool_defs if d["function"]["name"] not in T.WEB_TOOLS]
    elif not web["url"]:   # 읽기만(STEWART_WEB_FETCH=1)
        tool_defs = [d for d in tool_defs if d["function"]["name"] != "web_search"]
    if not backend.vision:   # 이미지 보기는 이미지를 받는 백엔드에서만(평가·실사용 기본 도구 목록은 그대로)
        tool_defs = [d for d in tool_defs if d["function"]["name"] != "view_image"]
    mem = MEM.Memory(profile, rec.dir) if (not parent and MEM.enabled(profile)) else None   # 기억 M1(작업자는 없음)
    if mem is not None and profile.tools:
        tool_defs += MEM.tool_defs()
    all_tool_names = [d["function"]["name"] for d in tool_defs if d["function"]["name"] not in MEM.NAMES]   # 작업자에게 넘길 수 있는 것
    if profile.manager_tools is not None:   # 관리자 모드: 모델에 보이는 자기 도구만 남긴다(ledger 는 여기서 붙는다)
        if MG.NAME in profile.manager_tools:
            tool_defs.append(MG.DEFINITION)
        tool_defs = [d for d in tool_defs if d["function"]["name"] in profile.manager_tools]
    tool_names = [d["function"]["name"] for d in tool_defs]
    env = T.ToolEnv(workdir=profile.workdir, shell_timeout_s=profile.shell_timeout_s, web=web,
                     artifacts_dir=rec.dir, **T.secret_options(profile))
    env.vision = bool(backend.vision)
    if parent.get("env") is not None:   # 웹 검색 상한·가져오기 기록은 부모와 같은 수를 쓴다
        pe = parent["env"]
        env.search_log, env.search_counts, env.fetch_log, env.fetch_cache = pe.search_log, pe.search_counts, pe.fetch_log, pe.fetch_cache
    check = load_check(profile.action_check)
    max_steps = max_steps or profile.max_steps
    mt = {"max_tokens": profile.max_tokens} if profile.max_tokens else {}  # 프로필 단계당 생성 상한
    stop_cfg = HK.stop_command(profile) if not parent else {"command": None, "source": "worker", "ignored": None}
    hk = HK.Hooks(profile.hooks if not parent else {}, profile.workdir, rec.dir,
                  record=lambda r: rec.event("hook", **r))   # hooks 최소판(작업자는 없음)
    hook_stops = 0
    bd = parent.get("bounds") or BND.from_profile(profile)   # 작업 폴더 경계(작업자는 부모 것을 함께 쓴다)
    sc = SC.flags(profile) if not parent else {**{k: False for k in SC.ENVS}, "source": {}, "ignored": []}   # 작업자는 끔
    review = {"ran": False}
    ask_state = {"ran": False}
    todo_state = {"final_nudged": False, "at_step": None, "pending_at_nudge": None, "reminds": 0}
    last_todo_step = last_remind_step = 0
    review_paths: set[str] = set()      # 요구 대조 뒤에 도구로 바꾼 파일
    tks = TH.settings(profile)   # 생각 켬 차례의 상한·샘플링 손잡이(기본은 없음 = 백엔드 params 그대로)
    ctx = sync_context(backend)          # 실제 문맥 길이 = 서버 max_model_len(있으면) — 접기 판단·기록 모두 이 값
    if ctx["warn"]:
        print(f"stewart: 경고 — {ctx['warn']}", file=sys.stderr)

    system = profile.system.strip()
    mem_text, mem_log = memory_for(profile)
    if mem_text:
        system += "\n\n" + mem_text
    if skill_list:
        system += "\n\n" + SK.list_text(skill_list)
    mem_inject = []
    if mem is not None:   # L3: 대상 MEMORY.md(대상당 40줄·4 KB, 합 6 KB) + 무엇을 위한 기억인지
        mtext, mem_inject = mem.inject()
        system += "\n\n" + mtext
    agents = PC.discover_agents(profile.workdir, pfl) if DG.NAME in tool_names else []
    if agents:   # .stewart/agents(호환 .claude/agents): delegate 의 kind 로 고를 수 있는 정의(이름·설명만)
        system += "\n\n" + PC.agents_text(agents)
    env_info = envinfo.measure(profile.workdir)   # 실행 시작 때 한 번
    # 다이어트 안 a: 과제마다 다른 환경 블록(작업 폴더·날짜)은 시스템 지시문이 아니라 첫 사람 입력 앞에 둔다.
    # 시스템 지시문 + 도구 정의가 과제 사이에 같아져 prefix cache 가 맞는다.
    if profile.output_format == "json":
        system += ("\n\n최종 답은 도구 호출 없이 아래 JSON 스키마에 맞는 JSON 하나만 출력한다(설명 문장 없이).\n"
                   + json.dumps(profile.output_schema, ensure_ascii=False))
    if profile.language == "ko":
        system += "\n\n최종 보고(사람이 읽는 글)는 반드시 한국어로 쓴다. 코드·파일 이름·명령은 원문 그대로 둔다."
    if exec_mode == "plan":
        task = M.PLAN_NOTE + "\n\n" + task
    task = envinfo.user_note(env_info) + "\n\n" + task
    messages: list[dict] = (prepare(history, system) if history else [{"role": "system", "content": system}]) \
        + [{"role": "user", "content": task}]
    hctx = hk.session_start("run", task) if hk.has("SessionStart") else ""
    if hctx:   # SessionStart 표준 출력은 첫 사람 입력 앞에 맥락으로(시스템 지시는 그대로 — prefix cache)
        messages[-1]["content"] = HK.SESSION_CTX.format(ctx=hctx) + messages[-1]["content"]
    img_desc = []
    if images:   # run --image: 첫 사람 입력에 이미지 자리표(파일은 기록 폴더 images/, 보낼 때만 펼친다 — images.py)
        if not backend.vision:
            raise IMG.ImageError(f"이 백엔드({backend.name})는 이미지를 받지 않는다(vision = false)")
        parts = []
        for path in images:
            part, desc = IMG.from_file(path, rec.dir)
            parts.append(part)
            img_desc.append(desc)
        messages[-1]["content"] = IMG.content(messages[-1]["content"], parts)

    meta = {
        "engine": "stewart", "engine_version": __version__, "engine_git": _git_rev(),
        "profile": profile.name, "profile_path": str(profile.path), "backend": backend.public(),
        "workdir": str(profile.workdir), "tools": tool_names, "max_steps": max_steps,
        "max_consecutive_errors": profile.max_consecutive_errors, "output_format": profile.output_format,
        "max_tokens": profile.max_tokens or backend.params.get("max_tokens"),
        "action_check": profile.action_check or "stewart.check:allow_all", "exec_mode": exec_mode,
        "web_search": ({k: v for k, v in web.items() if k != "url"} | {"url_host": web["url"].split("/")[2] if "://" in web["url"] else web["url"]})
                      if web else ("꺼짐(검색 주소 없음)" if "web_search" in profile.tools else None),
        "memory_files": mem_log, "context": ctx, "think": tks,
        "memory": ({"dir": str(mem.dir), "subjects": [x["key"] for x in mem.subjects], "injected": mem_inject} if mem else None), "check_on_stop": stop_cfg, "env_info": env_info, "skills": SK.meta_log(skill_list),
        **({"agents": PC.agents_log(agents)} if agents else {}),
        "resumed_from": resumed_from, "resumed_messages": len(messages) - 2 if history else 0,
        **({"images": img_desc} if img_desc else {}),
        "started": datetime.now().isoformat(timespec="seconds"),
    }
    rec.event("run_start", meta=meta, messages=messages)
    if parent.get("cps") is not None:   # 작업자: 부모의 지금 체크포인트에 쓴다(/undo·rewind 가 덮는다)
        cps = parent["cps"]
    else:
        cps = Checkpoints(rec.dir, profile.workdir)   # 이 실행이 체크포인트 하나(stewart rewind <기록> 으로 되돌림)
        cps.begin(task.splitlines()[0] if task else "")
    rec.md_header(meta, task)

    st = dict(steps=0, tool_calls=0, tool_errors=0, parse_failures=0, format_failures=0, denied=0, repeat_nudges=0, language_retries=0,
              forced_final=False, completion_tokens=0, prompt_tokens_total=0, last_prompt_tokens=0, model_time_s=0.0,
              pred_n=0, pred_ms=0.0, prompt_n=0, prompt_ms=0.0)
    st.update(tool_error_kinds={}, broken_toolcalls={}, reasoning_tokens=None, call_results={}, same_call_same_result=0,
              same_call_diff_result=0)   # 결함 기록(도구 오류 유형·버린 툴콜·생각 토큰·같은 호출 결과)
    comps = CM.Components(rec)            # 구성 요소 사건 기록(기록만, STEWART_COMPONENT_EVENTS=0 이면 끔)
    prompt_mark = 0                      # 마지막 응답 때의 메시지 수(그 뒤 붙은 것만 어림해 프롬프트 크기를 잰다)
    cset = compact_settings(profile, 0.7, 8)       # 프로필 + STEWART_COMPACT_*
    cmode = compact_mode()                         # 평가 손잡이(판 66). 없으면 None = 예전 동작 그대로
    if cmode:
        cset = {**cset, "mode": cmode["mode"], "window": cmode["window"], "env": cset["env"] + cmode["env"],
                "ignored": cset["ignored"] + cmode["ignored"]}
        if cmode["ratio"] is not None:
            cset["ratio"] = cmode["ratio"]

    def new_comp():
        return Compactor((cmode["window"] or backend.max_context) if cmode else backend.max_context, cset["ratio"], cset["keep_recent"], fold=cset["fold"],
                         plan_count=lambda: len(env.plan.items) if env.plan is not None else 0,
                         reserve=(tks["max_tokens"] if tks["max_tokens"] and TH.is_on(backend, None)
                                  else reply_reserve(profile, backend)), tail_tokens=bool(cmode))
    comp = new_comp()
    cstate = {"clip_step": -1, "sum_step": -1, "folds": 0}
    if cset["env"] or cset["ignored"]:
        rec.event("compact_settings", **cset)
    consecutive = 0
    fail_count: dict[str, int] = {}  # 같은 (도구, 인자) 실패 횟수 — 성공 호출이 끼어도 누적
    repeat_stop = False
    rf = RF.settings(profile, interactive=False) if not parent else {**RF.settings(profile, False), "on": False}   # 작업자는 끔
    rstate = RF.State()
    fail_steps: list[list[dict]] = []   # 모두 실패한 단계들의 호출(성공 단계가 오면 비움) — 되짚기의 거부만 판정
    recent_fail: list[dict] = []        # 최근 실패 호출(되짚기에 붙일 사실)
    force_format = False
    guard = G.LoopGuard()   # 같은 호출·같은 결과 반복(성공 포함): 3·5 알림, 6 이면 도구 없이 최종 보고(force_final)
    force_final = False
    asked: str | None = None   # ask_user 로 낸 질문
    skills_loaded: list[str] = []
    stop_reason, final_output = "max_steps", None
    stop_checks = 0
    calls_log: list[dict] = []   # 실제로 한 일 기록(facts.json)용
    files_written: list[str] = []
    dg = {"profile": profile, "backend": backend, "rec": rec, "cps": cps, "env": env, "exec_mode": exec_mode,
          "tool_names": all_tool_names, "budget": DG.Budget(max_steps, profile.delegate_budget), "log": [], "ask": None,
          "on_call": None, "bounds": bd, "ask_outside": None, "projcfg": pfl, "ledger": MG.Ledger(rec.dir / "ledger.json", task.splitlines()[0] if task else "")
          if MG.NAME in tool_names else None}
    t_start = time.monotonic()

    def try_reflect(trigger: str, step: int) -> bool:
        """실패 문턱에서 끝내기 전 도구 없는 되짚기 한 번. 했으면 True(이어 간다), 건너뛰거나 실패하면 False(지금처럼 끝)."""
        nonlocal consecutive, repeat_stop
        calls = ([tc for s in fail_steps[-consecutive:] for tc in s] if trigger == "consecutive_errors"
                 else [tc for tc in recent_fail[-1:]])
        busy = "force_final" if force_final else "force_format" if force_format else None
        why = RF.skip_reason(rf, rstate.used, calls, busy)
        if why is not None:
            if why != "off":
                rstate.skipped.append({"trigger": trigger, "at_step": step, "why": why})
                rec.event("reflect_skip", step=step, trigger=trigger, why=why)
            return False
        rstate.used += 1
        facts_text = RF.facts(fail_count, recent_fail)
        RF.append_user(messages, RF.PROMPT.format(why=RF.WHY[trigger], facts=facts_text))
        rec.event("reflect_start", step=step, trigger=trigger, last_k=consecutive,
                  fails={RF._hash(k): v for k, v in fail_count.items()})
        cap = RF.cap_tokens(rf, tks["max_tokens"], TH.is_on(backend, None))
        r = RF.call(chat, backend, messages, mt, cap)
        u = r["usage"]
        st["model_time_s"] += r["elapsed_s"]
        st["completion_tokens"] += u.get("completion_tokens") or 0
        st["prompt_tokens_total"] += u.get("prompt_tokens") or 0
        st["last_prompt_tokens"] = u.get("prompt_tokens") or st["last_prompt_tokens"]
        rec.event("reflect", step=step, trigger=trigger, **r)
        comps.fire("reflect", step, trigger, "return_to_model", ok=r["ok"], last_k=consecutive)
        entry = {"trigger": trigger, "at_step": step, "ok": r["ok"], "chars": len(r["content"]),
                 "completion_tokens": u.get("completion_tokens")}
        rstate.log.append(entry)
        if not r["ok"]:
            messages.append({"role": "assistant", "content": RF.FAILED_NOTE.format(why=r["error"])})
            rec.md(f"> 되짚기 실패({r['error']}) → 원래 사유로 끝냄\n")
            return False
        messages.append({"role": "assistant", "content": r["content"]})
        messages.append({"role": "user", "content": RF.GO})
        entry["hot"] = RF.lower_hot(fail_count, REPEAT_NUDGE, REPEAT_STOP)
        consecutive, repeat_stop = 0, False
        fail_steps.clear()
        rec.md(f"> 실패가 이어져({trigger}) 도구 없이 되짚음\n\n{r['content']}\n")
        return True

    baseline: dict = {"ran": False}
    base_ids = None
    bcmd = stop_cfg["command"] or profile.verify_command
    if sc["baseline_diff"] and bcmd:   # 회귀 기준선: 시작 때 확인 명령 한 번, 실패하던 시험 이름을 적는다
        b0 = HK.run_stop(bcmd, profile.workdir, profile.shell_timeout_s * 5, profile.check_on_stop_skip_exit,
                         {"workdir": str(profile.workdir), "run_dir": str(rec.dir), "step": 0})
        base_ids = b0["failing"]
        baseline = {"ran": True, "command": bcmd, "exit": b0["exit"],
                    "base_failing": len(base_ids) if base_ids is not None else None,
                    "new_failing": None, "still_failing": None, "unblocked": 0}
        rec.event("baseline", exit=b0["exit"], failing=(base_ids or [])[:200], unknown=base_ids is None)

    for step in range(1, max_steps + 1):
        st["steps"] = step
        n_before = len(messages)
        req_summary = {"messages": len(messages), "chars": sum(len(json.dumps(m, ensure_ascii=False)) for m in messages),
                       "last_role": messages[-1]["role"]}
        mode = None
        try:
            overflow_done = length_done = False
            extra: dict = {}
            est = think_tokens = None
            u0 = {}
            while True:   # 문맥 넘침이면 한 번 접고 다시. 생각만 하다 잘린 빈 답이면 생각 끔 + 알림으로 한 번 다시
                try:
                    kw = {**mt, **extra}
                    est = (st["last_prompt_tokens"] + est_tokens(json.dumps(messages[prompt_mark:], ensure_ascii=False))
                           if st["last_prompt_tokens"] else est_tokens(json.dumps(messages, ensure_ascii=False)))
                    kw.update(TH.call_kwargs(backend, tks, kw.get("thinking"), est))   # 생각 켬 차례만
                    if force_format:
                        mode = "형식 강제(response_format, 도구 없음)"
                        rf = {"type": "json_schema", "json_schema": {"name": "output", "schema": profile.output_schema}}
                        resp, dt = chat(backend, messages, None, rf, **kw)
                    elif force_final:
                        mode = "마무리 강제(반복 감지, 도구 없음)"
                        resp, dt = chat(backend, messages, None, **kw)
                    else:
                        resp, dt = chat(backend, messages, tool_defs, **kw)
                except BackendError as e:
                    c = comp.maybe(messages, st["last_prompt_tokens"], step, force=True, keep=1) \
                        if not overflow_done and is_context_overflow(e) else None
                    if not c:
                        raise
                    overflow_done = True
                    st["overflow_retries"] = st.get("overflow_retries", 0) + 1
                    rec.event("compact", overflow_retry=True, **c)
                    comps.fire("overflow_retry", step, "context_overflow", "compact", prompt_tokens=est, status=e.status)
                    comps.fire("compact", step, "overflow", "compact", tokens_before=c.get("before_tokens"),
                               clipped=c.get("clipped"), chars_before=c.get("chars_before"), chars_after=c.get("chars_after"))
                    rec.md(f"> 문맥 넘침({e.status}) → 접고 같은 단계 다시: 메시지 {c['from']}~{c['to'] - 1} 중 {c['clipped']}개 줄임"
                           f"({c['chars_before']}자 → {c['chars_after']}자)\n")
                    continue
                if length_done or not empty_length(resp):
                    break
                length_done = True   # 첫 응답은 기록만 하고 버린다(본문·툴콜 없음)
                st["length_empty_retries"] = st.get("length_empty_retries", 0) + 1
                st["model_time_s"] += dt
                u0 = resp.get("usage") or {}
                st["completion_tokens"] += u0.get("completion_tokens") or 0
                st["prompt_tokens_total"] += u0.get("prompt_tokens") or 0
                st["length_stops"] = st.get("length_stops", 0) + 1
                m0 = ((resp.get("choices") or [{}])[0].get("message") or {})
                t0 = m0.get("reasoning_content") or m0.get("reasoning") or ""
                think_tokens = (u0.get("completion_tokens_details") or {}).get("reasoning_tokens")
                rec.event("length_retry", step=step, elapsed_s=dt, usage=u0, reasoning_content=t0, think_tokens=think_tokens,
                          length_stats={"think": repeat_stats(t0), "content": repeat_stats(m0.get("content") or "")})
                rec.md(f"> 생각만 하다 상한(max_tokens)에 닿아 빈 답 → 생각 끔 + 알림으로 같은 단계 다시\n")
                messages.append({"role": "user", "content": LENGTH_NOTE})
                extra = {"thinking": False}   # backend.chat 이 chat_template_kwargs.enable_thinking 만 바꿔 끼운다
            if length_done:
                comps.fire("length_retry", step, "length_empty", "retry", think_tokens=think_tokens,
                           completion_tokens=u0.get("completion_tokens"), recovered=not empty_length(resp))
        except BackendError as e:
            body = e.body or ""
            if "timed out" in str(e) or "timeout" in str(e).lower():
                st["backend_timeouts"] = st.get("backend_timeouts", 0) + 1
            st_rec = {"elapsed_s": 0.0, "note": f"백엔드 오류 {e} {body[:300]}"}
            from .backend import take_retries
            rts = take_retries()
            if rts:
                st["backend_retries"] = st.get("backend_retries", 0) + len(rts)
            rec.event("step", step=step, request=req_summary, error=str(e), status=e.status, body=body[:2000],
                      **({"retries": rts} if rts else {}),
                      **({"error_chain": e.chain} if getattr(e, "chain", None) else {}))
            if e.status and "parse" in body.lower():  # llama-server가 툴콜 파싱에 실패한 경우
                st["parse_failures"] += 1
                _broken(st, rec, step, "server_parse", body)
                comps.fire("broken_toolcall", step, "server_parse", "return_to_model", repaired=False, how="nudge")
                consecutive += 1
                messages.append({"role": "user", "content": NUDGE_TOOLCALL})
                st_rec["note"] += " → 툴콜 파싱 실패로 셈, 다시 요청"
                rec.md_step(step, st_rec)
                fail_steps.append([])
                if consecutive >= profile.max_consecutive_errors and not try_reflect("consecutive_errors", step):
                    stop_reason = "consecutive_errors"
                    comps.fire("failure_stop", step, "consecutive_errors", "stop", reason=stop_reason, last_k=consecutive)
                    break
                continue
            rec.md_step(step, st_rec)
            stop_reason = "context_overflow" if is_context_overflow(e) else "backend_error"
            if stop_reason == "context_overflow":
                comps.fire("overflow_retry", step, "context_overflow", "stop", prompt_tokens=est, status=e.status)
            break

        st["model_time_s"] += dt
        from .backend import take_retries
        rts = take_retries()
        if rts:   # 429·5xx·끊김에 다시 보내 받은 응답(SW-34 ①)
            st["backend_retries"] = st.get("backend_retries", 0) + len(rts)
            rec.event("backend_retry", step=step, attempts=rts)
            comps.fire("backend_retry", step, "backend_error", "retry", attempts=len(rts), last_status=rts[-1].get("status"))
        choice = (resp.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        usage = resp.get("usage") or {}
        tim = resp.get("timings") or {}
        st["completion_tokens"] += usage.get("completion_tokens") or 0
        st["prompt_tokens_total"] += usage.get("prompt_tokens") or 0
        if usage.get("anthropic"):   # Messages API 백엔드(판 69): 캐시 읽기·쓰기·비용을 실행 합계에
            ac = st.setdefault("anthropic_usage", {"input_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                                                   "output_tokens": 0, "cost_usd": 0.0})
            for k2 in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"):
                ac[k2] += int(usage["anthropic"].get(k2) or 0)
            ac["cost_usd"] = round(ac["cost_usd"] + float(usage.get("cost_usd") or 0), 6)
        elif usage.get("cost_usd"):   # 가격표를 단 OpenAI 호환 API(백엔드 price)
            st["cost_usd"] = round(st.get("cost_usd", 0.0) + float(usage["cost_usd"]), 6)
        st["last_prompt_tokens"] = usage.get("prompt_tokens") or st["last_prompt_tokens"]
        prompt_mark = len(messages)
        rt = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        if isinstance(rt, int):   # 서버가 생각 토큰 수를 주면(안 주면 summary 에 null)
            st["reasoning_tokens"] = (st["reasoning_tokens"] or 0) + rt
        done = comp.observe(usage.get("prompt_tokens"))
        if done:
            rec.event("compact_effect", step=step, before_tokens=done["before_tokens"], after_tokens=done["after_tokens"])
        if tim:
            st["pred_n"] += tim.get("predicted_n") or 0
            st["pred_ms"] += tim.get("predicted_ms") or 0
            st["prompt_n"] += tim.get("prompt_n") or 0
            st["prompt_ms"] += tim.get("prompt_ms") or 0
        content = msg.get("content") or ""
        raw_calls = msg.get("tool_calls") or []
        if choice.get("finish_reason") == "length":
            st["length_stops"] = st.get("length_stops", 0) + 1
        srec = {"elapsed_s": dt, "content": content, "timings": tim, "usage": usage,
                "finish_reason": choice.get("finish_reason"), "mode": mode, "tool_calls": []}
        think = msg.get("reasoning_content") or msg.get("reasoning")   # vLLM 새 판은 reasoning 칸(전에는 기록에 빠졌다)
        if think:
            srec["reasoning_content"] = think
        if choice.get("finish_reason") == "length":   # 잘린 단계의 생각 되풀이 지표(고리인지 긴 생각인지)
            srec["length_stats"] = {"think": repeat_stats(think or ""), "content": repeat_stats(content)}

        if force_final and raw_calls:   # 도구를 뺀 요청에 툴콜이 오면 실행하지 않는다(본문만 최종 보고로)
            srec["dropped_tool_calls"] = len(raw_calls)
            raw_calls = []
        if raw_calls:
            force_format = False
            guard_stop = False
            for rc in raw_calls:
                rc.setdefault("id", "call_" + uuid.uuid4().hex[:8])
                rc.setdefault("type", "function")
            messages.append({"role": "assistant", "content": content or None, "tool_calls": raw_calls, **({"anthropic_content": msg["anthropic_content"]} if msg.get("anthropic_content") else {})})   # 생각 블록 되보내기(판 69)
            step_ok = False
            ask_hold = False     # 되묻기 점검을 건 단계: 같은 응답의 뒤 호출도 돌리지 않는다
            for rc in raw_calls:
                fn = rc.get("function") or {}
                name, raw_args = fn.get("name", ""), fn.get("arguments") or "{}"
                tc = {"id": rc["id"], "name": name, "raw_arguments": raw_args if isinstance(raw_args, str) else json.dumps(raw_args, ensure_ascii=False)}
                st["tool_calls"] += 1
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                    if not isinstance(args, dict):
                        raise ValueError("인자가 객체가 아니다")
                except Exception as e:
                    tc["parse_error"] = str(e)
                    st["parse_failures"] += 1
                    _broken(st, rec, step, "args_json", tc["raw_arguments"], name=name)
                    comps.fire("broken_toolcall", step, "args_json", "return_to_model", repaired=False, how="error_result")
                    if choice.get("finish_reason") == "length":   # 생성 상한에서 끊긴 툴콜: 끊겼다·나눠 써라(cutoff.py)
                        tc["cut"] = True
                        st["cut_toolcalls"] = st.get("cut_toolcalls", 0) + 1
                        result, ok = CO.cut_note(profile, backend, name, tc["raw_arguments"]), False
                    else:
                        result, ok = f"인자 JSON 파싱 실패: {e}. 인자를 올바른 JSON 객체로 다시 호출하라.", False
                else:
                    if ask_hold:
                        result, ok = SC.ASK_HOLD, True
                        tc["ask_hold"] = True
                    elif (sc["ask_check"] and not ask_state["ran"] and clarify.NAME in tool_names and name in tool_names
                          and SC.changes(name, args, bd, getattr(env.shell, "cwd", None) if env.shell is not None else None)):
                        # 되묻기 판단 받침: 첫 쓰기·셸 바꿈 직전 한 번, 이 호출은 돌리지 않고 점검을 돌려준다
                        ask_state.update(ran=True, at_step=step, tool=name)
                        ask_hold = True
                        result, ok = SC.ASK_CHECK, True
                        tc["ask_check"] = True
                        rec.event("ask_check", step=step, tool=name)
                        comps.fire("ask_check", step, "first_change", "return_to_model", decision="hold", returned_to_model=True,
                                   tool=name)
                    elif name not in tool_names:
                        result, ok = T.unknown_tool(name, tool_names), False
                    elif name == clarify.NAME:
                        q = clarify.question_text(args)
                        if not q:
                            result, ok = "question 이 비었다. 무엇을 묻는지 적어 다시 호출하라.", False
                        elif cps.cur and cps.cur["files"]:   # run: 파일을 바꾼 뒤에는 멈추지 않는다(작은 모델 시험에서 본 것: 고친 뒤 pytest 없음으로 물음)
                            result, ok = clarify.AFTER_CHANGE, False
                            tc["ask_after_change"] = True
                        else:
                            result, ok = clarify.TOOL_RESULT, True
                            if asked is None:
                                asked = q
                    elif name in MEM.NAMES and mem is not None:   # 기억 도구(기억 폴더·기록 읽기, 메모 쓰기만)
                        ok, result = MEM.execute(mem, name, args)
                        rec.event("memory_tool", step=step, name=name, ok=ok)
                    elif name == SK.NAME:
                        ok, result = SK.load(skill_list, str(args.get("name") or ""), args.get("file"),
                                             env.secret_globs, env.secret_allow)
                        rec.event("skill_load", step=step, name=args.get("name"), file=args.get("file"), ok=ok)
                        if ok:
                            skills_loaded.append(str(args.get("name")) + (f":{args['file']}" if args.get("file") else ""))
                    elif name == DG.NAME:
                        ok, result = DG.run_child(dg, args)
                    elif name == MG.NAME and dg["ledger"] is not None:
                        ok, result = dg["ledger"].op(args)
                    else:
                        call = ToolCall(id=rc["id"], name=name, arguments=args, raw_arguments=tc["raw_arguments"])
                        if parent.get("on_call"):
                            parent["on_call"](name, args)
                        denied = check(call, CheckContext(profile=profile.name, workdir=profile.workdir, step=step))
                        dsrc = "check" if denied else None   # 거부한 곳(구성 요소 기록용)
                        if not denied and M.decide(exec_mode, name) == "deny":
                            denied, dsrc = M.PLAN_DENY, "mode"
                        cg = None
                        if not denied:
                            cg = CG.hit(profile.workdir, name, args)   # stewart 사용자 설정 폴더: 무인 실행은 거부, chat 작업자는 매번 묻기
                            if cg is not None:
                                denied = (parent["ask_cfg"](name, args, cg) if parent.get("ask_cfg")
                                          else CG.RUN_DENY.format(path=cg))
                                dsrc = "config" if denied else None
                        if not denied and cg is None:   # 작업 폴더 밖: 쓰기는 run 거부(chat 작업자는 물음), 읽기는 read_outside 대로
                            denied, out_kind = _outside_check(bd, env, name, args, parent)
                            if out_kind:
                                tc["outside"] = out_kind
                            dsrc = out_kind and ("outside" if out_kind == "write" else "outside_read") if denied else None
                        verdict = None
                        if not denied and cg is None and not tc.get("outside") and name == "run_shell":
                            # 셸 허용 규칙: deny 는 모드와 관계없이 건다. allow 는 묻는 자리에서만 뜻이 있다
                            asking = M.decide(exec_mode, name) == "ask" and bool(parent.get("ask"))
                            verdict, why_rule = (parent["rule"](name, args, not asking) if parent.get("rule")
                                                 else own_rule(args, not asking))
                            if verdict == "deny":
                                denied, dsrc = why_rule, "rule"
                        if not denied and cg is None and not tc.get("outside") and M.decide(exec_mode, name) == "ask" \
                                and parent.get("ask") and verdict != "allow":
                            denied = parent["ask"](name, args)
                            dsrc = "user" if denied else None
                        if denied:
                            tc["denied"] = denied
                            st["denied"] += 1
                            if dsrc != "outside_read":
                                comps.fire("write_denied", step, dsrc or "check", "block", reason=dsrc or "check", tool=name)
                            result, ok = f"행동 검사에서 거부됨: {denied}", False
                        else:
                            t1 = time.monotonic()
                            n_log = len(env.search_log)
                            why = hk.pre(name, args, step) if hk.has("PreToolUse") else None
                            if why is not None:   # PreToolUse exit 2 → 돌리지 않고 까닭을 도구 결과로
                                tc["hook_blocked"] = True
                                ok, result = False, HK.PRE_BLOCK.format(why=why)
                                comps.fire("hook_block", step, "pre_tool_use", "block", hook_event="pre_tool_use", exit=2,
                                           tool=name)
                            else:
                                cps.before_write(name, args)
                                ok, result = T.execute(env, name, args)
                                comps.drain(env.component_notes, step, tool=name)
                                cps.after_write(name, args, ok)
                                post = hk.post(name, args, ok, result, step) if hk.has("PostToolUse") else None
                                if post is not None:   # PostToolUse exit 2 → 까닭을 결과 뒤에 붙여 모델에
                                    tc["hook_note"] = True
                                    result += HK.POST_NOTE.format(why=post)
                                    comps.fire("hook_block", step, "post_tool_use", "return_to_model",
                                               hook_event="post_tool_use", exit=2, tool=name)
                            if ok and name in DG.WRITE_TOOLS and args.get("path") and str(args["path"]) not in files_written:
                                files_written.append(str(args["path"]))
                            if ok and name in DG.WRITE_TOOLS and args.get("path") and review["ran"]:
                                review_paths.add(str(args["path"]))
                            tc["elapsed_s"] = time.monotonic() - t1
                            for entry in env.search_log[n_log:]:
                                rec.event("web_search", step=step, **entry)
                tc.setdefault("elapsed_s", 0.0)
                _mark_call(st, tc, name, ok, result)   # 오류 유형·같은 호출 결과 해시(엔진 덧말을 붙이기 전 결과로)
                if not ok:
                    sig = name + "\0" + tc["raw_arguments"]
                    fail_count[sig] = fail_count.get(sig, 0) + 1
                    if fail_count[sig] >= REPEAT_NUDGE:
                        tc["repeat"] = fail_count[sig]
                        result += (f"\n\n[엔진] 똑같은 호출이 {fail_count[sig]}번 실패했다. 이미 반영됐거나 접근이 틀렸을 수 있다. "
                                   "같은 호출을 되풀이하지 말고 현재 상태를 확인(테스트 실행 등)하거나 다른 방법을 써라.")
                        st["repeat_nudges"] += 1
                        comps.fire("repeat_nudge", step, "same_call_failed", "nudge", sig=CM.sig_hash(sig),
                                   count=fail_count[sig], kind="same_fail")
                        if fail_count[sig] >= REPEAT_STOP:
                            repeat_stop = True
                same = guard.observe(name, tc["raw_arguments"], ok, result)
                g_note = guard.note(same)
                if g_note:
                    result += "\n\n" + g_note
                    tc["guard"] = same
                    comps.fire("repeat_nudge", step, "same_call_result", "nudge",
                               sig=CM.sig_hash(name + "\0" + tc["raw_arguments"]), count=same, kind="same_result")
                    guard_stop = guard_stop or same >= G.STOP
                tc["ok"], tc["result"] = ok, result
                if name == "run_shell":
                    st["shell_calls"] = st.get("shell_calls", 0) + 1
                if not ok:
                    st["tool_errors"] += 1
                    recent_fail.append(tc)
                    del recent_fail[:-RF.RECENT_KEEP]
                step_ok = step_ok or ok
                srec["tool_calls"].append(tc)
                calls_log.append(tc)
                messages.append({"role": "tool", "tool_call_id": rc["id"], "name": name, "content": result})
            consecutive = 0 if step_ok else consecutive + 1
            if step_ok:
                fail_steps.clear()
            else:
                fail_steps.append(srec["tool_calls"])
            if asked is not None:   # 질문을 최종 출력으로 두고 끝낸다(같은 응답의 다른 호출은 이미 실행됐다)
                messages.append({"role": "assistant", "content": asked})
                final_output, stop_reason = asked, "asked"
                srec["note"] = "ask_user → 질문을 최종 출력으로 두고 끝냄"
            notes = []
            r_note = guard.step_names({(rc.get("function") or {}).get("name", "") for rc in raw_calls})
            if r_note:
                notes.append(r_note)
                comps.fire("repeat_nudge", step, "same_tool_names", "nudge", count=guard.notes, kind="same_names")
            if max_steps - step == G.STEPS_LEFT_NUDGE:
                notes.append(f"[엔진] 남은 단계는 {G.STEPS_LEFT_NUDGE}번이다. 마무리하라: 바꾼 것을 시험하고 최종 보고를 써라. "
                             "끝내지 못하면 손댄 파일을 시험이 통과하던 상태로 되돌리거나, 바꾼 것과 남은 일을 보고에 적어라.")
                guard.notes += 1
                comps.fire("steps_left_nudge", step, "steps_left", "nudge", left=G.STEPS_LEFT_NUDGE)
            if any((rc.get("function") or {}).get("name") == "todo_write" for rc in raw_calls):
                last_todo_step = step
            pend = SC.pending_todos(env.todos) if sc["todo_check"] else []
            if (pend and step - last_todo_step >= profile.todo_remind_every
                    and step - last_remind_step >= profile.todo_remind_every):   # 할 일 목록 상기
                notes.append(SC.todo_remind(pend))
                last_remind_step = step
                todo_state["reminds"] += 1
                rec.event("todo_remind", step=step, pending=len(pend))
                comps.fire("todo_remind", step, "every_n_steps", "nudge", open_n=len(pend))
            if notes and asked is None:   # 아직 보내지 않은 마지막 도구 결과에만 덧붙인다(물어서 끝나면 붙이지 않는다)
                messages[-1]["content"] += "\n\n" + "\n".join(notes)
                srec["tool_calls"][-1]["result"] = messages[-1]["content"]
            if env.pending_images and asked is None:   # view_image: 다음 user 메시지에 이미지(도구 결과는 글)
                parts, env.pending_images = list(env.pending_images), []
                messages.append({"role": "user", "content": IMG.content(f"[엔진] view_image 로 연 이미지 {len(parts)}장", parts)})
                srec["guard"] = notes
            if guard_stop and not repeat_stop and asked is None:
                force_final = st["forced_final"] = True
                if profile.output_format == "json":
                    force_format = True
                messages.append({"role": "user", "content": G.FINAL_PROMPT.format(n=G.STOP)})
                comps.fire("repeat_nudge", step, "same_call_result", "block", count=G.STOP, kind="force_final")
                srec["note"] = f"같은 호출·같은 결과 {G.STOP}번 → 도구를 빼고 최종 보고를 받는다"
        elif tool_names and not force_format and not force_final and _looks_like_toolcall(content, tool_names):
            st["parse_failures"] += 1
            _broken(st, rec, step, "in_content", content)
            comps.fire("broken_toolcall", step, "in_content", "return_to_model", repaired=False, how="nudge")
            consecutive += 1
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": NUDGE_TOOLCALL})
            srec["note"] = "툴콜 파싱 실패(본문에 도구 호출처럼 보이는 글) → 다시 요청"
        elif empty_length(resp):
            # 생각 끔으로 다시 보냈는데도 빈 잘림 — 빈 글을 최종 답으로 받지 않는다
            stop_reason = "length_empty"
            srec["note"] = "생각 끔으로 다시 보냈어도 상한에 닿아 빈 답 → length_empty 로 끝"
        else:
            # 최종 답
            messages.append({"role": "assistant", "content": content, **({"anthropic_content": msg["anthropic_content"]} if msg.get("anthropic_content") else {})})
            lang_bad = _language_problem(profile, content)
            chk = None
            # (hooks Stop 첫 꼴): 이번 실행이 파일을 바꿨으면 끝내기 전 확인 명령. 실패면 출력을 돌려주고 다시(상한까지)
            if (stop_cfg["command"] and not force_final and stop_checks < profile.check_on_stop_max
                    and ((cps.cur and cps.cur["files"]) or files_written)):
                chk = HK.run_stop(stop_cfg["command"], profile.workdir, profile.shell_timeout_s * 5,
                                  profile.check_on_stop_skip_exit,
                                  {"workdir": str(profile.workdir), "run_dir": str(rec.dir), "step": step,
                                   "final_output": content[:2000]})
                stop_checks += 1
                rec.event("check_on_stop", step=step, source=stop_cfg["source"], **chk)
            split = SC.baseline_split(chk, base_ids) if (chk is not None and baseline["ran"]) else None
            if split is not None:
                baseline.update(new_failing=len(split["new"]), still_failing=len(split["still"]))
                rec.event("baseline_compare", step=step, new=split["new"][:100], still=split["still"][:100],
                          fixed=split["fixed"][:100])
                if not chk["passed"] and not chk["skipped"] and not split["new"] and split["still"]:
                    chk = {**chk, "passed": True}   # 처음부터 실패하던 시험만 남았다 → 막지 않는다
                    baseline["unblocked"] += 1
                    srec["note"] = "끝내기 전 확인: 처음부터 실패하던 시험만 남음 → 막지 않음"
            chk_failed = chk is not None and not chk["passed"] and not chk["skipped"]
            if chk is not None:
                comps.fire("check_on_stop", step, "final_answer", "return_to_model" if chk_failed else "stop",
                           decision="skip" if chk["skipped"] else "pass" if chk["passed"] else "fail",
                           returned_to_model=chk_failed, exit=chk.get("exit"))
            if split is not None:
                unblocked = not chk_failed and not split["new"] and bool(split["still"])
                comps.fire("baseline_compare", step, "check_failed", "return_to_model" if chk_failed else "stop",
                           decision="unblock" if unblocked else "keep", returned_to_model=chk_failed,
                           new=len(split["new"]), still=len(split["still"]), fixed=len(split["fixed"]))
            pend = SC.pending_todos(env.todos) if sc["todo_check"] else []
            todo_due = bool(pend) and not todo_state["final_nudged"] and not force_final and not chk_failed
            review_due = (sc["review_on_stop"] and not review["ran"] and not force_final and not chk_failed and not todo_due
                          and bool((cps.cur and cps.cur["files"]) or files_written))
            if sc["todo_check"] and not todo_due and not chk_failed and not force_final:
                comps.fire("todo_check", step, "final_answer", "stop", decision="already_nudged" if pend else "pass",
                           open_n=len(pend))
            hstop = None
            if not chk_failed and not review_due and not todo_due and hk.has("Stop") \
                    and not force_final and hook_stops < profile.check_on_stop_max:
                hstop = hk.stop(content, step)   # [hooks] Stop: check_on_stop 이 통과(또는 없음)한 뒤, exit 2 = 끝내지 않음
                if hstop is not None:
                    hook_stops += 1
            if chk is not None and not chk["passed"] and not chk["skipped"]:
                st["stop_check_fails"] = st.get("stop_check_fails", 0) + 1
                messages.append({"role": "user", "content": HK.feedback(chk) + (SC.baseline_note(split) if split else "")})
                srec["note"] = f"끝내기 전 확인 실패(exit {chk['exit']}) → 출력을 돌려주고 다시"
            elif todo_due:
                # 남은 할 일이 있는데 최종 답: 한 번만 돌려준다
                todo_state.update(final_nudged=True, at_step=step, pending_at_nudge=len(pend))
                messages.append({"role": "user", "content": SC.todo_final(pend)})
                rec.event("todo_check", step=step, pending=len(pend))
                comps.fire("todo_check", step, "final_answer", "return_to_model", decision="open", open_n=len(pend))
                srec["note"] = f"할 일 {len(pend)}개가 남은 채 최종 답 → 한 번 되돌려 보냄"
            elif review_due:
                # 끝내기 전 요구 대조: 확인이 통과(또는 없음)한 뒤 한 번만
                review = {"ran": True, "at_step": step}
                messages.append({"role": "user", "content": SC.REVIEW})
                rec.event("review_on_stop", step=step)
                comps.fire("review_on_stop", step, "final_answer", "return_to_model", decision="review", returned_to_model=True)
                srec["note"] = "끝내기 전 요구 대조 → 한 번 되돌려 보냄"
            elif hstop is not None:
                messages.append({"role": "user", "content": HK.STOP_BLOCK.format(why=hstop)})
                comps.fire("hook_block", step, "stop", "return_to_model", hook_event="stop", exit=2)
                srec["note"] = "hook Stop 이 막음 → 까닭을 돌려주고 다시"
            elif lang_bad and st["language_retries"] < LANGUAGE_RETRIES:
                st["language_retries"] += 1
                messages.append({"role": "user", "content": lang_bad})
                comps.fire("lang_retry", step, "not_korean", "retry",
                           detected="fields" if profile.language_fields and profile.output_format == "json" else "content",
                           hangul=round(hangul_ratio(content), 3), retry_n=st["language_retries"])
                srec["note"] = "보고 언어가 한국어가 아님 → 한국어로 다시 요청"
            elif profile.output_format == "json":
                try:
                    obj = _extract_json(content)
                    errs = validate(obj, profile.output_schema)
                except ValueError as e:
                    obj, errs = None, [str(e)]
                if errs:
                    st["format_failures"] += 1
                    consecutive += 1
                    force_format = True
                    comps.fire("format_force", step, "schema_mismatch", "retry", attempt=st["format_failures"],
                               errors=len(errs))
                    messages.append({"role": "user", "content":
                                     "최종 답이 스키마에 맞지 않는다:\n- " + "\n- ".join(errs[:15])
                                     + "\n스키마에 맞는 JSON 하나만 다시 출력하라."})
                    srec["note"] = f"출력 형식 실패 {len(errs)}건 → 형식 강제로 다시 요청"
                else:
                    final_output = json.dumps(obj, ensure_ascii=False, indent=2)
                    stop_reason = "completed"
            else:
                final_output = content
                stop_reason = "completed"

        rec.event("step", step=step, request=req_summary, new_messages=messages[n_before:], response=srec,
                  consecutive_errors=consecutive)
        rec.md_step(step, srec)
        if stop_reason in ("completed", "asked", "length_empty"):
            break
        trig = ("consecutive_errors" if consecutive >= profile.max_consecutive_errors
                else "repeated_failure" if repeat_stop else None)
        if trig and try_reflect(trig, step):   # 끝내기 전 도구 없는 되짚기 한 번(reflect.py)
            trig = None
        if trig == "consecutive_errors":
            stop_reason = "consecutive_errors"
            comps.fire("failure_stop", step, "consecutive_errors", "stop", reason=stop_reason, last_k=consecutive)
            break
        if trig == "repeated_failure":
            stop_reason = "repeated_failure"
            comps.fire("failure_stop", step, "same_call_failed", "stop", reason=stop_reason,
                       last_k=max(fail_count.values(), default=0))
            break
        mode = cmode["mode"] if cmode else None
        win = comp.full_context
        last = st["last_prompt_tokens"]
        if mode == "mask-roll":   # 매 단계 최근 10단계 밖 관찰 가리기(논문 꼴 — prefix 캐시는 매번 깨진다)
            n_masked = mask_roll(messages, 10)
            if n_masked:
                rec.event("compact", mode=mode, step=step, masked=n_masked, before_tokens=last)
        c = None if mode in ("off", "sum", "mask-roll") else comp.maybe(messages, last, step)
        if c:
            cstate["clip_step"] = step
        sum_at = 0.7 if mode == "mask+sum" else 0.5 if mode == "sum" else None
        if (sum_at and not c and last >= sum_at * win and step > max(cstate["clip_step"] if mode == "mask+sum" else -1,
                                                                    cstate["sum_step"])):
            r = _fold_summary(messages, backend, rec, cstate, step, mode, last)
            if r:
                comp = new_comp()
                prompt_mark, st["last_prompt_tokens"] = 0, 0
                cstate["sum_step"] = step
        if c:
            c = {**c, **({"mode": mode} if mode else {})}
            rec.event("compact", **c)
            comps.fire("compact", step, "threshold", "compact", tokens_before=c.get("before_tokens"),
                       clipped=c.get("clipped"), chars_before=c.get("chars_before"), chars_after=c.get("chars_after"))
            rec.md(f"> 문맥 압축: 메시지 {c['from']}~{c['to'] - 1} 중 {c['clipped']}개 줄임"
                   f"({c['chars_before']}자 → {c['chars_after']}자, 프롬프트 {c['before_tokens']} tok 에서)\n")

    comps.fire("stop_decision", st["steps"], "run_end", "stop", final_reason=stop_reason, last_component=comps.last,
               steps=st["steps"])
    shell_end = T.close_env(env)          # 남은 백그라운드 작업·셸 정리(되돌리기·판정 명령 전에)
    if shell_end:
        rec.event("shell_cleanup", **shell_end)
    plan_end = None
    if T.ORGANIZE_TOOLS & {d["function"]["name"] for d in tool_defs}:   # 정리 실행: 어떤 종료든 plan.json 을 남긴다
        try:
            plan_end = T.finalize_plan(env, stop_reason)
        except (OSError, ValueError) as e:
            plan_end = {"error": str(e)[:300]}
        if plan_end:
            rec.event("plan_final", **plan_end)

    def run_verify(stage: str) -> dict:
        from .osdep import ShellNotFound
        try:
            p = run_command(profile.verify_command, cwd=profile.workdir, capture_output=True,
                            timeout=profile.shell_timeout_s * 5, env=tool_env())   # 모델의 셸과 같은 환경(Windows 는 같은 셸)
        except ShellNotFound as e:
            v = {"command": profile.verify_command, "exit": "no_shell", "passed": False, "output": f"판정 명령을 돌릴 셸이 없다: {e}"}
            rec.event("verify", stage=stage, **v)
            return v
        v = {"command": profile.verify_command, "exit": p.returncode, "passed": p.returncode == 0,
             "output": (p.stdout + p.stderr)[-6000:]}
        rec.event("verify", stage=stage, **v)
        return v

    rolled, not_rolled, verify = None, None, None
    if (profile.rollback_unfinished and not parent and stop_reason not in ("completed", "asked")
            and cps.cur and cps.cur["files"]):   # 끝내지 못한 run 은 바꾼 파일을 되돌린다(셸이 바꾼 것은 못 되돌림)
        # 기반 장애(백엔드 연결 실패·시간 초과·HTTP 오류)로 끝났거나, 판정 명령이 이미 통과하면 되돌리지 않는다
        if stop_reason == "backend_error":
            not_rolled = "backend_error(백엔드 연결 실패·시간 초과 등 기반 장애)"
        elif profile.verify_command:
            pre = run_verify("before_rollback")
            if pre["passed"]:
                not_rolled, verify = "verify_command 통과(exit 0)", pre
        if not_rolled:
            rec.event("rollback_skipped", stop_reason=stop_reason, reason=not_rolled)
        else:
            try:
                r = cps.rewind(cps.cur["id"])
                rolled = {"restored": len(r["restored"]), "moved_aside": len(r["moved_aside"]), "skipped": r["skipped"],
                          "outside_changes": r["outside_changes"], "aside_dir": r["aside_dir"], "shell_calls": st.get("shell_calls", 0)}
                rec.event("rollback", stop_reason=stop_reason, **rolled)
                comps.fire("rollback", st["steps"], "unfinished", "rollback", reason=stop_reason,
                           restored=rolled["restored"], moved_aside=rolled["moved_aside"])
                note = (f"[엔진] 끝내지 못해({stop_reason}) 바꾼 파일 {rolled['restored'] + rolled['moved_aside']}개를 실행 전으로 되돌렸다"
                        f"(되돌리기 직전 모습: {r['aside_dir']}).")
                if rolled["shell_calls"]:
                    note += f" 셸 명령 {rolled['shell_calls']}번이 바꾼 파일은 되돌리지 못했을 수 있다."
                final_output = (final_output + "\n\n" + note) if final_output else note
            except (OSError, ValueError) as e:
                rolled = {"error": str(e)}
                rec.event("rollback", stop_reason=stop_reason, error=str(e))
    if profile.verify_command and verify is None:
        verify = run_verify("final")   # 되돌렸으면 되돌린 뒤 모습으로 다시 잰다

    summary = {
        **{k: meta[k] for k in ("engine_version", "engine_git", "profile", "output_format", "started")},
        "backend": backend.name, "model": backend.model, "precision": backend.precision,
        "stop_reason": stop_reason, "steps": st["steps"], "elapsed_s": time.monotonic() - t_start,
        "model_time_s": st["model_time_s"], "tool_calls": st["tool_calls"], "tool_errors": st["tool_errors"],
        "parse_failures": st["parse_failures"], "format_failures": st["format_failures"], "denied": st["denied"],
        "repeat_nudges": st["repeat_nudges"], "language_retries": st["language_retries"],
        "guard_notes": guard.notes, "forced_final": st["forced_final"],
        "asked": asked,   # ask_user 질문(없으면 None). 판정은 평가기가 최종 출력 글·파일 변경으로 한다
        "asked_text": bool(stop_reason == "completed" and not (cps.cur and cps.cur["files"]) and clarify.looks_like_question(final_output or "")),
        "skills_loaded": skills_loaded,
        "length_stops": st.get("length_stops", 0), "backend_timeouts": st.get("backend_timeouts", 0),
        **({"cut_toolcalls": st["cut_toolcalls"]} if st.get("cut_toolcalls") else {}),
        "tool_error_kinds": st["tool_error_kinds"], "broken_toolcalls": st["broken_toolcalls"],
        **({"components": comps.summary()} if comps.on else {}),
        "reasoning_tokens": st["reasoning_tokens"],
        "same_call_same_result": st["same_call_same_result"], "same_call_diff_result": st["same_call_diff_result"],
        **({"think": {**tks, "dropped_params": list(backend.dropped_params)}}
           if tks["max_tokens"] or tks["sampling"] or tks["ignored"] else {}),
        **({"length_empty_retries": st["length_empty_retries"]} if st.get("length_empty_retries") else {}),
        **({"review": {**review, "steps_after": (st["steps"] - review["at_step"]) if review.get("ran") else None,
                       "files_changed_after": len(review_paths) if review.get("ran") else None}}
           if sc["review_on_stop"] else {}),
        **({"baseline": baseline} if sc["baseline_diff"] else {}),
        **({"todo_check": {**todo_state, "used_todos": bool(env.todos),
                           "pending_at_end": len(SC.pending_todos(env.todos))}} if sc["todo_check"] else {}),
        **({"ask_check": {**ask_state, "asked": bool(ask_state["ran"] and stop_reason == "asked"),
                          "proceeded": bool(ask_state["ran"] and stop_reason != "asked"
                                            and any(c.get("name") == ask_state.get("tool") and not c.get("ask_check")
                                                    and not c.get("ask_hold") and c.get("ok") for c in calls_log))}}
           if sc["ask_check"] else {}),
        **({"selfcheck": {k: sc[k] for k in SC.ENVS} | {"source": sc["source"], "ignored": sc["ignored"]}}
           if any(sc[k] for k in SC.ENVS) or sc["ignored"] else {}),
        **({"hooks": {"runs": hk.runs, "stop_blocks": hook_stops, "off": hk.off}} if hk.table or hk.off else {}),
        **({"check_on_stop": {"command": stop_cfg["command"], "source": stop_cfg["source"], "runs": stop_checks,
                              "fails": st.get("stop_check_fails", 0)}}
           if stop_cfg["command"] or stop_cfg["ignored"] else {}),                # (켜졌거나 env 값이 잘못됐을 때만)
        "compactions": len(comp.log), "compact_log": comp.log,
        **({"overflow_retries": st["overflow_retries"]} if st.get("overflow_retries") else {}),   # 넘침 뒤 접고 다시
        **({"context": ctx} if ctx["warn"] else {}),                            # 서버 문맥이 표와 다를 때만
        **({"compact_settings": cset} if cset["env"] else {}),                  # 환경 변수로 바꾼 실행만(평가 기록에 남게)
        **({"compact_folded": sum(c.get("folded", 0) for c in comp.log),       # 접기 실행만
            "compact_saved_tokens": sum((c["before_tokens"] - c["after_tokens"]) for c in comp.log if c.get("after_tokens"))}
           if comp.fold else {}),
        "checkpoint_files": len(cps.cur["files"]) if cps.cur else 0,
        **({"reflect": rstate.summary(stop_reason, st["steps"]) | {"source": rf["source"]}}
           if (rstate.log or rstate.skipped) else {}),   # 되짚기를 했거나 건너뛴 실행만(평가 summary 키는 그대로)
        "files_written": files_written, "delegations": dg["log"], "rolled_back": rolled, "not_rolled_back_reason": not_rolled,
        "ledger": dg["ledger"].brief() if dg["ledger"] is not None else None,
        "web_searches": env.search_log,
        **({"plan": plan_end} if plan_end else {}),   # 정리 실행만(그 밖 프로필의 summary 키는 그대로)
        **({"web_quota": env.quota} if env.quota is not None else {}),   # brave 이달 셈을 읽은 실행만
        "todos": env.todos,
        "web_fetches": env.fetch_log,
        "shell": shell_end,
        "web_result_urls": sorted({u for e in env.search_log for u in e["urls"]}),  # 나중 web_fetch 허용 목록
        "completion_tokens": st["completion_tokens"], "prompt_tokens_total": st["prompt_tokens_total"],
        **({"backend_retries": st["backend_retries"]} if st.get("backend_retries") else {}),
        **({"anthropic_usage": st["anthropic_usage"], "cost_usd": st["anthropic_usage"]["cost_usd"]} if st.get("anthropic_usage") else {}),
        **({"cost_usd": st["cost_usd"]} if st.get("cost_usd") and not st.get("anthropic_usage") else {}),
        "last_prompt_tokens": st["last_prompt_tokens"],
        "gen_tok_per_s": (st["pred_n"] / (st["pred_ms"] / 1000)) if st["pred_ms"] else None,
        "prompt_tok_per_s": (st["prompt_n"] / (st["prompt_ms"] / 1000)) if st["prompt_ms"] else None,
        # 서버 timings가 없는 백엔드(vLLM 등)용: 생성 토큰 합 / 모델 호출 시간 합(프롬프트 처리 포함, 낮게 나온다)
        "e2e_tok_per_s": (st["completion_tokens"] / st["model_time_s"]) if st["model_time_s"] else None,
        "verify": verify, "success": (verify["passed"] if verify else stop_reason == "completed"),
        "run_dir": str(rec.dir),
    }
    fx = None
    try:   # 실제로 한 일: output.md 에는 붙이지 않고 facts.json·facts.md 에 따로 남긴다
        fx = FX.collect(stop_reason, calls_log, (cps.cur or {}).get("files", []), profile.workdir, summary.get("delegations"),
                        summary.get("rolled_back"), not_rolled)
        summary["facts"] = str(FX.write(rec.dir, fx))
    except OSError as e:
        summary["facts"] = f"(쓰지 못함: {e})"
    if mem is not None:   # L1 엔진 자동 메모 + L0 색인 한 줄
        try:
            n_auto = mem.auto_notes(summary, fx)
            mem.index_run(task0, summary, [x["path"] for x in ((fx or {}).get("files") or [])])
            summary["memory"] = {"notes_model": mem.notes_written, "notes_engine": n_auto, "injected": mem_inject}
        except OSError as e:
            summary["memory"] = {"error": str(e)[:300]}
    if rstate.log:
        rec.event("reflect_end", **summary["reflect"])
    rec.event("run_end", summary=summary)
    save_messages(rec.dir, messages)
    rec.close(summary, final_output)
    return summary
