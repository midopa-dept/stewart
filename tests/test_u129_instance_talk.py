"""판 129: 따로 도는 허브의 대화 자리 폴더는 그 허브 자기 자리 안 — 기본 대화 폴더는 울타리 밖이라 칸을 열지 못했다."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import config as CF, hubmain as HM, instance as I, surfaces as SF


def _sandbox_works() -> bool:
    if not Path(I.SANDBOX).exists():
        return False
    r = subprocess.run([I.SANDBOX, "-p", "(version 1)(allow default)", "/usr/bin/true"], capture_output=True)
    return r.returncode == 0


class TTalkDirEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_default_unchanged(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(CF.TALK_DIR_ENV, None)
            p = CF.load_profile(SF.TALK_PROFILE)
        self.assertEqual(p.workdir, Path(os.path.expanduser("~/.local/share/stewart/talk")))

    def test_env_moves_talk_dir(self):
        want = self.tmp / "talk"
        with mock.patch.dict(os.environ, {CF.TALK_DIR_ENV: str(want)}):
            self.assertEqual(CF.load_profile(SF.TALK_PROFILE).workdir, want)
            self.assertEqual(HM._profile_workdir(SF.TALK_PROFILE), str(want))

    def test_env_leaves_code_profile(self):
        with mock.patch.dict(os.environ, {CF.TALK_DIR_ENV: str(self.tmp / "talk")}):
            self.assertNotEqual(CF.load_profile("chat").workdir, self.tmp / "talk")


class TInstanceTalkDir(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.home, ignore_errors=True))
        d = I.dirs("ex", self.home)
        d["config"].mkdir(parents=True)
        self.proj = self.home / "proj"
        (d["config"] / "instance.toml").write_text(f'project = "{self.proj}"\nport = 8766\nbackend = "x"\n', encoding="utf-8")
        self.d = d
        self.cfg = I.load("ex", self.home)

    def test_env_points_inside_own_share(self):
        e = I.env(self.cfg, None, base={})
        self.assertEqual(Path(e["STEWART_TALK_DIR"]), self.d["share"] / "talk")

    def test_inherited_talk_dir_not_passed(self):
        e = I.env(self.cfg, None, base={"STEWART_TALK_DIR": str(self.home / "user-talk")})
        self.assertEqual(Path(e["STEWART_TALK_DIR"]), self.d["share"] / "talk")

    def test_make_dirs_creates_talk(self):
        I.make_dirs(self.cfg)
        self.assertTrue((self.d["share"] / "talk").is_dir())

    def test_talk_dir_inside_write_fence(self):
        sb = I.profile(self.cfg)
        share = I._abs(str(self.d["share"]))
        self.assertIn(f'(subpath "{share}")', sb.split("; 읽기·쓰기 모두 막는 자리")[0])
        self.assertTrue(str(I._abs(str(self.d["share"] / "talk"))).startswith(share + os.sep))

    @unittest.skipUnless(_sandbox_works(), "sandbox-exec 를 여기서 걸 수 없다(이미 샌드박스 안)")
    def test_real_sandbox_talk_writable_default_not(self):
        I.make_dirs(self.cfg)
        sb = I.write_profile(self.cfg)
        ok = subprocess.run([I.SANDBOX, "-f", str(sb), "/usr/bin/touch", str(self.d["share"] / "talk" / "ok")], capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        other = self.home / ".local" / "share" / "stewart" / "talk"   # 기본 허브의 대화 폴더 자리(울타리 밖)
        other.mkdir(parents=True)
        no = subprocess.run([I.SANDBOX, "-f", str(sb), "/usr/bin/touch", str(other / "no")], capture_output=True)
        self.assertNotEqual(no.returncode, 0)


if __name__ == "__main__":
    unittest.main()
