"""견고성(판 81): 깨진 툴콜을 고쳐 쓰고, 편집 뒤 고친 자리·문법을 되먹이고, 생각을 되돌려 보내는 손잡이.

- 본문 툴콜(가-1): 모델이 도구 호출을 tool_calls 칸이 아니라 본문에 쓴 것 — Qwen 꼴 `<tool_call>{json}</tool_call>`,
  Qwen3-coder 꼴 `<function=이름><parameter=키>값</parameter></function>`, ```json 블록·본문 전체가 JSON 객체인 것.
  이름이 지금 도구 목록에 있고 인자가 객체일 때만 툴콜로 바꾼다(아니면 예전처럼 다시 요청).
- 인자 JSON 복구(가-2): 코드 울타리·끝 쉼표·둥근 따옴표·글 안 줄바꿈·홑따옴표(파이썬 꼴)·두 번 감싼 JSON 을 한 번 고친다.
  잘려 끝난 인자(생성 상한)는 고치지 않는다 — 괄호를 닫아 주면 반쯤 쓴 파일이 저장된다.
- 편집 되먹임(가-3): edit_file·multi_edit 성공 결과에 고친 자리 앞뒤 몇 줄(줄 번호), edit_file·write_file·multi_edit 결과에
  싼 문법 검사 한 줄(.py ast · .json · .toml). 실행하지 않는다.
- 생각 되돌려 보내기(가-5): STEWART_REASONING_PASSBACK = off(기본) | tool_turns(툴콜 차례만) | all — OpenAI 호환 길에서
  앞 assistant 메시지에 reasoning_content 를 남겨 서버(채팅 틀)가 다시 보게 한다. 실험 손잡이라 실사용·평가 모두 기본 끔.

손잡이: STEWART_TOOLCALL_REPAIR(가-1·가-2)·STEWART_EDIT_FEEDBACK(가-3) = 0|1 이 이긴다, 없으면 실사용(chat·허브) 켬, 평가 길(run) 끔.
"""

from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path

CONTEXT = 3          # 고친 자리 앞뒤 줄
MAX_SHOW = 24        # 되먹임 줄 상한(넘으면 앞뒤만)


def on(knob: str, interactive: bool, env=None) -> bool:
    env = os.environ if env is None else env
    v = env.get(knob)
    return v == "1" if v in ("0", "1") else bool(interactive)


def passback(env=None) -> str:
    env = os.environ if env is None else env
    v = (env.get("STEWART_REASONING_PASSBACK") or "off").strip().lower()
    return v if v in ("off", "tool_turns", "current_turn", "all") else "off"


def keep_reasoning(mode: str, think: str | None, has_calls: bool) -> bool:
    """current_turn(판 116): 툴콜이 있는 걸음의 생각을 남기되, 보낼 때 지난 차례 것은 뺀다(strip_old_turns)."""
    return bool(think) and (mode == "all" or (mode in ("tool_turns", "current_turn") and has_calls))


def strip_old_turns(messages: list[dict]) -> list[dict]:
    """current_turn: 마지막 사람(user) 말 앞의 assistant 생각(reasoning_content)을 뺀 사본. 차례 안 도구 사이 생각만 남는다."""
    last = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    return [({k: v for k, v in m.items() if k != "reasoning_content"} if i < last and "reasoning_content" in m else m)
            for i, m in enumerate(messages)]


# ── 가-2 인자 JSON 복구 ──────────────────────────────────────────────────
FENCE_RX = re.compile(r"^\s*```[\w-]*\s*\n?(.*?)\n?```\s*$", re.S)
TRAIL_COMMA_RX = re.compile(r",(\s*[}\]])")


def repair_args(raw) -> dict | None:
    """고칠 수 있으면 인자 객체, 아니면 None. 원래 꼴 그대로 읽히는 것은 부르는 쪽이 먼저 읽는다."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    m = FENCE_RX.match(s)
    if m:
        s = m.group(1).strip()
    s = s.replace("“", '"').replace("”", '"')
    cands = [s, TRAIL_COMMA_RX.sub(r"\1", s)]
    for c in cands:
        try:
            v = json.loads(c, strict=False)          # strict=False: 글 안의 날 줄바꿈·탭
        except ValueError:
            v = None
        if isinstance(v, str):                       # 두 번 감싼 JSON("{\"a\": 1}")
            try:
                v = json.loads(v, strict=False)
            except ValueError:
                v = None
        if isinstance(v, dict):
            return v
    for c in cands:                                  # 홑따옴표·True/None — 파이썬 글자값으로만 읽는다(실행하지 않는다)
        try:
            v = ast.literal_eval(c)
        except (ValueError, SyntaxError, MemoryError, RecursionError, TypeError):
            continue
        if isinstance(v, dict) and all(isinstance(k, str) for k in v):
            return v
    return None


# ── 가-1 본문 툴콜 ───────────────────────────────────────────────────────
TOOL_CALL_RX = re.compile(r"<\|?tool_call\|?>\s*(.*?)\s*(?:<\|?/tool_call\|?>|$)", re.S)
FUNC_RX = re.compile(r"<function=([\w.-]+)>(.*?)</function>", re.S)
PARAM_RX = re.compile(r"<parameter=([\w.-]+)>\n?(.*?)\n?</parameter>", re.S)
JSON_FENCE_RX = re.compile(r"```(?:json|tool_call|tool_code)?\s*\n(\{.*?\})\s*\n?```", re.S)


def _from_obj(obj, names) -> tuple[str, dict] | None:
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("function"), dict):        # {"type": "function", "function": {...}}
        obj = obj["function"]
    name = obj.get("name") or obj.get("tool") or obj.get("tool_name")
    if not isinstance(name, str) or name not in names:
        return None
    args = obj.get("arguments", obj.get("parameters", obj.get("args", {})))
    if isinstance(args, str):
        args = json.loads(args) if _is_json(args) else repair_args(args)
    return (name, args) if isinstance(args, dict) else None


def _is_json(s: str) -> bool:
    try:
        json.loads(s)
        return True
    except ValueError:
        return False


def _xml_call(body: str, names) -> tuple[str, dict] | None:
    m = FUNC_RX.search(body)
    if not m or m.group(1) not in names:
        return None
    args = {}
    for k, v in PARAM_RX.findall(m.group(2)):
        vs = v.strip("\n")
        try:                                          # 숫자·참거짓·목록은 JSON 값으로, 아니면 글 그대로
            args[k] = json.loads(vs) if vs[:1] in "[{" or vs in ("true", "false", "null") or re.fullmatch(r"-?\d+(\.\d+)?", vs) else vs
        except ValueError:
            args[k] = vs
    return m.group(1), args


def extract(content: str, names) -> tuple[str, list[dict]]:
    """본문에서 도구 호출을 꺼낸다 → (호출을 뺀 본문, tool_calls). 하나도 확실하지 않으면 (본문, [])."""
    text = content or ""
    names = set(names or ())
    if not text.strip() or not names:
        return text, []
    found: list[tuple[str, dict]] = []
    spans: list[tuple[int, int]] = []
    for mm in TOOL_CALL_RX.finditer(text):
        body = mm.group(1)
        got = _xml_call(body, names) if "<function=" in body else None
        if got is None:
            obj = repair_args(body) if not _is_json(body) else json.loads(body)
            got = _from_obj(obj, names)
        if got is None:
            return text, []                          # 꼴은 툴콜인데 못 읽는다 — 하나라도 틀리면 통째로 손대지 않는다
        found.append(got)
        spans.append(mm.span())
    if not found and "<function=" in text:
        for mm in FUNC_RX.finditer(text):
            got = _xml_call(mm.group(0), names)
            if got is None:
                return text, []
            found.append(got)
            spans.append(mm.span())
    if not found:
        for mm in JSON_FENCE_RX.finditer(text):
            got = _from_obj(repair_args(mm.group(1)), names)
            if got:
                found.append(got)
                spans.append(mm.span())
    if not found:
        s = text.strip()
        if s.startswith("{") and s.endswith("}"):
            got = _from_obj(repair_args(s), names)
            if got:
                found.append(got)
                spans.append((0, len(text)))
    if not found:
        return text, []
    rest, last = [], 0
    for a, b in spans:
        rest.append(text[last:a])
        last = b
    rest.append(text[last:])
    calls = [{"id": f"repaired_{os.urandom(4).hex()}", "type": "function",
              "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}} for n, a in found]
    return "".join(rest).strip(), calls


# ── 가-3 편집 되먹임 ─────────────────────────────────────────────────────
def syntax_line(path: Path) -> str:
    """싼 문법 검사 한 줄(모르는 꼴·못 읽으면 "")."""
    suf = path.suffix.lower()
    if suf not in (".py", ".json", ".toml"):
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    try:
        if suf == ".py":
            ast.parse(text, filename=path.name)
        elif suf == ".json":
            json.loads(text)
        else:
            import tomllib
            tomllib.loads(text)
    except SyntaxError as e:
        return f"문법 검사(.py): 오류 — {e.lineno}줄: {e.msg}"
    except ValueError as e:                           # json.JSONDecodeError·tomllib.TOMLDecodeError
        return f"문법 검사({suf}): 오류 — {str(e)[:200]}"
    return f"문법 검사({suf}): 통과"


def _around(text: str, needle: str) -> str:
    i = text.find(needle) if needle else -1
    if i < 0:
        return ""
    lines = text.split("\n")
    a = text.count("\n", 0, i)
    b = a + needle.count("\n")
    lo, hi = max(0, a - CONTEXT), min(len(lines), b + CONTEXT + 1)
    rows = [f"{n + 1:>5}\t{lines[n]}" for n in range(lo, hi)]
    if len(rows) > MAX_SHOW:
        rows = rows[:MAX_SHOW // 2] + [f"  … {len(rows) - MAX_SHOW}줄 생략"] + rows[-MAX_SHOW // 2:]
    return f"고친 자리(줄 {a + 1}~{b + 1}, 앞뒤 {CONTEXT}줄):\n" + "\n".join(rows)


def edit_feedback(workdir: Path, name: str, args: dict) -> str:
    """도구 결과 끝에 붙일 글(없으면 ""). 파일을 다시 읽기만 한다."""
    if name not in ("edit_file", "write_file", "multi_edit") or not isinstance(args, dict):
        return ""
    p = args.get("path")
    if not isinstance(p, str) or not p:
        return ""
    path = Path(p) if Path(p).is_absolute() else Path(workdir) / p
    out = []
    if name == "edit_file" and isinstance(args.get("new"), str):
        try:
            near = _around(path.read_text(encoding="utf-8"), args["new"])
        except (OSError, UnicodeDecodeError):
            near = ""
        if near:
            out.append(near)
    syn = syntax_line(path)
    if syn:
        out.append(syn)
    return ("\n\n" + "\n".join(out)) if out else ""
