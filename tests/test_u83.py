"""판 105(SW-45 ①): 허브 차례 되감기 — 파일만(그 차례 시작 체크포인트로, 대화 그대로·다음 말 앞 알림) / 차례 지우기(대화를 그 사람 말
앞에서 자르고 말을 돌려줌, 화면 기록도 그 말부터 지움), 일하는 중이면 거절, 모르는 차례 거절, 웹 POST /api/main/undo_turn."""

import json
import unittest
from pathlib import Path

from tests import test_hub_ui as HU
from tests.test_hub import msg


class TWeb(HU.TWebSessions):
    locals().update({k: None for k in dir(HU.TWebSessions) if k.startswith("test")})

    def turn(self, text, content):
        self.r.scripts["MAIN"] = [msg(calls=[("write_file", {"path": "a.txt", "content": content})]), msg(f"{content} 를 썼다")]
        self.assertEqual(self.main.send(text), "started")
        self.assertTrue(self.main.wait_idle())

    def test_files_only_then_cut(self):
        wd = Path(self.main.sess.env.workdir)
        self.turn("a.txt 에 하나", "하나")
        self.turn("a.txt 를 둘로", "둘")
        self.assertEqual((wd / "a.txt").read_text(encoding="utf-8"), "둘")
        log = self.main.state()["main_log"]
        self.assertEqual([m.get("turn") for m in log if m["kind"] == "user"], [1, 2])
        self.assertEqual([m.get("turn") for m in log if m["kind"] == "files"], [1, 2])
        n_msgs = len(self.main.sess.messages)
        code, r = self.post("/api/main/undo_turn", turn=2, cut=False)                 # 파일만
        self.assertEqual(code, 200)
        self.assertEqual((wd / "a.txt").read_text(encoding="utf-8"), "하나")
        self.assertEqual(len(self.main.sess.messages), n_msgs)                       # 대화 그대로
        self.assertIn("되돌렸다", self.main.sess._rewind_note)
        code, r = self.post("/api/main/undo_turn", turn=2, cut=True)                  # 차례 지우기
        self.assertEqual((code, r["text"]), (200, "a.txt 를 둘로"))
        self.assertEqual(r["dropped"], n_msgs - len(self.main.sess.messages))
        msgs = json.dumps(self.main.sess.messages, ensure_ascii=False)
        self.assertNotIn("a.txt 를 둘로", msgs)
        self.assertIn("a.txt 에 하나", msgs)
        self.assertIsNone(self.main.sess._rewind_note)                               # 잘랐으면 알림 없이 맞는 상태
        users = [m["text"] for m in self.main.state()["main_log"] if m["kind"] == "user"]
        self.assertEqual(users, ["a.txt 에 하나"])
        self.assertEqual(self.post("/api/main/undo_turn", turn=2, cut=True)[0], 409)  # 이미 지운 차례
        self.assertEqual(self.post("/api/main/undo_turn", turn="1", cut=True)[0], 400)
        self.turn("다시 둘로", "둘")                                                  # 지운 뒤 다시 보내기 — 새 차례로 돈다
        self.assertEqual([m["text"] for m in self.main.state()["main_log"] if m["kind"] == "user"], ["a.txt 에 하나", "다시 둘로"])


    def test_budget(self):
        full = self.main.state()["budget"]
        code, r = self.post("/api/main/budget", tokens=8000)
        self.assertEqual((code, r["budget"]), (200, 8000))
        st = self.main.state()
        self.assertEqual((st["budget"], st["budget_set"]), (8000, True))
        self.assertEqual(self.main.sess.comp.full_context if hasattr(self.main.sess.comp, "full_context") else 8000, 8000)
        self.assertEqual(self.post("/api/main/budget", tokens=10 ** 9)[1]["budget"], self.main.sess.backend.max_context)   # 서버 창 위로는 못 감
        self.assertEqual(self.post("/api/main/budget", tokens=None)[1]["budget"], full)
        self.assertFalse(self.main.state()["budget_set"])
        self.assertEqual(self.post("/api/main/budget", tokens="8k")[0], 400)


if __name__ == "__main__":
    unittest.main()
