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
import re
import os
import sys
import threading
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
from . import reflect as RF
from . import cutoff as CO
from . import intent as IN
from . import images as IMG
from . import manager as MG
from . import guard as G
from . import steer as ST
from . import forge as FG
from . import clarify
from . import envinfo
from . import skills as SK
from . import delegate as DG
from . import config_guard as CG
from . import projcfg as PC
from . import allowrules as AR
from . import components as CM
from . import train as TRAIN
from . import examples as EX
from . import spawn as SP
from .checkpoint import Checkpoints

WRITE_TOOLS = {"write_file", "edit_file", "multi_edit", "notebook_edit"}
CONFIRM_TOOLS = WRITE_TOOLS | {"run_shell"}
COMPACT_RATIO = 0.5    # 마지막 프롬프트 토큰이 max_context 의 이 비율을 넘으면 오래된 도구 결과를 줄인다(판 65: 0.75→0.5 — 긴 창에서 긴 문맥은 느리다)
SUMMARY_RATIO = 0.7    # 줄이고 나서도 이 비율을 넘으면 앞 대화를 요약 한 쪽으로 접는다(Claude Code 의 자동 compact 처럼, 판 65)
SUMMARY_KEEP_TURNS = 2 # 접을 때 원문 그대로 남기는 최근 사람 차례 수
SUMMARY_KEEP_STEPS = 10   # 차례가 셋이 안 되는 긴 대화(한 차례가 아주 긴 것)에서 원문 그대로 남기는 최근 단계 수
SUMMARY_MAX_CHARS = 60000
from .compact import SUMMARY_SYSTEM, summary_text, ask_summary   # noqa: E402 — 요약 접기 공용(판 66, 평가 길과 같은 것)
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


DIFF_KEEP = 2000   # 기록·화면에 남기는 diff 줄 상한(U2-5: 대화 안에서 펼쳐 본다)


def diff_stat(lines: list[str]) -> dict:
    """{"path", "plus", "minus"} — unified diff 줄에서 센다(머리 줄 +++/--- 는 빼고)."""
    path = next((l[6:] if l.startswith("+++ b/") else l[4:] for l in lines if l.startswith("+++ ")), "")
    plus = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    minus = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
    return {"path": path, "plus": plus, "minus": minus}


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


TODO_NUDGE_AFTER = 4     # 진행 중 항목이 있는데 도구 호출이 이만큼 지나도록 목록이 그대로면 한 번 끼운다(판 85)
_STEP_DONE = re.compile(r"(?:단계|step)\s*(\d+)\s*(?:을|를|은|는|이|가)?\s*(?:끝|마쳤|마침|완료|done|finished)|(\d+)\s*단계\s*(?:끝|완료|마침)", re.I)


def todo_nudge_on(env=None) -> bool:
    env = os.environ if env is None else env
    return env.get("STEWART_TODO_NUDGE", "1") != "0"


def TR_keep(mode: str, think, has_calls: bool) -> bool:
    from .toolrepair import keep_reasoning
    return keep_reasoning(mode, think, has_calls)


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


NEW_GOAL_NOTE = ("[엔진] 이 사람 말이 지금 목표다. 앞 차례의 미완 걸음·할 일 목록·되짚기 계획 중 이 말과 맞지 않는 것은 버리고, "
                 "할 일 목록이 있으면 이 말에 맞게 먼저 고쳐라(todo_write). 사람이 그만두라고 한 걸음으로 돌아가지 마라.")
SILENT_STEPS = 5
NARRATE_NOTE = "[엔진] 사람은 말 없이 도구만 도는 것을 보고 있다. 다음 답에 지금 무엇을 하고 있고 무엇을 알아냈는지 한 문장으로 먼저 말하고 이어 가라."
INTERJECT_NOTE = "[엔진] 사람이 일하는 중에 새로 말했다. 이 말이 지금 목표를 바꾸면 하던 걸음을 멈추고 이 말을 따른다."


class _LockedProxy:
    """여러 실(동시 작업자)이 같은 체크포인트를 쓸 때 부르는 메서드를 잠금 안에서(판 108)."""

    def __init__(self, obj, lock):
        self._o, self._l = obj, lock

    def __getattr__(self, k):
        v = getattr(self._o, k)
        if not callable(v):
            return v

        def f(*a, **kw):
            with self._l:
                return v(*a, **kw)
        return f


class ChatSession:
    def __init__(self, profile: Profile, backend: Backend, rec: Recorder, ui: UI | None = None, confirm: bool | None = None,
                 history: list[dict] | None = None, resumed_from: str | None = None, stream: bool = False,
                 mode: str | None = None, default_mode: str = "auto", think: bool | None = None,
                 extra_tools: tuple[list[dict], object] | None = None):
        self.profile, self.backend, self.rec = profile, backend, rec
        from .shell import hide_env
        hide_env(backend.api_key_env)          # 백엔드 키 변수는 모델 셸 환경에서 뺀다(판 69)
        from .backend import set_default_retries
        set_default_retries(4)                 # 실사용: 429·5xx·연결 끊김에 다시 보냄(평가 run 은 0 그대로, SW-34 ①)
        # 더 여는 도구(허브의 메인 세션 도구 등): (정의 목록, execute(name, args) -> (ok, 글)). delegate 처럼 확인 없이 돈다
        self.extra_defs, self.extra_exec = (list(extra_tools[0]), extra_tools[1]) if extra_tools else ([], None)
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
        if think is None:   # 판 116: --tier 등급의 think 칸(켬·끔). auto·없음 = 지금 그대로
            tv = (((profile.raw or {}).get("tier") or {}).get("think") or {}).get("think")
            self.think = tv if isinstance(tv, bool) else None
        web = web_search_config(profile, interactive=True)
        self.web_status = {"search": bool(web and web["url"]), "fetch": bool(web and web["fetch"]),
                           "why": (web or {}).get("why") or ("" if web else "웹 도구 꺼짐(검색 주소 없음·읽기 끔)")}
        base_names = [t for t in DG.other_tools(clarify.other_tools(profile.tools)) if t != SK.NAME]
        defs = T.definitions(base_names) if base_names else []
        if profile.tools and clarify.wanted(profile.tools):
            defs.append(clarify.DEFINITION)   # 되묻기: 질문을 보이고 그 차례를 끝낸다(다음 사람 입력이 답)
        if profile.tools and DG.wanted(profile.tools, 0):
            defs.append(DG.CHAT_DEFINITION)   # 작업자에게 맡기기(확인 질문은 같은 화면에 "[작업자 N]"). 기본 읽기 전용
            if SP.enabled():
                self.tiers = SP.load_tiers()  # 작업자 등급(판 110): backends 표 [tiers.*]
                defs.append(SP.definition(self.tiers))   # 동시 작업자(판 108, SW-43): 띄우고 바로 돌아옴, 걸음 사이 끝 알림, 끝에서 wait
        if not web:
            defs = [d for d in defs if d["function"]["name"] not in T.WEB_TOOLS]
        elif not web["url"]:   # 검색 주소가 없으면 읽기(web_fetch)만
            defs = [d for d in defs if d["function"]["name"] != "web_search"]
        if ((profile.tools == ["*"] or "doc_search" in profile.tools) and os.environ.get("STEWART_DOC_SEARCH") != "0"
                and "doc_search" not in [d["function"]["name"] for d in defs]):   # 문서 찾기(판 64): 실사용 기본 켬
            defs += T.definitions(["doc_search"])
        if not backend.vision:   # 이미지 보기는 이미지를 받는 백엔드에서만(실사용 기본 도구 목록은 그대로)
            defs = [d for d in defs if d["function"]["name"] != "view_image"]
        self.pfl = PC.flags(profile, interactive=True)   # 대화형: Claude Code 꼴 호환 기본 켬(사용자 전역 ~/.claude 는 기본 끔)
        self.skills = SK.discover(profile.workdir, self.pfl, builtin=SK.builtin_on(True)) if (profile.skills is not False and profile.tools) else []   # 세션 시작 때 한 번
        # 프로젝트 구성: .stewart/commands·agents(호환 .claude/…), 세션 시작 때 한 번
        self.commands = PC.discover_commands(profile.workdir, self.pfl, set(COMMANDS))
        self.agents = PC.discover_agents(profile.workdir, self.pfl) if profile.tools else []
        if self.skills:
            defs.append(SK.DEFINITION)
        self.mem = MEM.Memory(profile, rec.dir) if MEM.enabled(profile, interactive=True) else None   # 기억 M1(chat·허브 기본 켬)
        if self.mem is not None and profile.tools:
            defs += MEM.tool_defs()
        self.forge = None
        if profile.tools:   # 원격 저장소 설정(hub.toml [forge], 판 48): 있으면 도구 둘 + 지시 한 단락 + 셸 막이
            try:
                self.forge = FG.load()
            except FG.ForgeError as e:
                self.ui.warn(f"  forge 설정을 쓰지 않는다: {e}")
        if self.forge is not None:
            defs += FG.DEFINITIONS
        defs += self.extra_defs
        self.extra_names = {d["function"]["name"] for d in self.extra_defs}
        self.all_tool_names = [d["function"]["name"] for d in defs
                               if d["function"]["name"] not in MEM.NAMES and d["function"]["name"] not in self.extra_names]   # 작업자에게 넘길 수 있는 것
        if profile.manager_tools is not None:   # 관리자 모드: 모델에 보이는 자기 도구만
            if MG.NAME in profile.manager_tools:
                defs.append(MG.DEFINITION)
            defs = [d for d in defs if d["function"]["name"] in profile.manager_tools]
        # 허브 켬·끔 손잡이(판 99): 프로필 toggle_tools 의 도구는 사람이 켤 때만 모델에게 보인다(기본 끔, toggle_on 에 적으면 켬)
        self.toggleable = [n for n in (profile.raw or {}).get("toggle_tools") or [] if any(d["function"]["name"] == n for d in defs)]
        from . import slim as SL
        self.slim = SL.conf(profile)   # 판 113: 얇은 허브 메인 — 핵심 도구 밖은 켬·끔 칩(기본 끔)으로
        if self.slim is not None:
            kp = SL.keep(self.slim)
            self.toggleable += [d["function"]["name"] for d in defs
                                if d["function"]["name"] not in kp and d["function"]["name"] not in self.toggleable]
        self.disabled_tools = {n for n in self.toggleable if n not in ((profile.raw or {}).get("toggle_on") or [])}
        self._all_defs = defs
        defs = [d for d in defs if d["function"]["name"] not in self.disabled_tools]
        self.tool_defs = defs
        self.tool_names = [d["function"]["name"] for d in defs]
        self.ledger = MG.Ledger(rec.dir / "ledger.json", "(대화)") if MG.NAME in self.tool_names else None
        self.env = T.ToolEnv(workdir=profile.workdir, shell_timeout_s=profile.shell_timeout_s, web=web,
                              artifacts_dir=rec.dir, **T.secret_options(profile))
        self.env.vision = bool(backend.vision)
        from . import webnudge as WN
        self.dnudge = DG.Nudge(on=DG.nudge_on() and DG.NAME in self.tool_names)   # 맡기기 넛지(판 97)
        self.nudge = WN.Nudger(search=self.web_status["search"] and "web_search" in self.tool_names,
                               fetch=self.web_status["fetch"] and "web_fetch" in self.tool_names, on=WN.enabled())
        from . import toolrepair as TR   # 견고성(판 81): 본문 툴콜·깨진 인자 복구·편집 되먹임(실사용 켬), 생각 되돌려 보내기(기본 끔)
        self.repair_on = TR.on("STEWART_TOOLCALL_REPAIR", True)
        self.edit_fb = TR.on("STEWART_EDIT_FEEDBACK", True)
        self.passback = TR.passback()
        if self.mem is not None and self.mem.md():   # 자료 기억(판 83)은 doc_search(source="memory")로 찾는다
            self.env.memory_knowledge_dir = self.mem.store.dir / "knowledge"
        if self.mem is not None:   # 웹 글 캐시(판 64): 받은 본문을 기억 폴더에 남겨 doc_search 로 다시 찾게(30일·200개)
            self.env.web_cache_dir = self.mem.dir / "web-cache"
        self.check = load_check(profile.action_check)
        mem_text, mem_log = memory_for(profile, interactive=True)
        self.env_info = envinfo.measure(profile.workdir)   # 세션 시작 때 한 번(이어 하기는 새로 잰다)
        system = profile.system.strip() + (f"\n\n{mem_text}" if mem_text else "") \
            + (f"\n\n{SL.skills_text(self.skills) if self.slim is not None else SK.list_text(self.skills)}" if self.skills else "") \
            + (f"\n\n{PC.agents_text(self.agents)}" if self.agents else "")
        self.mem_inject: list = []
        if self.mem is not None:   # L3 주입
            mtext, self.mem_inject = self.mem.inject()
            system += "\n\n" + mtext
        self._env_note: str | None = (envinfo.user_note(self.env_info)   # 다이어트 안 a: 이 세션 첫 사람 입력 앞에 붙인다
                                      if os.environ.get("STEWART_ENV_BLOCK", "1") != "0" else None)   # 판 100: 끄는 손잡이
        if self.forge is not None and not (self.slim is not None and "forge_remote" in self.disabled_tools):
            system += "\n\n" + FG.note(self.forge)
        if profile.language == "ko":
            system += "\n\n사람에게 하는 말은 한국어로 쓴다. 코드·파일 이름·명령은 원문 그대로 둔다."
        self.system = system
        self.messages: list[dict] = prepare(history, system) if history else [{"role": "system", "content": system}]
        self.bd = BND.from_profile(profile)   # 작업 폴더 경계(밖 쓰기는 모드와 관계없이 묻는다)
        self.tks = TH.settings(profile)   # 생각 켬 차례의 상한·샘플링 손잡이
        self._pmark = 0                   # 마지막 응답 때의 메시지 수(프롬프트 어림용)
        self.summary_on = os.environ.get("STEWART_SUMMARY") != "0"   # 요약 접기 자동(판 65, chat·허브). 시험 묶음은 끔
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
        self._guard_tc = None
        self._blocked = {}
        self._last_reflect = ""
        self.bans: list = []              # 되짚기 결론으로 건 금지(세션 동안 — 사람이 화면에서 푼다, steer.py)
        self._last_reason = None
        self._asked: str | None = None
        self.rf = RF.settings(profile, interactive=True)   # 실패 문턱에서 도구 없는 되짚기(기본 켬, STEWART_REFLECT=0 이면 끔)
        self._err_sigs, self._human_noted, self._samerr_hit = {}, set(), None
        self._rstate = RF.State()
        self._fail_steps: list[list[dict]] = []
        self._recent_fail: list[dict] = []
        self._reflect_log: list[dict] = []   # 차례마다 되짚기 요약(했거나 건너뛴 차례만)
        # 이 세션에서 사람이 한 번 허용한 셸 명령(글자 그대로 같은 것만 다시 묻지 않는다). STEWART_ASK_SAME_COMMAND=1 이면 늘 묻는다
        self._approved_cmds: set[str] = set()
        self._same_cmd_on = os.environ.get("STEWART_ASK_SAME_COMMAND") != "1"
        self.t0 = time.monotonic()
        self.meta = {
            "engine": "stewart", "engine_version": __version__, "engine_git": _git_rev(), "mode": "chat",
            "profile": profile.name, "profile_path": str(profile.path), "backend": backend.public(),
            "workdir": str(profile.workdir), "tools": self.tool_names, "max_steps": self.max_steps,
            "thinking": self.think, "max_consecutive_errors": profile.max_consecutive_errors, "output_format": "free",
            "action_check": profile.action_check or "stewart.check:allow_all", "exec_mode": self.mode,
            "memory_files": mem_log, "env_info": self.env_info, "skills": SK.meta_log(self.skills),
            **({"project_config": {"commands": [{"name": c.name, "scope": c.scope, "origin": c.origin, "path": str(c.path),
                                                 "shadowed": c.shadowed,
                                                 **({"ignored_keys": c.ignored_keys} if c.ignored_keys else {})}
                                                for c in self.commands],
                                   "agents": PC.agents_log(self.agents)}} if (self.commands or self.agents) else {}),
            "resumed_from": resumed_from, "resumed_messages": len(self.messages) - 1 if history else 0,
            "started": datetime.now().isoformat(timespec="seconds"),
        }
        from . import features as FT
        self.meta["features"] = FT.fingerprint(self.messages[0].get("content") if self.messages and self.messages[0].get("role") == "system" else "",
                                               self.tool_defs, "chat", {"toggled_off": sorted(getattr(self, "disabled_tools", ()))})
        rec.event("run_start", meta=self.meta, messages=self.messages)
        if history:
            n_user = sum(1 for m in self.messages if m.get("role") == "user")
            self.ui.dim(f"이어 감: {resumed_from} (메시지 {len(self.messages) - 1}개, 사람 입력 {n_user}개)")
        save_messages(rec.dir, self.messages)
        if mem_log:
            self.ui.dim("프로젝트 기억 파일: " + ", ".join(f"{m['path']} ({m['used']}자{', 잘림' if m['truncated'] else ''})"
                                                          for m in mem_log if "used" in m))
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
        # 셸 허용 규칙(사용자 rules.toml·저장소 deny/ask·허브 "늘 허용"). 셸 확인 자리에서만 쓴다
        self.rules = AR.RuleSet(profile.workdir, compat=self.pfl.compat, hub_rules=getattr(self.ui, "hub_rules", None))
        self.comps = CM.Components(rec)
        self._ttft: float | None = None
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
            if name == "run_shell":   # auto 에서도 deny 규칙은 건다(allow·ask 는 auto 에 뜻이 없다)
                verdict, text = self.shell_rule(args, deny_only=True)
                if verdict == "deny":
                    return text
            return None
        cmd = str(args.get("command") or "") if name == "run_shell" else None
        if name == "run_shell":
            verdict, text = self.shell_rule(args)
            if verdict == "allow":
                return None
            if verdict == "deny":
                return text
            if self._same_cmd_on and cmd in self._approved_cmds:   # 같은 명령을 이 세션에서 이미 허용했다
                self.st["same_cmd_skips"] = self.st.get("same_cmd_skips", 0) + 1
                self.rec.event("same_command_allowed", step=self.st["steps"], chars=len(cmd))
                self.comps.fire("allow_rule", self.st["steps"], "shell_confirm", "allow", rule="same_command", source="session")
                self.ui.dim("    (이 세션에서 허용한 같은 명령이라 묻지 않음)")
                return None
        d = self._ask_permission("    실행할까? [y/n/모델에 할 말] ")
        if d is None and cmd:
            self._approved_cmds.add(cmd)
        return d

    def shell_rule(self, args: dict, deny_only: bool = False) -> tuple[str | None, str]:
        """셸 허용 규칙. ("allow", "") | ("deny", 까닭) | (None, "") — None 이면 지금처럼 묻는다.
        deny_only(auto 모드): deny 만 보고 나머지는 (None, "")."""
        cwd = getattr(self.env.shell, "cwd", None) if self.env.shell is not None else None
        ctx = AR.Ctx(self.profile.workdir, Path(cwd or self.profile.workdir), self.env.is_secret,
                     allow_redirect=self.mode == "edits")   # 작업 폴더 안 > 는 편집을 묻지 않는 edits 에서만
        r = self.rules.decide(str(args.get("command") or ""), ctx)
        if r.effect not in ("allow", "deny") or (deny_only and r.effect != "deny"):
            return None, ""
        self.comps.fire("allow_rule", self.st["steps"], "shell_confirm", "allow" if r.effect == "allow" else "block",
                        rule=":".join(r.rule_ids[:3]), source=":".join(r.sources), pieces_ok=r.effect == "allow")
        if r.effect == "allow":
            self.ui.dim(f"    (허용 규칙으로 실행: {', '.join(r.rule_ids[:3])})")
            return "allow", ""
        return "deny", f"셸 규칙(deny {r.rule_ids[0]})이 이 명령을 막았다. 다른 방법을 쓰거나 사용자에게 물어라."

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

    # ---- 할 일 목록이 걸음을 따라오게(판 85): 실행기가 대신 체크하지는 않는다(틀리게 표시하는 것이 더 나쁘다) ----
    def _prefix_check(self, step: int) -> None:
        """판 86: 요청 앞부분(지시문 + 도구 정의)이 단계마다 바이트 그대로인가 — 접두 캐시(DeepSeek 등)가 맞으려면 같아야 한다.
        바뀌면 사건 prefix_changed 와 st["prefix_changes"] 로 센다(바뀐 것이 잘못은 아니다: 압축·도구 목록 바꿈 뒤에는 바뀐다)."""
        import hashlib
        sysm = self.messages[0].get("content") if self.messages and self.messages[0].get("role") == "system" else ""
        h = hashlib.sha256((json.dumps(sysm, ensure_ascii=False) + json.dumps(self.tool_defs or [], ensure_ascii=False)).encode()).hexdigest()[:16]
        prev = getattr(self, "_prefix_h", None)
        self._prefix_h = h
        if prev is not None and prev != h:
            self.st["prefix_changes"] = self.st.get("prefix_changes", 0) + 1
            self.rec.event("prefix_changed", step=step)
        if getattr(self, "_defs_h", None) != (dh := hashlib.sha256(json.dumps(self.tool_defs or [], ensure_ascii=False).encode()).hexdigest()[:16]):
            self._defs_h = dh   # 학습 내보내기(판 90): 그 걸음에 실제로 보낸 도구 정의 — 바뀔 때만 남긴다
            self.rec.event("tool_defs", step=step, hash=dh, defs=self.tool_defs or [])

    def _todo_open(self) -> list:
        return [t for t in (getattr(self.env, "todos", None) or []) if t.get("status") != "done"]

    def _todo_watch(self, step: int, calls: list) -> None:
        if not todo_nudge_on() or "todo_write" not in self.tool_names:
            return
        if any(c.get("name") == "todo_write" and c.get("ok") for c in calls):
            self._todo_since = 0
            return
        self._todo_since += len(calls)
        doing = [t["content"] for t in self._todo_open() if t.get("status") == "in_progress"]
        if not doing or self._todo_since < TODO_NUDGE_AFTER or self._todo_since - self._todo_nudged_at < TODO_NUDGE_AFTER:
            return
        self._todo_nudged_at = self._todo_since
        RF.append_user(self.messages, f"[엔진] 할 일 목록이 도구 {self._todo_since}번 동안 그대로다(지금 '{doing[0][:60]}' 진행 중). "
                                      "그 항목을 끝냈으면 todo_write 로 done 으로, 다음 항목을 in_progress 로 고쳐라 — 사람 화면이 이 목록을 본다.")
        self.rec.event("todo_nudge", step=step, since=self._todo_since)
        self.comps.fire("todo_nudge", step, "stale_list", "nudge", since=self._todo_since)

    def _todo_final(self, content: str, step: int) -> bool:
        """답(물음 포함)을 내기 전: 이번 차례에 도구를 썼는데 목록이 그 뒤로 안 바뀌었거나, 답에 "단계 N 끝" 인데 목록의 N 이 안 끝났으면
        한 번 갱신 차례를 준다(같은 답을 다시 쓰지 말라고). 해야 하면 True."""
        if not todo_nudge_on() or self._todo_final_done or "todo_write" not in self.tool_names:
            return False
        todos = list(getattr(self.env, "todos", None) or [])
        if not todos or not self._todo_open():
            return False
        why = []
        if self._todo_since > 0 and self._turn_calls:
            why.append(f"이 차례에 도구를 {self._todo_since}번 쓴 뒤로 목록이 그대로다")
        for m in _STEP_DONE.finditer(content or ""):
            k = int(m.group(1) or m.group(2))
            if 1 <= k <= len(todos) and todos[k - 1].get("status") != "done":
                why.append(f"답에는 단계 {k} 를 끝냈다고 했는데 목록의 {k}번('{todos[k - 1]['content'][:40]}')은 아직 {todos[k - 1].get('status')}")
                break
        if not why:
            return False
        self._todo_final_done = True
        RF.append_user(self.messages, "[엔진] 사람에게 보이기 전에 할 일 목록을 지금 상태로 맞춰라: " + "; ".join(why)
                       + ". todo_write 로 끝낸 항목은 done, 지금 항목은 in_progress 로 고치고, 앞 답은 이미 사람에게 갔으니 다시 쓰지 말고 짧게 끝낸다.")
        self.rec.event("todo_final_nudge", step=step, why=why)
        self.comps.fire("todo_nudge", step, "before_answer", "nudge", reasons=len(why))
        return True

    def _exec(self, rc: dict, step: int) -> dict:
        fn = rc.get("function") or {}
        name, raw = fn.get("name", ""), fn.get("arguments") or "{}"
        raw_s = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        tc = {"id": rc["id"], "name": name, "raw_arguments": raw_s}
        self.st["tool_calls"] += 1
        try:
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
                if not isinstance(args, dict):
                    raise ValueError("인자가 객체가 아니다")
            except Exception:
                from . import toolrepair as TR
                fixed = TR.repair_args(raw) if self.repair_on and not getattr(self, "_cut", False) else None
                if fixed is None:
                    raise
                args = fixed      # 깨진 인자 JSON 을 한 번 고쳐 읽었다(가-2) — 잘린 인자는 고치지 않는다
                tc["args_repaired"] = True
                self.st["toolcall_repaired"] = self.st.get("toolcall_repaired", 0) + 1
                self.rec.event("broken_toolcall", step=step, form="args_json", repaired=True, name=name, raw=raw_s[:2000])
                self.comps.fire("broken_toolcall", step, "args_json", "rewrite", repaired=True, how="json_repair")
        except Exception as e:
            cut = getattr(self, "_cut", False)   # 생성 상한에서 끊긴 응답의 툴콜(cutoff.py)
            tc.update(parse_error=str(e), ok=False, elapsed_s=0.0, cut=cut or None,
                      result=(CO.cut_note(self.profile, self.backend, name, raw_s) if cut
                              else f"인자 JSON 파싱 실패: {e}. 올바른 JSON 객체로 다시 호출하라."))
            if cut:
                self.st["cut_toolcalls"] = self.st.get("cut_toolcalls", 0) + 1
                self.rec.event("toolcall_cut", step=step, name=name, chars=len(raw_s))
            self.st["parse_failures"] += 1
            self.ui.tool(name, f"인자 파싱 실패: {e}")
            return tc
        self.ui.tool(name, _arg_summary(name, args))
        if name + "\0" + raw_s in self._blocked:   # 되짚기 뒤 같은 호출: 돌리지 않는다(결과는 앞과 같다)
            tc.update(ok=False, result=RF.BLOCKED_NOTE, elapsed_s=0.0, blocked=True)
            self.ui.warn("    되짚기 뒤 같은 호출이라 돌리지 않았다")
            return tc
        cmd = str(args.get("command") or "") if name == "run_shell" else None
        ban = next((b for b in self.bans if b.matches(name, raw_s, cmd)), None)
        if ban is not None:   # 되짚기에서 다시 하지 않기로 한 호출(같은 프로그램·같은 대상이면 글자가 달라도)
            ban.hits += 1
            tc.update(ok=False, elapsed_s=0.0, banned=ban.id,
                      result=f"[엔진] 되짚기에서 다시 하지 않기로 한 호출이다({ban.text}). 돌리지 않았다 — 다른 길로 가라. "
                             "이 걸음이 목표에 꼭 필요하지 않으면 건너뛰고 본 목표로 가라.")
            self.ui.warn(f"    되짚기에서 금지한 호출이라 돌리지 않았다: {ban.text[:80]}")
            return tc
        if cmd and self.forge is not None:
            why = FG.outside(self.forge, cmd, Path(self.profile.workdir))
            if why:   # forge 밖으로 올리기·원격 바꾸기
                tc.update(ok=False, elapsed_s=0.0, denied=True, result=FG.BLOCK_NOTE.format(base=self.forge.base, why=why))
                self.ui.warn(f"    forge 밖이라 돌리지 않았다: {why}")
                return tc
        if cmd:
            sec = ST.secret_reads(cmd, lambda n: ST.is_secret_name(n, self.env.secret_globs, self.env.secret_allow))
            if sec and not ST.user_named(getattr(self, "_raw_text", ""), sec):
                ok_read = False
                if self.mode != "auto":
                    try:
                        ans = self.ui.ask(f"  비밀 꼴 파일({', '.join(sec[:3])})의 내용을 셸로 읽으려 한다: {cmd[:160]}\n"
                                          "  기본은 읽지 않는다(이름·크기만). 이번만 읽게 할까? [y/n/모델에 할 말] ").strip()
                    except EOFError:
                        ans = "n"
                    ok_read = ans.lower() in ("y", "yes", "ㅇ", "네")
                if not ok_read:
                    tc.update(ok=False, elapsed_s=0.0, denied=True, secret=True,
                              result=f"[엔진] {', '.join(sec[:3])} 은 비밀 꼴 파일이다. {ST.NAMES_ONLY_HINT}")
                    self.ui.warn(f"    비밀 꼴 파일 내용 읽기라 돌리지 않았다: {', '.join(sec[:3])}")
                    return tc
        if name in getattr(self, "disabled_tools", ()):
            tc.update(ok=False, elapsed_s=0.0, result=f"[엔진] {name} 도구는 지금 꺼져 있다 — 사람이 허브에서 켜야 쓸 수 있다. 다른 길로 하거나 사람에게 켜 달라고 말한다.")
            self.ui.warn(f"    꺼진 도구: {name}")
            return tc
        if name not in self.tool_names:
            tc.update(ok=False, result=T.unknown_tool(name, self.tool_names), elapsed_s=0.0)
            self.ui.warn("    없는 도구")
            return tc
        if name == clarify.NAME:
            q = clarify.question_text(args)
            if not q:
                tc.update(ok=False, result="question 이 비었다. 무엇을 묻는지 적어 다시 호출하라.", elapsed_s=0.0)
                return tc
            if not self._asked:
                self._asked_opts = [str(o).strip()[:200] for o in (args.get("options") or []) if str(o).strip()][:8]   # 판 106: 화면 단추
            self._asked = self._asked or q
            tc.update(ok=True, result=clarify.TOOL_RESULT, elapsed_s=0.0, question=q)
            return tc
        if name in FG.NAMES and self.forge is not None:   # 원격 저장소(forge) 도구
            if self.mode == "plan":
                tc.update(ok=False, result="계획 모드에서는 원격을 걸거나 협력자를 넣지 않는다", elapsed_s=0.0)
                return tc
            ok, result = FG.execute(self.forge, self.profile.workdir, name, args)
            self.rec.event("forge_tool", step=step, name=name, ok=ok)
            self.ui.dim(f"    {'✓' if ok else '✗'} {_one_line(result, 160)}")
            tc.update(ok=ok, result=result, elapsed_s=0.0)
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
        if name == SP.NAME:
            ok, result = self._workers(args)
            self.ui.dim(f"    {'✓' if ok else '✗'} {_one_line(result.splitlines()[0] if result else '', 160)}")
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
        if self.extra_exec is not None and name in self.extra_names:
            ok, result = self.extra_exec(name, args)
            self.ui.dim(f"    {'✓' if ok else '✗'} {_one_line(result, 160)}")
            if not ok:
                self.st["tool_errors"] += 1
            tc.update(ok=ok, result=result, elapsed_s=0.0)
            return tc
        call = ToolCall(id=rc["id"], name=name, arguments=args, raw_arguments=raw_s)
        if hasattr(self.ui, "call_context"):   # 허브 작업자 화면: 묻기 전에 어떤 호출인지 넘긴다(판정은 그대로)
            self.ui.call_context(name, args)
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
            if ok and self.edit_fb and name in ("edit_file", "write_file", "multi_edit"):   # 고친 자리·문법 한 줄(가-3)
                from . import toolrepair as TR
                fb = TR.edit_feedback(self.profile.workdir, name, args)
                if fb:
                    result += fb
                    tc["edit_feedback"] = True
                    bad = "오류 —" in fb
                    self.comps.fire("edit_feedback", step, "syntax_error" if bad else "edit_ok", "nudge", syntax_error=bad)
            post = self.hk.post(name, args, ok, result, step) if self.hk.has("PostToolUse") else None
            if post is not None:
                tc["hook_note"] = True
                result += HK.POST_NOTE.format(why=post)
        tc["elapsed_s"] = time.monotonic() - t1
        if name == "run_shell" and not ok and ST.soft_exit(str(args.get("command") or ""), result):
            ok = True                      # 출력은 받았고 마지막 조건식 거짓·grep 무매치라 1 — 실패로 세지 않는다
            result += ST.SOFT_NOTE
            tc["soft_exit"] = True
        if not ok:
            sig = name + "\0" + raw_s
            self.fail_count[sig] = self.fail_count.get(sig, 0) + 1
            if self.fail_count[sig] >= REPEAT_NUDGE:
                tc["repeat"] = self.fail_count[sig]
                result += (f"\n\n[엔진] 똑같은 호출이 {self.fail_count[sig]}번 실패했다. 되풀이하지 말고 현재 상태를 확인하거나 "
                           "다른 방법을 써라.")
                if self.fail_count[sig] >= REPEAT_STOP:
                    self._repeat_stop = True
        if name == "run_shell" and not ok and not tc.get("denied"):   # 같은 오류 되풀이·사람 경계(판 62)
            result += self._err_watch(str(args.get("command") or ""), result, tc)
        same = self.guard.observe(name, raw_s, ok, result)
        g_note = self.guard.note(same)
        if g_note is None and same == 2 and getattr(self.ui, "goal_notes", False):   # 허브: 두 번째부터 "이미 받은 결과"
            g_note = G.EARLY_NOTE
        if g_note:
            if same >= G.STRONG:   # chat 은 도구를 빼지 않고 사람에게 묻는다
                g_note = G.NOTE.format(n=same)
            result += "\n\n" + g_note
            tc["guard"] = same
            if same >= G.REFLECT_AT:     # 같은 호출·같은 결과: 되짚기로(남은 되짚기가 없으면 G.STOP 에서 사람에게)
                if same >= G.STOP or (self.rf["on"] and self._rstate.used < self.rf["max"]):
                    self._guard_hit = same
                    self._guard_tc = tc
        if self.mem is not None:   # 기억 대상 가르기·차례 셸 메모(판 54) — 깨져도 도구 흐름은 그대로
            try:
                result += self._mem_seen(name, args, ok, result)
            except Exception:
                pass
        tc.update(ok=ok, result=result)
        if target is not None and ok:
            after = _read(target)
            d = make_diff(before, after, os.path.relpath(target, self.profile.workdir) if target.is_absolute() else str(target),
                          max_lines=DIFF_KEEP)
            tc["diff"] = "\n".join(d)
            tc["diff_stat"] = diff_stat(d)
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
            from .engine import _error_kind   # 판 114: run 길과 같은 오류 갈래(edit_mismatch 등) — 평가기가 chat 길도 같은 칸으로 읽게
            k = _error_kind(name, tc, result)
            self.st.setdefault("tool_error_kinds", {})[k] = self.st.setdefault("tool_error_kinds", {}).get(k, 0) + 1
        self.ui.tool_result(tc)
        return tc

    # ---- 문맥 줄이기 -----------------------------------------------------
    def compact(self, force: bool = False, estimate: bool = False) -> int:
        """문턱을 넘었으면(또는 /compact) 아직 안 줄인 앞쪽 덩어리를 한 번에 줄인다(compact.Compactor). 줄인 메시지 수.
        줄인 뒤 새로 잰 문맥이 아직 SUMMARY_RATIO 를 넘으면(실사용) 앞 대화를 요약으로 접는다(summarize)."""
        last = self.st["last_prompt_tokens"]
        if estimate and not last:   # 이어 받은 대화의 첫 차례처럼 아직 잰 값이 없으면 어림으로 판정(SW-19)
            last = est_tokens(json.dumps(self.messages, ensure_ascii=False))
        c = self.comp.maybe(self.messages, last, self.st["steps"], force=force)
        if not c:
            win = getattr(self.comp, "full_context", None) or self.backend.max_context
            done = None
            if (self.summary_on and not force and last and win and last >= SUMMARY_RATIO * win
                    and self.st["steps"] > getattr(self, "_clip_step", -1)):   # 줄인 뒤 다시 잰 값으로만
                done = self.summarize(reason="auto")
            if (not done and not force and last and win and last >= self.comp.next_at
                    and getattr(self, "_nothing_said", -1) != self.st["turns"]):   # 문턱을 넘었는데 줄일 것도 접을 것도 없다 — 조용히 넘기지 않는다
                self._nothing_said = self.st["turns"]
                hook = getattr(self.ui, "compacted", None)
                if hook is not None:
                    hook("nothing", {"pct": round(100 * last / win)})
            return 0
        self.st["compactions"] += 1
        self._clip_step = self.st["steps"]
        c = {**c, "tokens_after": est_tokens(json.dumps(self.messages, ensure_ascii=False))}
        self.rec.event("compact", **c)
        hook = getattr(self.ui, "compacted", None)
        if hook is not None:
            hook("clip", c)
        return c["clipped"]

    def _turn_starts(self) -> list[int]:
        """사람 차례가 시작되는 메시지 자리(툴콜 짝이 끊기지 않는 경계)."""
        out = []
        for i, m in enumerate(self.messages):
            if i == 0 or m.get("role") != "user" or str(m.get("content") or "").startswith("[엔진]"):
                continue
            prev = self.messages[i - 1]
            if prev.get("role") in ("assistant", "system") and not prev.get("tool_calls") or prev.get("role") == "user":
                out.append(i)
        return out

    def summarize(self, reason: str = "manual") -> dict | None:
        """앞 대화(시스템 다음 ~ 최근 SUMMARY_KEEP_TURNS 사람 차례 앞)를 도구 없는 한 번으로 요약해 한 쪽으로 접는다.
        접을 것이 없거나 요약을 못 받으면 None(대화는 그대로). 기록(transcript·events)에는 원문이 그대로 남는다."""
        starts = self._turn_starts()
        keep_user = None
        if len(starts) > SUMMARY_KEEP_TURNS and starts[-SUMMARY_KEEP_TURNS] > 3:
            cut = starts[-SUMMARY_KEEP_TURNS]
        else:   # 한 차례가 아주 긴 경우: 최근 SUMMARY_KEEP_STEPS 단계 앞을 접고, 지금 차례의 사람 말은 원문 그대로 요약 뒤에 붙인다
            idx = [i for i, m in enumerate(self.messages) if m.get("role") == "assistant"]
            if len(idx) <= SUMMARY_KEEP_STEPS:
                return None
            cut = idx[-SUMMARY_KEEP_STEPS]
            last_user = max((i for i in starts if i < cut), default=None)
            if last_user is not None:
                keep_user = str(self.messages[last_user].get("content") or "")
        span = self.messages[1:cut]
        if len(span) < 3:
            return None
        text = summary_text(span)
        hook = getattr(self.ui, "compacting", None)
        if hook is not None:
            hook()
        try:
            summ = ask_summary(chat, self.backend, text)
        except Exception as e:
            self.rec.event("summarize_fail", reason=reason, error=f"{type(e).__name__}: {e}"[:300])
            hook = getattr(self.ui, "compacted", None)
            if hook is not None:   # 조용히 넘어가지 않는다(사용자 "자동 compact 안 하는 것 같다")
                hook("summary_fail", {"reason": reason, "error": f"{type(e).__name__}: {e}"[:160]})
            return None
        if not summ:
            return None
        before = len(self.messages)
        n_fold = self.st.get("summaries", 0) + 1
        try:   # 접은 원문을 따로 남긴다(감사·되돌림: 이 파일의 messages 를 요약 자리에 되넣으면 접기 전 대화)
            fd = self.rec.dir / "folds"
            fd.mkdir(parents=True, exist_ok=True)
            (fd / f"{n_fold:03d}.json").write_text(json.dumps({"reason": reason, "summary": summ, "messages": span},
                                                             ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        tail_user = (f"\n\n[지금 차례 사람 말(원문)]\n{keep_user}" if keep_user else "")
        rest = self.messages[cut:]
        self.messages = [self.messages[0],
                         {"role": "user", "content": "[엔진] 앞 대화 요약 — 문맥을 줄이려고 엔진이 앞 대화를 접었다. 원문은 기록에 있다"
                                                     "(memory_search·기록 파일). 사람의 지시·금지는 그대로 지킨다. 이어 가기 전에 할 일 목록(todo_read)과 "
                                                     "바꾼 파일의 지금 상태를 한 번 확인하고, 실패 기록의 길은 되풀이하지 않는다.\n\n" + summ + tail_user}] + \
            ([] if rest and rest[0].get("role") == "assistant" else [{"role": "assistant", "content": "앞 대화 요약을 받았다. 이어 간다."}]) + rest
        self.comp = self._new_compactor()
        self.st["last_prompt_tokens"], self._pmark = 0, 0
        self.st["summaries"] = self.st.get("summaries", 0) + 1
        info = {"reason": reason, "folded_messages": cut - 1, "before": before, "after": len(self.messages), "summary_chars": len(summ),
                "tokens_before": est_tokens(json.dumps(self.messages[:1] + span + rest, ensure_ascii=False)),
                "tokens_after": est_tokens(json.dumps(self.messages, ensure_ascii=False))}
        self.rec.event("summarize", **info)
        save_messages(self.rec.dir, self.messages)
        hook = getattr(self.ui, "compacted", None)
        if hook is not None:
            hook("summary", info)
        else:
            self.ui.dim(f"  (앞 대화 {cut - 1}개 메시지를 요약 한 쪽으로 접었다 — 문맥 줄이기)")
        return info

    def _clean(self) -> list[dict]:
        out = [{k: v for k, v in m.items() if not k.startswith("_")} for m in self.messages]
        if getattr(self, "passback", "off") == "current_turn":   # 판 116: 지난 차례의 생각은 보내지 않는다
            from .toolrepair import strip_old_turns
            out = strip_old_turns(out)
        return out

    def _new_compactor(self) -> Compactor:
        p = self.profile
        from .compact import settings as compact_settings
        c = compact_settings(p, COMPACT_RATIO, KEEP_RECENT)        # 프로필 + STEWART_COMPACT_*
        from .compact import reply_reserve
        return Compactor(self.backend.budget(), c["ratio"], c["keep_recent"], CLIP_OLD, start=1, fold=c["fold"],
                         reserve=reply_reserve(p, self.backend), tail_tokens=True)

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
        text = "\n\n".join(extra) + ("\n\n" + INTERJECT_NOTE if getattr(self.ui, "goal_notes", False) else "")
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
            kw = {**mt, **TH.call_kwargs(self.backend, self.tks, mt.get("thinking"), est, first=getattr(self, "_turn_n", 0) == 1)}   # 생각 켬 차례만
            self._prefix_check(step)
            try:
                if self.stream:
                    self.ui.stream_start()
                    t_req, first = time.monotonic(), []
                    try:   # 첫 조각(본문·생각·툴콜)까지 시간 = ttft
                        resp, dt = chat_stream(self.backend, self._clean(), self.tool_defs or None,
                                               on_delta=self.ui.stream_piece,
                                               on_first=lambda: first.append(time.monotonic() - t_req), **kw)
                    finally:
                        streamed = self.ui.stream_end()
                    self._ttft = round(first[0], 3) if first else None
                else:
                    resp, dt = chat(self.backend, self._clean(), self.tool_defs or None, **kw)
                    self._ttft = None   # 비스트리밍은 첫 토큰 시각을 모른다
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
    def _err_watch(self, cmd: str, result: str, tc: dict) -> str:
        """실패한 셸 하나: 사람 경계 꼴이면 사람 손 카드로 넘기라는 알림(차례에 한 번), 다른 명령이 같은 오류를 SAME_ERROR_AT 번 내면 되짚기."""
        note = ""
        hb = ST.human_boundary(result)
        if hb and hb not in self._human_noted:
            self._human_noted.add(hb)
            how = ("허브에서는 board add_todo(type=손작업, title 은 짧게, detail 에 기기·셸·한 줄·하는 일)로 올리고 그 U-번호로 말한 뒤 차례를 마친다."
                   if "board" in self.tool_names + [d["function"]["name"] for d in self.extra_defs]
                   else "ask_user 로 그 카드를 보이고 멈춘다.")
            note += ST.HUMAN_NOTE.format(what=hb, how=how, hint=ST.human_hint(hb))
            tc["human_boundary"] = hb
            self.rec.event("human_boundary", what=hb)
        sig = ST.err_sig(result)
        n = 0
        if sig:
            cmds = self._err_sigs.setdefault(sig, [])
            if cmd not in cmds:
                cmds.append(cmd)
            n = len(cmds)
        wn = self.nudge.on_error(sig, n, result)   # 웹 넛지(판 80): 같은 오류 두 번째·처음 보는 이름 오류
        if wn:
            note += wn
            tc["web_nudge"] = True
            self.rec.event("web_nudge", why_kind="error", step=self.st.get("steps", 0), why=self.nudge.log[-1]["why"])
            self.comps.fire("web_nudge", self.st.get("steps", 0), "error", "nudge")
        if sig:
            if n >= ST.SAME_ERROR_AT:
                note += ST.SAME_ERROR_NOTE.format(sig=sig, n=n)
                tc["same_error"] = n
                if n == ST.SAME_ERROR_AT:
                    self._samerr_hit = (sig, list(cmds), tc)
        return note

    def _mem_seen(self, name: str, args: dict, ok: bool, result: str) -> str:
        """도구 한 번: 만진 경로로 대상 프로젝트를 고르고(바뀌면 그 프로젝트 색인을 결과 끝에 한 번), 실패한 셸은 차례 끝 메모로 모은다."""
        paths = []
        for k in ("path", "file", "dir"):
            if isinstance(args.get(k), str) and args[k]:
                paths.append(args[k])
        if name == "run_shell":
            cwd = getattr(self.env.shell, "cwd", None) if self.env.shell is not None else None
            if cwd:
                paths.append(cwd)
            m = re.match(r"\[exit (-?\d+)\]", result or "")
            if m and not ok:
                self.__dict__.setdefault("_mem_shells", []).append({"command": str(args.get("command") or ""), "exit": int(m.group(1))})
        if paths and self.mem.retarget(paths) is not None:
            note = self.mem.target_note()
            if note:
                self.rec.event("memory_target", subject=self.mem.target["key"])
                return "\n\n" + note
        return ""

    def turn(self, text: str, images: list | None = None) -> str:
        self._mem_shells = []
        try:
            return self._turn(text, images)
        finally:
            if self.mem is not None and self._mem_shells:   # 실사용에서도 엔진 자동 메모가 쌓이게(판 54)
                try:
                    n = self.mem.turn_notes(self._mem_shells)
                    if n:
                        self.rec.event("memory_auto", notes=n, subject=self.mem.here()["key"])
                except OSError:
                    pass

    def set_tool(self, name: str, on: bool) -> bool:
        """허브 켬·끔(판 99): toggle_tools 의 도구를 다음 걸음부터 모델에게 보이게·안 보이게."""
        if name not in getattr(self, "toggleable", []):
            return False
        (self.disabled_tools.discard if on else self.disabled_tools.add)(name)
        self.tool_defs = [d for d in self._all_defs if d["function"]["name"] not in self.disabled_tools]
        self.tool_names = [d["function"]["name"] for d in self.tool_defs]
        self.rec.event("tool_toggle", name=name, on=bool(on))
        return True

    def label(self, turn: int, kind: str, by: str = "human", note: str = "") -> bool:
        """학습 표시(판 89, SW-39): 기록에 train_label 사건만 남긴다(대화·모델 문맥은 그대로). kind ∈ TRAIN.LABELS."""
        if kind not in TRAIN.LABELS or not (1 <= int(turn) <= self.st["turns"]):
            return False
        self.rec.event("train_label", turn=int(turn), label=kind, by=by, **({"note": note[:300]} if note else {}))
        hook = getattr(self.ui, "labeled", None)
        if hook is not None:
            hook(int(turn), kind, by)
        return True

    def _turn(self, text: str, images: list | None = None) -> str:
        """사람 입력 하나를 처리한다. 종료 사유: answered | asked | length_empty | max_steps | repeated_call | repeated_failure | backend_error | context_overflow | interrupted | consecutive_errors"""
        self.st["turns"] += 1
        self._raw_text = text   # 사람 말 그대로(말만 하고 멈춤 되돌리기 판정용, intent.py)
        if hasattr(self.ui, "turn_no"):   # 허브 화면: 답 줄에 차례 번호(👍👎 표시가 어느 차례인지)
            self.ui.turn_no = self.st["turns"]
        if self.st["turns"] > 1 and TRAIN.is_correction(text):   # 학습 표시(판 89): 교정 꼴 말 → 바로 앞 차례에 "교정됨"
            self.label(self.st["turns"] - 1, "corrected", by="rule")
        if self.mem is not None and getattr(self.ui, "memory_writes", True):   # 스스로 기억(사용자 전역): 다음에도 지킬 지시·선호
            for kind, sent in MEM.detect(text):
                how = "save"
                if self.mem.md():   # 세 갈래(판 83): 이번 차례 지시는 남기지 않고, 오래 지킬 지시는 사람에게 묻는다
                    kind, how = MEM.route(kind, sent)
                if how == "skip":
                    continue
                if how == "ask":
                    pend = self.mem.propose(kind, sent)
                    if pend is not None:
                        self.rec.event("remember_ask", id=pend["id"], what=kind)
                        ask = getattr(self.ui, "memory_ask", None)
                        if ask is not None:
                            ask(pend)
                        else:
                            self.ui.dim(f"  (방침으로 기억할까? \"{sent[:80]}\" — 남기려면 '기억해: …' 라고 말한다)")
                    continue
                item = self.mem.remember(kind, sent)
                if item is not None:
                    self.rec.event("remember", id=item["id"], what=kind)
                    hook = getattr(self.ui, "remembered", None)
                    if hook is not None:
                        hook(item)
                    else:
                        self.ui.dim(f"  (기억함: {sent[:80]} — 지우기: 허브 화면 또는 기억 폴더)")
        cp = self.cps.begin(text.splitlines()[0] if text else "")
        self._turn_cp = getattr(self, "_turn_cp", {})
        self._turn_cp[self.st["turns"]] = cp   # 판 105: 차례 되감기(그 차례 시작 체크포인트)
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
        if images and not self.backend.vision:   # 꺼진 백엔드에는 이미지를 붙이지 않는다(까닭을 말하고 글만)
            self.ui.warn(f"  이 백엔드({self.backend.name})는 이미지를 받지 않는다(backends 표 vision = false). 이미지는 빼고 글만 보낸다.")
            images = None
        todos_open = [t for t in (getattr(self.env, "todos", None) or []) if t.get("status") != "done"]
        if getattr(self.ui, "goal_notes", False) and (todos_open or self.bans or self._last_reason not in (None, "answered", "asked")):
            text = text + "\n\n" + NEW_GOAL_NOTE   # 앞 차례의 미완 걸음·목록·되짚기 계획이 이 말을 덮지 않게
        self.nudge.reset_turn()
        self.dnudge.reset_turn()
        rn = self.nudge.on_turn(self._raw_text)   # 웹 넛지: 최신 사실을 묻는 말이면 "먼저 웹으로 확인"
        if rn:
            text += rn
            self.rec.event("web_nudge", why_kind="recent", why=self.nudge.log[-1]["why"])
            self.comps.fire("web_nudge", self.st.get("steps", 0), "recent", "nudge")
        if self.mem is not None and EX.enabled():   # 예시 은행(판 92): 비슷한 지난 일의 받아 둔 꼴 하나
            try:
                hit = EX.pick(EX.load(self.mem.store.dir), self._raw_text)
            except (OSError, ValueError, AttributeError):
                hit = None
            if hit is not None:
                text += EX.block(hit[0])
                self.rec.event("example_used", id=hit[0]["id"], score=hit[1])
        self._turn_at = getattr(self, "_turn_at", {})
        self._turn_at[self.st["turns"]] = (len(self.messages), self._raw_text)   # 판 105: 그 차례 사람 말 자리
        self.messages.append({"role": "user", "content": IMG.content(text, images or [])})
        self.rec.event("user", turn=self.st["turns"], content=text, images=len(images or []) or None,
                       **({"img_names": [x.name for x in (IMG.path_of(q) for q in images) if x]} if images else {}),
                       **({"raw": self._raw_text} if self._raw_text != text else {}))
        self.rec.md(f"## 사람 (차례 {self.st['turns']})\n\n```\n{text}\n```\n")
        consecutive = 0
        reason = "max_steps"
        self._turn_calls: list[dict] = []   # /facts: 이번 차례의 도구 호출
        self._dg_budget = DG.Budget(self.max_steps, self.profile.delegate_budget)   # 작업자 단계 합 상한은 차례마다
        self.fail_count = {}          # 같은 호출 실패 세기는 차례마다 새로(사람이 "다시 해 봐" 하면 다시 센다)
        self._repeat_stop = False
        self.guard.reset()
        self._guard_hit = 0
        self._guard_tc = None
        self._silent, self._narrate_nudged = 0, False
        self._blocked: dict = {}          # 되짚기 뒤 이 차례에 막은 호출 서명
        self._last_reflect = ""
        self._asked = None
        self._err_sigs, self._human_noted, self._samerr_hit = {}, set(), None   # 같은 오류·사람 경계(판 62), 차례마다
        self.compact(estimate=True)   # 차례 시작에도 문턱을 본다(판 65): 도구 없이 답만 하는 차례가 이어지면 단계 뒤 판정이 돌지 않았다
        self._rstate = RF.State()     # 되짚기 횟수는 차례마다(사람이 "계속" 하면 다시 한 번 쓸 수 있다)
        self._fail_steps, self._recent_fail = [], []
        self._cut, self._continued = False, 0
        self._intent_nudged = False
        self._todo_since, self._todo_nudged_at, self._todo_final_done = 0, 0, False   # 할 일 갱신 장치(판 85) — 차례마다
        self._spawn_guard = False
        budget, n = self.max_steps, 0
        try:
            while True:
                if n >= budget:
                    if self._ask_more(budget):
                        budget += self.max_steps
                    else:
                        break
                n += 1
                self._turn_n = n   # 판 116: 차례 안 걸음 번호(첫 걸음 생각 상한)
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
                    be = getattr(self.ui, "backend_error", None)   # 허브 화면: 까닭을 사람 말 한 줄로 + 모델 서버 점을 곧바로
                    if be is not None:
                        be(e)
                    else:
                        self.ui.warn(f"백엔드 오류: {e} {(e.body or '')[:300]}")
                    self.rec.event("step", step=step, error=str(e), status=e.status, body=(e.body or "")[:2000],
                                   **({"error_chain": e.chain} if getattr(e, "chain", None) else {}))
                    self.rec.md_step(step, {"elapsed_s": 0.0, "note": f"백엔드 오류 {e}"})
                    if e.status and "parse" in (e.body or "").lower():
                        self.st["parse_failures"] += 1
                        consecutive += 1
                        self.messages.append({"role": "user", "content": "도구 호출 형식이 깨졌다. 정해진 tool call 형식으로 다시 호출하라."})
                        self._fail_steps.append([])
                        if consecutive < self.profile.max_consecutive_errors:
                            continue
                        if self._reflect("consecutive_errors", step, consecutive):
                            consecutive = 0
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
                from .backend import take_retries
                rts = take_retries()
                if rts:   # 429·5xx·끊김에 다시 보내 받은 응답(SW-34 ①) — 화면에 한 줄
                    self.st["backend_retries"] = self.st.get("backend_retries", 0) + len(rts)
                    self.rec.event("backend_retry", step=step, attempts=rts)
                    self.ui.dim(f"  (모델 서버가 {rts[-1].get('status') or '연결 끊김'} — {len(rts)}번 다시 보내 받았다)")
                self.st["completion_tokens"] += usage.get("completion_tokens") or 0
                self.st["prompt_tokens_total"] += usage.get("prompt_tokens") or 0
                if usage.get("cost_usd"):   # Messages API 백엔드(판 69)·가격표를 단 OpenAI 호환 API
                    self.st["cost_usd"] = round(self.st.get("cost_usd", 0.0) + float(usage["cost_usd"]), 6)
                if usage.get("prompt_tokens"):
                    self.st["last_prompt_tokens"], self.st["prompt_estimated"] = usage["prompt_tokens"], False
                else:   # usage 를 안 주는 API: 보낸 메시지 글자 수로 어림(화면에 "약")
                    from .compact import est_tokens
                    try:
                        self.st["last_prompt_tokens"] = est_tokens(json.dumps(self._clean(), ensure_ascii=False))
                        self.st["prompt_estimated"] = True
                    except (TypeError, ValueError):
                        pass
                done = self.comp.observe(usage.get("prompt_tokens"))
                if done:
                    self.rec.event("compact_effect", step=step, before_tokens=done["before_tokens"], after_tokens=done["after_tokens"])
                for k_src, k_dst in (("predicted_n", "pred_n"), ("predicted_ms", "pred_ms"), ("prompt_n", "prompt_n"), ("prompt_ms", "prompt_ms")):
                    self.st[k_dst] += tim.get(k_src) or 0
                content = msg.get("content") or ""
                calls = msg.get("tool_calls") or []
                if not calls and self.repair_on and content and self.tool_names:   # 본문에 쓴 툴콜을 툴콜로(가-1)
                    from . import toolrepair as TR
                    rest, fixed = TR.extract(content, self.tool_names)
                    if fixed:
                        self.st["toolcall_repaired"] = self.st.get("toolcall_repaired", 0) + 1
                        self.rec.event("broken_toolcall", step=step, form="in_content", repaired=True, raw=content[:2000],
                                       names=[c["function"]["name"] for c in fixed])
                        self.comps.fire("broken_toolcall", step, "in_content", "rewrite", repaired=True, how="parse", n=len(fixed))
                        content, calls = rest, fixed
                srec = {"elapsed_s": dt, "ttft_s": self._ttft, "content": content, "timings": tim, "usage": usage,
                        "finish_reason": choice.get("finish_reason"), "mode": None, "tool_calls": []}
                think = msg.get("reasoning_content") or msg.get("reasoning")
                if think:
                    srec["reasoning_content"] = think
                    if not self.stream:   # 스트리밍이면 화면이 이미 생각을 보였다(허브 생각 블록·TUI 글자 수)
                        self.ui.dim(f"  (생각 {len(think)}자)")
                if content.strip() and not streamed:   # 도구와 함께 온 글: 허브 화면은 그대로(걸음 사이 말), 터미널은 한 줄
                    self.ui.assistant(content.strip() if not calls or getattr(self.ui, "full_narration", False) else _one_line(content, 300))
                self._cut = choice.get("finish_reason") == "length"
                if calls:
                    for rc in calls:
                        rc.setdefault("id", "call_" + uuid.uuid4().hex[:8])
                        rc.setdefault("type", "function")
                    self.messages.append({"role": "assistant", "content": content or None, "tool_calls": calls, **({"anthropic_content": msg["anthropic_content"]} if msg.get("anthropic_content") else {}),
                                          **({"reasoning_content": think} if TR_keep(self.passback, think, True) else {})})   # 생각 블록 되보내기(판 69)·OpenAI 길 손잡이(판 81)
                    any_ok = False
                    for rc in calls:
                        tc = self._exec(rc, step)
                        done = getattr(self.ui, "tool_done", None)   # 허브 화면: 모든 길(일찍 끝난 것 포함)의 끝 — ✓/✕ 표시
                        if done is not None:
                            done(tc)
                        if not tc["ok"]:
                            self._recent_fail.append(tc)
                            del self._recent_fail[:-RF.RECENT_KEEP]
                        srec["tool_calls"].append(tc)
                        self._turn_calls.append(tc)
                        any_ok = any_ok or tc["ok"]
                        self.messages.append({"role": "tool", "tool_call_id": rc["id"], "name": tc["name"],
                                              "content": tc["result"]})
                    consecutive = 0 if any_ok else consecutive + 1
                    if getattr(self.ui, "goal_notes", False):   # 허브: 말 없이 도구만 5걸음 넘게 → 한 번 "지금 무엇을 하는지 한 문장으로"
                        self._silent = 0 if content.strip() else getattr(self, "_silent", 0) + 1
                        if self._silent >= SILENT_STEPS and not getattr(self, "_narrate_nudged", False):
                            self._narrate_nudged = True
                            RF.append_user(self.messages, NARRATE_NOTE)
                            self.rec.event("narrate_nudge", step=step, silent=self._silent)
                    self._todo_watch(step, srec["tool_calls"])   # 할 일 목록이 걸음을 따라오는지(판 85)
                    if getattr(self, "pool", None) is not None and (fin := self.pool.fresh_done()):   # 판 108: 끝난 작업자 알림
                        RF.append_user(self.messages, f"\n\n[엔진] 작업자 {', '.join(fin)} 가 끝났다 — workers(action='collect') 로 결과를 받는다.")
                        self.rec.event("spawn_inject", ids=fin)
                    dn = self.dnudge.on_step(self._turn_calls)
                    if dn:   # 맡기기 넛지(판 97): 읽기를 많이 한 차례에 한 번
                        RF.append_user(self.messages, dn)
                        self.rec.event("delegate_nudge", step=step, reads=sum(1 for c in self._turn_calls if c.get("name") in DG.READ_TOOLS))
                        self.comps.fire("delegate_nudge", step, "reads", "nudge")
                    wn = self.nudge.on_budget(budget - n, sum(1 for c in self._turn_calls if c.get("name") in T.WEB_TOOLS))
                    if wn:   # 웹 끝맺기(판 90): 남은 걸음이 거의 없으면 찾은 것으로 답하게
                        RF.append_user(self.messages, wn)
                        self.rec.event("web_nudge", why_kind="wrap", why=f"left {budget - n}")
                        self.comps.fire("web_nudge", step, "wrap", "nudge")
                    self._attach_viewed()
                    if any_ok:
                        self._fail_steps.clear()
                    else:
                        self._fail_steps.append(srec["tool_calls"])
                    self.rec.event("step", step=step, new_messages=self.messages[n_before:], response=srec,
                                   consecutive_errors=consecutive)
                    self.rec.md_step(step, srec)
                    if consecutive >= self.profile.max_consecutive_errors:
                        if self._reflect("consecutive_errors", step, consecutive):
                            consecutive = 0
                            self.compact()
                            continue
                        if self._ask_loop(n, step, "consecutive_errors", f"도구 호출이 모두 실패한 단계가 {consecutive}번 이어졌다"):
                            consecutive = 0
                            self.compact()
                            continue
                        reason = "consecutive_errors"
                        break
                    if self._asked:   # 질문을 보이고 그 차례를 끝낸다(다음 사람 입력이 답)
                        self.messages.append({"role": "assistant", "content": self._asked})
                        self.rec.event("asked", step=step, question=self._asked)
                        self.ui.assistant("질문: " + self._asked)
                        hook = getattr(self.ui, "asked_options", None)
                        if hook is not None and getattr(self, "_asked_opts", None):
                            hook(self._asked_opts)
                        self._asked_opts = []
                        reason = "asked"
                        break
                    if self._repeat_stop:
                        if self._reflect("repeated_failure", step, consecutive):
                            self._block(self._recent_fail[-1:])
                            consecutive = 0
                            self.compact()
                            continue
                        if self._ask_loop(n, step, "repeated_failure", f"똑같은 도구 호출이 {REPEAT_STOP}번 실패했다"):
                            self._repeat_stop = False
                            RF.lower_hot(self.fail_count, REPEAT_NUDGE, REPEAT_STOP)
                            consecutive = 0
                            continue
                        reason = "repeated_failure"
                        break
                    if self._samerr_hit:   # 다른 명령이 같은 오류를 거듭 냄 → 되짚기(판 62)
                        sig, cmds, stc = self._samerr_hit
                        self._samerr_hit = None
                        facts = f"- 오류 꼴 `{sig}` 을 낸 명령 {len(cmds)}개:\n" + "\n".join(f"    $ {c[:160]}" for c in cmds)
                        if self._reflect("same_error", step, consecutive, calls=[stc], facts=facts, label=f"같은 오류 {len(cmds)}번"):
                            consecutive = 0
                            continue
                    if self._guard_hit:
                        hit, self._guard_hit = self._guard_hit, 0
                        gtc, self._guard_tc = self._guard_tc, None
                        if self._reflect("repeated_call", step, consecutive, calls=[gtc] if gtc else [],
                                         facts=RF.facts_same(gtc, hit) if gtc else None, label=f"같은 호출 {hit}번"):
                            self._block([gtc] if gtc else [])
                            self.guard.reset()
                            consecutive = 0
                            self.compact()
                            continue
                        if self._ask_loop(n, step, "repeated_call", f"같은 호출을 결과가 같은 채로 {hit}번 되풀이했다"):
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
                self.messages.append({"role": "assistant", "content": content, **({"anthropic_content": msg["anthropic_content"]} if msg.get("anthropic_content") else {}),
                                      **({"reasoning_content": think} if TR_keep(self.passback, think, False) else {})})
                if self._cut and content.strip() and self._continued < CO.CONTINUE_MAX:   # 끊긴 글 답: 최종 답으로 받지 않고 잇게 한다
                    self._continued += 1
                    self.messages.append({"role": "user", "content": CO.CONTINUE_NOTE.format(mt=CO.limit_of(self.profile, self.backend))})
                    srec["note"] = "답이 생성 상한에서 끊김 → 이어 쓰게 함"
                    self.rec.event("step", step=step, new_messages=self.messages[n_before:], response=srec)
                    self.rec.md_step(step, srec)
                    self.rec.event("length_continue", step=step, n=self._continued)
                    self.ui.dim("  (답이 생성 상한에서 끊겨 이어 쓰게 한다)")
                    continue
                if (self._intent_nudged and IN.on() and self.tool_names
                        and IN.intent_only(self._raw_text, content, len(self._turn_calls))
                        and self._reflect("intent_only", step, 0, calls=[], label="말만 하고 멈춤 2번")):   # 두 번째: 되짚기
                    self.rec.event("step", step=step, new_messages=self.messages[n_before:], response=srec)
                    self.rec.md_step(step, srec)
                    continue
                if (not self._intent_nudged and IN.on() and self.tool_names
                        and IN.intent_only(self._raw_text, content, len(self._turn_calls))):   # 말만 하고 멈춤: 한 번 되돌린다
                    self._intent_nudged = True
                    self.st["intent_nudges"] = self.st.get("intent_nudges", 0) + 1
                    self.messages.append({"role": "user", "content": IN.NOTE})
                    srec["note"] = "할 일을 말로만 하고 끝냄 → 도구로 하게 되돌림"
                    self.rec.event("step", step=step, new_messages=self.messages[n_before:], response=srec)
                    self.rec.md_step(step, srec)
                    self.rec.event("intent_nudge", step=step)
                    self.ui.dim("  (할 일을 말로만 하고 끝내 도구로 하게 되돌린다)")
                    continue
                if getattr(self, "pool", None) is not None and self.pool.running() and not self._spawn_guard:   # 판 108: 남은 작업자
                    self._spawn_guard = True
                    RF.append_user(self.messages, f"[엔진] 작업자 {', '.join(self.pool.running())} 가 아직 돈다 — workers(action='wait') 로 "
                                                  "기다렸다 결과를 모아 답한다(필요 없으면 cancel). 앞 답은 이미 사람에게 갔으니 다시 쓰지 말고 이어 간다.")
                    self.rec.event("spawn_guard", step=step, running=self.pool.running())
                    self.rec.event("step", step=step, new_messages=self.messages[n_before:], response=srec)
                    self.rec.md_step(step, srec)
                    continue
                if self._todo_final(content, step):   # 사람에게 답·물음 전에 할 일 목록을 지금 상태로(판 85, 차례당 한 번)
                    self.rec.event("step", step=step, new_messages=self.messages[n_before:], response=srec)
                    self.rec.md_step(step, srec)
                    continue
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
            if getattr(self, "pool", None) is not None:   # 판 108: [멈춤]·Ctrl-C 는 작업자도 멈춘다(도는 걸음은 끝까지 — 결과는 버림)
                self.pool.cancel_all()
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
        self._last_reason = reason
        rsum = self._rstate.summary(reason, self.st["steps"])
        if rsum is not None:
            rsum["turn"] = self.st["turns"]
            self._reflect_log.append(rsum)
            if self._rstate.log:
                self.rec.event("reflect_end", **rsum)
                if reason in ("consecutive_errors", "repeated_failure"):
                    self.ui.warn("  (되짚기를 한 번 했지만 다시 같은 꼴로 실패해 멈췄다)")
        self.ui.dim(f"  [{reason} · 단계 {self.st['steps']} · 프롬프트 {self.st['last_prompt_tokens']}/{self.backend.max_context} tok"
                    f" · 생성 합 {self.st['completion_tokens']} tok · 모델 {self.st['model_time_s']:.0f}초]")
        changed = sorted({e["path"] for e in self.cps.cur["files"]}) if (self.cps.cur and self.cps.cur.get("files")) else []
        extra = list(getattr(self.env, "turn_extra_files", None) or [])   # 판 107: 도구가 체크포인트 밖에서 만든 파일
        if extra:
            self.env.turn_extra_files = []
            changed = sorted(set(changed) | {str(x) for x in extra})
        if changed and hasattr(self.ui, "turn_files"):   # 허브 화면: "이번 차례에 바뀐 파일" 한 줄
            self.ui.turn_files([os.path.relpath(f, self.profile.workdir) if os.path.isabs(f) else f for f in changed])
        self.rec.event("turn_end", turn=self.st["turns"], reason=reason, **({"reflect": rsum} if rsum else {}),
                       **({"files": [os.path.relpath(f, self.profile.workdir) if os.path.isabs(f) else f for f in changed][:50]}
                          if changed else {}))
        self._last_reason = reason
        save_messages(self.rec.dir, self.messages)
        return reason

    def _attach_viewed(self) -> None:
        """view_image 가 연 이미지를 다음 user 메시지로 붙인다(도구 결과는 글 — 백엔드가 tool 메시지 이미지를 못 받아서)."""
        if not self.env.pending_images:
            return
        parts, self.env.pending_images = list(self.env.pending_images), []
        self.messages.append({"role": "user", "content": IMG.content(f"[엔진] view_image 로 연 이미지 {len(parts)}장", parts)})
        self.rec.event("image_attach", n=len(parts))
        hook = getattr(self.ui, "tool_images", None)   # 허브: 작업 카드 안에 그 그림(판 91)
        if hook is not None:
            hook([str(x) for x in (IMG.path_of(q) for q in parts) if x])

    def _block(self, calls: list) -> None:
        """되짚기 뒤 이 차례에 같은 호출을 막는다(결과가 앞과 같을 것이라 돌리지 않는다)."""
        for tc in calls or []:
            if tc:
                self._blocked[str(tc.get("name", "")) + "\0" + str(tc.get("raw_arguments", ""))] = True
                self._add_ban(ST.ban_from_call(tc))

    def _add_ban(self, b) -> None:
        if b is not None and all(x.id != b.id for x in self.bans):
            self.bans.append(b)
            self.rec.event("ban", id=b.id, tool=b.tool, text=b.text, source=b.source)

    def unban(self, ban_id: str) -> bool:
        """사람이 화면에서 금지를 푼다."""
        n = len(self.bans)
        self.bans = [b for b in self.bans if b.id != ban_id]
        if len(self.bans) != n:
            self.rec.event("unban", id=ban_id)
            return True
        return False

    def _ask_loop(self, n: int, step: int, kind: str, why: str) -> bool:
        """스스로 되짚기를 다 쓰고도 같은 고리: 사람에게 묻는다 — 무엇을 해 봤고 왜 막혔는지 + 고를 길.
        y = 그대로 계속, n = 멈춤, 그 밖의 글 = 그 방향을 모델에 전하고 이어 감. auto 모드는 묻지 않고 멈춘다."""
        if self.mode == "auto":
            self.rec.event(kind, steps=n, asked=False, more=False)
            return False
        tried = self._last_reflect.strip()
        msg = (f"  {why}. 스스로 되짚기 {self._rstate.used}번으로도 풀리지 않았다."
               + (f"\n  마지막 되짚기(앞부분):\n    " + "\n    ".join(tried.splitlines()[:12]) if tried else "")
               + "\n  고를 길: y = 같은 방향으로 계속 · n = 여기서 멈춤 · 다른 말 = 그 방향을 모델에 전하고 이어 감")
        try:
            ans = self.ui.ask(msg + "\n  [y/n/모델에 할 말] ").strip()
        except EOFError:
            ans = "n"
        low = ans.lower()
        more = low in ("", "y", "yes", "ㅇ", "네", "계속")
        if not more and low not in ("n", "no", "ㄴ", "아니"):
            self.messages.append({"role": "user", "content": f"[사람] {ans}"})
            more = True
        self.rec.event(kind, steps=n, asked=True, more=more, said=bool(ans and low not in ("y", "n", "yes", "no")))
        return more

    def _reflect(self, trigger: str, step: int, consecutive: int, calls: list | None = None, facts: str | None = None,
                 label: str | None = None) -> bool:
        """막히기 직전(오류 누적·같은 실패·같은 호출 되풀이·말만 하고 멈춤) 도구 없는 되짚기 한 번(reflect.py). 했으면 True(이어 간다)."""
        if calls is None:
            calls = ([tc for s in self._fail_steps[-consecutive:] for tc in s] if trigger == "consecutive_errors"
                     else self._recent_fail[-1:])
        why = RF.skip_reason(self.rf, self._rstate.used, calls)
        if why is not None:
            if why != "off":
                self._rstate.skipped.append({"trigger": trigger, "at_step": step, "why": why})
                self.rec.event("reflect_skip", step=step, trigger=trigger, why=why)
            return False
        self._interject(step)   # 허브 긴급 지시·사람이 끼워 넣은 말은 되짚기 앞에(멈춤 요청도 여기서 걸린다)
        self._rstate.used += 1
        self.st["reflects"] = self.st.get("reflects", 0) + 1
        RF.append_user(self.messages, RF.CHAT_PROMPT.format(why=RF.WHY[trigger],
                                                       facts=facts or RF.facts(self.fail_count, self._recent_fail)))
        self.rec.event("reflect_start", step=step, trigger=trigger, last_k=consecutive,
                       fails={RF._hash(k): v for k, v in self.fail_count.items()})
        label = label or RF.LABEL.get(trigger, trigger)
        card = getattr(self.ui, "reflect_card", None)   # 허브 화면: 작업 카드에 "되짚는 중 — …" 줄
        if card is not None:
            card("start", trigger, label, "")
        else:
            self.ui.dim(f"  ({label} — 도구 없이 한 번 되짚는다)")
        kw = {"thinking": self.think} if self.think is not None else {}
        cap = RF.cap_tokens(self.rf, self.tks["max_tokens"], TH.is_on(self.backend, self.think))
        r = RF.call(chat, self.backend, self._clean(), kw, cap)
        u = r["usage"]
        self.st["model_time_s"] += r["elapsed_s"]
        self.st["completion_tokens"] += u.get("completion_tokens") or 0
        self.st["prompt_tokens_total"] += u.get("prompt_tokens") or 0
        self.st["last_prompt_tokens"] = u.get("prompt_tokens") or self.st["last_prompt_tokens"]
        self._pmark = len(self.messages)
        self.rec.event("reflect", step=step, trigger=trigger, **r)
        self.comps.fire("reflect", step, trigger, "return_to_model", ok=r["ok"], last_k=consecutive)
        entry = {"trigger": trigger, "at_step": step, "ok": r["ok"], "chars": len(r["content"]),
                 "completion_tokens": u.get("completion_tokens")}
        self._rstate.log.append(entry)
        if not r["ok"]:
            self.messages.append({"role": "assistant", "content": RF.FAILED_NOTE.format(why=r["error"])})
            self.ui.warn(f"  되짚기 답을 받지 못했다({r['error']}). 원래 사유로 멈춘다.")
            return False
        self.messages.append({"role": "assistant", "content": r["content"]})
        self.messages.append({"role": "user", "content": RF.CHAT_GO})
        self._last_reflect = r["content"]
        parsed = ST.parse_reflect(r["content"], self.tool_names)   # "금지: …" 줄 → 도구 층에서 거절
        for b in parsed["bans"]:
            self._add_ban(b)
        if card is not None:
            card("done", trigger, label, r["content"])
        else:
            self.ui.assistant("[되짚기]\n" + r["content"])
        entry["hot"] = RF.lower_hot(self.fail_count, REPEAT_NUDGE, REPEAT_STOP)
        self._repeat_stop = False
        self._fail_steps.clear()
        self.rec.md(f"> 실패가 이어져({trigger}) 도구 없이 되짚음\n\n{r['content']}\n")
        return True

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

    def expand_command(self, text: str) -> str | None:
        """.stewart/commands(호환 .claude/commands)의 "/이름 인자" → 모델에 보낼 글. 내장 명령과 겹친 이름·모르는 이름은 None."""
        r = PC.expand_command(self.commands, text)
        if r is None:
            return None
        c, body = r
        self.rec.event("project_command", name=c.name, scope=c.scope, origin=c.origin, chars=len(body))
        return body

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
    # ---- 동시 작업자(판 108, SW-43 ②) ----
    def _pool(self):
        if getattr(self, "pool", None) is None:
            hr = getattr(self.ui, "halt_req", None)
            self.pool = SP.Pool(self._spawn_run, SP.max_workers(self.backend), halted=lambda: hr is not None and hr.is_set(),
                                tier_limits={k: v["max"] or self._tier_conc(v) for k, v in (getattr(self, "tiers", None) or {}).items()})
            self._cps_lock = threading.RLock()
        return self.pool

    def _tier_conc(self, t: dict) -> int:
        """판 116: 등급에 max 가 없으면 그 백엔드 서버의 동시 수(메인과 같은 백엔드면 메인 몫 하나를 남긴 수)."""
        name = t.get("backend")
        if not name or name == self.backend.name:
            return SP.max_workers(self.backend)
        from .config import load_backends
        b = load_backends()[0].get(name)
        return max(1, int(getattr(b, "concurrency", 1) or 1)) if b is not None else 1

    def _spawn_run(self, args: dict, wid: str) -> tuple[bool, str]:
        """실 안에서: 작업자 하나(delegate 와 같은 길, 사람에게 묻지 않음 — 승인이 필요한 행동은 하지 않음)."""
        def on_call(n, name, a):
            self.ui.tool(f"[작업자 {wid}] {name}", _arg_summary(name, a))
        cps = _LockedProxy(self.cps, self._cps_lock)
        backend = self.backend
        tier = (getattr(self, "tiers", None) or {}).get(str(args.get("tier") or ""))
        if tier:   # 판 110: 등급 → 백엔드·프로필·문맥 예산
            import dataclasses
            from .config import load_backends
            if tier.get("backend"):
                bs, _ = load_backends()
                backend = bs.get(tier["backend"], backend)
            if tier.get("profile") and not args.get("kind"):
                args = {**args, "kind": tier["profile"]}
            if tier.get("budget"):
                backend = dataclasses.replace(backend, context_budget=int(tier["budget"]))
            args = {**args, "_tier": SP.tier_raw(tier)}   # 판 116: 작업자 프로필 raw["tier"] — 압축 예산·생각 칸
        ctx = {"profile": self.profile, "backend": backend, "rec": self.rec, "cps": cps, "env": self.env,
               "exec_mode": self.mode, "tool_names": [t for t in self.all_tool_names if t not in (DG.NAME, SP.NAME)], "ledger": None,
               "budget": self._dg_budget, "log": self.delegations, "ask": None, "on_call": on_call, "ask_cfg": None,
               "bounds": self.bd, "projcfg": self.pfl, "read_default": True,
               "rule": lambda name, a, deny_only=False: self.shell_rule(a, True) if name == "run_shell" else (None, ""),
               "ask_outside": None}
        self.rec.event("spawn_start", id=wid, task=str(args.get("task") or "")[:500])
        ok, text = DG.run_child(ctx, args)
        self.rec.event("spawn_end", id=wid, ok=ok)
        if tier and tier.get("backend") and not ok and "backend_error" in text:   # 판 116: 등급 서버가 죽었으면 분명하게(기본 모델로 대신 돌리지 않는다)
            text += (f"\n(등급 {args.get('tier')} 의 백엔드 {tier['backend']} 에 닿지 못했다 — 기본 모델로 대신 돌리지 않았다. "
                     "서버를 켜거나, 등급의 backend 줄을 지우면 기본 모델로 돈다)")
        return ok, f"[작업자 {wid}" + (f" · {args.get('tier')}" if tier else "") + "] " + text   # 꼬리표 "자리 · 등급"(판 115)

    def _workers(self, args: dict) -> tuple[bool, str]:
        act = str(args.get("action") or "")
        pool = self._pool()
        if act == "spawn":
            if not str(args.get("task") or "").strip():
                return False, "spawn 은 task 에 맡길 일을 적는다"
            note = ""
            if getattr(self, "tiers", None):
                note = SP.unknown_note(self.tiers, args.get("tier"))   # 판 116: 없는 등급 이름은 조용히 바꿔 타지 않고 알린다
                t = SP.pick_tier(self.tiers, args)
                args = {**args, "tier": t} if t else {k: v for k, v in args.items() if k != "tier"}
                if note:
                    note = f" ({note} — {t or '등급 없이'} 로 돌린다)"
            r = pool.spawn(args)
            self.ui.dim(f"  작업자 {r['id']}{' · ' + args['tier'] if args.get('tier') else ''} 띄움(동시 {pool.limit}): {_one_line(str(args.get('task')), 100)}")
            return True, (f"작업자 {r['id']} 를 띄웠다({'바로 돈다' if len(pool.running()) <= pool.limit else '자리가 나면 돈다'}){note}. "
                          "네 몫을 계속 하고, 끝나면 workers(action='wait') 또는 'collect' 로 결과를 모은다.")
        if act == "wait":
            got, still = pool.wait(args.get("ids"), args.get("timeout_s") or 600)
            self.rec.event("spawn_wait", got=[r["id"] for r in got], still=still)
            return True, SP.results_text(got, still)
        if act == "collect":
            got = pool.collect()
            return True, SP.results_text(got, pool.running())
        if act == "cancel":
            ok = pool.cancel(str(args.get("id") or ""))
            return ok, ("취소했다(도는 걸음은 그 걸음 끝까지 간다 — 결과는 버린다)" if ok else "그 작업자는 없거나 이미 끝났다")
        if act == "list":
            return True, pool.listing()
        return False, "action 은 spawn·wait·collect·cancel·list 가운데 하나"

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
               "bounds": self.bd, "projcfg": self.pfl, "read_default": True,
               "rule": lambda name, a, deny_only=False: self.shell_rule(a, deny_only) if name == "run_shell" else (None, ""),
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

    def set_budget(self, tokens: int | None) -> int:
        """판 105: 이 대화의 문맥 예산(압축 문턱을 재는 창). None 이면 백엔드 설정 그대로. 서버 창보다 크게는 못 한다. 지금 예산을 돌려준다."""
        import dataclasses
        base = getattr(self, "_backend0", None) or self.backend
        self._backend0 = base
        if tokens is not None:
            tokens = max(4096, min(int(tokens), base.max_context))
        self.backend = base if tokens is None else dataclasses.replace(base, context_budget=tokens)
        self.backend.used = base.used                        # 누계는 같은 것을 쓴다
        self.comp = self._new_compactor()
        self.rec.event("context_budget", tokens=tokens, window=self.backend.budget())
        return self.backend.budget()

    def undo_turn(self, turn: int, files: bool = True, cut: bool = True) -> dict:
        """판 105(SW-45 ①): 차례 turn 을 되감는다 — files 면 파일을 그 차례 시작 때로(그 뒤 차례들의 도구 쓰기 포함, 셸이 바꾼 것은 못 되돌림),
        cut 이면 대화를 그 차례 사람 말 앞에서 자른다(그 말은 돌려줘서 사람이 고쳐 다시 보낸다). {restored, not_restored, text, dropped}."""
        at = getattr(self, "_turn_at", {}).get(turn)
        cp = getattr(self, "_turn_cp", {}).get(turn)
        if at is None or cp is None:
            raise ValueError("그 차례의 자리를 모른다(이 대화에서 시작한 차례만 되감는다)")
        idx, raw = at
        out = {"turn": turn, "text": raw, "restored": [], "not_restored": [], "dropped": 0}
        if cut:
            m = self.messages[idx] if idx < len(self.messages) else None
            ok = m is not None and m.get("role") == "user" and raw[:200] in IMG.text_of(m.get("content"))
            if not ok:
                raise ValueError("접기·요약 뒤라 그 차례 자리를 찾지 못했다 — 파일만 되돌릴 수 있다")
        if files:
            try:
                rep = self.cps.rewind(cp)
                out["restored"] = [os.path.relpath(f, self.profile.workdir) for f in rep["restored"] + rep["moved_aside"]]
                out["not_restored"] = rep.get("skipped") or []
            except ValueError:
                pass   # 그 차례 뒤로 바뀐 파일이 없다
        if cut:
            out["dropped"] = len(self.messages) - idx
            self.messages = self.messages[:idx]
            self._turn_at = {t: v for t, v in self._turn_at.items() if t < turn}
            self._turn_cp = {t: v for t, v in self._turn_cp.items() if t < turn}
            self._rewind_note = None
            save_messages(self.rec.dir, self.messages)
        elif files and out["restored"]:
            self._rewind_note = (f"[사람이 파일을 차례 {turn} 시작 때로 되돌렸다. 되돌린 파일: " + ", ".join(out["restored"])
                                 + ". 그 뒤 네가 한 파일 변경은 지금 없다. 필요하면 다시 읽어라.]")
        self.rec.event("turn_undo", turn=turn, files=files, cut=cut, restored=len(out["restored"]), dropped=out["dropped"])
        return out

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
            "turns": st["turns"], "steps": st["steps"], "elapsed_s": time.monotonic() - self.t0,
            "model_time_s": st["model_time_s"], "tool_calls": st["tool_calls"], "tool_errors": st["tool_errors"],
            "parse_failures": st["parse_failures"], "format_failures": 0, "denied": st["denied"],
            "interrupts": st["interrupts"], "reflects": st.get("reflects", 0), "compactions": st["compactions"], "compact_log": self.comp.log,
            "files_changed": sorted(st["files_changed"]), "skills_loaded": self.skills_loaded, "delegations": self.delegations,
            "ledger": self.ledger.brief() if self.ledger is not None else None,
            "completion_tokens": st["completion_tokens"], "prompt_tokens_total": st["prompt_tokens_total"],
            "last_prompt_tokens": st["last_prompt_tokens"],
            "gen_tok_per_s": (st["pred_n"] / (st["pred_ms"] / 1000)) if st["pred_ms"] else None,
            "prompt_tok_per_s": (st["prompt_n"] / (st["prompt_ms"] / 1000)) if st["prompt_ms"] else None,
            "e2e_tok_per_s": (st["completion_tokens"] / st["model_time_s"]) if st["model_time_s"] else None,
            "tool_error_kinds": dict(st.get("tool_error_kinds") or {}), "features": self.meta.get("features"),
            **self._outcome(),
            "run_dir": str(self.rec.dir),
        }

    # 판 114: 한 번 돌리기(--once)·끝 판정 — run 길과 같은 칸(stop_reason·verify·success)
    RUN_REASON = {"answered": "completed"}

    def _outcome(self) -> dict:
        once = getattr(self, "_once", None)
        if once is not None:
            return {"stop_reason": once["stop_reason"], "verify": once["verify"],
                    "asked": self._last_answer()[:2000] if once["stop_reason"] == "asked" else None,
                    "success": once["verify"]["passed"] if once["verify"] else once["stop_reason"] == "completed"}
        last = getattr(self, "_last_reason", None)   # 대화형: 마지막 차례가 답으로 끝났을 때만 참(전에는 늘 참)
        return {"stop_reason": "chat_end", "verify": None, "success": bool(last == "answered"),
                "last_turn_reason": last}

    def run_once(self, task: str) -> dict:
        """판 114: 과제 하나를 사람 없이 한 차례 돌리고(묻기·max_steps 면 그대로 끝), 프로필 verify_command 로 판정한다."""
        reason = self.turn(task)
        stop = self.RUN_REASON.get(reason, reason or "completed")
        verify = None
        if self.profile.verify_command:
            from .osdep import ShellNotFound, run_command
            from .shell import tool_env
            try:
                p = run_command(self.profile.verify_command, cwd=self.profile.workdir, capture_output=True,
                                timeout=self.profile.shell_timeout_s * 5, env=tool_env())
                verify = {"command": self.profile.verify_command, "exit": p.returncode, "passed": p.returncode == 0,
                          "output": (p.stdout + p.stderr)[-6000:]}
            except ShellNotFound as e:
                verify = {"command": self.profile.verify_command, "exit": "no_shell", "passed": False, "output": f"판정 명령을 돌릴 셸이 없다: {e}"}
            except Exception as e:  # noqa: BLE001 — 시간 초과 등
                verify = {"command": self.profile.verify_command, "exit": "error", "passed": False, "output": f"{type(e).__name__}: {e}"[:2000]}
            self.rec.event("verify", stage="final", **verify)
        self._once = {"stop_reason": stop, "verify": verify}
        return self._once

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
        self.rec.close(s, self._last_answer() if getattr(self, "_once", None) is not None else None)   # --once 는 run 처럼 output.md
        return s

    def _last_answer(self) -> str:
        for m in reversed(self.messages):
            if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"].strip():
                return m["content"]
        return ""


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
    "/image": ("/image <파일> 다음 입력에 이미지 붙이기(vision 백엔드만, 작업 폴더 밖도 됨)", lambda a: True),
    "/good": ("/good [메모] 바로 앞 차례를 좋은 예로 표시(학습 자료 고르기, 내용은 안 바뀜)", lambda a: True),
    "/bad": ("/bad [메모] 바로 앞 차례를 나쁜 예로 표시", lambda a: True),
}

HELP = (MODE_HELP + "\n"
        "명령: /exit 끝내기 · /reset 대화 지우기 · /status 상태 · /confirm on|off 쓰기·셸 전에 묻기\n"
        "      /plan 정리 계획 요약·파일 경로 · /plan on|off · /stream on|off\n"
        "      /think on|off|default 모델 생각(thinking) 켜고 끄기 · /steps N 차례당 단계 상한(닿으면 계속할지 묻는다) · /compact 오래된 도구 결과 줄이기 · /help\n"
        "      /ledger 관리자 장부 보기(-p manager) · /skill [이름 [글]] 스킬 목록·고른 스킬 본문을 다음 입력(또는 글)에 붙이기\n"
        "      /undo 파일을 바꾼 마지막 차례 되돌리기 · /rewind [N] 체크포인트 목록·N 번 차례 시작 때로 되돌리기 · /facts 한 일\n"
        "      /good·/bad [메모] 바로 앞 차례를 좋은·나쁜 예로 표시(stewart train export 가 고른다)\n"
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
    pending_images: list = []   # /image 로 붙인 것(다음 입력과 함께 간다)
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
            if kind[0] in ("msg", "typo") and (exp := session.expand_command(t)) is not None:
                ui.dim(f"  ({t.split()[0]} → 프로젝트 명령 본문을 보낸다)")
                kind, t, text = ("msg",), exp, exp
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
                    ui.say(HELP + ("\n" + PC.commands_help(session.commands) if session.commands else ""))
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
                elif cmd == "/image":   # 다음 입력에 이미지 붙이기(images.py)
                    if not arg.strip():
                        ui.info(f"쓰는 법: {COMMANDS['/image'][0]} (붙인 것 {len(pending_images)}장)")
                    elif not session.backend.vision:
                        ui.warn(f"이 백엔드({session.backend.name})는 이미지를 받지 않는다(backends 표 vision = false).")
                    else:
                        try:
                            part, desc = IMG.from_file(arg.strip(), session.rec.dir)
                            pending_images.append(part)
                            ui.info(f"이미지 {len(pending_images)}장 붙임: {desc} — 다음 입력과 함께 보낸다")
                        except IMG.ImageError as e:
                            ui.warn(f"이미지를 붙이지 않았다: {e}")
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
                elif cmd in ("/good", "/bad"):
                    if session.label(session.st["turns"], cmd[1:], note=arg.strip()):
                        ui.info(f"차례 {session.st['turns']} 에 '{'좋음' if cmd == '/good' else '나쁨'}' 표시(기록에만, 내보내기: stewart train export)")
                    else:
                        ui.warn("표시할 차례가 아직 없다")
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
            if pending_images:
                session.turn(text, images=pending_images)
                pending_images = []
            else:
                session.turn(text)
    finally:
        s = session.close()
        ui.dim(f"\n기록: {session.rec.dir / 'transcript.md'}")
        dream_at_close(session, ui)
    return s


def dream_at_close(session, ui=None, ask_model=None, probe_fn=None) -> dict | None:
    """chat 을 닫을 때 꿈꾸기 자동(판 54): 새 메모가 N개 넘은 대상만, 모델 서버가 이미 준비됐을 때만(찔러 보기 한 번 — 깨우지 않는다)."""
    from . import dream as DR
    mem = getattr(session, "mem", None)
    if mem is None or not DR.auto_enabled() or not DR.due(mem.dir):
        return None
    from .modelstat import probe

    def ready():
        try:
            return (probe_fn or probe)(session.backend, timeout=3.0, pod=False).get("state") == "ok"
        except Exception:
            return False
    try:
        r = DR.auto(mem.dir, ask_model or DR.model_caller(session.backend), mem.runs_dir, ready)
    except Exception as e:
        r = {"status": f"못 함: {type(e).__name__}", "results": []}
    if ui is not None and r["status"] != "할 것 없음":
        ui.dim(f"  기억 정리(꿈꾸기): {r['status']} " + ", ".join(f"{x['subject']} {x['status']}" for x in r["results"]))
    return r
