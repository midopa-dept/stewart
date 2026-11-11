"""기억 정리(판 88, SW-40): 규칙으로 나눈 세 갈래(방침·프로필·자료)를 모델이 한 번 읽고 고칠 것을 **제안**한다 — 사람이 항목별로 받는다.

제안 꼴(모델 답의 JSON 줄, 하나에 하나):
  {"op": "move",    "id": "…", "to": "policy|profile|knowledge", "why": "…"}      갈래 옮김
  {"op": "merge",   "ids": ["…", "…"], "text": "합친 한 문장", "why": "…"}         겹침 합치기(첫 id 에 남기고 나머지는 지움)
  {"op": "drop",    "id": "…", "why": "…"}                                       낡았거나 이제 맞지 않음(지움 후보)
  {"op": "rewrite", "id": "…", "text": "짧게 다시 쓴 한 문장", "why": "…"}          뜻은 그대로 짧게
코드가 다시 본다: 없는 id·모르는 갈래·비밀 꼴 글·300자 넘는 글·같은 id 를 두 번 만지는 제안은 버린다. 모델을 한 번만 부른다(도구 없이, 생각 끔).
적용(apply): 받은 것만, 그 전에 세 갈래 파일 사본을 consolidate-<시각>/ 에 두고 한 일을 consolidate-<시각>.md 로. 되돌리기(revert): 그 사본으로.
`stewart dream` 과는 다루는 층이 다르다 — dream 은 프로젝트 메모(L1)→대상 쪽(L2)→MEMORY.md(L3), 이것은 사용자 세 갈래(policy.md·profile.md·knowledge/).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

OPS = ("move", "merge", "drop", "rewrite")
MAX_ITEMS = 120
TEXT_MAX = 300
LABEL = {"move": "갈래 옮김", "merge": "합치기", "drop": "지움 후보", "rewrite": "짧게 다시 쓰기"}

PROMPT = """[기억 정리] 아래는 사용자 기억(세 갈래)이다. 고칠 것만 제안한다 — 고칠 것이 없으면 아무것도 쓰지 않는다.
갈래 뜻: policy = 어떻게 일할지(사람이 "앞으로·항상·하지 마"로 오래 지키라고 한 것) · profile = 사용자에 대한 짧은 사실(언어·역할·기기·선호, 120자 안)
· knowledge = 고쳐 준 사실·참고·프로젝트 결정(지시문에 싣지 않고 찾아 쓴다). 애매하면 knowledge.
할 수 있는 것(JSON 줄만, 설명 없이, 한 줄에 하나, why 는 한 줄 까닭):
{{"op":"move","id":"<id>","to":"policy|profile|knowledge","why":"…"}}       갈래가 틀린 것
{{"op":"merge","ids":["<id>","<id>"],"text":"합친 한 문장","why":"…"}}        뜻이 겹치는 것
{{"op":"drop","id":"<id>","why":"…"}}                                    낡았거나 서로 부딪혀 이제 맞지 않는 것
{{"op":"rewrite","id":"<id>","text":"짧게 다시 쓴 한 문장","why":"…"}}       길거나 군더더기 — 뜻은 그대로
지키기: 사람이 한 금지·지시의 뜻을 바꾸지 않는다. 비밀 값·주소·토큰은 쓰지 않는다. 확실하지 않으면 제안하지 않는다.
기억(id · 갈래 · 범위 · 글):
{items}"""


def items_text(items: list[dict]) -> str:
    out = []
    for x in items[:MAX_ITEMS]:
        sc = "이 프로젝트" + (f"({x.get('where')})" if x.get("where") else "") if x.get("scope") == "project" else "모든 프로젝트"
        out.append(f"{x['id']} · {x['kind']} · {sc} · {x['text']}")
    return "\n".join(out) or "(없음)"


def parse(text: str, items: list[dict], secret_rx=None) -> list[dict]:
    """모델 답 → 확인한 제안 목록(번호 n 이 붙는다). 틀린 것은 버린다."""
    from .memstore import KINDS
    if secret_rx is None:
        from .memory import _SECRETISH as secret_rx
    known = {x["id"]: x for x in items}
    touched: set = set()
    out = []
    for line in (text or "").splitlines():
        line = line.strip().strip("`").strip()
        if not line.startswith("{"):
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        op = o.get("op")
        if op not in OPS:
            continue
        why = " ".join(str(o.get("why") or "").split())[:160]
        ids = [o.get("id")] if op != "merge" else list(o.get("ids") or [])
        if not ids or not all(isinstance(i, str) and i in known for i in ids) or touched & set(ids) or len(set(ids)) != len(ids):
            continue
        p = {"op": op, "ids": ids, "why": why}
        if op == "move":
            if o.get("to") not in KINDS or o["to"] == known[ids[0]]["kind"]:
                continue
            p["to"] = o["to"]
        if op in ("merge", "rewrite"):
            t = " ".join(str(o.get("text") or "").split())
            if not t or len(t) > TEXT_MAX or secret_rx.search(t):
                continue
            if op == "merge" and len(ids) < 2:
                continue
            p["text"] = t
        p["before"] = [known[i]["text"] for i in ids]
        p["kinds"] = [known[i]["kind"] for i in ids]
        touched |= set(ids)
        p["n"] = len(out) + 1
        out.append(p)
    return out


def propose(mem, backend, chat_fn=None) -> list[dict]:
    """모델을 한 번 불러 제안을 받는다. 세 갈래가 아니거나 기억이 없으면 []."""
    if not mem.md():
        return []
    items = mem.remembered()
    if not items:
        return []
    if chat_fn is None:
        from .backend import chat as chat_fn
    resp, _dt = chat_fn(backend, [{"role": "system", "content": "너는 기억을 정리하는 도우미다. 지시한 꼴의 JSON 줄만 쓴다."},
                                  {"role": "user", "content": PROMPT.format(items=items_text(items))}],
                        None, max_tokens=1500, thinking=False)
    msg = ((resp.get("choices") or [{}])[0].get("message") or {})
    return parse(msg.get("content") or "", items)


def describe(p: dict) -> str:
    head = f"{p['n']}. [{LABEL[p['op']]}]"
    if p["op"] == "move":
        return f"{head} {p['before'][0]}  ({p['kinds'][0]} → {p['to']}) — {p['why']}"
    if p["op"] == "merge":
        return f"{head} " + " + ".join(p["before"]) + f"  →  {p['text']} — {p['why']}"
    if p["op"] == "drop":
        return f"{head} {p['before'][0]} — {p['why']}"
    return f"{head} {p['before'][0]}  →  {p['text']} — {p['why']}"


def backup(mem) -> Path:
    d = mem.store.dir
    day = datetime.now().strftime("%Y%m%d-%H%M%S")
    b = d / f"consolidate-{day}"
    b.mkdir(parents=True, exist_ok=True)
    for name in ("policy.md", "profile.md"):
        if (d / name).is_file():
            shutil.copy2(d / name, b / name)
    if (d / "knowledge").is_dir():
        shutil.copytree(d / "knowledge", b / "knowledge", dirs_exist_ok=True)
    return b


def apply(mem, proposals: list[dict], accept: list[int] | None = None) -> dict:
    """받은 번호(accept, None 이면 전부)만 적용. {applied, skipped, backup, log}."""
    take = [p for p in proposals if accept is None or p["n"] in accept]
    if not take:
        return {"applied": [], "skipped": [p["n"] for p in proposals], "backup": None, "log": None}
    b = backup(mem)
    done = []
    for p in take:
        ok = False
        if p["op"] == "move":
            ok = mem.update(p["ids"][0], p["before"][0], source="human", kind=p["to"]) is not None
        elif p["op"] == "rewrite":
            ok = mem.update(p["ids"][0], p["text"], source="human") is not None
        elif p["op"] == "drop":
            ok = mem.forget(p["ids"][0])
        elif p["op"] == "merge":
            ok = mem.update(p["ids"][0], p["text"], source="human") is not None
            for i in p["ids"][1:]:
                ok = mem.forget(i) and ok
        if ok:
            done.append(p["n"])
    log = mem.store.dir / f"{b.name}.md"
    lines = [f"# 기억 정리 — {datetime.now().isoformat(timespec='seconds')}", "",
             f"받은 것 {len(done)}개 · 둔 것 {len(proposals) - len(take)}개. 적용 전 사본: {b.name}/. "
             f"되돌리기: `stewart memory consolidate --revert {b.name}`", ""]
    lines += [("- ✓ " if p["n"] in done else "- ✗ ") + describe(p) for p in take]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"applied": done, "skipped": [p["n"] for p in proposals if p not in take], "backup": str(b), "log": str(log)}


def revert(mem, name: str) -> list[str]:
    """consolidate-<시각>/ 사본으로 세 갈래 파일을 되돌린다. 되돌린 이름들."""
    d = mem.store.dir
    b = d / name
    if not name.startswith("consolidate-") or not b.is_dir():
        raise FileNotFoundError(f"사본이 없다: {b}")
    out = []
    for nm in ("policy.md", "profile.md"):
        if (b / nm).is_file():
            shutil.copy2(b / nm, d / nm)
            out.append(nm)
        elif (d / nm).is_file():
            (d / nm).unlink()        # 그때 없던 파일
            out.append(nm + "(지움)")
    if (d / "knowledge").is_dir():
        shutil.rmtree(d / "knowledge")
    if (b / "knowledge").is_dir():
        shutil.copytree(b / "knowledge", d / "knowledge")
        out.append("knowledge/")
    return out
