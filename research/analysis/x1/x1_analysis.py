#!/usr/bin/env python3
"""P-001(X1-01·X1-02·X1-04) 분석본 — 평가 담당 2026-11-10(총괄 지시). 데이터를 보기 전에 등록(커밋)한다.

사용: python3 -I -B x1_analysis.py <설정.json> [--out <결과.json>] [--boot 10000]
설정.json:
  {"set": "explore" | "confirm",                       # explore = v1.2.3·v2.0.5 73과제, confirm = h1.1f 32과제(확증, 개정 2·4 §G)
   "mode": "fence" | "nofence",                        # 개정 4 §H 두 갈래(울타리 됨 / 안 됨). X1-01 은 fence(§I)
   "cells": {"orig-base": [<merged_shards 결과 폴더>…], "orig-fix1": […], "orig-think": […],
             "abl-base": […], "abl-fix1": […], "abl-think": […],
             "abl3p-fix1": […],                         # X1-04(없으면 H4 건너뜀)
             "abl-fix1-specoff": […]},                  # X1-02(v2 만, 없으면 H6 건너뜀)
   "aliases": {"orig": "<별칭>", "abl": "<별칭>", "abl3p": "<별칭>"},   # runner.log 별칭 대조
   "runner_logs": [<evalq runner.log>…], "schedule": "<섞은 차례표 파일>",
   "spec_logs": {"eval-host-a": "<spec-log.tsv>", "eval-host-b": "<…>"},  # H5 자료(빌린 GPU 감시기 v15 — v14 에서 들어옴)
   "runner_tz_offset_h": 9,                            # runner.log 시각(맥 KST) − spec-log 시각(UTC)
   "mtp": {"orig-fix1": <수락률>, "abl-fix1": <수락률>}}  # spec_logs 가 없을 때만 쓰는 손 값(없으면 "자료 없음")
결과: 표준 출력에 사람이 읽는 표, --out 에 JSON(입력 파일 sha·거른 수·대비·Holm). 실행 판·과제 내용은 싣지 않는다(ID·숫자만).
환경 변수: X1_RESEARCH_RX = 연구 저장소·연구 작업 경로 정규식(기본 /path/to/stewart-research). 원래 실행 값은 그 기계 경로라 싣지 않는다.
  access 확인에 쓰는 path_scan.py 의 PATH_SCAN_* 도 같다(path_scan.py 머리).

규칙(등록 문장 → 코드, 어긋남·모호함은 KNOWN_GAPS 에 적고 문장 쪽은 연구가 정함):
- 성공 = 결과 줄 success. 과제 단위 = 그 칸에서 유효 회차 성공의 평균. 묶음 값 = 과제 평균(과제마다 같은 무게).
- 거르기 순서(개정 4 §R): ① 다시 돌림 대상(stop_reason backend_error, rc timeout·spool-timeout·refused)이 merge 뒤에도 남으면
                 그 (과제, 회차)를 모든 칸에서 함께 뺌(§8, G2 "가"). 한 회차가 ①② 둘 다면 ① 로 센다(겹친 수는 따로).
               ② 남은 회차의 무효·제외(사람 결정 11/10 G1 "나", 그 칸에서만):
                 mode fence: agent_fence 가 "stewart-agent.sb …" 아님 · leak · isolation_violation ·
                             access_suspicious 이면서 path_scan 이 평가기·기록·연구 경로 접근(OPEN·LIST)을 확인한 회차. 까닭별로 센다.
                             access_suspicious 인데 path_scan 확인이 없는 회차는 유효로 두고 따로 센다(거짓 양성).
                 mode nofence: leak 또는 access_suspicious 면 무효·제외(§H 그대로). 격리 위반은 성공을 실패로 두고 셈(§8)
               ③ 한 칸에서 유효 회차가 0 인 과제는 같은 대비 묶음 안의 칸에서만 뺌(G3 "나"): 주 6칸끼리 · X1-04 짝(abl3p-fix1·abl-fix1) ·
                  X1-02 짝(abl-fix1·abl-fix1-specoff). 대비는 자기 묶음의 빼기를 쓴다.
  rc 칸(평가기 결과 줄, agentspawn.py): "timeout" = 실행 대행이 시간 상한에 에이전트를 죽임 · "spool-timeout" = 평가기가 대행의 .done 을
  timeout+120초 안에 못 받음 · "refused" = evalq 실행 대행(울타리 밖 본체)이 평가기의 에이전트 띄우기 요청을 검사에서 거절해 띄우지 않음
  (evalq.py agent-req 검사, .done refused_why — 에이전트는 돌지 않았다).
  중단 표시: 한 칸 무효 > 5%(§H) 또는 backend_error > 10%(§8).
- 부트스트랩: 과제를 묶음(v1·v2·h1)별로 층화해 복원 추출, B=10,000, 시드 20261110, 모든 대비가 같은 추출 표본을 쓴다(짝 유지).
  95% 백분위 CI. TOST 는 90% CI 가 여유 안. 한쪽 p = (부트스트랩 값이 귀무 쪽인 수 + 1) / (B + 1).
- Holm: 확증 대비 H2 2개 + H3 6개(한쪽 p, α=0.05). explore 에서도 같은 계산을 하되 "탐색" 으로 표시.
- 과제 고정 추론(보조): 두 칸 대비는 과제 층 안 회차 순열(같은 추출 수), 상호작용은 과제 안 이항 분산으로 낸 정규 근사 CI.
- 확증 판 "h1.1f" = 보류 묶음 h1.1 의 울타리 실행 판: 과제 sha aa97ed44450d7e5a1eb8950a91061d59333c382f97aab36bcc0d4f4db1301266(h1.1 과 같음,
  task_sha.py 로 다시 셈), 실행 판 sha(run_eval --hash) 598e8cea24b3d4a430f26303a68ffff1b4108b56f97d5bc7496ef1cd284781b7.
- 호스트 층: runner.log 의 조각 줄(→ 호스트 차선 k(별칭))로 회차마다 호스트를 붙이고, 칸 × 호스트 성공률과
  호스트를 층으로 둔 짝 차이(호스트마다 과제 평균 차이 → 회차 수 무게 평균)를 함께 낸다.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import path_scan as PS  # noqa: E402  (G1: access_suspicious 확인 — 같은 폴더의 path_scan.py, sha 는 결과 JSON 에)

SEED = 20261110
CELLS_MAIN = ("orig-base", "orig-fix1", "orig-think", "abl-base", "abl-fix1", "abl-think")
CELLS_ALL = CELLS_MAIN + ("abl3p-fix1", "abl-fix1-specoff")
CELL_MODEL = {c: c.split("-")[0] for c in CELLS_ALL}
FAMILIES = {"main": CELLS_MAIN, "x1-04": ("abl3p-fix1", "abl-fix1"), "x1-02": ("abl-fix1", "abl-fix1-specoff")}
CELL_FAMILY = {**{c: "main" for c in CELLS_MAIN}, "abl3p-fix1": "x1-04", "abl-fix1-specoff": "x1-02"}   # 칸 표에 쓸 묶음
RX_RESEARCH = re.compile(os.environ.get("X1_RESEARCH_RX", "/path/to/stewart-research") + r"(?:/|\b)")
EXPLORE_VERSIONS = {"v1.2.3": "v1", "v2.0.5": "v2"}
CONFIRM_VERSIONS = {"h1.1f": "h1"}
RERUN_STOPS = {"backend_error"}
RERUN_RC = {"timeout", "spool-timeout", "refused"}
DEFECTS = ("D1", "D2", "D3", "D4", "D5", "D6", "D7")
H3_DEFECTS = ("D2", "D3", "D4")
EQ_H2 = 0.05      # H2 해석 "|I| < 5pt 동등성"
EQ_H4 = 0.03      # H4 ±3pt
EQ_H6 = 0.08      # H6 ±8pt

KNOWN_GAPS = [
    "G1 결정 '나'(11/10): 울타리 갈래에서도 leak·access_suspicious·isolation_violation 회차는 무효로 빼고 수를 보고. access_suspicious 는 path_scan 확인(평가기·기록·대기열·운영 기록·클론·평가 cache·연구 경로를 인자에서 OPEN·LIST)만. '정답 저장소' 경로는 따로 정해진 것이 없어 평가기 폴더(정답 파일이 거기 있음)로 갈음.",
    "G2 §8 '그 (과제, 회차)를 짝이 되는 모든 칸에서 함께 뺀다' — 회차 번호는 칸 사이 짝이 아니다(독립 회차). 코드는 글자 그대로 같은 회차 번호를 모든 칸에서 뺀다.",
    "G3 결정 '나'(11/10): 과제 빼기는 같은 대비 묶음 안에서만(주 6칸 · X1-04 짝 · X1-02 짝). abl-fix1 은 세 묶음에 다 들어가므로 묶음마다 과제 수가 다를 수 있다.",
    "G4 Holm 에 넣을 p 의 정의(한쪽·양쪽, 부트스트랩 p 계산법)가 등록에 없다 → 코드는 한쪽 부트스트랩 p((귀무 쪽 수+1)/(B+1)), α=0.05.",
    "G5 H2 해석 '|I| < 5pt 동등성' 의 검정이 없다 → 코드는 90% CI 가 ±5pt 안(TOST). '그 밖 → 판정 보류' 와 'I<0 → 원본에 더 이로움' 의 기준(CI 상한 < 0?)도 없다 → 코드는 CI 상한 < 0.",
    "G6 결함률이 회차 단위 '있음/없음' 인지 횟수인지 없다 → 코드는 회차마다 있음/없음(0·1)의 과제 평균.",
    "G7 D3 의 'completed ∧ ¬success' 는 실패와 거의 같아 J_{k,D3} 가 성공률 상호작용의 부호 반대에 가깝다 — 문장 그대로 둠.",
    "G8 D1 의 'content 에 샌 툴콜 표지' 의 표지 글이 정해지지 않았다 → 코드는 '<tool_call>' 또는 '<function=' (Qwen 계열 coder 툴콜 꼴).",
    "G9 '호스트를 블록으로 보고 분석에서 층으로 둔다' 의 계산이 없다 → 코드는 호스트 층별 짝 차이의 회차 수 무게 평균과 칸 × 호스트 표를 보조로 냄(주 CI 는 층 없는 부트스트랩). 조각이 한 호스트에서 돌아서 한 칸 안에서 호스트와 과제 일부가 겹친다.",
    "G10 부트스트랩을 묶음(v1·v2)별로 층화할지 없다 → 코드는 층화(73과제 = 41 + 32 꼴 유지).",
    "G11 H5 수락률의 자료 출처가 등록에 없다 → 코드는 빌린 GPU 감시기 v15 spec-log.tsv(1분마다 차선별 누계)를 조각 시작 직전 줄부터 끝 직후 줄까지의 누계 차로 칸에 모은다(수락률 = accepted_tokens/draft_tokens 합). 1분 해상도라 같은 차선에서 잇달아 돈 조각 경계의 1분 안팎이 두 조각에 겹쳐 셀 수 있다. 통계 검정 없이 값만(등록 '속도 지표로만').",
    "G12 결함 매개(회차 단위 로지스틱)는 탐색이고 모형식이 없다 → 이 분석본에 넣지 않음.",
    "G13 과제 고정 추론의 상호작용용 순열 방법이 정해지지 않았다 → 코드는 과제 안 이항 분산의 정규 근사 CI 로 대신함.",
]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- 읽기
def load_dir(d: Path, versions: dict):
    meta = json.loads((d / "summary.json").read_text(encoding="utf-8"))["meta"]
    v = meta.get("eval_version")
    if v not in versions:
        raise SystemExit(f"{d.name}: 평가기 판 {v} 는 이 묶음({sorted(versions)})이 아니다")
    kind = versions[v]
    rows = []
    for line in (d / "results.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            r["_kind"] = kind
            r["_key"] = f"{kind}:{r['task']}"
            rows.append(r)
    return meta, rows


def defect_flags(r: dict) -> dict:
    s, steps = {}, []
    rd = r.get("run_dir")
    if rd:
        try:
            s = json.loads((Path(rd) / "summary.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            s = {}
        try:
            for line in (Path(rd) / "events.jsonl").read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("kind") == "step":
                    steps.append(e.get("response") or {})
                elif e.get("kind") == "rollback":
                    s["_rollbacks"] = s.get("_rollbacks", 0) + 1
        except OSError:
            pass
    fin_len = len_empty = stop_empty = dup = leaked = 0
    prev = None
    for resp in steps:
        fr = resp.get("finish_reason")
        tcs = resp.get("tool_calls") or []
        content = resp.get("content") or ""
        empty = not content.strip() and not tcs
        fin_len += fr == "length"
        len_empty += fr == "length" and empty
        stop_empty += fr == "stop" and empty
        if not tcs and ("<tool_call>" in content or "<function=" in content):
            leaked += 1
        sig = json.dumps([[(t.get("function") or {}).get("name"), (t.get("function") or {}).get("arguments")] for t in tcs]) if tcs else None
        dup += bool(sig) and sig == prev
        prev = sig
    stop = r.get("stop_reason")
    q_bad = r.get("q_expect") is not None and r.get("q_verdict") != r.get("q_expect")
    return {
        "D1": bool((r.get("parse_failures") or 0) or (s.get("format_failures") or 0) or leaked or stop == "consecutive_errors"),
        "D2": bool(fin_len or len_empty or stop_empty or (s.get("length_stops") or 0)),
        "D3": bool(stop == "asked" or q_bad or (stop == "completed" and not r.get("success"))),
        "D4": bool(dup or (s.get("repeat_nudges") or 0) or stop in ("repeated_failure", "max_steps")),
        "D5": bool((r.get("tool_errors") or 0) or s.get("_rollbacks")),
        "D6": bool(r.get("language_retries") or s.get("language_retries")),
        "D7": bool(r.get("compliance_ok") is False or r.get("out_of_workdir")),
        "_length_stops": int(s.get("length_stops") or fin_len),
    }


RX_ASSIGN = re.compile(r"^\S+ \S+ step (\S+) → (\S+) 차선 (\d+)\(별칭 ([^)]+)\)")
RX_START = re.compile(r"^(\S+ \S+) step (\S+) start (\S+) 차선 (\d+) HEAD")
RX_END = re.compile(r"^(\S+ \S+) (\S+) \S+ end rc=0 (\S+)")
TFMT = "%Y-%m-%d %H:%M:%S"


def load_runner_logs(paths):
    """조각 결과 폴더 이름 → {host, lane, alias, start, end}. 같은 조각이 두 번 돌면 rc=0 으로 끝난 마지막 배정.
    start·end 는 runner.log 시각(맥 현지 시각, naive datetime)."""
    out = {}
    for p in paths:
        last, began = {}, {}
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            m = RX_ASSIGN.match(line)
            if m:
                last[m.group(1)] = {"host": m.group(2), "lane": int(m.group(3)), "alias": m.group(4)}
                continue
            m = RX_START.match(line)
            if m:
                began[m.group(2)] = dt.datetime.strptime(m.group(1), TFMT)
                continue
            m = RX_END.match(line)
            if m and m.group(2) in last:
                out[Path(m.group(3)).name] = dict(last[m.group(2)], step=m.group(2), start=began.get(m.group(2)),
                                                  end=dt.datetime.strptime(m.group(1), TFMT))
    return out


def parse_ts(x: str):
    x = x.strip().replace("T", " ").rstrip("Z")
    for f in (TFMT, "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return dt.datetime.strptime(x, f)
        except ValueError:
            pass
    return None


def load_spec_log(path):
    """감시기 v15 꼴: 시각(UTC) · 차선 · 별칭 · 어댑터 · drafts · draft_tokens · accepted_tokens · 자리별 수락 비율(쉼표). 탭 구분.
    머리줄·꼴이 다른 줄은 건너뛰고 센다. → ({차선: [(시각, drafts, draft_tokens, accepted)]}, 건너뛴 줄 수)"""
    lanes, skipped = {}, 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        f = line.rstrip("\n").split("\t")
        t = parse_ts(f[0]) if f else None
        try:
            row = (t, int(f[4]), int(f[5]), int(f[6]))
            lane = int(f[1])
        except (IndexError, ValueError):
            row = None
        if t is None or row is None:
            skipped += bool(line.strip())
            continue
        lanes.setdefault(lane, []).append(row)
    for v in lanes.values():
        v.sort()
    return lanes, skipped


def counter_delta(rows, t0, t1):
    """t0 직전(≤ t0) 줄부터 t1 직후(≥ t1) 줄까지 누계 증가(줄어든 줄 = 서버 재시작 경계 → 그 줄 값 전부를 증가로)."""
    i0 = max([i for i, r in enumerate(rows) if r[0] <= t0], default=None)
    i1 = min([i for i, r in enumerate(rows) if r[0] >= t1], default=None)
    if i0 is None or i1 is None or i1 <= i0:
        return None
    acc = [0, 0, 0]
    for a, b in zip(rows[i0:i1], rows[i0 + 1:i1 + 1]):
        for j in range(3):
            d = b[j + 1] - a[j + 1]
            acc[j] += d if d >= 0 else b[j + 1]
    return acc


def spec_by_cell(cfg, kept, spans):
    """조각마다 그 차선의 누계 차 → 칸 합. 수락률 = accepted_tokens / draft_tokens."""
    logs = {h: load_spec_log(p) for h, p in (cfg.get("spec_logs") or {}).items()}
    if not logs:
        return None
    off = dt.timedelta(hours=cfg.get("runner_tz_offset_h", 9))
    shard_cell = {}
    for c, rs in kept.items():
        for r in rs:
            sh = shard_of(r.get("run_dir") or "")
            if sh:
                shard_cell[sh] = c
    cells, missing = {}, []
    for sh, c in sorted(shard_cell.items()):
        sp = spans.get(sh)
        lg = logs.get(sp["host"]) if sp else None
        if not sp or not sp.get("start") or lg is None or sp["lane"] not in lg[0]:
            missing.append(sh)
            continue
        d = counter_delta(lg[0][sp["lane"]], sp["start"] - off, sp["end"] - off)
        if d is None:
            missing.append(sh)
            continue
        x = cells.setdefault(c, {"shards": 0, "drafts": 0, "draft_tokens": 0, "accepted_tokens": 0})
        x["shards"] += 1
        x["drafts"] += d[0]
        x["draft_tokens"] += d[1]
        x["accepted_tokens"] += d[2]
    for x in cells.values():
        x["acceptance"] = round(x["accepted_tokens"] / x["draft_tokens"], 4) if x["draft_tokens"] else None
    return {"by_cell": cells, "shards_missing": missing, "skipped_lines": {h: v[1] for h, v in logs.items()}}


def shard_of(run_dir: str):
    parts = Path(run_dir).parts
    for i, x in enumerate(parts):
        if x == "stewart-runs" and i > 0:
            return parts[i - 1]
    return None


# ---------------------------------------------------------------- 거르기
def access_confirmed(r: dict) -> bool:
    """G1: access_suspicious 회차를 path_scan 으로 확인 — 인자에 민감 경로(OPEN·LIST)가 있거나 연구 경로가 있으면 참."""
    rd = r.get("run_dir")
    if not rd:
        return False
    ev = Path(rd) / "events.jsonl"
    if any(k in ("OPEN", "LIST") for _, k, _, _ in PS.scan(str(ev), r.get("workdir"))):
        return True
    try:
        for line in ev.read_text(encoding="utf-8").splitlines():
            if RX_RESEARCH.search(line):
                e = json.loads(line)
                for c in ((e.get("response") or {}).get("tool_calls") or []) if e.get("kind") == "step" else []:
                    args = c.get("raw_arguments") or (c.get("function") or {}).get("arguments") or ""
                    if RX_RESEARCH.search(args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)):
                        return True
    except (OSError, ValueError):
        pass
    return False


def filter_rows(cells: dict, mode: str, hosts: dict, aliases: dict):
    """빼기 순서(총괄 11/10): ① §8 다시 돌림 남은 (과제, 회차) 를 모든 칸에서(G2) → ② 남은 회차에 무효(G1 나, 그 칸에서만)
    → ③ 유효 회차 0 인 과제를 대비 묶음 안에서(G3 나). 한 회차에 ①② 가 둘 다면 ① 로 세고 겹친 수를 따로 낸다."""
    rep = {c: {"runs": len(rs), "invalid": 0, "invalid_by": {"fence": 0, "leak": 0, "isolation": 0, "access": 0},
               "invalid_overlap_pair": 0, "access_unconfirmed": 0, "rerun_left": 0, "isolation_fail": 0,
               "host_unknown": 0, "alias_mismatch": 0}
           for c, rs in cells.items()}
    drop_pairs = set()
    for rs in cells.values():
        for r in rs:
            r["_rerun"] = r.get("stop_reason") in RERUN_STOPS or str(r.get("rc")) in RERUN_RC
            if r["_rerun"]:
                drop_pairs.add((r["_key"], r["rep"]))
    for c, rs in cells.items():
        for r in rs:
            why = []
            if mode == "fence":
                if not str(r.get("agent_fence") or "").startswith("stewart-agent.sb"):
                    why.append("fence")
                if r.get("leak"):
                    why.append("leak")
                if r.get("isolation_violation"):
                    why.append("isolation")
                if r.get("access_suspicious"):
                    if access_confirmed(r):
                        why.append("access")
                    else:
                        rep[c]["access_unconfirmed"] += 1
            else:
                if r.get("leak"):
                    why.append("leak")
                if r.get("access_suspicious"):
                    why.append("access")
            in_pair = (r["_key"], r["rep"]) in drop_pairs
            rep[c]["rerun_left"] += r["_rerun"]
            r["_invalid"] = bool(why) and not in_pair          # ① 이 먼저 — 겹치면 ① 로
            rep[c]["invalid_overlap_pair"] += bool(why) and in_pair
            if r["_invalid"]:
                for w in why:
                    rep[c]["invalid_by"][w] += 1
            rep[c]["invalid"] += r["_invalid"]
            bad = mode != "fence" and bool(r.get("isolation_violation"))
            r["_ok"] = bool(r.get("success")) and not bad
            rep[c]["isolation_fail"] += bad and bool(r.get("success"))
            h = hosts.get(shard_of(r.get("run_dir") or "") or "")
            r["_host"] = h["host"] if h else None
            rep[c]["host_unknown"] += h is None
            want = aliases.get(CELL_MODEL[c])
            if h and want and h["alias"] != want:
                rep[c]["alias_mismatch"] += 1
    kept = {}
    for c, rs in cells.items():
        kept[c] = [r for r in rs if not r["_invalid"] and (r["_key"], r["rep"]) not in drop_pairs]
        rep[c]["dropped_pair"] = sum(1 for r in rs if (r["_key"], r["rep"]) in drop_pairs)
    # §H + G3 "나": 한 칸에서 유효 회차 0 인 과제는 같은 대비 묶음 안의 칸에서만
    all_tasks = set().union(*[{r["_key"] for r in rs} for rs in cells.values()]) if cells else set()
    dead = {}
    for fam, members in FAMILIES.items():
        dd = set()
        for c in members:
            if c in cells:
                dd |= {r["_key"] for r in cells[c]} - {r["_key"] for r in kept[c]}
        dead[fam] = dd
    stop = []
    for c, x in rep.items():
        x["invalid_pct"] = 100.0 * x["invalid"] / x["runs"] if x["runs"] else 0.0
        x["backend_error_pct"] = 100.0 * sum(r.get("stop_reason") == "backend_error" for r in cells[c]) / x["runs"] if x["runs"] else 0.0
        if x["invalid_pct"] > 5:
            stop.append(f"{c}: 무효 {x['invalid_pct']:.1f}% > 5%(§H)")
        if x["backend_error_pct"] > 10:
            stop.append(f"{c}: backend_error {x['backend_error_pct']:.1f}% > 10%(§8)")
    return kept, rep, dead, len(all_tasks), sorted(drop_pairs), stop


# ---------------------------------------------------------------- 과제 값
def task_table(rows, key):
    acc = {}
    for r in rows:
        v = r["_ok"] if key == "success" else r["_def"][key]
        a = acc.setdefault(r["_key"], [0, 0])
        a[0] += bool(v)
        a[1] += 1
    return {t: (s / n, n, s) for t, (s, n) in acc.items()}


class Boot:
    def __init__(self, tasks, B, seed):
        self.tasks = sorted(tasks)
        groups = {}
        for i, t in enumerate(self.tasks):
            groups.setdefault(t.split(":")[0], []).append(i)
        rnd = random.Random(seed)
        self.idx = []
        for _ in range(B):
            draw = []
            for g in sorted(groups):
                ids = groups[g]
                draw.extend(ids[rnd.randrange(len(ids))] for _ in ids)
            self.idx.append(draw)

    def run(self, per_task: dict):
        """per_task: 과제 → 값(없는 과제는 빠짐). 추출에서 빠진 과제는 건너뛴다(그 과제가 없는 대비만)."""
        vec = [per_task.get(t) for t in self.tasks]
        have = [v for v in vec if v is not None]
        if not have:
            return None
        est = sum(have) / len(have)
        bs = []
        for draw in self.idx:
            s = n = 0
            for i in draw:
                v = vec[i]
                if v is not None:
                    s += v
                    n += 1
            if n:
                bs.append(s / n)
        bs.sort()
        return est, bs, len(have)


def q(bs, p):
    k = min(len(bs) - 1, max(0, int(round(p * (len(bs) - 1)))))
    return bs[k]


def summarize(name, res, direction=None, eq=None):
    if res is None:
        return {"name": name, "n_tasks": 0, "note": "자료 없음"}
    est, bs, n = res
    out = {"name": name, "n_tasks": n, "est_pt": round(est * 100, 2),
           "ci95_pt": [round(q(bs, 0.025) * 100, 2), round(q(bs, 0.975) * 100, 2)],
           "ci90_pt": [round(q(bs, 0.05) * 100, 2), round(q(bs, 0.95) * 100, 2)]}
    B = len(bs)
    if direction == ">":
        out["p_one_sided"] = (sum(b <= 0 for b in bs) + 1) / (B + 1)
    elif direction == "<":
        out["p_one_sided"] = (sum(b >= 0 for b in bs) + 1) / (B + 1)
    elif direction == "two":
        lo = sum(b <= 0 for b in bs)
        hi = sum(b >= 0 for b in bs)
        out["p_two_sided"] = min(1.0, 2 * (min(lo, hi) + 1) / (B + 1))
    if eq is not None:
        out["tost_margin_pt"] = eq * 100
        out["tost_equivalent"] = q(bs, 0.05) > -eq and q(bs, 0.95) < eq
    return out


def fixed_task_ci(tabs, signs, tasks):
    """과제 고정: 과제 안 이항 분산으로 낸 정규 근사 95%·90% CI. tabs = [칸 표…], signs = 같은 길이 ±1."""
    est = var = 0.0
    n = 0
    for t in tasks:
        if not all(t in tb for tb in tabs):
            continue
        n += 1
        for tb, sg in zip(tabs, signs):
            p, k, _ = tb[t]
            est += sg * p
            var += p * (1 - p) / max(k - 1, 1)
    if not n:
        return None
    est /= n
    se = math.sqrt(var) / n
    return {"est_pt": round(est * 100, 2), "ci95_pt": [round((est - 1.96 * se) * 100, 2), round((est + 1.96 * se) * 100, 2)],
            "ci90_pt": [round((est - 1.645 * se) * 100, 2), round((est + 1.645 * se) * 100, 2)]}


def perm_pair(rows_a, rows_b, B, seed):
    """과제 고정 보조: 과제 층 안 회차 순열로 두 칸 차이의 양쪽 p."""
    by = {}
    for r in rows_a:
        by.setdefault(r["_key"], ([], []))[0].append(int(r["_ok"]))
    for r in rows_b:
        by.setdefault(r["_key"], ([], []))[1].append(int(r["_ok"]))
    strata = [(a, b) for a, b in by.values() if a and b]
    if not strata:
        return None

    def stat(st):
        return sum(sum(a) / len(a) - sum(b) / len(b) for a, b in st) / len(st)
    obs = stat(strata)
    rnd = random.Random(seed)
    ge = 0
    for _ in range(B):
        st = []
        for a, b in strata:
            pool = a + b
            rnd.shuffle(pool)
            st.append((pool[:len(a)], pool[len(a):]))
        ge += abs(stat(st)) >= abs(obs) - 1e-12
    return {"est_pt": round(obs * 100, 2), "p_two_sided": (ge + 1) / (B + 1), "n_tasks": len(strata)}


def host_stratified(rows_a, rows_b):
    hosts = sorted({r["_host"] for r in rows_a + rows_b if r["_host"]})
    parts = []
    for h in hosts:
        ta = task_table([r for r in rows_a if r["_host"] == h], "success")
        tb = task_table([r for r in rows_b if r["_host"] == h], "success")
        common = set(ta) & set(tb)
        if common:
            d = sum(ta[t][0] - tb[t][0] for t in common) / len(common)
            w = sum(ta[t][1] + tb[t][1] for t in common)
            parts.append({"host": h, "n_tasks": len(common), "runs": w, "diff_pt": round(d * 100, 2)})
    if not parts:
        return None
    W = sum(p["runs"] for p in parts)
    return {"by_host": parts, "weighted_pt": round(sum(p["diff_pt"] * p["runs"] for p in parts) / W, 2)}


def holm(items, alpha=0.05):
    order = sorted(range(len(items)), key=lambda i: items[i]["p_one_sided"])
    m = len(items)
    stop = False
    for rank, i in enumerate(order):
        thr = alpha / (m - rank)
        rej = (not stop) and items[i]["p_one_sided"] <= thr
        stop = stop or not rej
        items[i]["holm_threshold"] = thr
        items[i]["holm_reject"] = rej
    return items


def h2_verdict(s):
    if s.get("n_tasks", 0) == 0:
        return "자료 없음"
    lo, hi = s["ci95_pt"]
    if lo > 0:
        return "손상 특이적 회복(CI 하한 > 0)"
    if s["ci90_pt"][0] > -EQ_H2 * 100 and s["ci90_pt"][1] < EQ_H2 * 100:
        return "일반 개선(|I| < 5pt 동등)"
    if hi < 0:
        return "원본에 더 이로움(CI 상한 < 0)"
    return "판정 보류"


# ---------------------------------------------------------------- 본체
def analyze(cfg: dict, B: int):
    versions = CONFIRM_VERSIONS if cfg.get("set") == "confirm" else EXPLORE_VERSIONS
    mode = cfg.get("mode", "fence")
    inputs, cells, metas = {}, {}, {}
    for c, dirs in cfg["cells"].items():
        if c not in CELLS_ALL:
            raise SystemExit(f"모르는 칸 {c}")
        cells[c] = []
        for d in dirs:
            d = Path(d)
            meta, rows = load_dir(d, versions)
            metas.setdefault(c, []).append({"dir": d.name, "eval_version": meta.get("eval_version"), "eval_hash": meta.get("eval_hash")})
            inputs[str(d / "results.jsonl")] = sha256(d / "results.jsonl")
            cells[c].extend(rows)
    for p in cfg.get("runner_logs", []):
        inputs[p] = sha256(Path(p))
    if cfg.get("schedule"):
        inputs[cfg["schedule"]] = sha256(Path(cfg["schedule"]))
    hosts = load_runner_logs(cfg.get("runner_logs", []))
    base, rep, dead, n_all, drop_pairs, stop = filter_rows(cells, mode, hosts, cfg.get("aliases", {}))
    for rs in base.values():
        for r in rs:
            r["_def"] = defect_flags(r)

    def fam_rows(fam):
        return {c: [r for r in base[c] if r["_key"] not in dead[fam]] for c in FAMILIES[fam] if c in base}
    KF = {fam: fam_rows(fam) for fam in FAMILIES}
    TF = {fam: {c: task_table(rs, "success") for c, rs in KF[fam].items()} for fam in FAMILIES}
    kept, tabs = KF["main"], TF["main"]
    tasks = set().union(*[set(task_table(rs, "success")) for rs in base.values()]) if base else set()
    boot = Boot(tasks, B, SEED)
    out = {"script_sha256": sha256(Path(__file__)), "path_scan_sha256": sha256(Path(PS.__file__)),
           "set": cfg.get("set", "explore"), "mode": mode, "B": B, "seed": SEED,
           "inputs_sha256": inputs, "meta": metas, "filter": rep, "tasks_all": n_all,
           "tasks_dropped_by_family": {f: sorted(v) for f, v in dead.items()},
           "pairs_dropped": [list(x) for x in drop_pairs], "stop_flags": stop, "known_gaps": KNOWN_GAPS}

    def per(tab_a, tab_b, kinds=None):
        return {t: tab_a[t][0] - tab_b[t][0] for t in tab_a if t in tab_b and (kinds is None or t.split(":")[0] in kinds)}

    def inter(c_k, c_b, o_k, o_b, key="success", kinds=None):
        T = {c: (tabs[c] if key == "success" else task_table(kept[c], key)) for c in (c_k, c_b, o_k, o_b)}
        common = set(T[c_k]) & set(T[c_b]) & set(T[o_k]) & set(T[o_b])
        return {t: (T[c_k][t][0] - T[c_b][t][0]) - (T[o_k][t][0] - T[o_b][t][0]) for t in common
                if kinds is None or t.split(":")[0] in kinds}, T

    subsets = [("전체", None)] + [(k, {k}) for k in sorted({t.split(":")[0] for t in tasks})]
    def cell_row(rs, tb):
        return {"tasks": len(tb), "runs": len(rs),
                "success_pt": round(100 * sum(v[0] for v in tb.values()) / len(tb), 2) if tb else None,
                "by_host": {h: round(100 * sum(r["_ok"] for r in rs if r["_host"] == h) / max(1, sum(r["_host"] == h for r in rs)), 2)
                            for h in sorted({r["_host"] for r in rs if r["_host"]})}}
    out["cells"] = {c: dict(cell_row(KF[CELL_FAMILY[c]][c], TF[CELL_FAMILY[c]][c]), family=CELL_FAMILY[c]) for c in base}
    have = set(base)
    res = {}
    if {"orig-fix1", "abl-fix1"} <= have:
        res["H1"] = []
        for nm, ks in subsets:
            s = summarize(f"H1 orig−abl fix1 [{nm}]", boot.run(per(tabs["orig-fix1"], tabs["abl-fix1"], ks)), "two")
            s["task_fixed_perm"] = perm_pair([r for r in kept["orig-fix1"] if ks is None or r["_kind"] in ks],
                                             [r for r in kept["abl-fix1"] if ks is None or r["_kind"] in ks], min(B, 2000), SEED)
            s["host_strata"] = host_stratified(kept["orig-fix1"], kept["abl-fix1"]) if ks is None else None
            res["H1"].append(s)
    holm_items = []
    if set(CELLS_MAIN) <= have:
        res["H2"], res["H3"], res["H3_explore"] = [], [], []
        for nm, ks in subsets:
            for k in ("fix1", "think"):
                d, T = inter(f"abl-{k}", "abl-base", f"orig-{k}", "orig-base", kinds=ks)
                s = summarize(f"H2 I_{k} [{nm}]", boot.run(d), ">", EQ_H2)
                s["verdict"] = h2_verdict(s)
                s["task_fixed"] = fixed_task_ci([T[f"abl-{k}"], T["abl-base"], T[f"orig-{k}"], T["orig-base"]], [1, -1, -1, 1], set(d))
                res["H2"].append(s)
                if ks is None:
                    holm_items.append(s)
            for D in DEFECTS:
                for k in ("fix1", "think"):
                    d, T = inter(f"abl-{k}", "abl-base", f"orig-{k}", "orig-base", key=D, kinds=ks)
                    s = summarize(f"H3 J_{k},{D} [{nm}]", boot.run(d), "<")
                    s["task_fixed"] = fixed_task_ci([T[f"abl-{k}"], T["abl-base"], T[f"orig-{k}"], T["orig-base"]], [1, -1, -1, 1], set(d))
                    (res["H3"] if D in H3_DEFECTS else res["H3_explore"]).append(s)
                    if ks is None and D in H3_DEFECTS:
                        holm_items.append(s)
        holm(holm_items)
        out["holm_family"] = [{"name": x["name"], "p": x["p_one_sided"], "threshold": x["holm_threshold"], "reject": x["holm_reject"]}
                              for x in holm_items]
        out["holm_label"] = "확증" if cfg.get("set") == "confirm" else "탐색(73과제 위 H2·H3 — §3)"
    if {"abl3p-fix1", "abl-fix1"} <= have:
        res["H4"] = []
        for nm, ks in subsets:
            t4, k4 = TF["x1-04"], KF["x1-04"]
            s = summarize(f"H4 abl3p−abl fix1 [{nm}]", boot.run(per(t4["abl3p-fix1"], t4["abl-fix1"], ks)), "two", EQ_H4)
            s["host_strata"] = host_stratified(k4["abl3p-fix1"], k4["abl-fix1"]) if ks is None else None
            res["H4"].append(s)
    spec = spec_by_cell(cfg, base, hosts)
    for p in (cfg.get("spec_logs") or {}).values():
        inputs[p] = sha256(Path(p))
    if spec:
        bc = spec["by_cell"]
        mtp = {c: bc[c]["acceptance"] for c in ("orig-fix1", "abl-fix1") if c in bc and bc[c]["acceptance"] is not None}
        res["H5_spec"] = spec
    else:
        mtp = cfg.get("mtp") or {}
    res["H5"] = ({"orig": mtp["orig-fix1"], "abl": mtp["abl-fix1"], "abl_lt_orig": mtp["abl-fix1"] < mtp["orig-fix1"],
                  "source": "spec_logs" if spec else "설정 mtp 값"}
                 if "orig-fix1" in mtp and "abl-fix1" in mtp else {"note": "자료 없음(G11)"})
    if {"abl-fix1-specoff", "abl-fix1"} <= have:
        on = [r for r in KF["x1-02"]["abl-fix1"] if r["_kind"] == "v2"]
        off = [r for r in KF["x1-02"]["abl-fix1-specoff"] if r["_kind"] == "v2"]
        ta, tb = task_table(on, "success"), task_table(off, "success")
        common = set(ta) & set(tb)
        fx = fixed_task_ci([ta, tb], [1, -1], common)
        s = {"name": "H6 spec on − off(abl fix1, v2, 과제 고정)", "n_tasks": len(common), "task_fixed": fx,
             "tost_margin_pt": EQ_H6 * 100,
             "tost_equivalent": bool(fx) and fx["ci90_pt"][0] > -EQ_H6 * 100 and fx["ci90_pt"][1] < EQ_H6 * 100,
             "task_fixed_perm": perm_pair(on, off, min(B, 2000), SEED),
             "aux": {}}
        for k in ("D1", "D4"):
            a, b = task_table(on, k), task_table(off, k)
            cm = set(a) & set(b)
            s["aux"][k + "_on_minus_off_pt"] = round(100 * sum(a[t][0] - b[t][0] for t in cm) / len(cm), 2) if cm else None
        s["aux"]["length_stops_on"] = sum(r["_def"]["_length_stops"] for r in on)
        s["aux"]["length_stops_off"] = sum(r["_def"]["_length_stops"] for r in off)
        res["H6"] = s
    out["results"] = res
    return out


def print_report(o):
    print(f"[x1_analysis] set={o['set']} mode={o['mode']} B={o['B']} seed={o['seed']} script={o['script_sha256'][:16]}")
    print("거르기(칸: 회차 · ② 무효(울타리/누설/격리/밖 접근, 사유 겹침 가능) · ①② 겹침(① 로 셈) · 밖 의심 미확인 · 다시돌림 남음 · ① 짝 제외 · 격리→실패 · 호스트 모름 · 별칭 어긋남)")
    for c, x in o["filter"].items():
        ib = x["invalid_by"]
        print(f"  {c:18s} {x['runs']:4d} · {x['invalid']} ({x['invalid_pct']:.1f}%: {ib['fence']}/{ib['leak']}/{ib['isolation']}/{ib['access']}) · "
              f"{x['invalid_overlap_pair']} · {x['access_unconfirmed']} · {x['rerun_left']} · {x['dropped_pair']} · {x['isolation_fail']} · {x['host_unknown']} · {x['alias_mismatch']}")
    print("  묶음별로 뺀 과제(G3): " + ", ".join(f"{f} {len(v)}" for f, v in o["tasks_dropped_by_family"].items())
          + f" / 전체 {o['tasks_all']}, 뺀 (과제, 회차) {len(o['pairs_dropped'])}")
    for s in o["stop_flags"]:
        print(f"  ** 중단 표시: {s}")
    print("칸 성공률(과제 평균)")
    for c, x in o["cells"].items():
        print(f"  {c:18s} {x['success_pt']}% ({x['tasks']}과제, {x['runs']}회차) 호스트별 {x['by_host']}")
    for h, items in o["results"].items():
        if isinstance(items, dict):
            items = [items]
        for s in items:
            ci = s.get("ci95_pt") or (s.get("task_fixed") or {}).get("ci95_pt")
            extra = " ".join(f"{k}={s[k]}" for k in ("p_one_sided", "p_two_sided", "tost_equivalent", "verdict") if k in s)
            est = s.get("est_pt", (s.get("task_fixed") or {}).get("est_pt"))
            print(f"  {s.get('name', h)}: {est} {ci} n={s.get('n_tasks')} {extra}" if "name" in s else f"  {h}: {s}")
    if "holm_family" in o:
        print(f"Holm({o['holm_label']}): " + ", ".join(f"{x['name']} p={x['p']:.4f} {'기각' if x['reject'] else '-'}" for x in o["holm_family"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--boot", type=int, default=10000)
    a = ap.parse_args()
    cfg = json.loads(a.config.read_text(encoding="utf-8"))
    o = analyze(cfg, a.boot)
    print_report(o)
    if a.out:
        a.out.write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
