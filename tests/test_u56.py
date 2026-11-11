"""SW-29: 허브 대화 여럿 — 메인과 같은 채팅(새로·갈래·다시 열기), 대화마다 기록·압축·문맥 따로, 승인·멈춤은 그 대화만."""

import json
import time
import unittest

from stewart import backend as B
from tests.test_hub import msg
from tests.test_hub3 import MainBase, TWeb


def sent(r, i=-1):
    return json.dumps(r.requests["MAIN"][i]["messages"], ensure_ascii=False)


class TChats(MainBase):
    def test_fresh_is_isolated(self):
        self.r.scripts["MAIN"] = [msg("메인 답")]
        self.main.send("메인에서: 낱말은 귤")
        self.assertTrue(self.main.wait_idle())
        c = self.main.new_chat("fresh")
        self.assertEqual(c.name, "chat-1")
        self.assertNotEqual(str(c.sess.rec.dir), str(self.main.sess.rec.dir))
        reg = self.hub.registry()["sessions"]
        self.assertEqual(reg["chat-1"]["role"], "chat")
        self.r.scripts["MAIN"] = [msg("모른다")]
        self.assertEqual(self.main.conv("chat-1").send("낱말이 뭐였지?"), "started")
        self.assertTrue(c.wait_idle())
        self.assertNotIn("귤", sent(self.r))
        st = c.state()
        self.assertEqual(st["chat"], "chat-1")
        self.assertTrue(any(m["kind"] == "assistant" and "모른다" in m["text"] for m in st["main_log"]))
        self.assertFalse(any("모른다" in (m.get("text") or "") for m in self.main.state()["main_log"]))   # 화면 기록 따로
        self.assertEqual([x["name"] for x in st["chats"]], ["main", "chat-1"])
        self.assertIs(c.hub.board, self.main.hub.board)                                                 # 현황판 공유

    def test_fork_copies_then_diverges(self):
        self.r.scripts["MAIN"] = [msg("메인 답")]
        self.main.send("메인에서: 낱말은 귤")
        self.assertTrue(self.main.wait_idle())
        c = self.main.new_chat("fork", source="main")
        self.r.scripts["MAIN"] = [msg("귤"), msg("메인 둘째 답")]
        c.send("낱말이 뭐였지?")
        self.assertTrue(c.wait_idle())
        self.assertIn("귤", sent(self.r))                       # 갈래는 앞 문맥을 가진다
        self.main.send("메인에서만: 사과")
        self.assertTrue(self.main.wait_idle())
        self.r.scripts["MAIN"] = [msg("…")]
        c.send("다른 낱말은?")
        self.assertTrue(c.wait_idle())
        self.assertNotIn("사과", sent(self.r))                   # 갈라진 뒤로는 서로 독립

    def test_chat_approval_routes_to_chat(self):
        self.r.scripts["MAIN"] = [msg(calls=[("run_shell", {"command": "touch c.txt && echo hi-from-chat"})]), msg("끝")]
        c = self.main.new_chat("fresh")
        c.send("돌려라")
        q = []
        for _ in range(100):
            q = [x for x in self.hub.queue() if x["session"] == "chat-1"]
            if q:
                break
            time.sleep(0.05)
        self.assertTrue(q, "대화의 승인 건이 대기열에 와야 한다")
        self.hub.answer("chat-1", q[0]["seq"], "y")
        self.assertTrue(c.wait_idle())
        tool = [m for m in self.r.requests["MAIN"][-1]["messages"] if m["role"] == "tool"]
        self.assertIn("hi-from-chat", tool[-1]["content"])
        self.assertFalse(self.main.busy)

    def test_close_and_reopen(self):
        self.r.scripts["MAIN"] = [msg("답")]
        c = self.main.new_chat("fresh")
        c.send("기억할 낱말은 배")
        self.assertTrue(c.wait_idle())
        run = self.main.close_chat("chat-1")
        self.assertNotIn("chat-1", self.hub.registry()["sessions"])
        with self.assertRaises(KeyError):
            self.main.conv("chat-1")
        self.assertIn(run, [p["run_dir"] for p in self.main.past_mains()])
        c2 = self.main.new_chat("reopen", run_dir=run)
        self.r.scripts["MAIN"] = [msg("배")]
        c2.send("낱말?")
        self.assertTrue(c2.wait_idle())
        self.assertIn("기억할 낱말은 배", sent(self.r))
        self.assertNotIn(str(c2.sess.rec.dir), [p["run_dir"] for p in self.main.past_mains()])   # 열린 대화는 지난 목록에 없다

    def test_clear_in_chat_only(self):
        c = self.main.new_chat("fresh")
        main_run = str(self.main.sess.rec.dir)
        self.assertEqual(c.send("/clear"), "cleared")
        self.assertEqual(str(self.main.sess.rec.dir), main_run)


class TAbortScope(unittest.TestCase):
    def test_scope_counters(self):
        B.set_abort_scope("chat-9")
        try:
            g0 = B._SCOPE_GEN.get("chat-9", 0)
            B.abort_streams("chat-8")
            self.assertEqual(B._SCOPE_GEN.get("chat-9", 0), g0)    # 다른 대화의 멈춤은 이 대화를 건드리지 않는다
            B.abort_streams("chat-9")
            self.assertEqual(B._SCOPE_GEN["chat-9"], g0 + 1)
        finally:
            B.set_abort_scope(None)


class TChatWeb(TWeb):
    def test_api(self):
        ck, csrf = self.login()
        h = {"Cookie": ck, "X-Stewart-Token": csrf, "Origin": self.origin, "Content-Type": "application/json"}
        r, data = self.req("POST", "/api/chat/new", json.dumps({"how": "fresh"}), h)
        self.assertEqual(r.status, 200, data)
        name = json.loads(data)["chat"]
        r, data = self.req("GET", f"/api/state?chat={name}", headers={"Cookie": ck})
        self.assertEqual(json.loads(data)["chat"], name)
        self.r.scripts["MAIN"] = [msg("대화 답")]
        r, data = self.req("POST", "/api/main", json.dumps({"text": "안녕", "chat": name}), h)
        self.assertEqual(r.status, 200, data)
        self.assertTrue(self.main.conv(name).wait_idle())
        self.assertFalse(self.main.state()["main_log"])                        # 메인 칸에는 안 간다
        r, data = self.req("GET", f"/api/session/conversation?name={name}", headers={"Cookie": ck})
        self.assertIn("안녕", data.decode())
        r, data = self.req("POST", "/api/chat/close", json.dumps({"chat": name}), h)
        self.assertEqual(r.status, 200, data)
        r, data = self.req("POST", "/api/main", json.dumps({"text": "x", "chat": name}), h)
        self.assertEqual(r.status, 404)
        r, data = self.req("GET", f"/api/state?chat={name}", headers={"Cookie": ck})
        self.assertTrue(json.loads(data).get("chat_gone"))


for _n in [n for n in dir(TWeb) if n.startswith("test_")]:   # 물려받은 TWeb 시험은 여기서 다시 돌리지 않는다
    setattr(TChatWeb, _n, None)

if __name__ == "__main__":
    unittest.main()
