import tempfile
import unittest
from pathlib import Path

from stewart import tools as T


class TestShellOutputClip(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.env = T.ToolEnv(workdir=Path(self.d.name))

    def tearDown(self):
        self.d.cleanup()

    def test_short_unchanged(self):
        ok, out = T.run_shell(self.env, "echo hi")
        self.assertTrue(ok)
        self.assertEqual(out, "[exit 0]\nhi\n")
        self.assertEqual(self.env.outputs, [])

    def test_head_and_tail_kept(self):
        cmd = "python3 -c \"print('HEAD'); print('x' * 30000); print('TAIL-SUMMARY FAILED (failures=1)')\""
        ok, out = T.run_shell(self.env, cmd)
        self.assertIn("HEAD", out)
        self.assertIn("TAIL-SUMMARY FAILED (failures=1)", out)
        self.assertIn("shell_output(id='1'", out)
        self.assertLess(len(out), T.SHELL_HEAD + T.SHELL_TAIL + 200)
        full = self.env.outputs[0]
        ok, part = T.execute(self.env, "shell_output", {"id": "1", "offset": len(full) - 40, "limit": 100})
        self.assertTrue(ok)
        self.assertIn("TAIL-SUMMARY", part)

    def test_clipped_output_errors_and_int_id(self):
        ok, msg = T.execute(self.env, "shell_output", {"id": "1"})
        self.assertFalse(ok)
        T._clip_ht(self.env, "y" * 20000)
        ok, msg = T.execute(self.env, "shell_output", {"id": "2"})
        self.assertFalse(ok)
        self.assertIn("1~1", msg)
        ok, part = T.execute(self.env, "shell_output", {"id": 1, "limit": 5})     # 모델이 숫자로 넘겨도 된다
        self.assertTrue(ok)
        self.assertIn("yyyyy", part)
        ok, msg = T.execute(self.env, "shell_output", {"id": "bg9"})              # 백그라운드 id 는 그대로
        self.assertFalse(ok)
        self.assertIn("백그라운드", msg)

    def test_old_name_points_to_new(self):
        names = [d["function"]["name"] for d in T.definitions(["*"])]
        msg = T.unknown_tool("read_output", names)
        self.assertIn("shell_output 로 합쳐졌다", msg)
        self.assertNotIn("쓸 수 있는 도구", msg)
        self.assertIn("쓸 수 있는 도구", T.unknown_tool("nope", names))
        self.assertIn("쓸 수 있는 도구", T.unknown_tool("read_output", ["read_file"]))   # 새 도구가 없는 프로필

    def test_no_files_written(self):
        T.run_shell(self.env, "python3 -c \"print('z' * 50000)\"")
        self.assertEqual(list(Path(self.d.name).iterdir()), [])

    def test_definition_listed(self):
        names = [d["function"]["name"] for d in T.definitions(["*"])]
        self.assertIn("shell_output", names)
        self.assertNotIn("read_output", names)


class OldNameInRun(unittest.TestCase):
    """run(engine)·chat 에서 옛 이름 read_output 으로 부르면 새 이름 안내가 도구 결과로 간다."""

    def test_engine_and_chat(self):
        import tempfile as tf
        from unittest import mock
        from stewart import chat as C, engine
        from stewart.config import Backend, Profile
        from stewart.record import Recorder
        from tests.test_engine import _resp
        tmp = Path(tf.mkdtemp())
        bk = Backend("fake", "http://x/v1", "m", "fp16", 4096)
        prof = Profile(name="t", path=tmp / "p.toml", system="s", tools=["*"], workdir=tmp, max_steps=4)
        seq = [_resp(calls=[("read_output", '{"id": 1}')]), _resp(content="끝")]
        with mock.patch.object(engine, "chat", side_effect=seq) as m:
            engine.run(prof, bk, "do", Recorder(tmp / "runs", "t"))
        tool_msgs = [x for x in m.call_args_list[1][0][1] if x["role"] == "tool"]
        self.assertIn("shell_output 로 합쳐졌다", tool_msgs[0]["content"])
        from tests.test_cloud import FakeUI
        s = C.ChatSession(prof, bk, Recorder(tmp / "runs", "c"), FakeUI(), mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        with mock.patch.object(C, "chat", side_effect=[_resp(calls=[("read_output", '{"id": 1}')]), _resp(content="끝")]) as m:
            s.turn("do")
        tool_msgs = [x for x in m.call_args_list[1][0][1] if x["role"] == "tool"]
        self.assertIn("shell_output 로 합쳐졌다", tool_msgs[0]["content"])


if __name__ == "__main__":
    unittest.main()
