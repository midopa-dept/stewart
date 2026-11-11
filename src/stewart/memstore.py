"""기억 세 갈래(판 83, SW-35): 방침(policy)·프로필(profile)·자료(knowledge) — 사람이 열어 고치는 마크다운이 정본.

자리: <기억>/user/
  policy.md            방침: 어떻게 일할지("앞으로 …", "… 하지 마라"). 지시문에 늘 싣는다(범위: 모든 프로젝트 또는 그 대상).
  profile.md           프로필: 사용자에 대한 짧은 사실(언어·역할·기기·선호). 지시문에 늘 싣는다(작은 상한).
  knowledge/<주제>.md   자료: 모델이 몰랐거나 틀려서 사람이 고쳐 준 사실·참고. 지시문에 싣지 않고 doc_search(source="memory")로 찾는다.
줄 하나가 기억 하나: "- 글 <!-- id=… · ts=… · scope=user|project:<열쇠> · where=… · src=said|model|human -->".
사람이 줄을 고치거나 지우거나 주석 없이 새 줄을 써도 된다(주석이 없으면 id 는 글에서 만들고 범위는 모든 프로젝트).

옮기기(migrate): 옛 remember.jsonl 은 그대로 두고 사본(.bak-<날짜>)을 남긴 뒤 종류로 나눈다 — 모델을 부르지 않는다(같은 입력이면 같은 결과).
  feedback·explicit → 지시 꼴(앞으로·항상·하지 마·…해라)이면 방침, 아니면 자료 / user → 120자 이하면 프로필, 아니면 자료 /
  project·reference → 자료. 옮긴 표는 migrate-<날짜>.md. 되돌리기(revert)는 세 갈래 파일을 reverted-<날짜>/ 로 옮기고 옛 파일로 돌아간다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

KINDS = ("policy", "profile", "knowledge")
NAMES = {"policy": "방침", "profile": "프로필", "knowledge": "자료"}
HEAD = {
    "policy": "# Policy — 방침: stewart 가 늘 지키는 일하는 방식(지시문에 실림)",
    "profile": "# Profile — 프로필: 사용자에 대한 짧은 사실(지시문에 실림)",
    "knowledge": "# Knowledge — 자료: 고쳐 준 사실·참고(지시문에 싣지 않음, doc_search 로 찾음)",
}
HELP = "<!-- stewart 기억: 줄 하나가 기억 하나다. 글을 고치거나 줄을 지우거나 새 줄을 써도 된다. 뒤의 주석은 stewart 가 쓰는 표(id·날짜·범위). -->"
PROFILE_MAX_CHARS = 120
JACCARD_SAME = 0.6
MARKER = "migrated.json"
_META = re.compile(r"\s*<!--\s*(.*?)\s*-->\s*$")
_LOCK = threading.Lock()

# 오래 지킬 지시의 꼴(이번 한 번이 아니라 "앞으로") — 이것이 없으면 방침으로 받지 않는다
PERSIST = re.compile(r"(앞으로(?:는|도)?|다음부터(?:는)?|이제부터(?:는)?|항상|언제나|늘\s|매번|절대(?:로)?|기본으로|기본값|"
                     r"\b(always|never|from now on|going forward|by default)\b)", re.I)
DIRECTIVE = re.compile(r"(하지\s*(?:마|말|않는다)|쓰지\s*(?:마|말|않는다)|하면\s*안\s*(?:돼|된다)|금지|말고\s|해라|하라|할 것|써라|남겨라|적어라)")


def kind_of_legacy(kind: str, text: str) -> str:
    """옛 종류 → 세 갈래(옮기기·옛 이름으로 부를 때)."""
    if kind in KINDS:
        return kind
    if kind in ("feedback", "explicit"):   # 지시 꼴(앞으로·항상·하지 마·…해라)이면 방침, 아니면 자료
        return "policy" if PERSIST.search(text or "") or DIRECTIVE.search(text or "") else "knowledge"
    if kind == "user":
        return "profile" if len(text or "") <= PROFILE_MAX_CHARS else "knowledge"
    return "knowledge"


def _words(t: str) -> set:
    return set(re.findall(r"[A-Za-z0-9_]+|[가-힣]{2,}", (t or "").lower()))


def similar(a: str, b: str) -> float:
    x, y = _words(a), _words(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def _slug(s: str) -> str:
    s = re.sub(r"[^\w가-힣.-]+", "-", s or "").strip("-.")
    return s[:60] or "general"


def _id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def _parse_meta(s: str) -> dict:
    out = {}
    for part in s.split("·"):
        k, sep, v = part.strip().partition("=")
        if sep:
            out[k.strip()] = v.strip()
    return out


class Store:
    """<기억>/user 의 세 갈래 파일을 읽고 쓴다. 쓰기는 파일 통째로(임시 파일 → 바꿔 놓기)."""

    def __init__(self, user_dir: Path):
        self.dir = Path(user_dir)

    # ── 자리 ──
    def path(self, kind: str, topic: str = "general") -> Path:
        if kind == "knowledge":
            return self.dir / "knowledge" / f"{_slug(topic)}.md"
        return self.dir / f"{kind}.md"

    def files(self, kind: str) -> list[Path]:
        if kind == "knowledge":
            d = self.dir / "knowledge"
            return sorted(d.glob("*.md")) if d.is_dir() else []
        p = self.path(kind)
        return [p] if p.is_file() else []

    def active(self) -> bool:
        """세 갈래로 옮겨졌나(파일이 하나라도 있거나 옮김 표지가 있고 되돌리지 않음)."""
        m = self.marker()
        if m.get("reverted"):
            return False
        return bool(m) or any(self.files(k) for k in KINDS)

    def marker(self) -> dict:
        try:
            return json.loads((self.dir / MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    # ── 읽기 ──
    def read(self, p: Path, kind: str) -> list[dict]:
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            mt = datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
        except OSError:
            return []
        out = []
        for ln in lines:
            if not ln.startswith("- "):
                continue
            body = ln[2:]
            meta = {}
            m = _META.search(body)
            if m:
                meta = _parse_meta(m.group(1))
                body = body[:m.start()]
            text = " ".join(body.split())
            if not text:
                continue
            sc = meta.get("scope", "user")
            out.append({"id": meta.get("id") or _id(kind + text), "ts": meta.get("ts") or mt, "kind": kind, "text": text,
                        "source": meta.get("src", "human"), "scope": "project" if sc.startswith("project") else "user",
                        "place": sc.split(":", 1)[1] if sc.startswith("project:") else None, "where": meta.get("where"),
                        "run": meta.get("run"), "file": str(p)})
        return out

    def items(self, kind: str | None = None) -> list[dict]:
        out = []
        for k in ([kind] if kind else KINDS):
            for p in self.files(k):
                out += self.read(p, k)
        out.sort(key=lambda x: x["ts"])
        return out

    # ── 쓰기 ──
    def _line(self, x: dict) -> str:
        sc = f"project:{x['place']}" if x.get("scope") == "project" and x.get("place") else "user"
        meta = [f"id={x['id']}", f"ts={x['ts']}", f"scope={sc}"]
        if x.get("where"):
            meta.append(f"where={str(x['where']).replace('·', ' ')}")
        meta.append(f"src={x.get('source') or 'human'}")
        if x.get("run"):
            meta.append(f"run={x['run']}")
        return f"- {x['text']} <!-- {' · '.join(meta)} -->"

    def _write(self, p: Path, kind: str, items: list[dict]) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join([HEAD[kind], HELP, ""] + [self._line(x) for x in items]) + "\n"
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, p)

    def topic_for(self, x: dict) -> str:
        return (x.get("where") or "general") if x.get("scope") == "project" else "general"

    def add(self, x: dict) -> dict:
        with _LOCK:
            p = self.path(x["kind"], self.topic_for(x)) if x["kind"] == "knowledge" else self.path(x["kind"])
            cur = self.read(p, x["kind"]) if p.is_file() else []
            self._write(p, x["kind"], cur + [x])
        return {**x, "file": str(p)}

    def replace(self, item_id: str, new: dict | None) -> dict | None:
        """id 인 줄을 new 로 바꾼다(None 이면 지운다). 바꾼 옛 것 또는 None."""
        with _LOCK:
            for k in KINDS:
                for p in self.files(k):
                    cur = self.read(p, k)
                    hit = next((x for x in cur if x["id"] == item_id), None)
                    if hit is None:
                        continue
                    if new is not None and new.get("kind", k) != k:   # 갈래를 바꾸면 그 파일로 옮긴다
                        self._write(p, k, [x for x in cur if x["id"] != item_id])
                        self.add({**hit, **new})
                    else:
                        self._write(p, k, [({**x, **new} if x["id"] == item_id else x) for x in cur if new is not None or x["id"] != item_id])
                    return hit
        return None

    def same(self, kind: str, text: str, scope: str, place) -> dict | None:
        """같은 갈래·범위에 거의 같은 것(낱말 겹침 ≥ JACCARD_SAME)이 있으면 그것."""
        best, score = None, 0.0
        for x in self.items(kind):
            if x["scope"] != scope or (scope == "project" and x.get("place") != place):
                continue
            s = 1.0 if x["text"] == text else similar(x["text"], text)
            if s > score:
                best, score = x, s
        return best if score >= JACCARD_SAME else None


# ── 옮기기 ───────────────────────────────────────────────────────────
def _legacy(user_dir: Path) -> list[dict]:
    f = Path(user_dir) / "remember.jsonl"
    if not f.is_file():
        return []
    items, gone = {}, set()
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            x = json.loads(line)
        except ValueError:
            continue
        if x.get("forget"):
            gone.add(x["forget"])
        elif x.get("id"):
            items[x["id"]] = x
    return [x for k, x in items.items() if k not in gone]


def plan(user_dir: Path) -> list[dict]:
    """옛 기억 → [{id, old_kind, kind, text, scope, place, where, ts, source}] (쓰지 않는다)."""
    out = []
    for x in _legacy(user_dir):
        text = " ".join(str(x.get("text") or "").split())
        if not text:
            continue
        out.append({"id": x["id"], "old_kind": x.get("kind"), "kind": kind_of_legacy(x.get("kind", ""), text), "text": text,
                    "scope": x.get("scope", "user"), "place": x.get("place"), "where": x.get("where"),
                    "ts": x.get("ts") or "", "source": x.get("source") or "said", "run": x.get("run")})
    return out


def table(rows: list[dict]) -> str:
    n = {k: sum(1 for r in rows if r["kind"] == k) for k in KINDS}
    lines = [f"# 기억 옮기기 — {datetime.now().isoformat(timespec='seconds')}", "",
             f"옛 기억 {len(rows)}건 → 방침 {n['policy']} · 프로필 {n['profile']} · 자료 {n['knowledge']}. "
             "옛 파일 remember.jsonl 은 그대로 두었다. 되돌리기: `stewart memory migrate --revert`.", "",
             "| 옛 id | 옛 종류 | 새 갈래 | 범위 | 글 |", "|---|---|---|---|---|"]
    for r in rows:
        sc = "이 프로젝트" + (f"({r['where']})" if r.get("where") else "") if r["scope"] == "project" else "모든 프로젝트"
        lines.append(f"| {r['id']} | {r['old_kind']} | {NAMES[r['kind']]} | {sc} | {r['text'].replace('|', '/')[:120]} |")
    return "\n".join(lines) + "\n"


def migrate(user_dir: Path, dry_run: bool = False) -> dict:
    """옛 remember.jsonl → 세 갈래 파일. {rows, counts, table_path?, skipped?}. 이미 옮겼으면 skipped."""
    d = Path(user_dir)
    st = Store(d)
    rows = plan(d)
    counts = {k: sum(1 for r in rows if r["kind"] == k) for k in KINDS}
    if dry_run:
        return {"rows": rows, "counts": counts, "table": table(rows)}
    if st.active():
        return {"rows": [], "counts": counts, "skipped": "이미 세 갈래로 옮겼다"}
    day = datetime.now().strftime("%Y%m%d-%H%M%S")
    src = d / "remember.jsonl"
    if src.is_file():
        shutil.copy2(src, d / f"remember.jsonl.bak-{day}")
    for k in KINDS:
        for r in [r for r in rows if r["kind"] == k]:
            st.add({"id": r["id"], "ts": r["ts"], "kind": k, "text": r["text"], "source": r["source"], "scope": r["scope"],
                    "place": r["place"], "where": r["where"], "run": r.get("run")})
    tp = d / f"migrate-{day}.md"
    tp.write_text(table(rows), encoding="utf-8")
    (d / MARKER).write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "counts": counts, "table": tp.name},
                                       ensure_ascii=False), encoding="utf-8")
    return {"rows": rows, "counts": counts, "table_path": str(tp)}


def revert(user_dir: Path) -> dict:
    """세 갈래 파일을 reverted-<날짜>/ 로 옮기고 옛 remember.jsonl 로 돌아간다(다시 자동으로 옮기지 않는다)."""
    d = Path(user_dir)
    day = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = d / f"reverted-{day}"
    moved = []
    for name in ("policy.md", "profile.md", "knowledge"):
        p = d / name
        if p.exists():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest / name))
            moved.append(name)
    (d / MARKER).write_text(json.dumps({"reverted": True, "at": datetime.now().isoformat(timespec="seconds"),
                                        "moved_to": dest.name if moved else None}, ensure_ascii=False), encoding="utf-8")
    return {"moved": moved, "to": str(dest) if moved else None}


def auto_migrate(user_dir: Path) -> dict | None:
    """처음 뜰 때 한 번: 옛 기억이 있고 아직 옮기지 않았고 되돌린 적이 없으면 옮긴다. 옮겼으면 결과, 아니면 None.
    STEWART_MEMORY_MIGRATE=0 이면 하지 않는다."""
    d = Path(user_dir)
    if os.environ.get("STEWART_MEMORY_MIGRATE") == "0":
        return None
    st = Store(d)
    if st.marker() or st.active() or not (d / "remember.jsonl").is_file():
        return None
    r = migrate(d)
    return r if not r.get("skipped") else None
