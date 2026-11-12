"""SW-28: 허브 메인 칸 /clear·/reset·/help, 화면 [새 대화](/api/main/new) — 지금 대화는 기록에 남고 빈 문맥으로 새로."""

import json
import unittest

from tests.test_hub import msg
from tests.test_hub3 import MainBase, TWeb


class TNewMain(MainBase):
    def test_clear_keeps_record_and_resets(self):
        self.r.scripts["MAIN"] = [msg("첫 대화 답")]
        self.main.send("첫 대화: 기억할 낱말은 귤")
        self.assertTrue(self.main.wait_idle())
        old = str(self.main.sess.rec.dir)
        self.assertGreater(self.main.tokens()["steps"], 0)
        board = self.hub.board
        self.assertEqual(self.main.send("/clear"), "cleared")
        self.assertNotEqual(str(self.main.sess.rec.dir), old)
        self.assertIs(self.hub.board, board)                         # 현황판 그대로
        t = self.main.tokens()
        self.assertEqual((t["steps"], t["last_prompt"], t["compactions"], t["summaries"]), (0, 0, 0, 0))
        log = self.main.state()["main_log"]
        self.assertEqual(len(log), 1)
        self.assertIn("새 대화를 시작했다", log[0]["text"])
        self.assertIn(old, log[0]["text"])
        self.assertIn(old, [p["run_dir"] for p in self.main.past_mains()])   # 지난 세션 목록에서 다시 열 수 있다
        self.r.scripts["MAIN"] = [msg("모른다")]
        self.main.send("아까 낱말이 뭐였지?")
        self.assertTrue(self.main.wait_idle())
        sent = json.dumps(self.r.requests["MAIN"][-1]["messages"], ensure_ascii=False)
        self.assertNotIn("귤", sent)                                     # 앞 문맥 없음
        self.main.reopen_main(old)                                      # 다시 열면 앞 대화
        self.r.scripts["MAIN"] = [msg("귤")]
        self.main.send("다시 묻는다")
        self.assertTrue(self.main.wait_idle())
        self.assertIn("귤", json.dumps(self.r.requests["MAIN"][-1]["messages"], ensure_ascii=False))

    def test_reset_alias_and_busy(self):
        self.assertEqual(self.main.send("/reset"), "cleared")
        self.main._busy.acquire()
        try:
            self.assertEqual(self.main.send("/clear"), "busy")
            self.assertTrue(any("일하는 중" in m["text"] for m in self.main.ui.log))
        finally:
            self.main._busy.release()

    def test_help_lists_hub_commands(self):
        self.assertEqual(self.main.send("/help"), "help")
        txt = self.main.ui.log[-1]["text"]
        for n in ("/clear", "/reset", "/compact", "/help"):
            self.assertIn(n, txt)


class TNewMainWeb(TWeb):
    def test_api(self):
        ck, csrf = self.login()
        h = {"Cookie": ck, "X-Stewart-Token": csrf, "Origin": self.origin, "Content-Type": "application/json"}
        r, data = self.req("GET", "/api/commands", headers={"Cookie": ck})
        names = [c["name"] for c in json.loads(data)["commands"]]
        self.assertEqual(names[:6], ["/clear", "/reset", "/compact", "/memory", "/improve", "/help"])   # 판 88: /memory · 판 93: /improve
        old = str(self.main.sess.rec.dir)
        r, data = self.req("POST", "/api/main/new", "{}", h)
        self.assertEqual(r.status, 200, data)
        self.assertNotEqual(json.loads(data)["run_dir"], old)
        self.main._busy.acquire()
        try:
            r, data = self.req("POST", "/api/main/new", "{}", h)
            self.assertEqual(r.status, 409)
        finally:
            self.main._busy.release()


for _n in [n for n in dir(TWeb) if n.startswith("test_")]:   # 물려받은 TWeb 시험은 여기서 다시 돌리지 않는다
    if _n != "test_api":
        setattr(TNewMainWeb, _n, None)

if __name__ == "__main__":
    unittest.main()
