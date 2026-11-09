"""hooks: 프로필 [hooks] 의 사용자 명령을 Claude Code hooks 와 같은 꼴로 돌린다.

최소판 — 이벤트 넷: SessionStart · PreToolUse · PostToolUse · Stop
- 프로필:
    [hooks]
    PreToolUse = [{command = "…", matcher = "run_shell|write_file", timeout_s = 30}]
    Stop = ["…"]                                  # 글 하나면 command 만
  matcher 는 도구 이름 정규식(PreToolUse·PostToolUse 만, 없으면 모든 도구). timeout_s 기본 60.
- 돌리는 꼴: 작업 폴더에서 셸로, 표준 입력 JSON {"event", "workdir", "run_dir", "step", …이벤트별 칸},
  환경 변수 STEWART_HOOK_EVENT=<이벤트>. 칸: PreToolUse tool_name·tool_input / PostToolUse 그 둘 + tool_response{ok, result 앞 4,000자}
  / Stop final_output 앞 2,000자 / SessionStart source(run|chat)·task 앞 2,000자.
- 종료 코드: 0 = 통과(SessionStart 의 표준 출력은 맥락으로 첫 사람 입력 앞에 붙는다, 4,000자까지),
  2 = 막음 — PreToolUse 는 도구를 돌리지 않고 표준 오류를 도구 결과로, PostToolUse 는 표준 오류를 도구 결과 뒤에 붙여 모델에,
  Stop 은 끝내지 않고 표준 오류를 모델에 돌려준다(check_on_stop_max 번까지), SessionStart 는 기록만.
  그 밖 = 막지 않는 오류(기록만).
- STEWART_HOOKS=0 이면 [hooks] 를 모두 끈다(check_on_stop 은 STEWART_CHECK_ON_STOP 로 따로). 작업자(delegate)에서는 돌리지 않는다.
- hooks 는 사용자 프로필에서만 온다(저장소 파일에서 읽지 않는다). 명령은 사용자 권한으로 돈다.

check_on_stop — 최종 답 직전 확인 명령.

- 명령: 프로필 check_on_stop(글이면 그 명령, true 면 verify_command). 끄고 켜기는 환경 변수 STEWART_CHECK_ON_STOP 가 이긴다:
  "0" = 끔, "1" = 켬(프로필에 명령이 없으면 verify_command), 그 밖 값은 무시하고 기록한다.
- 돌리는 꼴은 Claude Code 의 Stop hook 과 맞춘다: 작업 폴더에서 셸로, 표준 입력에 JSON
  {"event": "Stop", "workdir", "run_dir", "step", "final_output"(앞 2,000자)}, 환경 변수 STEWART_HOOK_EVENT=Stop.
- 판정(check 꼴): 종료 코드 0 = 통과, skip_exit(기본 5 = unittest 시험 없음) = 확인할 것 없음, 그 밖 = 막음 → 출력 끝을 모델에 돌려준다.
  (72차의 [hooks] Stop 은 Claude Code 꼴 "종료 코드 2 = 막음" 이다. check_on_stop 이 통과한 뒤에 돈다.)
- 평가기의 숨긴 시험과는 섞이지 않는다: 여기서 돌리는 것은 작업 폴더의 공개 명령뿐이다.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass

from .shell import tool_env

ENV_CHECK = "STEWART_CHECK_ON_STOP"
FEEDBACK_CHARS = 3000

FEEDBACK = ("[엔진] 끝내기 전에 확인 명령 `{command}` 을 돌렸더니 실패했다(exit {exit}). 출력 끝:\n{output}\n\n"
            "과제가 이 실패를 고치라는 것이면 고친 뒤 다시 확인하라. 과제 지시상 손대면 안 되는 실패(다른 사람 담당, "
            "고치지 말라고 한 파일 등)라면 그대로 두고 최종 보고에 그렇게 써라. 최종 보고는 과제가 정한 형식을 지킨다.")


def stop_command(profile, env=None) -> dict:
    """{"command": 명령|None, "source": profile|env:on|env:off|off, "ignored": 잘못된 env 값|None}"""
    env = os.environ if env is None else env
    v = env.get(ENV_CHECK)
    conf = getattr(profile, "check_on_stop", None)
    cmd = conf if isinstance(conf, str) and conf.strip() else (profile.verify_command if conf is True else None)
    if v == "0":
        return {"command": None, "source": "env:off", "ignored": None}
    if v == "1":
        return {"command": cmd or profile.verify_command or None, "source": "env:on", "ignored": None}
    return {"command": cmd, "source": "profile" if cmd else "off", "ignored": v if v is not None else None}


def run_stop(command: str, workdir, timeout_s: float, skip_exit=(5,), payload: dict | None = None) -> dict:
    """확인 명령 한 번. {"command", "exit", "passed", "skipped", "output", "elapsed_s"}"""
    t0 = time.monotonic()
    data = json.dumps({"event": "Stop", **(payload or {})}, ensure_ascii=False)
    try:
        p = subprocess.run(command, shell=True, cwd=workdir, input=data, capture_output=True, text=True,
                           timeout=timeout_s, errors="replace", env={**tool_env(), "STEWART_HOOK_EVENT": "Stop"})
    except subprocess.TimeoutExpired:
        return {"command": command, "exit": "timeout", "passed": False, "skipped": False,
                "output": f"시간 초과({timeout_s:.0f}초)", "elapsed_s": time.monotonic() - t0}
    skipped = p.returncode in tuple(skip_exit)
    from .selfcheck import failing_tests
    return {"command": command, "exit": p.returncode, "passed": p.returncode == 0, "skipped": skipped,
            "output": (p.stdout + p.stderr)[-6000:], "elapsed_s": time.monotonic() - t0,
            "failing": failing_tests(p.stdout + p.stderr, p.returncode)}


def feedback(r: dict) -> str:
    return FEEDBACK.format(command=r["command"], exit=r["exit"], output=r["output"][-FEEDBACK_CHARS:])


# ---- hooks 최소판 ----

EVENTS = ("SessionStart", "PreToolUse", "PostToolUse", "Stop")
ENV_HOOKS = "STEWART_HOOKS"
CONTEXT_CHARS = 4000


@dataclass
class Hook:
    command: str
    matcher: str | None = None
    timeout_s: float = 60


def parse(table, where: str = "프로필") -> dict[str, list[Hook]]:
    """[hooks] 표 → {이벤트: [Hook]}. 틀리면 ValueError(설정 오류로 바꿔 올린다)."""
    if table in (None, {}):
        return {}
    if not isinstance(table, dict):
        raise ValueError(f"{where}: hooks 는 표([hooks])")
    out: dict[str, list[Hook]] = {}
    for ev, items in table.items():
        if ev not in EVENTS:
            raise ValueError(f"{where}: hooks 의 모르는 이벤트 {ev}(쓸 수 있는 것: {', '.join(EVENTS)})")
        items = items if isinstance(items, list) else [items]
        hs = []
        for it in items:
            if isinstance(it, str) and it.strip():
                hs.append(Hook(it))
                continue
            if not isinstance(it, dict) or not isinstance(it.get("command"), str) or not it["command"].strip():
                raise ValueError(f"{where}: hooks.{ev} 의 항목은 명령 글 또는 {{command = \"…\"}} 표")
            bad = set(it) - {"command", "matcher", "timeout_s"}
            if bad:
                raise ValueError(f"{where}: hooks.{ev} 의 모르는 칸 {sorted(bad)}")
            m = it.get("matcher")
            if m is not None:
                if ev not in ("PreToolUse", "PostToolUse") or not isinstance(m, str):
                    raise ValueError(f"{where}: hooks.{ev} matcher 는 PreToolUse·PostToolUse 의 정규식 글만")
                try:
                    re.compile(m)
                except re.error as e:
                    raise ValueError(f"{where}: hooks.{ev} matcher 정규식 오류: {e}") from None
            t = it.get("timeout_s", 60)
            if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0 < t <= 3600:
                raise ValueError(f"{where}: hooks.{ev} timeout_s 는 0 초과 3600 이하")
            hs.append(Hook(it["command"], m, float(t)))
        out[ev] = hs
    return out


class Hooks:
    """한 실행(또는 chat 세션)의 hooks. record(dict) 로 돌린 결과를 사건에 남긴다."""

    def __init__(self, table, workdir, run_dir, record=None, env=None):
        env = os.environ if env is None else env
        self.off = env.get(ENV_HOOKS) == "0"
        self.table = {} if self.off else parse(table)
        self.workdir, self.run_dir = workdir, run_dir
        self.record = record or (lambda r: None)
        self.runs = 0

    def has(self, event: str) -> bool:
        return bool(self.table.get(event))

    def _run(self, event: str, payload: dict, tool: str | None = None) -> list[dict]:
        out = []
        for h in self.table.get(event, []):
            if tool is not None and h.matcher and not re.search(h.matcher, tool):
                continue
            data = json.dumps({"event": event, "workdir": str(self.workdir), "run_dir": str(self.run_dir), **payload},
                              ensure_ascii=False)
            t0 = time.monotonic()
            try:
                p = subprocess.run(h.command, shell=True, cwd=self.workdir, input=data, capture_output=True, text=True,
                                   timeout=h.timeout_s, errors="replace", env={**tool_env(), "STEWART_HOOK_EVENT": event})
                r = {"event": event, "command": h.command, "exit": p.returncode, "stdout": p.stdout[-CONTEXT_CHARS:],
                     "stderr": p.stderr[-FEEDBACK_CHARS:], "blocked": p.returncode == 2}
            except subprocess.TimeoutExpired:
                r = {"event": event, "command": h.command, "exit": "timeout", "stdout": "", "stderr": f"시간 초과({h.timeout_s:.0f}초)",
                     "blocked": False}
            r["elapsed_s"] = round(time.monotonic() - t0, 3)
            if tool is not None:
                r["tool"] = tool
            self.runs += 1
            self.record(r)
            out.append(r)
            if r["blocked"]:
                break   # 앞 hook 이 막으면 뒤 hook 은 돌리지 않는다
        return out

    @staticmethod
    def _why(rs: list[dict]) -> str | None:
        b = [r for r in rs if r["blocked"]]
        return (b[0]["stderr"].strip() or f"hook `{b[0]['command']}` 이 막았다(exit 2)") if b else None

    def session_start(self, source: str, task: str, step: int = 0) -> str:
        rs = self._run("SessionStart", {"source": source, "task": task[:2000], "step": step})
        ctx = "\n".join(r["stdout"].strip() for r in rs if r["exit"] == 0 and r["stdout"].strip())
        return ctx[:CONTEXT_CHARS]

    def pre(self, tool: str, args: dict, step: int) -> str | None:
        return self._why(self._run("PreToolUse", {"step": step, "tool_name": tool, "tool_input": args}, tool))

    def post(self, tool: str, args: dict, ok: bool, result: str, step: int) -> str | None:
        return self._why(self._run("PostToolUse", {"step": step, "tool_name": tool, "tool_input": args,
                                                   "tool_response": {"ok": ok, "result": (result or "")[:4000]}}, tool))

    def stop(self, final_output: str, step: int) -> str | None:
        return self._why(self._run("Stop", {"step": step, "final_output": (final_output or "")[:2000]}))


PRE_BLOCK = "hook(PreToolUse) 이 이 호출을 막았다: {why}"
POST_NOTE = "\n\n[hook PostToolUse] {why}"
STOP_BLOCK = "[hook Stop] 끝내기 전 hook 이 막았다:\n{why}\n\n이 내용을 반영한 뒤 끝내라. 과제 범위 밖이면 그대로 두고 최종 보고에 그렇게 써라."
SESSION_CTX = "[hook SessionStart 출력]\n{ctx}\n\n"
