"""지난 대화로 배워 두기(판 93, SW-41 ④ 판 B — 학습 없는 자가발전): 표시된 차례에서 예시·규칙 후보를 모델이 **제안**하고 사람이 받는다.

1. 자료 모으기(모델 없이): 이 인스턴스의 실행 기록에서 👍 차례, 교정됨 차례와 그 뒤 고친 차례를 고른다(학습 내보내기와 같은 엮기).
   이미 본 차례(<기억>/user/improve-seen.jsonl)는 건너뛰고, 한 번에 MAX_TURNS 차례·차례당 TURN_CHARS 자로 줄인다(비밀·홈 경로 가림).
2. 제안 받기(모델 한 번, 도구 없이, 생각 끔) — JSON 줄:
     {"op":"example","title":"상황 한 줄","situation":"…","did":"…","outcome":"…","src":"T3"}   예시 은행에 둘 잘 된 꼴
     {"op":"rule","text":"다음엔 이렇게(한 문장)","src":"T5"}                                   교정에서 배운 방침
     {"op":"skill","name":"kebab-이름","when":"언제 쓰나","steps":["…","…"],"src":"T2"}            되풀이되는 절차(판 94 — 초안만,
                                                                                                  <기억>/user/skill-drafts/ 에 두고 사람이 옮겨야 켜짐)
3. 코드가 다시 본다: src 가 고른 차례에 없음(지어낸 근거)·비밀 꼴·홈 경로·규칙 300자·예시 1200자 넘음·있는 예시 제목·방침과 거의 같음·
   같은 src 를 두 번 → 버린다.
4. 카드(SW-40 기억 정리와 같은 꼴): 항목마다 받기·두기. 받은 것만 쓴다 — 쓰기 전 examples.md·policy.md 사본을 improve-<시각>/ 에,
   한 일을 improve-<시각>.md 에. 되돌리기: `stewart improve --revert improve-<시각>`. 본 차례는 받든 말든 seen 에 적는다.
바깥 API 백엔드면 고른 차례 글이 그 회사로 나간다 — 허브·CLI 가 먼저 알리고 다시 묻는다.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from . import examples as EX
from . import train as TRAIN

MAX_TURNS = 20
TURN_CHARS = 2000
RULE_MAX, EX_MAX = 300, 1200
OPS = ("example", "rule", "skill")
SKILL_RX = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
DRAFTS = "skill-drafts"
SEEN = "improve-seen.jsonl"
LABEL = {"example": "예시", "rule": "방침", "skill": "스킬 초안"}

PROMPT = """[지난 대화로 배워 두기] 아래는 사람이 좋다고 표시했거나(👍) 고쳐 준(교정됨 → 고친 차례) 지난 차례들이다.
다음에 비슷한 일이 오면 도움이 될 것만 제안한다 — 없으면 아무것도 쓰지 않는다.
할 수 있는 것(JSON 줄만, 설명 없이, 한 줄에 하나):
{{"op":"example","title":"상황 한 줄","situation":"어떤 일이었나","did":"어떻게 했나(도구 호출 꼴)","outcome":"결과","src":"T번호"}}   잘 된 꼴
{{"op":"rule","text":"다음엔 이렇게 한다(한 문장, 까닭이 있으면 '— 까닭')","src":"T번호"}}                                   고쳐 준 데서 배운 것
{{"op":"skill","name":"영문-소문자-이름","when":"언제 쓰나 한 줄","steps":["1단계","2단계"],"src":"T번호"}}      여러 차례에 되풀이될 절차(드물게)
지키기: src 는 아래 차례 번호(T1…)만. 비밀 값·주소·사람 이름은 쓰지 않는다. 그 일에만 맞는 것(파일 이름 하나뿐인 것)은 쓰지 않는다. 확실하지 않으면 제안하지 않는다.
차례:
{turns}"""


# ── 1. 자료 모으기 ─────────────────────────────────────────────────────
def _seen(mem_dir: Path) -> set:
    try:
        return {json.loads(x)["ref"] for x in (Path(mem_dir) / SEEN).read_text(encoding="utf-8").splitlines() if x.strip()}
    except (OSError, ValueError, KeyError):
        return set()


def mark_seen(mem_dir: Path, refs: list[str]) -> None:
    if not refs:
        return
    Path(mem_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(mem_dir) / SEEN, "a", encoding="utf-8") as f:
        for r in refs:
            f.write(json.dumps({"ref": r, "ts": datetime.now().isoformat(timespec="seconds")}) + "\n")


def _short(t: dict, kind: str) -> str:
    home, user = os.path.expanduser("~"), os.environ.get("USER") or ""
    msgs, _ = TRAIN.clean(t, False, home, user)
    out = [f"[{kind}] 사람: {next((m['content'] for m in msgs if m['role'] == 'user'), '')[:400]}"]
    for m in msgs:
        if m["role"] == "assistant" and m.get("tool_calls"):
            out.append("  호출: " + "; ".join(f"{(tc.get('function') or {}).get('name')} {str((tc.get('function') or {}).get('arguments'))[:160]}"
                                             for tc in m["tool_calls"]))
        elif m["role"] == "tool":
            out.append(f"  결과: {str(m.get('content') or '')[:160]}")
    last = next((m["content"] for m in reversed(msgs) if m["role"] == "assistant" and m.get("content")), "")
    out.append(f"  답: {str(last)[:400]}")
    s = "\n".join(out)
    return s if len(s) <= TURN_CHARS else s[:TURN_CHARS] + "…"


def collect(runs_dir: Path, mem_dir: Path, since=None) -> list[dict]:
    """[{ref, label, text}] — 👍 차례와 (교정됨 차례 + 고친 차례) 짝. 본 것은 뺀다. 최근 것부터 MAX_TURNS."""
    import hashlib
    seen = _seen(mem_dir)
    out = []
    for d in reversed(TRAIN.run_dirs(runs_dir, since)):
        ts = TRAIN.turns_of(d)
        rid = hashlib.sha256(d.name.encode()).hexdigest()[:12]
        for i, t in enumerate(ts):
            ref = f"{rid}#{t['turn']}"
            if ref in seen:
                continue
            if "good" in t["labels"] and "corrected" not in t["labels"]:
                out.append({"ref": ref, "label": "좋음", "text": _short(t, "좋음")})
            elif "corrected" in t["labels"] and i + 1 < len(ts):
                nxt = ts[i + 1]
                out.append({"ref": ref, "label": "교정", "text": _short(t, "교정됨 — 사람이 다음 말로 고침") + "\n" + _short(nxt, "고친 차례")})
            if len(out) >= MAX_TURNS:
                return out
    return out


# ── 2·3. 제안·확인 ────────────────────────────────────────────────────
def parse(text: str, picked: list[dict], examples: list[dict], policies: list[str], secret_rx=None, taken: set | None = None) -> list[dict]:
    """taken: 이미 있는 스킬·초안 이름(겹치면 버림)."""
    taken = taken or set()
    from .memstore import similar
    if secret_rx is None:
        from .memory import _SECRETISH as secret_rx
    home = os.path.expanduser("~")
    srcs = {f"T{i + 1}": p["ref"] for i, p in enumerate(picked)}
    titles = {" ".join(x["title"].split()).lower() for x in examples}
    used: set = set()
    out = []
    for line in (text or "").splitlines():
        line = line.strip().strip("`").strip()
        if not line.startswith("{"):
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        op, src = o.get("op"), str(o.get("src") or "").strip()
        if op not in OPS or src not in srcs or src in used:
            continue
        if op == "skill":
            name = str(o.get("name") or "").strip()
            steps = [" ".join(str(x).split()) for x in (o.get("steps") or []) if str(x).strip()]
            when = " ".join(str(o.get("when") or "").split())[:200]
            if not SKILL_RX.match(name) or not when or not 2 <= len(steps) <= 12 or name in taken:
                continue
            title = name
            body = f"---\nname: {name}\ndescription: {when}\n---\n\n" + "\n".join(f"{i + 1}. {x[:300]}" for i, x in enumerate(steps))
        elif op == "rule":
            t = " ".join(str(o.get("text") or "").split())
            if not t or len(t) > RULE_MAX or any(similar(t, p) >= 0.8 for p in policies):
                continue
            body, title = t, t
        else:
            title = " ".join(str(o.get("title") or "").split())[:120]
            body = "\n".join(f"{k}: {' '.join(str(o.get(f) or '').split())}" for k, f in (("상황", "situation"), ("한 일", "did"), ("결과", "outcome")))
            if not title or not str(o.get("did") or "").strip() or len(body) > EX_MAX or title.lower() in titles:
                continue
        if secret_rx.search(title + "\n" + body) or (home and home in body) or (op != "skill" and len(body) > EX_MAX):
            continue
        used.add(src)
        out.append({"n": len(out) + 1, "op": op, "title": title, "body": body, "src": src, "ref": srcs[src]})
    return out


def propose(mem, backend, runs_dir: Path, picked: list[dict] | None = None, chat_fn=None) -> tuple[list[dict], list[dict]]:
    """(제안, 고른 차례). 고른 차례가 없으면 모델을 부르지 않는다."""
    picked = collect(runs_dir, mem.store.dir) if picked is None else picked
    if not picked:
        return [], []
    if chat_fn is None:
        from .backend import chat as chat_fn
    body = "\n\n".join(f"T{i + 1} ({p['label']})\n{p['text']}" for i, p in enumerate(picked))
    resp, _dt = chat_fn(backend, [{"role": "system", "content": "너는 지난 작업에서 다음에 쓸 꼴을 뽑는 도우미다. 지시한 꼴의 JSON 줄만 쓴다."},
                                  {"role": "user", "content": PROMPT.format(turns=body)}], None, max_tokens=2000, thinking=False)
    msg = ((resp.get("choices") or [{}])[0].get("message") or {})
    pols = [x["text"] for x in mem.remembered() if x.get("kind") == "policy"]
    return parse(msg.get("content") or "", picked, EX.load(mem.store.dir), pols, taken=skill_names(mem.store.dir)), picked


def skill_names(mem_dir: Path) -> set:
    """겹치면 안 되는 이름: 사용자 스킬 폴더·내장 스킬·이미 둔 초안."""
    from . import skills as SK
    out = set()
    for d in (SK.user_dir(), Path(mem_dir) / DRAFTS):
        if d.is_dir():
            out |= {x.name for x in d.iterdir() if x.is_dir()}
    try:
        out |= {x.name for x in SK.discover(Path(mem_dir), builtin=True)}
    except Exception:   # noqa: BLE001 — 이름 모으기 실패는 겹침 검사만 약해진다
        pass
    return out


def describe(p: dict) -> str:
    if p["op"] == "skill":
        steps = " · ".join(ln for ln in p["body"].split("---")[-1].strip().splitlines())
        desc = next((ln[len("description: "):] for ln in p["body"].splitlines() if ln.startswith("description: ")), "")
        return f"{p['n']}. [{LABEL['skill']}] {p['title']} — {desc}\n   {steps}  (근거 {p['src']} · 받으면 초안만, 켜려면 옮김)"
    return f"{p['n']}. [{LABEL[p['op']]}] {p['title'] if p['op'] == 'example' else p['body']}" + \
        (f"\n   {p['body'].replace(chr(10), ' · ')}" if p["op"] == "example" else "") + f"  (근거 {p['src']})"


# ── 4. 적용·되돌리기 ──────────────────────────────────────────────────
def apply(mem, proposals: list[dict], accept: list[int] | None, picked: list[dict]) -> dict:
    d = mem.store.dir
    take = [p for p in proposals if accept is None or p["n"] in accept]
    mark_seen(d, [p["ref"] for p in picked])
    if not take:
        return {"applied": [], "backup": None, "log": None}
    b = d / f"improve-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    b.mkdir(parents=True, exist_ok=True)
    for name in (EX.FILE, "policy.md"):
        if (d / name).is_file():
            shutil.copy2(d / name, b / name)
    (b / ".had").write_text("\n".join(n for n in (EX.FILE, "policy.md") if (d / n).is_file()), encoding="utf-8")
    done = []
    for p in take:
        if p["op"] == "example":
            EX.add(d, p["title"], p["body"], src=p["ref"])
            done.append(p["n"])
        elif p["op"] == "skill":
            sd = d / DRAFTS / p["title"]
            if not sd.exists():
                sd.mkdir(parents=True)
                (sd / "SKILL.md").write_text(p["body"] + "\n", encoding="utf-8")
                done.append(p["n"])
        elif mem.remember("policy", p["body"], source="human") is not None:
            done.append(p["n"])
    drafts = [p["title"] for p in take if p["op"] == "skill" and p["n"] in done]
    (b / ".drafts").write_text("\n".join(drafts), encoding="utf-8")
    log = d / f"{b.name}.md"
    log.write_text("\n".join([f"# 지난 대화로 배워 두기 — {datetime.now().isoformat(timespec='seconds')}", "",
                              f"받은 것 {len(done)}개. 적용 전 사본: {b.name}/. 되돌리기: `stewart improve --revert {b.name}`", ""]
                             + ([f"스킬 초안({', '.join(drafts)})은 `{d / DRAFTS}/` 에 있다 — 켜려면 그 폴더를 사용자 스킬 폴더로 옮긴다.", ""] if drafts else [])
                             + [("- ✓ " if p["n"] in done else "- ✗ ") + describe(p).replace("\n", " ") for p in take]) + "\n", encoding="utf-8")
    return {"applied": done, "backup": str(b), "log": str(log), "drafts": [str(d / DRAFTS / n) for n in drafts]}


def revert(mem_dir: Path, name: str) -> list[str]:
    d = Path(mem_dir)
    b = d / name
    if not name.startswith("improve-") or not b.is_dir():
        raise FileNotFoundError(f"사본이 없다: {b}")
    had = set((b / ".had").read_text(encoding="utf-8").split()) if (b / ".had").is_file() else set()
    out = []
    for nm in ((b / ".drafts").read_text(encoding="utf-8").split() if (b / ".drafts").is_file() else []):
        sd = d / DRAFTS / nm
        if SKILL_RX.match(nm) and (sd / "SKILL.md").is_file():
            shutil.rmtree(sd)
            out.append(f"{DRAFTS}/{nm}(지움)")
    for nm in (EX.FILE, "policy.md"):
        if (b / nm).is_file():
            shutil.copy2(b / nm, d / nm)
            out.append(nm)
        elif nm not in had and (d / nm).is_file():
            (d / nm).unlink()
            out.append(nm + "(지움)")
    return out


# ── 효과 보기(판 94): 예시가 붙은 차례 대 안 붙은 차례 ─────────────────
def stats(runs_dir: Path, mem_dir: Path, since=None) -> dict:
    """예시가 붙은 차례(example_used)와 안 붙은 차례의 👍·교정 비율, 예시별 쓰임·교정 수, 지움 후보(3번 넘게 쓰였는데 절반 넘게 교정)·안 쓰인 예시."""
    ex_ids = {x["id"]: x["title"] for x in EX.load(mem_dir)}
    per: dict = {}
    grp = {"with": {"turns": 0, "good": 0, "corrected": 0}, "without": {"turns": 0, "good": 0, "corrected": 0}}
    for d in TRAIN.run_dirs(runs_dir, since):
        used, pending = {}, None
        try:
            lines = (d / "events.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if e.get("kind") == "example_used":
                pending = e.get("id")
            elif e.get("kind") == "user":
                if pending:
                    used[int(e.get("turn") or 0)] = pending
                pending = None
        for t in TRAIN.turns_of(d):
            g = grp["with" if t["turn"] in used else "without"]
            g["turns"] += 1
            g["good"] += "good" in t["labels"]
            g["corrected"] += "corrected" in t["labels"]
            if t["turn"] in used:
                x = per.setdefault(used[t["turn"]], {"used": 0, "good": 0, "corrected": 0})
                x["used"] += 1
                x["good"] += "good" in t["labels"]
                x["corrected"] += "corrected" in t["labels"]
    drop = [i for i, x in per.items() if i in ex_ids and x["used"] >= 3 and x["corrected"] * 2 > x["used"]]
    unused = [i for i in ex_ids if i not in per]
    return {"groups": grp, "examples": {i: {**per.get(i, {"used": 0, "good": 0, "corrected": 0}), "title": t} for i, t in ex_ids.items()},
            "drop_candidates": drop, "unused": unused}


def stats_text(st: dict) -> str:
    def rate(g, k):
        return f"{g[k]}/{g['turns']}" + (f" ({100 * g[k] / g['turns']:.0f}%)" if g["turns"] else "")
    w, wo = st["groups"]["with"], st["groups"]["without"]
    out = [f"예시가 붙은 차례 {w['turns']}개: 👍 {rate(w, 'good')} · 교정됨 {rate(w, 'corrected')}",
           f"안 붙은 차례 {wo['turns']}개: 👍 {rate(wo, 'good')} · 교정됨 {rate(wo, 'corrected')}",
           "(표시는 사람이 고른 것만이라 비율은 어림이다 — 표시 없는 차례가 대부분이면 비교가 약하다)", ""]
    for i, x in sorted(st["examples"].items(), key=lambda kv: -kv[1]["used"]):
        flag = " ← 지움 후보(쓰일 때 절반 넘게 교정)" if i in st["drop_candidates"] else (" ← 아직 안 쓰임" if i in st["unused"] else "")
        out.append(f"  {i}  쓰임 {x['used']} · 👍 {x['good']} · 교정 {x['corrected']}  {x['title'][:60]}{flag}")
    if st["drop_candidates"]:
        out.append("\n지우기: stewart memory examples --rm <id>")
    return "\n".join(out)
