"""판 109(SW-43 ③): 쉬는 메인 깨우기 — 메인 차례가 끝난 뒤 작업자가 끝나면 사람 입력 없이 한 차례(연달아 3번까지, 사람이 말하면 셈 새로,
STEWART_AUTO_WAKE=0 끔), 허브 상태 workers(작업자 띠)."""

import os
import time
from unittest import mock

from stewart import spawn as SP
from tests.test_hub import msg
from tests.test_hub3 import MainBase


class TWake(MainBase):
    def pool_with_done(self):
        p = SP.Pool(lambda a, w: (True, f"{w} 결과"), 1)
        self.main.sess.pool = p
        p.spawn({"task": "조사"})
        for _ in range(50):
            if p.w["w1"]["status"] == "done":
                break
            time.sleep(0.05)
        return p

    def test_wake_and_limit(self):
        p = self.pool_with_done()
        self.assertEqual(self.main.state()["workers"][0]["status"], "done")
        self.r.scripts["MAIN"] = [msg("작업자 결과를 받았다")]
        self.assertEqual(self.main.auto_wake(), "woke")
        self.assertTrue(self.main.wait_idle())
        self.assertIn("workers(action='collect')", str(self.r.requests["MAIN"][-1]["messages"][-1]["content"]))
        self.assertTrue(any("자동으로 이어 감" in m["text"] for m in self.main.state()["main_log"]))
        self.assertIsNone(self.main.auto_wake())                                    # 같은 작업자로 두 번 깨우지 않음
        p.spawn({"task": "둘째"})
        time.sleep(0.3)
        self.main._auto_wakes = 3
        self.assertIsNone(self.main.auto_wake())                                    # 연달아 3번 넘으면 멈춤
        self.assertTrue(any("멈췄다" in m["text"] for m in self.main.state()["main_log"]))
        self.r.scripts["MAIN"] = [msg("네")]
        self.main.send("계속")
        self.assertTrue(self.main.wait_idle())
        self.assertEqual(self.main._auto_wakes, 0)                                  # 사람이 말하면 셈 새로

    def test_off(self):
        self.pool_with_done()
        with mock.patch.dict(os.environ, {"STEWART_AUTO_WAKE": "0"}):
            self.assertIsNone(self.main.auto_wake())
