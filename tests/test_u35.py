"""판 48 forge 설정: hub.toml [forge] → 새 저장소 원격·첫 push 뒤 비공개·협력자·forge 밖으로 올리지 않기. 주소는 설정 값으로만."""

import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from stewart import forge as FG

CONF = '''[forge]
url = "{url}"
owner = "bot"
user = "bot"
credential = "keychain"
collaborators = ["human:read"]
private = true
'''


class FakeForge:
    def __init__(self, exists=True):
        me = self
        self.got, self.exists = [], exists

        class Hd(BaseHTTPRequestHandler):
            def _do(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                me.got.append((self.command, self.path, self.headers.get("Authorization"), body))
                code = 404 if (self.command == "GET" and not me.exists) else 204 if self.command == "PUT" else 200
                self.send_response(code)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
            do_GET = do_PUT = do_PATCH = _do

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Hd)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.cfg = self.tmp / "config"
        (self.cfg / "stewart").mkdir(parents=True)
        p = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.cfg)})
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("STEWART_HUB_CONFIG", None)

    def write(self, url="https://forge.example:8463", extra=""):
        (self.cfg / "stewart" / "hub.toml").write_text('[hub]\nport = 18999\n\n' + CONF.format(url=url) + extra, encoding="utf-8")

    def repo(self):
        d = self.tmp / "work" / "proj"
        d.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(d)], check=True)
        return d


class TConfig(Base):
    def test_load(self):
        self.assertIsNone(FG.load())
        self.write()
        fg = FG.load()
        self.assertEqual((fg.owner, fg.user, fg.collaborators, fg.private), ("bot", "bot", [("human", "read")], True))
        self.assertEqual(fg.remote("abc"), "https://bot@forge.example:8463/bot/abc.git")
        self.assertEqual(fg.base, "https://forge.example:8463/bot/")
        self.assertIn("forge_remote", FG.note(fg))
        self.assertIn("다른 곳(GitHub 등)에는 올리지 않는다", FG.note(fg))

    def test_bad_values(self):
        for bad in ('url = "https://x@forge.example"', 'url = "ftp://forge.example"'):
            (self.cfg / "stewart" / "hub.toml").write_text(f'[forge]\n{bad}\nowner = "bot"\n', encoding="utf-8")
            with self.assertRaises(FG.ForgeError):
                FG.load()
        (self.cfg / "stewart" / "hub.toml").write_text('[forge]\nurl = "https://f.example"\nowner = "bot"\ntoken = "x"\n', encoding="utf-8")
        with self.assertRaises(FG.ForgeError):                          # 토큰 같은 모르는 키는 받지 않는다
            FG.load()
        (self.cfg / "stewart" / "hub.toml").write_text('[forge]\nurl = "https://f.example"\nowner = "bot"\ncollaborators = ["a:owner"]\n',
                                                        encoding="utf-8")
        with self.assertRaises(FG.ForgeError):
            FG.load()


class TRemote(Base):
    def test_remote_add_once(self):
        self.write()
        fg, d = FG.load(), self.repo()
        ok, out = FG.forge_remote(fg, self.tmp / "work", "proj", "proj")
        self.assertTrue(ok, out)
        url = subprocess.run(["git", "-C", str(d), "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(url, "https://bot@forge.example:8463/bot/proj.git")
        self.assertTrue(FG.forge_remote(fg, self.tmp / "work", "proj", "proj")[0])            # 같은 것은 그대로
        subprocess.run(["git", "-C", str(d), "remote", "set-url", "origin", "https://github.com/x/y.git"], check=True)
        ok, out = FG.forge_remote(fg, self.tmp / "work", "proj", "proj")
        self.assertFalse(ok)                                                                     # 다른 origin 은 바꾸지 않는다
        self.assertIn("바꾸지 않았다", out)
        self.assertFalse(FG.forge_remote(fg, self.tmp / "work", "../밖", "proj")[0])
        self.assertFalse(FG.forge_remote(fg, self.tmp / "work", "x", "..")[0])                  # 작업 폴더 밖

    def test_outside(self):
        self.write()
        fg, d = FG.load(), self.repo()
        w = self.tmp / "work"
        self.assertIsNotNone(FG.outside(fg, "git push https://github.com/me/x.git main", w))
        self.assertIsNotNone(FG.outside(fg, "cd proj && git remote add origin https://github.com/me/x.git", w))
        self.assertIsNotNone(FG.outside(fg, "gh repo create me/x --private", w))
        self.assertIsNone(FG.outside(fg, "git push https://bot@forge.example:8463/bot/x.git main", w))
        FG.forge_remote(fg, w, "proj", "proj")
        self.assertIsNone(FG.outside(fg, "cd proj && git push -u origin main", w))
        subprocess.run(["git", "-C", str(d), "remote", "add", "gh", "https://github.com/me/x.git"], check=True)
        self.assertIsNotNone(FG.outside(fg, "cd proj && git push gh main", w))                  # 이름으로 가리켜도 주소를 본다
        self.assertIsNone(FG.outside(fg, "git status && ls", w))


class TShare(Base):
    def test_share(self):
        srv = FakeForge()
        self.addCleanup(srv.close)
        self.write(url=srv.url)
        fg = FG.load()
        with mock.patch.object(FG, "token", return_value="SEKRIT-TOKEN"):
            ok, out = FG.forge_share(fg, self.tmp, "proj")
        self.assertTrue(ok, out)
        self.assertNotIn("SEKRIT-TOKEN", out)                                                    # 토큰은 결과에 없다
        calls = [(m, p, b) for m, p, _, b in srv.got]
        self.assertEqual(calls, [("GET", "/api/v1/repos/bot/proj", None),
                                 ("PATCH", "/api/v1/repos/bot/proj", {"private": True}),
                                 ("PUT", "/api/v1/repos/bot/proj/collaborators/human", {"permission": "read"})])
        self.assertTrue(all(a == "token SEKRIT-TOKEN" for _, _, a, _ in srv.got))

    def test_not_pushed_and_no_token(self):
        srv = FakeForge(exists=False)
        self.addCleanup(srv.close)
        self.write(url=srv.url)
        fg = FG.load()
        with mock.patch.object(FG, "token", return_value="t"):
            ok, out = FG.forge_share(fg, self.tmp, "proj")
        self.assertFalse(ok)
        self.assertIn("먼저 git push", out)
        with mock.patch.object(FG, "token", return_value=None):
            ok, out = FG.forge_share(fg, self.tmp, "proj")
        self.assertIn("키체인", out)


class TChat(Base):
    def test_chat_tools_note_and_block(self):
        from stewart import chat as C
        from stewart.config import Backend, Profile
        from stewart.record import Recorder
        from tests.test_cloud import FakeUI
        self.write()
        wd = self.tmp / "work"
        wd.mkdir()
        p = Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["read_file", "run_shell"], workdir=wd)
        s = C.ChatSession(p, Backend("fake", "http://x/v1", "m", "q", 100000), Recorder(self.tmp / "runs", "chat-f"), FakeUI(), mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        self.assertIn("forge_remote", s.tool_names)
        self.assertIn("원격 저장소(설정의 forge)", s.system)
        tc = s._exec({"id": "1", "function": {"name": "run_shell",
                                               "arguments": json.dumps({"command": "git push https://github.com/me/x.git main"})}}, 1)
        self.assertFalse(tc["ok"])
        self.assertIn("다른 곳(GitHub 등)에 올리지 않는다", tc["result"])
        s.mode = "plan"
        tc = s._exec({"id": "2", "function": {"name": "forge_remote", "arguments": json.dumps({"name": "x"})}}, 2)
        self.assertFalse(tc["ok"])

    def test_no_forge_no_tools(self):
        from stewart import chat as C
        from stewart.config import Backend, Profile
        from stewart.record import Recorder
        from tests.test_cloud import FakeUI
        wd = self.tmp / "work"
        wd.mkdir()
        p = Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=wd)
        s = C.ChatSession(p, Backend("fake", "http://x/v1", "m", "q", 100000), Recorder(self.tmp / "runs", "chat-n"), FakeUI(), mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        self.assertNotIn("forge_remote", s.tool_names)
        self.assertNotIn("forge", s.system)


if __name__ == "__main__":
    unittest.main()
