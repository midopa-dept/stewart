"""도구 셸 환경: stewart 자신의 가상 환경(VIRTUAL_ENV·PATH 의 .venv/bin)과 stewart 설정 변수가 모델의 셸로 새지 않는다."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import shell as SH


class TToolEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.own = self.tmp / "stewart-clone" / ".venv"
        (self.own / "bin").mkdir(parents=True)
        self.other = self.tmp / "proj" / ".venv"
        (self.other / "bin").mkdir(parents=True)
        os.symlink(self.own / "bin", self.tmp / "link-bin")
        p = mock.patch.object(SH, "own_venv", lambda: str(self.own))
        p.start()
        self.addCleanup(p.stop)

    def base(self, **extra):
        return {"PATH": os.pathsep.join([str(self.own / "bin"), str(self.tmp / "link-bin"), str(self.other / "bin"), "/usr/bin", "/bin"]),
                "VIRTUAL_ENV": str(self.own), "VIRTUAL_ENV_PROMPT": "stewart", "UV_RUN_RECURSION_DEPTH": "1",
                "STEWART_BACKENDS": "/somewhere/eval/backends/x.toml", "STEWART_CLOUD_API_KEY": "k",
                "HOME": "/home/u", "LANG": "C.UTF-8", "LEAK": str(self.own / "lib"), **extra}

    def test_strips_own_venv_and_settings(self):
        e = SH.tool_env(self.base())
        self.assertEqual(e["PATH"].split(os.pathsep), [str(self.other / "bin"), "/usr/bin", "/bin"])   # 링크로 돌아도 뺌
        for k in ("VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "UV_RUN_RECURSION_DEPTH", "STEWART_BACKENDS", "STEWART_CLOUD_API_KEY", "LEAK"):
            self.assertNotIn(k, e, k)
        self.assertEqual((e["HOME"], e["LANG"]), ("/home/u", "C.UTF-8"))

    def test_user_venv_kept(self):
        e = SH.tool_env(self.base(VIRTUAL_ENV=str(self.other)))                # 사용자가 켠 다른 venv
        self.assertEqual(e["VIRTUAL_ENV"], str(self.other))
        self.assertIn(str(self.other / "bin"), e["PATH"].split(os.pathsep))

    def test_system_python_no_change(self):
        with mock.patch.object(SH, "own_venv", lambda: None):
            e = SH.tool_env({"PATH": "/a:/b", "VIRTUAL_ENV": "/v", "X": "1"})
        self.assertEqual((e["PATH"], e["VIRTUAL_ENV"], e["X"]), ("/a:/b", "/v", "1"))

    def test_real_shell_does_not_see_it(self):
        env = self.base()
        with mock.patch.dict(os.environ, env, clear=True):
            sh = SH.Shell(self.tmp)
            self.addCleanup(sh.close)
            rc, out, _ = sh.run('echo "VE=[$VIRTUAL_ENV]"; echo "P=[$PATH]"; env | grep -c "^STEWART_" || true', 10)
        self.assertEqual(rc, 0, out)
        self.assertIn("VE=[]", out)
        self.assertNotIn(str(self.own), out)
        self.assertIn("\n0", "\n" + out.strip().splitlines()[-1])


if __name__ == "__main__":
    unittest.main()
