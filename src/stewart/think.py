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
- 판 123: thinking_token_budget(서버 생각 예산, 생각 켬 요청만) — 등급 think 칸 또는 STEWART_THINKING_TOKEN_BUDGET(0 = 끔).
  노력(effort)을 template 자리에 실었는데 틀이 그 낱말을 모른다고 400 이면 chat_template_kwargs 의 reasoning_effort 를 빼고 다시 보낸다.
  백엔드 effort_values(그 틀이 받는 낱말)를 적으면 밖의 값은 시작할 때 알리고 보내지 않는다(check_effort). 적지 않으면 그대로 보내는데,
  틀이 모르는 값은 오류 없이 다른 수준(예: 기본)으로 처리될 수 있다 — 400 이 아니라 위의 빼고 다시 보내기도 걸리지 않는다.
"""

from __future__ import annotations

import os

ENV_MAX, ENV_SAMPLING = "STEWART_THINK_MAX_TOKENS", "STEWART_THINK_SAMPLING"
# 판 116(실험 손잡이, 기본 없음 = 지금과 같다): 차례 첫 걸음의 생각 상한, 노력 수준(reasoning_effort)과 싣는 자리
ENV_MAX_FIRST, ENV_EFFORT, ENV_EFFORT_AT = "STEWART_THINK_MAX_TOKENS_FIRST", "STEWART_REASONING_EFFORT", "STEWART_REASONING_EFFORT_AT"
# 판 123(실험 손잡이, 기본 없음): 서버의 생각 예산 칸(vLLM 요청 thinking_token_budget — 예산에 닿으면 서버가 생각을 닫는다).
# 생각 켬 요청에만 싣는다. 서버가 모른다고 400 이면 빼고 다시 보낸다(backend.dropped_params).
ENV_BUDGET = "STEWART_THINKING_TOKEN_BUDGET"
EFFORT_RX = r"^[a-z]{2,12}$"
PRESETS: dict[str, dict] = {
    # Qwen3 계열 모델 카드의 생각 모드 권장값 + 되풀이를 줄이는 presence_penalty
    "qwen": {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "presence_penalty": 1.0},
    "qwen-nopp": {"temperature": 0.6, "top_p": 0.95, "top_k": 20},
    "qwen-pp15": {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "presence_penalty": 1.5},
}
SAMPLING_KEYS = ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "frequency_penalty", "repetition_penalty")
FLOOR, MARGIN = 1024, 256


def settings(profile, env=None) -> dict:
    """우선순위: 환경 변수 > 등급(프로필 raw["tier"]["think"], 판 116) > 프로필. 아무것도 없으면 지금과 같다."""
    import re
    env = os.environ if env is None else env
    mx, samp, src, bad = getattr(profile, "think_max_tokens", None), getattr(profile, "think_sampling", None), {}, []
    src["max_tokens"] = "profile" if mx else None
    src["sampling"] = "profile" if samp else None
    tt = ((getattr(profile, "raw", None) or {}).get("tier") or {}).get("think") or {}
    first, effort, at, params = None, None, "body", None
    if isinstance(tt.get("think_max_tokens"), int) and tt["think_max_tokens"] >= 256:
        mx, src["max_tokens"] = tt["think_max_tokens"], "tier"
    if isinstance(tt.get("think_max_tokens_first"), int) and tt["think_max_tokens_first"] >= 256:
        first, src["max_first"] = tt["think_max_tokens_first"], "tier"
    if isinstance(tt.get("reasoning_effort"), str) and re.match(EFFORT_RX, tt["reasoning_effort"]):
        effort, src["effort"] = tt["reasoning_effort"], "tier"
    if tt.get("reasoning_effort_at") in ("body", "template"):
        at = tt["reasoning_effort_at"]
    budget = None
    if isinstance(tt.get("thinking_token_budget"), int) and tt["thinking_token_budget"] >= 1:
        budget, src["budget"] = tt["thinking_token_budget"], "tier"
    ts = tt.get("think_sampling")
    if isinstance(ts, str) and ts in PRESETS:
        samp, src["sampling"] = ts, "tier"
    elif isinstance(ts, dict):
        params, src["sampling"] = {k: v for k, v in ts.items() if k in SAMPLING_KEYS}, "tier"
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
            samp, params, src["sampling"] = None, None, "env:off"
        elif v.strip() in PRESETS:
            samp, params, src["sampling"] = v.strip(), None, "env"
        else:
            bad.append(ENV_SAMPLING)
    v = env.get(ENV_MAX_FIRST)
    if v is not None:
        if v.strip() == "0":
            first, src["max_first"] = None, "env:off"
        elif v.strip().isdigit() and int(v) >= 256:
            first, src["max_first"] = int(v), "env"
        else:
            bad.append(ENV_MAX_FIRST)
    v = env.get(ENV_EFFORT)
    if v is not None:
        if v.strip().lower() in ("", "none", "off"):
            effort, src["effort"] = None, "env:off"
        elif re.match(EFFORT_RX, v.strip().lower()):
            effort, src["effort"] = v.strip().lower(), "env"
        else:
            bad.append(ENV_EFFORT)
    v = env.get(ENV_BUDGET)
    if v is not None:
        if v.strip() in ("", "0"):
            budget, src["budget"] = None, "env:off"
        elif v.strip().isdigit():
            budget, src["budget"] = int(v), "env"
        else:
            bad.append(ENV_BUDGET)
    v = env.get(ENV_EFFORT_AT)
    if v is not None:
        if v.strip() in ("body", "template"):
            at = v.strip()
        else:
            bad.append(ENV_EFFORT_AT)
    out = {"max_tokens": mx, "sampling": samp, "params": dict(params) if params is not None else (dict(PRESETS[samp]) if samp else {}),
           "source": src, "ignored": bad}
    if first:
        out["max_first"] = first
    if effort:
        out["effort"], out["effort_at"] = effort, at
    if budget:
        out["budget"] = budget
    return out


def is_on(backend, thinking) -> bool:
    if thinking is not None:
        return bool(thinking)
    return bool((backend.params.get("chat_template_kwargs") or {}).get("enable_thinking"))


def fit(want: int, max_context: int, est_prompt: int) -> int:
    room = max_context - est_prompt - MARGIN
    return max(min(want, room), min(want, FLOOR))


def check_effort(s: dict, backend) -> str | None:
    """판 123: 백엔드에 effort_values(그 틀이 받는 낱말)가 있는데 노력 값이 그 밖이면 노력을 빼고(보내지 않는다) 알릴 글을 돌려준다.
    목록이 없으면 그대로 둔다 — 틀이 모르는 값은 오류 없이 다른 수준으로 처리될 수 있다(서버 틀에 따라)."""
    allowed = getattr(backend, "effort_values", None)
    v = s.get("effort")
    if not v or not allowed or v in allowed:
        return None
    s.pop("effort", None)
    s.pop("effort_at", None)
    s.setdefault("ignored", []).append(f"effort:{v}")
    s["effort_rejected"] = {"value": v, "allowed": list(allowed), "backend": getattr(backend, "name", "")}
    return (f"노력 수준 '{v}' 는 백엔드 {getattr(backend, 'name', '')} 가 받는 낱말({'·'.join(allowed)})이 아니라 보내지 않는다 "
            f"— 기본 수준으로 돈다. 등급의 reasoning_effort 나 STEWART_REASONING_EFFORT 를 고친다.")


def call_kwargs(backend, s: dict, thinking, est_prompt: int, first: bool = False) -> dict:
    """이 요청에 더할 인자({max_tokens, overrides}). 손잡이가 없으면 {}.
    상한·샘플링은 생각 켬 요청에만. first(차례 첫 걸음)면 max_first 가 있을 때 그것(판 116).
    노력(effort)은 생각을 끄지 않은 요청에 싣는다 — body 면 본문 맨 위 reasoning_effort, template 이면 chat_template_kwargs 안(판 116)."""
    out: dict = {}
    if s.get("effort") and thinking is not False:
        if s.get("effort_at") == "template":
            ctk = dict(backend.params.get("chat_template_kwargs") or {})
            if thinking is not None:
                ctk["enable_thinking"] = bool(thinking)
            ctk["reasoning_effort"] = s["effort"]
            out["overrides"] = {"chat_template_kwargs": ctk}
        else:
            out["overrides"] = {"reasoning_effort": s["effort"]}
    if not is_on(backend, thinking):
        return out
    mx = s.get("max_first") if (first and s.get("max_first")) else s["max_tokens"]
    if mx:
        out["max_tokens"] = fit(mx, backend.max_context, est_prompt)
    if s["params"]:
        out["overrides"] = {**out.get("overrides", {}), **dict(s["params"])}
    if s.get("budget"):
        out["overrides"] = {**out.get("overrides", {}), "thinking_token_budget": s["budget"]}
    return out
