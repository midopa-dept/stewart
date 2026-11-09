"""실행기: 사람이 검토(review)한 계획 그대로만 옮긴다(apply). 이동 기록을 역순으로 되돌린다(undo).

전제(plan.md 머리에도 적음):
- --approve 의 sha256 은 "검토한 그 계획 그대로" 만 보장한다(무결성). "사람만 apply 한다" 는 이 프로그램이 아니라
  서버 계정 권한으로 지킨다(정리를 돌리는 계정은 정리 폴더 읽기 전용, apply·undo 는 쓰기 권한이 있는 사람 계정).
- 덮어쓰기·지우기·복사를 하지 않는다. 옮기기는 os.rename 하나(같은 파일 시스템 안). 폴더는 만들기만 한다.
- 남는 위험: 검사(realpath·lexists)와 rename 사이에 같은 기계의 다른 프로세스가 경로를 바꿔치기하면 막지 못한다.
"""

from __future__ import annotations

import errno
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import names
from .plan import Plan, sha256_file

PREMISE = ("- 승인 sha256 은 검토한 그 계획 그대로만 보장한다(무결성). '사람만 apply' 는 실행기가 아니라 서버 계정 권한으로 지킨다"
           "(정리를 돌리는 계정은 읽기 전용, apply·undo 는 쓰기 권한 있는 사람 계정).\n"
           "- 실행기는 덮어쓰지 않고, 지우지 않고, 복사하지 않는다. 지울 것은 _to_delete/ 로 옮기기만 한다.\n"
           "- 계획 뒤 바뀐 파일(크기·수정 시각), 이미 있는 dst, root 밖으로 나가는 경로, 경로 중간의 심볼릭 링크, "
           "다른 파일 시스템으로 가는 이동은 건너뛴다.")
APPROVE_MIN = 12


# ── 검토 ─────────────────────────────────────────────────────────

def human(n: float) -> str:
    from .scan import human as h
    return h(n)


def _unc(x: dict) -> bool:
    from .plan import is_unconfirmed
    return is_unconfirmed(x)


def _run_state(plan) -> str:
    r = plan.data.get("run") or {}
    return ("끝남" if r.get("complete") else f"끝내지 못함({r.get('stop_reason')})") + (", 검색 한도 도달" if r.get("web_limit_hit") else "")


def review(plan_path: Path) -> tuple[str, Path]:
    """plan.md 를 쓰고 (sha256, plan.md 경로) 를 돌려준다."""
    plan = Plan.load(plan_path)
    sha = sha256_file(plan_path)
    items = plan.items
    moves = [x for x in items if x["op"] == "move"]
    dels = [x for x in items if x["op"] == "to_delete"]
    new_dirs = sorted({"/".join(x["dst"].split("/")[:-1]) for x in moves if x["new_folder"]})
    L = [f"# 폴더 정리 계획 검토 — {plan.data['root']}", "",
         f"- 계획 파일: {plan_path}", f"- sha256: `{sha}`",
         f"- 승인해 실행: `stewart organize apply {plan_path} --approve {sha[:APPROVE_MIN]}`"
         f" (먼저 `--dry-run` 으로 검사만 해 볼 수 있다)",
         f"- 건수: 옮기기 {len(moves)}건 {human(sum(x['size'] for x in moves))}, "
         f"_to_delete 로 {len(dels)}건 {human(sum(x['size'] for x in dels))}, 새 폴더 {len(new_dirs)}개, "
         f"미확정 {sum(1 for x in items if _unc(x))}건" + (f" — 실행 {_run_state(plan)}" if plan.data.get("run") else ""),
         "", "## 전제", "", PREMISE, "",
         "## 고치는 법", "",
         "- plan.json 에서 줄(항목)을 지우거나 dst 를 고친다. 고치면 sha 가 바뀌므로 다시 `stewart organize review` 를 돌린다.",
         "- 고친 항목은 source 를 human 으로 바꿔 두면 표에서 구분된다.", ""]
    warn = [x for x in items if x.get("warning")]
    from .plan import is_unconfirmed
    low = sorted([x for x in items if is_unconfirmed(x)], key=lambda x: x["confidence"])   # 미확정(확신 < 0.5·이름뿐·0건 뒤)
    if warn:
        L += ["## 경고", "", "| # | src | dst | 경고 |", "|---|---|---|---|"]
        L += [f"| {x['id']} | {_c(x['src'])} | {_c(x['dst'])} | {_c(x['warning'])} |" for x in warn] + [""]
    if low:
        L += ["## 확신 낮은 것·미확정(0.5 미만·근거가 이름뿐·0건 검색 뒤) — 먼저 본다", "",
              "| # | 확신 | 근거 꼴 | src | dst | 근거 |", "|---|---|---|---|---|---|"]
        L += [f"| {x['id']} | {x['confidence']:.2f} | {_c(x.get('evidence', '-'))}{(' — ' + _c(x['unconfirmed_why'])) if x.get('unconfirmed_why') else ''} "
              f"| {_c(x['src'])} | {_c(x['dst'])} | {_c(x['reason'])} |" for x in low] + [""]
    if dels:
        L += ["## _to_delete 로 옮길 것", "", "| # | src | 크기 | 근거 |", "|---|---|---|---|"]
        L += [f"| {x['id']} | {_c(x['src'])} | {human(x['size'])} | {_c(x['reason'])} |" for x in dels] + [""]
    if new_dirs:
        L += ["## 새로 만들 폴더", ""] + [f"- {_c(d)}" for d in new_dirs] + [""]
    groups: dict[str, list[dict]] = {}
    for x in moves:
        groups.setdefault(x["dst"].split("/")[0], []).append(x)
    L += ["## 옮기기(목적지 최상위 폴더별)", ""]
    for g in sorted(groups):
        xs = groups[g]
        L += [f"### {_c(g)}/ — {len(xs)}건 {human(sum(x['size'] for x in xs))}", "",
              "| # | 종류 | src | dst | 확신 | 미확정 | 출처 | 근거 |", "|---|---|---|---|---|---|---|---|"]
        L += [f"| {x['id']} | {x['type']} | {_c(x['src'])} | {_c(x['dst'])} | {x['confidence']:.2f} | {'예' if _unc(x) else ''} "
              f"| {x['source']} | {_c(x['reason'])} |" for x in xs] + [""]
    md = plan_path.with_suffix(".md")
    md.write_text("\n".join(L), encoding="utf-8")
    return sha, md


def _c(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


# ── 기록 ─────────────────────────────────────────────────────────

class Journal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.f = open(self.path, "a", encoding="utf-8", errors="surrogateescape")

    def write(self, rec: dict):
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), **rec}
        self.f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.f.flush()
        os.fsync(self.f.fileno())

    def close(self):
        self.f.close()


def read_journal(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8", errors="surrogateescape").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass                                   # 죽으며 반쯤 쓴 마지막 줄
    return out


def journal_state(recs: list[dict]) -> dict[int, dict]:
    """seq → 마지막 상태가 반영된 이동 한 건. start 만 있으면 state=start."""
    st: dict[int, dict] = {}
    for r in recs:
        if r.get("op") not in ("move", "to_delete", "undo"):
            continue
        s = st.setdefault(r["seq"], dict(r))
        s.update({k: v for k, v in r.items() if v is not None})
    return st


# ── 경로 검사 ────────────────────────────────────────────────────

def _inside(root_real: str, p: str) -> bool:
    return p == root_real or p.startswith(root_real.rstrip("/") + "/")


def _path_problem(root: Path, rel: str, must_exist: bool) -> str | None:
    """root 아래 rel 경로의 각 단계에 심볼릭 링크가 없고, 실제 경로가 root 안인지. 마지막 단계는 링크여도 된다(그 자체를 옮김)."""
    root_real = os.path.realpath(root)
    cur = Path(root)
    parts = rel.split("/")
    for i, part in enumerate(parts):
        cur = cur / part
        last = i == len(parts) - 1
        if not os.path.lexists(cur):
            if must_exist:
                return "없다"
            break
        if not last and os.path.islink(cur):
            return f"경로 중간에 심볼릭 링크: {'/'.join(parts[:i + 1])}"
        if not last and not _inside(root_real, os.path.realpath(cur)):
            return "실제 경로가 root 밖"
    return None


def _name_taken(parent: Path, name: str, fold: bool) -> str | None:
    """parent 안에 정규화·대소문자만 다른 같은 이름이 있으면 그 이름."""
    if not parent.is_dir():
        return None
    k = names.key(name, fold)
    try:
        for e in os.scandir(parent):
            if names.key(e.name, fold) == k:
                return e.name
    except OSError:
        return None
    return None


def _fingerprint_ok(p: Path, it: dict) -> str | None:
    try:
        s = os.lstat(p)
    except OSError:
        return "src 가 없다"
    if it["type"] == "dir":
        if not os.path.isdir(p) or os.path.islink(p):
            return "src 가 폴더가 아니다"
        if s.st_mtime_ns != it["mtime_ns"]:
            return "계획 뒤 바뀜(폴더 수정 시각)"
        return None
    if not os.path.isfile(p) or os.path.islink(p):
        return "src 가 파일이 아니다"
    if s.st_size != it["size"] or s.st_mtime_ns != it["mtime_ns"]:
        return "계획 뒤 바뀜(크기·수정 시각)"
    return None


def _mkdirs(root: Path, rel_dir: str, j: Journal | None, seq: int, dry: bool, made: list[str]) -> str | None:
    cur = Path(root)
    for i, part in enumerate([x for x in rel_dir.split("/") if x]):
        cur = cur / part
        if dry and str(cur.relative_to(root)) in made:
            continue                              # dry-run: 앞 건이 만들 폴더
        if os.path.lexists(cur):
            if os.path.islink(cur) or not cur.is_dir():
                return f"상위 '{part}' 가 폴더가 아니다"
            continue
        taken = _name_taken(cur.parent, part, names.case_insensitive(root)) if cur.parent.is_dir() else None
        if taken:
            return f"상위 폴더 이름이 정규화·대소문자만 다른 '{taken}' 와 겹친다"
        if dry:
            made.append(str(cur.relative_to(root)))
            continue
        os.mkdir(cur)
        rel = str(cur.relative_to(root))
        made.append(rel)
        if j:
            j.write({"op": "mkdir", "seq": seq, "path": rel})
    return None


# ── 실행 ─────────────────────────────────────────────────────────

def apply(plan_path: Path, approve: str, dry_run: bool = False, journal: Path | None = None) -> dict:
    plan_path = Path(plan_path)
    sha = sha256_file(plan_path)
    if not approve or len(approve) < APPROVE_MIN or not sha.startswith(approve.lower()):
        raise PermissionError(f"--approve 가 지금 계획의 sha256 앞 {APPROVE_MIN}자 이상과 같아야 한다"
                              f"(지금 계획 {sha[:APPROVE_MIN]}…). 계획이 바뀌었으면 review 를 다시 한다.")
    plan = Plan.load(plan_path)
    root = Path(plan.data["root"])
    fold = names.case_insensitive(root)
    jpath = Path(journal) if journal else plan_path.with_name("moves.jsonl")
    old = journal_state(read_journal(jpath))
    recover = reconcile(root, old)
    done_keys = {names.key(r["src"], fold) for r in old.values() if r.get("state") == "done" and r["op"] != "undo"}
    j = None if dry_run else Journal(jpath)
    for fx in recover if j else []:
        j.write({"seq": fx["seq"], "op": fx["op"], "state": fx["state"], "why": "복구: 앞선 실행이 중간에 끊김(디스크 상태로 맞춤)"})
    seq0 = max(old, default=0)
    res = {"plan_sha256": sha, "dry_run": dry_run, "journal": str(jpath), "moved": 0, "bytes": 0,
           "skipped": Counter(), "skipped_items": [], "made_dirs": [], "emptied_dirs": [], "recovered": recover}
    try:
        for n, it in enumerate(plan.items, 1):
            seq = seq0 + n
            src, dst = root / it["src"], root / it["dst"]

            def skip(why):
                res["skipped"][why.split("(")[0].split(":")[0]] += 1
                res["skipped_items"].append({"id": it["id"], "src": it["src"], "why": why})
                if j:
                    j.write({"op": it["op"], "seq": seq, "id": it["id"], "src": it["src"], "dst": it["dst"], "state": "skip", "why": why})

            if names.key(it["src"], fold) in done_keys:
                skip("이미 옮김(앞선 apply)")
                continue
            why = (_path_problem(root, it["src"], True) or _path_problem(root, it["dst"], False)
                   or _fingerprint_ok(src, it))
            if why:
                skip(why)
                continue
            if os.path.lexists(dst):
                skip("dst 가 이미 있다(덮어쓰기 없음)")
                continue
            taken = _name_taken(dst.parent, dst.name, fold)
            if taken:
                skip(f"dst 와 정규화·대소문자만 다른 '{taken}' 가 있다(덮어쓰기 없음)")
                continue
            why = _mkdirs(root, str(Path(it["dst"]).parent) if "/" in it["dst"] else "", j, seq, dry_run, res["made_dirs"])
            if why:
                skip(why)
                continue
            if dry_run:
                res["moved"] += 1
                res["bytes"] += it["size"]
                continue
            rec = {"op": it["op"], "seq": seq, "id": it["id"], "src": it["src"], "dst": it["dst"], "type": it["type"],
                   "size": it["size"], "mtime_ns": it["mtime_ns"]}
            j.write({**rec, "state": "start"})
            try:
                os.rename(src, dst)
            except OSError as e:
                why = "다른 파일 시스템(복사 안 함)" if e.errno == errno.EXDEV else f"옮기기 실패: {e.strerror}"
                res["skipped"][why.split("(")[0].split(":")[0]] += 1
                res["skipped_items"].append({"id": it["id"], "src": it["src"], "why": why})
                j.write({"seq": seq, "op": it["op"], "state": "skip", "why": why})
                continue
            j.write({"seq": seq, "op": it["op"], "state": "done"})
            res["moved"] += 1
            res["bytes"] += it["size"]
            if not dry_run:
                par = src.parent
                if par != root and par.is_dir() and not any(os.scandir(par)):
                    res["emptied_dirs"].append(str(par.relative_to(root)))
    finally:
        if j:
            j.close()
    res["skipped"] = dict(res["skipped"])
    return res


def reconcile(root: Path, state: dict[int, dict]) -> list[dict]:
    """start 만 있는 건(중간에 죽음)을 디스크 상태로 맞춘다: dst 에 있고 src 에 없으면 done, 반대면 skip. 기록만 고친다."""
    fixes = []
    pend = {s: r for s, r in state.items() if r.get("state") == "start"}
    if not pend:
        return fixes
    for s, r in pend.items():
        at_src, at_dst = os.path.lexists(Path(root) / r["src"]), os.path.lexists(Path(root) / r["dst"])
        r["state"] = "done" if (at_dst and not at_src) else "skip"
        fixes.append({"seq": s, "op": r["op"], "state": r["state"], "src": r["src"], "dst": r["dst"]})
    return fixes


# ── 되돌리기 ─────────────────────────────────────────────────────

def undo(journal: Path, approve: str, dry_run: bool = False, root: Path | None = None) -> dict:
    """moves.jsonl 의 done 건을 역순으로 되돌린다. 기록은 같은 폴더의 undo-moves.jsonl.
    지금 자리(dst)의 파일이 기록과 같고(크기·수정 시각, 폴더는 폴더인지만) 원래 자리(src)가 비어 있을 때만 옮긴다."""
    journal = Path(journal)
    sha = sha256_file(journal)
    if not approve or len(approve) < APPROVE_MIN or not sha.startswith(approve.lower()):
        raise PermissionError(f"--approve 가 지금 기록 파일의 sha256 앞 {APPROVE_MIN}자 이상과 같아야 한다(지금 {sha[:APPROVE_MIN]}…).")
    if root is None:
        plan_path = journal.with_name("plan.json")
        if not plan_path.exists():
            raise FileNotFoundError(f"root 를 알 plan.json 이 기록 옆에 없다: {plan_path}")
        root = Path(json.loads(plan_path.read_text(encoding="utf-8", errors="surrogateescape"))["root"])
    root = Path(root)
    fold = names.case_insensitive(root)
    upath = journal.with_name("undo-moves.jsonl")
    moves = journal_state(read_journal(journal))
    reconcile(root, moves)
    made = [r["path"] for r in read_journal(journal) if r.get("op") == "mkdir"]
    ustate = journal_state(read_journal(upath))
    urecover = reconcile(root, ustate)
    undone = {r["of"] for r in ustate.values() if r.get("state") == "done"}
    j = None if dry_run else Journal(upath)
    for fx in urecover if j else []:
        j.write({"seq": fx["seq"], "op": "undo", "state": fx["state"], "why": "복구: 앞선 되돌리기가 중간에 끊김"})
    seq0 = max(ustate, default=0)
    res = {"journal_sha256": sha, "dry_run": dry_run, "undo_journal": str(upath), "restored": 0, "skipped": Counter(),
           "skipped_items": [], "made_dirs": [], "recovered": urecover, "left_dirs": []}
    todo = [r for s_, r in sorted(moves.items(), reverse=True) if r.get("state") == "done" and r["op"] in ("move", "to_delete")]
    try:
        for n, r in enumerate(todo, 1):
            seq = seq0 + n
            cur_rel, back_rel = r["dst"], r["src"]
            cur, back = root / cur_rel, root / back_rel

            def skip(why):
                res["skipped"][why.split("(")[0].split(":")[0]] += 1
                res["skipped_items"].append({"id": r.get("id"), "src": cur_rel, "why": why})
                if j:
                    j.write({"op": "undo", "seq": seq, "of": r["seq"], "src": cur_rel, "dst": back_rel, "state": "skip", "why": why})

            if r["seq"] in undone:
                skip("이미 되돌림")
                continue
            if not os.path.lexists(cur):
                skip("지금 자리에 없다")
                continue
            why = _path_problem(root, cur_rel, True) or _path_problem(root, back_rel, False)
            if why:
                skip(why)
                continue
            try:
                st = os.lstat(cur)
            except OSError:
                skip("지금 자리에 없다")
                continue
            if r.get("type") == "dir":
                if not os.path.isdir(cur) or os.path.islink(cur):
                    skip("지금 자리의 것이 폴더가 아니다")
                    continue
            elif os.path.islink(cur) or not os.path.isfile(cur) or st.st_size != r["size"] or st.st_mtime_ns != r["mtime_ns"]:
                skip("옮긴 뒤 바뀜(크기·수정 시각) — 충돌")
                continue
            if os.path.lexists(back):
                skip("원래 자리에 다른 것이 있다(덮어쓰기 없음) — 충돌")
                continue
            taken = _name_taken(back.parent, back.name, fold)
            if taken:
                skip(f"원래 자리에 정규화·대소문자만 다른 '{taken}' 가 있다 — 충돌")
                continue
            why = _mkdirs(root, str(Path(back_rel).parent) if "/" in back_rel else "", j, seq, dry_run, res["made_dirs"])
            if why:
                skip(why)
                continue
            if dry_run:
                res["restored"] += 1
                continue
            rec = {"op": "undo", "seq": seq, "of": r["seq"], "src": cur_rel, "dst": back_rel, "type": r.get("type"),
                   "size": r.get("size"), "mtime_ns": r.get("mtime_ns")}
            j.write({**rec, "state": "start"})
            try:
                os.rename(cur, back)
            except OSError as e:
                res["skipped"]["옮기기 실패"] += 1
                res["skipped_items"].append({"id": r.get("id"), "src": cur_rel, "why": f"옮기기 실패: {e.strerror}"})
                j.write({"seq": seq, "op": "undo", "state": "skip", "why": f"옮기기 실패: {e.strerror}"})
                continue
            j.write({"seq": seq, "op": "undo", "state": "done"})
            res["restored"] += 1
    finally:
        if j:
            j.close()
    for d in sorted(set(made), key=lambda x: -x.count("/")):        # apply 가 만든 폴더 중 빈 것(지우지 않음)
        p = root / d
        if p.is_dir() and not os.path.islink(p) and not any(os.scandir(p)):
            res["left_dirs"].append(d)
    res["skipped"] = dict(res["skipped"])
    return res
