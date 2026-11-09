"""사용자 정의 프로필: max_tokens·confirm 키, 사용자 폴더 검색, 이름 겹침 규칙."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from stewart import backend as B, chat as C, cli, config, engine
from stewart.config import Backend, ConfigError, find_profile, load_profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_engine import _resp

USER_TOML = '''name = "mine"
system = "내 지시문"
tools = ["read_file", "search"]
workdir = "."
backend = "mac"
max_tokens = 1024
confirm = true

[output]
format = "free"
'''


class TProfileKeys(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.udir = self.tmp / "profiles"
        self.udir.mkdir()
        (self.udir / "mine.toml").write_text(USER_TOML, encoding="utf-8")
        self.env = mock.patch.dict(os.environ, {"STEWART_PROFILES_DIR": str(self.udir)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_user_folder_and_keys(self):
        p = load_profile("mine")
        self.assertEqual(p.path, (self.udir / "mine.toml"))
        self.assertEqual((p.system, p.tools, p.backend, p.max_tokens, p.confirm), ("내 지시문", ["read_file", "search"], "mac", 1024, True))
        self.assertEqual(p.workdir, self.udir.resolve())   # workdir 은 프로필 파일 기준

    def test_builtin_unchanged(self):
        p = load_profile("code")
        self.assertEqual((p.max_tokens, p.confirm), (None, None))
        self.assertEqual(p.path, config.PROFILES_DIR / "code.toml")

    def test_name_clash_is_error(self):
        (self.udir / "code.toml").write_text(USER_TOML, encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "둘 다"):
            find_profile("code")
        # 경로로 주면 쓸 수 있다
        self.assertEqual(load_profile(str(self.udir / "code.toml")).name, "mine")

    def test_bad_values(self):
        for bad in ("max_tokens = 0", 'max_tokens = "8k"', 'confirm = "yes"'):
            f = self.udir / "bad.toml"
            f.write_text(f'system = "s"\n{bad}\n', encoding="utf-8")
            with self.assertRaises(ConfigError, msg=bad):
                load_profile(str(f))

    def test_missing_path_and_slash_name(self):
        with self.assertRaises(ConfigError):
            find_profile(str(self.tmp / "none.toml"))
        with self.assertRaises(ConfigError):
            find_profile("a/b")

    def test_max_tokens_overrides_backend_params(self):
        bk = Backend("fake", "http://x/v1", "m", "q", 4096, params={"max_tokens": 8192, "temperature": 0.2})
        self.assertEqual(B._body(bk, [], max_tokens=1024)["max_tokens"], 1024)
        self.assertEqual(B._body(bk, [])["max_tokens"], 8192)

    def test_engine_passes_max_tokens(self):
        p = load_profile("mine")
        p.workdir = self.tmp
        bk = Backend("fake", "http://x/v1", "m", "q", 4096)
        with mock.patch.object(engine, "chat", side_effect=[_resp("끝났다.")]) as m:
            s = engine.run(p, bk, "do", Recorder(self.tmp / "runs", "t"))
        self.assertEqual(m.call_args.kwargs.get("max_tokens"), 1024)
        self.assertEqual(s["stop_reason"], "completed")
        # 프로필에 없으면 넘기지 않는다(백엔드 params 그대로)
        p.max_tokens = None
        with mock.patch.object(engine, "chat", side_effect=[_resp("끝났다.")]) as m:
            engine.run(p, bk, "do", Recorder(self.tmp / "runs", "t2"))
        self.assertNotIn("max_tokens", m.call_args.kwargs)

    def test_chat_confirm_default_and_override(self):
        p = load_profile("mine")
        p.workdir = self.tmp
        bk = Backend("fake", "http://x/v1", "m", "q", 4096)
        s1 = C.ChatSession(p, bk, Recorder(self.tmp / "runs", "c1"), FakeUI())
        s2 = C.ChatSession(p, bk, Recorder(self.tmp / "runs", "c2"), FakeUI(), confirm=False)
        for s in (s1, s2):
            self.addCleanup(lambda s=s: s.rec._ev.closed or s.rec._ev.close())
        self.assertEqual((s1.confirm, s2.confirm), (True, False))
        with mock.patch.object(C, "chat", side_effect=[_resp("답")]) as m:
            s1.turn("안녕")
        self.assertEqual(m.call_args.kwargs.get("max_tokens"), 1024)

    def test_cli_confirm_flag(self):
        pa = cli.argparse.ArgumentParser()
        pa.add_argument("--confirm", action=cli.argparse.BooleanOptionalAction, default=None)
        self.assertEqual([pa.parse_args(x).confirm for x in ([], ["--confirm"], ["--no-confirm"])], [None, True, False])

    def test_profiles_listing(self):
        (self.udir / "code.toml").write_text(USER_TOML, encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["profiles"])
        out = buf.getvalue()
        self.assertIn("[user]", out)
        self.assertIn("이름 겹침", out)


if __name__ == "__main__":
    unittest.main()
