"""실사용 기록을 학습 자료로(판 89, SW-39 첫 조각) — 표시·내보내기·미리 보기. 모델을 부르지 않는다.

표시(기록 사건 train_label — 내용은 안 바꾼다):
  good·bad          사람이 그 차례를 좋음·나쁨으로(허브 답 줄의 👍·👎, chat 의 /good·/bad [메모])
  corrected (rule)  사람의 다음 말이 교정 꼴이면 바로 앞 모델 차례에 자동으로("아니", "그게 아니라", "다시 해", "틀렸", …)
  clear·uncorrect   사람이 표시를 뗌(잘못 붙은 "교정됨" 포함)
내보내기 `stewart train export --out <폴더> [--since YYYY-MM-DD] [--select good|after_fix|all] [--runs-dir …]`:
  실행 기록(events.jsonl)에서 차례를 다시 엮어 JSONL 한 줄 = 한 차례 {"messages": [...], "meta": {...}} 로 쓴다(OpenAI chat 꼴, tool_calls·tool 역할 그대로).
  고르기: good(기본) = 사람 👍 차례만 · after_fix = 교정 표시가 붙은 차례는 빼고, 교정 뒤 다시 한 차례가 answered 로 끝났으면 그 차례 ·
  all = 전부(분석용). 엔진 넛지([엔진] 줄)와 실패한 도구 호출은 기본으로 뺀다. 큰 도구 결과는 앞뒤만, 그림은 자리표만.
  비밀: nostore.scrub + backend.redact + 홈 경로·사용자 이름 가림. 평가 과제 지문 해시 목록(--exclude-hashes)과 같은 첫 사람 말은 뺀다.
  화면에는 개수·통계·걸러 낸 까닭별 수만 찍는다(내용은 안 찍는다).
미리 보기 `stewart train show <폴더> [-n 3]` — 사람이 몇 건을 훑는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
from datetime import date
from pathlib import Path

LABELS = ("good", "bad", "corrected", "clear", "uncorrect")
CORRECTION_RX = re.compile(r"^\s*(아니(요|야|오)?[\s,.!]|아닌데|그게\s*아니|그거\s*말고|다시\s*해|틀렸|잘못\s*(했|됐|알)|왜\s*\S+\s*했|"
                           r"no[,.!\s]|wrong|that'?s\s+not|not\s+what\s+i)", re.I)
TOOL_KEEP = 4000


def is_correction(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and len(t) <= 600 and bool(CORRECTION_RX.search(t))


def norm_hash(text: str) -> str:
    return hashlib.sha256(" ".join(str(text or "").split()).lower().encode("utf-8")).hexdigest()[:16]


def _plain(c) -> str:
    if isinstance(c, list):
        return "\n".join(str(x.get("text") or "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return str(c or "")


# ── 차례 다시 엮기 ─────────────────────────────────────────────────────
def turns_of(run_dir: Path) -> list[dict]:
    """events.jsonl → [{turn, user, messages, reason, labels, tools}] (messages 는 사람 말부터 그 차례 끝까지)."""
    f = Path(run_dir) / "events.jsonl"
    if not f.is_file():
        return []
    turns: dict[int, dict] = {}
    cur = None
    labels: dict[int, list] = {}
    system = ""
    tools = []
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        k = e.get("kind")
        if k == "run_start":
            msgs = e.get("messages") or []
            if msgs and msgs[0].get("role") == "system":
                system = str(msgs[0].get("content") or "")
            tools = (e.get("meta") or {}).get("tools") or []
        elif k == "user":
            t = int(e.get("turn") or 0)
            seen = e.get("content") or ""          # 모델이 본 꼴(엔진이 앞에 붙인 환경·모드 안내 포함)
            cur = turns.setdefault(t, {"turn": t, "user": e.get("raw") or seen, "messages": [{"role": "user", "content": seen}],
                                       "reason": None})
        elif k == "step" and cur is not None:
            for m in e.get("new_messages") or []:
                if m.get("role") == "user" and len(cur["messages"]) == 1 and _plain(m.get("content")) == cur["messages"][0]["content"]:
                    continue   # 차례 첫 사람 말이 다시 실린 것
                cur["messages"].append(m)
        elif k == "turn_end":
            t = int(e.get("turn") or 0)
            if t in turns:
                turns[t]["reason"] = e.get("reason")
        elif k == "train_label":
            labels.setdefault(int(e.get("turn") or 0), []).append(e.get("label"))
    out = []
    for t in sorted(turns):
        x = turns[t]
        st: set = set()
        for lb in labels.get(t, []):
            if lb in ("good", "bad"):
                st -= {"good", "bad"}
                st.add(lb)
            elif lb == "corrected":
                st.add("corrected")
            elif lb == "uncorrect":
                st.discard("corrected")
            elif lb == "clear":
                st -= {"good", "bad"}
        x["labels"] = sorted(st)
        x["system"] = system
        x["tools"] = tools
        out.append(x)
    return out


def select(turns: list[dict], how: str) -> list[dict]:
    if how == "all":
        return list(turns)
    if how == "good":
        return [t for t in turns if "good" in t["labels"]]
    if how == "after_fix":   # 교정된 차례 바로 뒤의 차례가 answered 로 끝났고 그 차례 자체는 교정·나쁨이 아니면
        out = []
        for i, t in enumerate(turns):
            prev = turns[i - 1] if i else None
            if prev is not None and "corrected" in prev["labels"] and t.get("reason") == "answered" \
                    and not ({"corrected", "bad"} & set(t["labels"])):
                out.append(t)
        return out
    raise ValueError(f"모르는 고르기: {how}")


# ── 다듬기·가림 ──────────────────────────────────────────────────────
def _scrub_text(s: str, home: str, user: str) -> str:
    from .backend import redact
    from .nostore import scrub
    s = redact(scrub(str(s)))
    if home:
        s = s.replace(home, "~")
    if user and len(user) >= 3:
        s = re.sub(rf"\b{re.escape(user)}\b", "<user>", s)
    return s


def clean(t: dict, keep_engine: bool = False, home: str = "", user: str = "") -> tuple[list[dict], dict]:
    """학습용 메시지로: 엔진 넛지·실패한 도구 호출(과 그 결과)을 빼고, 큰 결과는 앞뒤만, 그림은 자리표, 비밀 가림. (메시지, 센 것)."""
    cnt = {"engine_notes": 0, "failed_calls": 0, "clipped": 0, "images": 0}
    msgs = [{"role": "system", "content": _scrub_text(t.get("system") or "", home, user)}] if t.get("system") else []
    failed_ids = set()
    for m in t["messages"]:
        if m.get("role") == "tool":
            c = str(m.get("content") or "")
            if c.startswith(("인자 JSON 파싱 실패", "모르는 도구", "실행하지 않음")):
                failed_ids.add(m.get("tool_call_id"))
    for m in t["messages"]:
        role = m.get("role")
        c = m.get("content")
        if isinstance(c, list):
            cnt["images"] += sum(1 for x in c if isinstance(x, dict) and x.get("type") == "image_url")
            c = "\n".join(str(x.get("text") or "[그림]") if isinstance(x, dict) else str(x) for x in c)
        if role == "user" and not keep_engine and str(c or "").startswith("[엔진]"):
            cnt["engine_notes"] += 1
            continue
        if role == "tool" and m.get("tool_call_id") in failed_ids:
            cnt["failed_calls"] += 1
            continue
        out = {"role": role}
        if c is not None:
            c = str(c)
            if role == "tool" and len(c) > TOOL_KEEP:
                c = c[:TOOL_KEEP // 2] + f"\n…({len(c) - TOOL_KEEP}자 줄임)…\n" + c[-TOOL_KEEP // 2:]
                cnt["clipped"] += 1
            if role == "user" and not keep_engine and "\n\n[엔진]" in c:
                c = c.split("\n\n[엔진]")[0]
            out["content"] = _scrub_text(c, home, user)
        else:
            out["content"] = None
        if m.get("tool_calls"):
            calls = [tc for tc in m["tool_calls"] if tc.get("id") not in failed_ids]
            if not calls and out.get("content") is None:
                continue
            out["tool_calls"] = [{**tc, "function": {**tc.get("function", {}),
                                                     "arguments": _scrub_text((tc.get("function") or {}).get("arguments") or "", home, user)}}
                                 for tc in calls]
        if role == "tool":
            out["tool_call_id"] = m.get("tool_call_id")
            if m.get("name"):
                out["name"] = m.get("name")
        msgs.append(out)
    return msgs, cnt


def run_dirs(root: Path, since: date | None = None) -> list[Path]:
    out = []
    for d in sorted(Path(root).glob("*")):
        if not (d / "events.jsonl").is_file():
            continue
        if since is not None:
            try:
                if date(int(d.name[:4]), int(d.name[4:6]), int(d.name[6:8])) < since:
                    continue
            except ValueError:
                continue
        out.append(d)
    return out


def export(root: Path, out: Path, how: str = "good", since: date | None = None, keep_engine: bool = False,
           exclude_hashes: set | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    home, user = os.path.expanduser("~"), os.environ.get("USER") or ""
    stats = {"runs": 0, "turns_seen": 0, "picked": 0, "excluded_eval": 0, "written": 0, "messages": 0, "chars": 0,
             "engine_notes": 0, "failed_calls": 0, "clipped": 0, "images": 0, "labels": {}}
    with open(out / "train.jsonl", "w", encoding="utf-8") as f:
        for d in run_dirs(root, since):
            ts = turns_of(d)
            if not ts:
                continue
            stats["runs"] += 1
            stats["turns_seen"] += len(ts)
            for t in ts:
                for lb in t["labels"]:
                    stats["labels"][lb] = stats["labels"].get(lb, 0) + 1
            for t in select(ts, how):
                stats["picked"] += 1
                if exclude_hashes and norm_hash(t["user"]) in exclude_hashes:
                    stats["excluded_eval"] += 1
                    continue
                msgs, cnt = clean(t, keep_engine, home, user)
                for k, v in cnt.items():
                    stats[k] += v
                rec = {"messages": msgs, "meta": {"run": d.name, "turn": t["turn"], "labels": t["labels"], "reason": t.get("reason")}}
                line = json.dumps(rec, ensure_ascii=False)
                f.write(line + "\n")
                stats["written"] += 1
                stats["messages"] += len(msgs)
                stats["chars"] += len(line)
    (out / "stats.json").write_text(json.dumps({**stats, "select": how, "since": str(since) if since else None},
                                               ensure_ascii=False, indent=1), encoding="utf-8")
    return stats


def show(folder: Path, n: int = 3, seed: int | None = None) -> str:
    lines = (Path(folder) / "train.jsonl").read_text(encoding="utf-8").splitlines()
    rnd = random.Random(seed)
    pick = rnd.sample(lines, min(n, len(lines))) if lines else []
    out = [f"{len(lines)}건 가운데 {len(pick)}건"]
    for ln in pick:
        r = json.loads(ln)
        out.append(f"\n── {r['meta']['run']} 차례 {r['meta']['turn']} {r['meta']['labels']} ({r['meta'].get('reason')})")
        for m in r["messages"]:
            if m["role"] == "system":
                continue
            if m["role"] == "tool":
                out.append(f"  [도구 결과 {m.get('name') or ''}] {str(m.get('content') or '')[:160]!r}")
            elif m.get("tool_calls"):
                out.append("  [모델 → 도구] " + ", ".join((tc.get("function") or {}).get("name", "?") for tc in m["tool_calls"])
                           + (f" — {str(m.get('content'))[:120]!r}" if m.get("content") else ""))
            else:
                out.append(f"  [{'사람' if m['role'] == 'user' else '모델'}] {str(m.get('content') or '')[:300]}")
    return "\n".join(out)
