"""체크포인트·되돌리기: 처음 모습만 보관, 새 파일은 옮겨 둠, 여러 차례 거꾸로, 바깥 변경 표시, chat /undo, run + cli rewind."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from stewart import chat as C, cli, engine
from stewart.checkpoint import Checkpoints
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_engine import _resp
from stewart import tools as T


class TCheckpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.wd = self.tmp / "wd"            # git 아닌 폴더
        self.wd.mkdir()
        (self.wd / "a.py").write_text("x = 1\n")
        self.bk = Backend("fake", "http://x/v1", "m", "q", 4096)
        self.n = 0

    def write(self, cps, tool, args):
        cps.before_write(tool, args)
        ok, _ = T.execute(T.ToolEnv(self.wd), tool, args)
        cps.after_write(tool, args, ok)
        return ok

    def test_unit_rewind_two_turns(self):
        run = self.tmp / "run"
        run.mkdir()
        cps = Checkpoints(run, self.wd)
        cps.begin("하나")
        self.write(cps, "edit_file", {"path": "a.py", "old": "x = 1", "new": "x = 2"})
        self.write(cps, "edit_file", {"path": "a.py", "old": "x = 2", "new": "x = 3"})   # 같은 파일 두 번 → 처음 모습만
        self.write(cps, "write_file", {"path": "sub/new.py", "content": "n = 1\n"})
        cps.begin("둘")
        self.write(cps, "edit_file", {"path": "a.py", "old": "x = 3", "new": "x = 4"})
        lst = cps.list()
        self.assertEqual([len(c["files"]) for c in lst], [2, 1])
        rep = cps.rewind(2)                                      # 둘째 차례만
        self.assertEqual((self.wd / "a.py").read_text(), "x = 3\n")
        self.assertTrue((self.wd / "sub/new.py").exists())
        rep = cps.rewind(1)                                      # 처음으로
        self.assertEqual((self.wd / "a.py").read_text(), "x = 1\n")
        self.assertFalse((self.wd / "sub/new.py").exists())
        self.assertEqual(rep["moved_aside"], [str(self.wd / "sub/new.py")])
        aside = Path(rep["aside_dir"])                          # 지운 것이 아니라 옮겨 둠
        kept = {(aside / f).read_text(): (aside / f"{f}.path").read_text() for f in [p.name for p in aside.iterdir() if p.name.isdigit()]}
        self.assertIn("n = 1\n", kept)
        self.assertEqual(len((run / "checkpoints" / "rewinds.jsonl").read_text().splitlines()), 2)

    def test_outside_change_flagged(self):
        run = self.tmp / "run"
        run.mkdir()
        cps = Checkpoints(run, self.wd)
        cps.begin("하나")
        self.write(cps, "edit_file", {"path": "a.py", "old": "x = 1", "new": "x = 2"})
        (self.wd / "a.py").write_text("x = 99\n")                # 셸이 또 바꾼 셈
        rep = cps.rewind(1)
        self.assertEqual(rep["outside_changes"], [str(self.wd / "a.py")])
        self.assertEqual((self.wd / "a.py").read_text(), "x = 1\n")

    def test_chat_undo(self):
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.wd, max_steps=4)
        s = C.ChatSession(p, self.bk, Recorder(self.tmp / "runs", "chat-t"), FakeUI())
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        with mock.patch.object(C, "chat", side_effect=[_resp(calls=[("edit_file", '{"path": "a.py", "old": "x = 1", "new": "x = 5"}')]),
                                                        _resp("고쳤다"), _resp("그냥 답")]):
            s.turn("x 를 5 로")
            s.turn("고마워")                                     # 파일 안 바꾼 차례
        self.assertEqual((self.wd / "a.py").read_text(), "x = 5\n")
        rep = s.rewind()                                         # 파일을 바꾼 마지막 차례(1번)
        self.assertEqual(rep["to"], 1)
        self.assertEqual((self.wd / "a.py").read_text(), "x = 1\n")
        with mock.patch.object(C, "chat", side_effect=[_resp("알았다")]) as m:
            s.turn("다음")
        self.assertIn("되돌렸다", m.call_args[0][1][-1]["content"])
        with self.assertRaises(ValueError):
            C.ChatSession(p, self.bk, Recorder(self.tmp / "runs", "chat-u"), FakeUI()).rewind()

    def test_run_and_cli_rewind(self):
        p = Profile(name="code", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.wd, max_steps=4)
        rec = Recorder(self.tmp / "runs", "code")
        with mock.patch.object(engine, "chat", side_effect=[_resp(calls=[("write_file", '{"path": "b.py", "content": "b"}')]),
                                                             _resp("만들었다")]):
            s = engine.run(p, self.bk, "b.py 를 만든다", rec)
        self.assertEqual(s["checkpoint_files"], 1)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["rewind", rec.dir.name, "--runs-dir", str(self.tmp / "runs")]), 0)
        self.assertIn("새로", out.getvalue())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["rewind", str(rec.dir), "--to", "1"]), 0)
        self.assertFalse((self.wd / "b.py").exists())


if __name__ == "__main__":
    unittest.main()
