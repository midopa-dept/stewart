"""판 161: 켜면 빈 새 대화(이어 가기는 사람이 고를 때만), 처음 대화도 닫는다, 작업자 보고 알림은 그 작업자를 띄운 대화로."""

import argparse
import json

from stewart.hubmain import last_main
from tests.test_hub import msg
from tests.test_hub3 import MainBase


class TLanding(MainBase):
    def test_first_conversation_hidden_until_talk(self):
        rows = {c["name"]: c for c in self.main.state()["chats"]}
        self.assertTrue(rows["main"]["hidden"])                         # 켜자마자는 목록에 없다(첫 화면 = 새 대화)
        self.r.scripts["MAIN"] = [msg("답")]
        self.main.send("안녕")
        self.assertTrue(self.main.wait_idle())
        rows = {c["name"]: c for c in self.main.state()["chats"]}
        self.assertFalse(rows["main"]["hidden"])

    def test_close_first_conversation(self):
        self.r.scripts["MAIN"] = [msg("답")]
        self.main.send("닫을 대화")
        self.assertTrue(self.main.wait_idle())
        old = str(self.main.sess.rec.dir)
        self.assertEqual(self.main.close_chat("main"), old)              # 메인도 닫힌다(기록은 지난 대화로)
        self.assertNotEqual(str(self.main.sess.rec.dir), old)
        self.assertTrue({c["name"]: c for c in self.main.state()["chats"]}["main"]["hidden"])
        self.assertIn(old, [m["run_dir"] for m in self.main.past_mains(100)])
        self.main.tick()                                                  # 허브 몸통(고리)은 그대로 돈다

    def test_start_fresh_unless_resume_last(self):
        self.r.scripts["MAIN"] = [msg("답")]
        self.main.send("앞 대화")
        self.assertTrue(self.main.wait_idle())
        self.assertIsNotNone(last_main(self.hub)[0])
        from stewart.hubmain import start_history
        self.assertEqual(start_history(argparse.Namespace(new_main=False), self.hub), (None, None))   # 기본은 이어 받지 않는다
        got, prev = start_history(argparse.Namespace(resume_last=True), self.hub)
        self.assertEqual(prev, str(self.main.sess.rec.dir))


class TNoticeToParent(MainBase):
    def test_report_notice_goes_to_spawning_chat(self):
        c = self.main.new_chat("fresh")
        self.r.scripts["MAIN"] = [msg(calls=[("session_spawn", {"name": "w1", "task": "a.txt 를 만들고 보고하라",
                                                                "workdir": str(self.wd)})]),
                                  msg("작업자를 띄웠다")]
        self.r.scripts["WORKER"] = [msg(calls=[("write_file", {"path": "a.txt", "content": "A"})]), msg("a.txt 를 만들었다")]
        c.send("일을 나눠라")
        self.assertTrue(c.wait_idle())
        self.assertEqual(self.hub.registry()["sessions"]["w1"]["parent"], c.name)
        self.hub.wait_report("w1", 1)
        self.main.tick()
        self.assertTrue(any(m["kind"] == "hub" and "w1 보고 0001 도착" in m["text"] for m in c.ui.log))
        self.assertFalse(any(m["kind"] == "hub" and "w1 보고" in m["text"] for m in self.main.ui.log))   # 메인으로 가지 않는다
        self.assertTrue(any("w1 보고 0001" in n for n in c.ui.notices))
        self.assertFalse(self.main.ui.notices)

    def test_closed_parent_falls_back_to_first(self):
        self.hub.notices.append("[허브] x 보고 0001 도착")
        self.hub.notice_parent["[허브] x 보고 0001 도착"] = "chat-99"   # 닫힌 대화
        self.main.tick()
        self.assertTrue(any("x 보고 0001" in n for n in self.main.ui.notices))
        self.assertEqual(json.dumps(self.hub.notice_parent), "{}")
