"""판 53 stewart 현황판 나머지: 순서표(돈·장애 > 마감 > 남을 막음 > 나머지)·아직 아님(after·waiting·not_before)·
풀리면 알림·밤 조용(23:00~07:30 돈·장애만)·작업자 보고에서 사용자 할 일 뽑기·결정 장부 내보내기."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from stewart.board import Board, execute, extract_todos, take_from_report
from tests.test_hub import Base, msg, wait


class TOrder(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.b = Board(Path(tempfile.mkdtemp()), notify=self.sent.append)

    def test_order_and_later(self):
        soon = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        far = (datetime.now() + timedelta(days=9)).strftime("%Y-%m-%d")
        self.b.add_todo("보통 일")                                         # U-1
        self.b.add_todo("마감 먼 일", due=far)                             # U-2 → 나머지
        self.b.add_todo("마감 가까운 일", due=soon)                        # U-3 → 마감
        self.b.add_todo("돈 새는 것", urgency="돈")                        # U-4 → 맨 앞
        self.b.add_todo("U-1 뒤에 할 일", after=["U-1"])                    # U-5 → 아직 아님, U-1 은 남을 막음
        self.b.add_todo("받기 끝나면", waiting="받기 끝")                   # U-6 → 아직 아님
        now, later = self.b.order()
        self.assertEqual([t["id"] for t in now], ["U-4", "U-3", "U-1", "U-2"])
        self.assertEqual([t["why"] for t in now[:3]], ["돈", "마감 " + soon, "남을 막음"])
        self.assertEqual({t["id"]: t["later"] for t in later}, {"U-5": "먼저: U-1", "U-6": "기다림: 받기 끝"})
        self.assertNotIn("stewart 사용자 할 일 U-5: U-1 뒤에 할 일", self.sent)       # 아직 아님은 풀릴 때 울린다
        self.b.close_todo("U-1", "done")
        self.assertEqual(self.sent[-1], "stewart 사용자 할 일 U-5 (이제 할 수 있음): U-1 뒤에 할 일")
        self.assertIn("(U-6 받기 끝나면 — 기다림: 받기 끝)", self.b.tail())
        with self.assertRaises(ValueError):
            self.b.add_todo("없는 앞일", after=["U-99"])
        with self.assertRaises(ValueError):
            self.b.add_todo("날짜 틀림", due="11월 14일")
        ok, out = execute(self.b, {"op": "add_todo", "title": "도구로 넣기", "after": ["U-3"], "due": soon})
        self.assertTrue(ok, out)
        self.assertIn("아직 아님", execute(self.b, {"op": "show"})[1])

    def test_quiet_night(self):
        with mock.patch.dict(os.environ, {"STEWART_BOARD_QUIET": "1"}):
            self.b.clock = lambda: datetime(2026, 11, 11, 23, 30)
            self.b.add_todo("밤의 보통 일")
            self.b.add_todo("밤의 장애", urgency="장애")
            self.b.clock = lambda: datetime(2026, 11, 11, 7, 40)
            self.b.add_todo("아침 일")
        self.assertEqual(self.sent, ["stewart 사용자 할 일 U-2: 밤의 장애", "stewart 사용자 할 일 U-3: 아침 일"])

    def test_ledger(self):
        self.b.decide("시험 폴더를 labs 로", "사용자 자리와 나눔 | 까닭", "메인", True)
        self.b.decide("배포 판 49", "", "사용자", False)
        self.b.add_todo("문구 정하기", type="결정")
        self.b.close_todo("U-1", "done", "권장대로", by="사용자")
        md = self.b.ledger_md()
        self.assertIn("| 사용자 | 배포 판 49 |  | 아니오 |", md)
        self.assertIn("사용자 자리와 나눔 \\| 까닭", md)                                  # 표 칸 깨짐 막기
        self.assertIn("| U-1 | 문구 정하기 | 결정 |", md)
        self.assertIn("했음 — 권장대로", md)


class TExtract(unittest.TestCase):
    def test_extract(self):
        text = ("a.txt 를 고쳤다.\n\n사용자 할 일:\n- [손작업/막힘] Vast 토큰 넣기 — 키체인에 한 번, 안 하면 빌리기가 멈춘다\n"
                "- [결정] 배포 판 고르기\n- 그냥 확인할 것 -- 화면 한 번\n\n다른 글\n- 이건 목록이지만 절 밖")
        got = extract_todos(text)
        self.assertEqual([(x["title"], x["type"], x["urgency"]) for x in got],
                         [("Vast 토큰 넣기", "손작업", "막힘"), ("배포 판 고르기", "결정", "보통"), ("그냥 확인할 것", "확인", "보통")])
        self.assertEqual(got[0]["detail"], "키체인에 한 번, 안 하면 빌리기가 멈춘다")
        self.assertEqual(extract_todos("## 사람이 할 일\n1. [마감] 서류 내기\n"), [{"title": "서류 내기", "detail": "", "type": "확인", "urgency": "마감"}])
        self.assertEqual(extract_todos("보고만 있다\n- 목록"), [])
        b = Board(Path(tempfile.mkdtemp()))
        got = take_from_report(b, "w1", "사용자 할 일:\n- https://host/x 열기\n- [확인] 화면 보기 — 폰 폭")
        self.assertEqual([(t["id"], t["title"], t["source"]) for t in got], [("U-1", "화면 보기", "w1")])   # 주소 꼴 제목은 건너뜀
        self.assertTrue(b.load()["todo"][0]["detail"].startswith("[w1 보고] "))


class THubReport(Base):
    def test_worker_report_lifts_todo(self):
        sent = []
        self.hub.board = Board(self.hub.dir, notify=sent.append)
        self.fake.script = [msg("다 했다.\n\n사용자 할 일:\n- [승인/보통] 배포 켜기 — 48 을 켤지")]
        self.spawn("보고하라")
        self.hub.wait_report("w1", 1)
        r = wait(lambda: self.hub.tick()["reports"])
        self.assertIn("보고의 사용자 할 일을 판에 올림: U-1 배포 켜기", r[0])
        self.assertEqual(sent, ["stewart 사용자 할 일 U-1: 배포 켜기"])
        self.assertEqual(self.hub.board.open_todos()[0]["source"], "w1")
        self.assertIn("사용자 할 일:", self.fake.requests[0]["messages"][0]["content"])   # 작업자 지시문에 꼴


if __name__ == "__main__":
    unittest.main()
