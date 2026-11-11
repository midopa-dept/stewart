"""Anthropic Messages API 백엔드(판 69, backends.toml kind = "anthropic").

stewart 의 엔진·chat 은 OpenAI chat/completions 꼴(messages·tools·choices·usage)로 일한다. 이 모듈이 그 꼴을
Messages API 꼴로 옮겨 공식 SDK(anthropic, 선택 꾸러미 `stewart[anthropic]`)로 보내고, 응답을 다시 OpenAI 꼴로 돌려준다.
- 도구: {"type":"function","function":{name,description,parameters}} → {name, description, input_schema}
- 메시지: system → system 블록, assistant.tool_calls → tool_use 블록, role tool → tool_result 블록(사람 자리)
- 생각(thinking): 응답의 내용 블록을 assistant 메시지의 "anthropic_content" 에 그대로 남기고, 다음 요청에 바꾸지 않고 되보낸다
  (생각 블록은 바꾸지 않고 되보내야 한다). 엔진이 그 메시지의 글·툴콜을 바꿨으면(압축) 그 차례는 생각 없이 다시 꾸린다.
- 프롬프트 캐시: 요청 맨 위 cache_control(자동 — 마지막 캐시 가능 블록까지 접두가 캐시된다). 도구·시스템·앞 대화가 접두라 함께 캐시.
- usage: prompt_tokens = input + cache_read + cache_write, completion_tokens = output, prompt_tokens_details.cached_tokens = cache_read,
  원래 칸은 usage.anthropic, 회차 비용은 usage.cost_usd(단가는 params.price 또는 아래 표).
- 키: 파일에 적지 않는다. backends.toml 의 api_key_env(환경 변수 이름)만. 재시도(429·529·5xx·연결)는 SDK 가 한다(params.max_retries).
"""

from __future__ import annotations

import base64
import json
import re
import time

# 1M 토큰당 달러(공식 표 2026-10 기준 — 바뀌면 backends.toml params.price 로 덮는다). 캐시 쓰기는 5분 TTL = 입력 × 1.25
PRICES = {
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.2, "cache_write": 5.0},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5},
    "claude-haiku-5-5": {"input": 0.1, "output": 0.5, "cache_read": 0.01, "cache_write": 0.125},
}
DEFAULT_MAX_TOKENS = 32000


class AnthropicMissing(RuntimeError):
    pass


def _sdk():
    try:
        import anthropic
    except ImportError:
        raise AnthropicMissing("anthropic 꾸러미가 없다 — `uv sync --extra anthropic`(또는 pip install 'stewart[anthropic]')") from None
    return anthropic


# ── 꼴 옮기기(순수 함수, 망 없음) ──
def to_tools(tools: list[dict] | None) -> list[dict]:
    out = []
    for t in tools or []:
        f = t.get("function") or t
        out.append({"name": f["name"], "description": f.get("description") or "",
                    "input_schema": f.get("parameters") or {"type": "object", "properties": {}}})
    return out


_DATA_URL = re.compile(r"^data:(image/[\w.+-]+);base64,(.*)$", re.S)


def _user_blocks(content) -> list[dict] | str:
    if isinstance(content, str):
        return content
    out = []
    for part in content or []:
        if part.get("type") == "text":
            out.append({"type": "text", "text": part.get("text") or ""})
        elif part.get("type") == "image_url":
            m = _DATA_URL.match(str((part.get("image_url") or {}).get("url") or ""))
            if m:
                out.append({"type": "image", "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)}})
    return out or ""


def _args(tc: dict) -> dict:
    raw = (tc.get("function") or {}).get("arguments")
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {"_raw": raw}
    except (ValueError, TypeError):
        return {"_raw": raw}


def _assistant_blocks(m: dict) -> list[dict]:
    """엔진이 바꾸지 않은 응답이면 받은 블록(생각 포함) 그대로, 바뀌었으면 글·툴콜에서 다시(생각 없이)."""
    text = m.get("content") if isinstance(m.get("content"), str) else ""
    calls = m.get("tool_calls") or []
    raw = m.get("anthropic_content")
    if isinstance(raw, list) and raw:
        r_text = "".join(b.get("text", "") for b in raw if b.get("type") == "text")
        r_calls = [(b.get("id"), b.get("name"), b.get("input")) for b in raw if b.get("type") == "tool_use"]
        mine = [(tc.get("id"), (tc.get("function") or {}).get("name"), _args(tc)) for tc in calls]
        if r_text == (text or "") and r_calls == mine:
            return raw
    out = []
    if text:
        out.append({"type": "text", "text": text})
    for tc in calls:
        out.append({"type": "tool_use", "id": tc.get("id"), "name": (tc.get("function") or {}).get("name"), "input": _args(tc)})
    return out or [{"type": "text", "text": "(빈 답)"}]


def to_messages(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """(system 블록들, messages). 앞쪽 system 들은 system 으로, 중간의 system 은 사람 자리의 [시스템] 글로."""
    system, out, i = [], [], 0
    while i < len(messages) and messages[i].get("role") == "system":
        system.append({"type": "text", "text": str(messages[i].get("content") or "")})
        i += 1
    for m in messages[i:]:
        role = m.get("role")
        if role == "system":
            out.append({"role": "user", "content": "[시스템] " + str(m.get("content") or "")})
        elif role == "user":
            out.append({"role": "user", "content": _user_blocks(m.get("content"))})
        elif role == "assistant":
            out.append({"role": "assistant", "content": _assistant_blocks(m)})
        elif role == "tool":
            body = m.get("content")
            blk = {"type": "tool_result", "tool_use_id": m.get("tool_call_id"),
                   "content": body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)}
            if m.get("is_error"):
                blk["is_error"] = True
            out.append({"role": "user", "content": [blk]})
    return system, out


def thinking_param(model: str, thinking: bool | None, params: dict) -> dict | None:
    """켬(또는 정하지 않음) = adaptive. 끔 = Sonnet 5.5 는 between_tools, Opus 5.5 는 끌 수 없어 adaptive(노력 값으로 조절)."""
    disp = params.get("thinking_display")
    if thinking is False:
        if model.startswith("claude-sonnet-5-5"):
            return {"type": "between_tools"}
        if model.startswith(("claude-opus-5-5", "claude-fable")):
            return None   # 생략 = adaptive(끌 수 없다)
    t = {"type": "adaptive"}
    if disp:
        t["display"] = disp
    return t


def price_of(model: str, params: dict) -> dict | None:
    p = params.get("price")
    if isinstance(p, dict):
        return p
    return next((v for k, v in PRICES.items() if model.startswith(k)), None)


def to_openai(msg: dict, model: str, params: dict) -> dict:
    """Messages API 응답(dict) → OpenAI chat/completions 꼴 응답."""
    blocks = msg.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    think = "".join(b.get("thinking", "") or "" for b in blocks if b.get("type") == "thinking")
    calls = [{"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)}}
             for b in blocks if b.get("type") == "tool_use"]
    m = {"role": "assistant", "content": text or None, "anthropic_content": blocks}
    if calls:
        m["tool_calls"] = calls
    if think:
        m["reasoning_content"] = think
    sr = msg.get("stop_reason")
    fr = {"tool_use": "tool_calls", "end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length",
          "refusal": "refusal", "pause_turn": "stop"}.get(sr, sr or "stop")
    u = msg.get("usage") or {}
    inp, out = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
    cr, cw = int(u.get("cache_read_input_tokens") or 0), int(u.get("cache_creation_input_tokens") or 0)
    usage = {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw,   # 원 값 이름 그대로
             "prompt_tokens": inp + cr + cw, "completion_tokens": out, "total_tokens": inp + cr + cw + out,
             "prompt_tokens_details": {"cached_tokens": cr},
             "anthropic": {"input_tokens": inp, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw, "output_tokens": out}}
    pr = price_of(model, params)
    if pr:
        usage["cost_usd"] = round((inp * pr["input"] + out * pr["output"] + cr * pr["cache_read"] + cw * pr["cache_write"]) / 1e6, 6)
    out_resp = {"id": msg.get("id"), "model": msg.get("model") or model, "choices": [{"index": 0, "message": m, "finish_reason": fr}],
                "usage": usage, "stop_reason": sr}
    if msg.get("stop_details"):
        out_resp["stop_details"] = msg["stop_details"]
    return out_resp


def request(backend, messages: list[dict], tools: list[dict] | None, max_tokens: int | None, thinking: bool | None) -> dict:
    """SDK messages.stream 에 넘길 칸들."""
    params = dict(backend.params or {})
    system, msgs = to_messages(messages)
    kw = {"model": backend.model, "max_tokens": int(max_tokens or params.get("max_tokens") or DEFAULT_MAX_TOKENS),
          "messages": msgs, "cache_control": {"type": "ephemeral", **({"ttl": params["cache_ttl"]} if params.get("cache_ttl") else {})}}
    if system:
        kw["system"] = system
    t = to_tools(tools)
    if t:
        kw["tools"] = t
    th = thinking_param(backend.model, thinking, params)
    if th is not None:
        kw["thinking"] = th
    if params.get("effort") and not (th and th.get("type") == "between_tools" and params["effort"] in ("xhigh", "max")):
        kw["output_config"] = {"effort": params["effort"]}
    return kw


# ── 보내기 ──
_CLIENTS: dict = {}


def _client(backend):
    a = _sdk()
    key = backend.api_key()
    if not key:
        from .backend import BackendError
        raise BackendError(f"API 키가 없다: 환경 변수 {backend.api_key_env or '(api_key_env 를 backends.toml 에)'}", 401, "")
    ck = (backend.base_url, key, backend.timeout_s, int((backend.params or {}).get("max_retries", 4)))
    if ck not in _CLIENTS:
        _CLIENTS[ck] = a.Anthropic(api_key=key, base_url=backend.base_url, timeout=backend.timeout_s, max_retries=ck[3])
    return _CLIENTS[ck]


def _error(e):
    from .backend import BackendError
    a = _sdk()
    if isinstance(e, a.APIStatusError):
        body = ""
        try:
            body = json.dumps(e.body, ensure_ascii=False)[:4000] if e.body is not None else ""
        except (TypeError, ValueError):
            body = str(e.body)[:4000]
        return BackendError(f"Anthropic API 오류 {e.status_code}: {getattr(e, 'message', e)}", e.status_code, body)
    if isinstance(e, a.APIConnectionError):
        return BackendError(f"연결 실패: {e}")
    return BackendError(f"Anthropic 오류: {type(e).__name__}: {e}")


def chat(backend, messages, tools=None, max_tokens=None, thinking=None, on_delta=None, on_first=None) -> tuple[dict, float]:
    """요청 1회(늘 스트리밍 — 긴 출력에서 시간 끊김이 없게). (OpenAI 꼴 응답, 초). on_delta(본문 조각, 생각 조각)."""
    a = _sdk()
    kw = request(backend, messages, tools, max_tokens, thinking)
    client = _client(backend)
    t0 = time.monotonic()
    text_so_far = []
    try:
        with client.messages.stream(**kw) as s:
            for ev in s:
                if ev.type == "content_block_delta":
                    d = ev.delta
                    t, th = (getattr(d, "text", "") or "") if d.type == "text_delta" else "", \
                        (getattr(d, "thinking", "") or "") if d.type == "thinking_delta" else ""
                    if (t or th or d.type == "input_json_delta") and on_first is not None:
                        on_first()
                        on_first = None
                    if t:
                        text_so_far.append(t)
                    if on_delta is not None and (t or th):
                        on_delta(t, th)
            final = s.get_final_message()
    except KeyboardInterrupt as e:
        e.partial = {"role": "assistant", "content": "".join(text_so_far) or None}
        raise
    except a.AnthropicError as e:
        raise _error(e) from None
    return to_openai(final.model_dump(mode="json"), backend.model, backend.params or {}), time.monotonic() - t0
