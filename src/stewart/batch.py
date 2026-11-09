"""간단한 일괄 작업 대기열: 쌓아 둔 stewart 명령을 백엔드 동시 상한 안에서 병렬로 돌린다.

파드를 켠 시간에 bench·채점 반복·일괄 프로필을 몰아서 처리하려는 것이다. 정교한 스케줄러가 아니다.

  stewart queue 대기열.toml [-j 전체상한] [--dry-run]

대기열 파일(TOML):
  [[job]]
  name = "code-bench"                 # 기록 이름
  args = ["bench", "-p", "code", ...] # stewart 뒤에 붙일 인자 그대로
  backend = "cloud"                   # 있으면 --backend 로 붙이고, 동시 상한 계산에 쓴다(없으면 기본 백엔드)
  slots = 8                           # 이 작업이 동시에 쓰는 요청 수(없으면 args 의 -j/--jobs 값, 그것도 없으면 1)
  exclusive = "calc"                  # 같은 값을 가진 작업끼리는 겹쳐 돌지 않는다(작업 폴더 공유 등)
  repeat = 1                          # 같은 작업을 몇 번 넣을지

규칙: 백엔드별로 돌고 있는 작업의 slots 합 + 새 작업 slots ≤ 그 백엔드 concurrency 일 때 시작한다.
(slots 가 concurrency 보다 크면 그 백엔드가 비어 있을 때 혼자 돈다.) 순서는 파일 순서를 지키되, 앞 작업이
기다리는 동안 뒤의 다른 백엔드 작업은 먼저 시작할 수 있다. 작업마다 별도 프로세스로 돌린다.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import REPO_ROOT, ConfigError, load_backends


@dataclass
class Job:
    idx: int
    name: str
    args: list[str]
    backend: str
    slots: int
    exclusive: str | None
    proc: subprocess.Popen | None = None
    log: Path | None = None
    started: float | None = None
    ended: float | None = None
    started_at: str = ""
    ended_at: str = ""
    exit: int | None = None
    extra: dict = field(default_factory=dict)


def _slots_from_args(args: list[str]) -> int:
    for k, v in zip(args, args[1:]):
        if k in ("-j", "--jobs"):
            try:
                return max(1, int(v))
            except ValueError:
                pass
    return 1


def load_queue(path: Path) -> list[Job]:
    d = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    backends, default = load_backends()
    jobs: list[Job] = []
    for n, j in enumerate(d.get("job") or [], 1):
        if not isinstance(j.get("args"), list) or not j["args"]:
            raise ConfigError(f"{path}: job {n} 에 args(목록)가 없다")
        args = [str(x) for x in j["args"]]
        flag = next((f for f in ("--backend", "-b") if f in args[1:]), None)
        if flag:
            bname = args[args.index(flag, 1) + 1]
        else:
            bname = j.get("backend") or default
            if j.get("backend"):
                args += ["--backend", bname]
        if bname not in backends:
            raise ConfigError(f"{path}: job {n} 백엔드 '{bname}' 가 없다")
        slots = int(j.get("slots") or _slots_from_args(args))
        for r in range(int(j.get("repeat", 1))):
            name = j.get("name") or args[0]
            jobs.append(Job(idx=len(jobs) + 1, name=name + (f"-{r + 1}" if int(j.get("repeat", 1)) > 1 else ""),
                            args=args, backend=bname, slots=max(1, slots), exclusive=j.get("exclusive")))
    return jobs


def run_queue(path: Path, runs_dir: Path, max_parallel: int = 0, dry_run: bool = False, poll_s: float = 0.5) -> int:
    jobs = load_queue(path)
    backends, _ = load_backends()
    limit = {n: b.concurrency for n, b in backends.items()}
    if dry_run:
        for j in jobs:
            print(f"{j.idx:02d} {j.name:24} 백엔드={j.backend}(상한 {limit[j.backend]}) slots={j.slots} "
                  f"exclusive={j.exclusive or '-'}  stewart {' '.join(j.args)}")
        return 0
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    qdir = Path(runs_dir) / f"queue-{stamp}-{Path(path).stem}"
    qdir.mkdir(parents=True)
    print(f"stewart: 대기열 {len(jobs)}개, 기록 {qdir}", file=sys.stderr)
    pending, running, done = list(jobs), [], []
    t0 = time.monotonic()
    started_at = datetime.now().isoformat(timespec="seconds")

    def can_start(j: Job) -> bool:
        if max_parallel and len(running) >= max_parallel:
            return False
        if j.exclusive and any(r.exclusive == j.exclusive for r in running):
            return False
        used = sum(r.slots for r in running if r.backend == j.backend)
        return used == 0 or used + j.slots <= limit[j.backend]

    try:
        while pending or running:
            for j in list(pending):
                if can_start(j):
                    pending.remove(j)
                    j.log = qdir / f"{j.idx:02d}-{''.join(c if c.isalnum() or c in '-_' else '_' for c in j.name)}.log"
                    fh = j.log.open("w", encoding="utf-8")
                    fh.write(f"# stewart {' '.join(j.args)}\n")
                    fh.flush()
                    j.proc = subprocess.Popen([sys.executable, "-m", "stewart.cli", *j.args], cwd=REPO_ROOT,
                                              stdout=fh, stderr=subprocess.STDOUT, text=True)
                    j.extra["fh"] = fh
                    j.started, j.started_at = time.monotonic(), datetime.now().isoformat(timespec="seconds")
                    running.append(j)
                    print(f"stewart: 시작 {j.idx:02d} {j.name} ({j.backend}, slots {j.slots})", file=sys.stderr)
            for j in list(running):
                if j.proc.poll() is not None:
                    j.exit, j.ended = j.proc.returncode, time.monotonic()
                    j.ended_at = datetime.now().isoformat(timespec="seconds")
                    j.extra.pop("fh").close()
                    running.remove(j)
                    done.append(j)
                    print(f"stewart: 끝 {j.idx:02d} {j.name} exit={j.exit} {j.ended - j.started:.0f}초", file=sys.stderr)
            time.sleep(poll_s)
    except KeyboardInterrupt:
        print("stewart: 대기열 중단 — 돌던 작업을 끝낸다", file=sys.stderr)
        for j in running:
            j.proc.terminate()
        for j in running:
            try:
                j.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                j.proc.kill()
            j.exit, j.ended, j.ended_at = j.proc.returncode, time.monotonic(), datetime.now().isoformat(timespec="seconds")
            j.extra.pop("fh").close()
            done.append(j)
        running = []
    wall = time.monotonic() - t0

    def pointers(log: Path) -> list[str]:
        try:
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        return [l for l in lines if l.startswith(("bench:", "transcript:", "loadtest:"))][-3:]

    rows = []
    for j in sorted(done, key=lambda x: x.idx):
        rows.append({"idx": j.idx, "name": j.name, "backend": j.backend, "slots": j.slots, "exclusive": j.exclusive,
                     "args": j.args, "exit": j.exit, "started": j.started_at, "ended": j.ended_at,
                     "wall_s": round(j.ended - j.started, 1) if j.ended and j.started else None,
                     "log": j.log.name if j.log else None, "results": pointers(j.log) if j.log else []})
    agg = {"queue": str(path), "started": started_at, "finished": datetime.now().isoformat(timespec="seconds"),
           "wall_s": round(wall, 1), "jobs": len(jobs), "ok": sum(1 for r in rows if r["exit"] == 0),
           "not_started": [j.name for j in pending], "limits": {b: limit[b] for b in {j.backend for j in jobs}},
           "rows": rows}
    (qdir / "queue.json").write_text(json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [f"# queue — {Path(path).name}\n",
          f"- 시작 {agg['started']}, 끝 {agg['finished']}, 벽시계 {agg['wall_s']}초 (백엔드 상한 {agg['limits']})",
          f"- 성공 {agg['ok']}/{agg['jobs']}" + (f", 시작 못 함 {agg['not_started']}" if pending else "") + "\n",
          "| # | 작업 | 백엔드 | slots | exit | 시작 | 초 | 결과 |", "|---|---|---|---|---|---|---|---|"]
    md += [f"| {r['idx']} | {r['name']} | {r['backend']} | {r['slots']} | {r['exit']} | {r['started'][11:]} | {r['wall_s']} | "
           f"{'<br>'.join(r['results']) or r['log']} |" for r in rows]
    (qdir / "queue.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"queue: {qdir / 'queue.md'}")
    return 0 if agg["ok"] == agg["jobs"] else 1
