"""판 162: 대화 안 작업자(workers 도구)의 걸음을 그 대화 안에서 본다 — 받은 일·생각·답·도구·결과. 생각은 지난 걸음에도 실린다."""

import json
import time
import unittest

from tests import test_hub_ui as HU
from tests.test_hub import msg
from tests.test_hub3 import MainBase


def think(m: dict, text: str) -> dict:
    m["choices"][0]["message"]["reasoning_content"] = text
    return m


class TInnerWorker(MainBase):
    req = HU.TWebSessions.req      # 화면 시험의 요청·로그인만 빌린다(그 시험들은 다시 돌리지 않는다)
    login = HU.TWebSessions.login

    def setUp(self):
        super().setUp()
        from stewart.cli import resolve
        from stewart.hubmain import HubMain
        from stewart.record import Recorder
        from stewart.web import WebApp, make_token
        (self.tmp / "main2.toml").write_text('name = "main2"\ntools = ["read_file", "write_file", "run_shell", "delegate"]\n'
                                             'max_steps = 10\nsystem = "MAIN 지시"\n', encoding="utf-8")
        profile, backend = resolve(str(self.tmp / "main2.toml"), "fake", str(self.wd))
        self.main.close()
        self.main = HubMain(self.hub, profile, backend, Recorder(self.tmp / "runs", "hub-main2"), mode="edits",
                            worker_profile=str(self.tmp / "w.toml"), worker_backend="fake")
        self.addCleanup(self.main.close)
        self.token = make_token(self.hub.dir)
        self.web = WebApp(self.main, self.hub, self.token)
        self.web.serve()
        self.addCleanup(self.web.close)
        self.host = f"127.0.0.1:{self.web.port}"
        self.origin = f"http://{self.host}"
        self.ck, self.csrf = self.login()

    def get(self, path):
        r, d = self.req("GET", path, headers={"Cookie": self.ck})
        return r.status, json.loads(d)

    def test_steps_and_thinking_visible(self):
        self.r.scripts["MAIN"] = [msg(calls=[("workers", {"action": "spawn", "task": "a.txt 를 써라", "tools": ["write_file"]})]),
                                  msg(calls=[("workers", {"action": "wait"})]), msg("끝")]
        self.r.scripts["WORKER"] = [think(msg(calls=[("write_file", {"path": "a.txt", "content": "A"})]), "먼저 a.txt 를 쓰자"),
                                    think(msg("a.txt 를 썼다"), "다 했다")]
        self.main.send("맡겨라")
        self.assertTrue(self.main.wait_idle())
        t0 = time.time()
        while time.time() - t0 < 10 and len(self.r.requests["WORKER"]) < 2:
            time.sleep(0.1)
        ws = self.main.state()["workers"]
        self.assertEqual([w["id"] for w in ws], ["w1"])
        self.assertTrue(ws[0]["log"])                                             # 걸음을 열 수 있다
        st, d = self.get("/api/chat/worker?chat=main&id=w1")
        self.assertEqual(st, 200)
        kinds = [it["kind"] for it in d["items"]]
        self.assertIn("think", kinds)
        self.assertIn("tool", kinds)
        self.assertIn("result", kinds)
        thinks = [it["text"] for it in d["items"] if it["kind"] == "think"]
        self.assertEqual(thinks, ["먼저 a.txt 를 쓰자", "다 했다"])            # 생각은 자르지 않고 전부
        self.assertTrue(any(it["kind"] == "assistant" and "a.txt 를 썼다" in it["text"] for it in d["items"]))
        self.assertIn("a.txt 를 써라", d["task"])
        st, d = self.get("/api/chat/worker?chat=main&id=w9")
        self.assertEqual(d.get("error"), "no_worker")
        st, d = self.get("/api/chat/worker?chat=main&id=../../etc")
        self.assertEqual(d.get("error"), "no_worker")                             # 경로 인자는 받지 않는다

    def test_session_worker_conversation_has_thinking(self):
        ev = {"kind": "step", "step": 1, "response": {"reasoning_content": "허브 작업자의 생각"},
              "new_messages": [{"role": "assistant", "content": "답"}]}
        items = self.web._conv_items(ev)
        self.assertEqual([i["kind"] for i in items], ["think", "assistant"])   # 허브 세션 작업자 보기에도 생각(접힘)
        self.assertEqual(items[0]["text"], "허브 작업자의 생각")


if __name__ == "__main__":
    unittest.main()
