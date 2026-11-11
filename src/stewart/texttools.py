"""글 꼴 도구 호출: API 가 tools(함수 호출)를 못 받을 때 쓰는 길(backends 항목 tool_mode = "text").

- 보낼 때: tools 를 요청에 넣지 않고 시스템 지시 끝에 도구 목록과 부르는 꼴을 붙인다. 앞 대화의 도구 호출·결과는 글로 바꾼다
  (assistant 의 tool_calls → 본문 안 ```tool_call 블록, role tool → user 의 [도구 결과] 글).
- 받을 때: 본문의 ```tool_call {"name": …, "arguments": {…}}``` 블록을 tool_calls 로 꺼내고 본문에서 지운다.
엔진·chat 은 지금처럼 tool_calls 를 본다(이 모듈은 백엔드 안에서만 바꾼다).
"""

from __future__ import annotations

import json
import re

BLOCK_RX = re.compile(r"```tool_call\s*\n(.*?)\n?```", re.S)
GUIDE = (
    "\n\n[도구 부르기 — 이 서버는 함수 호출 칸이 없어 글로 부른다]\n"
    "도구를 쓰려면 답 안에 아래 꼴의 블록을 쓴다(여럿이면 블록 여럿). 블록 밖 글은 사람에게 하는 말이다.\n"
    "```tool_call\n{\"name\": \"도구 이름\", \"arguments\": {\"인자\": \"값\"}}\n```\n"
    "도구 결과는 다음 사람 차례에 [도구 결과 …] 로 온다. 도구가 필요 없으면 블록 없이 답한다.\n쓸 수 있는 도구:\n")


def _tool_line(t: dict) -> str:
    f = t.get("function") or t
    params = (f.get("parameters") or {}).get("properties") or {}
    req = set((f.get("parameters") or {}).get("required") or [])
    args = ", ".join(f"{k}{'' if k in req else '?'}: {(v or {}).get('type', 'any')}" for k, v in params.items())
    return f"- {f.get('name')}({args}) — {str(f.get('description') or '').strip()[:300]}"


def to_text(messages: list[dict], tools: list[dict] | None) -> list[dict]:
    """도구 정의·호출·결과를 글로 바꾼 메시지 목록(원본은 바꾸지 않는다)."""
    out: list[dict] = []
    guide = GUIDE + "\n".join(_tool_line(t) for t in tools or []) if tools else ""
    sys_done = False
    for m in messages:
        role = m.get("role")
        if role == "system" and not sys_done:
            out.append({**m, "content": str(m.get("content") or "") + guide})
            sys_done = True
        elif role == "assistant" and m.get("tool_calls"):
            blocks = []
            for tc in m["tool_calls"]:
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (TypeError, ValueError):
                    args = fn.get("arguments")
                blocks.append("```tool_call\n" + json.dumps({"name": fn.get("name"), "arguments": args}, ensure_ascii=False) + "\n```")
            body = (str(m.get("content") or "").strip() + "\n\n" if m.get("content") else "") + "\n".join(blocks)
            out.append({k: v for k, v in m.items() if k not in ("tool_calls", "content")} | {"content": body})
        elif role == "tool":
            out.append({"role": "user", "content": f"[도구 결과 {m.get('name') or ''} {m.get('tool_call_id') or ''}]\n{m.get('content') or ''}"})
        else:
            out.append(m)
    if guide and not sys_done:
        out.insert(0, {"role": "system", "content": guide.strip()})
    return _merge_users(out)


def _merge_users(msgs: list[dict]) -> list[dict]:
    """연이은 user 글(도구 결과 여럿)을 하나로 — user/user 연속을 싫어하는 서버가 있다."""
    out: list[dict] = []
    for m in msgs:
        if out and m.get("role") == "user" and out[-1].get("role") == "user" \
                and isinstance(m.get("content"), str) and isinstance(out[-1].get("content"), str):
            out[-1] = {**out[-1], "content": out[-1]["content"] + "\n\n" + m["content"]}
        else:
            out.append(m)
    return out


def parse(content: str, n0: int = 0) -> tuple[str, list[dict]]:
    """본문에서 tool_call 블록을 꺼낸다 → (블록을 뺀 본문, tool_calls). 깨진 블록은 인자 그대로 넘겨 엔진이 오류로 돌려준다."""
    calls = []
    for i, mm in enumerate(BLOCK_RX.finditer(content or "")):
        raw = mm.group(1).strip()
        try:
            obj = json.loads(raw)
            name = str(obj.get("name") or "")
            args = obj.get("arguments", {})
            args_s = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
        except (ValueError, AttributeError):
            name, args_s = "", raw
        calls.append({"id": f"text_call_{n0 + i}", "type": "function", "function": {"name": name, "arguments": args_s}})
    rest = BLOCK_RX.sub("", content or "").strip() if calls else (content or "")
    return rest, calls


def apply_response(data: dict, n0: int = 0) -> dict:
    """chat 응답(OpenAI 꼴)의 첫 선택지 본문에서 tool_call 블록을 tool_calls 로 옮긴다."""
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return data
    if msg.get("tool_calls"):
        return data
    rest, calls = parse(str(msg.get("content") or ""), n0)
    if calls:
        msg["content"] = rest or None
        msg["tool_calls"] = calls
        data["choices"][0]["finish_reason"] = "tool_calls"
    return data
