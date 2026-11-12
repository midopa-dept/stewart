"""문맥 값 재기(판 100): 프로필마다 모델이 첫 요청에서 받는 고정 몫 — 시스템 지시(기억·스킬 색인 주입 포함) + 도구 정의 + 첫 사람 말 앞
환경 블록 — 을 토큰 어림(compact.est_tokens)으로 센다. 모델을 부르지 않는다. `stewart profiles --cost [이름…] [--hub] [--clean]`.

--hub 는 허브 메인 꼴(역할 지시·현황판 지시 + 세션 도구), --clean 은 기억·사용자 스킬을 끈 판(설치마다 달라지는 몫을 뺀 뼈대 비교용).
"""

from __future__ import annotations

import dataclasses
import io
import json
import os
import tempfile
from pathlib import Path
from unittest import mock


def measure(profile_name: str, hub: bool = False, clean: bool = False, slim: bool = False) -> dict:
    from . import chat as C
    from .compact import est_tokens
    from .config import Backend, load_profile
    from .record import Recorder
    env = {"STEWART_MEMORY": "0", "STEWART_SKILLS_DIR": tempfile.mkdtemp(prefix="cost-skills-")} if clean else {}
    tmp = Path(tempfile.mkdtemp(prefix="stewart-cost-"))
    with mock.patch.dict(os.environ, env):
        p = load_profile(profile_name)
        p = dataclasses.replace(p, workdir=tmp)
        extra = None
        if hub and slim:   # 판 113: 얇은 메인 — 짧은 지시 하나 + 핵심 도구(나머지는 칩이라 안 보냄)
            from . import hubtools as HT
            from . import slim as SL
            p = SL.apply(p, {})
            extra = (HT.DEFINITIONS, None)
        elif slim:   # 판 114: chat 길의 얇은 꼴(stewart chat --tier <slim 등급>) — 허브 도구 없이
            from . import slim as SL
            p = SL.apply(p, {})
        elif hub:
            from . import hubtools as HT
            from .board import NOTE as BOARD_NOTE
            from .hubmain import HUB_MAIN_NOTE
            p = dataclasses.replace(p, system=(p.system or "").rstrip() + "\n\n" + HUB_MAIN_NOTE + "\n" + BOARD_NOTE)
            extra = (HT.DEFINITIONS, None)
        s = C.ChatSession(p, Backend("cost", "http://127.0.0.1:9/v1", "m", "-", 100000, vision=True), Recorder(tmp / "runs", "cost"),
                          C.UI(out=io.StringIO(), color=False), stream=False, mode="auto", extra_tools=extra)
        try:
            sys_txt = str(s.messages[0].get("content") or "") if s.messages and s.messages[0].get("role") == "system" else ""
            per = sorted(((d["function"]["name"], est_tokens(json.dumps(d, ensure_ascii=False))) for d in s.tool_defs), key=lambda x: -x[1])
            envb = est_tokens(getattr(s, "_env_note", "") or "")
            base = est_tokens(p.system or "")
            out = {"profile": profile_name, "hub": hub, "clean": clean, "slim": slim, "system": est_tokens(sys_txt), "system_profile": base,
                   "system_injected": max(0, est_tokens(sys_txt) - base), "tools": sum(t for _, t in per), "tool_count": len(per),
                   "env_block": envb, "top_tools": per[:6]}
            out["total"] = out["system"] + out["tools"] + out["env_block"]
            return out
        finally:
            try:
                C.T.close_env(s.env)
                s.rec._ev.close()
            except Exception:   # noqa: BLE001
                pass


def table(rows: list[dict]) -> str:
    out = ["| 프로필 | 꼴 | 시스템(그중 주입) | 도구 정의(개수) | 환경 블록 | 합 | 무거운 도구 |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        kind = ("얇은 허브 메인" if r.get("slim") and r["hub"] else "얇은 chat" if r.get("slim") else "허브 메인" if r["hub"] else "chat·run") + (" · 뼈대" if r["clean"] else "")
        out.append(f"| {r['profile']} | {kind} | {r['system']}({r['system_injected']}) | {r['tools']}({r['tool_count']}) | {r['env_block']} | "
                   f"**{r['total']}** | " + ", ".join(f"{n} {t}" for n, t in r["top_tools"][:4]) + " |")
    out.append("토큰은 어림(compact.est_tokens — 한글 1.2·숫자 1·영문 0.3·기호 0.5). 실제 토크나이저와 ±15% 쯤 다를 수 있다.")
    return "\n".join(out)
