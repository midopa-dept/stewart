"""teacher 돌리기(판 95, SW-41 ②): 공개 과제 풀을 더 센 백엔드(teacher)로 stewart 실행기 그대로 돌리고, 시험이 통과한 궤적만 학습 자료 후보로.

과제 풀(TOML):
    name = "묶음 이름"
    [[task]]
    id = "calc-fix"                    # 영문·숫자·-·_
    prompt = "calc.py 의 나눗셈 버그를 고쳐라"
    start = "tasks/calc"               # 시작 폴더(풀 파일 기준) — 회차마다 새로 복사한다
    verify = "python3 -m pytest -q"    # 끝난 뒤 그 폴더에서 돌려 exit 0 이면 통과
    max_steps = 30                     # (선택)

돌리기 `stewart train teacher --pool 풀.toml -b <백엔드> [-p 프로필] [--repeat N] [--max-cost USD] [--max-runs N] [--out 폴더] [--yes] [--dry-run]`:
  회차마다 시작 폴더를 복사 → engine.run(프로필의 verify_command 를 과제 verify 로) → 기록에 test_result 사건(통과·과제 id)을 덧붙인다.
  비용(가격표 있는 백엔드)·모델 호출(걸음) 수를 회차마다 찍고, 합이 --max-cost 에 닿으면 멈춘다.
  바깥 API 백엔드면 "과제 풀 내용이 <제공자> 로 나간다 — 공개 과제만" 을 알리고 --yes 없이는 묻는다(사용자 코드·대화는 보내지 않는다).
뒤: `stewart train export --runs-dir <그 폴더> --select passed --track teacher` 로 통과 궤적만 내보낸다(약관 등급 표가 그대로 걸린다).
"""

from __future__ import annotations

import json
import re
import shutil
import time
import tomllib
from datetime import datetime
from pathlib import Path

ID_RX = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class PoolError(ValueError):
    pass


def load_pool(path: Path) -> dict:
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise PoolError(f"과제 풀을 읽지 못했다: {path} ({e})") from None
    tasks = []
    for i, t in enumerate(data.get("task") or [], 1):
        tid, prompt, start, verify = str(t.get("id") or ""), str(t.get("prompt") or "").strip(), t.get("start"), t.get("verify")
        if not ID_RX.match(tid) or not prompt or not start or not verify:
            raise PoolError(f"{path}: task {i} 에 id(영문·숫자·-·_)·prompt·start·verify 가 다 있어야 한다")
        sd = (path.parent / str(start)).resolve()
        if not sd.is_dir():
            raise PoolError(f"{path}: task {tid} 의 시작 폴더가 없다: {sd}")
        tasks.append({"id": tid, "prompt": prompt, "start": sd, "verify": str(verify), "max_steps": int(t.get("max_steps") or 0) or None})
    if not tasks:
        raise PoolError(f"{path}: [[task]] 가 없다")
    if len({t["id"] for t in tasks}) != len(tasks):
        raise PoolError(f"{path}: 같은 id 가 둘 있다")
    return {"name": str(data.get("name") or path.stem), "tasks": tasks}


def append_event(run_dir: Path, kind: str, **data) -> None:
    """닫힌 기록에 사건 한 줄을 덧붙인다(engine.run 이 events.jsonl 을 닫은 뒤)."""
    with open(Path(run_dir) / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "kind": kind, **data}, ensure_ascii=False) + "\n")


def run_pool(pool: dict, profile, backend, out: Path, repeat: int = 1, max_cost: float | None = None, max_runs: int | None = None,
             runner=None, say=print) -> dict:
    """회차들을 차례로. runner(profile, backend, task, rec, max_steps) 는 시험용(기본 engine.run). 요약 dict."""
    from copy import deepcopy
    from .record import Recorder
    if runner is None:
        from .engine import run as runner
    out = Path(out)
    (out / "work").mkdir(parents=True, exist_ok=True)
    rows, cost, n = [], 0.0, 0
    stop_why = ""
    for t in pool["tasks"]:
        for k in range(max(1, repeat)):
            if max_runs is not None and n >= max_runs:
                stop_why = f"회차 상한 {max_runs}"
                break
            if max_cost is not None and cost >= max_cost:
                stop_why = f"비용 상한 ${max_cost}"
                break
            n += 1
            wd = out / "work" / f"{t['id']}-{k + 1}"
            if wd.exists():
                shutil.rmtree(wd)
            shutil.copytree(t["start"], wd)
            pf = deepcopy(profile)
            pf.workdir = wd
            pf.verify_command = t["verify"]
            rec = Recorder(out, f"teacher-{t['id']}")
            t0 = time.monotonic()
            try:
                s = runner(pf, backend, t["prompt"], rec, t["max_steps"])
                err = ""
            except Exception as e:  # noqa: BLE001 — 한 회차 실패가 묶음을 멈추지 않게
                s, err = {}, f"{type(e).__name__}: {str(e)[:200]}"
            v = s.get("verify") or {}
            passed = bool(v.get("passed")) and s.get("stop_reason") == "completed"
            append_event(rec.dir, "test_result", passed=passed, task=t["id"], attempt=k + 1, pool=pool["name"],
                         **({"error": err} if err else {}))
            c = float(s.get("cost_usd") or 0)
            cost += c
            rows.append({"task": t["id"], "attempt": k + 1, "passed": passed, "stop": s.get("stop_reason"), "steps": s.get("steps"),
                         "calls": s.get("tool_calls"), "cost_usd": round(c, 4), "sec": round(time.monotonic() - t0, 1),
                         "run": rec.dir.name, "error": err})
            say(f"  {t['id']} #{k + 1}: {'통과' if passed else '실패'} · 걸음 {s.get('steps')} · 도구 {s.get('tool_calls')} · "
                f"${c:.4f} (합 ${cost:.4f}) · {rows[-1]['sec']}초" + (f" · {err}" if err else ""))
        if stop_why:
            break
    summ = {"pool": pool["name"], "backend": backend.name, "model": backend.model, "runs": len(rows),
            "passed": sum(r["passed"] for r in rows), "cost_usd": round(cost, 4), "model_steps": sum(r["steps"] or 0 for r in rows),
            "stopped": stop_why, "rows": rows}
    (out / "teacher-summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    return summ
