"""행동 검사 자리 — 툴콜 실행 직전에 불린다.

검사 함수 형태: check(call: ToolCall, ctx: CheckContext) -> str | None
  None 이면 실행, 문자열이면 실행하지 않고 그 문자열을 거부 사유로 모델에 돌려준다.
프로필의 action_check = "모듈:함수" 로 바꿔 끼운다. 기본은 allow_all(no-op).
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    raw_arguments: str


@dataclass
class CheckContext:
    profile: str
    workdir: Path
    step: int


CheckFn = Callable[[ToolCall, CheckContext], "str | None"]


def allow_all(call: ToolCall, ctx: CheckContext) -> str | None:
    return None


def load_check(spec: str | None) -> CheckFn:
    if not spec:
        return allow_all
    mod, _, fn = spec.partition(":")
    return getattr(importlib.import_module(mod), fn)
