"""판 121: 실사용 장면 묶음 — 사용자가 실사용에서 겪은 장면 하나 = 대본 하나(사람이 한 말 그대로) + 기대하는 첫 행동 + 기계 판정.

- 장면 파일(TOML, [[scene]]): id·said(사람이 한 말)·source(어디서 전해 들었나)·profile·tools_off(칩으로 꺼 둔 도구)·tools_on(칩으로 켜 둔 도구)
  · set: "real"(실사용에서 겪은 말, 기본) 또는 "dev"(개발 장면 — 규칙을 고친 뒤 같은 장면으로 나아졌다고 말하지 않는다)
  · requires: 이 장면에 있어야 하는 도구 이름들 — 그 판본·설정에 없으면 실측은 건너뛴다(맞음·틀림에 넣지 않는다)
  · expect: first_any(첫 걸음에 하나라도 불러야 할 도구)·first_none(첫 걸음에 부르면 안 되는 도구)
            ·say_any(도구 없이 답했으면 답에 있어야 할 말, 정규식)·no_host_gui(셸로 이 컴퓨터 화면을 여는 명령을 내면 실패)
  · executor(가짜 모델로 볼 실행기 장치): note(첫 사람 말 뒤에 붙어야 할 엔진 글 조각)·fake_call(모델이 이 호출을 했다고 치면)·blocked(그 결과에 있어야 할 글)
- 두 길: 가짜 모델 대본으로 실행기 장치를 시험(tests/test_u98.py — CI), 진짜 모델로 N 번 중 몇 번 기대대로(tools/scenes/measure.py — 실사용에 닿는 판을 바꿀 때).
- 규칙: 사용자가 실사용에서 겪은 문제는 고치기 전에 이 묶음에 장면으로 먼저 넣는다.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

DIR = Path(__file__).resolve().parents[2] / "tests" / "scenes"


def profile_raw(scene: dict, raw: dict) -> dict:
    """장면의 칩 상태를 프로필 raw 에(꺼 둔 것은 toggle_tools 에, 켜 둔 것은 toggle_on 에)."""
    raw = dict(raw or {})
    if scene.get("tools_off"):
        raw["toggle_tools"] = list(raw.get("toggle_tools") or []) + [t for t in scene["tools_off"] if t not in (raw.get("toggle_tools") or [])]
    if scene.get("tools_on"):
        raw["toggle_on"] = list(raw.get("toggle_on") or []) + list(scene["tools_on"])
    return raw


def load(paths=None) -> list[dict]:
    out = []
    for p in paths or sorted(DIR.glob("*.toml")):
        for s in tomllib.loads(Path(p).read_text(encoding="utf-8")).get("scene") or []:
            s.setdefault("expect", {})
            s.setdefault("executor", {})
            s.setdefault("tools_off", [])
            s.setdefault("tools_on", [])
            s.setdefault("profile", "chat")
            s.setdefault("set", "real")
            s["_file"] = Path(p).name
            out.append(s)
    return out


def first_step(messages: list[dict]) -> tuple[list[dict], str]:
    """첫 사람 말 뒤 첫 모델 답: (도구 호출 [{name, args}], 글)."""
    seen_user = False
    for m in messages:
        if m.get("role") == "user":
            seen_user = True
            continue
        if seen_user and m.get("role") == "assistant":
            calls = []
            for tc in m.get("tool_calls") or []:
                f = tc.get("function") or {}
                try:
                    args = json.loads(f.get("arguments") or "{}")
                except ValueError:
                    args = {}
                calls.append({"name": f.get("name"), "args": args})
            return calls, str(m.get("content") or "")
    return [], ""


def final_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"].strip():
            return m["content"]
    return ""


def judge(scene: dict, messages: list[dict]) -> tuple[bool, str]:
    """(기대대로인가, 까닭). 첫 걸음 도구와 답 글로만 기계 판정한다."""
    from . import hostgui as HG
    e = scene.get("expect") or {}
    calls, text = first_step(messages)
    names = [c["name"] for c in calls]
    if e.get("no_host_gui"):
        for m in messages:
            for tc in m.get("tool_calls") or []:
                f = tc.get("function") or {}
                if f.get("name") == "run_shell":
                    try:
                        cmd = json.loads(f.get("arguments") or "{}").get("command", "")
                    except ValueError:
                        cmd = ""
                    if HG.hit(cmd):
                        return False, f"셸로 이 컴퓨터 화면을 열려 함: {cmd[:80]}"
    bad = [n for n in names if n in (e.get("first_none") or [])]
    if bad:
        return False, f"첫 걸음에 부르면 안 되는 도구: {', '.join(bad)}"
    if e.get("first_any"):
        if any(n in e["first_any"] for n in names):
            return True, f"첫 걸음 {', '.join(names)}"
        if not names and e.get("say_any"):
            pass   # 도구 없이 답한 경우는 아래 말로 본다
        else:
            return False, f"첫 걸음이 {', '.join(names) or '도구 없음'} — 기대 {', '.join(e['first_any'])}"
    if e.get("say_any"):
        answer = text or final_text(messages)
        if not any(re.search(rx, answer) for rx in e["say_any"]):
            return False, f"답에 기대한 말이 없다: {answer[:80]!r}"
        return True, "기대한 말"
    return True, f"첫 걸음 {', '.join(names) or '도구 없음'}"
