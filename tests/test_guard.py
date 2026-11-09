"""반복 감지(guard.py): 같은 호출·같은 결과(성공 포함) 3·5 알림, 6 이면 run 은 도구 없이 최종 보고, chat 은 묻기."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, engine, guard as G
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_engine import _resp

SHELL = ("run_shell", '{"command": "python -m unittest"}')


def _out(fails, t="0.002"):
    return f"exit 1\n.....F\nRan 9 tests in {t}s\n\nFAILED (failures={fails})"


class TGuardUnit(unittest.TestCase):
    def test_rerun_while_fixing_not_caught(self):
        g = G.LoopGuard()
        # 고치며 시험 재실행: 실패 수가 줄어든다(사이에 edit 도 있다)
        for f in (5, 4, 3, 2):
            self.assertEqual(g.observe(*SHELL, True, _out(f)), 1)
            g.observe("edit_file", '{"path": "a.py"}', True, "고침")
        # edit 없이 결과만 바뀌어도 세지 않는다
        g2 = G.LoopGuard()
        self.assertEqual([g2.observe(*SHELL, True, _out(f)) for f in (5, 4, 3)], [1, 1, 1])

    def test_same_command_nothing_changed_caught(self):
        g = G.LoopGuard()
        got = [g.observe(*SHELL, True, _out(5)) for _ in range(6)]
        self.assertEqual(got, [1, 2, 3, 4, 5, 6])
        self.assertIn("3번", g.note(3))
        self.assertIn("도구를 빼고", g.note(5))

    def test_time_only_difference_caught(self):
        g = G.LoopGuard()
        outs = [_out(5, t) for t in ("0.002", "0.031", "1.5")]
        outs[2] = outs[2] + "\n2026-11-08T16:20:01 /var/folders/ab/T/tmpk3j9x2q1 pid 4242 12.3초"
        outs[1] = outs[1] + "\n2026-11-08T16:20:59 /var/folders/ab/T/tmpzzzzzzzz pid 9 0.4초"
        outs[0] = outs[1]
        self.assertEqual([g.observe(*SHELL, True, o) for o in outs], [1, 2, 3])

    def test_counts_and_codes_kept(self):
        self.assertNotEqual(G.normalize("FAILED (failures=5)"), G.normalize("FAILED (failures=4)"))
        self.assertNotEqual(G.normalize("exit 1"), G.normalize("exit 0"))
        self.assertEqual(G.normalize("Ran 3 tests in 0.002s"), G.normalize("Ran 3 tests in 0.5s"))

    def test_other_shell_between_resets(self):
        g = G.LoopGuard()
        g.observe(*SHELL, True, _out(5))
        g.observe(*SHELL, True, _out(5))
        g.observe("run_shell", '{"command": "sed -i s/a/b/ a.py"}', True, "")
        self.assertEqual(g.observe(*SHELL, True, _out(5)), 1)
        # 읽기 도구는 사이에 끼어도 센다
        g.observe("read_file", '{"path": "a.py"}', True, "x")
        self.assertEqual(g.observe(*SHELL, True, _out(5)), 2)

    def test_exempt_and_reads(self):
        g = G.LoopGuard()
        self.assertEqual([g.observe("shell_output", '{"id": "bg1"}', True, "돌고 있음") for _ in range(6)], [0] * 6)
        self.assertEqual([g.observe("read_file", '{"path": "a.py"}', True, "x") for _ in range(3)], [1, 2, 3])
        self.assertIsNone(g.step_names({"read_file"}))
        notes = [g.step_names({"read_file", "search"}) for _ in range(5)]
        self.assertIn("6단계", notes[-1])
        self.assertIsNone(g.step_names({"edit_file"}))
        self.assertEqual(g.read_streak, 0)


class TGuardRun(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a.py").write_text("x = 1\n")
        self.bk = Backend("fake", "http://x/v1", "m", "q", 4096)

    def prof(self, steps=30):
        return Profile(name="code", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.tmp, max_steps=steps)

    def run_seq(self, seq, steps=30):
        rec = Recorder(self.tmp / "runs", "t")
        with mock.patch.object(engine, "chat", side_effect=seq) as m:
            s = engine.run(self.prof(steps), self.bk, "고쳐", rec)
        return s, m, rec

    def test_force_final_after_six(self):
        seq = [_resp(calls=[("read_file", '{"path": "a.py"}')]) for _ in range(G.STOP)] + [_resp("한 일: a.py 를 읽었다.")]
        s, m, rec = self.run_seq(seq)
        self.assertEqual((s["stop_reason"], s["forced_final"]), ("completed", True))
        self.assertEqual(m.call_count, G.STOP + 1)
        last = m.call_args_list[-1]
        self.assertIsNone(last[0][2])                                  # 도구 없이 요청
        self.assertIn("도구를 뺐다", last[0][1][-2]["content"])          # 마지막 사람 입력(그 뒤에 최종 답이 붙었다)
        self.assertIn("실제로 한 호출 횟수와 그 결과만 사실대로 적어라. 하지 않은 일을 했다고 쓰지 마라.", last[0][1][-2]["content"])
        self.assertGreaterEqual(s["guard_notes"], 3)
        tool_msgs = [x["content"] for x in last[0][1] if x["role"] == "tool"]
        self.assertNotIn("[엔진]", tool_msgs[1])
        self.assertIn("3번", tool_msgs[2])
        self.assertIn("도구를 빼고", tool_msgs[4])

    def test_tool_calls_after_force_are_dropped(self):
        seq = [_resp(calls=[("read_file", '{"path": "a.py"}')]) for _ in range(G.STOP)] \
            + [_resp("보고", calls=[("write_file", '{"path": "b.py", "content": "x"}')])]
        s, _, _ = self.run_seq(seq)
        self.assertEqual(s["stop_reason"], "completed")
        self.assertFalse((self.tmp / "b.py").exists())

    def test_steps_left_note(self):
        for i in range(8):
            (self.tmp / f"f{i}.py").write_text(f"v = {i}\n")
        seq = [_resp(calls=[("read_file", f'{{"path": "f{i}.py"}}')]) for i in range(8)] + [_resp("끝")]
        s, m, _ = self.run_seq(seq, steps=10)
        tool_msgs = [x["content"] for x in m.call_args_list[-1][0][1] if x["role"] == "tool"]
        self.assertIn("남은 단계는 5번", tool_msgs[4])
        self.assertIn("읽기·찾기만 6단계", tool_msgs[5])
        self.assertFalse(s["forced_final"])


class TGuardChat(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a.py").write_text("x = 1\n")
        self.bk = Backend("fake", "http://x/v1", "m", "q", 4096)

    def sess(self, ui, mode):
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.tmp, max_steps=50)
        s = C.ChatSession(p, self.bk, Recorder(self.tmp / "runs", "chat-g" + mode), ui, mode=mode)
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        return s

    def reads(self, k):
        return [_resp(calls=[("read_file", '{"path": "a.py"}')]) for _ in range(k)]

    def test_chat_asks_at_six(self):
        ui = FakeUI(answers=["n"])
        s = self.sess(ui, "edits")
        with mock.patch.object(C, "chat", side_effect=self.reads(G.STOP)):
            self.assertEqual(s.turn("읽어"), "repeated_call")
        self.assertEqual(ui.answers, [])
        ev = [json.loads(l) for l in (s.rec.dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual([e["more"] for e in ev if e["kind"] == "repeated_call"], [False])

    def test_chat_yes_continues(self):
        ui = FakeUI(answers=["y"])
        s = self.sess(ui, "edits")
        with mock.patch.object(C, "chat", side_effect=self.reads(G.STOP + 2) + [_resp("끝")]):
            self.assertEqual(s.turn("읽어"), "answered")

    def test_chat_auto_stops(self):
        s = self.sess(FakeUI(), "auto")
        with mock.patch.object(C, "chat", side_effect=self.reads(G.STOP)):
            self.assertEqual(s.turn("읽어"), "repeated_call")


if __name__ == "__main__":
    unittest.main()
