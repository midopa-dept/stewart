"""생각 켬 차례의 손잡이: 단계당 생성 상한(max_tokens)과 샘플링 묶음.

- 프로필 think_max_tokens(정수) · think_sampling(묶음 이름). 환경 변수가 이긴다:
  STEWART_THINK_MAX_TOKENS = 정수(256 이상) 또는 0(끔), STEWART_THINK_SAMPLING = 묶음 이름 또는 none(끔).
  잘못된 값은 무시하고 기록한다. 둘 다 없으면 지금과 같다(백엔드 params 그대로).
- 생각 켬 판단: 그 요청의 thinking 인자가 있으면 그것, 없으면 백엔드 params 의 chat_template_kwargs.enable_thinking.
  생각 끔으로 다시 보내는 요청(빈 잘림 재시도 등)에는 쓰지 않는다.
- 상한은 문맥에 맞춘다: 서버 문맥(max_context) − 프롬프트 어림 − 여유 256 보다 크면 줄여 보낸다(1,024 아래로는 줄이지 않는다 —
  그래도 넘치면 문맥 넘침 재시도가 받는다).
- 샘플링 묶음은 백엔드 params 위에 덮어쓴다. 서버가 어떤 칸을 모른다고 400 으로 거절하면 그 칸을 빼고 한 번 다시 보내고,
  그 백엔드에서는 이후에도 뺀다(backend.dropped_params).
"""

from __future__ import annotations

import os

ENV_MAX, ENV_SAMPLING = "STEWART_THINK_MAX_TOKENS", "STEWART_THINK_SAMPLING"
PRESETS: dict[str, dict] = {
    # Qwen3 계열 모델 카드의 생각 모드 권장값 + 되풀이를 줄이는 presence_penalty
    "qwen": {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "presence_penalty": 1.0},
    "qwen-nopp": {"temperature": 0.6, "top_p": 0.95, "top_k": 20},
    "qwen-pp15": {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "presence_penalty": 1.5},
}
SAMPLING_KEYS = ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "frequency_penalty", "repetition_penalty")
FLOOR, MARGIN = 1024, 256


def settings(profile, env=None) -> dict:
    env = os.environ if env is None else env
    mx, samp, src, bad = getattr(profile, "think_max_tokens", None), getattr(profile, "think_sampling", None), {}, []
    src["max_tokens"] = "profile" if mx else None
    src["sampling"] = "profile" if samp else None
    v = env.get(ENV_MAX)
    if v is not None:
        if v.strip() == "0":
            mx, src["max_tokens"] = None, "env:off"
        elif v.strip().isdigit() and int(v) >= 256:
            mx, src["max_tokens"] = int(v), "env"
        else:
            bad.append(ENV_MAX)
    v = env.get(ENV_SAMPLING)
    if v is not None:
        if v.strip().lower() == "none":
            samp, src["sampling"] = None, "env:off"
        elif v.strip() in PRESETS:
            samp, src["sampling"] = v.strip(), "env"
        else:
            bad.append(ENV_SAMPLING)
    return {"max_tokens": mx, "sampling": samp, "params": dict(PRESETS[samp]) if samp else {}, "source": src, "ignored": bad}


def is_on(backend, thinking) -> bool:
    if thinking is not None:
        return bool(thinking)
    return bool((backend.params.get("chat_template_kwargs") or {}).get("enable_thinking"))


def fit(want: int, max_context: int, est_prompt: int) -> int:
    room = max_context - est_prompt - MARGIN
    return max(min(want, room), min(want, FLOOR))


def call_kwargs(backend, s: dict, thinking, est_prompt: int) -> dict:
    """이 요청에 더할 인자({max_tokens, overrides}). 생각 끔이거나 손잡이가 없으면 {}."""
    if not is_on(backend, thinking):
        return {}
    out = {}
    if s["max_tokens"]:
        out["max_tokens"] = fit(s["max_tokens"], backend.max_context, est_prompt)
    if s["params"]:
        out["overrides"] = dict(s["params"])
    return out
