"""지난 세션 정리(U2-8): 지우기(휴지통) · 되돌리기 · 휴지통 비우기 · 이름 바꾸기 · 보관(목록에서 숨김).

- 지우는 것은 대화 기록뿐이다: 작업자 = 허브 폴더의 sessions/<이름> + 그 기록 폴더(run_dir) + 등록부 항목,
  지난 메인 = 그 기록 폴더(runs/…-hub-main…). 작업 폴더·worktree 의 파일은 건드리지 않는다.
- 지우기는 곧바로 없애지 않고 허브 폴더 trash/<id>/ 로 옮긴다(manifest.json 에 어디서 왔는지). 휴지통을 비우기 전까지 되돌린다.
- 도는 세션(작업자 starting·idle·working·승인 기다림, 지금 메인)은 지우지 않는다 — 먼저 멈춘다.
- 이름·보관은 허브 폴더 session-meta.json 에(기록 파일은 그대로).
"""

from __future__ import annotations

import json
import secrets
import shutil
import time
from datetime import datetime
from pathlib import Path

from .hub import LIVE, read_json, write_json

KEEP_DAYS_NOTE = "휴지통을 비우기 전까지 되돌릴 수 있다"


def _iso_ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


_INFO: dict[str, tuple] = {}   # 기록 폴더 → ((mtime, 크기), 요약) — 목록을 30초마다 다시 읽어도 바뀐 기록만 연다


def _run_info(d: Path) -> dict | None:
    """기록 하나의 요약: {workdir, profile, started, resumed_from, first, turns, last}. 사람 말이 없거나 못 읽으면 None."""
    from .hubmain import _strip_env
    from .images import text_of
    try:
        st = (d / "messages.json").stat()
    except OSError:
        return None
    key = (st.st_mtime_ns, st.st_size)
    hit = _INFO.get(str(d))
    if hit and hit[0] == key:
        return hit[1]
    try:
        from .record import head_meta
        meta = head_meta(d)   # 판 139: 첫 줄이 run_start 가 아닌 기록도
        msgs = json.loads((d / "messages.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    users = [text_of(m.get("content")) for m in msgs if m.get("role") == "user"]
    info = None
    if users:   # 사람 말이 없던 빈 대화는 보이지 않는다
        first = next((u for u in (_strip_env(x) for x in users) if u and not u.startswith("[엔진]")), "")
        fork = (d / "hub-origin.json").is_file()   # 갈래(원래 대화도 따로 이어진다) — 원래를 숨기지 않는다
        info = {"workdir": str(meta.get("workdir") or ""), "profile": meta.get("profile") or "", "started": meta.get("started"),
                "resumed_from": "" if fork else (meta.get("resumed_from") or ""), "first": " ".join(first.split())[:120], "turns": len(users),
                "last": st.st_mtime}
    _INFO[str(d)] = (key, info)
    return info


def _kind_of(profile: str) -> str:
    from . import surfaces as SF
    return "talk" if profile == SF.TALK_PROFILE else "code"


def past_mains(runs_dir: Path, project: Path | None = None, current: str | None = None, limit: int = 300,
               skip: set[str] | frozenset = frozenset()) -> list[dict]:
    """이 허브 기록 폴더의 지난 대화(지금 것·skip 빼고, 마지막으로 손댄 것부터):
    {run_dir, started, last, first, turns, place(talk·code), folder, here}.

    판 124.2: 작업 폴더로 거르지 않는다 — parlando 대화(대화 프로필 폴더)와 허브를 다른 코드 폴더로 켜던 때의 대화도 보인다
    (here = 지금 프로젝트 폴더의 대화인가). 다시 연 대화(resumed_from)가 있으면, 그 뒤로 손대지 않은 원래 기록은 숨긴다
    — 다시 연 쪽이 원래 내용을 모두 담는다(갈래처럼 원래 쪽도 이어 쓴 것은 둘 다 보인다)."""
    proj = str(Path(project).resolve()) if project else ""
    rows = []
    for d in Path(runs_dir).glob("*-hub-main*"):
        info = _run_info(d)
        if info is not None:
            rows.append((str(d), info))
    hidden = _hidden(rows)
    out = []
    for d, info in sorted(rows, key=lambda r: r[1]["last"], reverse=True):
        if d == current or d in skip or d in hidden:
            continue
        wd = info["workdir"]
        out.append({"run_dir": d, "started": info["started"], "last": info["last"], "first": info["first"], "turns": info["turns"],
                    "place": _kind_of(info["profile"]), "folder": Path(wd).name if wd else "",
                    "here": bool(proj) and bool(wd) and str(Path(wd).resolve()) == proj})
        if len(out) >= limit:
            break
    return out


def _hidden(rows: list[tuple[str, dict]]) -> set[str]:
    """다시 열기로 이어 받은 뒤 손대지 않은 원래 기록들(목록·찾기에서 숨긴다)."""
    newer: dict[str, float] = {}   # 원래 기록 → 그것을 다시 연 기록이 시작한 때(가장 늦은 것)
    for _, info in rows:
        rf = info["resumed_from"]
        t = _iso_ts(info["started"])
        if rf and t is not None:
            newer[rf] = max(newer.get(rf, 0.0), t)
    return {d for d, info in rows if d in newer and info["last"] <= newer[d] + 1}


def superseded(runs_dir: Path, key: str) -> list[str]:
    """key 가 이어 받은(다시 열기로 숨겨진) 앞 기록들 — 지울 때 함께 옮겨 옛 꼴로 다시 나타나지 않게."""
    by = {str(d): _run_info(d) for d in Path(runs_dir).glob("*-hub-main*")}
    out, cur = [], by.get(key)
    while cur and cur["resumed_from"] and cur["resumed_from"] in by and by[cur["resumed_from"]] and cur["resumed_from"] not in out:
        prev = by[cur["resumed_from"]]
        if prev["last"] > (_iso_ts(cur["started"]) or 0) + 1:   # 원래 쪽도 이어 쓴 갈래면 따로 보이는 대화다
            break
        out.append(cur["resumed_from"])
        cur = prev
    return out


class Manager:
    def __init__(self, hub, runs_dir: Path, project: Path, current_main: str | None = None, open_runs=frozenset()):
        self.hub, self.runs_dir, self.project = hub, Path(runs_dir), Path(project)
        self.current_main = current_main
        self.open_runs = set(open_runs)   # 지금 열린 대화 칸들의 기록(지난 목록에 넣지 않는다)
        self.meta_file = Path(hub.dir) / "session-meta.json"
        self.trash_dir = Path(hub.dir) / "trash"

    # ── 이름·보관 ──
    def meta(self) -> dict:
        d = read_json(self.meta_file, {}) or {}
        return {"workers": dict(d.get("workers") or {}), "mains": dict(d.get("mains") or {})}

    def _set_meta(self, kind: str, key: str, **fields) -> dict:
        d = self.meta()
        bucket = d["workers" if kind == "worker" else "mains"]
        e = {**bucket.get(key, {}), **fields}
        e = {k: v for k, v in e.items() if v not in (None, "", False)}
        if e:
            bucket[key] = e
        else:
            bucket.pop(key, None)
        write_json(self.meta_file, d)
        return e

    def rename(self, kind: str, key: str, title: str) -> dict:
        self._check(kind, key)
        title = " ".join(str(title or "").split())[:80]
        e = self._set_meta(kind, key, title=title or None)
        self.hub.event("session_rename", what=kind, key=key)
        return e

    def archive(self, kind: str, key: str, on: bool = True) -> dict:
        self._check(kind, key)
        e = self._set_meta(kind, key, archived=bool(on) or None)
        self.hub.event("session_archive" if on else "session_unarchive", what=kind, key=key)
        return e

    # ── 목록 ──
    def workers(self) -> list[dict]:
        meta = self.meta()["workers"]
        out = []
        for name, e in self.hub.registry()["sessions"].items():
            if e.get("role") in ("main", "chat") or e.get("status") not in ("paused", "stopped", "failed"):
                continue
            m = meta.get(name, {})
            out.append({"kind": "worker", "key": name, "name": name, "status": e["status"], "title": m.get("title", ""),
                        "archived": bool(m.get("archived")), "last_event": e.get("last_event") or e.get("started"),
                        "can_reopen": bool(e.get("run_dir"))})
        out.sort(key=lambda w: w["last_event"] or "", reverse=True)
        return out

    def mains(self) -> list[dict]:
        meta = self.meta()["mains"]
        out = []
        for m in past_mains(self.runs_dir, self.project, self.current_main, skip=self.open_runs):
            x = meta.get(m["run_dir"], {})
            out.append({**m, "kind": "main", "key": m["run_dir"], "title": x.get("title", ""), "archived": bool(x.get("archived"))})
        return out

    def older_than(self, days: float) -> list[tuple[str, str]]:
        """N일 넘게 손대지 않은 지난 세션(보관 포함): [(kind, key)]."""
        cut = time.time() - days * 86400
        out = []
        for w in self.workers():
            t = _iso_ts(w["last_event"])
            if t is not None and t < cut:
                out.append(("worker", w["key"]))
        for m in self.mains():
            t = _iso_ts(m.get("started"))
            if t is None:
                try:
                    t = (Path(m["run_dir"]) / "messages.json").stat().st_mtime
                except OSError:
                    t = None
            if t is not None and t < cut:
                out.append(("main", m["key"]))
        return out

    # ── 지우기·되돌리기 ──
    def _check(self, kind: str, key: str) -> None:
        if kind == "worker":
            e = self.hub.registry()["sessions"].get(key)
            if e is None or e.get("role") in ("main", "chat"):
                raise ValueError(f"없는 작업자: {key}")
            if e.get("status") in LIVE:
                raise RuntimeError(f"{key} 는 도는 중이다({e['status']}) — 먼저 멈추거나 끝낸 뒤 지운다")
        elif kind == "main":
            if key == self.current_main:
                raise RuntimeError("지금 메인 대화는 지우지 않는다(다른 대화를 다시 열거나 허브를 --new-main 으로 띄운 뒤)")
            if key in self.open_runs:
                raise RuntimeError("열려 있는 대화다 — 먼저 닫은 뒤 지운다")
            if key not in {m["run_dir"] for m in past_mains(self.runs_dir, self.project, self.current_main, limit=100000, skip=self.open_runs)}:
                raise ValueError("이 허브의 지난 대화 목록에 없는 기록")
        else:
            raise ValueError(f"모르는 종류: {kind}")

    def delete(self, kind: str, key: str) -> str:
        """휴지통으로 옮긴다. 휴지통 id 를 돌려준다."""
        self._check(kind, key)
        tid = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
        dest = self.trash_dir / tid
        dest.mkdir(parents=True)
        moved, entry = [], None
        meta = self.meta()["workers" if kind == "worker" else "mains"].get(key, {})
        if kind == "worker":
            reg = self.hub.registry()
            entry = reg["sessions"][key]
            srcs = [Path(self.hub.dir) / "sessions" / key]
            if entry.get("run_dir"):
                srcs.append(Path(entry["run_dir"]))
            label = meta.get("title") or key
        else:
            srcs = [Path(key)] + [Path(x) for x in superseded(self.runs_dir, key)]   # 판 124.2: 숨겨진 앞 기록도 함께
            first = next((m["first"] for m in past_mains(self.runs_dir, self.project, self.current_main, limit=100000, skip=self.open_runs)
                          if m["run_dir"] == key), "")
            label = meta.get("title") or (f"메인 · {first}" if first else Path(key).name)
        for i, src in enumerate(srcs):
            if src.exists():
                rel = f"{i}-{src.name}"
                shutil.move(str(src), str(dest / rel))
                moved.append([str(src), rel])
        man = {"id": tid, "kind": kind, "key": key, "label": label, "deleted_at": time.time(), "moved": moved,
               "entry": entry, "meta": meta}
        write_json(dest / "manifest.json", man)
        if kind == "worker":   # 등록부에서 뺀다(같은 이름으로 다시 띄울 수 있다)
            with self.hub._lock:
                reg = self.hub.registry()
                reg["sessions"].pop(key, None)
                self.hub._save(reg)
        self._set_meta(kind, key, title=None, archived=None)
        self.hub.event("session_trash", what=kind, key=key, trash=tid)
        return tid

    def trash(self) -> list[dict]:
        out = []
        if not self.trash_dir.is_dir():
            return out
        for d in sorted(self.trash_dir.iterdir(), reverse=True):
            m = read_json(d / "manifest.json", None)
            if isinstance(m, dict):
                out.append({k: m.get(k) for k in ("id", "kind", "key", "label", "deleted_at")})
        return out

    def restore(self, tid: str) -> dict:
        d = self.trash_dir / Path(tid).name
        m = read_json(d / "manifest.json", None)
        if not isinstance(m, dict):
            raise ValueError(f"휴지통에 없다: {tid}")
        if m["kind"] == "worker" and m["key"] in self.hub.registry()["sessions"]:
            raise RuntimeError(f"같은 이름의 작업자({m['key']})가 이미 있다 — 그 작업자를 지우거나 이름이 다른 뒤 되돌린다")
        for src, rel in m["moved"]:
            if Path(src).exists():
                raise RuntimeError(f"되돌릴 자리에 이미 무언가 있다: {src}")
        for src, rel in m["moved"]:
            Path(src).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(d / rel), src)
        if m["kind"] == "worker" and m.get("entry"):
            with self.hub._lock:
                reg = self.hub.registry()
                reg["sessions"][m["key"]] = m["entry"]
                self.hub._save(reg)
        if m.get("meta"):
            self._set_meta(m["kind"], m["key"], **m["meta"])
        shutil.rmtree(d, ignore_errors=True)
        self.hub.event("session_restore", what=m["kind"], key=m["key"], trash=tid)
        return {"kind": m["kind"], "key": m["key"], "label": m.get("label")}

    def empty(self, older_days: float | None = None) -> int:
        """휴지통 비우기(되돌릴 수 없다). older_days 면 그보다 오래 전에 지운 것만."""
        n = 0
        cut = time.time() - older_days * 86400 if older_days is not None else None
        for t in self.trash():
            if cut is not None and (t.get("deleted_at") or 0) > cut:
                continue
            shutil.rmtree(self.trash_dir / t["id"], ignore_errors=True)
            n += 1
        self.hub.event("trash_empty", count=n, older_days=older_days)
        return n


def past_search(runs_dir: Path, project, q: str, limit: int = 30, scan: int = 300) -> list[dict]:
    """판 112(SW-45 ⑦, 사람용): 이 허브의 지난 대화에서 사람 말·모델 답 글을 낱말로 찾는다(대소문자 무시).
    {run_dir, started, first, role, snippet[, kind]}. 모델 문맥과 무관(화면 검색).
    판 124.2: 작업 폴더로 거르지 않는다(project 는 옛 부름 꼴 맞춤으로만 받는다), kind 는 기록의 프로필에서."""
    from .hubmain import _strip_env
    from .images import text_of
    q = " ".join(str(q or "").split()).lower()
    if len(q) < 2:
        return []
    out, n = [], 0
    hidden = _hidden([(str(d), i) for d in Path(runs_dir).glob("*-hub-main*") if (i := _run_info(d)) is not None])
    for d in sorted(Path(runs_dir).glob("*-hub-main*"), reverse=True):
        if not (d / "messages.json").is_file() or str(d) in hidden:
            continue
        n += 1
        if n > scan:
            break
        try:
            from .record import head_meta
            meta = head_meta(d)   # 판 139: 첫 줄이 run_start 가 아닌 기록도
            msgs = json.loads((d / "messages.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        users = [_strip_env(text_of(m.get("content"))) for m in msgs if m.get("role") == "user"]
        first = next((u for u in users if u and not u.startswith("[엔진]")), "")
        for m in msgs:
            if m.get("role") not in ("user", "assistant"):
                continue
            t = " ".join((_strip_env(text_of(m.get("content"))) if m.get("role") == "user" else str(m.get("content") or "")).split())
            i = t.lower().find(q)
            if i < 0 or t.startswith("[엔진]"):
                continue
            out.append({"run_dir": str(d), "started": meta.get("started"), "first": first[:80], "role": m["role"], "kind": _kind_of(meta.get("profile") or ""), "folder": Path(str(meta.get("workdir") or "")).name,
                        "snippet": ("…" if i > 40 else "") + t[max(0, i - 40):i + len(q) + 60] + ("…" if i + len(q) + 60 < len(t) else "")})
            break   # 대화마다 첫 맞음 하나
        if len(out) >= limit:
            break
    return out
