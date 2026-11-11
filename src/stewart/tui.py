"""stewart chat 화면(TUI) — 아래 고정 입력창·진행 표시·도구 블록·마크다운·상태 줄, 답변 도중 끼워 넣기.

구조
- 차례 실행(ChatSession.turn)은 지금처럼 main 스레드에서 돈다. 화면(prompt_toolkit Application)은 따로 스레드에서 돈다.
- 끝난 덩어리(문단·표·코드 블록·도구 블록)는 렌더링해 터미널 스크롤 위로 올린다(전체 화면 전환 없음 — 기록이 남는다).
  아직 쓰는 중인 덩어리·스피너·줄 선 메시지·입력창·상태 줄만 아래 칸에서 다시 그린다.
- 입력창은 늘 살아 있다. 모델이 일하는 동안 Enter 로 친 글은 줄(inbox)에 세웠다가 다음 요청 앞에 user 메시지로 들어간다
  (chat.ChatSession._interject — 표지 [tui-interject]). 마지막 답 단계였으면 차례가 끝난 뒤 바로 다음 차례 입력이 된다.
- 확인 질문(ui.ask — 셸 y/n·계속할까)은 그 답으로만 받는다. 줄 선 글은 답으로 쓰지 않는다. 쓰던 글은 따로 두었다 되돌린다.
- 중단: Ctrl-C(어디서나), Esc(로컬에서 일하는 중). SSH 로 들어온 세션(SSH_TTY·SSH_CONNECTION)에서는 Esc 를 중단에 쓰지 않는다
  (화살표·Alt 키의 이스케이프 열이 늦게 갈라져 오면 Esc 하나로 잘못 읽힐 수 있다) — 지우기·줄 선 마지막 글 꺼내기만.
  화면이 raw 모드라 Ctrl-C 는 키로 들어온다. 차례 안(armed)일 때만 main 스레드에 SIGINT 를 보내 지금의 KeyboardInterrupt 경로를 탄다.
- 비대화·좁은 화면·TERM=dumb·STEWART_TUI=0·라이브러리 없음이면 pick_ui 가 None 을 돌려 지금 화면(chat.UI)을 쓴다.
"""

from __future__ import annotations

import asyncio
import collections
import io
import os
import re
import shutil
import signal
import sys
import threading
import time

from . import osdep
from .chat import UI, WRITE_TOOLS

MIN_COLS = 60          # 이보다 좁으면 지금 화면
FOLD_MAX = 10          # 도구 본문이 이 줄 수를 넘으면 접는다
FOLD_HEAD, FOLD_TAIL = 3, 4
KEEP_OUTPUTS = 50      # Ctrl-O 로 다시 찍을 도구 전체 출력(최근 것만)
LIVE_LINES = 6         # 아래 칸에 보일 쓰는 중 덩어리 줄 수
INPUT_MAX_LINES = 8
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
RESIZE_HINT_S = 8.0    # 창이 좁아진 뒤 상태 줄에 "Ctrl-L 다시 그리기" 를 보이는 시간
_EOF = object()

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean_line(s: str) -> str:
    """도구 출력 한 줄에서 터미널을 흐트러뜨리는 제어 문자(ANSI·CR 등)를 뺀다."""
    return _CTRL.sub("", _ANSI.sub("", s.replace("\t", "    ")))


def strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


def is_ssh(env=os.environ) -> bool:
    return bool(env.get("SSH_TTY") or env.get("SSH_CONNECTION"))


def plain_reason(choice: str = "auto", env=os.environ, stdin=None, stdout=None, cols: int | None = None) -> str | None:
    """지금 화면으로 돌아갈 이유(없으면 None = TUI). choice: auto | on | off."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    if choice == "off":
        return "--tui off"
    try:
        tty = stdin.isatty() and stdout.isatty()
    except (AttributeError, ValueError):
        tty = False
    if not tty:
        return "터미널이 아님(파이프·리다이렉트)"
    if choice == "on":
        return None
    if osdep.IS_WIN:
        # Windows 콘솔에는 보통 TERM 이 없다(WIN-4). TERM 대신 콘솔이 VT 처리를 켤 수 있는지로 가른다
        if env.get("TERM", "") == "dumb":
            return "TERM='dumb'"
        why = osdep.win_console_vt()
        if why:
            return why
    elif env.get("TERM", "") in ("", "dumb"):
        return f"TERM={env.get('TERM', '')!r}"
    if env.get("STEWART_TUI", "") == "0":
        return "STEWART_TUI=0"
    cols = shutil.get_terminal_size((0, 0)).columns if cols is None else cols
    if cols < MIN_COLS:
        return f"화면 폭 {cols}칸(<{MIN_COLS})"
    return None


def pick_ui(choice: str = "auto", env=os.environ) -> "TuiUI | None":
    """chat 에 쓸 UI. None 이면 지금 화면(chat.UI). TuiUI 면 화면이 이미 떠 있다(끝날 때 stop())."""
    why = plain_reason(choice, env)
    if why:
        if choice == "on":
            print(f"stewart: TUI 를 쓰지 않는다 — {why}", file=sys.stderr)
        return None
    try:
        import prompt_toolkit  # noqa: F401
        import rich  # noqa: F401
    except ImportError as e:
        print(f"\033[2mstewart: TUI 라이브러리를 불러오지 못해 지금 화면으로 — {e}\033[0m", file=sys.stderr)
        return None
    try:
        # libedit(macOS 의 readline)은 import 때 터미널을 실행 모드(ICANON·ECHO 켜짐)로 tcsetattr 한다.
        # 화면이 raw 모드에 들어간 뒤 repl 이 처음 import 하면 raw 가 풀려 키·CPR 응답이 글자로 메아리친다
        # (11/8 17:3x 눈 확인 A-1). 화면을 띄우기 전에 먼저 불러 둔다(뒤의 import 는 아무 일도 안 함).
        import readline  # noqa: F401
    except ImportError:
        pass
    ui = TuiUI(ssh=is_ssh(env))
    ui.start()
    why = ui.check_terminal()
    if why:                       # raw 모드·CPR 이 안 되면 경고 없이 지금 화면으로
        ui.stop()
        if choice == "on":
            print(f"stewart: TUI 를 쓰지 않는다 — {why}", file=sys.stderr)
        return None
    return ui


# ── 마크다운 덩어리 나누기 ─────────────────────────────────────────────────
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


class MdChunker:
    """스트림 조각을 받아 끝난 마크다운 덩어리를 돌려준다. 덩어리 끝 = 빈 줄(코드 펜스 밖), 또는 펜스가 닫힐 때."""

    def __init__(self):
        self.lines: list[str] = []
        self.tail = ""
        self.fence: str | None = None

    def feed(self, text: str) -> list[str]:
        self.tail += text
        out: list[str] = []
        while "\n" in self.tail:
            line, self.tail = self.tail.split("\n", 1)
            out += self._line(line)
        return out

    def _line(self, line: str) -> list[str]:
        m = _FENCE.match(line)
        if self.fence:
            self.lines.append(line)
            if m and m.group(1)[0] == self.fence[0] and len(m.group(1)) >= len(self.fence) and not line.strip()[len(m.group(1)):]:
                self.fence = None
                return self._cut()
            return []
        if m:
            prev = self._cut()          # 펜스 앞 문단은 따로
            self.fence = m.group(1)
            self.lines.append(line)
            return prev
        if not line.strip():
            return self._cut()
        self.lines.append(line)
        return []

    def _cut(self) -> list[str]:
        b, self.lines = self.lines, []
        return ["\n".join(b)] if any(l.strip() for l in b) else []

    def partial(self) -> str:
        return "\n".join(self.lines + ([self.tail] if self.tail else []))

    def flush(self) -> list[str]:
        if self.tail:
            self.lines.append(self.tail)
            self.tail = ""
        self.fence = None
        return self._cut()


def fold(lines: list[str], max_lines: int = FOLD_MAX, head: int = FOLD_HEAD, tail: int = FOLD_TAIL) -> tuple[list[str], int]:
    """(보일 줄, 접힌 줄 수). 접힌 자리에는 None 을 둔다."""
    if len(lines) <= max_lines:
        return list(lines), 0
    hidden = len(lines) - head - tail
    return lines[:head] + [None] + lines[-tail:], hidden


def color_system(env=os.environ) -> str:
    return "truecolor" if env.get("COLORTERM", "").lower() in ("truecolor", "24bit") else "256"


def render_md(text: str, width: int, colors: str = "256") -> str:
    """마크다운 → ANSI 문자열(제목은 왼쪽 맞춤, 표는 한글 폭 맞춤)."""
    from rich.console import Console
    from rich.markdown import Heading, Markdown

    class _Left(Heading):
        LEVEL_ALIGN = {k: "left" for k in Heading.LEVEL_ALIGN}

    class _Md(Markdown):
        elements = {**Markdown.elements, "heading_open": _Left}

    buf = io.StringIO()
    c = Console(file=buf, width=max(20, width), force_terminal=True, color_system=colors, legacy_windows=False,
                emoji=False, highlight=False)
    c.print(_Md(text, code_theme="monokai", hyperlinks=False))
    return buf.getvalue().rstrip("\n")


# ── 터미널 차이 ───────────────────────────────────────────────────────────
SHIFT_ENTER_SEQS = ("\x1b[13;2u",      # CSI u(iTerm2 키 매핑·kitty 등)
                    "\x1b[27;2;13~")   # xterm modifyOtherKeys(prompt_toolkit 기본은 이것을 Enter 로 읽는다)
SHIFT_ENTER_KEY = "f24"                 # 쓰지 않는 키 이름에 묶어 줄바꿈으로 받는다


def install_shift_enter() -> None:
    """Shift-Enter 신호를 줄바꿈 키로 읽게 prompt_toolkit 의 키 열 표를 고친다(한 번만)."""
    from prompt_toolkit.input import ansi_escape_sequences as A, vt100_parser as V
    from prompt_toolkit.keys import Keys
    changed = False
    for seq in SHIFT_ENTER_SEQS:
        if A.ANSI_SEQUENCES.get(seq) is not Keys.F24:
            A.ANSI_SEQUENCES[seq] = Keys.F24
            changed = True
    if changed:
        V._IS_PREFIX_OF_LONGER_MATCH_CACHE.clear()


def _k(n: int | float) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else f"{int(n)}"


# ── 입력 상태(스레드 둘 사이) ────────────────────────────────────────────
class InputState:
    """idle(사람 차례 입력을 기다림) · working(모델이 일함) · question(확인 질문의 답을 기다림)."""

    def __init__(self):
        self.cv = threading.Condition()
        self.mode = "working"
        self.label = ""
        self.inbox: list[str] = []
        self._answer = None
        self.dead = False       # 화면 스레드가 죽었으면 기다리지 않는다(EOFError)

    # UI 스레드
    def submit(self, text: str) -> str:
        with self.cv:
            if self.mode in ("idle", "question"):
                self._answer = text
                self.cv.notify_all()
                return self.mode
            if text.strip():
                self.inbox.append(text)
            return "queued"

    def eof(self) -> bool:
        with self.cv:
            if self.mode != "idle":
                return False
            self._answer = _EOF
            self.cv.notify_all()
            return True

    def pop_last(self) -> str | None:
        with self.cv:
            return self.inbox.pop() if self.inbox else None

    # main 스레드
    def take(self) -> list[str]:
        with self.cv:
            out, self.inbox = self.inbox, []
            return out

    def wait(self, mode: str, label: str = "") -> str:
        """답이 올 때까지(사이에 SIGINT 가 오면 KeyboardInterrupt 가 여기서 난다). EOF 면 EOFError.
        idle 은 잠금 안에서 줄 선 글부터 본다 — 꺼내기와 기다리기 사이에 Enter 가 오면 글이 줄에 남은 채 멈추던 틈(공개 시험 u76-4)."""
        with self.cv:
            if mode == "idle" and self.inbox:
                out, self.inbox = self.inbox, []
                return "\n\n".join(out)
            self.mode, self.label, self._answer = mode, label, None
            try:
                while self._answer is None:
                    if self.dead:
                        raise EOFError
                    self.cv.wait(0.1)
                a = self._answer
            finally:
                self.mode, self.label, self._answer = "working", "", None
        if a is _EOF:
            raise EOFError
        return a


# ── 화면 ────────────────────────────────────────────────────────────────
class _Proxy(io.TextIOBase):
    """화면이 떠 있는 동안 다른 코드의 print(sys.stdout·stderr)도 위 스크롤로 보낸다."""

    def __init__(self, ui: "TuiUI"):
        self.ui = ui

    def write(self, s: str) -> int:
        self.ui._emit_raw(s)
        return len(s)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


class TuiUI(UI):
    def __init__(self, ssh: bool = False, input=None, output=None, colors: str | None = None):
        super().__init__(out=io.StringIO(), color=True)
        self.ssh = ssh
        self.colors = colors or color_system()
        self.state = InputState()
        self.log = io.StringIO()            # 위로 올린 글 전부(시험·디버깅용)
        self._pending: list[str] = []
        self._plock = threading.Lock()
        self._partial_line = ""             # print 의 줄바꿈 없는 조각
        self.session = None
        self._armed = False
        self._interrupted = False
        self._main = threading.main_thread().ident
        self._old_sigint = None
        self._block: dict | None = None
        self.outputs: collections.deque = collections.deque(maxlen=KEEP_OUTPUTS)
        self._show_back = 0
        self._chunk: MdChunker | None = None
        self._first_md = True
        self._think_chars = 0
        self.phase = ""
        self.t_turn = time.monotonic()
        self.app = None
        self._thread = None
        self._running = threading.Event()
        self._stopped = False
        self._in, self._out = input, output
        self._saved_std = None
        self._draft = ""
        self._dead = None
        self.nl_key = "Ctrl-J"
        self._resize_hint_until = 0.0
        self.redraws = 0

    # ---- 시작·끝 ----------------------------------------------------------
    def start(self) -> None:
        from prompt_toolkit.application import Application
        from prompt_toolkit.buffer import Buffer
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.output import create_output

        out = self._out or create_output(stdout=sys.__stdout__)
        self.buffer = Buffer(multiline=True, history=InMemoryHistory())
        self.app = Application(layout=self._layout(), key_bindings=self._keys(), full_screen=False,
                               refresh_interval=0.15, terminal_size_polling_interval=0.5, erase_when_done=True,
                               input=self._in, output=out, mouse_support=False)
        self.app.ttimeoutlen = 0.3 if self.ssh else 0.05
        self.app.renderer.cpr_not_supported_callback = None   # prompt_toolkit 의 CPR 경고를 찍지 않는다(check_terminal 이 판단)
        orig_resize = self.app._on_resize

        def on_resize():
            # 폭이 줄면 터미널(Terminal.app·iTerm2)이 이미 그린 줄을 다시 접는다. 접는 방식이 터미널마다 달라
            # 몇 줄을 지울지 알 수 없다(CPR 도 지금 커서 줄만 알려 준다) — 지우기는 prompt_toolkit 기본대로 두고
            # 남은 조각은 Ctrl-L(redraw)로 치우게 상태 줄에 잠깐 알린다(11/8 T17).
            try:
                old, new = self.app.renderer._last_size, self.app.output.get_size()
                if old is not None and new.columns < old.columns:
                    self._resize_hint_until = time.monotonic() + RESIZE_HINT_S
            except Exception:
                pass
            orig_resize()
        self.app._on_resize = on_resize
        self.app.timeoutlen = 0.3
        self._thread = threading.Thread(target=self._run, name="stewart-tui", daemon=True)
        self._thread.start()
        self._running.wait(5)
        self._saved_std = (sys.stdout, sys.stderr)
        sys.stdout = sys.stderr = _Proxy(self)

    def _run(self) -> None:
        def pre():
            self.app.create_background_task(self._printer())
            self._running.set()
        try:
            self.app.run(pre_run=pre, handle_sigint=False)
        except BaseException as e:   # 화면이 죽어도 main 이 멈추지 않게: 입력 대기는 EOFError, 글은 바로 쓴다
            self._dead = repr(e)
        finally:
            self.state.dead = True
            with self.state.cv:
                self.state.cv.notify_all()
            self._running.set()

    def bind(self, session) -> None:
        """세션을 붙인다(상태 줄·Ctrl-C). main 스레드에서 부른다."""
        self.session = session
        orig = session.turn

        def turn(text: str) -> str:
            self.t_turn = time.monotonic()
            self.phase = "요청 보냄"
            self._armed = True
            try:
                return orig(text)
            except KeyboardInterrupt:     # 차례 안 처리 구역 밖(끝 정리 중)에서 난 중단: 채팅은 이어 간다
                self.warn("  중단했다.")
                return "interrupted"
            finally:
                self._armed = False
                self.phase = ""
        session.turn = turn
        if threading.current_thread() is threading.main_thread():
            self._old_sigint = signal.signal(signal.SIGINT, self._on_sigint)

    def _on_sigint(self, signum, frame):
        if self._armed:
            raise KeyboardInterrupt

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._close_block()
        if self._saved_std:
            sys.stdout, sys.stderr = self._saved_std
        if self._old_sigint is not None:
            signal.signal(signal.SIGINT, self._old_sigint)
        app = self.app
        if app is not None and app.is_running and app.loop is not None:
            done = threading.Event()

            async def final():
                await self._flush_once()
                app.exit()
                done.set()
            app.loop.call_soon_threadsafe(lambda: app.create_background_task(final()))
            done.wait(3)
        if self._thread is not None:
            self._thread.join(3)
        fd = self._tty_fd()
        if fd is not None:
            try:
                import termios
                termios.tcflush(fd, termios.TCIFLUSH)   # 늦게 온 CPR 응답이 셸 입력으로 새지 않게
            except Exception:
                pass
        rest = self._take_pending()
        if rest and self._out is None:
            sys.__stdout__.write(rest)
            sys.__stdout__.flush()

    # ---- 위 스크롤로 올리기 ------------------------------------------------
    def _emit_raw(self, s: str) -> None:
        if not s:
            return
        self._partial_line += s
        if "\n" not in self._partial_line:
            return
        whole, self._partial_line = self._partial_line.rsplit("\n", 1)
        self._emit(whole)

    def _emit(self, text: str) -> None:
        """한 줄 이상을 위로 올린다(끝 줄바꿈은 붙인다)."""
        t = text + "\n"
        self.log.write(t)
        with self._plock:
            self._pending.append(t)
        if self._stopped or self.state.dead:
            rest = self._take_pending()
            if self._out is None:
                sys.__stdout__.write(rest)
                sys.__stdout__.flush()

    def _take_pending(self) -> str:
        with self._plock:
            t, self._pending = "".join(self._pending), []
        return t

    async def _printer(self):
        n = 0
        while True:
            await asyncio.sleep(0.03)
            await self._flush_once()
            n += 1
            if n % 15 == 0:
                self.guard_raw()

    # ---- 터미널 상태 -------------------------------------------------------
    def redraw(self) -> None:
        """Ctrl-L: 보이는 화면을 위 기록(터미널 스크롤)으로 밀어 올리고 아래 칸을 새로 그린다. 화면 스레드에서 부른다.
        지운 줄은 없다 — 남은 조각도 기록 쪽으로 올라갈 뿐 보이는 화면은 깨끗해진다."""
        app, out = self.app, self.app.output
        app.renderer.erase(leave_alternate_screen=False)   # 지금 아래 칸
        rows = out.get_size().rows
        out.write_raw("\n" * max(1, rows))                # 화면 높이만큼 줄을 내려 보이는 것을 모두 스크롤로 올린다
        out.flush()
        app._request_absolute_cursor_position()
        app.invalidate()
        self.redraws += 1
        self._resize_hint_until = 0.0

    def _tty_fd(self) -> int | None:
        if self._in is not None:          # 시험(pipe 입력)
            return None
        try:
            return sys.__stdin__.fileno()
        except (AttributeError, ValueError, OSError):
            return None

    def check_terminal(self, timeout: float = 2.5, tcget=None, fd: int | None = -1) -> str | None:
        """화면을 띄운 뒤: raw 모드가 걸렸고 CPR 응답이 왔는지. 안 되면 이유(문자열), 되면 None.
        Windows: termios·CPR 이 없다(prompt_toolkit 이 콘솔 API 로 입력 모드를 건다) — 화면이 떴는지만 본다."""
        if osdep.IS_WIN and tcget is None:
            end = time.monotonic() + min(timeout, 1.0)
            while time.monotonic() < end:
                if self.state.dead:
                    return "화면을 띄우지 못함" + (f" ({self._dead})" if self._dead else "")
                if self._running.is_set():
                    return None
                time.sleep(0.02)
            return None if not self.state.dead else "화면을 띄우지 못함"
        import termios
        from prompt_toolkit.renderer import CPR_Support
        tcget = tcget or termios.tcgetattr
        fd = self._tty_fd() if fd == -1 else fd
        if fd is None:
            return None
        end = time.monotonic() + timeout
        raw = False
        while time.monotonic() < end:
            if self.state.dead:
                return "화면을 띄우지 못함" + (f" ({self._dead})" if self._dead else "")
            try:
                attrs = tcget(fd)
            except termios.error as e:
                return f"터미널 상태를 읽지 못함({e})"
            raw = not (attrs[3] & (termios.ECHO | termios.ICANON))
            cpr = self.app.renderer.cpr_support
            if raw and cpr == CPR_Support.SUPPORTED:
                self._raw_attrs = attrs
                return None
            if cpr == CPR_Support.NOT_SUPPORTED:
                return "터미널이 커서 위치 요청(CPR)에 답하지 않음"
            time.sleep(0.02)
        return "CPR 응답을 받지 못함" if raw else "터미널 raw 모드가 걸리지 않음"

    def guard_raw(self, tcget=None, tcset=None) -> bool:
        """떠 있는 동안 누가 터미널을 cooked 로 되돌렸으면(readline 등) raw 를 다시 건다. 다시 걸었으면 True."""
        if osdep.IS_WIN and tcget is None:
            return False                  # termios 없음(콘솔 입력 모드는 prompt_toolkit 이 쥔다)
        attrs = getattr(self, "_raw_attrs", None)
        fd = self._tty_fd() if tcget is None else 0
        if attrs is None or fd is None:
            return False
        import termios
        tcget, tcset = tcget or termios.tcgetattr, tcset or termios.tcsetattr
        try:
            if tcget(fd)[3] & (termios.ECHO | termios.ICANON):
                tcset(fd, termios.TCSANOW, attrs)
                self.raw_fixes = getattr(self, "raw_fixes", 0) + 1
                return True
        except termios.error:
            pass
        return False

    async def _flush_once(self):
        from prompt_toolkit.application import run_in_terminal
        text = self._take_pending()
        if text:
            def w():
                self.app.output.write_raw(text)
                self.app.output.flush()
            await run_in_terminal(w, in_executor=False)

    def width(self) -> int:
        try:
            if self.app is not None and self.app.is_running:
                return self.app.output.get_size().columns
        except Exception:
            pass
        return shutil.get_terminal_size((100, 30)).columns

    # ---- chat.UI 바꿔 끼우기 ------------------------------------------------
    def say(self, s: str = "") -> None:
        self._close_block()
        self._emit(s)

    def info(self, s: str) -> None:
        self.say(self._c("36", s))

    def dim(self, s: str) -> None:
        if self._block is not None:
            self._block["body"].append(("dim", s))
        else:
            self.say(self._c("2", s))

    def warn(self, s: str) -> None:
        if self._block is not None:
            self._block["body"].append(("warn", s))
            self._block["bad"] = True
        else:
            self.say(self._c("33", s))

    def diff(self, lines: list[str]) -> None:
        if self._block is None:
            return UI.diff(self, lines)
        self._block["body"] += [("diff", l) for l in lines]

    def assistant(self, s: str) -> None:
        self._close_block()
        self._first_md = True
        self._md(s)

    # 마크다운 덩어리 하나를 올린다(답의 첫 덩어리에 ● )
    def _md(self, block: str) -> None:
        w = self.width() - 3   # 앞 두 칸(● ) + 오른쪽 끝 자동 줄바꿈 피하기
        try:
            r = render_md(block, w, self.colors)
        except Exception:
            r = block
        lines = r.split("\n")
        mark = self._c("1", "● ") if self._first_md else "  "
        if not self._first_md:
            self._emit("")
        self._first_md = False
        self._emit("\n".join((mark if i == 0 else "  ") + l for i, l in enumerate(lines)))

    # 도구 블록
    def _bar(self, name: str, bad: bool = False) -> str:
        code = "31" if bad else ("32" if name in WRITE_TOOLS else "33" if name.startswith("run_shell") or name.startswith("shell_")
                                 else "36" if name.startswith("web_") else "34")
        return self._c(code, "▌")

    def tool(self, name: str, summary: str) -> None:
        self._close_block()
        self._block = {"name": name, "body": [], "bad": False, "t0": time.monotonic()}
        w = self.width()
        head = clean_line(f"{name} {summary}")
        if len(head) > w - 4:
            head = head[:w - 5] + "…"
        n, _, rest = head.partition(" ")
        self._emit(self._bar(name) + self._c("1;35", "⏺ " + n) + " " + rest)
        self.phase = f"{name} 실행 중"

    def tool_result(self, tc: dict) -> None:
        b = self._block
        self.outputs.append({"name": tc.get("name", ""), "result": tc.get("result") or "", "diff": tc.get("diff") or ""})
        self._show_back = 0
        if b is not None:
            b["bad"] = b["bad"] or not tc.get("ok", True)
            b["elapsed"] = tc.get("elapsed_s")
        self._close_block()
        self.phase = "처리 중"

    def _flush_block_body(self, folding: bool) -> None:
        b = self._block
        if b is None:
            return
        body, b["body"] = b["body"], []
        shown, hidden = fold(body) if folding else (body, 0)
        w = self.width()
        bar = self._bar(b["name"], b["bad"])
        out = []
        for item in shown:
            if item is None:
                out.append(bar + self._c("2", f"  … {hidden}줄 접힘 (Ctrl-O 전부 보기)"))
                continue
            kind, s = item
            s = clean_line(s.strip("\n").removeprefix("    "))
            if len(s) > w - 4:
                s = s[:w - 5] + "…"
            if kind == "diff":
                code = "32" if s.startswith("+") and not s.startswith("+++") else "31" if s.startswith("-") and not s.startswith("---") \
                    else "36" if s.startswith("@@") else "2"
                out.append(bar + "  " + self._c(code, s))
            else:
                out.append(bar + "  " + self._c("33" if kind == "warn" else "2", s))
        el = b.get("elapsed")
        if folding and el is not None and el >= 1.0:
            out.append(bar + self._c("2", f"  {'✗' if b['bad'] else '✓'} {el:.1f}초"))
        if out:
            self._emit("\n".join(out))

    def _close_block(self) -> None:
        if self._block is not None:
            self._flush_block_body(True)
            self._block = None

    def show_output(self) -> None:
        """Ctrl-O: 마지막(다시 누르면 그 앞) 도구 출력 전부를 찍는다."""
        if not self.outputs:
            self._emit(self._c("2", "  (다시 볼 도구 출력이 없다)"))
            return
        i = len(self.outputs) - 1 - (self._show_back % len(self.outputs))
        self._show_back += 1
        o = self.outputs[i]
        lines = (o["diff"] or o["result"]).splitlines()
        out = [self._c("1;35", f"── {o['name']} 전체 출력 {len(lines)}줄 ({len(self.outputs) - i}번째 최근) ──")]
        for l in lines:
            l = clean_line(l)
            if o["diff"]:
                code = "32" if l.startswith("+") else "31" if l.startswith("-") else "36" if l.startswith("@@") else "2"
                out.append(self._c(code, l))
            else:
                out.append(self._c("2", l))
        self._emit("\n".join(out))

    # 스트리밍
    def stream_start(self) -> None:
        self._close_block()
        self._chunk = MdChunker()
        self._first_md = True
        self._streamed = False
        self._think_chars = 0
        self.phase = "응답 기다림"

    def stream_piece(self, text: str, think: str) -> None:
        if think:
            self._think_chars += len(think)
            self.phase = f"생각 중 {self._think_chars:,}자"
        if text:
            self._streamed = True
            self.phase = "생성 중"
            for b in self._chunk.feed(text):
                self._md(b)

    def stream_end(self) -> bool:
        if self._chunk is not None:
            for b in self._chunk.flush():
                self._md(b)
            self._chunk = None
        if self._think_chars and not self._streamed:
            self._emit(self._c("2", f"  (생각 {self._think_chars:,}자)"))
        self.phase = "처리 중"
        return getattr(self, "_streamed", False)

    # 입력
    def read_line(self, prompt: str) -> str:
        self._close_block()
        if self._interrupted:          # 중단 뒤: 줄 선 글은 보내지 않고 입력창으로 되돌린다
            self._interrupted = False
            q = self.state.take()
            if q:
                self._call(lambda: self._prepend("\n\n".join(q)))
                self._emit(self._c("2", f"  (줄 선 메시지 {len(q)}개를 입력창으로 되돌렸다)"))
        else:
            q = self.state.take()
            if q:                      # 일하는 동안 친 글 → 바로 다음 차례 입력
                text = "\n\n".join(q)
                self._echo(text)
                return text
        text = self.state.wait("idle")
        self._echo(text)
        return text

    def ask(self, prompt: str) -> str:
        self._flush_block_body(False)
        p = prompt.strip()
        self._call(self._enter_question)      # 기다리는 동안 질문은 입력창 위 "? …" 한 줄로만 보인다
        try:
            a = self.state.wait("question", p)
        except BaseException:
            self._emit(self._c("33", "  " + p) + self._c("2", " → (중단)"))
            raise
        finally:
            self._call(self._leave_question)
        self._emit(self._c("33", "  " + p) + self._c("2", f" → {a or '(Enter)'}"))   # 답한 뒤 기록에 한 줄
        return a

    def take_interjections(self) -> list[str]:
        q = self.state.take()
        for t in q:
            self._echo(t, queued=True)
        return q

    def _echo(self, text: str, queued: bool = False) -> None:
        lines = text.split("\n")
        mark = "› " if not queued else "↳ "
        self._emit("\n".join(self._c("1", (mark if i == 0 else "  ") + l) for i, l in enumerate(lines)))
        if not queued:
            self._emit("")

    # UI 스레드에서 돌릴 일
    def _call(self, fn) -> None:
        app = self.app
        if app is not None and app.is_running and app.loop is not None:
            app.loop.call_soon_threadsafe(fn)
        else:
            fn()

    def _prepend(self, text: str) -> None:
        cur = self.buffer.text
        self.buffer.text = text + ("\n" + cur if cur else "")
        self.buffer.cursor_position = len(self.buffer.text)

    def _enter_question(self) -> None:
        self._draft = self.buffer.text
        self.buffer.reset()

    def _leave_question(self) -> None:
        if self._draft and not self.buffer.text:
            self.buffer.text = self._draft
            self.buffer.cursor_position = len(self._draft)
        self._draft = ""

    def interrupt(self) -> bool:
        """차례 안이면 main 스레드에 SIGINT. 보냈으면 True.
        Windows: pthread_kill 이 없다 — 받는 중인 연결을 끊고(backend.abort_streams) interrupt_main 으로 SIGINT 를 낸다."""
        if not self._armed:
            return False
        self._interrupted = True
        self.phase = "중단하는 중"
        if osdep.IS_WIN or not hasattr(signal, "pthread_kill"):
            import _thread
            from .backend import abort_streams
            abort_streams()
            _thread.interrupt_main(signal.SIGINT)
            return True
        try:
            signal.pthread_kill(self._main, signal.SIGINT)
        except (OSError, ValueError, AttributeError):
            return False
        return True

    # ---- 키 ---------------------------------------------------------------
    def _keys(self):
        from prompt_toolkit.key_binding import KeyBindings
        install_shift_enter()
        kb = KeyBindings()
        st = self.state

        @kb.add("enter", eager=True)
        def _(e):
            text = self.buffer.text
            if st.mode == "working" and not text.strip():
                return
            if text.strip():
                self.buffer.append_to_history()
            self.buffer.reset()
            if st.submit(text) == "question" and self._draft:
                pass   # 쓰던 글은 _leave_question 이 되돌린다

        def newline(name):
            def h(e):
                self.buffer.insert_text("\n")
                self.nl_key = name          # 상태 줄에는 실제로 써 본 줄바꿈 키를 보인다(기본 Ctrl-J)
            return h
        kb.add("escape", "enter")(newline("Alt-Enter"))
        kb.add("c-j")(newline("Ctrl-J"))
        kb.add(SHIFT_ENTER_KEY)(newline("Shift-Enter"))

        @kb.add("c-c", eager=True)
        def _(e):
            if st.mode in ("working", "question") and self.interrupt():
                return
            if self.buffer.text:
                self.buffer.reset()
            else:
                self._emit(self._c("2", "  (끝내려면 Ctrl-D 또는 /exit)"))

        @kb.add("escape")
        def _(e):
            if not self.ssh and st.mode in ("working", "question") and self.interrupt():
                return
            if self.buffer.text:
                self.buffer.reset()
            elif st.mode == "working":
                last = st.pop_last()
                if last is not None:
                    self.buffer.text = last
                    self.buffer.cursor_position = len(last)

        @kb.add("c-d", eager=True)
        def _(e):
            if not self.buffer.text:
                st.eof()
            else:
                self.buffer.delete()

        @kb.add("c-o")
        def _(e):
            self.show_output()

        @kb.add("c-l", eager=True)
        def _(e):
            self.redraw()

        return kb

    # ---- 아래 칸 ------------------------------------------------------------
    def _layout(self):
        from prompt_toolkit.formatted_text import ANSI
        from prompt_toolkit.layout import HSplit, Layout, Window
        from prompt_toolkit.layout.containers import ConditionalContainer
        from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
        from prompt_toolkit.layout.dimension import Dimension
        from prompt_toolkit.filters import Condition

        def live():
            return ANSI(self._live_text())

        def rule():
            return ANSI(self._c("2", "─" * max(1, self.width() - 1)))   # 끝 칸은 비운다

        def status():
            return ANSI(self._status_text())

        def prefix(line_no, wrap_count):
            if line_no == 0 and wrap_count == 0:
                st = self.state
                return ANSI(self._c("33;1", "? ") if st.mode == "question" else self._c("1", "› "))
            return "  "

        has_live = Condition(lambda: bool(self._live_text()))
        inp = Window(BufferControl(buffer=self.buffer), height=Dimension(min=1, max=INPUT_MAX_LINES), wrap_lines=True,
                     get_line_prefix=prefix, dont_extend_height=True)
        return Layout(HSplit([
            ConditionalContainer(Window(FormattedTextControl(live), dont_extend_height=True, wrap_lines=False), filter=has_live),
            Window(FormattedTextControl(rule), height=1),
            inp,
            Window(FormattedTextControl(rule), height=1),
            Window(FormattedTextControl(status), height=1),
        ]), focused_element=inp)

    def _live_text(self) -> str:
        w = self.width()
        out = []
        ch = self._chunk
        if ch is not None:
            part = ch.partial()
            if part:
                ls = part.split("\n")[-LIVE_LINES:]
                for i, l in enumerate(ls):
                    l = clean_line(l)
                    out.append(self._c("2", (("● " if self._first_md and i == 0 else "  ") + l)[:w - 1]))
        st = self.state
        if st.mode == "question":
            out.append(self._c("33;1", ("? " + st.label)[:w - 1]))
        elif st.mode == "working" and self._armed:
            sp = SPIN[int(time.monotonic() * 10) % len(SPIN)]
            steps = self.session.st["steps"] if self.session is not None else 0
            out.append(self._c("36", sp) + self._c("2", f" {self.phase or '처리 중'} · {time.monotonic() - self.t_turn:.0f}초 · 단계 {steps}"))
        for t in list(st.inbox):
            one = t.replace("\n", " ⏎ ")
            out.append(self._c("2", f"⏳ 줄에 섬: {one}"[:w - 1]))
        return "\n".join(out)

    def _status_text(self) -> str:
        s = self.session
        w = self.width()
        st = self.state
        if st.mode == "question":
            hint = "Enter 답 · Ctrl-C 중단" if self.ssh else "Enter 답 · Esc·Ctrl-C 중단"
        elif st.mode == "working" and self._armed:
            hint = "Enter 줄에 세움 · Ctrl-C 중단 · Esc 지우기" if self.ssh else "Enter 줄에 세움 · Esc·Ctrl-C 중단"
        else:
            hint = f"Enter 보냄 · {self.nl_key} 줄바꿈 · Ctrl-D 끝"
        left = ""
        if s is not None:
            x = s.st
            th = {None: "기본", True: "켜짐", False: "꺼짐"}.get(s.think, "?")
            left = (f"{s.mode} · think {th} · {_k(x['last_prompt_tokens'])}/{_k(s.backend.max_context)} tok"
                    f" · 생성 {_k(x['completion_tokens'])} · 모델 {x['model_time_s']:.0f}초 · {s.backend.model}")
        if time.monotonic() < self._resize_hint_until:
            left = "창이 줄었다 · Ctrl-L 다시 그리기"
            return self._status_pad(left, hint, w, warn=True)
        return self._status_pad(left, hint, w)

    def _status_pad(self, left: str, hint: str, w: int, warn: bool = False) -> str:
        from prompt_toolkit.utils import get_cwidth
        pad = w - get_cwidth(left) - get_cwidth(hint) - 1
        while pad < 1 and left:
            left = left[:-1]
            pad = w - get_cwidth(left) - get_cwidth(hint) - 1
        pad = max(1, pad)
        return self._c("33" if warn else "2", left) + " " * pad + self._c("2", hint)
