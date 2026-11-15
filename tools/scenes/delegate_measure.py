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
- 메인 걸음 상한 --steps(기본 12). 작업자는 delegate 의 예산(메인 상한 × 2)·기본 걸음 안에서 돈다.
- 판정(기계): 첫 N 걸음 안에 delegate 또는 workers(action=spawn)를 불렀는가 · 부른 걸음 번호 · 맡긴 글에 범위(경로·폴더)와 돌려받을 것(정리·목록·표…)이
  있는가 · 맡기지 말아야 할 말에서의 헛부름 · 넛지가 떴는가(기록의 delegate_nudge 사건).
- 모델을 부르니 비용·장비 시간이 든다(장면 12 × 칸 4 × n 회).
"""

from __future__ import annotations

import argparse
import dataclasses
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


def one(scene: dict, backend, cell: str, limit: int, dry: bool = False) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="dg-scene-"))
    repo = make_repo(tmp / "repo")
    p = load_profile("chat")
    p = dataclasses.replace(p, workdir=repo, max_steps=limit)
    rec, extra = Recorder(tmp / "runs", "dg"), None
    if cell.startswith("hub"):   # 판 151: `chat --once --hub-main` 과 같은 길(역할 글 + 허브 도구 정의, 허브 도구는 거절 글)
        from stewart.hubmain import hub_main_form
        p, extra = hub_main_form(p, rec)
    hist = [{"role": "system", "content": ""}] + list(scene.get("history") or []) if scene.get("history") else None
    s = C.ChatSession(p, backend, rec, C.UI(out=io.StringIO(), color=False), stream=False, mode="auto",
                      history=hist, extra_tools=extra)
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
    nudges = refused = 0
    try:
        evs = (s.rec.dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        nudges = sum(1 for ln in evs if '"delegate_nudge"' in ln)
        refused = sum(1 for ln in evs if '"hub_tool_refused"' in ln)   # 판 151: 허브 도구를 불러 거절 글을 받은 수
    except OSError:
        pass
    r = judge(scene, s.messages, nudges, limit)
    r.update(reason=str(reason)[:120], secs=round(time.time() - t0, 1), hub_refused=refused)
    return r


def table_md(res: dict, meta: dict) -> str:
    lines = [f"# 맡기기 장면 실측 — {meta.get('when')} · 걸음 상한 {meta.get('steps')} · 장면마다 {meta.get('n')}번", "",
             "> " + M.DEV_HEAD, "",
             "| 칸 | 갈래 | 장면 | 맞음 | 맡김 | 첫 맡김 걸음(평균) | 넛지 뜸 | 맡긴 글: 범위·돌려받을 것 |", "|---|---|---|---|---|---|---|---|"]
    for cell in meta["cells"]:
        tot = {"should": [0, 0], "should_not": [0, 0]}
        for sc in meta["scenes"]:
            rs = [x for x in res.get(f"{cell}|{sc['id']}", []) if "ok" in x]
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
        lines.append(f"- {cell}: 맡겨야 할 말 {tot['should'][0]}/{tot['should'][1]} · 맡기지 말아야 할 말(헛부름 없음) "
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
    ap.add_argument("--cells", default=",".join(CELLS))
    ap.add_argument("--only", default="")
    ap.add_argument("--scenes", default=str(SCENES))
    ap.add_argument("--out", default="")
    ap.add_argument("--table", default="")
    ap.add_argument("--dry", action="store_true", help="실행하지 않는 꼴: 읽기만 실제로, 쓰기·셸은 거절, delegate·workers 는 부름·맡긴 글만 기록")
    a = ap.parse_args()
    if a.lanes and not a.model:
        ap.error("--lanes 에는 --model 이 필요하다")
    os.environ.setdefault("STEWART_MEMORY", "0")
    os.environ.setdefault("STEWART_SEARXNG_URL", "http://127.0.0.1:9/search")
    os.environ.setdefault("STEWART_SEARXNG_MIN_INTERVAL", "0")
    os.environ.update({k: v for k, v in M.SC.scene_env().items() if k != "STEWART_USER_DIR"})   # 스킬·프로필은 빈 시험 자리
    scenes = [s for s in load(a.scenes) if not a.only or s["id"] in a.only.split(",")]
    cells = [c for c in a.cells.split(",") if c in CELLS]
    lanes = M.lane_backends(a)
    jobs: queue.Queue = queue.Queue()
    for _ in range(a.n):
        for cell in cells:
            for sc in scenes:
                jobs.put((cell, sc))
    res: dict = {}
    lock = threading.Lock()
    total = jobs.qsize()

    def worker(b):
        while True:
            try:
                cell, sc = jobs.get_nowait()
            except queue.Empty:
                return
            r = one(sc, b, cell, a.steps, a.dry)
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
    meta = {"when": time.strftime("%Y-%m-%d %H:%M"), "steps": a.steps, "n": a.n, "cells": cells, "dry": a.dry,
            "scenes": [{"id": s["id"], "kind": s["kind"]} for s in scenes]}
    if a.out:
        Path(a.out).write_text(json.dumps({"meta": meta, "results": res}, ensure_ascii=False, indent=1), encoding="utf-8")
    md = table_md(res, meta)
    if a.table:
        Path(a.table).write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
