"""stewart 자체 현황판(판 49, 사용자 결정 11-10 — 본보기 repos/hq/PROTOCOL.md).

허브 폴더 board.json 하나. 허브 메인이 board 도구로 쓰고, 화면(사이드바 현황판·작업 내역 [현황판] 탭)과 차례 끝 꼬리표·
폰 알림(ntfy)으로 사람에게 보인다.
- 사용자 할 일 [[todo]]: U-번호(한 번 쓴 번호는 다시 안 씀), 제목(40자·비밀·주소 금지 — 폰 알림에 나간다), 자세히, 꼴(결정/승인/손작업/확인),
  급함(돈/장애/마감/막힘/보통), open → done/dropped(지우지 않는다, 닫은 때·결과를 적는다)
- 진행 중 [[progress]]: 무엇 · 다음
- 끝난 일 [[done]]: 최근 것부터
- 결정 [[decision]]: 언제 · 누가(사용자/메인) · 무엇 · 까닭 · 되돌릴 수 있나 — 메인이 혼자 정한 것은 빠짐없이
승인 대기(허브 대기열)는 판에 따로 쓰지 않고 화면이 같은 자리에 함께 보인다(같은 출처에서).
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

TYPES = ("결정", "승인", "손작업", "확인")
URGENCY = ("돈", "장애", "마감", "막힘", "보통")
URG_RANK = {u: i for i, u in enumerate(URGENCY)}
DONE_KEEP, PROGRESS_KEEP, DECISION_KEEP = 40, 12, 200
_LEAK = re.compile(r"https?://|\b\d{1,3}(\.\d{1,3}){3}\b|[A-Za-z0-9_\-]{32,}")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _parse(v: str) -> datetime | None:
    v = str(v or "").strip()
    for f in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, f)
        except ValueError:
            pass
    return None


def _date(v, name: str) -> str:
    v = str(v or "").strip()
    if v and _parse(v) is None:
        raise ValueError(f"{name} 는 YYYY-MM-DD 또는 YYYY-MM-DD HH:MM")
    return v


def _quiet(t: datetime) -> bool:
    """23:00~07:30 — 밤에는 돈·장애만 울린다."""
    m = t.hour * 60 + t.minute
    return m >= 23 * 60 or m < 7 * 60 + 30


class Board:
    def __init__(self, state_dir: Path, notify=None):
        self.path = Path(state_dir) / "board.json"
        self.notify = notify       # 새 사용자 할 일 → 폰(허브 ntfy). 없으면 조용히
        self._lock = threading.RLock()
        self.clock = datetime.now   # 밤 조용(23:00~07:30) 판정 시각 — 시험은 바꿔 끼운다

    def load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        return {"next": int(d.get("next") or 1), "todo": list(d.get("todo") or []), "progress": list(d.get("progress") or []),
                "done": list(d.get("done") or []), "decision": list(d.get("decision") or []), "updated": d.get("updated")}

    def _save(self, d: dict) -> None:
        from .hub import write_json
        d["updated"] = _now()
        write_json(self.path, d)

    # ── 쓰기(허브 메인 board 도구 · 화면) ──
    def add_todo(self, title: str, detail: str = "", type: str = "확인", urgency: str = "보통", due: str = "",
                 after: list | None = None, waiting: str = "", not_before: str = "", source: str = "") -> dict:
        title = " ".join(str(title or "").split())
        if not title:
            raise ValueError("title 이 비었다")
        if len(title) > 40:
            raise ValueError("title 은 40자까지(폰 알림에 나간다) — 자세한 것은 detail 에")
        if _LEAK.search(title):
            raise ValueError("title 에 주소·IP·긴 토큰 꼴을 넣지 않는다(폰 알림에 나간다) — detail 에")
        if type not in TYPES or urgency not in URGENCY:
            raise ValueError(f"type 은 {TYPES}, urgency 는 {URGENCY} 중")
        with self._lock:
            d = self.load()
            for t in d["todo"]:   # 같은 제목의 열린 할 일이 있으면 그것을 돌려준다(되풀이해 넣지 않게)
                if t["status"] == "open" and t["title"] == title:
                    return t
            t = {"id": f"U-{d['next']}", "title": title, "detail": str(detail or "")[:2000], "type": type, "urgency": urgency,
                 "opened": _now(), "status": "open"}
            for k, v in (("due", _date(due, "due")), ("not_before", _date(not_before, "not_before")),
                         ("waiting", " ".join(str(waiting or "").split())[:120]), ("source", str(source or "")[:40])):
                if v:
                    t[k] = v
            ids = {x["id"] for x in d["todo"]}
            aft = [str(a).strip() for a in (after or []) if str(a).strip()]
            bad = [a for a in aft if a not in ids]
            if bad:
                raise ValueError(f"after 에 없는 할 일: {bad}")
            if aft:
                t["after"] = aft
            d["next"] += 1
            d["todo"].append(t)
            self._save(d)
        if self._later(t, self.load()) is None:   # 지금 할 수 있는 것만 울린다(아직 아님은 풀릴 때)
            self._ring(t, "")
        return t

    def _ring(self, t: dict, why: str) -> None:
        """폰 알림(ntfy). 23:00~07:30 은 돈·장애만(규약 '알림이 울리는 때')."""
        if self.notify is None:
            return
        if os.environ.get("STEWART_BOARD_QUIET") != "0" and _quiet(self.clock()) and t.get("urgency") not in ("돈", "장애"):
            return
        try:
            self.notify(f"stewart 사용자 할 일 {t['id']}{why}: {t['title']}")
        except Exception:
            pass

    def close_todo(self, id: str, status: str = "done", outcome: str = "", by: str = "메인") -> dict:
        if status not in ("done", "dropped"):
            raise ValueError("status 는 done|dropped")
        with self._lock:
            d = self.load()
            t = next((x for x in d["todo"] if x["id"] == id), None)
            if t is None:
                raise ValueError(f"없는 할 일: {id}")
            before = {x["id"] for x in d["todo"] if x["status"] == "open" and self._later(x, d) is not None}
            t.update(status=status, closed=_now(), outcome=str(outcome or "")[:300], closed_by=by)
            self._save(d)
            freed = [x for x in d["todo"] if x["id"] in before and self._later(x, d) is None]
        for x in freed:   # 막힌 것이 풀려 지금 할 수 있게 된 것도 새 할 일처럼 울린다
            self._ring(x, " (이제 할 수 있음)")
        return t

    def progress(self, what: str, next: str = "", clear: bool = False) -> None:
        what = " ".join(str(what or "").split())[:120]
        if not what:
            raise ValueError("what 이 비었다")
        with self._lock:
            d = self.load()
            d["progress"] = [p for p in d["progress"] if p["what"] != what]
            if not clear:
                d["progress"].insert(0, {"what": what, "next": str(next or "")[:200], "at": _now()})
                d["progress"] = d["progress"][:PROGRESS_KEEP]
            self._save(d)

    def done(self, what: str) -> None:
        what = " ".join(str(what or "").split())[:160]
        if not what:
            raise ValueError("what 이 비었다")
        with self._lock:
            d = self.load()
            d["progress"] = [p for p in d["progress"] if p["what"] != what]
            d["done"].insert(0, {"what": what, "at": _now()})
            d["done"] = d["done"][:DONE_KEEP]
            self._save(d)

    def decide(self, what: str, why: str = "", by: str = "메인", reversible: bool = True) -> dict:
        what = " ".join(str(what or "").split())[:200]
        if not what or by not in ("사용자", "메인"):
            raise ValueError("what 이 비었거나 by 가 사용자|메인 이 아니다")
        x = {"at": _now(), "by": by, "what": what, "why": str(why or "")[:400], "reversible": bool(reversible)}
        with self._lock:
            d = self.load()
            d["decision"].insert(0, x)
            d["decision"] = d["decision"][:DECISION_KEEP]
            self._save(d)
        return x

    # ── 읽기 ──
    @staticmethod
    def _later(t: dict, d: dict, now: datetime | None = None) -> str | None:
        """아직 아님이면 까닭, 지금 할 수 있으면 None."""
        now = now or datetime.now()
        if t.get("waiting"):
            return f"기다림: {t['waiting']}"
        nb = t.get("not_before")
        if nb and _parse(nb) and _parse(nb) > now:
            return f"{nb} 부터"
        st = {x["id"]: x["status"] for x in d["todo"]}
        left = [a for a in t.get("after") or [] if st.get(a) == "open"]
        if left:
            return "먼저: " + ", ".join(left)
        return None

    def order(self, now: datetime | None = None) -> tuple[list[dict], list[dict]]:
        """순서표(규약 '순서는 이렇게 매긴다', 판단 없음): 1 돈·장애 2 마감(due 이틀 안 또는 급함 '마감') 3 남을 막는 것
        (누군가의 after 에 적힘 또는 급함 '막힘') 4 나머지 — 같은 칸은 연 순서. 돌려줌: (지금, 아직 아님) — 각 항목에 rank·why."""
        now = now or datetime.now()
        d = self.load()
        opens = [t for t in d["todo"] if t["status"] == "open"]
        blocking = {a for t in opens for a in t.get("after") or []}
        now_l, later = [], []
        for t in opens:
            t = dict(t)
            w = self._later(t, d, now)
            due = _parse(t.get("due") or "")
            if t.get("urgency") in ("돈", "장애"):
                t["rank"], t["why"] = 1, t["urgency"]
            elif t.get("urgency") == "마감" or (due and (due - now).total_seconds() <= 2 * 86400):
                t["rank"], t["why"] = 2, ("마감 " + t["due"]) if t.get("due") else "마감"
            elif t["id"] in blocking or t.get("urgency") == "막힘":
                t["rank"], t["why"] = 3, "남을 막음" if t["id"] in blocking else "막힘"
            else:
                t["rank"], t["why"] = 4, ""
            if w:
                t["later"] = w
                later.append(t)
            else:
                now_l.append(t)
        key = lambda t: (t["rank"], t.get("due") or "9999", t.get("opened") or "")
        return sorted(now_l, key=key), sorted(later, key=key)

    def open_todos(self) -> list[dict]:
        a, b = self.order()
        return a + b

    def tail(self, asks: list[dict] | None = None) -> str:
        """차례 끝 꼬리표: 열린 사용자 할 일 + 승인 대기. 없으면 빈 글."""
        a, b = self.order()
        parts = [f"{t['id']} {t['title']}" for t in a]
        parts += [f"승인 {q['session']} #{q['seq']}" for q in (asks or [])]
        parts += [f"({t['id']} {t['title']} — {t['later']})" for t in b]
        return ("열린 사용자 할 일: " + " · ".join(parts)) if parts else ""

    def view(self) -> dict:
        d = self.load()
        a, b = self.order()
        return {"open": a, "later": b, "closed": [t for t in d["todo"] if t["status"] != "open"][-20:][::-1],
                "progress": d["progress"], "done": d["done"][:15], "decision": d["decision"][:20], "updated": d["updated"]}

    def ledger_md(self) -> str:
        """결정 장부 내보내기(마크다운): 정한 것 전부 + 닫은 사용자 할 일. 화면 [현황판] 탭의 내려받기."""
        d = self.load()
        out = ["# stewart 결정 장부", "", f"내보낸 때: {_now()}", "", "## 정한 것", "",
               "| 언제 | 누가 | 무엇 | 까닭 | 되돌릴 수 있나 |", "|---|---|---|---|---|"]
        cell = lambda x: str(x or "").replace("|", "\\|").replace("\n", " ")
        out += [f"| {cell(x['at'])} | {cell(x['by'])} | {cell(x['what'])} | {cell(x.get('why'))} | {'예' if x.get('reversible', True) else '아니오'} |"
                for x in d["decision"]] or ["| | | 없음 | | |"]
        out += ["", "## 닫은 사용자 할 일", "", "| 번호 | 제목 | 꼴 | 연 때 | 닫은 때 | 누가 | 결과 |", "|---|---|---|---|---|---|---|"]
        out += [f"| {t['id']} | {cell(t['title'])} | {cell(t.get('type'))} | {cell(t.get('opened'))} | {cell(t.get('closed'))} | "
                f"{cell(t.get('closed_by'))} | {('했음' if t['status'] == 'done' else '안 함')}{(' — ' + cell(t.get('outcome'))) if t.get('outcome') else ''} |"
                for t in d["todo"] if t["status"] != "open"] or ["| | 없음 | | | | | |"]
        return "\n".join(out) + "\n"

    def text(self) -> str:
        v = self.view()
        out = ["사용자 할 일(지금, 순서대로): " + ("; ".join(f"{t['id']} [{t['type']}/{t['urgency']}] {t['title']}" for t in v["open"]) or "없음"),
               "아직 아님: " + ("; ".join(f"{t['id']} {t['title']} ({t['later']})" for t in v["later"]) or "없음"),
               "진행 중: " + ("; ".join(f"{p['what']} → {p['next']}" if p["next"] else p["what"] for p in v["progress"]) or "없음"),
               "끝난 일(최근): " + ("; ".join(x["what"] for x in v["done"][:5]) or "없음"),
               "결정(최근): " + ("; ".join(f"{x['by']}: {x['what']}" for x in v["decision"][:5]) or "없음")]
        return "\n".join(out)


DEFINITION = {"type": "function", "function": {
    "name": "board",
    "description": ("현황판(사람에게 보이는 판)에 쓴다. 사람에게 해 달라고 할 일은 add_todo(U-번호를 받아 대화에서도 그 번호로 부른다), "
                    "사람이 했거나 안 하기로 하면 close_todo. 진행 중인 큰 일은 progress, 끝나면 done, 혼자 정한 것은 decide 로 빠짐없이. show 는 지금 판."),
    "parameters": {"type": "object", "properties": {
        "op": {"type": "string", "enum": ["add_todo", "close_todo", "progress", "done", "decide", "show"]},
        "title": {"type": "string", "description": "add_todo: 40자, 주소·명령·비밀 금지(폰 알림에 나간다)"},
        "detail": {"type": "string", "description": "add_todo: 자세히 — 무엇을 어떻게, 안 하면 무엇이 멈추나"},
        "type": {"type": "string", "enum": list(TYPES)},
        "urgency": {"type": "string", "enum": list(URGENCY)},
        "id": {"type": "string", "description": "close_todo: U-번호"},
        "status": {"type": "string", "enum": ["done", "dropped"]},
        "outcome": {"type": "string"},
        "what": {"type": "string", "description": "progress·done·decide: 무엇"},
        "next": {"type": "string", "description": "progress: 다음 걸음"},
        "why": {"type": "string", "description": "decide: 까닭"},
        "by": {"type": "string", "enum": ["사용자", "메인"]},
        "reversible": {"type": "boolean"},
        "due": {"type": "string", "description": "add_todo: 마감 YYYY-MM-DD(이틀 안이면 저절로 위로)"},
        "after": {"type": "array", "items": {"type": "string"}, "description": "add_todo: 먼저 끝나야 하는 U-번호들(그때까지 '아직 아님')"},
        "waiting": {"type": "string", "description": "add_todo: 밖의 일을 기다리면 무엇을(그동안 '아직 아님')"},
        "not_before": {"type": "string", "description": "add_todo: 이때부터 할 수 있으면 YYYY-MM-DD HH:MM"},
    }, "required": ["op"]}}}


def execute(board: Board, args: dict) -> tuple[bool, str]:
    op = args.get("op")
    try:
        if op == "add_todo":
            t = board.add_todo(args.get("title", ""), args.get("detail", ""), args.get("type") or "확인", args.get("urgency") or "보통",
                               due=args.get("due") or "", after=args.get("after") or None, waiting=args.get("waiting") or "",
                               not_before=args.get("not_before") or "")
            return True, f"{t['id']} 을 판에 올렸다({t['title']}). 사람에게도 이 번호로 말하라."
        if op == "close_todo":
            t = board.close_todo(str(args.get("id") or ""), args.get("status") or "done", args.get("outcome", ""))
            return True, f"{t['id']} 닫음({t['status']})"
        if op == "progress":
            board.progress(args.get("what", ""), args.get("next", ""))
            return True, "진행 중에 적었다"
        if op == "done":
            board.done(args.get("what", ""))
            return True, "끝난 일에 적었다"
        if op == "decide":
            board.decide(args.get("what", ""), args.get("why", ""), args.get("by") or "메인", args.get("reversible", True))
            return True, "결정 장부에 적었다"
        if op == "show":
            return True, board.text()
    except ValueError as e:
        return False, str(e)
    return False, f"모르는 op: {op}"


NOTE = ("현황판(board 도구): 사람에게 해 달라고 하는 일(결정·승인·손작업·확인)은 말하기 전에 board add_todo 로 올리고 받은 U-번호로 부른다 — "
        "사람이 했다거나 안 한다고 하면 close_todo. 큰 일을 시작하면 progress, 끝나면 done. 사람에게 묻지 않고 혼자 정한 '어떻게'는 decide 로 빠짐없이 "
        "적는다(방향·돈·되돌릴 수 없는 것은 혼자 정하지 말고 묻는다). 마감은 due, 앞에 끝나야 할 할 일은 after, 밖의 일을 기다리면 waiting 에 적는다 — "
        "순서표(돈·장애 > 마감 > 남을 막음 > 나머지)는 엔진이 적힌 대로 매긴다. 작업자 보고의 '사용자 할 일:' 절은 허브가 판에 저절로 올리고 알린다. "
        "차례 끝의 '열린 사용자 할 일' 꼬리표는 엔진이 붙인다.")

# 작업자 보고에서 사용자 할 일 뽑기(판 53). 작업자 지시문 WORKER_NOTE 가 이 꼴을 가르친다.
WORKER_NOTE = ("사람(사용자)이 해 줘야 하는 일(결정·승인·손작업·확인)이 있으면 보고 끝에 이 꼴로 적는다 — 허브가 현황판에 올리고 폰으로 알린다:\n"
               "사용자 할 일:\n- [결정|승인|손작업|확인/돈|장애|마감|막힘|보통] 제목(40자, 주소·명령·비밀 없이) — 자세히(무엇을, 안 하면 무엇이 멈추나)\n"
               "없으면 이 절을 쓰지 않는다.")
_HEAD = re.compile(r"^\s*(?:#+\s*)?\**\s*(?:사용자|사람)(?:이)? ?할 ?일\s*\**\s*[:：]?\s*\**\s*$")
_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(?:\[([^\]/]+)(?:/([^\]]+))?\]\s*)?(.+?)\s*$")


def extract_todos(text: str) -> list[dict]:
    """보고 글의 '사용자 할 일:' 절(머리 줄 다음 목록, 빈 줄·다른 머리에서 끝)에서 [{title, detail, type, urgency}]."""
    out, on = [], False
    for line in str(text or "").splitlines():
        if _HEAD.match(line):
            on = True
            continue
        if not on:
            continue
        if not line.strip():
            if out:
                on = False
            continue
        m = _ITEM.match(line)
        if not m:
            on = False
            continue
        ty, urg, body = (m.group(1) or "").strip(), (m.group(2) or "").strip(), m.group(3)
        if ty in URGENCY and not urg:
            ty, urg = "", ty
        parts = re.split(r"\s+[—–]\s+|\s+--\s+", body, maxsplit=1)
        title, detail = parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")
        if len(title) > 40:
            title, detail = title[:39] + "…", (body if not detail else detail)
        out.append({"title": title, "detail": detail, "type": ty if ty in TYPES else "확인",
                    "urgency": urg if urg in URGENCY else "보통"})
    return out[:10]


def take_from_report(board: Board, session: str, text: str) -> list[dict]:
    """작업자 보고 한 건에서 뽑아 판에 올린다. 제목이 위험한 꼴이면(주소 등) 그 항목만 건너뛴다. 올린(또는 이미 있던) 할 일들."""
    got = []
    for it in extract_todos(text):
        try:
            got.append(board.add_todo(it["title"], (f"[{session} 보고] " + it["detail"]).strip(), it["type"], it["urgency"], source=session))
        except ValueError:
            continue
    return got
