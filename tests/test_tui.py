"""chat 화면(TUI): 마크다운 덩어리·접기·고르기 조건, 입력 상태, 끼워 넣기·확인 질문·중단(가짜 입력 pipe + DummyOutput)."""

import io
import json
import tempfile
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from stewart import chat as C, tui as U
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_engine import _resp


def until(pred, timeout=5.0):
    t = time.monotonic() + timeout
    while time.monotonic() < t:
        if pred():
            return True
        time.sleep(0.02)
    raise AssertionError("기다린 상태가 오지 않았다")


class TChunk(unittest.TestCase):
    def test_blocks(self):
        c = U.MdChunker()
        self.assertEqual(c.feed("# 제목\n문단"), [])
        self.assertEqual(c.partial(), "# 제목\n문단")
        self.assertEqual(c.feed(" 끝\n\n"), ["# 제목\n문단 끝"])
        # 펜스 안 빈 줄은 덩어리 끝이 아니다. 펜스 앞 문단은 따로
        out = c.feed("앞 글\n```py\nx = 1\n\ny = 2\n``")
        self.assertEqual(out, ["앞 글"])
        self.assertEqual(c.feed("`\n"), ["```py\nx = 1\n\ny = 2\n```"])
        self.assertEqual(c.feed("| a | b |\n|---|---|\n| 한글 | 2 |"), [])
        self.assertEqual(c.flush(), ["| a | b |\n|---|---|\n| 한글 | 2 |"])
        self.assertEqual(c.flush(), [])

    def test_tilde_fence_and_longer_close(self):
        c = U.MdChunker()
        self.assertEqual(c.feed("~~~\n```\n\n~~~~\n"), ["~~~\n```\n\n~~~~"])

    def test_render_md_left_heading_and_table(self):
        r = U.strip_ansi(U.render_md("# 제목\n\n| 파일 | 줄 |\n|---|---|\n| 한글.py | 3 |", 50))
        self.assertTrue(r.startswith("제목"))       # h1 도 왼쪽(가운데 아님)
        self.assertIn("한글.py", r)

    def test_fold(self):
        self.assertEqual(U.fold(list("abc")), (list("abc"), 0))
        shown, hidden = U.fold([str(i) for i in range(20)])
        self.assertEqual(hidden, 13)
        self.assertEqual(shown, ["0", "1", "2", None, "16", "17", "18", "19"])

    def test_clean_line(self):
        self.assertEqual(U.clean_line("\x1b[31m빨강\x1b[0m\r끝\x07"), "빨강끝")


class _S(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self._t = tty

    def isatty(self):
        return self._t


class TPick(unittest.TestCase):
    def r(self, choice="auto", env=None, tty=True, cols=100):
        return U.plain_reason(choice, env if env is not None else {"TERM": "xterm-256color"}, _S(tty), _S(tty), cols)

    def test_conditions(self):
        self.assertIsNone(self.r())
        self.assertIn("터미널", self.r(tty=False))
        self.assertIn("TERM", self.r(env={"TERM": "dumb"}))
        self.assertIn("TERM", self.r(env={}))
        self.assertIn("STEWART_TUI", self.r(env={"TERM": "xterm", "STEWART_TUI": "0"}))
        self.assertIn("60", self.r(cols=59))
        self.assertIsNone(self.r(cols=60))
        self.assertIsNotNone(self.r("off"))
        # on: 좁아도·STEWART_TUI=0 이어도 쓰지만 터미널은 있어야 한다
        self.assertIsNone(self.r("on", env={"TERM": "dumb", "STEWART_TUI": "0"}, cols=30))
        self.assertIn("터미널", self.r("on", tty=False))

    def test_ssh(self):
        self.assertTrue(U.is_ssh({"SSH_TTY": "/dev/ttys001"}))
        self.assertTrue(U.is_ssh({"SSH_CONNECTION": "1 2 3 4"}))
        self.assertFalse(U.is_ssh({}))

    def test_pick_off_and_pipe_give_none(self):
        self.assertIsNone(U.pick_ui("off"))
        with mock.patch("sys.stdin", _S(False)):
            self.assertIsNone(U.pick_ui("auto"))


class TPickStart(unittest.TestCase):
    """실제 터미널 시작 경로(pty 없이): readline 을 화면보다 먼저 불러 두는지, 점검 실패면 조용히 지금 화면."""

    def _pick(self, choice, why):
        seen = {}

        def start(ui):
            seen["readline_before_start"] = "readline" in sys.modules
        stops = []
        with mock.patch.dict(sys.modules), mock.patch.object(U, "plain_reason", return_value=None), \
                mock.patch.object(U.TuiUI, "start", start), mock.patch.object(U.TuiUI, "check_terminal", lambda ui: why), \
                mock.patch.object(U.TuiUI, "stop", lambda ui: stops.append(1)), mock.patch("sys.stderr", io.StringIO()) as err:
            sys.modules.pop("readline", None)
            ui = U.pick_ui(choice)
        return ui, seen, stops, err.getvalue()

    def test_readline_imported_before_screen(self):
        ui, seen, stops, err = self._pick("auto", None)
        self.assertIsInstance(ui, U.TuiUI)
        self.assertTrue(seen["readline_before_start"])   # libedit 이 raw 를 풀지 못하게
        self.assertEqual((stops, err), ([], ""))

    def test_check_fails_falls_back_quietly(self):
        ui, seen, stops, err = self._pick("auto", "터미널 raw 모드가 걸리지 않음")
        self.assertIsNone(ui)
        self.assertEqual(stops, [1])
        self.assertEqual(err, "")                          # auto 는 조용히
        ui, seen, stops, err = self._pick("on", "CPR 응답을 받지 못함")
        self.assertIsNone(ui)
        self.assertIn("CPR 응답을 받지 못함", err)          # on 은 이유 한 줄
        self.assertEqual(len(err.strip().splitlines()), 1)


class TState(unittest.TestCase):
    def test_submit_by_mode(self):
        s = U.InputState()
        self.assertEqual(s.submit("a"), "queued")
        self.assertEqual(s.submit("  "), "queued")
        self.assertEqual(s.inbox, ["a"])
        got = []
        t = threading.Thread(target=lambda: got.append(s.wait("question", "y/n?")))
        t.start()
        until(lambda: s.mode == "question")
        self.assertEqual(s.submit("n"), "question")
        t.join(2)
        self.assertEqual(got, ["n"])
        self.assertEqual(s.inbox, ["a"])            # 줄 선 글은 답으로 쓰지 않는다
        self.assertEqual(s.mode, "working")
        self.assertFalse(s.eof())                   # working 에서는 Ctrl-D 무시


class TIdleRace(unittest.TestCase):
    def test_queued_text_is_taken_by_idle_wait(self):
        """read_line 이 줄을 비운 뒤 기다리기 전에 Enter 가 오면(working 중 submit → 줄) idle 기다리기가 그 글을 바로 가져간다."""
        s = U.InputState()
        self.assertEqual(s.submit("사이에 친 글"), "queued")
        t0 = time.monotonic()
        self.assertEqual(s.wait("idle"), "사이에 친 글")
        self.assertLess(time.monotonic() - t0, 1)
        self.assertEqual(s.inbox, [])


class TApp(unittest.TestCase):
    """실제 prompt_toolkit Application 을 pipe 입력으로 돌린다."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a.py").write_text("x = 1\n")
        cm = create_pipe_input()            # cm 을 잡아 둔다(놓치면 pipe 가 닫혀 화면이 EOF 로 끝난다)
        self.pipe = cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        self.bk = Backend("fake", "http://x/v1", "m", "q", 4096, params={})

    def ui(self, ssh=False):
        ui = U.TuiUI(ssh=ssh, input=self.pipe, output=DummyOutput())
        ui.start()
        self.addCleanup(ui.stop)
        return ui

    def sess(self, ui, mode="auto"):
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.tmp, max_steps=10)
        s = C.ChatSession(p, self.bk, Recorder(self.tmp / "runs", f"chat-{mode}-{id(ui)}"), ui, mode=mode)
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        self.addCleanup(lambda: C.T.close_env(s.env))   # 셸을 닫는다(판 68 시험 감시)
        ui.bind(s)
        return s

    def events(self, s, kind):
        return [json.loads(l) for l in (s.rec.dir / "events.jsonl").read_text().splitlines() if json.loads(l)["kind"] == kind]

    def log(self, ui):
        return U.strip_ansi(ui.log.getvalue())

    def test_read_line_and_eof(self):
        ui = self.ui()
        self.pipe.send_text("안녕 하세요\r")
        self.assertEqual(ui.read_line("› "), "안녕 하세요")
        self.pipe.send_text("첫 줄\x1b\r둘째 줄\r")      # Alt-Enter = 줄바꿈
        self.assertEqual(ui.read_line("› "), "첫 줄\n둘째 줄")
        got = []

        def eof_later():   # Ctrl-D 는 기다리는 중(idle)에만 뜻이 있다 — 일하는 중 Ctrl-D 는 무시(설계)
            until(lambda: ui.state.mode == "idle")
            self.pipe.send_text("\x04")
        t = threading.Thread(target=eof_later, daemon=True)
        t.start()
        with self.assertRaises(EOFError):
            got.append(ui.read_line("› "))
        t.join(5)

    def test_interject_after_tool_step(self):
        ui = self.ui()
        s = self.sess(ui)
        seen = []

        def fake(bk, msgs, tools, **kw):
            seen.append([dict(m) for m in msgs])
            if len(seen) == 1:      # 첫 요청 도중 사람이 친다
                self.pipe.send_text("b.py 는 건드리지 마\r")
                until(lambda: ui.state.inbox)
                return _resp(calls=[("read_file", '{"path": "a.py"}')])
            return _resp("알았다.")
        with mock.patch.object(C, "chat", side_effect=fake):
            self.assertEqual(s.turn("a.py 봐"), "answered")
        last = seen[1]
        self.assertEqual([m["role"] for m in last[-3:]], ["assistant", "tool", "user"])   # 도구 결과 직후
        self.assertEqual(last[-1]["content"], "b.py 는 건드리지 마")                     # 꾸밈 없이
        self.assertEqual(self.events(s, "interject")[0]["n"], 1)
        self.assertEqual(ui.state.inbox, [])
        self.assertIn("↳ b.py 는 건드리지 마", self.log(ui))

    def test_queued_during_final_answer_is_next_input(self):
        ui = self.ui()
        s = self.sess(ui)

        def fake(bk, msgs, tools, **kw):
            self.pipe.send_text("하나 더\r")
            until(lambda: ui.state.inbox)
            return _resp("끝.")
        with mock.patch.object(C, "chat", side_effect=fake):
            s.turn("해")
        self.assertEqual(ui.read_line("› "), "하나 더")    # Enter 다시 칠 필요 없음

    def test_question_not_answered_by_queue_and_draft_restored(self):
        ui = self.ui()
        s = self.sess(ui, mode="ask")
        seen = []

        def fake(bk, msgs, tools, **kw):
            seen.append([dict(m) for m in msgs])
            if len(seen) == 1:
                self.pipe.send_text("줄 선 글\r")
                until(lambda: ui.state.inbox)
                self.pipe.send_text("쓰던 글")          # Enter 없이 입력창에 남김
                until(lambda: ui.buffer.text == "쓰던 글")
                return _resp(calls=[("run_shell", '{"command": "uname"}')])
            return _resp("됐다.")

        waiting_log = []

        def answer():
            until(lambda: ui.state.mode == "question")
            time.sleep(0.1)
            waiting_log.append(self.log(ui))
            until(lambda: ui.buffer.text == "")         # 쓰던 글은 따로 두고 빈 창
            self.pipe.send_text("n\r")
        th = threading.Thread(target=answer)
        th.start()
        with mock.patch.object(C, "chat", side_effect=fake):
            self.assertEqual(s.turn("셸 써"), "answered")
        th.join(3)
        tool_msg = [m for m in seen[1] if m["role"] == "tool"][0]
        self.assertIn("거부", tool_msg["content"])
        self.assertEqual(seen[1][-1], {"role": "user", "content": "줄 선 글"})
        until(lambda: ui.buffer.text == "쓰던 글")         # 답 뒤 되돌림
        self.assertNotIn("실행할까?", waiting_log[0])        # 기다리는 동안은 입력창 위 "? …" 에만
        lines = [l for l in self.log(ui).splitlines() if "실행할까?" in l]
        self.assertEqual(len(lines), 1)                        # 답한 뒤 기록에 한 줄
        self.assertIn("→ n", lines[0])

    def _blocking(self, started):
        def fake(bk, msgs, tools, **kw):
            started.set()
            while True:
                time.sleep(0.01)
        return fake

    def _interrupt(self, key, ssh=False):
        ui = self.ui(ssh=ssh)
        s = self.sess(ui)
        started = threading.Event()

        def press():
            started.wait(3)
            self.pipe.send_text("나중 말\r")
            until(lambda: ui.state.inbox)
            self.pipe.send_text(key)
        th = threading.Thread(target=press)
        th.start()
        return ui, s, started, th

    def test_ctrl_c_interrupts_and_returns_queue_to_box(self):
        ui, s, started, th = self._interrupt("\x03")
        with mock.patch.object(C, "chat", side_effect=self._blocking(started)):
            self.assertEqual(s.turn("오래 걸려"), "interrupted")
        th.join(3)
        self.assertEqual(self.events(s, "interrupt")[0]["kind"], "interrupt")
        got = []
        rl = threading.Thread(target=lambda: got.append(ui.read_line("› ")))
        rl.start()
        until(lambda: ui.buffer.text == "나중 말" and ui.state.mode == "idle")   # 입력창에 되돌아온 뒤
        self.pipe.send_text("\r")                       # 그대로 보냄
        rl.join(3)
        self.assertEqual(got, ["나중 말"])
        self.assertIn("입력창으로 되돌렸다", self.log(ui))

    def test_esc_interrupts_locally(self):
        ui, s, started, th = self._interrupt("\x1b")
        with mock.patch.object(C, "chat", side_effect=self._blocking(started)):
            self.assertEqual(s.turn("오래 걸려"), "interrupted")
        th.join(3)

    def test_esc_over_ssh_does_not_interrupt(self):
        ui = self.ui(ssh=True)
        s = self.sess(ui)

        def fake(bk, msgs, tools, **kw):
            self.pipe.send_text("고칠 말\r")
            until(lambda: ui.state.inbox)
            self.pipe.send_text("\x1b")
            until(lambda: ui.buffer.text == "고칠 말")   # 줄 선 마지막 글을 꺼냄
            self.assertEqual(ui.state.inbox, [])
            return _resp("끝.")
        with mock.patch.object(C, "chat", side_effect=fake):
            self.assertEqual(s.turn("해"), "answered")
        self.pipe.send_text("\x1b")                     # 쉬는 중 Esc = 지우기
        until(lambda: ui.buffer.text == "")

    def test_shift_enter_is_newline(self):
        ui = self.ui()
        self.sess(ui)
        self.assertIn("Ctrl-J 줄바꿈", U.strip_ansi(ui._status_text()))     # 기본은 늘 되는 키
        self.pipe.send_text("첫\x1b[13;2u둘\x1b[27;2;13~셋")              # CSI u, modifyOtherKeys
        until(lambda: ui.buffer.text == "첫\n둘\n셋")                     # 보내지 않고 줄바꿈
        self.assertEqual(ui.nl_key, "Shift-Enter")
        self.assertIn("Shift-Enter 줄바꿈", U.strip_ansi(ui._status_text()))
        self.pipe.send_text("\r")
        self.assertEqual(ui.read_line("› "), "첫\n둘\n셋")

    def test_shrink_hint_and_ctrl_l_redraw(self):
        from prompt_toolkit.data_structures import Size
        ui = self.ui()
        self.sess(ui)
        r, out = ui.app.renderer, ui.app.output
        calls = []

        def resize():                                          # 화면 스레드에서 크기 변화 처리를 돌린다
            done = threading.Event()
            ui._call(lambda: (ui.app._on_resize(), done.set()))
            self.assertTrue(done.wait(3))

        def erase(**kw):
            calls.append("erase")
            r.reset(leave_alternate_screen=False)
        with mock.patch.object(r, "_last_size", Size(rows=30, columns=100)), \
                mock.patch.object(out, "get_size", lambda: Size(rows=30, columns=60)), \
                mock.patch.object(r, "erase", erase):
            resize()                                           # 폭이 줄었다
        self.assertIn("Ctrl-L 다시 그리기", U.strip_ansi(ui._status_text()))
        self.assertEqual(calls, ["erase"])                     # 지우기는 prompt_toolkit 기본(추측 계산 없음)
        self.assertFalse(hasattr(U, "reflow_extra"))
        ui._resize_hint_until = 0.0
        with mock.patch.object(r, "_last_size", Size(rows=30, columns=60)), \
                mock.patch.object(out, "get_size", lambda: Size(rows=40, columns=100)), \
                mock.patch.object(r, "erase", erase):
            resize()                                           # 늘리기·높이만은 안내 없음
        self.assertNotIn("Ctrl-L", U.strip_ansi(ui._status_text()))
        ui._resize_hint_until = time.monotonic() + 30
        writes = []
        with mock.patch.object(out, "write_raw", lambda t: writes.append(t)), \
                mock.patch.object(out, "get_size", lambda: Size(rows=24, columns=80)):
            self.pipe.send_text("\x0c")                       # Ctrl-L
            until(lambda: ui.redraws == 1)
        self.assertIn("\n" * 24, "".join(writes))             # 보이는 화면을 스크롤로 밀어 올림(지우지 않음)
        self.assertNotIn("Ctrl-L", U.strip_ansi(ui._status_text()))   # 안내는 바로 사라진다

    def test_ctrl_c_idle_does_not_raise(self):
        ui = self.ui()
        self.sess(ui)
        self.pipe.send_text("지울 글")
        until(lambda: ui.buffer.text == "지울 글")
        self.pipe.send_text("\x03")
        until(lambda: ui.buffer.text == "")
        self.pipe.send_text("\x03")
        until(lambda: "Ctrl-D" in self.log(ui))

    def test_tool_block_fold_and_ctrl_o(self):
        ui = self.ui()
        s = self.sess(ui)
        cmd = json.dumps({"command": "for i in $(seq 1 30); do echo line$i; done"})
        with mock.patch.object(C, "chat", side_effect=[_resp(calls=[("run_shell", cmd)]), _resp("됐다.")]):
            s.turn("셸")
        lg = self.log(ui)
        self.assertIn("⏺ run_shell", lg)
        self.assertIn("줄 접힘 (Ctrl-O 전부 보기)", lg)
        self.assertNotIn("line5\n", lg)
        self.pipe.send_text("\x0f")
        until(lambda: "line5" in self.log(ui))
        self.assertIn("run_shell 전체 출력", self.log(ui))

    def test_stream_markdown_blocks_and_stdout_proxy(self):
        ui = self.ui()
        ui.stream_start()
        ui.stream_piece("", "생각")
        self.assertIn("생각 중", ui.phase)
        ui.stream_piece("## 결과\n\n| a | b |\n", "")
        self.assertIn("결과", self.log(ui))
        self.assertIn("| a | b |", ui._live_text())   # 표는 아직 쓰는 중
        ui.stream_piece("|---|---|\n| 한 | 2 |", "")
        self.assertTrue(ui.stream_end())
        self.assertIn("한", self.log(ui))
        print("다른 코드의 print")
        self.assertIn("다른 코드의 print", self.log(ui))

    def test_dead_screen_does_not_hang(self):
        ui = self.ui()
        self.pipe.close()                   # 입력이 끝나 화면이 죽는다
        with self.assertRaises(EOFError):
            ui.read_line("› ")
        self.assertTrue(ui.state.dead)

    def test_status_hint_local_vs_ssh(self):
        for ssh, want, not_want in ((False, "Esc·Ctrl-C 중단", "Esc 지우기"), (True, "Esc 지우기", "Esc·Ctrl-C")):
            ui = self.ui(ssh=ssh)
            self.sess(ui)
            ui._armed = True
            st = U.strip_ansi(ui._status_text())
            ui._armed = False
            self.assertIn(want, st)
            self.assertNotIn(not_want, st)
            self.assertIn("Ctrl-D", U.strip_ansi(ui._status_text()))   # 쉬는 중 안내

    def test_check_terminal_and_guard_raw(self):
        import termios
        from prompt_toolkit.renderer import CPR_Support
        ui = self.ui()
        self.assertIsNone(ui.app.renderer.cpr_not_supported_callback)   # prompt_toolkit 의 CPR 경고 끔
        cooked = [0, 0, 0, termios.ECHO | termios.ICANON, 0, 0, []]
        raw = [0, 0, 0, 0, 0, 0, []]
        ui.app.renderer.cpr_support = CPR_Support.SUPPORTED
        self.assertIn("raw", ui.check_terminal(0.2, tcget=lambda fd: cooked, fd=0))
        self.assertIsNone(ui.check_terminal(0.2, tcget=lambda fd: raw, fd=0))
        ui.app.renderer.cpr_support = CPR_Support.UNKNOWN
        self.assertIn("CPR", ui.check_terminal(0.2, tcget=lambda fd: raw, fd=0))
        ui.app.renderer.cpr_support = CPR_Support.NOT_SUPPORTED
        self.assertIn("CPR", ui.check_terminal(0.2, tcget=lambda fd: raw, fd=0))
        self.assertIsNone(ui.check_terminal(0.2))           # pipe 입력(시험)은 점검하지 않음
        # 떠 있는 동안 누가 cooked 로 되돌리면 raw 를 다시 건다
        ui._raw_attrs = raw
        sets = []
        self.assertTrue(ui.guard_raw(tcget=lambda fd: cooked, tcset=lambda fd, when, a: sets.append(a)))
        self.assertEqual(sets, [raw])
        self.assertFalse(ui.guard_raw(tcget=lambda fd: raw, tcset=lambda fd, when, a: sets.append(a)))
        self.assertEqual(len(sets), 1)

    def test_status_text_fits(self):
        ui = self.ui()
        self.sess(ui)
        from prompt_toolkit.utils import get_cwidth
        st = U.strip_ansi(ui._status_text())
        self.assertLessEqual(get_cwidth(st), 80)
        self.assertIn("think", st)


if __name__ == "__main__":
    unittest.main()
