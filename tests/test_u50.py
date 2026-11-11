"""판 68 시험 감시: 시험이 띄운 셸을 남기면 묶음째 끝내고 그 시험을 실패로, 시간 끊김이면 스택 + 정리 + 끝(코드 3)."""

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from stewart import shell as SH
from stewart import tools as T


class TLeak(unittest.TestCase):
    def test_leaking_test_fails_and_is_reaped(self):
        tmp = Path(tempfile.mkdtemp())

        class Leaky(unittest.TestCase):
            def test_leak(self):
                T.run_shell(T.ToolEnv(tmp), "echo hi")   # close_env 를 안 부른다

        r = unittest.TestResult()
        Leaky("test_leak").run(r)
        self.assertEqual(len(r.failures), 1, r.failures)
        self.assertIn("띄운 것이 남았다", r.failures[0][1])
        self.assertFalse([s for s in SH._LIVE if not s.closed and s.proc is not None and s.proc.poll() is None])

    def test_timeout_dumps_and_exits(self):
        code = textwrap.dedent("""
            import time, unittest, tests
            from pathlib import Path
            import tempfile
            from stewart import tools as T
            class Slow(unittest.TestCase):
                def test_slow(self):
                    T.run_shell(T.ToolEnv(Path(tempfile.mkdtemp())), "echo hi")
                    time.sleep(30)
            unittest.main(argv=["x"])
        """)
        env = {**os.environ, "STEWART_TEST_TIMEOUT": "2"}
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, env=env,
                           cwd=str(Path(__file__).resolve().parent.parent))
        self.assertEqual(r.returncode, 3, r.stderr[-800:])
        self.assertIn("[시험 감시] __main__.Slow.test_slow 가 2초를 넘었다", r.stderr)
        self.assertIn("test_slow", r.stderr)                      # 스택


if __name__ == "__main__":
    unittest.main()
