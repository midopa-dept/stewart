"""세션 이어 하기: messages.json 저장, 불러오기·옛 기록 다시 짜기, 빈 툴콜 채우기, chat·run 두 경로, cli."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from stewart.envinfo import strip_user_note

from stewart import chat as C, cli, engine, resume as R
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_engine import _resp


class TResume(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.wd = self.tmp / "wd"
        self.wd.mkdir()
        (self.wd / "a.txt").write_text("hello\n")
        self.runs = self.tmp / "runs"
        self.bk = Backend("fake", "http://x/v1", "m", "q", 4096)

    def prof(self, name="chat", system="지시"):
        return Profile(name=name, path=self.tmp / "p.toml", system=system, tools=["*"], workdir=self.wd, max_steps=4)

    def chat_session(self, **kw):
        s = C.ChatSession(self.prof(**{k: v for k, v in kw.items() if k in ("system",)}), self.bk,
                          Recorder(self.runs, "chat-chat"), FakeUI(),
                          **{k: v for k, v in kw.items() if k in ("history", "resumed_from")})
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        return s

    def test_chat_saves_and_resumes(self):
        s1 = self.chat_session()
        with mock.patch.object(C, "chat", side_effect=[_resp(calls=[("read_file", '{"path": "a.txt"}')]), _resp("hello 라고 써 있다.")]):
            s1.turn("a.txt 읽어")
        s1.close()
        saved = json.loads((s1.rec.dir / R.MESSAGES_FILE).read_text())
        self.assertEqual([m["role"] for m in saved], ["system", "user", "assistant", "tool", "assistant"])
        # 옛 기록 다시 짜기(messages.json 없이)와 같다
        self.assertEqual(R.rebuild_from_events(s1.rec.dir), saved)

        d = R.find_latest(self.runs, self.wd, chat=True)
        self.assertEqual(d, s1.rec.dir)
        s2 = self.chat_session(system="새 지시", history=R.load_messages(d), resumed_from=str(d))
        self.assertEqual(s2.messages[0]["content"].split("\n")[0], "새 지시")
        with mock.patch.object(C, "chat", side_effect=[_resp("앞에서 a.txt 를 읽었다.")]) as m:
            s2.turn("방금 뭐 했지?")
        sent = m.call_args[0][1]
        self.assertEqual([x["role"] for x in sent], ["system", "user", "assistant", "tool", "assistant", "user"])
        self.assertEqual(s2.meta["resumed_from"], str(d))
        self.assertEqual(s2.meta["resumed_messages"], 4)
        self.assertNotEqual(s2.rec.dir, s1.rec.dir)      # 새 기록 폴더, 옛 것은 그대로
        s2.close()

    def test_interrupt_and_reset_rebuild(self):
        s = self.chat_session()
        with mock.patch.object(C, "chat", side_effect=KeyboardInterrupt):
            self.assertEqual(s.turn("길게 해"), "interrupted")
        with mock.patch.object(C, "chat", side_effect=[_resp("둘째")]):
            s.turn("다시")
        self.assertEqual(R.rebuild_from_events(s.rec.dir), json.loads((s.rec.dir / R.MESSAGES_FILE).read_text()))
        s.reset()
        self.assertEqual(len(R.load_messages(s.rec.dir)), 1)
        self.assertEqual(len(R.rebuild_from_events(s.rec.dir)), 1)
        s.close()

    def test_rebuild_with_stream_partial(self):
        s = C.ChatSession(self.prof(), self.bk, Recorder(self.runs, "chat-p"), FakeUI(), stream=True)
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())

        def cut(*a, **k):
            k["on_delta"]("앞부분", "")
            e = KeyboardInterrupt()
            e.partial = {"role": "assistant", "content": "앞부분"}
            raise e

        with mock.patch.object(C, "chat_stream", side_effect=cut):
            s.turn("길게")
        self.assertEqual(R.rebuild_from_events(s.rec.dir), json.loads((s.rec.dir / R.MESSAGES_FILE).read_text()))

    def test_close_dangling(self):
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
                    {"id": "c2", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": "ok"}]
        out = R.prepare(msgs, "새")
        self.assertEqual(out[0], {"role": "system", "content": "새"})
        self.assertEqual([m.get("tool_call_id") for m in out if m["role"] == "tool"], ["c1", "c2"])

    def test_engine_run_resume(self):
        p = self.prof("code")
        rec1 = Recorder(self.runs, "code")
        with mock.patch.object(engine, "chat", side_effect=[_resp("첫 답")]):
            engine.run(p, self.bk, "첫 과제", rec1)
        h = R.load_messages(R.find_latest(self.runs, self.wd, chat=False))
        self.assertEqual([m["role"] for m in h], ["system", "user", "assistant"])
        with mock.patch.object(engine, "chat", side_effect=[_resp("이어서 답")]) as m:
            s = engine.run(p, self.bk, "둘째 과제", Recorder(self.runs, "code2"), history=h, resumed_from=str(rec1.dir))
        self.assertEqual([strip_user_note(x["content"]) for x in m.call_args[0][1] if x["role"] == "user"], ["첫 과제", "둘째 과제"])
        self.assertEqual(s["stop_reason"], "completed")
        with self.assertRaises(R.ResumeError):
            R.find_latest(self.runs, self.tmp, chat=False)    # 다른 작업 폴더

    def test_resolve_run(self):
        rec = Recorder(self.runs, "x")
        rec.event("run_start", meta={"workdir": str(self.wd)}, messages=[])
        self.assertEqual(R.resolve_run(self.runs, rec.dir.name), rec.dir.resolve())
        self.assertEqual(R.resolve_run(self.runs, str(rec.dir)), rec.dir.resolve())
        with self.assertRaises(R.ResumeError):
            R.resolve_run(self.runs, "없는것")
        rec._ev.close()

    def test_cli_run_continue(self):
        prof = self.tmp / "c.toml"
        prof.write_text(f'name = "c"\nsystem = "s"\ntools = ["*"]\nworkdir = "{self.wd}"\n', encoding="utf-8")
        base = ["run", "-p", str(prof), "--backend", "local", "--runs-dir", str(self.runs)]
        with mock.patch.object(engine, "chat", side_effect=[_resp("하나")]), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(base + ["--task", "첫"]), 0)
        err = io.StringIO()
        with mock.patch.object(engine, "chat", side_effect=[_resp("둘")]) as m, redirect_stdout(io.StringIO()), redirect_stderr(err):
            self.assertEqual(cli.main(base + ["--task", "둘째", "--continue"]), 0)
        self.assertEqual([strip_user_note(x["content"]) for x in m.call_args[0][1] if x["role"] == "user"], ["첫", "둘째"])
        self.assertIn("이어 감", err.getvalue())
        # 없는 기록
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(cli.main(base + ["--task", "x", "--resume", "없는것"]), 2)
        self.assertIn("찾지 못했다", err.getvalue())


if __name__ == "__main__":
    unittest.main()
