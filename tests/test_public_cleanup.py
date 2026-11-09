"""공개 정리: 백엔드 설정 찾는 차례, 기록 기본 위치(XDG), 일반 media 프로필과 사용자 프로필 extends 의 상대 경로."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import config as CF
from stewart.config import ConfigError, load_profile


class TBackendsPath(unittest.TestCase):
    def test_order(self):
        tmp = Path(tempfile.mkdtemp())
        user = tmp / "cfg" / "stewart" / "backends.toml"
        with mock.patch.dict(os.environ, {"STEWART_BACKENDS": str(tmp / "x.toml"), "XDG_CONFIG_HOME": str(tmp / "cfg")}):
            self.assertEqual(CF.backends_path(), tmp / "x.toml")                    # 환경 변수가 먼저
        env = {k: v for k, v in os.environ.items() if k != "STEWART_BACKENDS"}
        env["XDG_CONFIG_HOME"] = str(tmp / "cfg")
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(CF, "DEFAULT_BACKENDS", tmp / "nope.toml"):
            with self.assertRaises(ConfigError) as e:
                CF.backends_path()
            self.assertIn("backends.example.toml", str(e.exception))               # 없으면 예시를 복사하라고 안내
            user.parent.mkdir(parents=True)
            user.write_text('default = "a"\n[backends.a]\nbase_url = "http://127.0.0.1:1/v1"\nmodel = "m"\n')
            self.assertEqual(CF.backends_path(), user)                             # 그다음 사용자 설정 폴더
            self.assertEqual(CF.load_backends()[1], "a")

    def test_example_is_loadable_and_generic(self):
        bs, default = CF.load_backends(CF.REPO_ROOT / "backends.example.toml")
        self.assertEqual(default, "local")
        self.assertEqual(set(bs), {"local", "cloud"})
        self.assertIn("example", bs["cloud"].base_url)


class TXdg(unittest.TestCase):
    def test_runs_default_under_state(self):
        self.assertEqual(CF.DEFAULT_RUNS_DIR.parts[-2:], ("stewart", "runs"))
        self.assertNotIn("labs", CF.DEFAULT_RUNS_DIR.parts)


class TMedia(unittest.TestCase):
    def test_media_builtin_and_user_extends(self):
        m = load_profile("media")
        self.assertEqual((m.name, m.system, m.output_format), ("media", "", "json"))
        self.assertEqual(m.workdir, (CF.REPO_ROOT / "demos" / "media").resolve())
        tmp = Path(tempfile.mkdtemp())
        (tmp / "media-local.toml").write_text('extends = "media"\nmax_steps = 12\n', encoding="utf-8")
        p = load_profile(str(tmp / "media-local.toml"))
        self.assertEqual(p.workdir, m.workdir)                                     # 부모의 상대 workdir 는 부모 파일 기준
        self.assertEqual(p.output_schema, m.output_schema)
        self.assertEqual(p.max_steps, 12)

    def test_cli_media_default(self):
        src = (CF.REPO_ROOT / "src" / "stewart" / "cli.py").read_text(encoding="utf-8")
        self.assertIn('m.add_argument("--profile", default="media")', src)


if __name__ == "__main__":
    unittest.main()
