#!/usr/bin/env python3
"""조각(--tasks 로 나눠 돈) 평가 결과 폴더들을 합쳐 한 요약으로 만든다. 평가 담당, 2026-11-09.

평가기 판 폴더(<평가기 뿌리>/<판>/) 밖의 별도 스크립트다. 평가기 파일은 읽기만 하고(바이트코드도 쓰지 않음),
채점은 다시 하지 않는다. 각 조각의 results.jsonl 줄은 그 평가기가 이미 채점한 것이므로, 합친 줄에 그 평가기의
aggregate()·write_md() 를 그대로 불러 summary.json·summary.md 를 만든다(평가기 main() 끝과 같은 계산).

사용:
  python3 -I merge_shards.py --eval v2.0.1 --label <이름> <조각 폴더> [<조각 폴더> …]
  python3 -I merge_shards.py --eval … --label … --partial <조각 …> [--fix <고침 조각> …]   # 끊긴 조각 받기(11/10 총괄 지시)
    --partial: summary.json 이 없는 조각(끊긴 조각)도 읽는다 — meta 는 같은 목록의 끝난 조각 것을 빌리고, 결과 줄 중
               회차가 다 찬(reps 만큼) 과제만 받는다. 회차가 덜 찬 과제는 버리고 "빠진 과제" 로 적는다.
               backend_error 회차가 있는 과제도 버린다(등록 §8 "1회 다시" — 고침 차례로 다시 돈다).
    --fix <폴더>: 고침 차례 결과. 그 폴더의 과제는 다른 조각의 같은 과제를 통째로 대신한다(뒤에 준 것이 이긴다).
  python3 -I merge_shards.py --plan-fix <evalq 실행 폴더> <묶음 이름> [--no-backend-error]
    → 그 묶음에서 다시 돌릴 과제 목록(끊긴 조각의 덜 찬 과제 + 시작 못 한 조각의 과제 + backend_error 과제)을 JSON 한 줄로 낸다.
  python3 -I merge_shards.py --selftest <완주 결과 폴더> [--shards 4]   # 완주 결과를 조각으로 나눠 합친 수가 원래와 같은지

검사(하나라도 어기면 멈춤): 조각마다 같은 평가기 판·해시·stewart HEAD·reps·backend·max_steps·profile(있으면).
과제가 조각끼리 겹치면 멈춤. 합친 과제 수가 평가기 전체 과제와 다르면 invalid_reasons 에 "과제 일부만"을 남긴다.
조각의 invalid_reasons 중 "과제 일부만" 은 떼고 나머지는 모두 이어받는다.

환경 변수: STEWART_EVAL_ROOT = 평가기 판 폴더들의 뿌리(기본 이 스크립트의 두 단계 위 폴더),
          STEWART_RUNS_ROOT = 합친 결과를 쓸 폴더(기본 <STEWART_EVAL_ROOT>/../runs).
"""
import argparse
import importlib.util
import json
import os
import random
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True
EVAL_ROOT = Path(os.environ.get("STEWART_EVAL_ROOT") or Path(__file__).resolve().parent.parent)   # 평가기 판 폴더들의 뿌리
RUNS_ROOT = Path(os.environ.get("STEWART_RUNS_ROOT") or EVAL_ROOT.parent / "runs")
PARTIAL = "과제 일부만"
SAME_KEYS = ("eval_version", "eval_hash", "reps", "backend", "max_steps", "profile", "agent", "model")


def load_eval(ver: str):
    d = EVAL_ROOT / ver
    p = d / "run_eval.py"
    if not p.is_file():
        sys.exit(f"평가기 판이 없다: {d}")
    sys.path.insert(0, str(d))
    try:
        spec = importlib.util.spec_from_file_location(f"run_eval_{ver.replace('.', '_')}", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


def read_rows(d: Path) -> list[dict]:
    """results.jsonl 의 줄들. 끊긴 조각의 마지막 줄이 반쯤 쓰였으면 버린다."""
    out = []
    p = d / "results.jsonl"
    if not p.exists():
        return out
    for l in p.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        try:
            out.append(json.loads(l))
        except ValueError:
            pass
    return out


def read_shard(d: Path, offset: int = 0, borrow_meta: dict | None = None) -> tuple[dict, list[dict]]:
    if (d / "summary.json").exists():
        s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    elif borrow_meta is not None:
        s = {"meta": dict(borrow_meta, partial=True, n_tasks=None, wall_s=None, started=None)}
    else:
        sys.exit(f"summary.json 이 없다(끊긴 조각이면 --partial): {d}")
    rows = read_rows(d)
    if offset:
        for r in rows:
            r["rep"] = int(r["rep"]) + offset
            r["rep_orig"] = int(r["rep"]) - offset
    return s["meta"], rows


def split_arg(x: str) -> tuple[Path, int]:
    """'폴더' 또는 '폴더:+3'(회차 번호 밀기 — 같은 과제를 다른 대기열에서 더 돌린 회차를 이어 붙일 때)."""
    if ":+" in x:
        d, n = x.rsplit(":+", 1)
        return Path(d).resolve(), int(n)
    return Path(x).resolve(), 0


def validity(mod, rows: list[dict], reasons: list[str]) -> None:
    if hasattr(mod, "_validity"):
        mod._validity(rows, reasons)
        return
    for cond, msg in ((any(r.get("leak") for r in rows), "누설 의심 회차 있음"),
                      (any(r.get("access_suspicious") for r in rows), "작업 폴더 밖 의심 접근 회차 있음")):
        if cond and msg not in reasons:
            reasons.append(msg)


def merge(mod, metas: list[dict], rowsets: list[list[dict]], label: str, srcs: list[str], concat_reps: bool = False) -> tuple[dict, dict, list[dict]]:
    m0 = metas[0]
    keys = [k for k in SAME_KEYS if not (concat_reps and k == "reps")]
    for i, m in enumerate(metas[1:], 2):
        for k in keys:
            if m.get(k) != m0.get(k):
                sys.exit(f"조각 {i} 의 {k} 가 다르다: {m.get(k)!r} ≠ {m0.get(k)!r}")
        if m0.get("agent") != "claude" and (m.get("stewart") or {}).get("head") != (m0.get("stewart") or {}).get("head"):
            sys.exit(f"조각 {i} 의 stewart HEAD 가 다르다")
    seen = {}
    if concat_reps:
        pairs = {}
        for i, rows in enumerate(rowsets, 1):
            for r in rows:
                k = (r["task"], r["rep"])
                if k in pairs:
                    sys.exit(f"과제 {k[0]} 회차 {k[1]} 가 조각 {pairs[k]} 과 {i} 에 겹친다(회차 밀기 확인)")
                pairs[k] = i
                seen.setdefault(r["task"], i)
    else:
        for i, rows in enumerate(rowsets, 1):
            for t in {r["task"] for r in rows}:
                if t in seen:
                    sys.exit(f"과제 {t} 가 조각 {seen[t]} 과 {i} 에 겹친다")
                seen[t] = i
    rows = [r for rs in rowsets for r in rs]
    order = {t["id"]: n for n, t in enumerate(mod.load_tasks())}
    rows.sort(key=lambda r: (r["rep"], order.get(r["task"], 1 << 30)))
    reasons = []
    for m in metas:
        for x in m.get("invalid_reasons") or []:
            if x != PARTIAL and x not in reasons:
                reasons.append(x)
    all_ids = set(order)
    got = set(seen)
    if got != all_ids:
        reasons.append(PARTIAL)
    validity(mod, rows, reasons)
    meta = dict(m0)
    meta.update({
        "label": label, "n_tasks": len(got), "started": min(m.get("started") or "" for m in metas),
        "wall_s": max(m.get("wall_s") or 0 for m in metas),
        "valid": not reasons, "invalid_reasons": reasons,
        "merged_at": datetime.now().isoformat(timespec="seconds"),
        "merged_from": [{"dir": s, "n_tasks": m.get("n_tasks"), "wall_s": m.get("wall_s"), "machine": m.get("machine"),
                         "jobs": m.get("jobs"), "long_jobs": m.get("long_jobs")} for s, m in zip(srcs, metas)],
        "missing_tasks": sorted(all_ids - got),
    })
    if concat_reps:
        meta["reps"] = max(r["rep"] for r in rows)
        meta["concat_reps"] = True
    agg = mod.aggregate(rows, meta["reps"])
    return meta, agg, rows


def write_out(mod, out: Path, meta: dict, agg: dict, rows: list[dict]) -> None:
    out.mkdir(parents=True)
    with (out / "results.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out / "summary.json").write_text(json.dumps({"meta": meta, **agg}, ensure_ascii=False, indent=2), encoding="utf-8")
    mod.write_md(out, meta, agg)


def complete_tasks(rows: list[dict], reps: int, drop_backend_error: bool = True) -> tuple[list[dict], list[str]]:
    """회차가 1..reps 모두 있고(겹침 없이) backend_error 가 없는 과제의 줄만. (남긴 줄, 버린 과제)"""
    by = {}
    for r in rows:
        by.setdefault(r["task"], []).append(r)
    keep, dropped = [], []
    for t, rs in by.items():
        reps_seen = sorted(int(r["rep"]) for r in rs)
        bad = drop_backend_error and any(r.get("stop_reason") == "backend_error" for r in rs)
        if reps_seen == list(range(1, reps + 1)) and not bad:
            keep += rs
        else:
            dropped.append(t)
    return keep, sorted(dropped)


def cmd_merge(a) -> int:
    mod = load_eval(a.eval)
    parsed = [split_arg(d) for d in a.shards] + [(Path(f).resolve(), 0) for f in (a.fix or [])]
    nfix = len(a.fix or [])
    dirs = [d for d, _ in parsed]
    borrow = None
    if a.partial:
        done = [d for d in dirs if (d / "summary.json").exists()]
        if not done:
            sys.exit("--partial: meta 를 빌릴 끝난 조각이 하나도 없다")
        borrow = json.loads((done[0] / "summary.json").read_text(encoding="utf-8"))["meta"]
    metas, rowsets = map(list, zip(*(read_shard(d, off, borrow) for d, off in parsed)))
    dropped_all = {}
    if a.partial or nfix:
        reps = int(borrow["reps"]) if borrow else int(metas[0]["reps"])
        fixed = {r["task"] for rs in rowsets[len(rowsets) - nfix:] for r in rs} if nfix else set()
        for i, (d, rs) in enumerate(zip(dirs, rowsets)):
            is_fix = i >= len(rowsets) - nfix
            if not is_fix:
                rs = [r for r in rs if r["task"] not in fixed]          # 고침 차례가 대신하는 과제
            keep, dropped = complete_tasks(rs, reps)
            if dropped:
                dropped_all[str(d)] = dropped
            rowsets[i] = keep
        for i, m in enumerate(metas):
            m["n_tasks"] = len({r["task"] for r in rowsets[i]})
    if any(m.get("eval_version") != getattr(mod, "EVAL_VERSION", a.eval) for m in metas):
        sys.exit(f"조각의 eval_version 이 --eval {a.eval} 과 다르다")
    keep = [i for i, rs in enumerate(rowsets) if rs]                       # 줄이 하나도 안 남은 조각은 뺀다
    metas, rowsets, parsed = [metas[i] for i in keep], [rowsets[i] for i in keep], [parsed[i] for i in keep]
    meta, agg, rows = merge(mod, list(metas), list(rowsets), a.label, [f"{d}:+{o}" if o else str(d) for d, o in parsed], a.concat_reps)
    if a.partial or nfix:
        meta["partial_merge"] = {"dropped_tasks": dropped_all, "fix_dirs": a.fix or [],
                                 "rule": "회차가 다 차고 backend_error 없는 과제만, 고침 차례가 같은 과제를 대신"}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    pre = "eval-" if a.eval.startswith("v1") else f"eval-{a.eval}-"
    out = RUNS_ROOT / f"{pre}{stamp}-{a.label}-merged"
    write_out(mod, out, meta, agg, rows)
    print(f"merged: {out} 과제 {meta['n_tasks']} 회차 {len(rows)} valid={meta['valid']} {meta['invalid_reasons']}")
    return 0


def cmp(a, b, path="") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += cmp(a.get(k), b.get(k), f"{path}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += cmp(x, y, f"{path}[{i}]")
        return out
    if isinstance(a, float) and isinstance(b, float):
        return [] if abs(a - b) <= 1e-9 * max(1, abs(a), abs(b)) else [f"{path}: {a} ≠ {b}"]
    return [] if a == b else [f"{path}: {str(a)[:80]} ≠ {str(b)[:80]}"]


def cmd_selftest(a) -> int:
    src = Path(a.selftest).resolve()
    s = json.loads((src / "summary.json").read_text(encoding="utf-8"))
    ver = s["meta"]["eval_version"]
    mod = load_eval(ver)
    rows = [json.loads(l) for l in (src / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    tasks = sorted({r["task"] for r in rows})
    random.Random(1109).shuffle(tasks)
    groups = [tasks[i::a.n] for i in range(a.n)]
    metas, rowsets = [], []
    for g in groups:
        m = dict(s["meta"])
        m["n_tasks"] = len(g)
        m["invalid_reasons"] = list(m.get("invalid_reasons") or []) + [PARTIAL]
        m["valid"] = False
        metas.append(m)
        rowsets.append([r for r in rows if r["task"] in g])
    meta, agg, merged = merge(mod, metas, rowsets, s["meta"].get("label", "selftest"), [f"조각{i}" for i in range(a.n)])
    orig = {k: v for k, v in s.items() if k != "meta"}
    diffs = cmp(orig, agg)
    vdiff = [] if meta["valid"] == s["meta"]["valid"] and meta["invalid_reasons"] == s["meta"]["invalid_reasons"] else \
        [f"valid {meta['valid']} {meta['invalid_reasons']} ≠ 원래 {s['meta']['valid']} {s['meta']['invalid_reasons']}"]
    print(f"selftest {src.name}: 평가기 {ver}, 과제 {len(tasks)}개를 {a.n}조각({', '.join(str(len(g)) for g in groups)})으로 나눠 합침")
    print(f"  집계 차이 {len(diffs)}건, valid 차이 {len(vdiff)}건, 회차 {len(merged)}/{len(rows)}")
    for d in (diffs + vdiff)[:20]:
        print("   ", d)
    if a.keep:
        tmp = Path(a.keep) / f"selftest-{src.name}"
        if tmp.exists():
            shutil.rmtree(tmp)
        write_out(mod, tmp, meta, agg, merged)
        print(f"  합친 결과: {tmp}")
    return 0 if not diffs and not vdiff else 1


def cmd_plan_fix(a) -> int:
    """evalq 실행 폴더의 state.json(shards)로 고침 차례 과제를 고른다. 이어 하기로 이어받은 조각(carried)도 본다."""
    rd, group = Path(a.plan_fix[0]).resolve(), a.plan_fix[1]
    st = json.loads((rd / "state.json").read_text(encoding="utf-8"))
    g = (st.get("shards") or {}).get(group)
    if not g:
        sys.exit(f"{rd}/state.json 에 묶음 {group} 이 없다")
    want, why = [], {}
    reps = None
    for sh in g["shards"]:
        d = Path(sh["result_dir"]) if sh.get("result_dir") else None
        meta = None
        if d and (d / "summary.json").exists():
            meta = json.loads((d / "summary.json").read_text(encoding="utf-8"))["meta"]
            reps = reps or int(meta["reps"])
    reps = reps or 3
    for sh in g["shards"]:
        tasks = list(sh.get("tasks") or [])
        d = Path(sh["result_dir"]) if sh.get("result_dir") else None
        rows = read_rows(d) if d else []
        ok_done = sh.get("rc") == 0 and d and (d / "summary.json").exists()
        _, dropped = complete_tasks(rows, reps, drop_backend_error=not a.no_backend_error)
        seen = {r["task"] for r in rows}
        for t in tasks:
            if t not in seen and not ok_done:
                want.append(t); why[t] = "시작 못 함·끊김"
            elif t in dropped:
                want.append(t); why[t] = "회차 덜 참" if any(r["task"] == t and r.get("stop_reason") != "backend_error" for r in rows) and \
                    len([r for r in rows if r["task"] == t]) < reps else "backend_error·회차 덜 참"
    print(json.dumps({"group": group, "reps": reps, "tasks": sorted(set(want)), "why": why}, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", help="평가기 판(v1.2·v1.2.1·v2.0.1·v2.0.2)")
    ap.add_argument("--label")
    ap.add_argument("--selftest", help="완주 결과 폴더")
    ap.add_argument("--shards", dest="n", type=int, default=4)
    ap.add_argument("--keep", help="selftest 합친 결과를 둘 폴더(없으면 안 씀)")
    ap.add_argument("--concat-reps", action="store_true", help="같은 과제의 회차를 이어 붙인다(폴더:+N 으로 회차 밀기)")
    ap.add_argument("--partial", action="store_true", help="summary 없는 끊긴 조각도 받는다(회차가 다 찬 과제만)")
    ap.add_argument("--fix", action="append", help="고침 차례 결과 폴더(같은 과제를 대신한다). 여러 번 줄 수 있다")
    ap.add_argument("--plan-fix", nargs=2, metavar=("RUNDIR", "GROUP"), help="evalq 실행 폴더·묶음에서 다시 돌릴 과제 목록")
    ap.add_argument("--no-backend-error", action="store_true", help="--plan-fix: backend_error 과제는 넣지 않는다")
    ap.add_argument("shards", nargs="*")
    a = ap.parse_args()
    if a.selftest:
        return cmd_selftest(a)
    if a.plan_fix:
        return cmd_plan_fix(a)
    if not (a.eval and a.label and len(a.shards) >= 2):
        ap.error("--eval·--label 과 조각 폴더 2개 이상")
    return cmd_merge(a)


if __name__ == "__main__":
    sys.exit(main())
