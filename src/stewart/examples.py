"""예시 은행(판 92, SW-41 ④ 판 A — 학습 없는 자가발전): 지난 일에서 잘 된 꼴을 사람이 받아 두고, 비슷한 일이 오면 하나만 붙인다.

자리: <기억>/user/examples.md(기억 세 갈래와 같은 폴더, 인스턴스마다 따로). 사람이 손으로 고쳐도 된다. 꼴:

    ## ex-<id> · <상황 한 줄>
    <!-- id:<id> ts:<시각> src:<기록 가명>#<차례> -->
    상황: …
    한 일: …(도구 호출 꼴 — 예: run_shell "pytest -q" → 실패 줄을 읽고 edit_file)
    결과: …

붙이기: 차례 시작 때 사람 말의 낱말(docsearch 와 같은 낱말 꼴)이 가장 많이 겹치는 예시를 찾아 몫이 MIN_SCORE 이상인 **하나만**
사람 말 끝에 [예시] 블록으로 붙인다(MAX_TOKENS 넘으면 자름). 지시문(시스템)은 그대로라 접두 캐시가 깨지지 않는다.
기록 사건 example_used(id·점수). 끄기: STEWART_EXAMPLES=0. 평가 실행(엔진)에는 붙이지 않는다(chat·허브만).
예시를 만드는 길은 판 B(/improve — 모델 제안 → 사람이 받기)와 사람 손.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime
from pathlib import Path

FILE = "examples.md"
MIN_SCORE = 0.4     # 사람 말 낱말 가운데 예시에 걸린 몫
MIN_HITS = 2
MAX_TOKENS = 600
HEAD_RX = re.compile(r"^##\s+(ex-[0-9a-f]{6,16})\s*·?\s*(.*)$")
META_RX = re.compile(r"<!--(.*?)-->")
NOTE = "[예시] 비슷한 지난 일에서 사람이 좋다고 받아 둔 꼴이다. 그대로 따르지 말고 이번 일에 맞는지 보고 참고만 한다:\n"


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return env.get("STEWART_EXAMPLES", "1") != "0"


def path_of(mem_dir: Path) -> Path:
    return Path(mem_dir) / FILE


def load(mem_dir: Path) -> list[dict]:
    """examples.md → [{id, title, body, meta}] (꼴이 틀린 덩어리는 건너뛴다)."""
    p = path_of(mem_dir)
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out, cur = [], None
    for ln in lines:
        m = HEAD_RX.match(ln)
        if m:
            cur = {"id": m.group(1), "title": m.group(2).strip(), "body": [], "meta": {}}
            out.append(cur)
            continue
        if cur is None:
            continue
        mm = META_RX.search(ln)
        if mm and not cur["body"]:
            for kv in mm.group(1).split():
                if ":" in kv:
                    k, v = kv.split(":", 1)
                    cur["meta"][k] = v
            continue
        cur["body"].append(ln)
    for x in out:
        x["body"] = "\n".join(x["body"]).strip()
    return [x for x in out if x["body"]]


def add(mem_dir: Path, title: str, body: str, src: str = "") -> dict:
    """예시 하나를 끝에 붙인다(같은 제목·본문이면 그대로 둔다). 만든 항목."""
    title = " ".join(str(title).split())[:120]
    body = str(body).strip()
    eid = "ex-" + hashlib.sha256((title + "\n" + body).encode("utf-8")).hexdigest()[:10]
    for x in load(mem_dir):
        if x["id"] == eid:
            return x
    p = path_of(mem_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    head = "" if p.is_file() and p.stat().st_size else "# 예시 은행 — 비슷한 일이 오면 하나만 붙는다(stewart, 손으로 고쳐도 된다)\n"
    meta = f"<!-- id:{eid} ts:{datetime.now().isoformat(timespec='seconds')}" + (f" src:{src}" if src else "") + " -->"
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"{head}\n## {eid} · {title}\n{meta}\n{body}\n")
    return {"id": eid, "title": title, "body": body, "meta": {}}


def remove(mem_dir: Path, eid: str) -> bool:
    p = path_of(mem_dir)
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    out, skip, hit = [], False, False
    for ln in lines:
        m = HEAD_RX.match(ln)
        if m:
            skip = m.group(1) == eid
            hit = hit or skip
        if not skip:
            out.append(ln)
    if hit:
        p.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    return hit


def pick(items: list[dict], query: str) -> tuple[dict, float] | None:
    """사람 말에 가장 가까운 예시 하나. 점수 = 사람 말의 낱말(두 글자 이상) 가운데 예시에 걸린 몫 — 낱말의 docsearch 낱말 꼴
    (한글은 두 글자씩도) 하나라도 예시에 있으면 걸린 것. MIN_HITS 낱말 이상·몫 MIN_SCORE 이상일 때만
    (예시가 몇 개뿐이면 BM25 의 idf 가 거의 0 이라 몫으로 잰다). 없으면 None."""
    from .docsearch import tokens
    words = [w for w in dict.fromkeys(re.findall(r"\w+", str(query or "").lower())) if len(w) >= 2]
    if not items or not words:
        return None
    best = None
    for x in items:
        d = set(tokens(x["title"] + "\n" + x["body"]))
        hit = sum(1 for w in words if set(tokens(w)) & d)
        sc = hit / len(words)
        if hit >= MIN_HITS and sc >= MIN_SCORE and (best is None or (sc, hit) > best[1:]):
            best = (x, sc, hit)
    return (best[0], round(best[1], 3)) if best else None


def block(x: dict) -> str:
    """사람 말 끝에 붙일 글(MAX_TOKENS 어림 — 글자 3개 ≈ 1토큰으로 자름)."""
    body = x["body"]
    cap = MAX_TOKENS * 3
    if len(body) > cap:
        body = body[:cap] + "…(줄임)"
    return "\n\n" + NOTE + f"## {x['title']}\n{body}"
