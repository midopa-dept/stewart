"""OpenAI /v1/chat/completions 클라이언트(표준 라이브러리만).

백엔드마다 동시 요청 수 상한(Backend.concurrency)을 둔다. 한 프로세스 안의 여러 스레드(bench -j, chat 등)가
같은 백엔드를 부르면 상한까지만 동시에 나가고 나머지는 기다린다. mac은 1, cloud·box는 N.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request

from .config import Backend


class BackendError(Exception):
    def __init__(self, msg: str, status: int | None = None, body: str = ""):
        super().__init__(msg)
        self.status = status
        self.body = body


# 백엔드가 "문맥(최대 길이) 초과"로 요청을 거부한 응답 글. vLLM·OpenAI·llama.cpp 의 문구를 모았다.
_OVERFLOW = re.compile(
    r"maximum context length|context length (?:is|of)|context_length_exceeded|exceeds? (?:the )?(?:available )?context|"
    r"prompt is too long|too many tokens|input (?:tokens )?exceed|max_model_len|context size has been exceeded",
    re.I)


def is_context_overflow(e: "BackendError") -> bool:
    """백엔드 오류가 문맥 넘침(요청 길이 > 최대 길이)인가. HTTP 4xx(주로 400·413)와 응답 문구로 판단한다."""
    if e.status not in (400, 413, 422):
        return False
    return bool(_OVERFLOW.search(e.body or "") or _OVERFLOW.search(str(e)))


_SEMS: dict[tuple[str, int], threading.BoundedSemaphore] = {}
_SEMS_LOCK = threading.Lock()


def slot(backend: Backend) -> threading.BoundedSemaphore:
    """백엔드 이름·상한별 세마포어(프로세스 안에서 공유)."""
    key = (backend.name, backend.concurrency)
    with _SEMS_LOCK:
        if key not in _SEMS:
            _SEMS[key] = threading.BoundedSemaphore(backend.concurrency)
        return _SEMS[key]


def _headers(backend: Backend) -> dict:
    headers = {"Content-Type": "application/json"}
    key = backend.api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _body(backend: Backend, messages, tools=None, response_format=None, **extra) -> dict:
    body: dict = {"model": backend.model, "messages": messages, "stream": False}
    body.update(backend.params)
    if tools:
        body["tools"] = tools
        body.setdefault("tool_choice", "auto")
    if response_format:
        body["response_format"] = response_format
    body.update(extra)
    return body


def thinking_kwargs(backend: Backend, thinking: bool | None) -> dict:
    """백엔드 params 의 chat_template_kwargs 에 enable_thinking 만 바꿔 끼운 덮어쓰기 값(None 이면 빈 dict)."""
    if thinking is None:
        return {}
    kw = dict(backend.params.get("chat_template_kwargs") or {})
    kw["enable_thinking"] = bool(thinking)
    return {"chat_template_kwargs": kw}


def _open(backend: Backend, path: str, body: dict | None, timeout: float):
    req = urllib.request.Request(
        backend.base_url + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
        headers=_headers(backend),
        method="POST" if body is not None else "GET",
    )
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        raise BackendError(f"HTTP {e.code}", e.code, text[:4000]) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise BackendError(f"연결 실패: {e}") from None


def _send(backend: Backend, body: dict, overrides: dict | None):
    """요청을 연다. 덮어쓴 샘플링 칸을 서버가 모른다고 400 으로 거절하면 그 칸을 빼고 한 번 다시(그 백엔드에서는 이후에도 뺀다)."""
    from .think import SAMPLING_KEYS
    for k in backend.dropped_params:
        body.pop(k, None)
    try:
        return _open(backend, "/chat/completions", body, backend.timeout_s)
    except BackendError as e:
        bad = [k for k in (overrides or {}) if k in SAMPLING_KEYS and k in body and k in (e.body or "")]
        if e.status not in (400, 422) or not bad:
            raise
        for k in bad:
            body.pop(k, None)
            if k not in backend.dropped_params:
                backend.dropped_params.append(k)
        return _open(backend, "/chat/completions", body, backend.timeout_s)


def chat(
    backend: Backend,
    messages: list[dict],
    tools: list[dict] | None = None,
    response_format: dict | None = None,
    max_tokens: int | None = None,
    thinking: bool | None = None,
    overrides: dict | None = None,
) -> tuple[dict, float]:
    """요청 1회. (응답 JSON, 소요 초)를 돌려준다. 소요 초에는 상한 대기 시간이 들어가지 않는다.
    max_tokens 가 있으면 백엔드 params 의 값을 덮어쓴다(프로필 max_tokens).
    thinking 이 None 이 아니면 chat_template_kwargs.enable_thinking 을 그 값으로 덮어쓴다(chat /think)."""
    extra = {"max_tokens": max_tokens} if max_tokens else {}
    extra.update(thinking_kwargs(backend, thinking))
    extra.update(overrides or {})   # 생각 켬 샘플링 묶음(think.py)
    body = _body(backend, messages, tools, response_format, **extra)
    with slot(backend):
        t0 = time.monotonic()
        try:
            with _send(backend, body, overrides) as r:
                data = json.loads(r.read().decode("utf-8"))
        except (TimeoutError, ConnectionError, OSError) as e:
            if isinstance(e, BackendError):
                raise
            raise BackendError(f"연결 실패: {e}") from None
        return data, time.monotonic() - t0


def models(backend: Backend, timeout: float = 15) -> tuple[list[str], float]:
    """GET /models — 서버가 올린 모델 이름 목록."""
    t0 = time.monotonic()
    with _open(backend, "/models", None, timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    return [m.get("id", "?") for m in data.get("data") or []], time.monotonic() - t0


def served_context(backend: Backend, timeout: float = 5) -> int | None:
    """서버가 실제로 받는 문맥 길이. vLLM GET /v1/models 의 max_model_len(같은 id 의 것, 없으면 하나뿐일 때 그것).
    서버가 알려 주지 않거나 닿지 않으면 None."""
    try:
        with _open(backend, "/models", None, timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    items = [m for m in (data.get("data") or []) if isinstance(m, dict)] if isinstance(data, dict) else []
    pick = [m for m in items if m.get("id") == backend.model] or (items if len(items) == 1 else [])
    for m in pick:
        v = m.get("max_model_len")
        if isinstance(v, int) and not isinstance(v, bool) and v > 0:
            return v
    return None


def sync_context(backend: Backend, timeout: float = 5) -> dict:
    """실제 문맥 길이를 서버 값으로 맞춘다. 서버 max_model_len 이 있으면 backends 의 max_context 보다 우선한다.
    한 Backend 에 한 번만 묻는다(batch·eval 이 같은 백엔드로 여러 번 돌려도). 다르면 warn 에 한 줄을 돌려준다.
    STEWART_SERVED_CONTEXT=0 이면 묻지 않고 backends 값을 쓴다(오프라인 시험 등).
    돌려주는 값: {configured, served, used, source, warn}"""
    if backend.configured_context is None:
        backend.configured_context = backend.max_context
        import os
        served = None if os.environ.get("STEWART_SERVED_CONTEXT") == "0" else served_context(backend, timeout)
        backend.served_max_len = served
        if served:
            backend.max_context, backend.context_source = served, "server"
    served = backend.served_max_len
    conf = backend.configured_context
    warn = ""
    if served and served != conf:
        warn = (f"문맥 길이: 서버 max_model_len {served:,} 를 쓴다(backends.toml {backend.name}.max_context {conf:,} 와 다름"
                f" — 표를 고치거나 서버 설정을 본다)")
    return {"configured": conf, "served": served, "used": backend.max_context, "source": backend.context_source, "warn": warn}


def stream_measure(backend: Backend, messages: list[dict], max_tokens: int, use_slot: bool = True,
                   extra: dict | None = None) -> dict:
    """스트리밍 요청 1회로 서빙 속도를 잰다: 첫 토큰까지(TTFT), 생성 토큰 수, 디코드 tok/s, 전체 시간.
    토큰 수는 서버 usage(stream_options.include_usage)를 쓰고, 없으면 받은 조각 수로 어림한다."""
    body = _body(backend, messages, None, None, stream=True, max_tokens=max_tokens,
                 stream_options={"include_usage": True}, **(extra or {}))
    sem = slot(backend) if use_slot else None
    if sem:
        sem.acquire()
    try:
        t0 = time.monotonic()
        ttft = None
        chunks = 0
        usage = {}
        text_len = 0
        with _open(backend, "/chat/completions", body, backend.timeout_s) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    ev = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if ev.get("usage"):
                    usage = ev["usage"]
                for ch in ev.get("choices") or []:
                    d = ch.get("delta") or {}
                    piece = (d.get("content") or "") + (d.get("reasoning_content") or d.get("reasoning") or "")
                    if piece:
                        if ttft is None:
                            ttft = time.monotonic() - t0
                        chunks += 1
                        text_len += len(piece)
        total = time.monotonic() - t0
    finally:
        if sem:
            sem.release()
    n = usage.get("completion_tokens") or chunks
    decode_s = total - (ttft or 0)
    return {"ttft_s": ttft, "total_s": total, "completion_tokens": n, "token_source": "usage" if usage else "chunks",
            "prompt_tokens": usage.get("prompt_tokens"), "chars": text_len,
            "decode_tok_per_s": (n - 1) / decode_s if n > 1 and decode_s > 0 else None,
            "e2e_tok_per_s": n / total if total > 0 else None}


class StreamAssembler:
    """스트리밍 조각(SSE data JSON)을 모아 일반 응답과 같은 모양({"choices":[{"message", "finish_reason"}], "usage"})을 만든다.
    툴콜 조각은 index 로 모은다(id·이름은 첫 조각, arguments 는 이어 붙임)."""

    def __init__(self):
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.calls: dict[int, dict] = {}
        self.finish_reason = None
        self.usage: dict = {}
        self.timings: dict = {}

    def feed(self, ev: dict) -> tuple[str, str]:
        """조각 하나를 넣고 (새 본문 글, 새 생각 글)을 돌려준다."""
        if ev.get("usage"):
            self.usage = ev["usage"]
        if ev.get("timings"):
            self.timings = ev["timings"]
        text, think = "", ""
        for ch in ev.get("choices") or []:
            d = ch.get("delta") or {}
            if d.get("content"):
                text += d["content"]
            r = d.get("reasoning_content") or d.get("reasoning")
            if r:
                think += r
            for tc in d.get("tool_calls") or []:
                slot = self.calls.setdefault(int(tc.get("index", len(self.calls))),
                                             {"id": None, "type": "function", "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["function"]["arguments"] += fn["arguments"]
            if ch.get("finish_reason"):
                self.finish_reason = ch["finish_reason"]
        self.content.append(text)
        self.reasoning.append(think)
        return text, think

    def message(self) -> dict:
        msg: dict = {"role": "assistant", "content": "".join(self.content) or None}
        if any(self.reasoning):
            msg["reasoning_content"] = "".join(self.reasoning)
        if self.calls:
            msg["tool_calls"] = [{k: v for k, v in self.calls[i].items() if v is not None} for i in sorted(self.calls)]
        return msg

    def response(self) -> dict:
        r = {"choices": [{"message": self.message(), "finish_reason": self.finish_reason}], "usage": self.usage}
        if self.timings:
            r["timings"] = self.timings
        return r


def chat_stream(
    backend: Backend,
    messages: list[dict],
    tools: list[dict] | None = None,
    response_format: dict | None = None,
    max_tokens: int | None = None,
    on_delta=None,
    thinking: bool | None = None,
    overrides: dict | None = None,
) -> tuple[dict, float]:
    """chat() 과 같은 (응답, 초)를 돌려주지만 스트리밍으로 받는다. on_delta(본문 조각, 생각 조각)를 조각마다 부른다.
    도중에 KeyboardInterrupt 가 나면 연결을 바로 닫고(서버도 생성을 멈춘다) 받은 데까지를 e.partial 에 붙여 다시 던진다."""
    extra = {"stream": True, "stream_options": {"include_usage": True}}
    if max_tokens:
        extra["max_tokens"] = max_tokens
    extra.update(thinking_kwargs(backend, thinking))
    extra.update(overrides or {})
    body = _body(backend, messages, tools, response_format, **extra)
    asm = StreamAssembler()
    with slot(backend):
        t0 = time.monotonic()
        try:
            with _send(backend, body, overrides) as r:
                for raw in r:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        ev = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("error"):
                        raise BackendError(f"스트림 오류: {ev['error']}", 500, json.dumps(ev["error"], ensure_ascii=False)[:4000])
                    text, think = asm.feed(ev)
                    if on_delta and (text or think):
                        on_delta(text, think)
        except KeyboardInterrupt as e:
            e.partial = asm.message()
            raise
        except (TimeoutError, ConnectionError, OSError) as e:
            if isinstance(e, BackendError):
                raise
            raise BackendError(f"연결 실패: {e}") from None
        return asm.response(), time.monotonic() - t0
