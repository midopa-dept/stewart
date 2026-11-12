"""판 103(SW-45 ②): 허브 머리줄 모드·생각 — 켤 때 정한 모드보다 넓히지 못함, 바꾸면 다음 걸음부터(작업자 상한도 같이),
생각 켬·끔·기본(null), 상태 mode·mode_max·think, 웹 POST /api/main/mode·/api/main/think. 문맥 값은 그대로."""

import unittest

from tests import test_hub_ui as HU


class TWeb(HU.TWebSessions):
    locals().update({k: None for k in dir(HU.TWebSessions) if k.startswith("test")})

    def test_mode_think(self):
        st = self.main.state()
        self.assertEqual((st["mode"], st["mode_max"], st["think"]), ("edits", "edits", None))
        self.assertEqual(self.post("/api/main/mode", mode="plan"), (200, {"ok": True, "mode": "plan"}))
        self.assertEqual(self.main.sess.mode, "plan")
        self.assertEqual(self.main.tools.main_mode, "plan")                      # 작업자도 계획보다 넓을 수 없다
        code, r = self.post("/api/main/mode", mode="auto")
        self.assertEqual(code, 400)                                               # 켤 때 edits 였으면 auto 로 못 넓힘
        self.assertIn("넓힐 수 없다", r["error"])
        self.assertEqual(self.post("/api/main/mode", mode="edits")[0], 200)
        self.assertEqual(self.post("/api/main/think", think=True), (200, {"ok": True, "think": True}))
        self.assertTrue(self.main.sess.think)
        self.assertEqual(self.post("/api/main/think", think=None)[1]["think"], None)
        self.assertEqual(self.post("/api/main/think", think="on")[0], 400)
        sysm = self.main.sess.messages[0]["content"]
        self.post("/api/main/mode", mode="ask")
        self.assertEqual(self.main.sess.messages[0]["content"], sysm)            # 지시문은 그대로(문맥 값 0)


if __name__ == "__main__":
    unittest.main()
