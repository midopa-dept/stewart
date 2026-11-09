"""끝내지 못한 run 되돌리기(rollback_unfinished)와 남은 단계 알림 한 줄(안 A), chat 은 /undo 안내만."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, engine
from stewart.config import Backend, Profile, load_profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_engine import _resp

EDIT = ("edit_file", '{"path": "a.py", "old": "x = 1", "new": "x = 2"}')
NEW = ("write_file", '{"path": "b.py", "content": "y"}')


class TRollback(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a.py").write_text("x = 1\n")
        self.bk = Backend("fake", "http://x/v1", "m", "q", 4096)

    def prof(self, on=True, steps=3):
        return Profile(name="code", path=self.tmp / "p.toml", system="s", tools=["read_file", "edit_file", "write_file", "run_shell"],
                       workdir=self.tmp, max_steps=steps, rollback_unfinished=on)

    def go(self, seq, **kw):
        rec = Recorder(self.tmp / "runs", "t")
        with mock.patch.object(engine, "chat", side_effect=seq):
            s = engine.run(self.prof(**kw), self.bk, "고쳐", rec)
        return s, rec

    def test_unfinished_rolls_back(self):
        seq = [_resp(calls=[EDIT]), _resp(calls=[NEW]), _resp(calls=[("run_shell", '{"command": "true"}')])]
        s, rec = self.go(seq)
        self.assertEqual(s["stop_reason"], "max_steps")
        self.assertEqual((self.tmp / "a.py").read_text(), "x = 1\n")
        self.assertFalse((self.tmp / "b.py").exists())
        rb = s["rolled_back"]
        self.assertEqual((rb["restored"], rb["moved_aside"], rb["shell_calls"]), (1, 1, 1))
        out = (rec.dir / "output.md").read_text()
        self.assertIn("끝내지 못해(max_steps) 바꾼 파일 2개를 실행 전으로 되돌렸다", out)
        self.assertIn("셸 명령 1번", out)
        self.assertIn("rollback", [json.loads(l)["kind"] for l in (rec.dir / "events.jsonl").read_text().splitlines()])

    def test_completed_or_off_keeps(self):
        s, _ = self.go([_resp(calls=[EDIT]), _resp("고쳤다")])
        self.assertEqual((s["stop_reason"], s["rolled_back"]), ("completed", None))
        self.assertEqual((self.tmp / "a.py").read_text(), "x = 2\n")
        (self.tmp / "a.py").write_text("x = 1\n")
        s, _ = self.go([_resp(calls=[EDIT])] * 3, on=False)
        self.assertEqual((s["stop_reason"], s["rolled_back"]), ("max_steps", None))
        self.assertEqual((self.tmp / "a.py").read_text(), "x = 2\n")

    def test_nothing_changed_no_rollback(self):
        s, _ = self.go([_resp(calls=[("read_file", '{"path": "a.py"}')])] * 3)
        self.assertIsNone(s["rolled_back"])

    def test_profile_defaults(self):
        self.assertIs(load_profile("code").rollback_unfinished, True)
        self.assertIsNone(load_profile("chat").rollback_unfinished)

    def test_steps_left_note_has_line(self):
        seq = [_resp(calls=[("read_file", '{"path": "a.py"}')])] * 6 + [_resp("끝")]
        rec = Recorder(self.tmp / "runs", "t")
        with mock.patch.object(engine, "chat", side_effect=seq) as m:
            engine.run(self.prof(steps=10), self.bk, "고쳐", rec)
        tool_msgs = [x["content"] for x in m.call_args_list[-1][0][1] if x["role"] == "tool"]
        self.assertTrue(any("끝내지 못하면 손댄 파일을 시험이 통과하던 상태로 되돌리거나" in t for t in tool_msgs))

    def test_chat_hints_undo_not_auto(self):
        ui = FakeUI()
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["edit_file"], workdir=self.tmp, max_steps=1)
        s = C.ChatSession(p, self.bk, Recorder(self.tmp / "runs", "chat-r"), ui, mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        with mock.patch.object(C, "chat", side_effect=[_resp(calls=[EDIT])]):
            self.assertEqual(s.turn("고쳐"), "max_steps")
        self.assertEqual((self.tmp / "a.py").read_text(), "x = 2\n")   # chat 은 자동으로 되돌리지 않는다
        self.assertIn("되돌리려면 /undo", ui.out.getvalue())


if __name__ == "__main__":
    unittest.main()
