#!/usr/bin/env python3
"""x1_analysis.py 시험 — 가짜 결과만(실제 X1·보류 묶음 결과 없음). 사용: python3 -I -B test_x1_analysis.py
꼴 셋: effect(abl 손상 + fix1·think 가 abl 만 회복 → I > 0), null(효과 없음), missing(무효·backend_error·빈 과제·호스트 모름).
가짜 경로는 path_scan.py·x1_analysis.py 의 기본 정규식에 맞춘 것이다 — PATH_SCAN_*·X1_RESEARCH_RX 를 비우고 돌린다."""
import json
import os
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import x1_analysis as X  # noqa: E402

FAIL = []


def ok(c, m):
    print(("ok   " if c else "FAIL ") + m)
    if not c:
        FAIL.append(m)


def make(root: Path, scenario: str, seed: int):
    rnd = random.Random(seed)
    v1 = [f"t{i:02d}" for i in range(41)]
    v2 = [f"T{i:02d}" for i in range(32)]
    base_p = {t: rnd.choice([0.0, 0.2, 0.5, 0.8, 1.0]) for t in v1 + v2}
    eff = {"orig": {"base": 0.0, "fix1": 0.0, "think": 0.0}, "abl": {"base": 0.0, "fix1": 0.0, "think": 0.0}, "abl3p": {"fix1": 0.0}}
    if scenario == "effect":
        eff = {"orig": {"base": 0.0, "fix1": 0.02, "think": 0.05}, "abl": {"base": -0.35, "fix1": -0.05, "think": 0.0},
               "abl3p": {"fix1": -0.05}}
    cells = {"orig-base": ("orig", "base"), "orig-fix1": ("orig", "fix1"), "orig-think": ("orig", "think"),
             "abl-base": ("abl", "base"), "abl-fix1": ("abl", "fix1"), "abl-think": ("abl", "think"),
             "abl3p-fix1": ("abl3p", "fix1"), "abl-fix1-specoff": ("abl", "fix1")}
    aliases = {"orig": "o-alias", "abl": "a-alias", "abl3p": "a3p"}
    hosts = ["eval-host-a", "eval-host-b"]
    log = [f"2026-11-11 00:00:00 시작 v0 x1"]
    t0 = X.dt.datetime(2026, 11, 11, 0, 0, 0)
    busy = []          # (host, lane, 시작, 끝, 별칭) — spec-log 가짜 누계용
    slot = 0
    cfg_cells = {}
    for c, (m, k) in cells.items():
        cfg_cells[c] = []
        for ver, kind, tasks in (("v1.2.3", "v1", v1), ("v2.0.5", "v2", v2)):
            if c == "abl-fix1-specoff" and kind == "v1":
                continue
            step = f"{c}-{kind}"
            tag = f"x1-{step}"
            d = root / f"eval-{ver}-20261111-000000-{tag}"
            (d / "stewart-runs").mkdir(parents=True)
            host = hosts[rnd.randrange(2)]
            lane = rnd.randrange(4)
            alias = aliases[m]
            if scenario == "missing" and c == "orig-think" and kind == "v2":
                alias = "wrong-alias"
            st = t0 + X.dt.timedelta(hours=2 * slot, minutes=1)
            en = st + X.dt.timedelta(minutes=59)
            slot += 1
            log.append(f"{st - X.dt.timedelta(seconds=1):%Y-%m-%d %H:%M:%S} step {step} → {host} 차선 {lane}(별칭 {alias})")
            log.append(f"{st:%Y-%m-%d %H:%M:%S} step {step} start {host} 차선 {lane} HEAD 0000000")
            if scenario == "missing" and c == "abl-base" and kind == "v1":
                pass   # runner.log end 줄 없음 → 호스트 모름
            else:
                log.append(f"{en:%Y-%m-%d %H:%M:%S} {step} {ver} end rc=0 {d}")
            busy.append((host, lane, st, en, m))
            rows = []
            for t in tasks:
                p = min(1.0, max(0.0, base_p[t] + eff[m][k]))
                for rep in range(1, 6):
                    rd = d / "stewart-runs" / f"{t}-r{rep}"
                    rd.mkdir()
                    succ = rnd.random() < p
                    stop = "completed" if succ else rnd.choice(["completed", "max_steps", "asked"])
                    fence = "stewart-agent.sb 66edcd33"
                    susp, cmd = False, "ls"
                    if scenario == "missing":
                        if c == "abl-base" and t == "T12" and rep == 2:
                            susp, cmd = True, "cat /path/to/stewart-lab/eval/v2.0.5/tasks.py"                 # G1: path_scan 확인 → 무효
                        if c == "abl-base" and t == "T13" and rep == 3:
                            susp, cmd = True, "sed -n 's/a/b/' x.py"                                          # G1: 확인 없음 → 유효, 따로 셈
                        if c == "orig-think" and t == "T14" and rep == 1:
                            susp, cmd = True, "ls /path/to/stewart-research"                                  # G1: 연구 경로 → 무효
                        if c == "orig-base" and t == "T05" and rep == 4:
                            fence = "none"                         # ①② 겹침 → ① 로 셈
                        if c == "abl-fix1" and t == "t03" and rep in (1, 2):
                            fence = "none"                         # 무효 2
                        if c == "orig-base" and t == "T05" and rep == 4:
                            stop, succ = "backend_error", False    # 다시 돌림 남음 → (T05, 4) 모든 칸에서
                        if c == "abl-think" and t == "t07":
                            fence = "none"                         # 이 칸 t07 유효 0 → 모든 칸에서 t07 제외
                        if c == "abl-fix1-specoff" and t in ("T20", "T21", "T22", "T23"):
                            fence = "none"                         # 20/160 = 12.5% 무효 → 중단 표시, 네 과제 모든 칸에서 제외(G3)
                    steps = [{"kind": "step", "response": {"finish_reason": "length" if (scenario == "effect" and m == "abl" and k == "base" and not succ and rnd.random() < 0.6) else "stop",
                                                           "content": "", "tool_calls": [{"function": {"name": "bash", "arguments": json.dumps({"command": cmd})}}]}}]
                    (rd / "events.jsonl").write_text("\n".join(json.dumps(e) for e in steps) + "\n", encoding="utf-8")
                    (rd / "summary.json").write_text(json.dumps({"length_stops": 0, "format_failures": 0, "repeat_nudges": 0}), encoding="utf-8")
                    rows.append({"task": t, "group": kind, "cat": "R", "rep": rep, "rc": 0, "stop_reason": stop, "success": succ,
                                 "functional_ok": succ, "compliance_ok": True, "parse_failures": 0, "tool_errors": 0,
                                 "language_retries": 0, "leak": False, "access_suspicious": susp, "out_of_workdir": False,
                                 "isolation_violation": scenario == "missing" and c == "orig-fix1" and t == "T10" and rep == 1,
                                 "run_dir": str(rd), "agent_fence": fence})
            (d / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            (d / "summary.json").write_text(json.dumps({"meta": {"eval_version": ver, "eval_hash": "fake"}}), encoding="utf-8")
            cfg_cells[c].append(str(d))
    rl = root / "runner.log"
    rl.write_text("\n".join(log) + "\n", encoding="utf-8")
    ratio = {"orig": 0.70, "abl": 0.60, "abl3p": 0.65}
    spec = {}
    for host in hosts:
        lines = ["time_utc\tlane\talias\tadapter\tdrafts\tdraft_tokens\taccepted_tokens\tpos_ratios"]
        for lane in range(4):
            cum = [0, 0, 0]
            for mnt in range(0, 2 * 60 * slot + 60):
                t = t0 + X.dt.timedelta(minutes=mnt)
                act = [b for b in busy if b[0] == host and b[1] == lane and b[2] <= t < b[3]]
                if act:
                    cum[0] += 30
                    cum[1] += 100
                    cum[2] += int(100 * ratio[act[0][4]])
                if lane == 1 and mnt == 2 * 60 * 3 + 30:
                    cum = [0, 0, 0]            # 서버 재시작(누계가 0 으로)
                utc = t - X.dt.timedelta(hours=9)
                lines.append(f"{utc:%Y-%m-%dT%H:%M:%SZ}\t{lane}\tx\tdraft-adapter\t{cum[0]}\t{cum[1]}\t{cum[2]}\t0.8,0.6,0.4")
        sp = root / f"spec-{host}.tsv"
        sp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        spec[host] = str(sp)
    sched = root / "schedule.json"
    sched.write_text(json.dumps({"seed": 20261110, "fake": True}), encoding="utf-8")
    return {"set": "explore", "mode": "fence", "cells": cfg_cells, "aliases": aliases, "runner_logs": [str(rl)],
            "schedule": str(sched), "spec_logs": spec, "runner_tz_offset_h": 9}


def find(o, h, prefix):
    for s in o["results"][h]:
        if s["name"].startswith(prefix):
            return s
    raise KeyError(prefix)


def main():
    B = int(os.environ.get("X1_TEST_B", "2000"))
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
        tmp = Path(tmp)
        # effect
        cfg = make(tmp / "effect", "effect", 1)
        o = X.analyze(cfg, B)
        s = find(o, "H2", "H2 I_fix1 [전체]")
        ok(s["ci95_pt"][0] > 0 and s["est_pt"] > 5, f"effect: I_fix1 > 0 검출 ({s['est_pt']} {s['ci95_pt']}) · {s['verdict']}")
        ok(s["verdict"].startswith("손상 특이적"), "effect: 해석 = 손상 특이적 회복")
        h1 = find(o, "H1", "H1 orig−abl fix1 [전체]")
        ok(h1["est_pt"] > 0, f"effect: H1 orig−abl fix1 > 0 ({h1['est_pt']})")
        ok(any(x["reject"] for x in o["holm_family"] if x["name"].startswith("H2")), "effect: Holm 에서 H2 기각")
        ok(o["holm_label"].startswith("탐색"), "explore 묶음은 Holm 을 '탐색' 으로 표시")
        d2 = find(o, "H3", "H3 J_fix1,D2 [전체]")
        ok(d2["est_pt"] < 0, f"effect: D2(잘림) 상호작용 < 0 ({d2['est_pt']})")
        h5 = o["results"]["H5"]
        bc = o["results"]["H5_spec"]["by_cell"]
        ok(h5.get("source") == "spec_logs" and h5["abl_lt_orig"] is True, f"H5: spec-log 로 abl < orig ({h5})")
        ok(all(abs(bc[c]["acceptance"] - ratio) < 0.02 for c, ratio in (("orig-base", 0.70), ("orig-think", 0.70), ("abl-fix1", 0.60),
                                                                          ("abl3p-fix1", 0.65))),
           "H5: 칸 수락률 = 가짜 값(0.70·0.60·0.65, 재시작 경계 포함) " + str({c: bc[c]["acceptance"] for c in bc}))
        ok(o["results"]["H5_spec"]["skipped_lines"] == {"eval-host-a": 1, "eval-host-b": 1}, "H5: 머리줄 하나씩 건너뜀")
        ok(o["filter"]["orig-base"]["host_unknown"] == 0, "effect: 모든 회차에 호스트")
        ok(len(o["results"]["H2"]) == 6, "H2: 전체·v1·v2 × fix1·think = 6줄")
        # null
        cfg = make(tmp / "null", "null", 2)
        o = X.analyze(cfg, B)
        s = find(o, "H2", "H2 I_fix1 [전체]")
        ok(s["ci95_pt"][0] <= 0 <= s["ci95_pt"][1], f"null: I_fix1 CI 에 0 ({s['est_pt']} {s['ci95_pt']})")
        rej = 0
        for sd in range(20, 40):     # 효과 없는 자료 20벌: Holm 묶음에서 하나라도 기각한 벌의 비율(목표 ≈ α=5%, 넉넉히 ≤ 20%)
            on = X.analyze(make(tmp / f"null{sd}", "null", sd), 400)
            rej += any(x["reject"] for x in on["holm_family"])
        ok(rej <= 4, f"null: 20벌 중 Holm 묶음 기각 {rej}벌(≤ 4)")
        h4 = find(o, "H4", "H4 abl3p−abl fix1 [전체]")
        ok(abs(h4["est_pt"]) < 6, f"null: H4 차이 작음 ({h4['est_pt']}, TOST {h4['tost_equivalent']})")
        h6 = o["results"]["H6"]
        ok(h6["n_tasks"] == 32 and "ci90_pt" in h6["task_fixed"], f"null: H6 v2 32과제 과제 고정 CI {h6['task_fixed']['ci90_pt']}")
        # missing
        cfg = make(tmp / "missing", "missing", 3)
        o = X.analyze(cfg, B)
        f = o["filter"]
        ok(f["abl-fix1"]["invalid"] == 2, f"missing: abl-fix1 무효 2 ({f['abl-fix1']['invalid']})")
        ok(f["abl-think"]["invalid"] == 5, f"missing: abl-think 무효 5 ({f['abl-think']['invalid']})")
        dbf = o["tasks_dropped_by_family"]
        ok(dbf["main"] == ["v1:t07"] and dbf["x1-02"] == ["v2:T20", "v2:T21", "v2:T22", "v2:T23"] and dbf["x1-04"] == [],
           f"G3 나: 묶음 안에서만 과제 빼기 {dbf}")
        cl = o["cells"]
        ok(all(cl[c]["tasks"] == 72 for c in X.CELLS_MAIN) and cl["abl3p-fix1"]["tasks"] == 73 and cl["abl-fix1-specoff"]["tasks"] == 28,
           f"G3 나: 주 칸 72 · X1-04 73 · spec off 28 ({ {c: cl[c]['tasks'] for c in cl} })")
        ok(o["results"]["H6"]["n_tasks"] == 28 and find(o, "H4", "H4 abl3p−abl fix1 [전체]")["n_tasks"] == 73,
           "G3 나: H6 은 28과제, H4 는 73과제(주 칸의 t07 빼기가 번지지 않음)")
        ok(f["abl-base"]["invalid_by"]["access"] == 1 and f["abl-base"]["access_unconfirmed"] == 1
           and f["orig-think"]["invalid_by"]["access"] == 1, "G1 나: path_scan 확인 회차만 무효(평가기·연구 경로), 미확인 1 은 따로 셈")
        ok(f["orig-fix1"]["invalid_by"]["isolation"] == 1 and f["orig-fix1"]["isolation_fail"] == 0,
           "G1 나: 울타리 갈래 격리 위반은 실패가 아니라 무효")
        ok(f["orig-base"]["invalid_overlap_pair"] == 1 and f["orig-base"]["invalid"] == 0,
           "빼기 순서: backend_error + 울타리 없음 회차는 ① 로 셈(겹침 1, 무효 0)")
        ok(["v2:T05", 4] in o["pairs_dropped"], "missing: 남은 backend_error (T05, 4) 를 모든 칸에서")
        ok(f["orig-base"]["rerun_left"] == 1 and all(f[c]["dropped_pair"] == 1 for c in X.CELLS_MAIN if c != "orig-base")
           and f["orig-base"]["dropped_pair"] == 1, "missing: 짝 제외 칸마다 1")
        ok(f["abl-base"]["host_unknown"] == 205, f"missing: end 줄 없는 조각 → 호스트 모름 205회차 ({f['abl-base']['host_unknown']})")
        ok(f["orig-think"]["alias_mismatch"] == 160, f"missing: 별칭 어긋남 160회차 ({f['orig-think']['alias_mismatch']})")
        ok(any("abl-fix1-specoff" in s and "무효" in s for s in o["stop_flags"]) and not any("abl-think" in s for s in o["stop_flags"]),
           f"missing: 무효 12.5% 칸만 중단 표시(1.4% 칸은 아님) {o['stop_flags']}")
        ok(len(o["inputs_sha256"]) == 8 * 2 - 1 + 2 + 2, "입력 sha: 결과 15 + runner.log + 차례표 + spec-log 2")
        ok(len(o["results"]["H5_spec"]["shards_missing"]) == 1, f"missing: end 줄 없는 조각은 H5 에서 빠짐 {o['results']['H5_spec']['shards_missing']}")
        # 판 막기
        bad = dict(cfg, set="confirm")
        try:
            X.analyze(bad, 10)
            ok(False, "confirm 묶음에 v1.2.3·v2.0.5 를 넣으면 거부")
        except SystemExit as e:
            ok("이 묶음" in str(e), "confirm 묶음에 v1.2.3·v2.0.5 를 넣으면 거부")
        # 같은 입력 = 같은 출력(시드 고정)
        a = X.analyze(make(tmp / "r1", "effect", 9), 500)
        b = X.analyze(make(tmp / "r2", "effect", 9), 500)
        ok(find(a, "H2", "H2 I_think [전체]")["ci95_pt"] == find(b, "H2", "H2 I_think [전체]")["ci95_pt"], "시드 고정: 같은 자료 → 같은 CI")
    print(f"\n{len(FAIL)} 실패")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
