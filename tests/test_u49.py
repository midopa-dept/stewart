"""판 67 실사용 지적 SW-4 2·3: 큰 일은 단계 계획 → 묶음마다 요약 + "진행할까요?"(허브 메인·chat 지시문, "끝까지"면 그 차례만 끝까지),
차례 끝 폰 알림(끝·질문·막힘, 1분 안 차례·사람이 멈춘 차례·밤 조용 시간은 안 알림, 답 글은 싣지 않음)."""

import os
from datetime import datetime
from unittest import mock

from stewart.config import load_profile
from stewart.hubmain import HUB_MAIN_NOTE
from tests.test_hub import msg
from tests.test_hub3 import MainBase


class TCheckpoint(MainBase):
    def test_prompts(self):
        for s in (HUB_MAIN_NOTE, load_profile("chat").system):
            self.assertIn("단계 세우고 묻기", s)
            self.assertIn("진행할까요?", s)
            self.assertIn("끝까지", s)

    def test_turn_end_notify(self):
        sent = []
        self.hub._notify_on, self.hub._notify = True, sent.append
        self.assertIsNone(self.main._notify_turn_end("answered", 30))           # 짧은 차례
        self.assertIsNone(self.main._notify_turn_end("answered", 90, stopped=True))
        self.main.ui._add("assistant", "다 고쳤다.")
        self.assertEqual(self.main._notify_turn_end("answered", 90), "끝")
        self.main.ui._add("assistant", "다음은 시험입니다. 진행할까요?")
        self.assertEqual(self.main._notify_turn_end("answered", 125), "질문")
        self.assertEqual(self.main._notify_turn_end("max_steps", 200), "막힘")
        self.assertEqual(sent[0], "stewart: 차례 끝(1분 30초)")
        self.assertTrue(all("고쳤다" not in x for x in sent))                    # 답 글은 싣지 않는다
        with mock.patch.dict(os.environ, {"STEWART_BOARD_QUIET": "1"}), \
                mock.patch("stewart.board._quiet", return_value=True):
            self.assertIsNone(self.main._notify_turn_end("answered", 300))     # 밤 조용

    def test_wired_in_turn(self):
        sent = []
        self.hub._notify_on, self.hub._notify = True, sent.append
        self.r.scripts["MAIN"] = [msg("끝냈다")]
        with mock.patch.dict(os.environ, {"STEWART_NOTIFY_DONE_MIN_S": "0.0001"}):
            self.main.send("해 줘")
            self.assertTrue(self.main.wait_idle())
        self.assertTrue(any(x.startswith("stewart: 차례 끝") for x in sent), sent)
