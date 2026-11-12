"""실사용 장면 묶음을 진짜 모델로 잰다(판 121): 장면마다 N 번 돌려 "기대대로" 몇 번인지.
실사용에 닿는 판(지시문·도구 설명·넛지·자리)을 바꿀 때 한 번 — 바꾸기 전·뒤를 같은 백엔드로 재서 표를 보고에 붙인다.

  python tools/scenes/measure.py --backend <백엔드 이름> [-n 3] [--only id,id] [--scenes 파일 …] [--out 결과.json]

- 장면마다 새 대화(빈 작업 폴더, 기억 끔)에서 사람 말 하나를 보내고, 첫 걸음 도구·답 글로 기계 판정한다(stewart.scenes.judge).
- 걸음 상한은 3(첫 행동만 본다). 백엔드는 STEWART_BACKENDS(또는 사용자 설정)의 표에서 이름으로 고른다. 모델을 부르니 비용이 든다.
- 웹 도구는 사용자 설정(hub.toml [web])대로 실린다 — 설정이 없으면 웹 장면은 "도구 없음"으로 실패한다(그것도 결과다).
"""

from __future__ import annotations

import argparse
import dataclasses
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from stewart import chat as C, scenes as SC  # noqa: E402
from stewart.config import load_backends, load_profile  # noqa: E402
from stewart.record import Recorder  # noqa: E402


def one(scene: dict, backend, max_steps: int) -> tuple[bool, str, list]:
    tmp = Path(tempfile.mkdtemp(prefix="scene-"))
    p = load_profile(scene["profile"])
    p = dataclasses.replace(p, workdir=tmp, raw=SC.profile_raw(scene, p.raw), max_steps=max_steps)
    s = C.ChatSession(p, backend, Recorder(tmp / "runs", "scene"), C.UI(out=io.StringIO(), color=False), stream=False, mode="auto")
    try:
        s.turn(scene["said"])
    except Exception as e:   # noqa: BLE001 — 백엔드 오류도 결과로
        return False, f"오류 {type(e).__name__}: {e}"[:160], []
    finally:
        s.close()
    ok, why = SC.judge(scene, s.messages)
    calls, _ = SC.first_step(s.messages)
    return ok, why, [c["name"] for c in calls]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", required=True)
    ap.add_argument("-n", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--scenes", nargs="*")
    ap.add_argument("--max-steps", type=int, default=3)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    os.environ.setdefault("STEWART_MEMORY", "0")
    backend = load_backends()[0][a.backend]
    scenes = [s for s in SC.load([Path(x) for x in a.scenes] if a.scenes else None) if not a.only or s["id"] in a.only.split(",")]
    rows = []
    for sc in scenes:
        res = [one(sc, backend, a.max_steps) for _ in range(a.n)]
        good = sum(1 for r in res if r[0])
        rows.append({"id": sc["id"], "said": sc["said"], "good": good, "n": a.n, "runs": [{"ok": r[0], "why": r[1], "first": r[2]} for r in res]})
        print(f"{sc['id']:<34} {good}/{a.n}  " + " | ".join(r[1][:50] for r in res), flush=True)
    total = sum(r["good"] for r in rows), sum(r["n"] for r in rows)
    print(f"합: {total[0]}/{total[1]} · 백엔드 {a.backend}({backend.model}) · {time.strftime('%Y-%m-%d %H:%M')}")
    if a.out:
        Path(a.out).write_text(json.dumps({"backend": a.backend, "model": backend.model, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
