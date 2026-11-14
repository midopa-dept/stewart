"""판 143: 칩을 바꾸면 지시문의 "꺼져 있는 도구" 줄도 지금 상태로 — 세션 시작 때 줄이 남아 도구 목록과 어긋나지 않게.

- 실사용에서 본 것: 칩을 켠 긴 대화가 압축되자 "칩 켬" 알림 줄은 사라지고 지시문은 여전히 그 도구가 꺼져 있다고 말해,
  모델이 도구 목록에 있는 도구를 "꺼져 있다, 켜 달라"고 답했다.
- 차례 도중에 칩을 켜도 다음 걸음의 지시문이 지금 상태를 말한다.
"""

import json
import unittest
from unittest import mock

from stewart import chat as C
from tests.test_u60 import resp
from tests.test_u78 import sess


def off_line(system: str) -> str:
    return next((ln for ln in system.split("\n") if ln.startswith(C.OFF_TOOLS_NOTE.split(":")[0])), "")


class TToggleLive(unittest.TestCase):
    def mk(self, **raw):
        s = sess({"toggle_tools": ["list_dir"], **raw})
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        return s

    def test_on_rewrites_system(self):
        s = self.mk()
        self.assertIn("list_dir", off_line(s.messages[0]["content"]))
        s.set_tool("list_dir", True)
        self.assertEqual(off_line(s.messages[0]["content"]), "")                  # 다 켜지면 줄이 없다
        self.assertEqual(s.messages[0]["content"], s.system)
        s.set_tool("list_dir", False)
        self.assertIn("list_dir", off_line(s.messages[0]["content"]))             # 다시 끄면 다시 생긴다

    def test_starts_on_then_off(self):
        s = self.mk(toggle_on=["list_dir"])
        self.assertEqual(off_line(s.messages[0]["content"]), "")
        s.set_tool("list_dir", False)
        self.assertIn("list_dir", off_line(s.messages[0]["content"]))

    def test_after_compaction(self):
        s = self.mk()
        s.set_tool("list_dir", True)
        with mock.patch.object(C, "chat", return_value=resp("끝")):
            s.turn("첫 말")
        self.assertIn("켬", json.dumps(s.messages, ensure_ascii=False))           # 칩 알림은 첫 사람 말에 붙었다
        s.messages = [s.messages[0], {"role": "user", "content": "[압축된 앞 대화 요약]"}]   # 압축 꼴: 앞 말이 요약으로 바뀜
        sent = []
        with mock.patch.object(C, "chat", side_effect=lambda b, m, tools=None, *a, **k: (sent.append((m[0]["content"], [d["function"]["name"] for d in tools or []])), resp("끝"))[1]):
            s.turn("둘째 말")
        sysm, names = sent[0]
        self.assertIn("list_dir", names)
        self.assertNotIn("list_dir", off_line(sysm))                               # 목록에 있는 도구를 꺼졌다고 하지 않는다

    def test_mid_turn(self):
        s = self.mk()
        sent = []

        def fake(b, m, tools=None, *a, **k):
            sent.append((m[0]["content"], [d["function"]["name"] for d in tools or []]))
            if len(sent) == 1:
                s.set_tool("list_dir", True)                                          # 차례 도중(첫 걸음 뒤) 사람이 칩을 켬
                return resp(calls=[("read_file", {"path": "x"})])
            return resp("끝")
        with mock.patch.object(C, "chat", side_effect=fake):
            s.turn("목록을 봐")
        self.assertIn("list_dir", off_line(sent[0][0]))
        self.assertIn("list_dir", sent[1][1])
        self.assertNotIn("list_dir", off_line(sent[1][0]))


if __name__ == "__main__":
    unittest.main()
