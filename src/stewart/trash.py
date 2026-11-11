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


def past_mains(runs_dir: Path, project: Path, current: str | None = None, limit: int = 200) -> list[dict]:
    """이 프로젝트의 지난 허브 메인 대화(지금 것 빼고, 새것부터): {run_dir, started, first, turns}."""
    from .hubmain import _strip_env
    from .images import text_of
    proj = str(Path(project).resolve())
    out = []
    for d in sorted(Path(runs_dir).glob("*-hub-main*"), reverse=True):
        if str(d) == current or not (d / "messages.json").is_file():
            continue
        try:
            with open(d / "events.jsonl", encoding="utf-8") as f:
                meta = (json.loads(f.readline()).get("meta") or {})
            if str(Path(meta.get("workdir") or "").resolve()) != proj:
                continue
            msgs = json.loads((d / "messages.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        users = [text_of(m.get("content")) for m in msgs if m.get("role") == "user"]
        if not users:   # 사람 말이 없던 빈 대화는 보이지 않는다
            continue
        first = next((u for u in (_strip_env(x) for x in users) if u), "")
        out.append({"run_dir": str(d), "started": meta.get("started"), "first": first[:120], "turns": len(users)})
        if len(out) >= limit:
            break
    return out


class Manager:
    def __init__(self, hub, runs_dir: Path, project: Path, current_main: str | None = None):
        self.hub, self.runs_dir, self.project = hub, Path(runs_dir), Path(project)
        self.current_main = current_main
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
        for m in past_mains(self.runs_dir, self.project, self.current_main):
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
            if key not in {m["run_dir"] for m in past_mains(self.runs_dir, self.project, self.current_main)}:
                raise ValueError("이 프로젝트의 지난 메인 대화 목록에 없는 기록")
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
            srcs = [Path(key)]
            first = next((m["first"] for m in past_mains(self.runs_dir, self.project, self.current_main) if m["run_dir"] == key), "")
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
