"""stewart chat — 대화형 모드(Claude Code 비슷하게).

- 대화가 이어진다(같은 messages에 차례로 쌓는다).
- 모델의 도구 호출과 결과 요약, 파일 변경(diff), 셸 명령과 출력 끝부분을 화면에 보여 준다.
- 사람이 끼어들 수 있다:
    · 모델이 일하는 중 Ctrl-C → 그 차례를 멈추고 입력으로 돌아온다(대화는 이어진다).
      스트리밍(기본)이면 생성 도중에도 연결을 바로 끊고, 받은 데까지의 답을 대화에 남긴다.
    · /confirm on → 파일 쓰기·고치기·셸 실행 직전마다 묻는다. y(실행) / n(거부) / 다른 글(거부하고 그 글을 모델에 전달).
- 작업 폴더 밖 쓰기(파일 도구·셸의 흔한 쓰기 꼴)는 모드와 관계없이 묻는다(bounds.py). 그 밖의 제한은 행동 검사 자리(check.py)와
  운영체제 샌드박스(있으면)가 맡는다.
- 실행 기록은 runs/<시각>-chat-<프로필>/ 에 events.jsonl·transcript.md·summary.json 으로 남는다.

명령: /help /exit /reset /status /confirm on|off /mode auto|edits|ask|plan /plan [on|off] /stream on|off /think on|off|default /skill 이름 [글] /ledger /steps N /facts /compact /undo /rewind [N]
여러 줄 입력: 줄 끝에 \\ 를 붙이면 다음 줄로 이어진다. 또는 \"\"\" 로 열고 \"\"\" 로 닫는다.
"""

from __future__ import annotations

import difflib
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import __version__, tools as T
from .backend import BackendError, chat, chat_stream, is_context_overflow, sync_context
from .compact import Compactor, est_tokens
from .check import CheckContext, ToolCall, load_check
from .config import Backend, Profile
from .engine import LENGTH_NOTE, REPEAT_NUDGE, REPEAT_STOP, _git_rev, empty_length, memory_for, web_search_config
from .record import Recorder
from .resume import prepare, save_messages
from . import bounds as BND, hooks as HK, memory as MEM, modes as M, think as TH
from . import facts as FX
from . import manager as MG
from . import guard as G
from . import clarify
from . import envinfo
from . import skills as SK
from . import delegate as DG
from . import config_guard as CG
from .checkpoint import Checkpoints

WRITE_TOOLS = {"write_file", "edit_file", "multi_edit", "notebook_edit"}
CONFIRM_TOOLS = WRITE_TOOLS | {"run_shell"}
COMPACT_RATIO = 0.75   # 마지막 프롬프트 토큰이 max_context 의 이 비율을 넘으면 오래된 도구 결과를 줄인다
KEEP_RECENT = 8        # 줄이지 않고 둘 최근 메시지 수
CLIP_OLD = 300


class UI:
    """화면 출력. 터미널이면 색을 쓴다. 시험에서는 바꿔 끼운다."""

    def __init__(self, out=None, color: bool | None = None):
        self.out = out or sys.stdout
        self.color = self.out.isatty() if color is None else color

    def _c(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.color else s

    def say(self, s: str = "") -> None:
        print(s, file=self.out, flush=True)

    def dim(self, s: str) -> None:
        self.say(self._c("2", s))

    def info(self, s: str) -> None:
        self.say(self._c("36", s))

    def warn(self, s: str) -> None:
        self.say(self._c("33", s))

    def assistant(self, s: str) -> None:
        self.say(self._c("1", "● ") + s)

    def tool(self, name: str, summary: str) -> None:
        self.say(self._c("35", f"  ⏺ {name}") + f" {summary}")

    def diff(self, lines: list[str]) -> None:
        for l in lines:
            if l.startswith("+") and not l.startswith("+++"):
                self.say("    " + self._c("32", l))
            elif l.startswith("-") and not l.startswith("---"):
                self.say("    " + self._c("31", l))
            elif l.startswith("@@"):
                self.say("    " + self._c("36", l))
            else:
                self.say("    " + self._c("2", l))

    def ask(self, prompt: str) -> str:
        """확인 질문(셸 y/n·계속할까 등). TUI 는 끼워 넣기 줄과 섞지 않고 이 답으로만 받는다."""
        return input(prompt)

    def read_line(self, prompt: str) -> str:
        """평소 입력(사람 차례). 기본은 ask 와 같다."""
        return self.ask(prompt)

    # TUI 연결 자리(tui.py). 기본 UI 에서는 아무 일도 하지 않는다
    def take_interjections(self) -> list[str]:
        """모델이 일하는 동안 사람이 줄에 세운 글(다음 요청 앞에 user 메시지로 넣는다)."""
        return []

    def tool_result(self, tc: dict) -> None:
        """도구 하나가 끝났다(전체 결과는 tc['result']). 접기·펼치기용."""

    # 스트리밍: 조각을 줄바꿈 없이 바로 쓴다
    def stream_start(self) -> None:
        self._streamed = False
        self._thinking = False

    def stream_piece(self, text: str, think: str) -> None:
        if think and not self._streamed and not self._thinking:
            self.out.write(self._c("2", "  (생각 중…)"))
            self._thinking = True
        if text:
            if not self._streamed:
                self.out.write(("\n" if self._thinking else "") + self._c("1", "● "))
                self._streamed = True
            self.out.write(text)
        self.out.flush()

    def stream_end(self) -> bool:
        """조각을 하나라도 썼으면 줄을 끝내고 True."""
        if getattr(self, "_streamed", False) or getattr(self, "_thinking", False):
            self.out.write("\n")
            self.out.flush()
        return getattr(self, "_streamed", False)


def _target(env: T.ToolEnv, args: dict) -> Path | None:
    p = args.get("path")
    return T._p(env, p) if p else None


def _read(p: Path | None) -> str | None:
    if p is None or not p.is_file():
        return None
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def make_diff(before: str | None, after: str | None, name: str, max_lines: int = 80) -> list[str]:
    a = (before or "").splitlines()
    b = (after or "").splitlines()
    d = list(difflib.unified_diff(a, b, f"a/{name}" if before is not None else "/dev/null", f"b/{name}", lineterm="", n=2))
    if len(d) > max_lines:
        d = d[:max_lines] + [f"…(diff {len(d)}줄 중 {max_lines}줄만 보임)"]
    return d


def _one_line(s: str, n: int = 100) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _arg_summary(name: str, args: dict) -> str:
    if name == "run_shell":
        return "$ " + _one_line(args.get("command", ""), 140)
    if name in ("read_file", "write_file", "edit_file", "multi_edit", "list_dir", "notebook_edit"):
        return str(args.get("path", "."))
    if name == "search":
        return f"{args.get('pattern', '')!r} in {args.get('path', '.')}"
    if name == "web_search":
        return repr(args.get("query", ""))
    if name == "web_fetch":
        return _one_line(str(args.get("url", "")), 140)
    return _one_line(json.dumps(args, ensure_ascii=False), 120)


class ChatSession:
    def __init__(self, profile: Profile, backend: Backend, rec: Recorder, ui: UI | None = None, confirm: bool | None = None,
                 history: list[dict] | None = None, resumed_from: str | None = None, stream: bool = False,
                 mode: str | None = None, default_mode: str = "auto", think: bool | None = None):
        self.profile, self.backend, self.rec = profile, backend, rec
        self.ui = ui or UI()
        # 모드 우선순위: 명령줄 --mode > 명령줄 --confirm/--no-confirm(ask/auto) > 프로필 mode > 프로필 confirm > 기본.
        # 기본은 cli chat 이 edits 를 준다(일반 터미널에는 셸 격리가 없다). 시험 등에서 직접 만들면 auto.
        yn = {True: "ask", False: "auto"}
        self.mode = (mode or (yn[confirm] if confirm is not None else None) or profile.mode
                     or (yn[profile.confirm] if profile.confirm is not None else None) or default_mode)
        M.check_mode(self.mode, interactive=True)
        self.stream = stream   # True 면 응답을 조각으로 받아 바로 보여 준다(cli chat 기본). Ctrl-C 가 생성을 바로 끊는다
        self.max_steps = profile.max_steps   # 차례당 단계 상한. 닿으면 묻는 모드는 "계속할까?"(한 번 더 같은 만큼), auto 는 끝내고 안내
        self.think = think     # None = 백엔드 params 그대로, True/False = chat_template_kwargs.enable_thinking 덮어쓰기(/think)
        web = web_search_config(profile)
        base_names = [t for t in DG.other_tools(clarify.other_tools(profile.tools)) if t != SK.NAME]
        defs = T.definitions(base_names) if base_names else []
        if profile.tools and clarify.wanted(profile.tools):
            defs.append(clarify.DEFINITION)   # 되묻기: 질문을 보이고 그 차례를 끝낸다(다음 사람 입력이 답)
        if profile.tools and DG.wanted(profile.tools, 0):
            defs.append(DG.DEFINITION)   # 작업자에게 맡기기(확인 질문은 같은 화면에 "[작업자 N]")
        if not web:
            defs = [d for d in defs if d["function"]["name"] not in T.WEB_TOOLS]
        self.skills = SK.discover(profile.workdir) if (profile.skills is not False and profile.tools) else []   # 세션 시작 때 한 번
        if self.skills:
            defs.append(SK.DEFINITION)
        self.mem = MEM.Memory(profile, rec.dir) if MEM.enabled(profile) else None   # 기억 M1
        if self.mem is not None and profile.tools:
            defs += MEM.tool_defs()
        self.all_tool_names = [d["function"]["name"] for d in defs if d["function"]["name"] not in MEM.NAMES]   # 작업자에게 넘길 수 있는 것
        if profile.manager_tools is not None:   # 관리자 모드: 모델에 보이는 자기 도구만
            if MG.NAME in profile.manager_tools:
                defs.append(MG.DEFINITION)
            defs = [d for d in defs if d["function"]["name"] in profile.manager_tools]
        self.tool_defs = defs
        self.tool_names = [d["function"]["name"] for d in defs]
        self.ledger = MG.Ledger(rec.dir / "ledger.json", "(대화)") if MG.NAME in self.tool_names else None
        self.env = T.ToolEnv(workdir=profile.workdir, shell_timeout_s=profile.shell_timeout_s, web=web,
                              artifacts_dir=rec.dir, **T.secret_options(profile))
        self.check = load_check(profile.action_check)
        mem_text, mem_log = memory_for(profile)
        self.env_info = envinfo.measure(profile.workdir)   # 세션 시작 때 한 번(이어 하기는 새로 잰다)
        system = profile.system.strip() + (f"\n\n{mem_text}" if mem_text else "") \
            + (f"\n\n{SK.list_text(self.skills)}" if self.skills else "")
        self.mem_inject: list = []
        if self.mem is not None:   # L3 주입
            mtext, self.mem_inject = self.mem.inject()
            system += "\n\n" + mtext
        self._env_note: str | None = envinfo.user_note(self.env_info)   # 다이어트 안 a: 이 세션 첫 사람 입력 앞에 붙인다
        if profile.language == "ko":
            system += "\n\n사람에게 하는 말은 한국어로 쓴다. 코드·파일 이름·명령은 원문 그대로 둔다."
        self.system = system
        self.messages: list[dict] = prepare(history, system) if history else [{"role": "system", "content": system}]
        self.bd = BND.from_profile(profile)   # 작업 폴더 경계(밖 쓰기는 모드와 관계없이 묻는다)
        self.tks = TH.settings(profile)   # 생각 켬 차례의 상한·샘플링 손잡이
        self._pmark = 0                   # 마지막 응답 때의 메시지 수(프롬프트 어림용)
        self.ctx = sync_context(backend)    # 실제 문맥 길이 = 서버 max_model_len(있으면). 다르면 시작 화면에 경고
        self.hk = HK.Hooks(profile.hooks, profile.workdir, rec.dir, record=lambda r: self.rec.event("hook", **r))
        self._hook_ctx = self.hk.session_start("chat", "") if self.hk.has("SessionStart") else ""
        self.comp = self._new_compactor()
        self.st = dict(turns=0, steps=0, tool_calls=0, tool_errors=0, parse_failures=0, denied=0, interrupts=0,
                       completion_tokens=0, prompt_tokens_total=0, last_prompt_tokens=0, model_time_s=0.0,
                       pred_n=0, pred_ms=0.0, prompt_n=0, prompt_ms=0.0, compactions=0, files_changed=set())
        self.fail_count: dict[str, int] = {}
        self._repeat_stop = False
        self.guard = G.LoopGuard()
        self._guard_hit = 0
        self._asked: str | None = None
        self.t0 = time.monotonic()
        self.meta = {
            "engine": "stewart", "engine_version": __version__, "engine_git": _git_rev(), "mode": "chat",
            "profile": profile.name, "profile_path": str(profile.path), "backend": backend.public(),
            "workdir": str(profile.workdir), "tools": self.tool_names, "max_steps": self.max_steps,
            "thinking": self.think, "max_consecutive_errors": profile.max_consecutive_errors, "output_format": "free",
            "action_check": profile.action_check or "stewart.check:allow_all", "exec_mode": self.mode,
            "memory_files": mem_log, "env_info": self.env_info, "skills": SK.meta_log(self.skills),
            "resumed_from": resumed_from, "resumed_messages": len(self.messages) - 1 if history else 0,
            "started": datetime.now().isoformat(timespec="seconds"),
        }
        rec.event("run_start", meta=self.meta, messages=self.messages)
        if history:
            n_user = sum(1 for m in self.messages if m.get("role") == "user")
            self.ui.dim(f"이어 감: {resumed_from} (메시지 {len(self.messages) - 1}개, 사람 입력 {n_user}개)")
        save_messages(rec.dir, self.messages)
        if mem_log:
            self.ui.dim("프로젝트 기억 파일: " + ", ".join(f"{m['path']} ({m['used']}자{', 잘림' if m['truncated'] else ''})" for m in mem_log))
        if self.skills:
            self.ui.dim(f"스킬 {len(self.skills)}개: " + ", ".join(f"{k.name}({'프로젝트' if k.scope == 'project' else '사용자'})" for k in self.skills))
            proj = sorted({str(k.dir.parent) for k in self.skills if k.scope == "project"})
            if proj:
                self.ui.dim("프로젝트 스킬 폴더: " + ", ".join(proj))
            ign = [f"{k.name}: {', '.join(k.ignored_keys)}" for k in self.skills if k.ignored_keys]
            if ign:
                self.ui.dim("무시한 키(스킬은 도구·모드·확인을 바꾸지 못한다): " + " · ".join(ign))
        self.skills_loaded: list[str] = []
        self._skill_note: str | None = None
        self.cps = Checkpoints(rec.dir, profile.workdir)
        self._rewind_note: str | None = None
        self.delegations: list[dict] = []
        self._dg_budget = DG.Budget(self.max_steps, profile.delegate_budget)
        rec.md_header(self.meta, "(대화형 — 아래 '사람' 항목이 차례마다의 입력)")

    # ---- 모드 -----------------------------------------------------------
    @property
    def confirm(self) -> bool:
        """옛 이름: 쓰기·셸 전에 묻는가(ask). 켜면 ask, 끄면 auto."""
        return self.mode == "ask"

    @confirm.setter
    def confirm(self, on: bool) -> None:
        self.set_mode("ask" if on else "auto")

    def set_mode(self, mode: str) -> str | None:
        """모드를 바꾼다. 계획 모드를 벗어나면 다음 사람 입력 앞에 '계획 승인' 줄을 붙인다. 화면에 낼 말을 돌려준다."""
        M.check_mode(mode, interactive=True)
        old, self.mode = self.mode, mode
        self.rec.event("mode", old=old, new=mode)
        if old == "plan" and mode != "plan":
            self._plan_exit = True
            return "계획 모드를 끝냈다. 다음 입력(예: '진행해')부터 계획대로 실행한다."
        return None

    # ---- 도구 실행 -------------------------------------------------------
    def _confirm(self, name: str, args: dict) -> str | None:
        """None이면 실행, 문자열이면 거부 사유(모드에 따라 실행·묻기·거부)."""
        d = M.decide(self.mode, name)
        if d == "deny":
            return M.PLAN_DENY
        cg = CG.hit(self.profile.workdir, name, args)
        if cg is not None:   # stewart 사용자 설정 폴더: 모드와 관계없이 매번 묻는다
            return self._ask_cfg(CG.ASK_PROMPT.format(path=cg))
        paths = self._outside_writes(name, args)
        if paths:   # 작업 폴더 밖 쓰기: 모드와 관계없이 묻는다(y = 그 경로만 이 세션에서 연다)
            return self._ask_outside(paths, "write")
        r = self.bd.tool_read(name, args)
        if r:
            return BND.READ_DENY.format(path=r) if self.bd.read_outside == "deny" else self._ask_outside([r], "read")
        if d == "run":
            return None
        return self._ask_permission("    실행할까? [y/n/모델에 할 말] ")

    def _outside_writes(self, name: str, args: dict) -> list[str]:
        paths = [p for p in [self.bd.tool_write(name, args)] if p]
        if name == "run_shell":
            cwd = getattr(self.env.shell, "cwd", None) if self.env.shell is not None else None
            paths += [p for p in self.bd.shell_outside(str(args.get("command") or ""), cwd) if p not in paths]
        return paths

    def _ask_outside(self, paths: list[str], kind: str, prefix: str = "") -> str | None:
        """y 면 그 경로만 이 세션에서 연다(None). 빈 입력은 승인이 아니다."""
        q = getattr(self.ui, "question", None) or self.ui.ask
        prompt = (BND.ASK_WRITE.format(paths=", ".join(paths)) if kind == "write" else BND.ASK_READ.format(path=paths[0]))
        try:
            ans = q(prefix + prompt.lstrip() if prefix else prompt).strip()
        except EOFError:
            ans = ""
        if ans.lower() in ("y", "yes", "ㅇ", "네"):
            (self.bd.approved if kind == "write" else self.bd.approved_read).update(paths)
            self.rec.event("outside_approved", what=kind, paths=paths)
            return None
        what = "작업 폴더 밖 쓰기" if kind == "write" else "작업 폴더 밖 읽기"
        if ans.lower() in ("", "n", "no", "ㄴ", "아니"):
            return f"사용자가 {what}({', '.join(paths)})를 거부했다. 작업 폴더 안에서 하거나, 밖을 바꿔야 하면 보고에 적어라."
        return f"사용자가 {what}를 거부하고 이렇게 말했다: {ans}"

    def _ask_permission(self, prompt: str) -> str | None:
        """y 면 None(실행), 아니면 거부 사유. 질문은 ui.question(TUI)이 있으면 그것으로."""
        q = getattr(self.ui, "question", None) or self.ui.ask
        try:
            ans = q(prompt).strip()
        except EOFError:
            ans = "n"
        if ans.lower() in ("", "y", "yes", "ㅇ", "네"):
            return None
        if ans.lower() in ("n", "no", "ㄴ", "아니"):
            return "사용자가 이 호출을 거부했다. 다른 방법을 쓰거나 사용자에게 물어라."
        return f"사용자가 이 호출을 거부하고 이렇게 말했다: {ans}"

    def _ask_cfg(self, prompt: str) -> str | None:
        q = getattr(self.ui, "question", None) or self.ui.ask
        try:
            ans = q(prompt).strip()
        except EOFError:
            ans = ""
        if ans.lower() in ("y", "yes", "ㅇ", "네"):    # 빈 입력(Enter)은 허락으로 보지 않는다
            return None
        if ans.lower() in ("", "n", "no", "ㄴ", "아니"):
            return "사용자가 stewart 설정 파일 쓰기를 거부했다. 설정 파일은 고치지 말고 하려던 바꿈을 보고에 적어라."
        return f"사용자가 stewart 설정 파일 쓰기를 거부하고 이렇게 말했다: {ans}"

    def _exec(self, rc: dict, step: int) -> dict:
        fn = rc.get("function") or {}
        name, raw = fn.get("name", ""), fn.get("arguments") or "{}"
        raw_s = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        tc = {"id": rc["id"], "name": name, "raw_arguments": raw_s}
        self.st["tool_calls"] += 1
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            if not isinstance(args, dict):
                raise ValueError("인자가 객체가 아니다")
        except Exception as e:
            tc.update(parse_error=str(e), ok=False, result=f"인자 JSON 파싱 실패: {e}. 올바른 JSON 객체로 다시 호출하라.",
                      elapsed_s=0.0)
            self.st["parse_failures"] += 1
            self.ui.tool(name, f"인자 파싱 실패: {e}")
            return tc
        self.ui.tool(name, _arg_summary(name, args))
        if name not in self.tool_names:
            tc.update(ok=False, result=T.unknown_tool(name, self.tool_names), elapsed_s=0.0)
            self.ui.warn("    없는 도구")
            return tc
        if name == clarify.NAME:
            q = clarify.question_text(args)
            if not q:
                tc.update(ok=False, result="question 이 비었다. 무엇을 묻는지 적어 다시 호출하라.", elapsed_s=0.0)
                return tc
            self._asked = self._asked or q
            tc.update(ok=True, result=clarify.TOOL_RESULT, elapsed_s=0.0, question=q)
            return tc
        if name in MEM.NAMES and self.mem is not None:   # 기억 도구
            ok, result = MEM.execute(self.mem, name, args)
            self.rec.event("memory_tool", step=step, name=name, ok=ok)
            self.ui.dim(f"    {'✓' if ok else '✗'} {_one_line(result, 90)}")
            if not ok:
                self.st["tool_errors"] += 1
            tc.update(ok=ok, result=result, elapsed_s=0.0)
            return tc
        if name == SK.NAME:
            ok, result = SK.load(self.skills, str(args.get("name") or ""), args.get("file"),
                                 self.env.secret_globs, self.env.secret_allow)
            self.rec.event("skill_load", step=step, name=args.get("name"), file=args.get("file"), ok=ok)
            if ok:
                self.skills_loaded.append(str(args.get("name")) + (f":{args['file']}" if args.get("file") else ""))
            self.ui.dim(f"    {'✓' if ok else '✗'} {_one_line(result, 90)}")
            if not ok:
                self.st["tool_errors"] += 1
            tc.update(ok=ok, result=result, elapsed_s=0.0)
            return tc
        if name == MG.NAME and self.ledger is not None:
            ok, result = self.ledger.op(args)
            self.ui.dim(f"    {'✓' if ok else '✗'} 장부 {args.get('op')}")
            if not ok:
                self.st["tool_errors"] += 1
            tc.update(ok=ok, result=result, elapsed_s=0.0)
            return tc
        if name == DG.NAME:
            ok, result = self._delegate(args)
            first = result.splitlines()[0] if result else ""
            self.ui.dim(f"    {'✓' if ok else '✗'} {_one_line(first, 160)}")
            if not ok:
                self.st["tool_errors"] += 1
            tc.update(ok=ok, result=result, elapsed_s=0.0)
            return tc
        call = ToolCall(id=rc["id"], name=name, arguments=args, raw_arguments=raw_s)
        denied = self.check(call, CheckContext(profile=self.profile.name, workdir=self.profile.workdir, step=step)) \
            or self._confirm(name, args)
        if denied:
            self.st["denied"] += 1
            tc.update(denied=denied, ok=False, result=f"실행하지 않음: {denied}", elapsed_s=0.0)
            self.ui.warn(f"    거부: {denied}")
            return tc
        target = _target(self.env, args) if name in WRITE_TOOLS else None
        before = _read(target)
        t1 = time.monotonic()
        why = self.hk.pre(name, args, step) if self.hk.has("PreToolUse") else None
        if why is not None:   # PreToolUse exit 2 → 돌리지 않음
            ok, result = False, HK.PRE_BLOCK.format(why=why)
            tc["hook_blocked"] = True
            self.ui.warn(f"    hook 이 막음: {_one_line(why, 200)}")
        else:
            self.cps.before_write(name, args)
            ok, result = T.execute(self.env, name, args)
            self.cps.after_write(name, args, ok)
            post = self.hk.post(name, args, ok, result, step) if self.hk.has("PostToolUse") else None
            if post is not None:
                tc["hook_note"] = True
                result += HK.POST_NOTE.format(why=post)
        tc["elapsed_s"] = time.monotonic() - t1
        if not ok:
            sig = name + "\0" + raw_s
            self.fail_count[sig] = self.fail_count.get(sig, 0) + 1
            if self.fail_count[sig] >= REPEAT_NUDGE:
                tc["repeat"] = self.fail_count[sig]
                result += (f"\n\n[엔진] 똑같은 호출이 {self.fail_count[sig]}번 실패했다. 되풀이하지 말고 현재 상태를 확인하거나 "
                           "다른 방법을 써라.")
                if self.fail_count[sig] >= REPEAT_STOP:
                    self._repeat_stop = True
        same = self.guard.observe(name, raw_s, ok, result)
        g_note = self.guard.note(same)
        if g_note:
            if same >= G.STRONG:   # chat 은 도구를 빼지 않고 사람에게 묻는다
                g_note = G.NOTE.format(n=same)
            result += "\n\n" + g_note
            tc["guard"] = same
            if same >= G.STOP:
                self._guard_hit = same
        tc.update(ok=ok, result=result)
        if target is not None and ok:
            after = _read(target)
            d = make_diff(before, after, os.path.relpath(target, self.profile.workdir) if target.is_absolute() else str(target))
            tc["diff"] = "\n".join(d)
            self.st["files_changed"].add(str(target))
            self.ui.diff(d or ["(바뀐 내용 없음)"])
        elif name == "run_shell":
            lines = result.splitlines()
            tail = lines[-15:]
            if len(lines) > 15:
                self.ui.dim(f"    …({len(lines)}줄 중 끝 15줄)")
            for l in tail:
                self.ui.dim("    " + l[:200])
        else:
            first = result.splitlines()[0] if result else ""
            self.ui.dim(f"    {'✓' if ok else '✗'} {len(result.splitlines())}줄  {_one_line(first, 90)}")
        if not ok:
            self.st["tool_errors"] += 1
        self.ui.tool_result(tc)
        return tc

    # ---- 문맥 줄이기 -----------------------------------------------------
    def compact(self, force: bool = False) -> int:
        """문턱을 넘었으면(또는 /compact) 아직 안 줄인 앞쪽 덩어리를 한 번에 줄인다(compact.Compactor). 줄인 메시지 수."""
        c = self.comp.maybe(self.messages, self.st["last_prompt_tokens"], self.st["steps"], force=force)
        if not c:
            return 0
        self.st["compactions"] += 1
        self.rec.event("compact", **c)
        return c["clipped"]

    def _clean(self) -> list[dict]:
        return [{k: v for k, v in m.items() if not k.startswith("_")} for m in self.messages]

    def _new_compactor(self) -> Compactor:
        p = self.profile
        from .compact import settings as compact_settings
        c = compact_settings(p, COMPACT_RATIO, KEEP_RECENT)        # 프로필 + STEWART_COMPACT_*
        from .compact import reply_reserve
        return Compactor(self.backend.max_context, c["ratio"], c["keep_recent"], CLIP_OLD, start=1, fold=c["fold"],
                         reserve=reply_reserve(p, self.backend))

    def _close_open_calls(self, reason: str) -> None:
        """중단으로 결과 없이 남은 tool_calls 에 결과를 채운다(다음 요청이 형식 오류가 나지 않게)."""
        for i in range(len(self.messages) - 1, 0, -1):
            m = self.messages[i]
            if m.get("role") == "assistant" and m.get("tool_calls"):
                have = {x.get("tool_call_id") for x in self.messages[i + 1:] if x.get("role") == "tool"}
                for rc in m["tool_calls"]:
                    if rc["id"] not in have:
                        self.messages.append({"role": "tool", "tool_call_id": rc["id"],
                                              "name": (rc.get("function") or {}).get("name", ""), "content": reason})
                return
            if m.get("role") == "user":
                return

    def _interject(self, step: int) -> None:
        """[tui-interject] 모델이 일하는 동안 사람이 친 글을 다음 요청 앞에 user 메시지로 넣는다(꾸밈 없이)."""
        extra = [t for t in self.ui.take_interjections() if t.strip()]
        if not extra:
            return
        text = "\n\n".join(extra)
        self.messages.append({"role": "user", "content": text})
        self.st["interjections"] = self.st.get("interjections", 0) + len(extra)
        self.rec.event("interject", step=step, n=len(extra), chars=len(text), content=text)
        self.rec.md(f"## 사람 (끼워 넣음, 단계 {step} 앞)\n\n```\n{text}\n```\n")

    def _call(self, step: int, mt: dict):
        """모델 요청 한 번. 문맥 넘침이면 한 번 접고(force, 최근 1단계만 원문) 같은 단계를 다시 보낸다.
        두 번째도 넘치거나 접을 것이 없으면 그 오류를 그대로 올린다(차례 끝 context_overflow).
        생각만 하다 상한에 닿아 빈 답이면(본문·툴콜 없음) 생각 끔 + 짧은 알림으로 한 번 다시 보낸다."""
        overflow_done = length_done = False
        mt = dict(mt)
        while True:
            streamed = False
            last = self.st["last_prompt_tokens"]
            est = (last + est_tokens(json.dumps(self.messages[self._pmark:], ensure_ascii=False)) if last
                   else est_tokens(json.dumps(self.messages, ensure_ascii=False)))
            kw = {**mt, **TH.call_kwargs(self.backend, self.tks, mt.get("thinking"), est)}   # 생각 켬 차례만
            try:
                if self.stream:
                    self.ui.stream_start()
                    try:
                        resp, dt = chat_stream(self.backend, self._clean(), self.tool_defs or None,
                                               on_delta=self.ui.stream_piece, **kw)
                    finally:
                        streamed = self.ui.stream_end()
                else:
                    resp, dt = chat(self.backend, self._clean(), self.tool_defs or None, **kw)
            except BackendError as e:
                c = self.comp.maybe(self.messages, self.st["last_prompt_tokens"], step, force=True, keep=1) \
                    if not overflow_done and is_context_overflow(e) else None
                if not c:
                    raise
                overflow_done = True
                self.st["compactions"] += 1
                self.st["overflow_retries"] = self.st.get("overflow_retries", 0) + 1
                self.rec.event("compact", overflow_retry=True, **c)
                self.ui.dim(f"  (문맥 넘침 → 오래된 도구 결과 {c['clipped']}개를 줄이고 다시 보낸다)")
                continue
            if length_done or not empty_length(resp):
                self._pmark = len(self.messages)
                return resp, dt, streamed
            length_done = True
            self.st["length_empty_retries"] = self.st.get("length_empty_retries", 0) + 1
            u0 = resp.get("usage") or {}
            self.st["model_time_s"] += dt
            self.st["completion_tokens"] += u0.get("completion_tokens") or 0
            self.rec.event("length_retry", step=step, elapsed_s=dt, usage=u0)
            self.ui.dim("  (생각만 하다 생성 상한에 닿아 답이 비었다 → 생각 끄고 같은 단계 다시)")
            self.messages.append({"role": "user", "content": LENGTH_NOTE})
            mt["thinking"] = False

    # ---- 한 차례 ---------------------------------------------------------
    def turn(self, text: str) -> str:
        """사람 입력 하나를 처리한다. 종료 사유: answered | asked | length_empty | max_steps | repeated_call | repeated_failure | backend_error | context_overflow | interrupted | consecutive_errors"""
        self.st["turns"] += 1
        cp = self.cps.begin(text.splitlines()[0] if text else "")
        self.rec.event("checkpoint", id=cp, turn=self.st["turns"])
        if self._skill_note:    # /skill 로 사람이 고른 스킬 본문(다음 사람 입력 앞에)
            text = self._skill_note + "\n\n" + text
            self._skill_note = None
        self._first_text = getattr(self, "_first_text", "") or text   # 색인용 첫 사람 입력(붙이기 전)
        if self._hook_ctx:   # SessionStart hook 출력은 첫 사람 입력 앞에 한 번
            text = HK.SESSION_CTX.format(ctx=self._hook_ctx) + text
            self._hook_ctx = ""
        self._hook_stops = 0
        if self._rewind_note:   # 되돌린 일을 모델에 알린다(다음 사람 입력 앞에)
            text = self._rewind_note + "\n\n" + text
            self._rewind_note = None
        if getattr(self, "_plan_exit", False):
            text = M.PLAN_EXIT + "\n\n" + text
            self._plan_exit = False
        if self.mode == "plan":   # 시스템 지시는 그대로 두고(cache) 사람 입력 앞에 붙인다
            text = M.PLAN_NOTE + "\n\n" + text
        if self._env_note:      # 세션 첫 입력 맨 앞에 환경 블록(시스템 지시문을 과제 사이에 같게)
            text = self._env_note + "\n\n" + text
            self._env_note = None
        self.messages.append({"role": "user", "content": text})
        self.rec.event("user", turn=self.st["turns"], content=text)
        self.rec.md(f"## 사람 (차례 {self.st['turns']})\n\n```\n{text}\n```\n")
        consecutive = 0
        reason = "max_steps"
        self._turn_calls: list[dict] = []   # /facts: 이번 차례의 도구 호출
        self._dg_budget = DG.Budget(self.max_steps, self.profile.delegate_budget)   # 작업자 단계 합 상한은 차례마다
        self.fail_count = {}          # 같은 호출 실패 세기는 차례마다 새로(사람이 "다시 해 봐" 하면 다시 센다)
        self._repeat_stop = False
        self.guard.reset()
        self._guard_hit = 0
        self._asked = None
        budget, n = self.max_steps, 0
        try:
            while True:
                if n >= budget:
                    if self._ask_more(budget):
                        budget += self.max_steps
                    else:
                        break
                n += 1
                self.st["steps"] += 1
                step = self.st["steps"]
                self._interject(step)   # [tui-interject] 앞 단계의 도구 결과 뒤·이 요청 전
                n_before = len(self.messages)
                streamed = False
                mt = {"max_tokens": self.profile.max_tokens} if self.profile.max_tokens else {}
                if self.think is not None:
                    mt["thinking"] = self.think
                try:
                    resp, dt, streamed = self._call(step, mt)
                except BackendError as e:
                    self.ui.warn(f"백엔드 오류: {e} {(e.body or '')[:300]}")
                    self.rec.event("step", step=step, error=str(e), status=e.status, body=(e.body or "")[:2000])
                    self.rec.md_step(step, {"elapsed_s": 0.0, "note": f"백엔드 오류 {e}"})
                    if e.status and "parse" in (e.body or "").lower():
                        self.st["parse_failures"] += 1
                        consecutive += 1
                        self.messages.append({"role": "user", "content": "도구 호출 형식이 깨졌다. 정해진 tool call 형식으로 다시 호출하라."})
                        if consecutive < self.profile.max_consecutive_errors:
                            continue
                        reason = "consecutive_errors"
                        break
                    if is_context_overflow(e):
                        reason = "context_overflow"
                        self.ui.warn("  문맥이 최대 길이를 넘었다. /compact 로 오래된 도구 결과를 줄이거나 /reset 으로 대화를 지운 뒤 이어 가라.")
                    else:
                        reason = "backend_error"
                    break
                self.st["model_time_s"] += dt
                choice = (resp.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                usage = resp.get("usage") or {}
                tim = resp.get("timings") or {}
                self.st["completion_tokens"] += usage.get("completion_tokens") or 0
                self.st["prompt_tokens_total"] += usage.get("prompt_tokens") or 0
                self.st["last_prompt_tokens"] = usage.get("prompt_tokens") or self.st["last_prompt_tokens"]
                done = self.comp.observe(usage.get("prompt_tokens"))
                if done:
                    self.rec.event("compact_effect", step=step, before_tokens=done["before_tokens"], after_tokens=done["after_tokens"])
                for k_src, k_dst in (("predicted_n", "pred_n"), ("predicted_ms", "pred_ms"), ("prompt_n", "prompt_n"), ("prompt_ms", "prompt_ms")):
                    self.st[k_dst] += tim.get(k_src) or 0
                content = msg.get("content") or ""
                calls = msg.get("tool_calls") or []
                srec = {"elapsed_s": dt, "content": content, "timings": tim, "usage": usage,
                        "finish_reason": choice.get("finish_reason"), "mode": None, "tool_calls": []}
                think = msg.get("reasoning_content") or msg.get("reasoning")
                if think:
                    srec["reasoning_content"] = think
                    self.ui.dim(f"  (생각 {len(think)}자)")
                if content.strip() and not streamed:
                    self.ui.assistant(content.strip() if not calls else _one_line(content, 300))
                if calls:
                    for rc in calls:
                        rc.setdefault("id", "call_" + uuid.uuid4().hex[:8])
                        rc.setdefault("type", "function")
                    self.messages.append({"role": "assistant", "content": content or None, "tool_calls": calls})
                    any_ok = False
                    for rc in calls:
                        tc = self._exec(rc, step)
                        srec["tool_calls"].append(tc)
                        self._turn_calls.append(tc)
                        any_ok = any_ok or tc["ok"]
                        self.messages.append({"role": "tool", "tool_call_id": rc["id"], "name": tc["name"],
                                              "content": tc["result"]})
                    consecutive = 0 if any_ok else consecutive + 1
                    self.rec.event("step", step=step, new_messages=self.messages[n_before:], response=srec,
                                   consecutive_errors=consecutive)
                    self.rec.md_step(step, srec)
                    if consecutive >= self.profile.max_consecutive_errors:
                        reason = "consecutive_errors"
                        break
                    if self._asked:   # 질문을 보이고 그 차례를 끝낸다(다음 사람 입력이 답)
                        self.messages.append({"role": "assistant", "content": self._asked})
                        self.rec.event("asked", step=step, question=self._asked)
                        self.ui.assistant("질문: " + self._asked)
                        reason = "asked"
                        break
                    if self._repeat_stop:
                        reason = "repeated_failure"
                        break
                    if self._guard_hit:
                        hit, self._guard_hit = self._guard_hit, 0
                        if self._ask_more(n, f"같은 호출을 결과가 같은 채로 {hit}번 되풀이했다", "repeated_call"):
                            self.guard.reset()
                        else:
                            reason = "repeated_call"
                            break
                    self.compact()
                    continue
                if empty_length(resp):   # 생각 끔으로 다시 보냈어도 빈 잘림 — 빈 답으로 받지 않는다
                    self.rec.event("step", step=step, new_messages=self.messages[n_before:], response=srec)
                    self.rec.md_step(step, srec)
                    self.ui.warn("  생각 끄고 다시 보냈어도 생성 상한에 닿아 답이 비었다. 다시 묻거나 /think off 로 바꿔 본다.")
                    reason = "length_empty"
                    break
                self.messages.append({"role": "assistant", "content": content})
                why = (self.hk.stop(content, step) if self.hk.has("Stop")
                       and self._hook_stops < self.profile.check_on_stop_max else None)
                if why is not None:   # [hooks] Stop exit 2: 끝내지 않고 까닭을 돌려준다(차례당 check_on_stop_max 번)
                    self._hook_stops += 1
                    self.messages.append({"role": "user", "content": HK.STOP_BLOCK.format(why=why)})
                    srec["note"] = "hook Stop 이 막음 → 까닭을 돌려주고 다시"
                    self.rec.event("step", step=step, new_messages=self.messages[n_before:], response=srec)
                    self.rec.md_step(step, srec)
                    self.ui.dim("  (hook Stop 이 막음 → 까닭을 모델에 돌려주고 이어 간다)")
                    continue
                self.rec.event("step", step=step, new_messages=self.messages[n_before:], response=srec)
                self.rec.md_step(step, srec)
                reason = "answered"
                break
        except KeyboardInterrupt as e:
            self.st["interrupts"] += 1
            self._close_open_calls("사용자가 중단해 실행하지 않았다.")
            partial = (getattr(e, "partial", None) or {}).get("content")
            if partial:   # 스트리밍 도중 끊음: 받은 데까지의 답을 남긴다(끊긴 툴콜은 실행하지 않고 버린다)
                self.messages.append({"role": "assistant", "content": partial + "\n[사람이 중간에 끊음]"})
                self.rec.event("partial", step=self.st["steps"], content=partial)
            self.messages.append({"role": "user", "content": "[사용자가 작업을 중단했다. 다음 입력을 기다려라.]"})
            self.messages.append({"role": "assistant", "content": "알겠다. 중단했다."})
            self.rec.event("interrupt", step=self.st["steps"])
            self.rec.md("> 사용자가 중단함(Ctrl-C)\n")
            self.ui.warn("\n  중단했다. 이어서 말하거나 /exit.")
            reason = "interrupted"
        if reason not in ("answered", "interrupted", "asked"):
            self.ui.warn(f"  차례 끝: {reason}")
        if reason == "max_steps":
            self.ui.warn(f"  이번 차례 단계 {n}개(상한 {self.max_steps}씩). 대화는 그대로다 — '계속' 이라고 치면 이어서 한다."
                         " 상한 바꾸기: /steps N")
        elif reason == "repeated_call":
            self.ui.warn("  같은 호출을 결과가 같은 채로 되풀이해 멈췄다. 방향을 알려 주거나 '계속' 이라고 친다.")
        if reason in ("max_steps", "consecutive_errors", "repeated_failure", "repeated_call", "backend_error", "context_overflow") \
                and self.cps.cur and self.cps.cur["files"]:
            self.ui.warn(f"  이번 차례에 바꾼 파일 {len(self.cps.cur['files'])}개는 그대로다. 되돌리려면 /undo(셸이 바꾼 것은 못 되돌림).")
        if reason == "repeated_failure":
            self.ui.warn(f"  똑같은 도구 호출이 {REPEAT_STOP}번 실패해 멈췄다. 방향을 알려 주거나 '계속' 이라고 친다.")
        self.ui.dim(f"  [{reason} · 단계 {self.st['steps']} · 프롬프트 {self.st['last_prompt_tokens']}/{self.backend.max_context} tok"
                    f" · 생성 합 {self.st['completion_tokens']} tok · 모델 {self.st['model_time_s']:.0f}초]")
        self.rec.event("turn_end", turn=self.st["turns"], reason=reason)
        self._last_reason = reason
        save_messages(self.rec.dir, self.messages)
        return reason

    def _ask_more(self, n: int, why: str | None = None, kind: str = "steps_limit") -> bool:
        """단계 상한(또는 반복 감지)에 닿았을 때 더 할지. 묻는 모드(edits·ask·plan)는 사람에게 묻고, auto 는 묻지 않고 끝낸다."""
        if self.mode == "auto":
            self.rec.event(kind, steps=n, asked=False, more=False)
            return False
        try:
            ans = self.ui.ask(f"  {why or f'단계 {n}개를 썼다(상한)'}. 계속할까? [y/n] ").strip().lower()
        except EOFError:
            ans = "n"
        more = ans in ("", "y", "yes", "ㅇ", "네", "계속")
        self.rec.event(kind, steps=n, asked=True, more=more)
        return more

    def plan_summary(self) -> str:
        """정리 계획(plan.json)의 요약과 두 파일 경로. 계획이 없으면 그렇다고 + 지금 모드."""
        from .organize.plan import is_unconfirmed
        path = Path(self.env.artifacts_dir) / "plan.json" if self.env.artifacts_dir else None
        plan, _ = T._plan(self.env) if path else (None, None)
        if plan is None:
            return f"정리 계획 없음(plan_add 를 아직 안 했다). 지금 모드 {self.mode} — 계획 모드 전환은 /plan on|off"
        items = plan.items
        moves = sum(1 for x in items if x["op"] == "move")
        dels = sum(1 for x in items if x["op"] == "to_delete")
        unc = sum(1 for x in items if is_unconfirmed(x))
        return (f"정리 계획 {len(items)}건: 옮기기 {moves} · _to_delete {dels} · 미확정 {unc}\n"
                f"  계획 파일 {path}\n  사람용 {path.with_suffix('.md')}\n"
                f"  (지금 모드 {self.mode} — 계획 모드 전환은 /plan on|off)")

    def facts_md(self) -> str:
        """/facts: 이번(또는 방금 끝난) 차례에 실제로 한 일."""
        f = FX.collect(getattr(self, "_last_reason", "진행 중"), getattr(self, "_turn_calls", []),
                       (self.cps.cur or {}).get("files", []), self.profile.workdir, getattr(self, "delegations", None))
        return FX.to_md(f).replace("# 실제로 한 일(엔진 기록)", f"# 차례 {self.st['turns']} 에 실제로 한 일(엔진 기록)", 1)

    def pick_skill(self, name: str) -> str:
        """/skill 이름: 본문을 다음 사람 입력 앞에 붙인다. 화면에 낼 말을 돌려준다(없는 이름이면 ValueError)."""
        ok, body = SK.load(self.skills, name, None, self.env.secret_globs, self.env.secret_allow)
        if not ok:
            raise ValueError(body)
        self._skill_note = f"[사람이 스킬 {name} 을 골랐다. 아래 본문을 따른다]\n{body}"
        self.skills_loaded.append(name)
        self.rec.event("skill_load", step=self.st["steps"], name=name, file=None, ok=True, by="user")
        return f"스킬 {name} 을 다음 입력에 붙인다"
    def _delegate(self, args: dict) -> tuple[bool, str]:
        """작업자 하나. 같은 화면에 작업자 도구 줄과 확인 질문을 "[작업자 N]" 머리말로 낸다."""
        def on_call(n, name, a):
            self.ui.tool(f"[작업자 {n}] {name}", _arg_summary(name, a))

        def ask(n, name, a):
            d = self._ask_permission(f"    [작업자 {n}] 실행할까? [y/n/모델에 할 말] ")
            if d:
                self.st["denied"] += 1
            return d
        ctx = {"profile": self.profile, "backend": self.backend, "rec": self.rec, "cps": self.cps, "env": self.env,
               "exec_mode": self.mode, "tool_names": [t for t in self.all_tool_names if t != DG.NAME], "ledger": self.ledger,
               "budget": self._dg_budget, "log": self.delegations, "ask": ask, "on_call": on_call,
               "ask_cfg": lambda n, name, a, path: self._ask_cfg(f"    [작업자 {n}]" + CG.ASK_PROMPT.format(path=path)[3:]),
               "bounds": self.bd,
               "ask_outside": lambda n, name, a, paths, kind: self._ask_outside(paths, kind, f"    [작업자 {n}] ")}
        self.ui.dim(f"  작업자에게 맡김: {_one_line(str(args.get('task') or ''), 120)}")
        ok, result = DG.run_child(ctx, args)
        if ok and self.delegations:
            for f in self.delegations[-1].get("files_written") or []:
                p = Path(f)
                self.st["files_changed"].add(str(p if p.is_absolute() else self.profile.workdir / p))
        return ok, result

    def rewind(self, to_id: int | None = None) -> dict:
        """파일을 체크포인트 to_id(없으면 파일을 바꾼 마지막 차례)가 시작될 때로 되돌린다. 대화는 그대로 두고, 다음 입력 앞에 알린다."""
        cps = self.cps.list()
        if to_id is None:
            changed = [c for c in cps if c["files"]]
            if not changed:
                raise ValueError("되돌릴 파일 변경이 없다")
            to_id = changed[-1]["id"]
        rep = self.cps.rewind(to_id)
        self.rec.event("rewind", **rep)
        files = rep["restored"] + rep["moved_aside"]
        self._rewind_note = (f"[사람이 파일을 체크포인트 {to_id}(차례 시작) 때로 되돌렸다. 되돌린 파일: "
                             + (", ".join(os.path.relpath(f, self.profile.workdir) for f in files) or "없음")
                             + ". 그 뒤 네가 한 파일 변경은 지금 없다. 필요하면 다시 읽어라.]")
        return rep

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": self.system}]
        self.comp = self._new_compactor()
        self.fail_count.clear()
        self.rec.event("reset")
        save_messages(self.rec.dir, self.messages)
        self.rec.md("> 대화 초기화(/reset)\n")

    def summary(self) -> dict:
        st = self.st
        return {
            "engine_version": __version__, "engine_git": self.meta["engine_git"], "profile": self.profile.name,
            "output_format": "free", "started": self.meta["started"], "mode": "chat", "exec_mode": self.mode,
            "backend": self.backend.name, "model": self.backend.model, "precision": self.backend.precision,
            "stop_reason": "chat_end", "turns": st["turns"], "steps": st["steps"], "elapsed_s": time.monotonic() - self.t0,
            "model_time_s": st["model_time_s"], "tool_calls": st["tool_calls"], "tool_errors": st["tool_errors"],
            "parse_failures": st["parse_failures"], "format_failures": 0, "denied": st["denied"],
            "interrupts": st["interrupts"], "compactions": st["compactions"], "compact_log": self.comp.log,
            "files_changed": sorted(st["files_changed"]), "skills_loaded": self.skills_loaded, "delegations": self.delegations,
            "ledger": self.ledger.brief() if self.ledger is not None else None,
            "completion_tokens": st["completion_tokens"], "prompt_tokens_total": st["prompt_tokens_total"],
            "last_prompt_tokens": st["last_prompt_tokens"],
            "gen_tok_per_s": (st["pred_n"] / (st["pred_ms"] / 1000)) if st["pred_ms"] else None,
            "prompt_tok_per_s": (st["prompt_n"] / (st["prompt_ms"] / 1000)) if st["prompt_ms"] else None,
            "e2e_tok_per_s": (st["completion_tokens"] / st["model_time_s"]) if st["model_time_s"] else None,
            "verify": None, "success": True, "run_dir": str(self.rec.dir),
        }

    def close(self) -> dict:
        shell_end = T.close_env(self.env)
        if shell_end:
            self.rec.event("shell_cleanup", **shell_end)
            if shell_end["cleaned_up"]:
                self.ui.dim(f"  남은 백그라운드 작업 정리: {', '.join(shell_end['cleaned_up'])}")
        s = self.summary()
        if self.mem is not None:   # 세션 끝에 엔진 자동 메모 + 색인 한 줄(과제 = 첫 사람 입력)
            try:
                first = getattr(self, "_first_text", "")
                n_auto = self.mem.auto_notes(s, None)
                self.mem.index_run(first, s, sorted(self.st.get("files_changed") or []))
                s["memory"] = {"notes_model": self.mem.notes_written, "notes_engine": n_auto, "injected": self.mem_inject}
            except OSError as e:
                s["memory"] = {"error": str(e)[:300]}
        self.rec.event("run_end", summary=s)
        self.rec.close(s, None)
        return s


MODE_HELP = ("모드 바꾸기: /mode auto|edits|ask|plan — auto=도구를 모두 바로 실행 · edits=파일 편집은 바로·셸은 묻기(chat 기본)"
             " · ask=읽기 말고는 모두 묻기 · plan=읽기만 하고 계획을 쓴다(/plan on|off 로도)")

# 등록된 명령(이름 → 쓰는 법 한 줄, 받는 인자 검사). 첫 낱말이 이 이름과 정확히 같을 때만 명령으로 본다.
_ONOFF = ("", "on", "off")
COMMANDS: dict[str, tuple[str, object]] = {
    "/mode": ("/mode [auto|edits|ask|plan] 지금 모드 보기·바꾸기", lambda a: a in ("",) + M.MODES),
    "/help": ("/help 명령 목록", lambda a: a == ""),
    "/exit": ("/exit 끝내기(/quit·/q 도 같다)", lambda a: a == ""),
    "/quit": ("/quit 끝내기", lambda a: a == ""),
    "/q": ("/q 끝내기", lambda a: a == ""),
    "/reset": ("/reset 대화 지우기", lambda a: a == ""),
    "/status": ("/status 상태", lambda a: a == ""),
    "/confirm": ("/confirm [on|off] 쓰기·셸 전에 묻기", lambda a: a in _ONOFF),
    "/plan": ("/plan 정리 계획 요약·파일 경로 · /plan on|off 계획 모드 켜고 끄기", lambda a: a in _ONOFF),
    "/stream": ("/stream [on|off] 응답을 조각으로 바로 보이기", lambda a: a in _ONOFF),
    "/think": ("/think [on|off|default] 모델 생각(thinking) 켜고 끄기", lambda a: a in ("", "on", "off", "default")),
    "/steps": ("/steps [N] 차례당 단계 상한(N 은 1 이상)", lambda a: a == "" or (a.isdigit() and int(a) > 0)),
    "/compact": ("/compact 오래된 도구 결과 줄이기", lambda a: a == ""),
    "/facts": ("/facts 이번 대화에서 실제로 한 일", lambda a: a == ""),
    "/undo": ("/undo 파일을 바꾼 마지막 차례 되돌리기", lambda a: a == ""),
    "/rewind": ("/rewind [N] 체크포인트 목록·N 번 차례 시작 때로 되돌리기", lambda a: a == "" or a.isdigit()),
    "/ledger": ("/ledger 관리자 장부 보기(-p manager)", lambda a: a == ""),
    "/skill": ("/skill [이름 [글]] 스킬 목록·고른 스킬 본문을 다음 입력(또는 글)에 붙이기", lambda a: True),
}

HELP = (MODE_HELP + "\n"
        "명령: /exit 끝내기 · /reset 대화 지우기 · /status 상태 · /confirm on|off 쓰기·셸 전에 묻기\n"
        "      /plan 정리 계획 요약·파일 경로 · /plan on|off · /stream on|off\n"
        "      /think on|off|default 모델 생각(thinking) 켜고 끄기 · /steps N 차례당 단계 상한(닿으면 계속할지 묻는다) · /compact 오래된 도구 결과 줄이기 · /help\n"
        "      /ledger 관리자 장부 보기(-p manager) · /skill [이름 [글]] 스킬 목록·고른 스킬 본문을 다음 입력(또는 글)에 붙이기\n"
        "      /undo 파일을 바꾼 마지막 차례 되돌리기 · /rewind [N] 체크포인트 목록·N 번 차례 시작 때로 되돌리기 · /facts 한 일\n"
        "/ 로 시작해도 첫 낱말이 위 명령 이름과 정확히 같을 때만 명령이다. /srv/… 같은 경로로 시작하는 글은 그대로 모델에게 간다.\n"
        "모델이 일하는 중 Ctrl-C = 그 차례 중단(대화는 이어짐). 입력에서 Ctrl-D = 끝내기.\n"
        "여러 줄: 줄 끝 \\ 로 잇기, 또는 \"\"\" 로 열고 닫기.")


def classify(text: str) -> tuple:
    """사람 입력이 명령인가. ("cmd", 이름, 인자) | ("usage", 이름) | ("typo", 비슷한 명령) | ("msg",).
    - 첫 낱말이 등록된 명령과 정확히 같으면 명령(인자가 틀리면 usage — 그 명령 쓰는 법 한 줄, 실행 안 함).
    - / 로 시작하지 않거나, 첫 낱말에 / 가 또 있으면(경로 /srv/…·/mnt/…) 메시지.
    - 등록 명령과 아주 비슷한 첫 낱말(/hlep)은 typo — 안내만 하고 모델에게도 보내지 않는다.
    - 그 밖(/로 시작하는 낯선 낱말)은 메시지."""
    import difflib
    t = text.strip()
    if not t.startswith("/"):
        return ("msg",)
    parts = t.split(None, 1)
    first, arg = parts[0], (parts[1].strip() if len(parts) > 1 else "")
    if first in COMMANDS:
        return ("cmd", first, arg) if COMMANDS[first][1](arg) else ("usage", first)
    if first == "/" or "/" in first[1:]:          # 경로(/srv/data·/mnt/…)는 글
        return ("msg",)
    near = difflib.get_close_matches(first.lower(), [c for c in COMMANDS if len(c) > 2], n=1, cutoff=0.75)
    return ("typo", near[0]) if near else ("msg",)


def read_input(ui: UI) -> str | None:
    try:
        first = ui.read_line("\n› ")
    except EOFError:
        return None
    if first.strip() == '"""':
        lines = []
        while True:
            try:
                l = ui.read_line("… ")
            except EOFError:
                break
            if l.strip() == '"""':
                break
            lines.append(l)
        return "\n".join(lines)
    lines = [first]
    while lines[-1].endswith("\\"):
        lines[-1] = lines[-1][:-1]
        try:
            lines.append(ui.read_line("… "))
        except EOFError:
            break
    return "\n".join(lines)


def repl(session: ChatSession, first: str | None = None) -> dict:
    ui = session.ui
    b = session.backend
    ui.info(f"stewart chat — {b.name} {b.model} ({b.precision}, ctx {b.max_context}) · 작업 폴더 {session.profile.workdir}")
    if session.ctx.get("warn"):
        ui.warn("  " + session.ctx["warn"])
    ui.dim(f"기록 {session.rec.dir} · /help")
    ui.info(f"모드 {session.mode} — 바꾸기: /mode {'|'.join(M.MODES)} (/help 맨 위에 모드 설명)")
    try:
        import readline  # noqa: F401  (입력 줄 편집·기록)
    except ImportError:
        pass
    pending = first
    try:
        while True:
            if pending is not None:
                text, pending = pending, None
            else:
                try:
                    text = read_input(ui)
                except KeyboardInterrupt:
                    ui.dim("  (Ctrl-D 또는 /exit 로 끝낸다)")
                    continue
                if text is None:
                    break
            t = text.strip()
            if not t:
                continue
            kind = classify(t)
            if kind[0] == "usage":
                ui.warn(f"쓰는 법: {COMMANDS[kind[1]][0]}")
                continue
            if kind[0] == "typo":
                ui.warn(f"혹시 {kind[1]}? (모델에게 보내지 않았다 · 명령 목록 /help)")
                continue
            if kind[0] == "cmd":
                cmd, arg = kind[1], kind[2]
                if cmd in ("/exit", "/quit", "/q"):
                    break
                if cmd == "/help":
                    ui.say(HELP)
                elif cmd == "/reset":
                    session.reset()
                    ui.info("대화를 지웠다.")
                elif cmd == "/confirm":
                    session.confirm = arg.strip() != "off"
                    ui.info(f"쓰기·셸 확인 {'켜짐' if session.confirm else '꺼짐'} (모드 {session.mode})")
                elif cmd == "/mode":
                    if arg.strip() in M.MODES:
                        msg = session.set_mode(arg.strip())
                        ui.info(f"모드 {session.mode}" + (f" — {msg}" if msg else ""))
                    else:
                        ui.info(f"모드 {session.mode}")
                        ui.dim("  " + MODE_HELP)
                elif cmd == "/plan" and not arg.strip():   # 정리 계획 요약 + 두 파일 경로(모드 전환은 /plan on|off)
                    ui.say(session.plan_summary())
                elif cmd == "/plan":
                    msg = session.set_mode("plan" if arg.strip() != "off" else "edits")
                    ui.info(f"모드 {session.mode}" + (f" — {msg}" if msg else " — 읽기만 하고 계획을 쓴다. 끝내려면 /plan off(편집 자동 허락으로)"))
                elif cmd == "/stream":
                    session.stream = arg.strip() != "off"
                    ui.info(f"스트리밍 {'켜짐' if session.stream else '꺼짐'}")
                elif cmd == "/steps":
                    if arg.strip().isdigit() and int(arg) > 0:
                        session.max_steps = int(arg)
                    ui.info(f"차례당 단계 상한 {session.max_steps}" + ("" if session.mode == "auto" else " (닿으면 계속할지 묻는다)"))
                elif cmd == "/think":
                    v = arg.strip()
                    if v in ("on", "off", "default"):
                        session.think = None if v == "default" else v == "on"
                        session.rec.event("think", value=session.think)
                    ui.info("생각(thinking) " + {None: "백엔드 기본", True: "켜짐", False: "꺼짐"}[session.think]
                            + " (바꾸기: /think on|off|default)")
                elif cmd == "/ledger":
                    ui.say(session.ledger.show() if session.ledger is not None else "장부 없음(관리자 프로필이 아니다: -p manager)")
                elif cmd == "/skill":
                    sname, _, rest = arg.strip().partition(" ")
                    if not sname:
                        ui.info("스킬: " + (", ".join(k.name for k in session.skills) or "없음") + " (쓰기: /skill 이름 [글])")
                        continue
                    try:
                        ui.info(session.pick_skill(sname))
                    except ValueError as e:
                        ui.warn(str(e))
                        continue
                    if rest.strip():
                        session.turn(rest.strip())
                elif cmd in ("/undo", "/rewind"):
                    if cmd == "/rewind" and not arg.strip():
                        for c in session.cps.list():
                            ui.say(f"  {c['id']:>3}  파일 {len(c['files'])}개  {c['label'][:70]}")
                        ui.dim("  /rewind N = N 번 차례가 시작될 때로 파일을 되돌린다. /undo = 파일을 바꾼 마지막 차례 되돌리기")
                        continue
                    try:
                        rep = session.rewind(int(arg) if arg.strip().isdigit() else None)
                    except ValueError as e:
                        ui.warn(str(e))
                        continue
                    ui.info(f"되돌림: 체크포인트 {rep['to']}~ · 되씌운 파일 {len(rep['restored'])} · 새로 생겼던 파일 옮김 {len(rep['moved_aside'])}"
                            f" · 지금 모습 보관 {rep['aside_dir']}")
                    if rep["outside_changes"]:
                        ui.warn(f"  도구가 쓴 뒤 바깥(셸 등)에서 또 바뀐 파일: {', '.join(rep['outside_changes'])}")
                    if rep["skipped"]:
                        ui.warn(f"  너무 커서 보관하지 못해 못 되돌린 파일: {', '.join(rep['skipped'])}")
                    ui.dim("  셸 명령이 바꾼 파일은 되돌리지 않는다.")
                elif cmd == "/compact":
                    ui.info(f"줄인 도구 결과 {session.compact(force=True)}개")
                elif cmd == "/facts":
                    ui.say(session.facts_md())
                elif cmd == "/status":
                    s = session.summary()
                    ui.say(json.dumps({k: s[k] for k in ("turns", "steps", "tool_calls", "tool_errors", "interrupts",
                                                         "last_prompt_tokens", "completion_tokens", "files_changed")},
                                      ensure_ascii=False, indent=1))
                continue
            session.turn(text)
    finally:
        s = session.close()
        ui.dim(f"\n기록: {session.rec.dir / 'transcript.md'}")
    return s
