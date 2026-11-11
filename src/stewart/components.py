"""구성 요소 사건 기록: 엔진의 손잡이(반복 알림, 잘라내기, 다시 묻기, 끝내기 전 확인 등)가 언제 무엇을 했는지 적는다.

- 기록만 한다. 모델에 가는 메시지, 종료 사유, 단계 수는 바꾸지 않는다.
- 사건 꼴(events.jsonl):
  {"kind": "component", "name", "step", "trigger", "action", "detail"}
  · action: ACTIONS 중 하나
  · detail: 숫자·참거짓·None·짧은 코드 글만 받는다. 글·경로 원문은 넣지 않는다(맞지 않는 값은 버리고 _dropped 로 센다).
- summary.components = {name: {"fired": n, "actions": {action: n}}}
- STEWART_COMPONENT_EVENTS=0 이면 끈다(사건도 summary 칸도 없음). 기본은 켬.
"""

from __future__ import annotations

import contextvars
import hashlib
import os
import re

ENV = "STEWART_COMPONENT_EVENTS"
ACTIONS = ("nudge", "block", "retry", "rewrite", "clip", "compact", "rollback", "return_to_model", "stop", "allow")
_CODE = re.compile(r"^[a-z0-9_.:-]{1,40}$")

# 도구 안(tools.py)에서 일어난 일을 엔진에 넘기는 자리. T.execute 가 호출마다 목록을 걸어 둔다
NOTES: contextvars.ContextVar[list | None] = contextvars.ContextVar("stewart_component_notes", default=None)


def enabled(environ=None) -> bool:
    return (os.environ if environ is None else environ).get(ENV, "1").strip() != "0"


def sig_hash(s: str) -> str:
    """호출 서명(이름+인자)의 짧은 해시. 원문은 남기지 않는다."""
    return hashlib.sha256(s.encode("utf-8", "replace")).hexdigest()[:12]


def clean(detail: dict) -> dict:
    """detail 에서 숫자·참거짓·None·코드 글만 남긴다."""
    out, dropped = {}, 0
    for k, v in detail.items():
        if not _CODE.match(str(k)):
            dropped += 1
            continue
        if v is None or isinstance(v, (bool, int, float)):
            out[k] = v
        elif isinstance(v, str) and _CODE.match(v):
            out[k] = v
        else:
            dropped += 1
    if dropped:
        out["_dropped"] = dropped
    return out


def note(name: str, trigger: str, action: str, **detail) -> None:
    """도구 안에서 부른다. T.execute 밖(시험 등)에서는 아무 일도 하지 않는다."""
    sink = NOTES.get()
    if sink is not None:
        sink.append({"name": name, "trigger": trigger, "action": action, "detail": detail})


class Components:
    def __init__(self, rec, on: bool | None = None):
        self.rec = rec
        self.on = enabled() if on is None else on
        self.counts: dict[str, dict] = {}
        self.last: str | None = None

    def fire(self, name: str, step: int, trigger: str, action: str, **detail) -> None:
        if not self.on:
            return
        if action not in ACTIONS:
            raise ValueError(f"모르는 action: {action}")
        d = clean(detail)
        self.rec.event("component", name=name, step=step, trigger=trigger if _CODE.match(trigger) else "other",
                       action=action, detail=d)
        c = self.counts.setdefault(name, {"fired": 0, "actions": {}})
        c["fired"] += 1
        c["actions"][action] = c["actions"].get(action, 0) + 1
        self.last = name

    def drain(self, notes: list, step: int, **extra) -> None:
        for n in notes:
            self.fire(n["name"], step, n["trigger"], n["action"], **{**n["detail"], **extra})
        notes.clear()

    def summary(self) -> dict | None:
        return self.counts if self.on else None
