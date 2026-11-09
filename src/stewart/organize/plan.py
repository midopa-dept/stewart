"""이동 계획: 모델은 plan_add·plan_drop·plan_show 로만 계획을 만든다. 파일은 기록 폴더의 plan.json 하나.

한 건: {id, op move|to_delete, src, dst, type file|dir, size, mtime_ns, reason, confidence, source, new_folder, warning}
- src·dst 는 root 기준 상대 경로. src 는 scan 의 디스크 이름 그대로, dst 의 이미 있는 폴더 부분도 디스크 이름으로 바꿔 둔다
  (모델이 NFC 로 써도 NFD 폴더에 들어가게, 대소문자가 다른 새 폴더를 따로 만들지 않게).
- 비교는 names.key(NFC, 대소문자 무시 FS 면 casefold).
- to_delete 의 dst 는 _to_delete/<src> 로 여기서 정한다.
- media 제안 한 건과 같은 칸(destination→dst, reason, confidence, source, new_folder, warning)을 쓴다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from . import names, scan as S

VERSION = 1
PLAN_MAX = 100_000


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class ScanIndex:
    """scan jsonl 을 열쇠 → 항목으로 읽어 둔다."""

    def __init__(self, path: Path, fold: bool):
        self.fold = fold
        self.by_key: dict[str, dict] = {}
        for it in S.iter_items(path):
            self.by_key[it["key"]] = it

    def get(self, rel: str) -> dict | None:
        return self.by_key.get(names.key(rel, self.fold))


UNCONFIRMED_BELOW = 0.5   # apply 의 검토 화면이 '낮은 확신' 으로 따로 보이는 기준과 같다


EVIDENCE = ("scan", "search", "name_only")   # 근거 꼴: 훑기·엿보기로 확인 / 검색 결과로 확인 / 이름만 보고 짐작


def is_unconfirmed(row: dict) -> bool:
    """미확정 항목: 확신 0.5 아래, reason 이 '미확정' 으로 시작, 근거가 이름뿐, 또는 도구가 강제(unconfirmed_why — 0건 검색 뒤)."""
    return (float(row.get("confidence", 0.5)) < UNCONFIRMED_BELOW or str(row.get("reason", "")).lstrip().startswith("미확정")
            or row.get("evidence") == "name_only" or bool(row.get("unconfirmed_why")))


def finalize(plan: "Plan", run: dict) -> None:
    """실행이 끝날 때(어떤 종료 사유든) 계획에 실행 요약을 적는다. 항목은 그대로 둔다."""
    for it in plan.items:
        it["unconfirmed"] = is_unconfirmed(it)
    plan.data["run"] = {**run, "items": len(plan.items), "unconfirmed": sum(1 for it in plan.items if it["unconfirmed"]),
                        "finished": datetime.now().isoformat(timespec="seconds")}
    plan.save()


class Plan:
    def __init__(self, path: Path, data: dict, index: ScanIndex):
        self.path, self.data, self.index = Path(path), data, index
        self.fold = data["case_insensitive"]

    # ── 만들기·읽기 ──
    @classmethod
    def create(cls, path: Path, root: Path, scan_id: str, scan_path: Path, fold: bool) -> "Plan":
        data = {"version": VERSION, "root": str(root), "scan_id": scan_id, "scan_path": str(scan_path),
                "case_insensitive": fold, "created": datetime.now().isoformat(timespec="seconds"), "items": []}
        p = cls(path, data, ScanIndex(scan_path, fold))
        p.save()
        return p

    @classmethod
    def load(cls, path: Path) -> "Plan":
        data = json.loads(Path(path).read_text(encoding="utf-8", errors="surrogateescape"))
        if data.get("version") != VERSION:
            raise ValueError(f"모르는 계획 판: {data.get('version')}")
        return cls(path, data, ScanIndex(Path(data["scan_path"]), data["case_insensitive"]))

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        # 리눅스의 UTF-8 이 아닌 파일 이름(surrogateescape)도 바이트 그대로 남긴다
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", errors="surrogateescape")
        tmp.replace(self.path)

    @property
    def items(self) -> list[dict]:
        return self.data["items"]

    def k(self, rel: str) -> str:
        return names.key(rel, self.fold)

    # ── 검사 ──
    def _disk_dst(self, dst: str) -> tuple[str, bool]:
        """dst 의 이미 있는 폴더 부분(scan)과 계획이 이미 만든 새 폴더 부분을 처음 본 철자로 바꾼다. (dst, 새 폴더인지)"""
        parts = [x for x in dst.replace("\\", "/").split("/") if x]
        out: list[str] = []
        new_folder = False
        planned = {}
        for it in self.items:
            ps = it["dst"].split("/")
            for i in range(1, len(ps)):
                planned.setdefault(self.k("/".join(ps[:i])), "/".join(ps[:i]))
        for i, part in enumerate(parts):
            pref = "/".join(out + [part])
            last = i == len(parts) - 1
            hit = self.index.get(pref)
            if not last and hit and hit["type"] == "dir":
                out = hit["rel"].split("/")
                continue
            if not last and self.k(pref) in planned:
                out = planned[self.k(pref)].split("/")
                continue
            if not last:
                new_folder = True
            out.append(part)
        return "/".join(out), new_folder

    def check(self, it: dict) -> tuple[dict | None, str | None]:
        op = it.get("op", "move")
        if op not in ("move", "to_delete"):
            return None, "op 은 move 또는 to_delete"
        ev = it.get("evidence", "")
        if ev and ev not in EVIDENCE:
            return None, f"evidence 는 {', '.join(EVIDENCE)} 중 하나"
        src = str(it.get("src", "")).strip().rstrip("/")     # 앞 '/' 는 떼지 않는다(절대 경로는 거부)
        if (why := names.parts_ok(src)):
            return None, f"src: {why}"
        s = self.index.get(src)
        if s is None:
            return None, f"src 가 scan 에 없다: {src}"
        if s["to_delete"]:
            return None, f"이미 {names.TO_DELETE}/ 안이다(계획의 src 로 못 씀): {src}"
        if s["type"] == "link":
            return None, f"심볼릭 링크는 옮기지 않는다: {src}"
        sk = s["key"]
        if op == "to_delete":
            dst_in = f"{names.TO_DELETE}/{s['rel']}"
        else:
            dst_in = str(it.get("dst", "")).strip().rstrip("/")
            if (why := names.parts_ok(dst_in)):
                return None, f"dst: {why}"
            if names.under_to_delete(self.k(dst_in), self.fold):
                return None, f"지울 것은 op=to_delete 로 한다({names.TO_DELETE}/ 로 직접 옮기지 않는다)"
        dst, new_folder = self._disk_dst(dst_in)
        dk = self.k(dst)
        if dk == sk:
            return None, "src 와 dst 가 같다(정규화·대소문자만 다른 이름 바꾸기는 받지 않는다)"
        if dk.startswith(sk + "/"):
            return None, "폴더를 자기 안으로 옮길 수 없다"
        if self.index.get(dst) is not None:
            return None, f"dst 가 이미 있다(덮어쓰기 없음): {dst}"
        ps = dst.split("/")
        for i in range(1, len(ps)):
            a = self.index.get("/".join(ps[:i]))
            if a is not None and a["type"] != "dir":
                return None, f"dst 의 상위 '{'/'.join(ps[:i])}' 가 폴더가 아니다"
        for o in self.items:
            ok_, odk = self.k(o["src"]), self.k(o["dst"])
            if ok_ == sk:
                return None, f"같은 src 가 이미 계획에 있다(#{o['id']})"
            if odk == dk:
                return None, f"같은 dst 가 이미 계획에 있다(#{o['id']})"
            if o["type"] == "dir" and sk.startswith(ok_ + "/"):
                return None, f"이 항목은 이미 옮기는 폴더(#{o['id']} {o['src']}) 안에 있다"
            if s["type"] == "dir" and ok_.startswith(sk + "/"):
                return None, f"이 폴더 안 항목(#{o['id']} {o['src']})을 이미 따로 옮긴다"
            if o["type"] == "dir" and dk.startswith(ok_ + "/"):
                return None, f"dst 가 옮겨 가는 폴더(#{o['id']} {o['src']}) 안이다"
            if s["type"] == "dir" and odk.startswith(sk + "/"):
                return None, f"다른 건(#{o['id']})의 dst 가 이 폴더 안이다"
            if odk.startswith(dk + "/") or dk.startswith(odk + "/"):
                return None, f"dst 가 다른 건(#{o['id']})의 dst 와 겹친다(한쪽이 다른 쪽 안)"
        try:
            conf = float(it.get("confidence", 0.5))
        except (TypeError, ValueError):
            return None, "confidence 는 0~1 숫자"
        row = {"op": op, "src": s["rel"], "dst": dst, "type": s["type"], "size": s["size"], "mtime_ns": s["mtime_ns"],
               "reason": str(it.get("reason", ""))[:500], "confidence": max(0.0, min(1.0, conf)),
               "source": it.get("source", "model") if it.get("source") in ("rule", "model", "human") else "model",
               "new_folder": new_folder, "warning": it.get("warning", ""), "evidence": ev or "unspecified"}
        row["unconfirmed"] = is_unconfirmed(row)
        return row, None

    def add(self, items: list[dict]) -> tuple[list[dict], list[tuple[int, str]]]:
        added, refused = [], []
        for i, it in enumerate(items, 1):
            if len(self.items) >= PLAN_MAX:
                refused.append((i, f"계획은 {PLAN_MAX}건까지"))
                continue
            if not isinstance(it, dict):
                refused.append((i, "항목은 {op, src, dst, reason, confidence}"))
                continue
            row, why = self.check(it)
            if why:
                refused.append((i, why))
                continue
            row["id"] = (max((x["id"] for x in self.items), default=0) + 1)
            self.items.append(row)
            added.append(row)
        self.save()
        return added, refused

    def drop(self, ids: list[int]) -> int:
        want = {int(x) for x in ids}
        before = len(self.items)
        self.data["items"] = [x for x in self.items if x["id"] not in want]
        self.save()
        return before - len(self.items)


def from_media(result: dict, plan: Plan) -> tuple[list[dict], list[tuple[str, str]]]:
    """media result.json 의 제안(파일 이름 → destination 폴더)을 계획 항목으로. 이름이 scan 에 한 곳에만 있을 때만."""
    by_name: dict[str, list[dict]] = {}
    for it in plan.index.by_key.values():
        if it["type"] == "file" and not it["to_delete"]:
            by_name.setdefault(names.key(it["rel"].rsplit("/", 1)[-1], plan.fold), []).append(it)
    rows, skipped = [], []
    for m in result.get("items", []):
        dest = (m.get("destination") or "").strip("/")
        if not dest or dest.startswith("_review") or dest.startswith("_"):
            skipped.append((m.get("file", ""), "검토(_review 등) 제안은 옮기지 않는다"))
            continue
        hits = by_name.get(names.key(m.get("file", ""), plan.fold), [])
        if len(hits) != 1:
            skipped.append((m.get("file", ""), f"scan 에서 이 이름이 {len(hits)}곳"))
            continue
        name = hits[0]["rel"].rsplit("/", 1)[-1]
        rows.append({"op": "move", "src": hits[0]["rel"], "dst": f"{dest}/{name}", "reason": m.get("reason", ""),
                     "confidence": m.get("confidence", 0.5), "source": "rule" if m.get("source") == "rule" else "model",
                     "warning": m.get("warning", "")})
    return rows, skipped
