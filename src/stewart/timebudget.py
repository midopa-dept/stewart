"""판 128: 엔진 안 시간 예산 — 바깥 시간 제한에 끊기기 전에, 지금까지 본 것으로 마지막 답을 받는다.

- 손잡이: `stewart chat --once --time-budget <초>` 또는 환경 변수 STEWART_TIME_BUDGET_S. 값이 없으면 지금과 같다(끔).
- 예산의 FRAC(기본 0.85, STEWART_TIME_BUDGET_FRAC)에 닿으면 새 도구 걸음을 시작하지 않고, 도구 없이 마지막 답 한 차례를 받는다
  (되풀이 호출·연속 잘림과 같은 길 — 생각 끔). 남은 몫(1 - FRAC)은 그 마지막 답의 몫이다.
- 걸음 도중: 그 걸음의 생성 상한을 남은 시간 × 지금까지 잰 생성 속도로 줄인다. 한 걸음이 수백 초를 쓰다 바깥 벽에 끊기지 않게.
- 끝 이유는 time_budget(되풀이 호출·잘림과 따로 센다).
"""

from __future__ import annotations

import os
import time

ENV_S = "STEWART_TIME_BUDGET_S"
ENV_FRAC = "STEWART_TIME_BUDGET_FRAC"
DEFAULT_FRAC = 0.85
CAP_FLOOR = 256      # 줄여도 이만큼은 준다(그보다 적으면 도구 호출 한 줄도 못 쓴다)
CAP_MARGIN = 0.9     # 잰 속도의 이만큼만 믿는다(문맥이 길어지면 느려진다)


class TimeBudget:
    """예산 seconds 초, 그중 frac 에 닿으면 마지막 답. t0 는 시계 기준점(없으면 만든 때)."""

    def __init__(self, seconds: float, frac: float = DEFAULT_FRAC, t0: float | None = None, clock=time.monotonic):
        if seconds <= 0:
            raise ValueError("시간 예산은 0 보다 커야 한다")
        if not 0 < frac <= 1:
            raise ValueError("시간 예산 비율은 0 초과 1 이하")
        self.seconds, self.frac, self.clock = float(seconds), float(frac), clock
        self.t0 = clock() if t0 is None else t0

    @property
    def soft_s(self) -> float:
        return self.seconds * self.frac

    def used(self) -> float:
        return self.clock() - self.t0

    def left(self) -> float:
        """마지막 답으로 넘어갈 때까지 남은 초(음수면 지났다)."""
        return self.soft_s - self.used()

    def over(self) -> bool:
        return self.left() <= 0

    def cap_tokens(self, max_tokens: int | None, tok_per_s: float | None) -> int | None:
        """이 걸음의 생성 상한을 남은 시간에 맞춘 값 — 줄일 필요가 없거나 속도를 아직 모르면 None."""
        if not tok_per_s or tok_per_s <= 0:
            return None
        allowed = int(max(0.0, self.left()) * tok_per_s * CAP_MARGIN)
        if max_tokens and allowed >= max_tokens:
            return None
        return max(CAP_FLOOR, allowed)

    def info(self) -> dict:
        return {"budget_s": self.seconds, "frac": self.frac, "used_s": round(self.used(), 1)}


def frac_from_env(env=None) -> float:
    env = os.environ if env is None else env
    try:
        f = float(env.get(ENV_FRAC) or DEFAULT_FRAC)
    except ValueError:
        return DEFAULT_FRAC
    return f if 0 < f <= 1 else DEFAULT_FRAC


def from_env(env=None, t0: float | None = None) -> TimeBudget | None:
    """환경 변수에서 — 없거나 0·잘못된 값이면 None(끔)."""
    env = os.environ if env is None else env
    try:
        s = float(env.get(ENV_S) or 0)
    except ValueError:
        return None
    return TimeBudget(s, frac_from_env(env), t0=t0) if s > 0 else None


def make(seconds: float | None, env=None, t0: float | None = None) -> TimeBudget | None:
    """손잡이 값(--time-budget)이 있으면 그것, 없으면 환경 변수."""
    if seconds is not None:
        return TimeBudget(seconds, frac_from_env(env), t0=t0) if seconds > 0 else None
    return from_env(env, t0=t0)
