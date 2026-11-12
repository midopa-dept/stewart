"""판 118: 대화 자리 프로필 talk(웹·받은 파일·글 건네기·run_code, 셸·편집 없음, 대화 폴더, 허브 역할 지시 없음, 환경은 날짜만),
run_code(울타리 규칙·화면 조작 거절·만든 파일 카드·울타리 없으면 안 실음), 인자 없는 stewart."""

import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, modes as M, runcode as RC, tools as T
from stewart.config import Backend, load_profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_u60 import resp


class TTalk(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def sess(self, **env):
        import dataclasses
        p = dataclasses.replace(load_profile("talk"), workdir=self.tmp)
        with mock.patch.dict(os.environ, {"STEWART_MEMORY": "0", **env}):
            s = C.ChatSession(p, Backend("b", "http://127.0.0.1:9/v1", "m", "q", 100000), Recorder(self.tmp / "runs", "t"), FakeUI(),
                              stream=False, mode="edits")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        return s

    def test_profile(self):
        p = load_profile("talk")
        self.assertEqual(p.read_outside, "deny")
        self.assertTrue(str(p.workdir).endswith("stewart/talk"))
        self.assertFalse(p.raw["hub_role"])
        s = self.sess()
        names = set(s.tool_names)
        self.assertNotIn("run_shell", names)
        self.assertNotIn("edit_file", names)
        self.assertNotIn("search", names)
        self.assertLessEqual({"web_search", "web_fetch", "write_file", "read_file", "ask_user"} - names, {"web_search", "web_fetch"})
        self.assertIn("오늘 날짜", s._env_note)
        self.assertNotIn("셸", s._env_note)                               # 환경은 날짜만
        with mock.patch.object(RC, "available", return_value=None):
            s2 = self.sess()
        self.assertNotIn("run_code", s2.tool_names)
        self.assertIn("코드 실행이 꺼져 있다", s2.messages[0]["content"])     # 꺼진 까닭을 모델이 안다

    def test_hub_role_off(self):
        from stewart.hubmain import HUB_MAIN_NOTE
        from tests.test_hub3 import MainBase  # noqa: F401 — 허브 꼴은 ctxcost 로 본다
        from stewart import ctxcost as CC
        r = CC.measure("talk", hub=True, clean=True)
        self.assertLess(r["total"], 8000)
        self.assertNotIn("session_spawn", [t for t, _ in r["top_tools"]])   # 허브 세션 도구는 칩(기본 끔)

    def test_modes(self):
        self.assertEqual(M.decide("edits", "run_code"), "run")
        self.assertEqual(M.decide("plan", "run_code"), "deny")
        self.assertEqual(M.decide("ask", "run_code"), "ask")


class TRunCode(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = T.ToolEnv(workdir=self.tmp)

    def test_sbpl(self):
        sb = RC.sbpl(self.tmp / "r", ["/py"], ["/py/bin/python3"], "/home/u")
        for must in ['(deny file-write* (subpath "/"))', "(deny network*)", "(deny appleevent-send)", "(deny process-exec*)",
                     "launchservicesd", '(deny file-read* (subpath "/home/u") (subpath "/Volumes"))', '(literal "/py/bin/python3")']:
            self.assertIn(must, sb)

    def test_gui_hit(self):
        self.assertEqual(RC.code_gui_hit("import webbrowser\nwebbrowser.open('x')"), "webbrowser")
        self.assertEqual(RC.code_gui_hit("import subprocess\nsubprocess.run(['open', 'https://x'])"), "open")
        self.assertEqual(RC.code_gui_hit("start = 0\nprint(sum(range(10)))"), "")
        ok, out = RC.run(self.env, "import webbrowser", kind="sandbox-exec")
        self.assertFalse(ok)
        self.assertIn("이 컴퓨터", out)

    def test_run_fake(self):
        seen = {}

        def runner(cmd, cwd, **kw):
            seen["cmd"], seen["env"] = cmd, kw["env"]
            Path(cwd, "plot.png").write_bytes(b"\x89PNG")
            return subprocess.CompletedProcess(cmd, 0, stdout="합 45\n".encode(), stderr=b"")
        (self.tmp / "data.csv").write_text("a\n1\n", encoding="utf-8")
        with mock.patch.object(RC, "python_dirs", return_value=(["/py"], ["/py/bin/python3"])):
            ok, out = RC.run(self.env, "print(45)", files=["data.csv"], kind="sandbox-exec", python="/py/bin/python3", _runner=runner)
        self.assertTrue(ok)
        self.assertEqual(seen["cmd"][:2], [RC.SANDBOX, "-f"])
        self.assertNotIn("PYTHONPATH", seen["env"])
        self.assertIn("합 45", out)
        self.assertIn("만든 파일: .code/", out)
        self.assertTrue(any(f.endswith("plot.png") for f in self.env.turn_extra_files))   # 만든 파일 카드
        self.assertFalse(any(f.endswith("data.csv") for f in self.env.turn_extra_files))  # 넣은 파일은 아님
        ok, out = RC.run(self.env, "print(1)", files=["../x"], kind="sandbox-exec", _runner=runner)
        self.assertFalse(ok)

    def test_timeout_and_off(self):
        def runner(cmd, cwd, **kw):
            raise subprocess.TimeoutExpired(cmd, 1, output=b"part")
        with mock.patch.object(RC, "python_dirs", return_value=(["/py"], ["/py/bin/python3"])):
            ok, out = RC.run(self.env, "while True: pass", kind="sandbox-exec", timeout_s=1, _runner=runner)
        self.assertFalse(ok)
        self.assertIn("시간 초과", out)
        with mock.patch.object(RC, "available", return_value=None):
            ok, out = RC.run(self.env, "print(1)")
        self.assertEqual((ok, out), (False, RC.OFF_WHY))


class TBare(unittest.TestCase):
    def test_bare_non_tty_help(self):
        from stewart import cli
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf), mock.patch("sys.stdin", io.StringIO("")):
            self.assertEqual(cli.main([]), 0)
        self.assertIn("usage: stewart", buf.getvalue())


class TParlando(unittest.TestCase):
    def test_alias(self):
        from stewart import cli, surfaces as SF
        tmp = Path(tempfile.mkdtemp())
        # 인자 바꿈만 본다(resolve 에 넘어가는 프로필 이름)
        with mock.patch.object(cli, "resolve", side_effect=SystemExit(7)) as r:
            with self.assertRaises(SystemExit):
                cli.main([SF.TALK_COMMAND, "--once", "--workdir", str(tmp), "안녕"])
        self.assertEqual(r.call_args[0][0], SF.TALK_PROFILE)


class TNoTable(unittest.TestCase):
    def test_session_without_backends_table(self):   # 판 118.1: 백엔드 표가 없어도 코드로 준 Backend 로 대화가 뜬다(등급 없이)
        from stewart import config as CF, spawn as SP
        from stewart.config import ConfigError, Profile
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(CF, "backends_path", side_effect=ConfigError("백엔드 설정이 없다")):
            self.assertEqual(SP.load_tiers(), {})
            p = Profile(name="x", path=tmp / "p.toml", system="s", tools=["read_file", "delegate"], workdir=tmp, max_steps=2)
            s = C.ChatSession(p, Backend("b", "http://127.0.0.1:9/v1", "m", "q", 100000), Recorder(tmp / "runs", "c"), FakeUI(),
                              stream=False, mode="auto")
            s.rec._ev.close()
        self.assertEqual(s.tiers, {})
        self.assertIn("workers", s.tool_names)


if __name__ == "__main__":
    unittest.main()
