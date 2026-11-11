"""stewart 명령줄.

  stewart run   -p <프로필> (-t 과제파일 | --task "글") [--backend 이름] [--workdir 폴더] [--max-steps N] [--runs-dir 폴더]
                [--continue | --resume 기록]   앞 대화 뒤에 과제를 붙여 이어 간다
  stewart bench -p <프로필> (-t … | --task …) -n N [--setup "명령"] [그 밖은 run과 같음]
  stewart media -i 이름목록.txt [--batch N] [--backend 이름] [--runs-dir 폴더]
  stewart chat  [-p chat] [--backend 이름] [--workdir 폴더] [--confirm] [--continue | --resume 기록] [첫 입력]
  stewart ping  [-b 이름]                      연결 확인(/models, 짧은 답, 툴콜)
  stewart loadtest [-b 이름] [-c 1,8] [--max-tokens 512] [--rounds 1] [--prompt-tokens N]
  stewart podstatus [-b 이름] [--port 8001]   파드 감시기 상태 읽기·기록(runs/pod-status.jsonl)
  stewart queue 대기열.toml [-j N] [--dry-run]  일괄 작업 대기열(백엔드 동시 상한 안에서 병렬)
  stewart rewind 기록 [--to N]                 체크포인트 목록 / N 번이 시작될 때로 파일 되돌리기
  stewart hub   [--project 폴더] [-b 이름] [--mode edits] [--port 8765]  메인 + 작업자 세션을 웹 화면(127.0.0.1) 하나로
  stewart hub url [--project 폴더]            떠 있는 허브의 접속 주소·로그인 토큰 다시 보기
  stewart sessions list|delete|restore|trash|empty|rename|archive [--project 폴더]  허브 지난 세션 정리(휴지통)
  stewart instance <이름> start|run|check|profile|dirs   따로 도는 허브(바깥 API 등)를 OS 샌드박스 안에 — instance.py
  stewart backends | profiles
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import engine
from .config import (DEFAULT_RUNS_DIR, REPO_ROOT, Backend, ConfigError, Profile, list_profiles, load_backends,
                     load_profile, user_profiles_dir)
from .record import Recorder
from .resume import ResumeError

SUMMARY_KEYS = ("stop_reason", "success", "steps", "elapsed_s", "tool_calls", "tool_errors", "parse_failures",
                "format_failures", "repeat_nudges", "language_retries", "gen_tok_per_s", "prompt_tok_per_s", "e2e_tok_per_s")


def _common(p: argparse.ArgumentParser, task: bool = True) -> None:
    if task:
        p.add_argument("-p", "--profile", required=True)
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument("-t", "--task-file")
        g.add_argument("--task")
        p.add_argument("--workdir")
        p.add_argument("--max-steps", type=int)
    p.add_argument("--backend")
    p.add_argument("--runs-dir", default=os.environ.get("STEWART_RUNS_DIR") or str(DEFAULT_RUNS_DIR))


def resolve(profile_name: str, backend_name: str | None, workdir: str | None = None,
            check_workdir: bool = True) -> tuple[Profile, Backend]:
    backends, default = load_backends()
    profile = load_profile(profile_name)
    if workdir:
        profile.workdir = Path(workdir).resolve()
    bname = backend_name or profile.backend or default
    if bname not in backends:
        raise ConfigError(f"백엔드가 없다: {bname} (있는 것: {', '.join(backends)})")
    backend = backends[bname]
    if not backend.enabled:
        raise ConfigError(f"백엔드 '{bname}'는 자리만 있고 꺼져 있다(backends.toml enabled=false)")
    if check_workdir and not profile.workdir.is_dir():
        raise ConfigError(f"작업 폴더가 없다: {profile.workdir}")
    return profile, backend


def _short(s: dict) -> dict:
    return {k: (round(s[k], 2) if isinstance(s.get(k), float) else s.get(k)) for k in SUMMARY_KEYS}


PRUNE_ROOT = Path(os.path.expanduser(os.environ.get("STEWART_PRUNE_ROOT") or
                                     os.path.join(os.environ.get("XDG_CACHE_HOME") or "~/.cache", "stewart")))
_OLD_SUFFIX = re.compile(r"\.old-\d{8}-\d{6}$")


def prune_old(workdir: Path, keep: int) -> list[Path]:
    """setup이 옮겨 둔 <작업폴더>.old-YYYYMMDD-HHMMSS 를 최근 keep개만 남기고 지운다.
    캐시 루트(PRUNE_ROOT = STEWART_PRUNE_ROOT, 없으면 $XDG_CACHE_HOME/stewart 또는 ~/.cache/stewart) 아래일 때만 지운다."""
    workdir = workdir.resolve()
    try:
        workdir.relative_to(PRUNE_ROOT.resolve())
    except ValueError:
        return []
    olds = sorted(p for p in workdir.parent.glob(workdir.name + ".old-*")
                  if p.is_dir() and not p.is_symlink() and _OLD_SUFFIX.search(p.name))
    gone = olds[: max(0, len(olds) - keep)]
    for p in gone:
        shutil.rmtree(p)
    return gone


def cmd_bench(a, profile: Profile, backend: Backend, task: str) -> int:
    """같은 과제를 N번 돌려 성공률·단계 분포를 남긴다. 회차마다 --setup 명령을 먼저 돌린다(저장소 루트에서).
    -j K(K>1)면 K개씩 동시에 돈다. 이때 회차마다 작업 폴더가 달라야 하므로 --setup·--workdir 에 {i}(회차 번호)를 넣는다.
    실제 동시 요청 수는 백엔드 concurrency 상한을 넘지 않는다."""
    jobs = max(1, a.jobs)
    wd_tpl = a.workdir if a.workdir and "{i}" in a.workdir else None
    if jobs > 1 and not wd_tpl:
        print("stewart: -j 2 이상이면 --workdir 에 {i} 가 있어야 한다(회차마다 다른 작업 폴더)", file=sys.stderr)
        return 2
    if jobs > 1 and a.setup and "{i}" not in a.setup:
        print("stewart: -j 2 이상이면 --setup 에도 {i} 가 있어야 한다", file=sys.stderr)
        return 2
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in profile.name)
    bdir = Path(a.runs_dir) / f"bench-{stamp}-{safe}"
    bdir.mkdir(parents=True)
    print(f"stewart: bench 기록 {bdir} (동시 {jobs}, 백엔드 상한 {backend.concurrency})", file=sys.stderr)
    lock = threading.Lock()
    t_bench = time.monotonic()

    def one(i: int) -> dict:
        prof = dataclasses.replace(profile, workdir=Path(wd_tpl.format(i=i)).resolve()) if wd_tpl else profile
        if a.setup:
            from .osdep import run_command
            r = run_command(a.setup.format(i=i), cwd=REPO_ROOT, capture_output=True)
            if r.returncode != 0:
                raise RuntimeError(f"setup 실패(회차 {i}): {r.stdout}{r.stderr}")
            pruned = prune_old(prof.workdir, a.keep_old)
            if pruned:
                with lock:
                    print(f"stewart: 오래된 작업 폴더 정리 {len(pruned)}개: {', '.join(p.name for p in pruned)}", file=sys.stderr)
        if not prof.workdir.is_dir():
            raise RuntimeError(f"작업 폴더가 없다(회차 {i}): {prof.workdir}")
        rec = Recorder(bdir, f"{i:02d}-{prof.name}")
        s = engine.run(prof, backend, task, rec, a.max_steps)
        row = {"run": i, "dir": rec.dir.name, "workdir": str(prof.workdir), **_short(s), "model_time_s": round(s["model_time_s"], 1)}
        with lock:
            print(json.dumps(row, ensure_ascii=False), file=sys.stderr)
        return row

    try:
        if jobs == 1:
            rows = [one(i) for i in range(1, a.n + 1)]
        else:
            with ThreadPoolExecutor(max_workers=jobs) as ex:
                rows = list(ex.map(one, range(1, a.n + 1)))
    except RuntimeError as e:
        print(f"stewart: {e}", file=sys.stderr)
        return 2
    wall = time.monotonic() - t_bench

    steps = [r["steps"] for r in rows]
    ok = [r for r in rows if r["success"]]
    times = [r["elapsed_s"] for r in rows]
    agg = {
        "profile": profile.name, "backend": backend.name, "model": backend.model, "precision": backend.precision,
        "task_file": a.task_file, "setup": a.setup, "n": a.n,
        "success": len(ok), "success_rate": len(ok) / a.n,
        "stop_reasons": dict(Counter(r["stop_reason"] for r in rows)),
        "steps": {"all": steps, "min": min(steps), "median": statistics.median(steps), "max": max(steps),
                  "success_only_median": statistics.median([r["steps"] for r in ok]) if ok else None},
        "elapsed_s": {"median": round(statistics.median(times), 1), "max": round(max(times), 1), "sum": round(sum(times), 1)},
        "jobs": jobs, "backend_concurrency": backend.concurrency, "wall_s": round(wall, 1),
        "parse_failures": sum(r["parse_failures"] for r in rows),
        "tool_errors": sum(r["tool_errors"] for r in rows),
        "repeat_nudges": sum(r["repeat_nudges"] or 0 for r in rows),
        "language_retries": sum(r["language_retries"] or 0 for r in rows),
        "runs": rows,
    }
    (bdir / "bench.json").write_text(json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8")
    cols = ["run", "stop_reason", "success", "steps", "elapsed_s", "tool_calls", "tool_errors", "parse_failures",
            "repeat_nudges", "language_retries", "gen_tok_per_s", "e2e_tok_per_s"]
    md = [f"# bench — {profile.name} × {a.n} ({backend.name} {backend.model} {backend.precision})\n",
          f"- 성공 {len(ok)}/{a.n} ({agg['success_rate']:.0%}), 종료 사유 {agg['stop_reasons']}",
          f"- 단계 min/median/max {agg['steps']['min']}/{agg['steps']['median']}/{agg['steps']['max']}, 모두 {steps}",
          f"- 시간 median {agg['elapsed_s']['median']}초, max {agg['elapsed_s']['max']}초, 합 {agg['elapsed_s']['sum']}초, "
          f"벽시계 {agg['wall_s']}초(동시 {jobs}, 백엔드 상한 {backend.concurrency})",
          f"- 파싱 실패 합 {agg['parse_failures']}, 반복 경고 합 {agg['repeat_nudges']}, 한국어 재요청 합 {agg['language_retries']}\n",
          "| " + " | ".join(cols) + " | 기록 |", "|" + "---|" * (len(cols) + 1)]
    md += ["| " + " | ".join(str(r[c]) for c in cols) + f" | {r['dir']}/transcript.md |" for r in rows]
    (bdir / "bench.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({k: agg[k] for k in ("success", "n", "stop_reasons", "steps", "elapsed_s", "wall_s", "parse_failures")}, ensure_ascii=False))
    print(f"bench: {bdir / 'bench.md'}")
    return 0


def media_bench(a, runs: list[dict]) -> None:
    """media -n N 결과 모으기: 정확·최상위 일치의 평균·범위(전체, 판별 불가 제외)."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = Path(a.runs_dir) / f"media-bench-{stamp}{'-orig' if a.use_original else ''}"
    bdir.mkdir(parents=True)

    def stat(vals):
        return {"mean": round(statistics.mean(vals), 2), "min": min(vals), "max": max(vals), "all": vals}

    agg = {"input": a.input, "use_original": a.use_original, "folders": a.folders, "web_candidates": a.web_candidates,
           "batch": a.batch, "candidates": a.candidates, "n": len(runs), "runs": runs}
    if runs and "all" in runs[0]:
        for scope in ("all", "determinable"):
            agg[scope] = {m: stat([r[scope][m] for r in runs]) for m in ("exact", "top", "review", "wrong")}
            agg[scope]["n"] = runs[0][scope]["n"]
        agg["model_exact"] = stat([(r.get("model") or {}).get("exact", 0) for r in runs])
        agg["cand_hit"] = stat([r["cand_hit"] for r in runs])
    agg["new_folder_lines"] = stat([r["new_folder_lines"] for r in runs])
    agg["model_time_s"] = stat([r["model_time_s"] for r in runs])
    agg["web_requests"] = [r["web_requests"] for r in runs]
    (bdir / "bench.json").write_text(json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [f"# media bench × {len(runs)} — {Path(a.input).name}{' (원본 이름)' if a.use_original else ''}\n"]
    if "all" in agg:
        for scope, title in (("all", "전체"), ("determinable", "판별 불가 제외")):
            g = agg[scope]
            md.append(f"- {title}({g['n']}줄): 정확 평균 {g['exact']['mean']} (범위 {g['exact']['min']}~{g['exact']['max']}), "
                      f"최상위 평균 {g['top']['mean']} ({g['top']['min']}~{g['top']['max']}), "
                      f"_review 평균 {g['review']['mean']}, 틀림 평균 {g['wrong']['mean']}")
        md.append(f"- 모델 정확 {agg['model_exact']['all']}, 후보 적중 {agg['cand_hit']['all']}")
    md.append(f"- new_folder 줄 {agg['new_folder_lines']['all']}, 모델 시간 {agg['model_time_s']['all']}초, 검색 실요청 {agg['web_requests']}")
    md += ["", "| 회차 | 정확 | 최상위 | _review | 틀림 | 기록 |", "|---|---|---|---|---|---|"]
    md += [f"| {i} | {r['all']['exact']} | {r['all']['top']} | {r['all']['review']} | {r['all']['wrong']} | {r['dir']}/result.md |"
           for i, r in enumerate(runs, 1) if "all" in r]
    (bdir / "bench.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md[:4]))
    print(f"bench: {bdir / 'bench.md'}")


def _resume_args(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group()
    g.add_argument("--continue", dest="cont", action="store_true", help="이 작업 폴더의 가장 최근 기록을 이어 간다")
    g.add_argument("--resume", metavar="RUN", help="기록 폴더(경로 또는 runs 폴더 안 이름)를 이어 간다")


def _history(a, workdir: Path | None, chat: bool) -> tuple[list[dict] | None, str | None, dict | None]:
    """(불러온 대화, 기록 폴더, 그 meta). 이어 하기가 아니면 (None, None, None)."""
    from .resume import find_latest, load_messages, resolve_run, run_meta
    if getattr(a, "resume", None):
        d = resolve_run(Path(a.runs_dir), a.resume)
    elif getattr(a, "cont", False):
        d = find_latest(Path(a.runs_dir), workdir or Path.cwd(), chat)
    else:
        return None, None, None
    meta = run_meta(d)
    if meta.get("profile") and meta["profile"] != a.profile:
        print(f"stewart: 이어 하는 기록의 프로필은 {meta['profile']}, 지금은 {a.profile}(지시문은 지금 프로필로 만든다)", file=sys.stderr)
    return load_messages(d), str(d), meta


def main(argv: list[str] | None = None) -> int:
    args0 = list(sys.argv[1:] if argv is None else argv)
    if args0[:1] == ["instance"]:   # stewart instance <이름> start|run|check|profile|dirs — 따로 도는 허브(instance.py)
        from .instance import main as instance_main
        return instance_main(args0[1:])
    ap = argparse.ArgumentParser(prog="stewart")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="프로필로 과제 하나를 실행")
    _common(r)
    _resume_args(r)
    r.add_argument("--plan", action="store_true", help="계획 모드: 읽기 도구만 쓰고 고칠 계획을 최종 답으로 낸다(파일·명령 안 건드림)")
    r.add_argument("--image", action="append", default=None, help="과제와 함께 보낼 이미지 파일(여러 번 가능, vision 백엔드만)")
    b = sub.add_parser("bench", help="같은 과제를 N번 반복해 성공률·단계 분포 기록")
    _common(b)
    b.add_argument("-n", type=int, required=True)
    b.add_argument("--setup", help="회차마다 먼저 돌릴 셸 명령(저장소 루트에서), 예: 연습 저장소 새로 만들기")
    b.add_argument("-j", "--jobs", type=int, default=1,
                   help="동시에 돌릴 회차 수(기본 1). 2 이상이면 --workdir·--setup 에 {i} 필요. 실제 동시 요청은 백엔드 concurrency 이하")
    b.add_argument("--keep-old", type=int, default=3,
                   help="setup 뒤 <작업폴더>.old-* 를 최근 N개만 남김(캐시 루트 아래일 때만, 기본 3)")
    m = sub.add_parser("media", help="미디어 분류 실패 목록: 규칙 전처리 + 애매한 것만 모델(묶음)")
    _common(m, task=False)
    m.add_argument("-i", "--input", required=True, help="파일 이름 목록(한 줄에 하나)")
    m.add_argument("--batch", type=int, default=10)
    m.add_argument("--profile", default="media")
    m.add_argument("--rules-only", action="store_true", help="모델을 부르지 않고 규칙 결과만")
    m.add_argument("--folders", help="서고 폴더 목록 파일(한 줄에 상대 폴더 하나) — 기존 폴더 중에서 고르게 한다")
    m.add_argument("--candidates", type=int, default=15, help="모델에 넘길 파일당 후보 폴더 수(기본 15)")
    m.add_argument("--web-candidates", action="store_true",
                   help="후보가 약한 파일만 웹 검색(SearXNG)해 결과 낱말로 후보를 다시 뽑는다(--folders 필요)")
    m.add_argument("--web-cache", help="검색 결과 캐시 JSONL(같은 검색어를 다시 보내지 않는다)")
    m.add_argument("-n", type=int, default=1, help="같은 입력을 N번 돌려 정확·최상위 일치의 평균·범위를 낸다")
    m.add_argument("--use-original", action="store_true",
                   help="이름에 ' - [원본 이름].ext'가 있으면 대괄호 안(+확장자)만 입력으로 쓴다(실제 inbox 형태)")
    c = sub.add_parser("chat", help="대화형 모드(도구 호출·diff를 보여 주고, Ctrl-C로 끼어든다)")
    c.add_argument("-p", "--profile", default="chat")
    c.add_argument("--workdir", help="작업 폴더(없으면 프로필 값, 프로필에도 없으면 현재 폴더)")
    c.add_argument("--max-steps", type=int, help="차례당 단계 상한(프로필 chat 은 200). 닿으면 edits·ask·plan 은 계속할지 묻고, auto 는 끝내고 안내")
    c.add_argument("--think", action=argparse.BooleanOptionalAction, default=None,
                   help="모델 생각(chat_template_kwargs.enable_thinking)을 켜거나 끈다. 없으면 백엔드 설정(예시 설정은 끔). chat 안에서 /think")
    c.add_argument("--confirm", action=argparse.BooleanOptionalAction, default=None,
                   help="파일 쓰기·셸 실행 전마다 묻는다. --mode 가 없을 때 켜면 ask, 끄면 auto")
    c.add_argument("--stream", action=argparse.BooleanOptionalAction, default=True,
                   help="응답을 조각으로 받아 바로 보여 준다(기본 켜짐). Ctrl-C 가 생성을 바로 끊는다")
    c.add_argument("--mode", choices=["auto", "edits", "ask", "plan"],
                   help="실행 모드(기본 edits): auto 전부 바로, edits 파일 편집은 바로·셸은 묻기, ask 읽기 말고 다 묻기(=--confirm), plan 읽기만 하고 계획")
    c.add_argument("--tui", choices=["auto", "on", "off"], default="auto",
                   help="화면: auto(터미널·폭 60칸 이상이면 입력창 고정 화면, 파이프·좁은 화면·STEWART_TUI=0 이면 지금 화면) | on | off")
    c.add_argument("first", nargs="*", help="첫 입력(없으면 바로 대화)")
    _resume_args(c)
    _common(c, task=False)
    rw = sub.add_parser("rewind", help="실행 기록의 체크포인트로 파일을 되돌린다(도구가 쓴 파일만)")
    rw.add_argument("run", help="기록 폴더(경로 또는 runs 폴더 안 이름)")
    rw.add_argument("--to", type=int, help="이 체크포인트가 시작될 때로(없으면 목록만 보인다)")
    rw.add_argument("--runs-dir", default=os.environ.get("STEWART_RUNS_DIR") or str(DEFAULT_RUNS_DIR))
    pg = sub.add_parser("ping", help="백엔드 연결 확인")
    pg.add_argument("-b", "--backend")
    lt = sub.add_parser("loadtest", help="서빙 속도 측정: 동시 요청별 TTFT·1개당·합계 tok/s")
    lt.add_argument("-b", "--backend")
    lt.add_argument("-c", "--concurrency", default="1,8", help="쉼표로 여러 개(기본 1,8)")
    lt.add_argument("--max-tokens", type=int, default=512)
    lt.add_argument("--rounds", type=int, default=1)
    lt.add_argument("--prompt-tokens", type=int, default=0, help="긴 문맥 시험: 대략 이만큼의 채움 글을 앞에 붙인다")
    lt.add_argument("--ignore-eos", action="store_true", help="max_tokens 까지 끝까지 생성(vLLM 확장)")
    lt.add_argument("--runs-dir", default=os.environ.get("STEWART_RUNS_DIR") or str(DEFAULT_RUNS_DIR))
    ps = sub.add_parser("podstatus", help="파드 감시기 상태(켜진 시간·유휴·GPU 메모리·KV 캐시 줄)를 읽어 기록")
    ps.add_argument("-b", "--backend")
    ps.add_argument("--port", type=int, default=8001)
    ps.add_argument("--runs-dir", default=os.environ.get("STEWART_RUNS_DIR") or str(DEFAULT_RUNS_DIR))
    q = sub.add_parser("queue", help="일괄 작업 대기열 실행")
    q.add_argument("file")
    q.add_argument("-j", "--max-parallel", type=int, default=0, help="전체 동시 작업 상한(0=백엔드 상한만)")
    q.add_argument("--dry-run", action="store_true")
    q.add_argument("--runs-dir", default=os.environ.get("STEWART_RUNS_DIR") or str(DEFAULT_RUNS_DIR))
    cl = sub.add_parser("cloud", help="래퍼(stewart-cloud)가 빌린 인스턴스 기다리기·보기(wait|info). 빌리기·지우기는 래퍼가 한다")
    cl.add_argument("action", choices=["wait", "info"])
    cl.add_argument("-b", "--backend", default="cloud")
    cl.add_argument("--port", type=int, default=8001)
    cl.add_argument("--timeout-min", type=float, default=45)
    cl.add_argument("--runs-dir", default=os.environ.get("STEWART_RUNS_DIR") or str(DEFAULT_RUNS_DIR))
    dr = sub.add_parser("dream", help="기억 꿈꾸기: 메모를 기록과 대조해 대상마다 쪽을 고쳐 쓰고 주입 색인을 제안(사람이 부른다)")
    dr.add_argument("--subject", action="append", help="대상 키(여럿 가능, 없으면 메모가 있는 모든 대상)")
    dr.add_argument("-b", "--backend", help="쪽을 쓸 모델의 백엔드(없으면 기본)")
    dr.add_argument("--dry-run", action="store_true", help="쓰지 않고 바뀔 diff 만")
    dr.add_argument("--yes", action="store_true", help="주입 색인(MEMORY.md)도 바로 바꾼다(없으면 MEMORY.md.proposed)")
    dr.add_argument("--accept", metavar="대상", help="제안된 MEMORY.md 를 들인다(모델을 부르지 않음)")
    dr.add_argument("--runs-dir", default=os.environ.get("STEWART_RUNS_DIR") or str(DEFAULT_RUNS_DIR))
    mm = sub.add_parser("memory", help="기억 세 갈래(방침·프로필·자료): migrate [--dry-run|--revert] — 옛 remember.jsonl 옮기기·되돌리기")
    mm.add_argument("action", choices=["migrate"])
    mm.add_argument("--dry-run", action="store_true", help="쓰지 않고 옮길 표만")
    mm.add_argument("--revert", action="store_true", help="세 갈래 파일을 reverted-<날짜>/ 로 옮기고 옛 파일로 돌아간다")
    og = sub.add_parser("organize", help="폴더 정리 계획 검토·실행·되돌리기(review|apply|undo)", add_help=False)
    og.add_argument("rest", nargs=argparse.REMAINDER)
    sub.add_parser("backends", help="백엔드 목록")
    sub.add_parser("profiles", help="프로필 목록")
    hb = sub.add_parser("hub", help="허브: 메인 세션 + 작업자 세션들을 웹 화면(127.0.0.1) 하나로 본다")
    hb.add_argument("action", nargs="?", choices=["url"], help="url: 떠 있는 허브의 접속 주소·로그인 토큰을 다시 보인다(허브는 띄우지 않음)")
    hb.add_argument("--project", default=None, help="프로젝트 폴더(메인의 작업 폴더, 기본: 지금 폴더)")
    hb.add_argument("--new-token", action="store_true", help="로그인 토큰을 새로 만든다(예전 로그인은 모두 끝남). 기본은 상태 폴더의 것을 다시 쓴다")
    hb.add_argument("-p", "--profile", default="chat", help="메인 프로필(기본 chat)")
    hb.add_argument("-b", "--backend", default=None)
    hb.add_argument("--worker-profile", default=None, help="작업자 기본 프로필(기본: 메인과 같음)")
    hb.add_argument("--new-main", action="store_true", help="메인 대화를 새로 시작(기본: 이 허브의 마지막 메인 대화를 이어 받는다)")
    hb.add_argument("--mode", choices=["auto", "edits", "ask", "plan"], default="edits", help="메인 모드(작업자는 이보다 넓을 수 없다)")
    hb.add_argument("--port", type=int, default=None, help="웹 포트(기본 설정값 또는 8765)")
    hb.add_argument("--config", default=None, help="허브 설정 파일([hub] 표, 기본 ~/.config/stewart/hub.toml)")
    hb.add_argument("--state-dir", default=None, help="허브 폴더(기본 설정값 또는 ~/.local/state/stewart/hub/<프로젝트>)")
    hb.add_argument("--runs-dir", default=os.environ.get("STEWART_RUNS_DIR") or str(DEFAULT_RUNS_DIR))
    ss = sub.add_parser("sessions", help="허브의 지난 세션 정리: list|delete|restore|trash|empty|rename|archive|unarchive")
    ss.add_argument("sessions_action", choices=["list", "delete", "restore", "trash", "empty", "rename", "archive", "unarchive"])
    ss.add_argument("keys", nargs="*", help="작업자 이름·메인 기록 폴더 이름(delete·archive 는 여럿), restore 는 휴지통 id, rename 은 이름 다음 새 제목")
    ss.add_argument("--older-days", type=float, default=None, help="delete: 이보다 오래 손대지 않은 것 모두 / empty: 이보다 오래 전에 지운 것만")
    ss.add_argument("--archived", action="store_true", help="list: 보관한 것을 본다")
    ss.add_argument("--yes", action="store_true", help="empty: 되돌릴 수 없음을 알고 비운다")
    ss.add_argument("--project", default=None)
    ss.add_argument("--state-dir", default=None)
    ss.add_argument("--config", default=None)
    ss.add_argument("--runs-dir", default=os.environ.get("STEWART_RUNS_DIR") or str(DEFAULT_RUNS_DIR))
    hu = sub.add_parser("hub-user", help="계정 모드 허브([hub] accounts = true)의 사용자 더하기·비밀번호 바꾸기·막기·목록")
    hu.add_argument("action", choices=["add", "passwd", "disable", "enable", "list"])
    hu.add_argument("name", nargs="?")
    hu.add_argument("--project", default=None, help="허브를 띄우는 프로젝트 폴더(허브 폴더를 찾는 데만 쓴다)")
    hu.add_argument("--config", default=None)
    hu.add_argument("--state-dir", default=None, help="허브 폴더(허브를 띄울 때와 같은 값)")
    hu.add_argument("--password-stdin", action="store_true", help="비밀번호를 표준 입력 한 줄로(묻지 않음)")
    wk = sub.add_parser("worker", help="(허브가 띄운다) 작업자 세션 하나")
    wk.add_argument("--session-dir", required=True)
    wk.add_argument("--approval-fd", type=int)
    wk.add_argument("--resume")
    a = ap.parse_args(argv)
    from . import osdep
    if osdep.IS_WIN:   # 콘솔 Ctrl-C 가 막혀 있는 소켓 읽기를 깨우지 못하므로 받는 중인 연결을 먼저 끊는다(WIN-4)
        from .backend import abort_streams
        osdep.win_on_ctrl_c(abort_streams)
        if a.cmd != "worker":     # 작업자는 Ctrl-C 를 받지 않는 새 프로세스 그룹 그대로(hub 22번)
            osdep.win_ctrl_c_enable()
    if a.cmd == "worker":
        from . import worker
        return worker.main(["--session-dir", a.session_dir]
                           + (["--approval-fd", str(a.approval_fd)] if a.approval_fd is not None else [])
                           + (["--resume", a.resume] if a.resume else []))

    try:
        if a.cmd == "hub":
            from .hubmain import run_hub
            return run_hub(a)
        if a.cmd == "sessions":
            from .hubmain import run_sessions
            return run_sessions(a)
        if a.cmd == "hub-user":
            from .hubmain import run_hub_user
            return run_hub_user(a)
        if a.cmd == "cloud":
            from . import cloud
            if a.action == "info":
                print(json.dumps(cloud.info(), ensure_ascii=False, indent=1))
                return 0
            backends, _ = load_backends()
            if a.backend not in backends:
                raise ConfigError(f"백엔드가 없다: {a.backend}")
            r = cloud.wait(backends[a.backend], a.port, Path(a.runs_dir), timeout_s=a.timeout_min * 60)
            print(json.dumps(r, ensure_ascii=False, indent=1))
            return 0 if r["ok"] else 1
        if a.cmd == "memory":
            from . import memstore as MS
            from .memory import USER, memory_dir
            d = memory_dir() / USER["key"]
            if a.revert:
                r = MS.revert(d)
                print(f"되돌렸다: {', '.join(r['moved']) or '(옮길 세 갈래 파일 없음)'}" + (f" → {r['to']}" if r["to"] else "")
                      + " — 옛 remember.jsonl 을 다시 쓴다(자동으로 다시 옮기지 않는다)")
                return 0
            if not a.dry_run and MS.Store(d).marker().get("reverted"):
                (d / MS.MARKER).unlink()       # 되돌린 뒤 다시 옮기기: 표지를 지우고 옮긴다
            r = MS.migrate(d, dry_run=a.dry_run)
            if a.dry_run:
                print(r["table"])
            elif r.get("skipped"):
                print(r["skipped"])
            else:
                print(f"옮겼다: 방침 {r['counts']['policy']} · 프로필 {r['counts']['profile']} · 자료 {r['counts']['knowledge']} — 표 {r['table_path']}")
            return 0
        if a.cmd == "dream":
            from . import dream as DR
            from .memory import memory_dir
            if a.accept:
                print(json.dumps(DR.accept(memory_dir(), a.accept), ensure_ascii=False, indent=1))
                return 0
            backends, default = load_backends()
            name = a.backend or default
            if name not in backends:
                raise ConfigError(f"백엔드가 없다: {name}")
            rows = DR.run(a.subject, backends[name], Path(a.runs_dir), dry_run=a.dry_run, yes=a.yes)
            for r in rows:
                print(json.dumps({k: v for k, v in r.items() if k != "diff"}, ensure_ascii=False))
                if a.dry_run and r.get("diff"):
                    print(r["diff"])
            return 0
        if a.cmd == "organize":
            from .organize.cli import main as organize_main
            return organize_main(a.rest)
        if a.cmd == "backends":
            backends, default = load_backends()
            for n, bk in backends.items():
                mark = "*" if n == default else " "
                key = f" 키={bk.api_key_env}({'있음' if bk.api_key() else '없음'})" if bk.api_key_env else ""
                print(f"{mark} {n:6} {'켜짐' if bk.enabled else '꺼짐(자리만)'}  {bk.model} {bk.precision} ctx={bk.max_context} "
                      f"동시={bk.concurrency}{key}  {bk.base_url}")
            return 0
        if a.cmd == "rewind":
            from .checkpoint import Checkpoints
            d = Path(a.run).expanduser()
            if not (d / "events.jsonl").is_file():
                d = Path(a.runs_dir) / a.run
            if not (d / "events.jsonl").is_file():
                raise ConfigError(f"기록 폴더가 아니다: {a.run}")
            meta = json.loads((d / "events.jsonl").read_text(encoding="utf-8").splitlines()[0]).get("meta") or {}
            cps = Checkpoints(d, Path(meta.get("workdir", ".")))
            if a.to is None:
                for c in cps.list():
                    print(f"{c['id']:>3}  파일 {len(c['files'])}개  {c['label'][:70]}")
                    for e in c["files"]:
                        print(f"       {'고침' if e['existed'] else '새로'}  {e['path']}")
                print("(되돌리려면 --to N. 셸 명령이 바꾼 파일은 되돌리지 않는다)")
                return 0
            try:
                rep = cps.rewind(a.to)
            except ValueError as e:
                raise ConfigError(str(e)) from None
            print(json.dumps({k: rep[k] for k in ("to", "restored", "moved_aside", "outside_changes", "skipped", "aside_dir")},
                             ensure_ascii=False, indent=1))
            return 0
        if a.cmd == "profiles":
            names = Counter(n for n, _, _ in list_profiles())
            for n, f, src in list_profiles():
                try:
                    p = load_profile(str(f))
                except ConfigError as e:
                    print(f"{n:16} [{src}] 읽기 오류: {e}")
                    continue
                clash = "  ※ 이름 겹침: 경로로만 쓸 수 있다" if names[n] > 1 else ""
                print(f"{n:16} [{src}] 도구={','.join(p.tools)}  출력={p.output_format}  언어={p.language or '-'}  "
                      f"작업폴더={p.workdir}{clash}")
            print(f"(사용자 프로필 폴더: {user_profiles_dir()} — STEWART_PROFILES_DIR 로 바꿈)")
            return 0
        if a.cmd in ("ping", "loadtest", "podstatus"):
            from . import probe
            backends, default = load_backends()
            bname = a.backend or default
            if bname not in backends:
                raise ConfigError(f"백엔드가 없다: {bname} (있는 것: {', '.join(backends)})")
            bk = backends[bname]
            if bk.api_key_env and not bk.api_key():
                print(f"stewart: 경고 — 키 환경변수 {bk.api_key_env} 가 비어 있다(키 없이 보낸다)", file=sys.stderr)
            if a.cmd == "podstatus":
                r = probe.podstatus(bk, a.port, Path(a.runs_dir))
                print(json.dumps(r, ensure_ascii=False, indent=1))
                sf = (r.get("status") or {}).get("stop_failed")
                if sf:
                    bar = "!" * 72
                    print(f"\n{bar}\n경고: 파드가 스스로 멈추지 못했다 — 과금이 계속된다. Vast Instances 화면에서 Stop(■)을 눌러야 한다.\n"
                          f"  사유 {sf.get('reason')}, 실패 {sf.get('count')}회, 마지막 {sf.get('last_ts')}: {sf.get('last_error')}\n{bar}",
                          file=sys.stderr)
                    return 3
                return 0 if r["ok"] else 1
            if a.cmd == "ping":
                r = probe.ping(bk)
                print(json.dumps(r, ensure_ascii=False, indent=1))
                return 0 if r["ok"] else 1
            levels = [int(x) for x in a.concurrency.split(",") if x.strip()]
            r = probe.loadtest(bk, levels, a.max_tokens, a.rounds, a.prompt_tokens, Path(a.runs_dir), a.ignore_eos)
            print(f"loadtest: {Path(r['dir']) / 'loadtest.md'}")
            return 0
        if a.cmd == "queue":
            from .batch import run_queue
            return run_queue(Path(a.file), Path(a.runs_dir), a.max_parallel, a.dry_run)
        if a.cmd == "chat":
            from .chat import ChatSession, repl
            from .resume import resolve_run, run_meta
            prof_has_wd = "workdir" in load_profile(a.profile).raw
            if a.resume and not a.workdir:   # --resume 은 그 기록의 작업 폴더로 간다
                a.workdir = run_meta(resolve_run(Path(a.runs_dir), a.resume)).get("workdir")
            wd = a.workdir or (None if prof_has_wd else os.getcwd())
            profile, backend = resolve(a.profile, a.backend, wd)
            history, resumed, _ = _history(a, profile.workdir, chat=True)
            if a.max_steps:
                profile.max_steps = a.max_steps
            rec = Recorder(Path(a.runs_dir), "chat-" + profile.name)
            from .tui import pick_ui
            ui = pick_ui(a.tui)   # None = 지금 화면(chat.UI)
            try:
                sess = ChatSession(profile, backend, rec, ui=ui, confirm=a.confirm, history=history, resumed_from=resumed, stream=a.stream, mode=a.mode, default_mode="edits", think=a.think)
                if ui:
                    ui.bind(sess)
                repl(sess, " ".join(a.first) if a.first else None)
            finally:
                if ui:
                    ui.stop()
            return 0
        if a.cmd == "media":
            from .media import run_media
            profile, backend = resolve(a.profile, a.backend)
            from .folders import FolderIndex
            idx = FolderIndex.load(Path(a.folders)) if a.folders else None
            runs = []
            for i in range(a.n):
                if a.n > 1:
                    print(f"stewart: media 반복 {i + 1}/{a.n}", file=sys.stderr)
                runs.append(run_media(profile, backend, Path(a.input), Path(a.runs_dir), a.batch, a.rules_only,
                                      a.use_original, idx, a.candidates, a.web_candidates,
                                      Path(a.web_cache) if a.web_cache else None))
            if a.n > 1:
                media_bench(a, runs)
            return 0
        if a.cmd == "run" and a.resume and not a.workdir:   # --resume 은 그 기록의 작업 폴더로 간다
            from .resume import resolve_run, run_meta
            a.workdir = run_meta(resolve_run(Path(a.runs_dir), a.resume)).get("workdir")
        wd = a.workdir if not (a.workdir and "{i}" in a.workdir) else None  # bench -j 의 회차별 폴더 틀은 회차마다 확인
        if not a.workdir and "workdir" not in load_profile(a.profile).raw:   # 프로필에 작업 폴더가 없으면 지금 폴더(chat 과 같게)
            wd = os.getcwd()
        profile, backend = resolve(a.profile, a.backend, wd, check_workdir=not (a.workdir and "{i}" in a.workdir))
        task = Path(a.task_file).read_text(encoding="utf-8") if a.task_file else a.task
        history, resumed = None, None
        if a.cmd == "run":
            history, resumed, _ = _history(a, profile.workdir, chat=False)
        from .modes import check_mode
        check_mode("plan" if getattr(a, "plan", False) else (profile.mode or "auto"), interactive=False)
    except (ConfigError, OSError, KeyError, ResumeError) as e:
        print(f"stewart: {e}", file=sys.stderr)
        return 2

    try:
        if a.cmd == "bench":
            return cmd_bench(a, profile, backend, task)
        rec = Recorder(Path(a.runs_dir), profile.name)
        print(f"stewart: 기록 {rec.dir}", file=sys.stderr)
        if resumed:
            print(f"stewart: 이어 감 {resumed}", file=sys.stderr)
        imgs = getattr(a, "image", None)
        if imgs and not backend.vision:
            print(f"stewart: 이 백엔드({backend.name})는 이미지를 받지 않는다(backends 표 vision = false)", file=sys.stderr)
            return 2
        s = engine.run(profile, backend, task, rec, a.max_steps, history=history, resumed_from=resumed,
                       mode=getattr(a, "plan", False) and "plan" or None, images=imgs)
    except KeyboardInterrupt:
        print("stewart: 중단됨", file=sys.stderr)
        return 130
    except ValueError as e:   # images.ImageError(형식·크기·비밀 꼴 이름) — 까닭만 찍는다
        from .images import ImageError
        if not isinstance(e, ImageError):
            raise
        print(f"stewart: 이미지를 붙이지 않았다: {e}", file=sys.stderr)
        return 2
    print(json.dumps(_short(s), ensure_ascii=False))
    print(f"transcript: {rec.dir / 'transcript.md'}")
    return 0 if s["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
