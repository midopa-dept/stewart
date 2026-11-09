"""실행 모드: auto·edits·ask·plan 판단, chat 묻기·거부·계획 모드 드나들기, run --plan, 무인에 묻는 모드 금지."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from stewart import chat as C, cli, engine, modes as M
from stewart.config import Backend, ConfigError, Profile, load_profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_engine import _resp


class TModes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a.py").write_text("x = 1\n")
        self.bk = Backend("fake", "http://x/v1", "m", "q", 4096)

    def prof(self, **kw):
        return Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.tmp, max_steps=5, **kw)

    def sess(self, ui, **kw):
        self.n = getattr(self, "n", 0) + 1
        s = C.ChatSession(self.prof(**kw.pop("pkw", {})), self.bk, Recorder(self.tmp / "runs", f"chat-t{self.n}"), ui, **kw)
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        return s

    def test_decide_table(self):
        t = {(m, tool): M.decide(m, tool) for m in M.MODES for tool in ("read_file", "edit_file", "run_shell", "새도구")}
        self.assertEqual([t[("auto", x)] for x in ("read_file", "edit_file", "run_shell", "새도구")], ["run"] * 4)
        self.assertEqual([t[("edits", x)] for x in ("read_file", "edit_file", "run_shell", "새도구")], ["run", "run", "ask", "ask"])
        self.assertEqual([t[("ask", x)] for x in ("read_file", "edit_file", "run_shell", "새도구")], ["run", "ask", "ask", "ask"])
        self.assertEqual([t[("plan", x)] for x in ("read_file", "edit_file", "run_shell", "새도구")], ["run", "deny", "deny", "deny"])

    def test_edits_mode_asks_only_shell(self):
        ui = FakeUI(answers=["n"])
        s = self.sess(ui, mode="edits")
        seq = [_resp(calls=[("edit_file", '{"path": "a.py", "old": "x = 1", "new": "x = 2"}'),
                            ("run_shell", '{"command": "echo hi"}')]),
               _resp("고쳤고 셸은 거부됐다.")]
        with mock.patch.object(C, "chat", side_effect=seq):
            s.turn("고쳐")
        self.assertEqual((self.tmp / "a.py").read_text(), "x = 2\n")    # 편집은 묻지 않고 됨
        self.assertEqual(ui.answers, [])                               # 셸에서 한 번 물었다
        self.assertEqual(s.st["denied"], 1)

    def test_plan_mode_then_exit(self):
        ui = FakeUI()
        s = self.sess(ui)
        self.assertIsNone(s.set_mode("plan"))
        seq = [_resp(calls=[("read_file", '{"path": "a.py"}'), ("edit_file", '{"path": "a.py", "old": "x = 1", "new": "x = 9"}')]),
               _resp("계획: 1) a.py 의 x 를 9 로")]
        with mock.patch.object(C, "chat", side_effect=seq) as m:
            s.turn("x 를 9 로 바꿀 계획")
        self.assertEqual((self.tmp / "a.py").read_text(), "x = 1\n")    # 계획 모드에서는 안 바뀜
        from stewart.envinfo import strip_user_note
        first_user = [x for x in m.call_args_list[0][0][1] if x["role"] == "user"][0]["content"]
        self.assertTrue(strip_user_note(first_user).startswith("[계획 모드]"))
        tool_res = [x for x in s.messages if x["role"] == "tool"]
        self.assertIn("1\tx = 1", tool_res[0]["content"])
        self.assertIn("계획 모드라 실행하지 않았다", tool_res[1]["content"])
        self.assertIn("끝냈다", s.set_mode("edits"))
        with mock.patch.object(C, "chat", side_effect=[_resp(calls=[("edit_file", '{"path": "a.py", "old": "x = 1", "new": "x = 9"}')]),
                                                        _resp("했다")]) as m:
            s.turn("진행해")
        self.assertTrue(m.call_args_list[0][0][1][-1]["content"].startswith("[계획 모드 끝]"))
        self.assertEqual((self.tmp / "a.py").read_text(), "x = 9\n")
        kinds = [json.loads(l)["kind"] for l in (s.rec.dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual(kinds.count("mode"), 2)

    def test_confirm_compat(self):
        s = self.sess(FakeUI(), confirm=True)
        self.assertEqual((s.mode, s.confirm), ("ask", True))
        s.confirm = False
        self.assertEqual(s.mode, "auto")
        s2 = self.sess(FakeUI(), pkw={"mode": "edits"})
        self.assertEqual(s2.mode, "edits")
        s3 = self.sess(FakeUI(), mode="plan", confirm=True)                 # 명령줄 --mode 가 이긴다
        self.assertEqual(s3.mode, "plan")

    def test_precedence_and_cli_default(self):
        # --mode > --confirm > 프로필 mode > 프로필 confirm > 기본(cli chat 은 edits)
        mk = lambda **kw: self.sess(FakeUI(), **kw)
        self.assertEqual(mk(default_mode="edits").mode, "edits")
        self.assertEqual(mk(pkw={"confirm": True}, default_mode="edits").mode, "ask")
        self.assertEqual(mk(pkw={"confirm": True, "mode": "plan"}, default_mode="edits").mode, "plan")
        self.assertEqual(mk(pkw={"mode": "plan"}, confirm=False).mode, "auto")
        self.assertEqual(mk(pkw={"mode": "plan"}, confirm=True, mode="edits").mode, "edits")

    def test_organize_tools_are_read(self):
        for t in ("folder_scan", "scan_read", "plan_add", "peek", "shell_output"):
            self.assertEqual((M.decide("edits", t), M.decide("plan", t)), ("run", "run"), t)
        self.assertEqual(M.decide("edits", "shell_kill"), "ask")

    def test_run_plan(self):
        p = self.prof()
        with mock.patch.object(engine, "chat", side_effect=[_resp(calls=[("write_file", '{"path": "b.py", "content": "y"}')]),
                                                             _resp("계획: b.py 를 만든다")]) as m:
            s = engine.run(p, self.bk, "b.py 만들어", Recorder(self.tmp / "runs", "t"), mode="plan")
        self.assertFalse((self.tmp / "b.py").exists())
        self.assertEqual((s["stop_reason"], s["denied"]), ("completed", 1))
        from stewart.envinfo import strip_user_note
        self.assertTrue(strip_user_note(m.call_args_list[0][0][1][1]["content"]).startswith("[계획 모드]"))
        with self.assertRaises(ConfigError):
            engine.run(p, self.bk, "x", Recorder(self.tmp / "runs", "t2"), mode="edits")

    def test_cli_run_rejects_asking_mode(self):
        f = self.tmp / "e.toml"
        f.write_text(f'system = "s"\ntools = ["*"]\nworkdir = "{self.tmp}"\nmode = "edits"\n', encoding="utf-8")
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["run", "-p", str(f), "--backend", "local", "--task", "x", "--runs-dir", str(self.tmp / "runs")]), 2)
        self.assertIn("무인", err.getvalue())
        self.assertEqual(load_profile(str(f)).mode, "edits")
        f.write_text('system = "s"\nmode = "yolo"\n', encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_profile(str(f))


if __name__ == "__main__":
    unittest.main()
