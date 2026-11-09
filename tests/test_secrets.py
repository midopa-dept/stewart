"""비밀 파일 보호: read_file·write_file·edit_file 거부, search 건너뜀, glob 표시, 프로필 덮어쓰기."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import tools as T

SECRET = "TOKEN_abc123_SECRET"


class Secrets(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for rel in [".env", "conf/.env.local", "conf/server.pem", "keys/id_ed25519", ".git-credentials",
                    "conf/aws_credentials.json", "vault.kdbx", "conf/my-git-credentials.txt", ".ENV.prod"]:
            p = self.tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"KEY={SECRET}\n")
        (self.tmp / ".env.example").write_text("KEY=put-your-token\n")
        (self.tmp / "app.py").write_text(f"# reads KEY from env, not {SECRET[:5]}\n")
        self.env = T.ToolEnv(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_write_edit_refused(self):
        for rel in [".env", "conf/.env.local", "conf/server.pem", "keys/id_ed25519", ".git-credentials",
                    "vault.kdbx", "conf/my-git-credentials.txt", ".ENV.prod", str(self.tmp / ".env")]:
            ok, out = T.read_file(self.env, rel)
            self.assertFalse(ok, rel)
            self.assertNotIn(SECRET, out)
            self.assertIn("비밀 파일", out)
        self.assertFalse(T.write_file(self.env, ".env", "X=1")[0])
        self.assertFalse(T.edit_file(self.env, ".env", "KEY", "K")[0])
        self.assertIn(SECRET, (self.tmp / ".env").read_text())      # 바뀌지 않았다
        self.assertFalse(T.write_file(self.env, "new/.env.local", "X=1")[0])   # 새로 만들기도 막는다
        self.assertTrue(T.read_file(self.env, ".env.example")[0])            # 견본은 허용

    def test_search_skips(self):
        for env_vars in ({}, {"STEWART_NO_RG": "1"}):
            with mock.patch.dict(os.environ, env_vars):
                ok, out = T.search(self.env, "TOKEN|KEY")
                self.assertTrue(ok)
                self.assertNotIn(SECRET, out)
                self.assertEqual(out.splitlines(), [".env.example:1:KEY=put-your-token", "app.py:1:# reads KEY from env, not TOKEN"])
                self.assertFalse(T.search(self.env, "KEY", path=".env")[0])

    def test_no_side_channel(self):
        # 비밀 파일에만 있는 문자열과 어디에도 없는 문자열의 결과가 output 세 가지 모두에서 같아야 한다(rg·파이썬)
        (self.tmp / "conf/.env.local").write_text("ONLY_IN_SECRET_77\nshared KEY line\n")
        for env_vars in ({}, {"STEWART_NO_RG": "1"}):
            with mock.patch.dict(os.environ, env_vars):
                for kw in ({"output": "content"}, {"output": "files"}, {"output": "count"},
                           {"output": "content", "context": 2}, {"output": "files", "glob": "*"},
                           {"output": "count", "ignore_case": True}):
                    a = T.search(self.env, "ONLY_IN_SECRET_77", **kw)
                    b = T.search(self.env, "NOWHERE_AT_ALL_00", **kw)
                    self.assertEqual(a, b, (env_vars, kw))
                    self.assertEqual(a, (True, "(일치 없음)"))
                    # 다른 파일에도 맞는 패턴: 비밀 파일이 있을 때와 없을 때 결과가 같아야 한다
                    with_secret = T.search(self.env, "KEY", **kw)
                    keep = (self.tmp / "conf/.env.local").read_text()
                    (self.tmp / "conf/.env.local").unlink()
                    try:
                        without = T.search(self.env, "KEY", **kw)
                    finally:
                        (self.tmp / "conf/.env.local").write_text(keep)
                    self.assertEqual(with_secret, without, (env_vars, kw))

    def test_glob_marks(self):
        ok, out = T.glob_files(self.env, "**/*", sort="name")
        lines = out.splitlines()
        self.assertIn(".env  (비밀 파일 — 도구로 읽기·쓰기 막힘)", lines)
        self.assertIn(".env.example", lines)
        self.assertIn("app.py", lines)

    def test_override(self):
        env = T.ToolEnv(self.tmp, secret_globs=("*.py",), secret_allow=())
        self.assertFalse(T.read_file(env, "app.py")[0])
        self.assertTrue(T.read_file(env, ".env")[0])
        env2 = T.ToolEnv(self.tmp, secret_allow=())
        self.assertFalse(T.read_file(env2, ".env.example")[0])



class ProfileKeys(unittest.TestCase):
    def test_profile_secret_files(self):
        from stewart.config import ConfigError, load_profile
        tmp = Path(tempfile.mkdtemp())
        (tmp / "p.toml").write_text('system = "s"\ntools = ["*"]\n[secret_files]\nextra = ["*.sqlite"]\nallow = []\n')
        prof = load_profile(str(tmp / "p.toml"))
        opts = T.secret_options(prof)
        self.assertIn("*.sqlite", opts["secret_globs"])
        self.assertIn(".env", opts["secret_globs"])
        self.assertEqual(opts["secret_allow"], ())
        (tmp / "q.toml").write_text('system = "s"\n[secret_files]\nglob = ["x"]\n')
        with self.assertRaises(ConfigError):
            load_profile(str(tmp / "q.toml"))
        shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
