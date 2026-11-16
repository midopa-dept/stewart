"""맡기기 장면을 진짜 모델로 잰다(판 149): 맡겨야 할 말에서 delegate·동시 작업자를 부르는가, 맡기지 말아야 할 말에서 헛부르는가.

  python tools/scenes/delegate_measure.py --lanes http://<서버>:<포트>/v1[,…] --model <서빙 모델> -n 8 --out d.json --table d.md
  python tools/scenes/delegate_measure.py --backend <이름> -n 8 --out d.json --table d.md

- 장면: tests/scenes/delegate/scenes.toml(개발 묶음 — 결과를 읽고 고쳐도 된다. 확증은 따로 둔 장면으로).
- 작업 폴더: 장면·회차마다 새 시험 저장소(tools/scenes/fixture_repo.py, 파일 46개).
- 칸 넷(--cells 로 고름): hub-nudge·hub-nonudge·chat-nudge·chat-nonudge
  - hub = 허브 메인 꼴(hubmain.hub_main_form — 역할 글 + 허브 도구 정의, `chat --once --hub-main` 과 같다), chat = 보통 코드 자리
  - nudge = 맡기기 넛지(판 97) 켬·끔
- 도구는 실제로 돈다(읽기·쓰기·작업자·위임 모두, 모드 auto, 작업 폴더 안). 웹 검색은 닿지 않는 주소(빨리 실패).
  셸도 실제로 돈다 — 평가 실행기의 격리 안에서 돌린다.
  --dry 면 읽기 도구만 실제로 돌고, 쓰기·셸은 거절 글, delegate·workers 는 띄우지 않고 부름·맡긴 글만 기록(dry_call)에 남긴다.
  판정은 부름과 맡긴 글로 하니 같다. 다만 맡긴 일의 결과를 받지 못하니 그 뒤 걸음은 실제와 다르다.
- 등급 칸(판 166): --tier <등급> --tier-table <이름>=<백엔드 표> 를 주면 칸 hub@<이름> 이 더해진다.
  허브 메인 꼴에 그 표의 [tiers.<등급>] 를 허브 --tier 와 같은 길(프로필 raw.tier — 도구·지시문 절·생각 칸·budget, slim)로 싣는다.
  등급의 backend 칸은 쓰지 않는다(모델은 --lanes·--backend). 넛지는 제품 기본(STEWART_DELEGATE_NUDGE·prompt.nudges)을 따른다.
  --tier-cell <이름> 은 표 자리에 지금 백엔드 표(STEWART_BACKENDS 등, --backend 가 읽는 그 표)를 쓴다 — 평가 대행이 차례마다 표 하나만 줄 때.
  --tier-table·--tier-cell 이 없으면 칸 넷만 돌 때와 결과가 같다.
- 메인 걸음 상한 --steps(기본 12). 작업자는 delegate 의 예산(메인 상한 × 2)·기본 걸음 안에서 돈다.
- 판정(기계): 첫 N 걸음 안에 delegate 또는 workers(action=spawn)를 불렀는가 · 부른 걸음 번호 · 맡긴 글에 범위(경로·폴더)와 돌려받을 것(정리·목록·표…)이
  있는가 · 맡기지 말아야 할 말에서의 헛부름 · 넛지가 떴는가(기록의 delegate_nudge 사건).
- 모델을 부르니 비용·장비 시간이 든다(장면 12 × 칸 4 × n 회).
- 사용자 자리는 빈 임시 폴더, 기억은 끔(--memory 로만 켬). 회차 기록은 --work(기본 --out 옆 work/<칸>-<장면>-<회차>).
- 끝: 결과가 모자라면 rc 2, 오류 회차가 있으면 rc 1, 다 정상이면 0. --out 머리에 expected·got·errors·scenes_sha.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import io
import json
import os
import queue
import re
import sys
import tempfile
import threading
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))
sys.path.insert(0, str(HERE))
from stewart import chat as C, delegate as DG  # noqa: E402
from stewart.config import load_profile  # noqa: E402
from stewart.record import Recorder  # noqa: E402
from fixture_repo import make as make_repo  # noqa: E402
import measure as M  # noqa: E402

SCENES = HERE.parents[1] / "tests" / "scenes" / "delegate" / "scenes.toml"   # 판 149.1: 하위 폴더 — 실사용 장면 묶음(scenes.load 의 *.toml)과 섞이지 않게
CELLS = ("hub-nudge", "hub-nonudge", "chat-nudge", "chat-nonudge")
SCOPE_RX = re.compile(r"[\w.-]+/[\w./-]+|\.py\b|\.md\b|\b(src|docs|tests|handlers|README)\b|저장소|폴더|모듈|파일")
RETURN_RX = re.compile(r"정리|목록|표로|표를|보고|돌려|알려|요약|찾아|모아|뽑아|결과")


def load(path=SCENES) -> list[dict]:
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))["scene"]


def delegations(messages: list[dict]) -> list[dict]:
    """[{step, name, text}] — 메인이 부른 delegate·workers(spawn). step 은 1부터(도구를 부른 assistant 메시지 차례)."""
    out, step = [], 0
    for m in messages:
        if m.get("role") != "assistant" or not m.get("tool_calls"):
            continue
        step += 1
        for c in m["tool_calls"]:
            fn = c.get("function") or {}
            try:
                a = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                a = {}
            if not isinstance(a, dict):
                a = {}
            if fn.get("name") == DG.NAME or (fn.get("name") == "workers" and a.get("action") == "spawn"):
                out.append({"step": step, "name": fn.get("name"), "text": str(a.get("task") or "")})
    return out


def steps_used(messages: list[dict]) -> int:
    return sum(1 for m in messages if m.get("role") == "assistant" and m.get("tool_calls"))


def judge(scene: dict, messages: list[dict], nudges: int, limit: int) -> dict:
    ds = [d for d in delegations(messages) if d["step"] <= limit]
    first = ds[0] if ds else None
    r = {"delegated": bool(ds), "first_step": first["step"] if first else None, "n_calls": len(ds), "nudge": nudges,
         "steps": steps_used(messages), "names": sorted({d["name"] for d in ds})}
    if first:
        t = first["text"]
        r.update(scope=bool(SCOPE_RX.search(t)), ret=bool(RETURN_RX.search(t)), task_chars=len(t))
    r["ok"] = r["delegated"] if scene["kind"] == "should" else not r["delegated"]
    return r


DRY_RESULT = "(측정: 이 도구는 실행하지 않았다. 읽기 도구만 실제로 돈다.)"


def dry_wrap(s) -> None:
    """판 151 --dry: 읽기 도구(delegate.READ_TOOLS)만 실제로 돌리고, 쓰기·셸은 거절 글, delegate·workers 는 띄우지 않고
    부름과 맡긴 글만 기록(dry_call 사건)에 남긴다."""
    real = s._exec

    def ex(rc: dict, step: int) -> dict:
        fn = rc.get("function") or {}
        name, raw = fn.get("name", ""), fn.get("arguments") or "{}"
        if name in DG.READ_TOOLS:
            return real(rc, step)
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (ValueError, TypeError):
            args = {}
        task = args.get("task") if isinstance(args, dict) else None
        s.rec.event("dry_call", name=name, step=step, **({"task": str(task)} if task else {}))
        s.st["tool_calls"] += 1
        return {"id": rc.get("id"), "name": name, "raw_arguments": raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False),
                "ok": True, "result": DRY_RESULT, "elapsed_s": 0.0}
    s._exec = ex


def tier_profile(p, backend, table: str, tier: str):
    """판 166: 등급 칸 — 백엔드 표의 [tiers.<tier>] 를 허브 --tier(hubmain.main_tier)와 같은 꼴로 프로필·백엔드에 싣는다(backend 칸은 빼고)."""
    from stewart import spawn as SP
    t = SP.load_tiers(path=table, main=True).get(tier)
    if t is None:
        raise ValueError(f"표 {table} 에 등급 {tier} 이 없다")
    if t.get("profile"):
        p = dataclasses.replace(load_profile(t["profile"]), workdir=p.workdir, max_steps=p.max_steps)
    if t.get("budget"):
        backend = dataclasses.replace(backend, context_budget=max(4096, min(int(t["budget"]), backend.max_context)))
    p = dataclasses.replace(p, raw={**(p.raw or {}), "tier": SP.tier_raw(t, backend)})
    if t.get("slim"):
        from stewart import slim as SL
        p = SL.apply(p, t)
    return p, backend


def one(scene: dict, backend, cell: str, limit: int, dry: bool = False, work: Path | None = None, tiers: dict | None = None) -> dict:
    """한 회차. work 가 있으면 그 폴더에 시험 저장소(repo)·기록(runs)을 남긴다(없으면 임시 폴더)."""
    if work is not None:
        work.mkdir(parents=True, exist_ok=True)
        tmp = work
    else:
        tmp = Path(tempfile.mkdtemp(prefix="dg-scene-"))
    repo = make_repo(tmp / "repo")
    p = load_profile("chat")
    p = dataclasses.replace(p, workdir=repo, max_steps=limit)
    rec, extra = Recorder(tmp / "runs", "dg"), None
    tier_cell = cell.startswith("hub@")
    if tier_cell:   # 판 166: 등급 칸 — (표, 등급)
        table, tname = (tiers or {})[cell[4:]]
        p, backend = tier_profile(p, backend, table, tname)
    if cell.startswith("hub"):   # 판 151: `chat --once --hub-main` 과 같은 길(역할 글 + 허브 도구 정의, 허브 도구는 거절 글)
        from stewart.hubmain import hub_main_form
        p, extra = hub_main_form(p, rec)
    hist = [{"role": "system", "content": ""}] + list(scene.get("history") or []) if scene.get("history") else None
    s = C.ChatSession(p, backend, rec, C.UI(out=io.StringIO(), color=False), stream=False, mode="auto",
                      history=hist, extra_tools=extra)
    shown = [n for n in s.tool_names if n not in s.disabled_tools]   # 판 166: 모델에게 보인 도구(등급 칸 대조용)
    if not tier_cell:   # 등급 칸은 세션이 정한 제품 기본 그대로
        s.dnudge = DG.Nudge(on=cell.endswith("-nudge") and DG.NAME in s.tool_names)
    if dry:
        dry_wrap(s)
    t0 = time.time()
    try:
        reason = s.turn(scene["said"])
    except Exception as e:   # noqa: BLE001 — 백엔드 오류도 결과로
        reason = f"오류 {type(e).__name__}: {str(e)[:120]}"
    finally:
        try:
            s.close()
        except Exception:   # noqa: BLE001
            pass
    nudges = refused = cleaned = 0
    try:
        evs = (s.rec.dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        nudges = sum(1 for ln in evs if '"delegate_nudge"' in ln)
        refused = sum(1 for ln in evs if '"hub_tool_refused"' in ln)   # 판 151: 허브 도구를 불러 거절 글을 받은 수
        for ln in evs:   # 세션을 닫을 때 정리한 백그라운드 작업 수
            if '"shell_cleanup"' in ln:
                try:
                    cleaned += len(json.loads(ln).get("cleaned_up") or [])
                except ValueError:
                    pass
    except OSError:
        pass
    r = judge(scene, s.messages, nudges, limit)
    if tier_cell:   # 판 166: 등급 칸만 — 칸 넷의 결과 꼴은 그대로
        r.update(n_tools=len(shown), tools_sha=hashlib.sha256(",".join(sorted(shown)).encode()).hexdigest()[:12])
    r.update(reason=str(reason)[:120], secs=round(time.time() - t0, 1), hub_refused=refused, bg_cleaned=cleaned)
    if str(reason).startswith("오류 ") or str(reason) == "backend_error":   # 판 166: 서버 오류로 끝난 회차도 오류(맡기지 않음으로 세지 않게)
        r["error"] = str(reason)[:200]
    return r


def safe_one(scene: dict, backend, cell: str, limit: int, dry: bool = False, work: Path | None = None, tiers: dict | None = None) -> dict:
    """one() 이 어디서 깨지든(세션 만들기 포함) 그 회차를 오류 결과로 남긴다 — 스레드가 죽어 결과가 모자라는 일이 없게."""
    try:
        return one(scene, backend, cell, limit, dry, work, tiers)
    except Exception as e:   # noqa: BLE001
        return {"ok": False, "delegated": False, "first_step": None, "n_calls": 0, "nudge": 0, "steps": 0, "names": [],
                "error": f"{type(e).__name__}: {str(e)[:200]}", "secs": 0.0, "hub_refused": 0, "bg_cleaned": 0}


def empty_user_env() -> dict:
    """빈 사용자 자리: 기억 글(STEWART.md)·스킬·프로필이 없는 임시 폴더."""
    d = Path(tempfile.mkdtemp(prefix="dscene-user-"))
    for sub in ("skills", "profiles"):
        (d / sub).mkdir()
    return {"STEWART_USER_DIR": str(d), "STEWART_SKILLS_DIR": str(d / "skills"), "STEWART_PROFILES_DIR": str(d / "profiles")}


def engine_head() -> str:
    """판 166: 엔진 git 머리(없으면 빈 글) — 결과가 어느 엔진 판에서 났는지."""
    import subprocess
    try:
        return subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=HERE.parents[1], capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def table_md(res: dict, meta: dict) -> str:
    lines = [f"# 맡기기 장면 실측 — {meta.get('when')} · 걸음 상한 {meta.get('steps')} · 장면마다 {meta.get('n')}번", "",
             "> " + M.DEV_HEAD, "",
             "| 칸 | 갈래 | 장면 | 맞음 | 맡김 | 첫 맡김 걸음(평균) | 넛지 뜸 | 맡긴 글: 범위·돌려받을 것 |", "|---|---|---|---|---|---|---|---|"]
    for cell in meta["cells"]:
        tot = {"should": [0, 0], "should_not": [0, 0]}
        for sc in meta["scenes"]:
            rs = [x for x in res.get(f"{cell}|{sc['id']}", []) if "ok" in x and not x.get("error")]
            if not rs:
                continue
            ok = sum(x["ok"] for x in rs)
            dl = [x for x in rs if x["delegated"]]
            fs = [x["first_step"] for x in dl]
            q = f"{sum(1 for x in dl if x.get('scope'))}·{sum(1 for x in dl if x.get('ret'))}/{len(dl)}" if dl else "-"
            lines.append(f"| {cell} | {sc['kind']} | {sc['id']} | {ok}/{len(rs)} | {len(dl)} | {sum(fs) / len(fs):.1f} | "
                         f"{sum(1 for x in rs if x['nudge'])} | {q} |" if fs else
                         f"| {cell} | {sc['kind']} | {sc['id']} | {ok}/{len(rs)} | 0 | - | {sum(1 for x in rs if x['nudge'])} | - |")
            tot[sc["kind"]][0] += ok
            tot[sc["kind"]][1] += len(rs)
        nerr = sum(1 for sc in meta["scenes"] for x in res.get(f"{cell}|{sc['id']}", []) if x.get("error"))
        lines.append(f"- {cell}: 오류 회차 {nerr}(표에서 뺌) · 맡겨야 할 말 {tot['should'][0]}/{tot['should'][1]} · 맡기지 말아야 할 말(헛부름 없음) "
                     f"{tot['should_not'][0]}/{tot['should_not'][1]}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--backend")
    g.add_argument("--lanes")
    ap.add_argument("--model", default="")
    ap.add_argument("--think", choices=("on", "off", "default"), default="on")
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--max-tokens", type=int, default=0)
    ap.add_argument("--max-context", type=int, default=65536)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("-n", type=int, default=3)
    ap.add_argument("--per-lane", type=int, default=2)
    ap.add_argument("--steps", type=int, default=12)
    ap.add_argument("--cells", default="", help="고를 칸(쉼표). 기본: 칸 넷, --tier-table 을 주면 그 등급 칸들만")
    ap.add_argument("--tier", default="largo", help="등급 칸이 쓸 [tiers.<이름>](판 166)")
    ap.add_argument("--tier-table", action="append", default=[], metavar="이름=표", help="등급 칸 hub@<이름> 을 더한다(되풀이)")
    ap.add_argument("--tier-cell", action="append", default=[], metavar="이름", help="등급 칸 hub@<이름> — 표는 지금 백엔드 표(되풀이)")
    ap.add_argument("--only", default="")
    ap.add_argument("--scenes", default=str(SCENES))
    ap.add_argument("--out", default="")
    ap.add_argument("--table", default="")
    ap.add_argument("--work", default="", help="회차 기록(시험 저장소·runs)을 남길 폴더. 기본: --out 옆의 work, --out 이 없으면 임시 폴더")
    ap.add_argument("--memory", action="store_true", help="기억(STEWART_MEMORY)을 켠다. 기본은 끔으로 못박는다")
    ap.add_argument("--dry", action="store_true", help="실행하지 않는 꼴: 읽기만 실제로, 쓰기·셸은 거절, delegate·workers 는 부름·맡긴 글만 기록")
    a = ap.parse_args()
    if a.lanes and not a.model:
        ap.error("--lanes 에는 --model 이 필요하다")
    os.environ["STEWART_MEMORY"] = "1" if a.memory else "0"   # 바깥 환경으로 뒤집히지 않게 못박는다
    os.environ.setdefault("STEWART_SEARXNG_URL", "http://127.0.0.1:9/search")
    os.environ.setdefault("STEWART_SEARXNG_MIN_INTERVAL", "0")
    os.environ.update(empty_user_env())   # 사용자 자리는 빈 시험 자리(기억 글·스킬·프로필 없음) — 돌리는 사람 설정에 기대지 않게
    work = Path(a.work) if a.work else (Path(a.out).resolve().parent / "work" if a.out else Path(tempfile.mkdtemp(prefix="dscene-work-")))
    scenes = [s for s in load(a.scenes) if not a.only or s["id"] in a.only.split(",")]
    tiers = {}
    for x in a.tier_table:
        k, _, v = x.partition("=")
        if not k or not v or not Path(v).is_file():
            ap.error(f"--tier-table 은 이름=있는 표 파일: {x}")
        tiers[k] = (str(Path(v).resolve()), a.tier)
    for k in a.tier_cell:
        from stewart.config import backends_path
        if not k or k in tiers:
            ap.error(f"--tier-cell 이름이 비었거나 겹친다: {k!r}")
        tiers[k] = (str(Path(backends_path()).resolve()), a.tier)
    known = list(CELLS) + [f"hub@{k}" for k in tiers]
    cells = [c for c in (a.cells.split(",") if a.cells else ([f"hub@{k}" for k in tiers] or list(CELLS))) if c in known]
    for k, (table, tname) in tiers.items():   # 시작 전에 표·등급을 확인한다(회차마다 오류로 새지 않게)
        from stewart import spawn as SP
        if tname not in SP.load_tiers(path=table, main=True):
            ap.error(f"표 {table} 에 등급 {tname} 이 없다")
    lanes = M.lane_backends(a)
    jobs: queue.Queue = queue.Queue()
    for i in range(a.n):
        for cell in cells:
            for sc in scenes:
                jobs.put((cell, sc, i))
    res: dict = {}
    lock = threading.Lock()
    total = jobs.qsize()

    def worker(b):
        while True:
            try:
                cell, sc, i = jobs.get_nowait()
            except queue.Empty:
                return
            wd = work / f"{cell}-{sc['id']}-{i + 1}"
            r = safe_one(sc, b, cell, a.steps, a.dry, wd, tiers)
            r["work"] = os.path.relpath(wd, Path(a.out).resolve().parent) if a.out else str(wd)
            with lock:
                res.setdefault(f"{cell}|{sc['id']}", []).append(r)
                done = sum(len(v) for v in res.values())
            print(f"[{done}/{total}] {cell} {sc['id']}: {'맞음' if r.get('ok') else '틀림'} 맡김={r.get('delegated')} "
                  f"걸음={r.get('first_step')} 넛지={r.get('nudge')} ({r.get('secs')}초)", flush=True)

    ths = [threading.Thread(target=worker, args=(b,), daemon=True) for b in lanes for _ in range(max(1, a.per_lane))]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    got = sum(len(v) for v in res.values())
    errors = sum(1 for v in res.values() for x in v if x.get("error"))
    import hashlib
    meta = {"when": time.strftime("%Y-%m-%d %H:%M"), "steps": a.steps, "n": a.n, "cells": cells, "dry": a.dry,
            "scenes": [{"id": s["id"], "kind": s["kind"]} for s in scenes],
            "scenes_sha": hashlib.sha256(Path(a.scenes).read_bytes()).hexdigest()[:16],
            "expected": total, "got": got, "errors": errors, "memory": a.memory,
            **({"tier": a.tier, "tier_tables": {k: {"file": Path(v[0]).name, "sha16": hashlib.sha256(Path(v[0]).read_bytes()).hexdigest()[:16]}
                                                for k, v in tiers.items()}, "engine": engine_head(),
                "cell_tools": {c: sorted({(x.get("n_tools"), x.get("tools_sha")) for v in [res.get(f"{c}|{s['id']}", []) for s in scenes] for x in v
                                         if x.get("n_tools") is not None}) for c in cells},
                "place_hints": os.environ.get("STEWART_PLACE_HINTS", "")} if tiers else {})}
    if a.out:
        Path(a.out).write_text(json.dumps({"meta": meta, "results": res}, ensure_ascii=False, indent=1), encoding="utf-8")
    md = table_md(res, meta)
    if a.table:
        Path(a.table).write_text(md, encoding="utf-8")
    print(md)
    if got < total:
        print(f"결과가 모자라다: {got}/{total}", file=sys.stderr)
        return 2
    if errors:
        print(f"오류 회차 {errors}/{total}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
