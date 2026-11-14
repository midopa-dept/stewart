"""실사용 장면 묶음을 진짜 모델로 잰다(판 121·133): 장면마다 N 번 돌려 첫 걸음이 기대대로인 횟수.
실사용에 닿는 판(지시문·도구 설명·넛지·자리)을 바꿀 때 — 바꾸기 전·뒤를 같은 모델로 재서 표를 보고에 붙인다.

  # backends 표의 이름으로
  python tools/scenes/measure.py --backend <이름>[,<이름>…] -n 8 --out r.json --table r.md
  # 표 없이 서버 주소만으로(차선마다 하나, 쉼표로) — 한 줄
  python tools/scenes/measure.py --lanes http://<서버>:<포트>/v1,http://<서버>:<포트>/v1 --model <서빙 모델 이름> -n 8 --out r.json --table r.md

- 장면마다 새 대화(빈 작업 폴더, 기억 끔)에서 사람 말 하나를 보내고, 첫 걸음 도구·답 글로 기계 판정한다(stewart.scenes.judge).
- 도구는 실행하지 않는다(기본). 첫 걸음의 도구 이름만 보면 되므로, 모델이 부른 도구에는 "측정 중 — 실행하지 않았다"를 돌려준다.
  그래서 웹 검색 서버도 필요 없고, 셸 명령도 돌지 않아 바깥에 닿는 일이 없다. 웹 도구를 목록에 싣기 위해 검색 주소가 없으면 닿지 않는 자리 주소를 넣는다.
  --run-tools 를 주면 예전처럼 도구를 실제로 돌린다(그때는 검색 주소를 STEWART_SEARXNG_URL 로 준다).
- 걸음 상한 1(첫 걸음만 본다). 차선(--lanes 나 --backend 여럿)마다 일꾼 --per-lane 개가 일을 나눠 든다.
- 장면 갈래: real = 실사용에서 겪은 말, dev = 개발 장면. 표 머리에 "개발 장면은 규칙을 고친 뒤 같은 장면으로 나아졌다고 말하지 않는다"를 적는다.
- 기본 조건: 사용자 기억 파일(STEWART.md)이 든 임시 사용자 폴더(stewart.scenes.USER_MEMORY). --no-user-memory 로 끈다.
  스킬·프로필 폴더는 늘 빈 시험 자리다(판 137) — 실행하는 사람의 실제 스킬·프로필이 실리지 않는다.
- 판 141
  - 허브 꼴: 허브가 메인에 붙이는 역할 글(hubmain.with_role)을 붙여 돈다(기본). --no-hub-role 로 보통 chat 꼴.
  - 장면의 max_steps·run_tools: 그 장면만 걸음 상한을 바꾸고, run_tools 의 읽기 도구는 실제로 돌린다(빈 작업 폴더).
  - 판정의 "보류"(첫 걸음이 폴더만 → 둘째 걸음까지 웹)는 표에 따로 센다.
  - --user-shape: 실사용 꼴 설정(tests/test_realuse_shape.make_user_shape — 기억 파일·사용자 스킬·hub.toml·사용자 프로필)으로
    임시 HOME 을 만들어 돈다. 코드 자리 장면(profile chat)은 그 사용자 프로필로 연다. 이때는 스킬·프로필 빈 시험 자리 대신 그 꼴을 쓴다.
- 모델을 부르니 비용·장비 시간이 든다.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import io
import json
import os
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from stewart import chat as C, scenes as SC  # noqa: E402
from stewart.config import Backend, load_backends, load_profile  # noqa: E402
from stewart.record import Recorder  # noqa: E402

DRY_RESULT = "[측정] 측정 중이라 이 도구를 실행하지 않았다."
DEV_HEAD = ("개발 장면(set = dev)은 결과를 보고 지시문·규칙을 고쳤으면, 같은 장면으로 \"나아졌다\"를 말하지 않는다 — "
            "나아졌는지는 고친 뒤 새로 만든 장면으로 잰다.")


def dry_exec(session):
    """도구를 실행하지 않는 _exec(첫 걸음 도구 이름만 본다)."""
    def ex(rc: dict, step: int) -> dict:
        fn = rc.get("function") or {}
        raw = fn.get("arguments") or "{}"
        session.st["tool_calls"] += 1
        return {"id": rc.get("id"), "name": fn.get("name", ""), "raw_arguments": raw if isinstance(raw, str) else json.dumps(raw),
                "ok": True, "result": DRY_RESULT, "elapsed_s": 0.0}
    return ex


PROFILE_MAP: dict = {}   # 판 141: --user-shape 면 {"chat": <사용자 프로필>}


def one(scene: dict, backend, max_steps: int, run_tools: bool, hub_role: bool = True) -> tuple[bool, str, list]:
    tmp = Path(tempfile.mkdtemp(prefix="scene-"))
    p = load_profile(PROFILE_MAP.get(scene["profile"], scene["profile"]))
    p = dataclasses.replace(p, workdir=tmp, raw=SC.profile_raw(scene, p.raw), max_steps=int(scene.get("max_steps") or max_steps))
    if hub_role:   # 판 141: 허브가 메인에 붙이는 역할 글(사용자는 허브로 쓴다). parlando 처럼 hub_role = false 인 프로필은 그대로
        from stewart.hubmain import with_role
        p = with_role(p)
    s = C.ChatSession(p, backend, Recorder(tmp / "runs", "scene"), C.UI(out=io.StringIO(), color=False), stream=False, mode="auto")
    if not run_tools:
        real, dry, live = s._exec, dry_exec(s), set(scene.get("run_tools") or [])
        s._exec = (lambda rc, step: real(rc, step) if (rc.get("function") or {}).get("name") in live else dry(rc, step)) if live else dry
    miss = [t for t in scene.get("requires") or [] if t not in (getattr(s, "all_tool_names", None) or s.tool_names or [])]
    if miss:   # 이 판본·설정에 그 도구가 없다 — 잴 것이 없다(맞음·틀림에 넣지 않는다)
        s.close()
        return None, f"도구 없음: {', '.join(miss)}", []
    try:
        s.turn(scene["said"])
    except Exception as e:   # noqa: BLE001 — 백엔드 오류도 결과로
        return False, f"오류 {type(e).__name__}: {e}"[:160], []
    finally:
        s.close()
    ok, why = SC.judge(scene, s.messages)
    calls, _ = SC.first_step(s.messages)
    return ok, why, [c["name"] for c in calls]


def lane_backends(a) -> list:
    if a.lanes:
        params = {"temperature": a.temperature}
        if a.think != "default":
            params["chat_template_kwargs"] = {"enable_thinking": a.think == "on"}
        if a.max_tokens:
            params["max_tokens"] = a.max_tokens
        return [Backend(f"lane{i}", u.strip(), a.model, None, a.max_context, params=params, timeout_s=a.timeout)
                for i, u in enumerate(a.lanes.split(",")) if u.strip()]
    table = load_backends()[0]
    return [table[n.strip()] for n in a.backend.split(",") if n.strip()]


def table_md(res: dict) -> str:
    """결과 표: 장면 × 첫 걸음 도구 × N 번, 맞음 수."""
    lines = [f"# 장면 실측 — {res['model']} · {res['when']} · 차선 {len(res['lanes'])} · 장면마다 {res['n']}번 · 걸음 상한 {res['max_steps']}"
             f"{' · 허브 꼴' if res.get('hub_role', True) else ' · 보통 chat 꼴'}{' · 실사용 꼴 설정' if res.get('user_shape') else ''}"
             f" · 자리 줄 {'켬' if res.get('place_hints', True) else '끔'}"
             f"{' · 도구 실행 안 함' if not res['run_tools'] else ''}{' · 사용자 기억 파일 있음' if res.get('user_memory') else ''}"
             f"{' · 스킬·프로필은 실사용 꼴 설정의 것' if res.get('user_shape') else ' · 스킬·프로필 폴더는 빈 시험 자리'}", "",
             f"> {DEV_HEAD}", "",
             "| 갈래 | 자리 | 장면 | 사람 말 | 맞음 | 보류 | 첫 걸음 도구(횟수) |", "|---|---|---|---|---|---|---|"]
    for r in res["rows"]:
        dist = collections.Counter("+".join(x["first"]) or "도구 없음" for x in r["runs"])
        tools = " · ".join(f"{k} {v}" for k, v in dist.most_common())
        if r["skipped"]:
            tools = f"건너뜀 {r['skipped']} — {next(x['why'] for x in r['runs'] if x['ok'] is None)}"
        held = sum(1 for x in r["runs"] if str(x.get("why") or "").startswith("보류"))
        lines.append(f"| {r['set']} | {r.get('place', '')} | {r['id']} | {r['said']} | {r['good']}/{r['n'] - r['skipped']} | {held or ''} | {tools} |")
    for s in ("real", "dev", "heldout"):
        g = [r for r in res["rows"] if r["set"] == s]
        if g:
            lines.append(f"- {s} 합: {sum(r['good'] for r in g)}/{sum(r['n'] - r['skipped'] for r in g)}")
    errs = [x["why"] for r in res["rows"] for x in r["runs"] if x["why"].startswith("오류")]
    if errs:
        lines.append(f"- 오류 {len(errs)}번(맞음에 넣지 않음): {errs[0]}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--backend", help="backends 표의 이름(쉼표로 여럿 = 차선 여럿)")
    g.add_argument("--lanes", help="서버 주소(…/v1)를 쉼표로 — 표 없이")
    ap.add_argument("--model", default="", help="--lanes 일 때 서빙 모델 이름")
    ap.add_argument("--think", choices=("on", "off", "default"), default="on", help="--lanes 일 때 생각 켬·끔(대화 틀의 enable_thinking)")
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--max-tokens", type=int, default=0)
    ap.add_argument("--max-context", type=int, default=65536)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("-n", type=int, default=3)
    ap.add_argument("--per-lane", type=int, default=2, help="차선 하나에 동시에 도는 대화 수")
    ap.add_argument("--only", default="", help="장면 id 를 쉼표로")
    ap.add_argument("--set", default="", help="real 또는 dev 만")
    ap.add_argument("--scenes", nargs="*")
    ap.add_argument("--max-steps", type=int, default=1)
    ap.add_argument("--run-tools", action="store_true", help="도구를 실제로 돌린다(기본은 실행하지 않음)")
    ap.add_argument("--no-user-memory", action="store_true", help="사용자 기억 파일 없이(기본은 임시 사용자 폴더에 기억 파일을 둔다)")
    ap.add_argument("--no-hub-role", action="store_true", help="허브 역할 글 없이 보통 chat 꼴로(기본은 허브 꼴)")
    ap.add_argument("--user-shape", action="store_true", help="실사용 꼴 설정(임시 HOME)으로 — 코드 자리는 그 사용자 프로필로")
    ap.add_argument("--out", default="")
    ap.add_argument("--table", default="")
    a = ap.parse_args()
    if a.lanes and not a.model:
        ap.error("--lanes 에는 --model 이 필요하다")
    os.environ.setdefault("STEWART_MEMORY", "0")
    if a.user_shape:   # 판 141: 실사용 꼴 설정 — 임시 HOME, XDG·STEWART 자리는 비움(그 HOME 의 기본 자리를 쓴다)
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from tests.test_realuse_shape import SHAPE, make_user_shape
        shape_env = make_user_shape(Path(tempfile.mkdtemp(prefix="scene-shape-")), "http://127.0.0.1:9")
        for k in [k for k in os.environ if k.startswith("XDG_") or (k.startswith("STEWART_") and k not in ("STEWART_MEMORY",))]:
            os.environ.pop(k)
        os.environ["HOME"] = os.environ["USERPROFILE"] = shape_env["HOME"]   # Windows 의 ~ 는 USERPROFILE
        PROFILE_MAP["chat"] = SHAPE["profile"]
    elif not a.no_user_memory:   # 판 134: 기본 조건 — 사용자 기억 파일이 있는 사용자 폴더(실사용 허브처럼)
        os.environ.update(SC.scene_env())   # 판 137: 스킬·프로필 폴더도 시험 자리(빈 폴더) — 실제 사용자 설정이 실리지 않게
    else:
        os.environ.update({k: v for k, v in SC.scene_env().items() if k != "STEWART_USER_DIR"})
    if not a.run_tools:   # 웹 도구를 목록에 싣기만 한다(실행하지 않으므로 닿지 않는 자리)
        os.environ.setdefault("STEWART_SEARXNG_URL", "http://127.0.0.1:9/search")
        os.environ.setdefault("STEWART_WEB_FETCH", "1")
    lanes = lane_backends(a)
    scenes = [s for s in SC.load([Path(x) for x in a.scenes] if a.scenes else None)
              if (not a.only or s["id"] in a.only.split(",")) and (not a.set or s["set"] == a.set)]
    jobs: queue.Queue = queue.Queue()
    for si, sc in enumerate(scenes):
        for k in range(a.n):
            jobs.put((si, k))
    got: dict = {}
    lock = threading.Lock()

    def worker(b):
        while True:
            try:
                si, k = jobs.get_nowait()
            except queue.Empty:
                return
            r = one(scenes[si], b, a.max_steps, a.run_tools, hub_role=not a.no_hub_role)
            with lock:
                got[(si, k)] = r
                mark = "건너뜀" if r[0] is None else "맞음" if r[0] else "틀림"
                print(f"{scenes[si]['id']:<32} {k + 1}번째 {b.name:<8} {mark}  {r[1][:70]}", flush=True)
    ths = [threading.Thread(target=worker, args=(b,), daemon=True) for b in lanes for _ in range(max(1, a.per_lane))]
    t0 = time.time()
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    rows = []
    for si, sc in enumerate(scenes):
        res = [got[(si, k)] for k in range(a.n)]
        rows.append({"id": sc["id"], "set": sc["set"], "said": sc["said"], "good": sum(1 for r in res if r[0]), "n": a.n,
                     "place": "parlando" if sc["profile"] == "talk" else "코드",
                     "skipped": sum(1 for r in res if r[0] is None),
                     "runs": [{"ok": r[0], "why": r[1], "first": r[2]} for r in res]})
    out = {"model": lanes[0].model if lanes else "", "user_memory": not a.no_user_memory or a.user_shape,
           "hub_role": not a.no_hub_role, "user_shape": a.user_shape, "place_hints": C.place_hints_on(), "lanes": [b.base_url for b in lanes], "n": a.n, "max_steps": a.max_steps,
           "run_tools": a.run_tools, "think": a.think if a.lanes else "table", "when": time.strftime("%Y-%m-%d %H:%M"),
           "elapsed_s": round(time.time() - t0, 1), "dev_head": DEV_HEAD, "rows": rows}
    md = table_md(out)
    print("\n" + md)
    if a.out:
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.table:
        Path(a.table).write_text(md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
