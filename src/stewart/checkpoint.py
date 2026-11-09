"""체크포인트·되돌리기(git 아닌 폴더도). 쓰기 도구가 파일을 바꾸기 직전에 원래 파일을 기록 폴더에 보관한다.

- 체크포인트 = chat 은 사람 입력 한 차례, run 은 실행 하나. 기록 폴더/checkpoints/NNN/manifest.json + files/
- 한 체크포인트 안에서 같은 파일은 처음 한 번만 보관한다(그 차례가 시작될 때의 모습).
- 되돌리기(rewind N): N 번과 그 뒤 체크포인트를 거꾸로 풀어 N 이 시작될 때로 돌린다.
  · 원래 있던 파일 → 보관본으로 되씌움. 새로 생긴 파일 → 지우지 않고 옮겨 둔다.
  · 되돌리기 직전의 지금 파일은 모두 checkpoints/rewound-<시각>/ 에 남긴다(되돌리기도 되돌릴 수 있게).
  · 도구가 쓴 뒤 바깥(셸 등)에서 또 바뀐 파일은 표시한다.
- 덮는 범위: 쓰기 도구(write_file·edit_file·multi_edit·notebook_edit)의 path 뿐이다. 셸 명령이 바꾼 파일은 모른다.
  폴더 정리(organize)의 이동은 그쪽 되돌리기(apply.undo)가 맡는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

CHECKPOINT_TOOLS = frozenset({"write_file", "edit_file", "multi_edit", "notebook_edit"})
MAX_BACKUP_BYTES = 50 * 1024 * 1024


def _sha(p: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def target_path(workdir: Path, args: dict) -> Path | None:
    p = args.get("path")
    if not isinstance(p, str) or not p:
        return None
    q = Path(os.path.expanduser(p))
    return (q if q.is_absolute() else Path(workdir) / q).resolve()


class Checkpoints:
    def __init__(self, run_dir: Path, workdir: Path):
        self.root = Path(run_dir) / "checkpoints"
        self.workdir = Path(workdir)
        self.cur: dict | None = None
        self._cur_dir: Path | None = None

    # ---- 기록 --------------------------------------------------------------
    def begin(self, label: str) -> int:
        n = len(self.list()) + 1
        self._cur_dir = self.root / f"{n:03d}"
        self.cur = {"id": n, "label": label[:200], "started": datetime.now().isoformat(timespec="seconds"), "files": []}
        return n

    def _save(self) -> None:
        self._cur_dir.mkdir(parents=True, exist_ok=True)
        (self._cur_dir / "manifest.json").write_text(json.dumps(self.cur, ensure_ascii=False, indent=1), encoding="utf-8")

    def before_write(self, tool: str, args: dict) -> dict | None:
        """쓰기 도구 실행 직전. 이 체크포인트에서 처음 건드리는 파일이면 보관한다."""
        if tool not in CHECKPOINT_TOOLS or self.cur is None:
            return None
        p = target_path(self.workdir, args)
        if p is None:
            return None
        for e in self.cur["files"]:
            if e["path"] == str(p):
                return e
        e = {"path": str(p), "tool": tool, "existed": p.is_file()}
        if e["existed"]:
            size = p.stat().st_size
            e.update(size=size, sha=_sha(p))
            if size > MAX_BACKUP_BYTES:
                e["too_big"] = True
            else:
                k = len(self.cur["files"])
                dst = self._cur_dir / "files" / str(k)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
                e["backup"] = f"files/{k}"
        self.cur["files"].append(e)
        self._save()
        return e

    def after_write(self, tool: str, args: dict, ok: bool) -> None:
        if tool not in CHECKPOINT_TOOLS or self.cur is None:
            return
        p = target_path(self.workdir, args)
        for e in self.cur["files"]:
            if p is not None and e["path"] == str(p):
                e["after_sha"] = _sha(p) if p.is_file() else None
                self._save()

    # ---- 읽기·되돌리기 -------------------------------------------------------
    def list(self) -> list[dict]:
        out = []
        if self.root.is_dir():
            for d in sorted(x for x in self.root.iterdir() if x.is_dir() and x.name.isdigit()):
                try:
                    out.append(json.loads((d / "manifest.json").read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    continue
        return out

    def rewind(self, to_id: int) -> dict:
        """to_id 체크포인트가 시작될 때로 파일을 돌린다. {"restored", "moved_aside", "outside_changes", "skipped", "aside_dir"}."""
        cps = [c for c in self.list() if c["id"] >= to_id]
        if not cps:
            raise ValueError(f"체크포인트 {to_id} 이후가 없다")
        aside = self.root / f"rewound-{datetime.now():%Y%m%d-%H%M%S}"
        rep = {"to": to_id, "checkpoints": [c["id"] for c in cps], "restored": [], "moved_aside": [],
               "outside_changes": [], "skipped": [], "aside_dir": str(aside)}
        latest: dict[str, dict] = {}
        for c in cps:   # 파일마다 가장 늦은 체크포인트의 after_sha 가 '지금이어야 할 모습'
            for e in c["files"]:
                latest[e["path"]] = e
        k = 0
        for c in sorted(cps, key=lambda c: -c["id"]):
            for e in reversed(c["files"]):
                p = Path(e["path"])
                if latest.get(e["path"]) is e and p.is_file() and e.get("after_sha") and _sha(p) != e["after_sha"]:
                    rep["outside_changes"].append(e["path"])
                if p.is_file():   # 지금 모습을 옆에 남긴다
                    aside.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, aside / str(k))
                    (aside / f"{k}.path").write_text(str(p), encoding="utf-8")
                    k += 1
                if e.get("existed"):
                    if e.get("too_big") or not e.get("backup"):
                        rep["skipped"].append(e["path"])
                        continue
                    p.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(self.root / f"{c['id']:03d}" / e["backup"], p)
                    rep["restored"].append(e["path"])
                elif p.is_file():
                    p.unlink()     # 지금 모습은 위에서 aside 에 복사해 두었다
                    rep["moved_aside"].append(e["path"])
        rep["restored"] = sorted(set(rep["restored"]))
        rep["moved_aside"] = sorted(set(rep["moved_aside"]) - set(rep["restored"]))
        with (self.root / "rewinds.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rep, ensure_ascii=False) + "\n")
        return rep
