"""실사용 기록을 학습 자료로(판 89, SW-39 첫 조각) — 표시·내보내기·미리 보기. 모델을 부르지 않는다.

표시(기록 사건 train_label — 내용은 안 바꾼다):
  good·bad          사람이 그 차례를 좋음·나쁨으로(허브 답 줄의 👍·👎, chat 의 /good·/bad [메모])
  corrected (rule)  사람의 다음 말이 교정 꼴이면 바로 앞 모델 차례에 자동으로("아니", "그게 아니라", "다시 해", "틀렸", …)
  clear·uncorrect   사람이 표시를 뗌(잘못 붙은 "교정됨" 포함)
내보내기 `stewart train export --out <폴더> [--since YYYY-MM-DD] [--select good|after_fix|all] [--runs-dir …]`:
  실행 기록(events.jsonl)에서 차례를 다시 엮어 JSONL 한 줄 = 한 차례 {"messages": [...], "meta": {...}} 로 쓴다(OpenAI chat 꼴, tool_calls·tool 역할 그대로).
  고르기: good(기본) = 사람 👍 차례만 · after_fix = 교정 뒤 다시 한 차례가 answered 로 끝났으면 그 차례(교정된 앞 차례를 train false 로 앞에 싣는다) ·
  all = 전부(분석용). 엔진 넛지([엔진] 줄)와 실패한 도구 호출은 기본으로 뺀다. 큰 도구 결과는 앞뒤만, 그림은 자리표만.
  비밀: nostore.scrub + backend.redact + 홈 경로·사용자 이름 가림. 평가 과제 지문 해시 목록(--exclude-hashes)과 같은 첫 사람 말은 뺀다.
  화면에는 개수·통계·걸러 낸 까닭별 수만 찍는다(내용은 안 찍는다).
미리 보기 `stewart train show <폴더> [-n 3]` — 사람이 몇 건을 훑는다.

꼴(판 90, "stewart-train/1" — 학습 도구 prep 의 입력과 맞춤): 한 줄 = 한 차례 {"messages", "tools", "meta"}.
  assistant 메시지에 reasoning_content(있으면)·tool_calls 원문과 "train" 불리언(교정됨·나쁨 차례는 false = 손실에서 가림, 빼지 않는다).
  tools = 그 차례에 실제로 보낸 도구 정의(기록 사건 tool_defs). meta = schema·source·engine_version·profile·run(가명)·turn·track·
  backend{name, model, provider, kind}·license_tier·license_table·labels·reason·task_hash(64자)·test_pass·tool_calls·tool_errors·
  secrets_masked·compacted. 약관 등급이 train-ok 가 아닌 차례는 기본으로 빼고 수만 센다(--include-tier 로 풂).
  대화형(chat·허브) 기록은 user·turn_end 사건으로, 한 번 실행(stewart run) 기록은 run_start 의 사람 말과 run_end 로 한 차례를 엮는다.
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
    """과제 지문 해시: 공백 접기·소문자·sha256 64자(평가 쪽 해시 목록도 이 함수로 만든다)."""
    return hashlib.sha256(" ".join(str(text or "").split()).lower().encode("utf-8")).hexdigest()


SCHEMA = "stewart-train/1"
# 약관 등급(출력으로 다른 모델을 학습해도 되는가) — 법률 판단이 아니라 기본값. 약관은 바뀌므로 날짜를 적고, 설정 파일로 덮는다.
LICENSE_TABLE = "2026-11-12"
TIER_RULES = [   # (정규식 — "이름 모델 제공자" 에 대어 봄, 등급). 위에서부터 첫 맞음
    (r"anthropic|claude", "private-eval-only"),
    (r"openai\.com|\bgpt-|\bo[134]-|chatgpt", "private-eval-only"),
    (r"googleapis|generativelanguage|gemini|vertex", "private-eval-only"),
    (r"\bz\.ai\b|bigmodel|zhipu|\bglm-.*z\.ai", "private-eval-only"),
    (r"exaone|lgresearch|lgai", "private-eval-only"),
    (r"api\.deepseek\.com", "train-ok"),
]
TIERS = ("train-ok", "private-eval-only", "unknown")


def tier_rules(path: Path | None = None) -> list:
    """설정 파일([[rule]] match = "정규식", tier = "…")이 있으면 그 규칙을 앞에 둔다."""
    rules = []
    if path is not None:
        import tomllib
        for r in tomllib.loads(Path(path).read_text(encoding="utf-8")).get("rule", []):
            if r.get("tier") in TIERS and r.get("match"):
                rules.append((str(r["match"]), r["tier"]))
    return rules + TIER_RULES


def tier_of(backend: dict, rules: list | None = None) -> str:
    """로컬(이 기계·사설망)에서 돈 모델은 자기 자신 → train-ok. 바깥 API 는 표로, 표에 없으면 unknown."""
    key = " ".join(str(backend.get(k) or "") for k in ("name", "model", "provider")).lower()
    for rx, t in (rules if rules is not None else TIER_RULES):
        if re.search(rx, key, re.I):
            return t
    return "train-ok" if backend.get("kind") == "local" else "unknown"


def backend_meta(b: dict) -> dict:
    """기록의 백엔드 표 → 내보낼 꼴(주소는 싣지 않는다 — 사설 이름·IP 가 들어 있을 수 있다)."""
    import ipaddress
    from urllib.parse import urlsplit
    ext = bool(b.get("external"))
    prov = str(b.get("provider") or "") if ext else "local"
    if ext and not prov:
        prov = urlsplit(str(b.get("base_url") or "")).hostname or ""
    try:
        ipaddress.ip_address(prov)
        prov = "ip-host"
    except ValueError:
        pass
    return {"name": b.get("name"), "model": b.get("model"), "provider": prov, "kind": "api" if ext else "local"}


def _plain(c) -> str:
    if isinstance(c, list):
        return "\n".join(str(x.get("text") or "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return str(c or "")


# ── 차례 다시 엮기 ─────────────────────────────────────────────────────
def _with_reasoning(msgs: list, srec: dict | None) -> list:
    """기록의 걸음 응답(response)에 있는 생각 글을 그 걸음의 assistant 메시지에 붙인다(되보내기가 꺼져 문맥에서 뺐어도)."""
    r = (srec or {}).get("reasoning_content")
    if not r:
        return msgs
    out = []
    for m in msgs:
        if m.get("role") == "assistant" and not m.get("reasoning_content"):
            m = {**m, "reasoning_content": r}
            r = None
        out.append(m)
    return out


def turns_of(run_dir: Path) -> list[dict]:
    """events.jsonl → [{turn, user, messages, reason, labels, tools, backend, …}] (messages 는 사람 말부터 그 차례 끝까지)."""
    f = Path(run_dir) / "events.jsonl"
    if not f.is_file():
        return []
    turns: dict[int, dict] = {}
    cur = None
    labels: dict[int, list] = {}
    system, meta0, first_user = "", {}, None
    defs: list = []
    compacted = False
    run_end = None
    test_pass = None
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
            first_user = next((m for m in msgs if m.get("role") == "user"), None)
            meta0 = e.get("meta") or {}
        elif k == "tool_defs":
            defs = e.get("defs") or []
        elif k in ("compact", "summarize"):
            compacted = True
        elif k == "user":
            t = int(e.get("turn") or 0)
            seen = e.get("content") or ""          # 모델이 본 꼴(엔진이 앞에 붙인 환경·모드 안내 포함)
            cur = turns.setdefault(t, {"turn": t, "user": e.get("raw") or seen, "messages": [{"role": "user", "content": seen}],
                                       "reason": None, "compacted": compacted, "tools": defs, "calls": 0, "errors": 0})
            compacted = False
        elif k == "step":
            if cur is None and first_user is not None and not turns:   # 한 번 실행(stewart run): 사람 말은 run_start 에
                cur = turns.setdefault(1, {"turn": 1, "user": _plain(first_user.get("content")), "messages": [dict(first_user)],
                                           "reason": None, "compacted": False, "tools": defs, "calls": 0, "errors": 0, "single": True})
            if cur is None:
                continue
            if defs and not cur["tools"]:
                cur["tools"] = defs
            srec = e.get("response") or {}
            for tc in srec.get("tool_calls") or []:
                cur["calls"] += 1
                cur["errors"] += 0 if tc.get("ok", True) else 1
            for m in _with_reasoning(e.get("new_messages") or [], srec):
                if m.get("role") == "user" and len(cur["messages"]) == 1 and _plain(m.get("content")) == _plain(cur["messages"][0]["content"]):
                    continue   # 차례 첫 사람 말이 다시 실린 것
                cur["messages"].append(m)
        elif k == "turn_end":
            t = int(e.get("turn") or 0)
            if t in turns:
                turns[t]["reason"] = e.get("reason")
        elif k == "run_end":
            run_end = e.get("summary") or {}
        elif k == "test_result":      # teacher 돌리기(판 91)가 시험 명령 결과를 남긴다
            test_pass = bool(e.get("passed"))
        elif k == "train_label":
            labels.setdefault(int(e.get("turn") or 0), []).append(e.get("label"))
    if run_end is not None and 1 in turns and turns[1].get("single"):
        r = run_end.get("stop_reason")
        turns[1]["reason"] = "answered" if r == "completed" else r
        v = run_end.get("verify")
        if test_pass is None and isinstance(v, dict) and "passed" in v:
            test_pass = bool(v["passed"])
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
        x["backend"] = backend_meta(meta0.get("backend") or {})
        x["profile"] = meta0.get("profile")
        x["engine_version"] = meta0.get("engine_version")
        x["test_pass"] = test_pass if x.get("single") else None
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
                out.append({**t, "prev": prev})   # 교정된 앞 차례를 문맥으로 앞에 싣는다(train false — 손실에서 가림)
        return out
    raise ValueError(f"모르는 고르기: {how}")


# ── 다듬기·가림 ──────────────────────────────────────────────────────
MASK = "[가림]"


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
    """학습용 메시지로: 엔진 넛지·실패한 도구 호출(과 그 결과)을 빼고, 큰 결과는 앞뒤만, 그림은 자리표, 비밀 가림.
    assistant 에는 train 불리언(교정됨·나쁨 차례는 false — 문맥에는 두고 손실에서 가림). (메시지, 센 것)."""
    cnt = {"engine_notes": 0, "failed_calls": 0, "clipped": 0, "images": 0, "secrets_masked": 0}
    train = not ({"corrected", "bad"} & set(t.get("labels") or []))

    def sc(x: str) -> str:
        y = _scrub_text(x, home, user)
        cnt["secrets_masked"] += max(0, y.count(MASK) - str(x).count(MASK))
        return y

    msgs = [{"role": "system", "content": sc(t.get("system") or "")}] if t.get("system") else []
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
            if role in ("user", "tool") and not keep_engine and "\n\n[엔진]" in c:
                c = c.split("\n\n[엔진]")[0]
                cnt["engine_notes"] += 1
            out["content"] = sc(c)
        else:
            out["content"] = None
        if role == "assistant" and m.get("reasoning_content"):
            out["reasoning_content"] = sc(m["reasoning_content"])
        if m.get("tool_calls"):
            calls = [tc for tc in m["tool_calls"] if tc.get("id") not in failed_ids]
            if not calls and not out.get("content"):
                continue
            if calls:
                out["tool_calls"] = [{**tc, "function": {**tc.get("function", {}),
                                                         "arguments": sc((tc.get("function") or {}).get("arguments") or "")}}
                                     for tc in calls]
        if role == "tool":
            out["tool_call_id"] = m.get("tool_call_id")
            if m.get("name"):
                out["name"] = m.get("name")
        if role == "assistant":
            out["train"] = train
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
           exclude_hashes: set | None = None, track: str = "live", include_tiers: set | None = None,
           tier_file: Path | None = None) -> dict:
    """기록 폴더들 → out/train.jsonl + stats.json. include_tiers 기본 {"train-ok"} — 다른 등급은 빼고 수만 센다."""
    out.mkdir(parents=True, exist_ok=True)
    home, user = os.path.expanduser("~"), os.environ.get("USER") or ""
    rules = tier_rules(tier_file)
    tiers_ok = include_tiers or {"train-ok"}
    exclude = {h[:16] for h in (exclude_hashes or set())}   # 앞 16자 목록도 받는다(64자와 앞이 같다)
    stats = {"runs": 0, "turns_seen": 0, "picked": 0, "excluded_eval": 0, "excluded_tier": {}, "written": 0, "messages": 0,
             "chars": 0, "engine_notes": 0, "failed_calls": 0, "clipped": 0, "images": 0, "secrets_masked": 0, "labels": {},
             "tiers": {}, "train_false_turns": 0}
    import hashlib as _h
    sha = _h.sha256()
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
            run_id = _h.sha256(d.name.encode()).hexdigest()[:12]
            for t in select(ts, how):
                stats["picked"] += 1
                th = norm_hash(t["user"])
                if th[:16] in exclude:
                    stats["excluded_eval"] += 1
                    continue
                tier = tier_of(t["backend"], rules)
                stats["tiers"][tier] = stats["tiers"].get(tier, 0) + 1
                if tier not in tiers_ok:
                    stats["excluded_tier"][tier] = stats["excluded_tier"].get(tier, 0) + 1
                    continue
                msgs, cnt = clean(t, keep_engine, home, user)
                if t.get("prev"):
                    pm, pc = clean(t["prev"], keep_engine, home, user)
                    msgs = pm + [m for m in msgs if m["role"] != "system"]
                    for k, v in pc.items():
                        cnt[k] += v
                for k, v in cnt.items():
                    stats[k] += v
                if not any(m.get("train") for m in msgs):
                    stats["train_false_turns"] += 1
                meta = {"schema": SCHEMA, "source": "stewart", "engine_version": t.get("engine_version"), "profile": t.get("profile"),
                        "run": run_id, "turn": t["turn"], "track": track, "backend": t["backend"], "license_tier": tier,
                        "license_table": LICENSE_TABLE, "labels": t["labels"], "reason": t.get("reason"), "task_hash": th,
                        "test_pass": t.get("test_pass"), "tool_calls": t.get("calls", 0), "tool_errors": t.get("errors", 0),
                        "secrets_masked": cnt["secrets_masked"], "compacted": bool(t.get("compacted"))}
                line = json.dumps({"messages": msgs, "tools": t.get("tools") or [], "meta": meta}, ensure_ascii=False) + "\n"
                f.write(line)
                sha.update(line.encode("utf-8"))
                stats["written"] += 1
                stats["messages"] += len(msgs)
                stats["chars"] += len(line)
    stats["sha256"] = sha.hexdigest()
    (out / "stats.json").write_text(json.dumps({**stats, "schema": SCHEMA, "select": how, "track": track,
                                                "since": str(since) if since else None, "include_tiers": sorted(tiers_ok),
                                                "license_table": LICENSE_TABLE}, ensure_ascii=False, indent=1), encoding="utf-8")
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
