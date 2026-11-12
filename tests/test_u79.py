"""판 100: 문맥 값 재기(stewart profiles --cost — 시스템·도구 정의·환경 블록 토큰 어림, 허브 꼴, 뼈대) + 빠졌던 손잡이 둘
(STEWART_ENV_BLOCK=0 → 첫 사람 말 앞 환경 블록 없음, STEWART_GOAL_NOTES=0 → 허브 목표·같은 결과·끼워 넣기 알림 없음)."""

import io
import os
import unittest
from unittest import mock

from stewart import ctxcost as CC


class TCost(unittest.TestCase):
    def test_measure_and_knob(self):
        r = CC.measure("chat", clean=True)
        self.assertGreater(r["tools"], 1000)
        self.assertGreater(r["system"], r["system_injected"])
        self.assertEqual(r["total"], r["system"] + r["tools"] + r["env_block"])
        h = CC.measure("chat", hub=True, clean=True)
        self.assertGreater(h["tool_count"], r["tool_count"])                     # 세션 도구·현황판
        self.assertGreater(h["system"], r["system"])                              # 허브 역할 지시
        with mock.patch.dict(os.environ, {"STEWART_ENV_BLOCK": "0"}):
            self.assertEqual(CC.measure("chat", clean=True)["env_block"], 0)
        self.assertIn("| chat |", CC.table([r, h]))

    def test_cli(self):
        from stewart import cli
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            self.assertEqual(cli.main(["profiles", "--cost", "code", "--clean"]), 0)
        self.assertIn("| code |", buf.getvalue())

    def test_goal_notes_knob(self):
        from stewart.hub import Hub
        from stewart.hubmain import HubMainUI
        import tempfile
        from pathlib import Path
        hub = Hub(Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp()))
        self.assertTrue(HubMainUI(hub).goal_notes)
        with mock.patch.dict(os.environ, {"STEWART_GOAL_NOTES": "0"}):
            self.assertFalse(HubMainUI(hub).goal_notes)


if __name__ == "__main__":
    unittest.main()
