"""실사용 길 훑기 — 상태가 대화 도중에 바뀌는 갈래(판 143–). 실사용 꼴 설정으로 허브를 띄우고, 가짜 모델의 요청 본문·화면 API 로 판정한다.

- 모드를 대화 도중에 바꿈(edits → plan → edits, 차례 도중, 승인 카드가 떠 있을 때).
- 긴 대화를 접은(요약) 뒤: 등급·모드·할 일 목록·지시문(기억 안내 포함)이 그대로인지.
- 허브를 차례 도중에 끄고 다시 켬(승인 대기 카드·도는 작업자가 있을 때).
- 모델 서버가 도중에 안 닿았다 다시 닿음, 그 사이 보낸 말.
- 대화를 닫았다 다시 연 뒤의 모드.
- 작업자가 도는 동안 메인의 모드를 바꿈.
"""

import threading
import time
import unittest

from stewart import modes as M
from tests.test_hub import msg
from tests.test_realuse_paths import PathBase, is_memory_helper, text_of, tool_results
from tests.test_realuse_shape import Hub, is_worker


def users(body) -> list[str]:
    return [str(m.get("content")) for m in body.get("messages") or [] if m.get("role") == "user"]


class MidBase(PathBase):
    def mode(self, mode, chat=None):
        return self.hub.post("/api/main/mode", {"mode": mode, **({"chat": chat} if chat else {})})

    def card(self, s=20):
        return self.hub.wait(lambda: next((q for q in self.state()["queue"] if q.get("kind") == "confirm"), None), s)

    def turn_bodies(self, n0):
        return [b for b in self.model.requests[n0:] if b.get("tools") and not is_worker(b) and not is_memory_helper(b)]


class M01ModeBetweenTurns(MidBase):
    """edits → plan → edits: 계획 모드 안내가 사람 말에 붙고, 쓰기는 막히고, 벗어나면 '계획 끝'이 붙는다."""

    def route(self, body):
        if is_memory_helper(body):
            return msg("")
        last = users(body)[-1] if users(body) else ""
        if "고쳐라" in last and not tool_results(body)[-1:]:
            return msg(calls=[("write_file", {"path": "p.txt", "content": "P"})])
        return msg("알겠다")

    def test_plan_then_back(self):
        self.say("안녕")
        self.assertTrue(self.idle(), self.log_tail())
        self.assertTrue(self.mode("plan")["ok"])
        self.assertEqual(self.state()["mode"], "plan")
        n0 = len(self.model.requests)
        self.say("p.txt 를 고쳐라")
        self.assertTrue(self.idle(), self.log_tail())
        bodies = self.turn_bodies(n0)
        self.assertIn(M.PLAN_NOTE.split("]")[0] + "]", users(bodies[0])[-1])                           # 계획 모드 안내
        self.assertFalse((self.proj / "p.txt").exists())                                          # 쓰기는 막힘
        self.assertTrue(any(M.PLAN_DENY in r for b in bodies for r in tool_results(b)))
        self.assertTrue(self.mode("edits")["ok"])
        n1 = len(self.model.requests)
        self.say("진행해")
        self.assertTrue(self.idle(), self.log_tail())
        last = users(self.turn_bodies(n1)[0])[-1]
        self.assertIn(M.PLAN_EXIT.split("]")[0] + "]", last)
        self.assertNotIn(M.PLAN_NOTE.split("]")[0] + "]", last)


class M02ModeMidTurn(MidBase):
    """차례 도중(첫 걸음이 도는 사이) plan 으로 바꾸면 다음 걸음의 쓰기는 막힌다."""

    def setUp(self):
        self.go = threading.Event()
        super().setUp()

    def route(self, body):
        if is_memory_helper(body):
            return msg("")
        n = len(tool_results(body))
        if n == 0:
            self.go.wait(10)
            return msg(calls=[("read_file", {"path": "README.md"})])
        if n == 1:
            return msg(calls=[("write_file", {"path": "q.txt", "content": "Q"})])
        return msg("끝")

    def test_plan_mid_turn(self):
        self.say("읽고 고쳐라")
        time.sleep(0.6)
        self.assertTrue(self.mode("plan")["ok"])
        self.go.set()
        self.assertTrue(self.idle(), self.log_tail())
        self.assertFalse((self.proj / "q.txt").exists())
        self.assertTrue(any(M.PLAN_DENY in r for b in self.main_reqs() for r in tool_results(b)))


class M03ModeWhileCard(MidBase):
    """승인 카드가 떠 있을 때 모드를 ask 로 바꿔도 카드는 그대로 답할 수 있고, 답이 그대로 먹는다(허브를 켤 때 모드 edits 보다 넓힐 수는 없다)."""

    def route(self, body):
        if is_memory_helper(body):
            return msg("")
        if not tool_results(body):
            return msg(calls=[("run_shell", {"command": "touch made.txt"})])
        return msg("끝")

    def test_card_survives_mode_change(self):
        self.say("파일 하나 만들어")
        q = self.card()
        self.assertTrue(q, self.log_tail())
        with self.assertRaises(AssertionError):                                                     # 켤 때 모드(edits)보다 넓히지 못함
            self.mode("auto")
        self.assertTrue(self.mode("ask")["ok"])
        self.assertTrue(next((x for x in self.state()["queue"] if x.get("seq") == q["seq"]), None))   # 카드는 남는다
        self.hub.post("/api/answer", {"session": q["session"], "seq": q["seq"], "answer": "y"})
        self.assertTrue(self.idle(), self.log_tail())
        self.assertTrue((self.proj / "made.txt").exists())
        self.assertEqual(self.state()["mode"], "ask")


class M04AfterSummary(MidBase):
    """등급 대화에서 모드·할 일 목록을 만든 뒤 앞 대화를 요약으로 접으면: 등급·모드·목록·지시문이 그대로다."""

    def route(self, body):
        if is_memory_helper(body):
            return msg("")
        sysm = str(body["messages"][0]["content"])
        if "요약" in sysm[:200] and not body.get("tools"):
            return msg("앞 대화 요약: 할 일 목록을 만들었다.")
        last = users(body)[-1] if users(body) else ""
        if "목록" in last and not tool_results(body):
            return msg(calls=[("todo_write", {"items": [{"content": "가 하기", "status": "in_progress"},
                                                        {"content": "나 하기", "status": "pending"}]})])
        return msg("알겠다")

    def test_state_survives_summary(self):
        name = self.hub.post("/api/chat/new", {"how": "fresh", "kind": "code", "tier": "moderato", "title": "새 대화", "from": "main"})["chat"]
        self.assertTrue(self.mode("ask", name)["ok"])
        for t in ("목록을 만들어", "둘째 말", "셋째 말"):
            self.say(t, name)
            self.assertTrue(self.idle(name), self.log_tail())
        before = self.turn_bodies(0)[-1]["messages"][0]["content"]
        todos = self.state(name)["todos"]
        self.assertEqual(len(todos), 2)
        r = self.hub.post("/api/main/compact", {"mode": "summary", "chat": name})
        self.assertTrue(r, self.log_tail())
        st = self.state(name)
        row = next(c for c in st["chats"] if c["name"] == name)
        self.assertEqual((row.get("tier"), st["mode"], st["todos"]), ("moderato", "ask", todos))   # 화면: 등급·모드·목록
        n0 = len(self.model.requests)
        self.say("접은 뒤 첫 말", name)
        self.assertTrue(self.idle(name), self.log_tail())
        body = self.turn_bodies(n0)[0]
        self.assertEqual(body["messages"][0]["content"], before)                                  # 지시문(기억 안내 포함) 그대로
        self.assertIn("사용자 기억", before)
        self.assertEqual(body.get("model"), "m-cloud")


class M05RestartWithCard(MidBase):
    """승인 카드가 떠 있을 때 허브를 끄고 다시 켜면: 낡은 카드·바쁨이 남지 않고, 새 말이 앞 대화를 이어 받는다."""

    def route(self, body):
        if is_memory_helper(body):
            return msg("")
        if "다시 켠 뒤" in text_of(body):
            return msg("이어 받았다")
        if not tool_results(body):
            return msg(calls=[("run_shell", {"command": "touch made.txt"})])
        return msg("끝")

    def test_restart_while_card(self):
        self.say("카드 띄울 말")
        self.assertTrue(self.card(), self.log_tail())
        self.hub.close()
        self.hub = Hub(self.root, self.env)
        self.addCleanup(self.hub.close)
        st = self.idle()
        self.assertTrue(st, self.log_tail())
        self.assertFalse([q for q in st["queue"] if q.get("kind") == "confirm"], st["queue"])     # 낡은 카드 없음
        self.assertFalse((self.proj / "made.txt").exists())
        n0 = len(self.model.requests)
        self.say("다시 켠 뒤 첫 말")
        self.assertTrue(self.idle(), self.log_tail())
        body = self.turn_bodies(n0)[0]
        self.assertIn("카드 띄울 말", text_of(body))


class M06RestartWithWorker(MidBase):
    """작업자가 도는 중에 허브를 끄고 다시 켜면: 화면에 '도는 중' 작업자가 남지 않고 메인이 새 말을 받는다."""

    def route(self, body):
        if is_memory_helper(body):
            return msg("")
        if is_worker(body):
            time.sleep(30)
            return msg("늦은 작업자")
        if "다시 켠 뒤" in text_of(body):
            return msg("받았다")
        if not tool_results(body):
            return msg(calls=[("workers", {"action": "spawn", "task": "README.md 를 오래 읽어라"})])
        return msg("맡겼다")

    def test_restart_while_worker(self):
        self.say("맡겨라")
        self.assertTrue(self.hub.wait(lambda: any(is_worker(b) for b in self.model.requests), 20), self.log_tail())
        self.hub.close()
        self.hub = Hub(self.root, self.env)
        self.addCleanup(self.hub.close)
        st = self.idle()
        self.assertTrue(st, self.log_tail())
        running = [w for w in st.get("workers") or [] if str(w.get("status") or w.get("state") or "") in ("running", "busy")]
        self.assertFalse(running, st.get("workers"))
        self.say("다시 켠 뒤 첫 말")
        self.assertTrue(self.idle(), self.log_tail())


class M07BackendDownUp(MidBase):
    """모델 서버가 안 닿을 때 보낸 말: 바쁨이 풀리고 까닭이 화면에 보인다. 다시 닿으면 다음 말이 돈다(앞 말도 대화에 남는다)."""

    def route(self, body):
        return msg("") if is_memory_helper(body) else msg("닿았다")

    def test_down_then_up(self):
        self.say("첫 말")
        self.assertTrue(self.idle(), self.log_tail())
        self.model.down = True
        self.say("안 닿을 때 보낸 말")
        st = self.idle(s=90)
        self.assertTrue(st, self.log_tail())
        kinds = [(m.get("kind"), str(m.get("text") or "")[:120]) for m in st["main_log"][-6:]]
        self.assertTrue([k for k in kinds if k[0] in ("error", "warn", "turn_end", "info") and k[1]], kinds)
        self.model.down = False
        n0 = len(self.model.requests)
        self.say("다시 닿은 뒤 말")
        st = self.idle(s=60)
        self.assertTrue(st, self.log_tail())
        body = self.turn_bodies(n0)[0]
        self.assertIn("다시 닿은 뒤 말", text_of(body))
        self.assertIn("닿았다", [m.get("text") for m in st["main_log"] if m.get("kind") == "assistant"][-1])


class M08ReopenMode(MidBase):
    """plan 으로 바꾼 대화를 닫았다 다시 열면 모드는? — 판정: 허브의 켤 때 모드(edits) 이하이고 화면과 실제가 같다."""

    def route(self, body):
        return msg("") if is_memory_helper(body) else msg("알겠다")

    def test_reopen_mode(self):
        name = self.hub.post("/api/chat/new", {"how": "fresh", "kind": "code", "title": "새 대화", "from": "main"})["chat"]
        self.assertTrue(self.mode("plan", name)["ok"])
        self.say("첫 말", name)
        self.assertTrue(self.idle(name), self.log_tail())
        run = self.hub.post("/api/chat/close", {"chat": name})["run_dir"]
        again = self.hub.post("/api/chat/new", {"how": "reopen", "run_dir": run, "title": "첫 말"})["chat"]
        st = self.state(again)
        n0 = len(self.model.requests)
        self.say("다시 연 뒤 말", again)
        self.assertTrue(self.idle(again), self.log_tail())
        last = users(self.turn_bodies(n0)[0])[-1]
        self.assertEqual(st["mode"] == "plan", M.PLAN_NOTE.split("]")[0] + "]" in last, (st["mode"], last[-300:]))   # 화면 모드 = 모델이 받는 모드


class M09ModeWhileWorker(MidBase):
    """작업자가 도는 동안 메인을 plan 으로 바꿔도: 이미 뜬 쓰기 작업자는 일을 마치고, 메인은 결과를 모은다."""

    def setUp(self):
        self.go = threading.Event()
        super().setUp()

    def route(self, body):
        if is_memory_helper(body):
            return msg("")
        if is_worker(body):
            self.go.wait(10)
            return msg(calls=[("write_file", {"path": "w.txt", "content": "W"})]) if not tool_results(body) else msg("w.txt 를 썼다")
        n = len(tool_results(body))
        if n == 0:
            return msg(calls=[("workers", {"action": "spawn", "task": "w.txt 에 써라", "tools": ["write_file"]})])
        if n == 1:
            return msg(calls=[("workers", {"action": "wait"})])
        return msg("받았다")

    def test_plan_while_worker(self):
        self.say("맡겨라")
        self.assertTrue(self.hub.wait(lambda: any(is_worker(b) for b in self.model.requests), 20), self.log_tail())
        self.assertTrue(self.mode("plan")["ok"])
        self.go.set()
        self.assertTrue(self.idle(s=60), self.log_tail())
        got = [r for b in self.main_reqs() for r in tool_results(b) if "w.txt" in r]
        self.assertTrue(got, [tool_results(b) for b in self.main_reqs()])
        self.assertFalse([r for b in self.main_reqs() for r in tool_results(b) if "작업자가 실패했다" in r])


if __name__ == "__main__":
    unittest.main()
