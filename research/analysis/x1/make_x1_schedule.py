#!/usr/bin/env python3
"""X1-01 S4(탐색 6칸 + X1-04) 대기열·smoke 대기열·차례표를 만든다 — 평가 담당 2026-11-10(총괄 지시).
같은 인자면 같은 파일(바이트까지)이 나온다. 시각·호스트 instance 는 차례표에 넣지 않는다(차례표 sha 를 실행 전에 등록하려고).

사용: python3 -I -B make_x1_schedule.py [--hosts <eval instance>:<machine>,<eval2 instance>:<machine>] [--check]
  --hosts 가 없으면 대기열 hosts 에 자리값 1:1 을 넣는다(검증만 가능, start 전에 실제 값으로 다시 만든다 — 차례표는 같다).
  --check: 쓰지 않고, 이미 있는 차례표와 지금 만든 것이 같은지만 본다.
환경 변수: STEWART_LABS = 연구 작업 폴더(기본 . — 그 아래 eval/tools·queue), STEWART_EVALQ_DIR = evalq.py 가 있는 폴더(기본 <STEWART_LABS>/tools/evalq),
  X1_ALIAS_ORIG·X1_ALIAS_ABL·X1_ALIAS_ABL3P = 서빙 별칭(기본 역할 이름 orig·abl·abl3p. 원래 실행 값은 모델 판 이름이라 싣지 않는다).
낸 파일(<STEWART_LABS>/queue/):
  x1-s4smoke-20261110.json  smoke(모델마다 v2 두 과제 × 1회, base·fix1, orig 는 think 도) — 분석에 쓰지 않는다
  x1-s4-20261110.json       본 대기열(칸 7 × 평가기 2 = 14차례, 시드로 섞은 차례, 조각은 evalq LPT)
  x1-s4-schedule-20261110.json  차례표: (칸, 평가기, 과제, 회차) → 조각 tag, 차례 순서, 만든 스크립트 sha
배정의 무작위성: 14차례의 파일 순서를 시드 20261110 으로 섞는다. evalq 는 빈 차선마다 "파일 순서대로 별칭이 맞는 첫 차례" 를 주므로
(칸 × 과제 조각) → 호스트·차선은 이 순서와 차선이 비는 때로 정해지고, 실제 값은 runner.log 조각 줄에 남는다(연구 조건 ②).
"""
import argparse
import hashlib
import json
import os
import random
import sys
from pathlib import Path

LABS = Path(os.environ.get("STEWART_LABS", "."))
sys.path.insert(0, str(Path(os.environ.get("STEWART_EVALQ_DIR") or LABS / "tools" / "evalq")))
import evalq as E  # noqa: E402  (validate_queue·expand_shards 를 그대로 써서 evalq 가 펼 조각과 같게)

SEED = 20261110
DATE = "20261110"
TT = LABS / "eval" / "tools"
ALIAS = {"orig": os.environ.get("X1_ALIAS_ORIG", "orig"), "abl": os.environ.get("X1_ALIAS_ABL", "abl"),
         "abl3p": os.environ.get("X1_ALIAS_ABL3P", "abl3p")}   # 서빙 별칭(모델 판 이름 가림 — 실행 때 환경 변수로)
COMMIT = {"base": "b480fe7", "fix1": "914bd71", "think": "914bd71"}
CELLS = ["orig-base", "orig-fix1", "orig-think", "abl-base", "abl-fix1", "abl-think", "abl3p-fix1"]
EVALS = {"v1": "v1.2.3", "v2": "v2.0.5"}
# 회차당 분(X1-01 계획의 실측, 비공개 운영 기록) · 처리율 2.25 회차분/차선분(E18) · 조각 목표 ≈ 35 차선분
PER_RUN = {("v1", False): 0.67, ("v1", True): 0.82, ("v2", False): 2.5, ("v2", True): 5.44}
SHARDS = {("v1", False): 2, ("v1", True): 2, ("v2", False): 5, ("v2", True): 11}
TIMETABLE = {("v1", False): "tt-v1-code.json", ("v1", True): "tt-v1-code.json",
             ("v2", False): "tt-v2-code-L2.json", ("v2", True): "tt-v2-think-L2.json"}
REPS = 5
PLANS = {"eval-host-a": "x1-01-eval", "eval-host-b": "x1-01-eval2"}   # 평가 장비 둘(장비 별칭 가림)


def step_for(cell, kind, reps=REPS, tasks=None, shards=True, tag=None):
    m, k = cell.split("-")
    think = k == "think"
    s = {"commit": COMMIT[k], "tag": tag or f"x1-{cell}-{kind}", "profile": "code", "evals": [EVALS[kind]], "model": ALIAS[m],
         "est_min": max(1, int(round(len(tasks or [0] * (41 if kind == "v1" else 32)) * reps * PER_RUN[(kind, think)] / 2.25))),
         "reps": reps}
    if think:
        s["backends_file"] = "cloud-think"
    if tasks:
        s["tasks"] = tasks
    if shards:
        s["shards"] = SHARDS[(kind, think)]
        s["timetable"] = str(TT / TIMETABLE[(kind, think)])
    return s


def hosts_arg(v):
    if not v:
        return [{"host": h, "instance": "1", "machine": "1", "gpus": 4} for h in PLANS]
    parts = v.split(",")
    if len(parts) != 2:
        raise SystemExit("--hosts <eval inst>:<machine>,<eval2 inst>:<machine>")
    out = []
    for h, p in zip(PLANS, parts):
        inst, mach = p.split(":")
        out.append({"host": h, "instance": inst, "machine": mach, "gpus": 4})
    return out


def dump(obj):
    return json.dumps(obj, ensure_ascii=False, indent=1) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hosts")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    hosts = hosts_arg(a.hosts)
    me = Path(__file__).resolve()
    script_sha = hashlib.sha256(me.read_bytes()).hexdigest()
    tt_sha = {p: hashlib.sha256((TT / p).read_bytes()).hexdigest() for p in sorted(set(TIMETABLE.values()))}

    # 본 대기열: 14차례를 시드로 섞는다
    steps = [step_for(c, kind) for c in CELLS for kind in ("v1", "v2")]
    random.Random(SEED).shuffle(steps)
    q = {"name": f"x1-s4-{DATE}", "role": "eval", "jobs_per_gpu": 4, "wait_quality": True, "deadline": None,
         "hosts": hosts, "steps": steps}
    nq = E.validate_queue(q)
    ex = E.expand_shards(nq)

    # 차례표
    tag_cell = {f"x1-{c}-{kind}": (c, kind) for c in CELLS for kind in ("v1", "v2")}
    shards, assign = [], []
    for i, s in enumerate(ex["steps"]):
        grp = (s.get("shard") or {}).get("group", s["tag"])
        cell, kind = tag_cell[grp]
        shards.append({"order": i, "tag": s["tag"], "group": grp, "cell": cell, "eval": s["evals"][0], "model": s["model"],
                       "commit": s["commit"], "backends_file": s["backends_file"], "est_min": s["est_min"], "reps": s["reps"],
                       "tasks": s["tasks"]})
        for t in s["tasks"]:
            for r in range(1, s["reps"] + 1):
                assign.append([cell, s["evals"][0], t, r, s["tag"]])
    sched = {"what": "X1-01 S4 차례표(P-001 §2 배정표) — (칸, 평가기, 과제, 회차) → 조각 tag",
             "seed": SEED, "script": str(me), "script_sha256": script_sha, "evalq": str(Path(E.__file__).resolve()),
             "evalq_sha256": hashlib.sha256(Path(E.__file__).read_bytes()).hexdigest(),
             "timetables_sha256": tt_sha, "queue_name": q["name"], "plans": PLANS, "aliases": ALIAS, "commits": COMMIT,
             "cells": CELLS, "reps": REPS,
             "rule": "14차례 파일 순서 = random.Random(seed).shuffle. 조각 = evalq expand_shards(LPT, timetable 무게). "
                     "evalq 는 빈 차선마다 파일 순서대로 별칭이 맞는 첫 차례를 준다 — 실제 호스트·차선은 runner.log 조각 줄.",
             "step_order": [s["tag"] for s in steps], "shards": shards, "assignment": assign,
             "counts": {"steps": len(steps), "shards": len(shards), "runs": len(assign)}}

    # smoke: 모델마다 v2 가장 짧은 두 과제 × 1회
    tt = json.loads((TT / "tt-v2-code-L2.json").read_text(encoding="utf-8"))
    tt = tt.get("tasks", tt)
    short = sorted(sorted(tt), key=lambda t: tt[t])[:2]
    sm_steps = []
    for c in ("orig-base", "orig-fix1", "orig-think", "abl-base", "abl-fix1", "abl3p-fix1"):
        sm_steps.append(step_for(c, "v2", reps=1, tasks=short, shards=False, tag=f"x1smk-{c}"))
    sq = {"name": f"x1-s4smoke-{DATE}", "role": "eval", "jobs_per_gpu": 4, "wait_quality": True, "deadline": None,
          "hosts": hosts, "steps": sm_steps}
    E.validate_queue(sq)

    out = LABS / "queue"
    files = {out / f"x1-s4-{DATE}.json": dump(q), out / f"x1-s4smoke-{DATE}.json": dump(sq),
             out / f"x1-s4-schedule-{DATE}.json": dump(sched)}
    if a.check:
        p = out / f"x1-s4-schedule-{DATE}.json"
        same = p.exists() and p.read_text(encoding="utf-8") == files[p]
        print("차례표 같음" if same else "차례표 다름")
        return 0 if same else 1
    for p, txt in files.items():
        if p.name.startswith("x1-s4-schedule-") and p.exists():
            if p.read_text(encoding="utf-8") != txt:     # 등록한 차례표는 바꾸지 않는다(잠금 0444)
                raise SystemExit(f"차례표가 다르게 나온다 — 등록한 것과 어긋남: {p}")
            print(p, hashlib.sha256(txt.encode("utf-8")).hexdigest(), "(그대로)")
            continue
        p.write_text(txt, encoding="utf-8")
        print(p, hashlib.sha256(txt.encode("utf-8")).hexdigest())
    print(json.dumps(sched["counts"]), "smoke 과제", short, "smoke 회차", len(sm_steps) * 2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
