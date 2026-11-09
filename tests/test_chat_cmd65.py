"""chat 입력에서 명령인지 가르기. 첫 낱말이 등록된 명령과 정확히 같을 때만 명령, 경로로 시작하는 글은 메시지."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI


class TClassify(unittest.TestCase):
    def test_paths_are_messages(self):
        for t in ("/srv/private 를 정리하고 싶어 …", "/home/user/projects 아래 큰 파일 찾아 줘", "/srv/private",
                  "/ 에서 시작해", "/tmp/a.txt 읽어", "  /srv/x\n둘째 줄", "그냥 글", "/etc/hosts 보여 줘"):
            self.assertEqual(C.classify(t), ("msg",), t)

    def test_registered_commands(self):
        self.assertEqual(C.classify("/help"), ("cmd", "/help", ""))
        self.assertEqual(C.classify("/mode auto"), ("cmd", "/mode", "auto"))
        self.assertEqual(C.classify("/mode"), ("cmd", "/mode", ""))
        self.assertEqual(C.classify("/plan"), ("cmd", "/plan", ""))
        self.assertEqual(C.classify("/plan off"), ("cmd", "/plan", "off"))
        self.assertEqual(C.classify("/steps 12"), ("cmd", "/steps", "12"))
        self.assertEqual(C.classify("/rewind 3"), ("cmd", "/rewind", "3"))
        self.assertEqual(C.classify("/think default"), ("cmd", "/think", "default"))
        for c in C.COMMANDS:
            self.assertEqual(C.classify(c)[0], "cmd", c)                     # 인자 없이도 모두 받는다

    def test_bad_args_give_usage(self):
        for t, c in (("/mode fast", "/mode"), ("/steps 0", "/steps"), ("/steps x", "/steps"), ("/plan 정리해 줘", "/plan"),
                     ("/think maybe", "/think"), ("/help me", "/help"), ("/rewind 마지막", "/rewind"), ("/compact 지금", "/compact")):
            self.assertEqual(C.classify(t), ("usage", c), t)

    def test_typo_hint_only(self):
        self.assertEqual(C.classify("/hlep"), ("typo", "/help"))
        self.assertEqual(C.classify("/stauts"), ("typo", "/status"))
        self.assertEqual(C.classify("/mdoe auto"), ("typo", "/mode"))
        self.assertEqual(C.classify("/zzzz 뭔가"), ("msg",))                   # 비슷한 것이 없으면 그대로 글
        self.assertEqual(C.classify("/HELP"), ("typo", "/help"))               # 대소문자만 다른 것도 안내만


class TRepl(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.tmp, max_steps=3)
        self.ui = FakeUI()
        self.s = C.ChatSession(p, Backend("fake", "http://x/v1", "m", "q", 1000), Recorder(self.tmp / "runs", "chat-t"),
                               self.ui, mode="edits")
        self.addCleanup(lambda: self.s.rec._ev.closed or self.s.rec._ev.close())

    def drive(self, lines):
        sent = []
        with mock.patch.object(self.ui, "read_line", side_effect=list(lines) + [EOFError()]), \
                mock.patch.object(self.s, "turn", side_effect=lambda t: sent.append(t) or "answered"):
            C.repl(self.s)
        return sent, self.ui.out.getvalue()

    def test_path_message_goes_to_model(self):
        sent, out = self.drive(["/srv/private 를 정리하고 싶어 …", "/hlep", "/help", "/mode fast", "/mode auto"])
        self.assertEqual(sent, ["/srv/private 를 정리하고 싶어 …"])          # 경로 글만 모델에게, 오타·명령은 아님
        self.assertNotIn("모르는 명령", out)
        self.assertIn("혹시 /help?", out)
        self.assertIn("모드 바꾸기: /mode auto|edits|ask|plan", out)           # /help 맨 위
        self.assertLess(out.index("모드 바꾸기:"), out.index("명령: /exit"))
        self.assertIn("쓰는 법: /mode [auto|edits|ask|plan]", out)             # 틀린 인자 → 도움말 한 줄, 모드 그대로
        self.assertEqual(self.s.mode, "auto")                                  # 마지막 /mode auto 는 적용

    def test_start_screen_shows_mode_line(self):
        _, out = self.drive([])
        self.assertIn("모드 edits — 바꾸기: /mode auto|edits|ask|plan", out)
        self.assertTrue(C.HELP.startswith(C.MODE_HELP))


if __name__ == "__main__":
    unittest.main()
