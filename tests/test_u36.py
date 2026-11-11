"""판 49 stewart 자체 현황판: 사용자 할 일(U-번호)·진행·끝난 일·결정, 차례 끝 꼬리표, 새 할 일 폰 알림, 사람이 화면에서 닫기."""

import tempfile
import unittest
from pathlib import Path

from stewart.board import Board, execute
from tests.test_hub import msg
from tests.test_hub3 import MainBase


class TBoard(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.b = Board(Path(tempfile.mkdtemp()), notify=self.sent.append)

    def test_todos(self):
        a = self.b.add_todo("Vast 토큰 넣기", "키체인에", "손작업", "막힘")
        c = self.b.add_todo("분석 문구 정하기", type="결정")
        self.assertEqual((a["id"], c["id"]), ("U-1", "U-2"))
        self.assertEqual(self.b.add_todo("Vast 토큰 넣기")["id"], "U-1")                 # 같은 열린 할 일은 다시 안 넣음
        self.assertEqual(self.sent, ["stewart 사용자 할 일 U-1: Vast 토큰 넣기", "stewart 사용자 할 일 U-2: 분석 문구 정하기"])
        self.assertEqual([t["id"] for t in self.b.open_todos()], ["U-1", "U-2"])            # 막힘이 보통보다 앞
        self.b.close_todo("U-1", "done", "넣음")
        self.assertEqual([t["id"] for t in self.b.open_todos()], ["U-2"])
        self.assertEqual(self.b.add_todo("새 일")["id"], "U-3")                           # 번호는 다시 안 씀
        for bad in ("x" * 41, "https://host/x 열기", "토큰 " + "a" * 40):
            with self.assertRaises(ValueError):
                self.b.add_todo(bad)
        with self.assertRaises(ValueError):
            self.b.close_todo("U-9")

    def test_progress_done_decide_tail(self):
        self.b.progress("새 화면", "폰 폭 찍기")
        self.b.progress("새 화면", "보고")                                                  # 같은 것은 고친다
        self.assertEqual([(p["what"], p["next"]) for p in self.b.view()["progress"]], [("새 화면", "보고")])
        self.b.done("새 화면")
        self.assertEqual(self.b.view()["progress"], [])
        self.assertEqual(self.b.view()["done"][0]["what"], "새 화면")
        self.b.decide("시험 폴더를 labs 로", "사용자 자리와 나눔", "메인", True)
        self.assertEqual(self.b.view()["decision"][0]["by"], "메인")
        self.assertEqual(self.b.tail(), "")
        self.b.add_todo("문구 정하기", type="결정")
        self.assertEqual(self.b.tail([{"session": "w1", "seq": 3}]), "열린 사용자 할 일: U-1 문구 정하기 · 승인 w1 #3")
        ok, out = execute(self.b, {"op": "show"})
        self.assertIn("U-1", out)
        self.assertFalse(execute(self.b, {"op": "nope"})[0])


class THub(MainBase):
    def test_main_writes_board_and_tail(self):
        sent = []
        self.hub.board.notify = sent.append
        self.r.scripts["MAIN"] = [msg(calls=[("board", {"op": "add_todo", "title": "배포 승인", "type": "승인", "detail": "48 을 켤지"})]),
                                  msg(calls=[("board", {"op": "progress", "what": "판 49", "next": "화면"})]),
                                  msg("U-1 배포 승인을 부탁드립니다.")]
        self.main.send("할 일 정리해")
        self.assertTrue(self.main.wait_idle())
        st = self.main.state()
        self.assertEqual([t["id"] for t in st["board"]["open"]], ["U-1"])
        self.assertEqual(st["board"]["progress"][0]["what"], "판 49")
        self.assertEqual(sent, ["stewart 사용자 할 일 U-1: 배포 승인"])
        tails = [m["text"] for m in st["main_log"] if m["kind"] == "tail"]
        self.assertEqual(tails, ["열린 사용자 할 일: U-1 배포 승인"])
        from stewart.hubmain import HUB_MAIN_NOTE  # noqa: F401
        self.assertIn("board add_todo", self.main.sess.system)

    def test_user_closes_from_web(self):
        from stewart.web import WebApp
        self.hub.board.add_todo("문구 정하기", type="결정")
        web = WebApp(self.main, self.hub, "tok")
        self.addCleanup(web.httpd.server_close)
        t = self.hub.board.close_todo("U-1", "done", "권장대로", by="사용자")
        self.assertEqual((t["status"], t["closed_by"]), ("done", "사용자"))
        self.assertEqual(self.main.state()["board"]["open"], [])


if __name__ == "__main__":
    unittest.main()
