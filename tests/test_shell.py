"""셸 상태 유지 + 백그라운드."""

import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from stewart import shell as SH, tools as T


class Persistent(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "sub").mkdir()
        self.env = T.ToolEnv(self.tmp, shell_timeout_s=5, artifacts_dir=self.tmp / "runs")

    def tearDown(self):
        T.close_env(self.env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sh(self, cmd, **k):
        return T.run_shell(self.env, cmd, **k)

    def test_state_kept(self):
        self.assertTrue(self.sh("cd sub && export FOO=bar && BAZ=1")[0])
        ok, out = self.sh("pwd; echo $FOO $BAZ")
        self.assertTrue(ok)
        self.assertIn(str((self.tmp / "sub").resolve()).split("/")[-1], out)
        self.assertIn("bar 1", out)
        ok, out = self.sh("false")
        self.assertEqual((ok, out.splitlines()[0]), (False, "[exit 1]"))
        ok, out = self.sh("printf 'no newline'")
        self.assertEqual(out, "[exit 0]\nno newline")

    def test_stdin_not_swallowed(self):
        ok, out = self.sh("cat; echo after")                        # cat 이 다음 명령을 삼키지 않는다(stdin=/dev/null)
        self.assertTrue(ok)
        self.assertIn("after", out)
        self.assertIn("still", self.sh("echo still")[1])

    def test_timeout_restarts_with_state(self):
        self.sh("cd sub; export KEEP=yes; LOST=1")
        t0 = time.monotonic()
        ok, out = self.sh("sleep 30 & sleep 30; echo never", timeout_s=0.5)
        self.assertLess(time.monotonic() - t0, 5)
        self.assertFalse(ok)
        self.assertIn("[시간 초과]", out)
        self.assertNotIn("never", out)
        ok, out = self.sh("echo ${KEEP:-none} ${LOST:-gone}; basename $PWD")
        self.assertEqual(out.splitlines()[1:], ["yes gone", "sub"])  # export·cd 는 살고, 내보내지 않은 변수는 사라짐
        self.assertEqual(self.env.shell.restarts, 1)

    def test_timeout_kills_children(self):
        marker = self.tmp / "late"
        self.sh(f"(sleep 1.5; touch {marker}) ; sleep 10", timeout_s=0.3)
        time.sleep(2)
        self.assertFalse(marker.exists())                           # 그 명령의 자식까지 묶음째 끝남

    def test_exit_and_syntax(self):
        self.sh("cd sub; export A=1")
        ok, out = self.sh("exit 3")
        self.assertFalse(ok)
        self.assertIn("셸이 끝났다(exit 3)", out)
        self.assertIn("1 sub", self.sh("echo $A $(basename $PWD)")[1])
        ok, out = self.sh("echo 'unclosed")
        self.assertFalse(ok)
        self.assertIn("문법 오류라 보내지 않았다", out)
        self.assertIn("ok", self.sh("echo ok")[1])                   # 셸이 다음 입력을 삼키지 않았다

    def test_marker_spoof(self):
        ok, out = self.sh("echo '__STEWART_deadbeef_1__ 0 /'; echo real")
        self.assertIn("real", out)

    def test_long_output_clip(self):
        ok, out = self.sh("python3 -c \"print('x'*30000)\"")
        self.assertTrue(ok)
        self.assertIn("shell_output(id='1'", out)


class Background(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "sub").mkdir()
        self.env = T.ToolEnv(self.tmp, shell_timeout_s=5, artifacts_dir=self.tmp / "runs")

    def tearDown(self):
        T.close_env(self.env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_start_output_kill_and_close(self):
        T.run_shell(self.env, "cd sub; export WHO=bg")
        ok, out = T.execute(self.env, "run_shell", {"command": "echo $WHO $(basename $PWD); sleep 0.2; echo done", "background": True})
        self.assertTrue(ok, out)
        bid = re.search(r"백그라운드 (bg\d+)", out).group(1)
        time.sleep(0.8)
        ok, out = T.shell_output(self.env, bid)
        self.assertIn("끝남 exit 0", out)
        self.assertIn("bg sub", out)                                 # 셸의 cd·export 를 물려받는다
        self.assertIn("done", out)
        ok, out = T.run_shell(self.env, "sleep 30", background=True)
        bid2 = re.search(r"(bg\d+)", out).group(1)
        self.assertIn("도는 중", T.shell_output(self.env, bid2)[1])
        self.assertTrue(T.shell_kill(self.env, bid2)[0])
        self.assertIn("shell_kill", T.shell_output(self.env, bid2)[1])
        self.assertFalse(T.shell_output(self.env, "bg99")[0])
        self.assertTrue((self.tmp / "runs" / f"shell-{bid}.log").exists())   # 출력은 기록 폴더(작업 폴더 밖)

    def test_limit_and_cleanup(self):
        ids = []
        for _ in range(SH.BG_MAX):
            ok, out = T.run_shell(self.env, "sleep 30", background=True)
            self.assertTrue(ok)
            ids.append(re.search(r"(bg\d+)", out).group(1))
        ok, out = T.run_shell(self.env, "sleep 30", background=True)
        self.assertFalse(ok)
        self.assertIn(f"{SH.BG_MAX}개까지", out)
        procs = [self.env.shell.bg[i]["proc"] for i in ids]
        end = T.close_env(self.env)
        self.assertEqual(sorted(end["cleaned_up"]), sorted(ids))
        self.assertTrue(all(p.poll() is not None for p in procs))
        self.assertEqual(T.close_env(self.env)["background"], [])       # 두 번 불러도 괜찮다

    def test_engine_records_cleanup(self):
        import json
        from unittest import mock
        from stewart import engine
        from stewart.config import Backend, Profile
        from stewart.record import Recorder
        from tests.test_engine import _resp
        prof = Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["run_shell"], workdir=self.tmp, max_steps=3)
        seq = [_resp(calls=[("run_shell", json.dumps({"command": "sleep 30", "background": True}))]), _resp(content="끝")]
        with mock.patch.object(engine, "chat", side_effect=seq):
            s = engine.run(prof, Backend("fake", "http://x/v1", "m", "fp16", 4096), "do", Recorder(self.tmp / "out", "t"))
        self.assertEqual(s["shell"]["cleaned_up"], ["bg1"])
        ev = [json.loads(l)["kind"] for l in (Path(s["run_dir"]) / "events.jsonl").read_text().splitlines()]
        self.assertIn("shell_cleanup", ev)

    def test_single_spawn_point(self):
        src = Path(SH.__file__).read_text()
        self.assertEqual(src.count("subprocess.Popen("), 1)              # 셸은 spawn() 한 곳에서만 띄운다
        tsrc = Path(T.__file__).read_text()
        self.assertNotIn("shell=True", tsrc)


if __name__ == "__main__":
    unittest.main()
