"""판 56 생각 블록: 허브가 받는 생각 글의 끝을 화면 상태(live.think_text·think_since)에 싣고, 단계가 끝나면 접힌 블록(kind think: 글·자 수·걸린 초)으로 남긴다."""

import tempfile
import unittest
from pathlib import Path

from stewart import hub as H
from stewart.hubmain import THINK_KEEP, HubMainUI


class TThink(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.hub = H.Hub(d / "hub", d / "runs")
        self.addCleanup(self.hub.close)
        self.ui = HubMainUI(self.hub)

    def test_live_and_log(self):
        ui = self.ui
        ui.stream_start()
        ui.stream_piece("", "## 폴더 보기\n")
        ui.stream_piece("", "먼저 ls")
        self.assertEqual(ui.live["think_text"], "## 폴더 보기\n먼저 ls")
        self.assertTrue(ui.live["think_since"])
        ui.stream_piece("답", "")
        self.assertFalse(ui.stream_end())
        m = [x for x in ui.log if x["kind"] == "think"]
        self.assertEqual(len(m), 1)
        self.assertEqual((m[0]["text"], m[0]["chars"], m[0]["cut"]), ("## 폴더 보기\n먼저 ls", len("## 폴더 보기\n먼저 ls"), False))
        self.assertGreaterEqual(m[0]["secs"], 0)
        ui.stream_start()                                         # 생각 없는 단계는 블록을 남기지 않는다
        ui.stream_piece("그냥 답", "")
        ui.stream_end()
        self.assertEqual(len([x for x in ui.log if x["kind"] == "think"]), 1)

    def test_cut(self):
        ui = self.ui
        ui.stream_start()
        ui.stream_piece("", "가" * (THINK_KEEP + 10))
        ui.stream_end()
        m = [x for x in ui.log if x["kind"] == "think"][0]
        self.assertEqual((len(m["text"]), m["chars"], m["cut"]), (THINK_KEEP, THINK_KEEP + 10, True))


if __name__ == "__main__":
    unittest.main()
