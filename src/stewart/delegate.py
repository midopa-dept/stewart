"""서브에이전트: delegate 도구로 새 문맥의 작업자에게 일을 맡기고 결과만 받는다.

- 작업자 = engine.run 한 번. 대화 기록은 나누지 않는다(작업자는 맡긴 글만 받는다).
- 나누는 것: 작업 폴더, 백엔드(slot 동시 상한), 모드(작업자는 부모보다 넓지 않다), 확인 질문(chat 은 같은 화면에 "[작업자 N]"),
  체크포인트(작업자의 쓰기도 부모의 지금 체크포인트에 들어가 /undo·stewart rewind 가 덮는다), 행동 검사·비밀 파일 규칙,
  웹 검색 상한·가져오기 기록(부모와 같은 수를 쓴다).
- 도구 범위 = 부모 도구 ∩ kind 프로필 도구 ∩ tools 인자. delegate·ask_user 는 작업자에게 없다(깊이 1). plan 이면 읽기 도구만.
- 처음 판은 한 번에 하나(한 응답의 여러 delegate 도 차례로). 4B(llama-serve --parallel 1)도 같다.
- 상한: 작업자 max_steps = kind 프로필 값(없으면 30), 인자로 줄일 수만 있다. 부모 하나(run 하나, chat 차례 하나)에서
  작업자 단계 합 ≤ 부모 max_steps × 2. 돌려주는 글 8,000자.
- 돌려주는 것: 작업자 최종 글 + 사실 한 줄(종료 사유·단계·바꾼 파일. 기록 폴더 경로는 넣지 않는다). 부모는 바뀐 파일을 다시 읽어야 쓸 수 있다
  (write_file 의 읽지 않은 파일 덮어쓰기 거부 그대로).
- 기록: 작업자 기록은 부모 기록 폴더/sub/<시각>-<N>-<kind>/. 부모 events delegate_start·delegate_end, summary delegations.
- 도구 정의는 tools.py 가 아니라 여기 둔다(clarify.py 와 같다). 프로필 tools 가 ["*"] 이거나 "delegate" 를 적었을 때만 연다.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from . import modes as M
from .config import ConfigError, load_profile

NAME = "delegate"
WRITE_TOOLS = frozenset({"write_file", "edit_file", "multi_edit", "notebook_edit"})
NO_CHILD = frozenset({NAME, "ask_user"})
DEFAULT_STEPS = 30
BUDGET_FACTOR = 2
MAX_RETURN = 8000
MIN_STEPS = 3   # 인자 max_steps 로 줄일 수 있는 하한(작은 모델 시험에서 본 것: 검증자에 1단계를 줘 시험 결과도 판정도 없이 끝남)
# 작업자 지시문 머리(작은 모델 시험에서 본 것: 부모 code 지시문만 받은 작업자가 값 대신 코드를 써서 돌려줬다)
WORKER_NOTE = ("너는 부모 에이전트가 맡긴 일 하나만 하는 작업자다. 부모는 너의 마지막 글만 본다. "
               "맡은 일 밖의 일(다른 파일 고치기, 코드 새로 쓰기 등)은 맡은 글에 적힌 경우에만 한다. "
               "다 하면 도구 호출 없이, 찾은 값·한 일·바꾼 파일을 사실 그대로 짧게 적어 끝낸다.")

# 맡기기 넛지(판 97, SW-43 ①): 모델이 스스로 delegate 를 고르는 일이 거의 없어서(평가 code 기록 9,171 중 0), 한 차례에 읽기를
# 많이 할 때 한 번 "맡기면 문맥이 덜 찬다"를 끼운다. chat·허브만(엔진 평가 길은 쓰지 않는다). STEWART_DELEGATE_NUDGE=0 이면 끔.
READ_TOOLS = frozenset({"read_file", "search", "glob", "list_dir", "doc_search", "peek", "scan_read", "folder_scan"})
NUDGE_READS = 8
NUDGE_PER_SESSION = 3
NUDGE_NOTE = ("\n\n[엔진] 이 차례에 읽기 도구를 {n}번 불렀다. 남은 조사가 넓으면 delegate(task=…)로 맡기고 결과 글만 받아라 — "
              "이 대화 문맥이 덜 찬다(작업자는 이 대화를 못 보니 task 에 목표·범위·돌려받을 것을 다 적는다). 곧 끝날 일이면 그대로 한다.")


def nudge_on(env=None) -> bool:
    import os
    return (os.environ if env is None else env).get("STEWART_DELEGATE_NUDGE", "1") != "0"


class Nudge:
    def __init__(self, on: bool = True):
        self.on, self.turn_done, self.session_n = on, False, 0

    def reset_turn(self) -> None:
        self.turn_done = False

    def on_step(self, calls: list[dict]) -> str:
        """이 차례 호출들(_turn_calls) → 끼울 글(없으면 "")."""
        if not self.on or self.turn_done or self.session_n >= NUDGE_PER_SESSION:
            return ""
        if any(c.get("name") == NAME for c in calls):
            return ""
        n = sum(1 for c in calls if c.get("name") in READ_TOOLS)
        if n < NUDGE_READS:
            return ""
        self.turn_done = True
        self.session_n += 1
        return NUDGE_NOTE.format(n=n)


# 엔진(평가) 길의 설명은 판 97 전 그대로 둔다(평가 조건을 바꾸지 않게). chat·허브는 CHAT_DEFINITION(언제 맡기는지 적힘)을 쓴다.
DESC_SHORT = "새 문맥의 작업자에게 일 하나를 맡기고 결과 글만 받는다. 작업자는 이 대화를 못 보니 task 에 다 적는다. 짧은 일은 직접 한다."


DEFINITION = {"type": "function", "function": {
    "name": NAME,
    "description": DESC_SHORT,
    "parameters": {"type": "object", "properties": {
        "task": {"type": "string", "description": "작업자에게 줄 일(목표·범위·돌려받을 것)"},
        "kind": {"type": "string", "description": "작업자 프로필 이름(없으면 지금 프로필에서 도구만 줄여 쓴다)"},
        "tools": {"type": "array", "items": {"type": "string"}, "description": "작업자에게 줄 도구(지금 도구 안에서만, 없으면 전부)"},
        "max_steps": {"type": "integer", "description": "작업자 단계 상한(줄이기만 된다)"},
    }, "required": ["task"]},
}}


# chat·허브용(U2-2 3b): 도구를 적지 않으면 읽기 도구만 준다(조사·탐색, 승인 없음). run·평가 경로는 위 DEFINITION 그대로
CHAT_DEFINITION = {"type": "function", "function": {
    "name": NAME,
    "description": ("새 문맥의 작업자에게 조사·탐색 하나를 맡기고 결과 글만 받는다(넓게 읽어야 해서 이 대화를 무겁게 할 일: 코드 찾기·"
                    "구조 파악·여러 파일 대조). tools 를 적지 않으면 읽기 도구만 받는다(승인 없음). 파일을 바꾸게 하려면 tools 를 적는다. "
                    "작업자는 이 대화를 못 보니 task 에 목표·범위·돌려받을 것을 다 적는다. 파일 한두 개 보기는 직접 한다."),
    "parameters": {"type": "object", "properties": {
        "task": {"type": "string", "description": "작업자에게 줄 일(목표·범위·돌려받을 것)"},
        "kind": {"type": "string", "description": "작업자 프로필 이름(없으면 지금 프로필에서 도구만 줄여 쓴다)"},
        "tools": {"type": "array", "items": {"type": "string"},
                  "description": "작업자에게 줄 도구(지금 도구 안에서만). 없으면 읽기 도구만"},
        "max_steps": {"type": "integer", "description": "작업자 단계 상한(줄이기만 된다)"},
    }, "required": ["task"]},
}}


def wanted(tools: list[str], depth: int) -> bool:
    return depth == 0 and (tools == ["*"] or NAME in tools)


def other_tools(tools: list[str]) -> list[str]:
    return [t for t in tools if t != NAME]


class Budget:
    """부모 하나(run 하나, chat 차례 하나)의 작업자 단계 합."""

    def __init__(self, parent_max_steps: int, limit: int | None = None):
        self.limit = limit or parent_max_steps * BUDGET_FACTOR
        self.used = 0
        self.n = 0


def child_profile(parent, parent_tools: list[str], exec_mode: str, args: dict, budget: Budget, pfl=None,
                  read_default: bool = False):
    """(작업자 프로필, max_steps) 또는 ConfigError."""
    kind = str(args.get("kind") or "").strip()
    agent = None
    try:
        base = load_profile(kind) if kind else parent
    except ConfigError:   # stewart 프로필이 없으면 .stewart/agents(호환 .claude/agents)의 정의. 같은 이름이면 프로필이 이긴다
        from . import projcfg as PC
        agent = PC.find_agent(parent.workdir, kind, pfl or PC.Flags())
        if agent is None:
            raise
        base = PC.agent_profile(agent, parent)
    base_tools = set(parent_tools) if base.tools == ["*"] else set(base.tools)
    allowed = set(parent_tools) & base_tools
    if args.get("tools"):
        allowed &= {str(t) for t in args["tools"]}
    elif read_default and not kind:   # chat·허브: 도구를 적지 않으면 읽기 도구만(조사·탐색)
        allowed &= M.READ_TOOLS
    allowed -= NO_CHILD
    if exec_mode == "plan":
        allowed &= M.READ_TOOLS
    tools = [t for t in parent_tools if t in allowed]
    steps = base.max_steps if (kind and agent is None) else DEFAULT_STEPS
    if args.get("max_steps"):
        try:
            steps = min(steps, max(MIN_STEPS, int(args["max_steps"])))
        except (TypeError, ValueError):
            pass
    left = budget.limit - budget.used
    if left <= 0:
        raise ConfigError(f"이번 일의 작업자 단계 합 상한({budget.limit})을 다 썼다. 직접 마무리하라.")
    steps = min(steps, left)
    prof = dataclasses.replace(base, tools=tools, workdir=parent.workdir, system=WORKER_NOTE + "\n\n" + base.system.strip(), verify_command=None, output_format="free",
                               output_schema=None, mode=None, confirm=None, manager_tools=None, delegate_budget=None, secret_files=parent.secret_files,
                               action_check=parent.action_check)
    return prof, steps


def result_text(summary: dict, out: str | None, child_dir: Path, n: int) -> str:
    hint = ""
    if not (out or "").strip() and summary.get("stop_reason") == "max_steps":
        hint = f" 단계 상한({summary.get('steps')})에 닿았다. 같은 일을 다시 맡기려면 max_steps 를 빼거나 늘려라."
    out = (out or "").strip() or f"(작업자가 최종 글을 남기지 않았다.{hint})"
    if len(out) > MAX_RETURN:
        out = out[:MAX_RETURN] + f"\n…(잘림: 전체 {len(out)}자 중 {MAX_RETURN}자)"
    files = summary.get("files_written") or []
    facts = (f"[작업자 {n}: 종료 {summary.get('stop_reason')} · 단계 {summary.get('steps')} · "
             f"바꾼 파일 {', '.join(files) if files else '없음'}]")
    # 기록 폴더 경로는 모델에게 보이지 않는다(관리자가 작업 폴더 밖 작업자 기록을 열어 읽었다). 경로는 summary delegations·facts 에만
    return f"{facts}\n{out}"


def run_child(ctx: dict, args: dict) -> tuple[bool, str]:
    """작업자 하나를 끝까지 돌리고 (성공, 돌려줄 글). ctx 는 부모 engine.run·ChatSession 이 만든 것."""
    from . import engine
    from .record import Recorder
    task = str(args.get("task") or "").strip()
    if not task:
        return False, "task 가 비었다. 작업자에게 맡길 일을 적어 다시 호출하라."
    budget: Budget = ctx["budget"]
    try:
        prof, steps = child_profile(ctx["profile"], ctx["tool_names"], ctx["exec_mode"], args, budget, ctx.get("projcfg"),
                                    read_default=bool(ctx.get("read_default")))
    except ConfigError as e:
        return False, f"작업자를 만들 수 없다: {e}"
    if args.get("_tier"):   # 판 116: 등급으로 띄운 작업자 — 압축 예산·생각 칸을 프로필 raw["tier"] 로(엔진이 읽는다)
        import dataclasses
        prof = dataclasses.replace(prof, raw={**(prof.raw or {}), "tier": dict(args["_tier"])})
    budget.n += 1
    n = budget.n
    crec = Recorder(Path(ctx["rec"].dir) / "sub", f"{n}-{prof.name}")
    ctx["rec"].event("delegate_start", n=n, worker_kind=args.get("kind"), tools=prof.tools, max_steps=steps,
                     task=task[:2000], dir=str(crec.dir))
    ask, on_call, ask_cfg = ctx.get("ask"), ctx.get("on_call"), ctx.get("ask_cfg")
    ask_out = ctx.get("ask_outside")
    parent = {"cps": ctx["cps"], "env": ctx["env"], "bounds": ctx.get("bounds"),
              "ask_outside": (lambda name, a, paths, kind: ask_out(n, name, a, paths, kind)) if ask_out else None,
              "ask": (lambda name, a: ask(n, name, a)) if ask else None,
              "ask_cfg": (lambda name, a, path: ask_cfg(n, name, a, path)) if ask_cfg else None,
              "on_call": (lambda name, a: on_call(n, name, a)) if on_call else None,
              "rule": ctx.get("rule")}   # 부모 chat 의 셸 허용 규칙(작업자 확인 자리에서도 같게)
    from . import manager as MG
    verifier = prof.name == MG.VERIFIER
    git_before = MG.git_state(prof.workdir) if verifier else None
    try:
        s = engine.run(prof, ctx["backend"], task, crec, steps, mode=ctx["exec_mode"], parent=parent)
    except KeyboardInterrupt:
        ctx["rec"].event("delegate_end", n=n, stop_reason="interrupted")
        ctx["log"].append({"n": n, "worker_kind": args.get("kind"), "stop_reason": "interrupted", "dir": str(crec.dir)})
        raise
    budget.used += s["steps"]
    of = crec.dir / "output.md"
    out = of.read_text(encoding="utf-8") if of.exists() else None
    entry = {"n": n, "worker_kind": args.get("kind"), "tools": prof.tools, "stop_reason": s["stop_reason"], "steps": s["steps"],
             "files_written": s.get("files_written", []), "elapsed_s": round(s["elapsed_s"], 2), "dir": str(crec.dir)}
    verdict = MG.parse_verdict(out) if verifier else None
    if verifier:
        if git_before is None:
            entry["git_check"] = "확인 불가(git 저장소 아님)"
        elif MG.git_state(prof.workdir) != git_before:
            entry["git_check"] = "검증자가 파일을 바꿈"
            verdict = "무효(검증자가 파일을 바꿈)"
            out = "[엔진] 검증자가 작업 폴더 파일을 바꿔 이 판정은 무효다(git status 가 달라짐).\n" + (out or "")
        else:
            entry["git_check"] = "바뀜 없음"
        entry["verdict"] = verdict
    ctx["log"].append(entry)
    ctx["rec"].event("delegate_end", **entry)
    text = result_text(s, out, crec.dir, n)
    if ctx.get("ledger") is not None:
        ctx["ledger"].add_run(entry, verdict)
        text += "\n" + ctx["ledger"].summary_line()
    return True, text
