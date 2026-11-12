"""판 117: 이 컴퓨터 화면 조작 셸 명령 기본 거절(hostgui) · 켜면 명령마다 대상을 밝혀 묻고 그 한 번만 · 도구 지목 감지(toolmention) ·
웹 넛지에 "알아봐"·처음 보는 이름."""

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, hostgui as HG, toolmention as TM, tools as T, webnudge as W
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_u60 import resp


class THit(unittest.TestCase):
    def test_hit(self):
        yes = ["open https://www.naver.com", "open -a Safari 'https://x'", "/usr/bin/open .", "osascript -e 'tell app \"Safari\"'",
               "cd /tmp && open x.html", "sudo open x", "shortcuts run Foo", "screencapture a.png", "xdg-open http://x", "gio open x",
               "xdotool click 1", "cmd /c start http://x", "powershell Start-Process notepad", "python3 -m webbrowser http://x",
               "python3 -c 'import webbrowser; webbrowser.open(\"x\")'", "ls; open x", "echo $(open x)"]
        no = ["ls -la", "npm start", "git open-pr", "python3 -m http.server", "grep open file.txt", "cat opener.py", "openssl version",
              "starts=1", "echo starting", "pytest -k open"]
        for c in yes:
            self.assertTrue(HG.hit(c), c)
        for c in no:
            self.assertEqual(HG.hit(c), "", c)

    def test_enabled(self):
        p = Profile(name="x", path=Path("p"), system="s", tools=[], workdir=Path("."), raw={"host_gui": True})
        self.assertTrue(HG.enabled(p, env={}))
        self.assertFalse(HG.enabled(p, env={"STEWART_HOST_GUI": "0"}))
        self.assertFalse(HG.enabled(None, env={}))


class TRunShell(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_tools_refuse_and_once(self):
        env = T.ToolEnv(workdir=self.tmp)
        self.addCleanup(lambda: T.close_env(env))
        ok, out = T.execute(env, "run_shell", {"command": "open https://example.com"})
        self.assertFalse(ok)
        self.assertIn("실행 안 함", out)
        self.assertIn("이 컴퓨터", out)
        env.host_gui = True
        ok, out = T.execute(env, "run_shell", {"command": "open https://example.com"})
        self.assertFalse(ok)                                                      # 켜져 있어도 사람이 그 명령을 승인해야
        env.host_gui_once = "echo open-ok; true || open x"
        ok, out = T.execute(env, "run_shell", {"command": "echo open-ok; true || open x"})
        self.assertTrue(ok)
        self.assertIsNone(env.host_gui_once)                                      # 한 번만

    def sess(self, raw=None, mode="auto"):
        p = Profile(name="pt", path=self.tmp / "p.toml", system="s", tools=["run_shell"], workdir=self.tmp, max_steps=4, raw=raw or {})
        ui = FakeUI()
        s = C.ChatSession(p, Backend("b", "http://127.0.0.1:9/v1", "m", "q", 100000), Recorder(self.tmp / "runs", "c"), ui,
                          stream=False, mode=mode)
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        return s, ui

    def test_chat_refuse_in_auto(self):
        s, _ = self.sess()
        seq = [resp(calls=[("run_shell", {"command": "open https://www.naver.com"})]), resp("못 했다")]
        with mock.patch.object(C, "chat", side_effect=seq):
            s.turn("네이버 띄워 봐")
        tool = [m for m in s.messages if m.get("role") == "tool"][-1]["content"]
        self.assertIn("이 컴퓨터를 조작하는 기능은 꺼져 있어", tool)

    def test_chat_ask_when_enabled(self):
        s, ui = self.sess(raw={"host_gui": True})
        asked = []
        ui.ask = lambda q: (asked.append(q), "n")[1]
        seq = [resp(calls=[("run_shell", {"command": "open https://www.naver.com"})]), resp("안 했다")]
        with mock.patch.object(C, "chat", side_effect=seq):
            s.turn("네이버 띄워 봐")
        self.assertIn("이 컴퓨터", asked[0])
        self.assertIn("open https://www.naver.com", asked[0])
        tool = [m for m in s.messages if m.get("role") == "tool"][-1]["content"]
        self.assertIn("거부했다", tool)


class TMention(unittest.TestCase):
    def test_notes(self):
        t, sc = TM.notes("웹 검색해서 알려 줘", {"web_search", "read_file"}, set())
        self.assertIn("그 도구(web_search)부터", t)
        self.assertEqual(sc, [])
        t, sc = TM.notes("웹 검색해서 알려 줘", {"read_file"}, {"web_search"})
        self.assertIn("꺼져 있다", t)
        self.assertTrue(sc)
        t, _ = TM.notes("인터넷에서 찾아 줘", {"read_file"}, set(), {"web_search": "검색 주소 없음"})
        self.assertIn("없다(검색 주소 없음)", t)
        self.assertEqual(TM.notes("파일 고쳐 줘", {"read_file"}, set()), ("", []))

    def test_web_lookup(self):
        self.assertTrue(W.recent_hit("claude fable 5.2 알아봐"))
        self.assertTrue(W.recent_hit("Fable 5.2 가 뭐야?"))
        self.assertEqual(W.recent_hit("이 폴더 파일 찾아봐"), "")
        self.assertEqual(W.recent_hit("README 고쳐"), "")


if __name__ == "__main__":
    unittest.main()
