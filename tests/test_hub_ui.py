"""허브 화면에서 사람이 세션을 열고·직접 말하고·멈추기(새 세션·직접 지시·멈춤/이어 가기/끝내기)."""

import http.client
import json
import os
import re
import subprocess
import unittest
from pathlib import Path
from urllib.parse import urlencode

from stewart import hub as H
from stewart.web import WebApp, make_token
from tests.test_hub import msg, wait
from tests.test_hub3 import MainBase


class TWebSessions(MainBase):
    def setUp(self):
        super().setUp()
        self.token = make_token(self.hub.dir)
        self.web = WebApp(self.main, self.hub, self.token)
        self.web.serve()
        self.addCleanup(self.web.close)
        self.host = f"127.0.0.1:{self.web.port}"
        self.origin = f"http://{self.host}"
        self.ck, self.csrf = self.login()
        self.good = {"Cookie": self.ck, "Origin": self.origin, "X-Stewart-Token": self.csrf, "Content-Type": "application/json"}

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=30)
        c.request(method, path, body=body, headers={"Host": self.host, **(headers or {})})
        r = c.getresponse()
        d = r.read()
        c.close()
        return r, d

    def login(self):
        r, _ = self.req("POST", "/login", urlencode({"token": self.token}),
                        {"Content-Type": "application/x-www-form-urlencoded", "Origin": self.origin})
        ck = r.getheader("Set-Cookie").split(";")[0]
        _, page = self.req("GET", "/", headers={"Cookie": ck})
        return ck, re.search(rb'name="csrf" content="([^"]+)"', page).group(1).decode()

    def post(self, path, **body):
        r, d = self.req("POST", path, json.dumps(body), self.good)
        return r.status, json.loads(d)

    def test_new_send_control_and_main_notices(self):
        self.r.scripts["WORKER"] = [msg("첫 일을 했다"), msg("직접 받은 일을 했다"), msg("이어 간 일을 했다")]
        st, d = self.post("/api/session/new", name="w1", task="첫 일", workdir=str(self.wd), mode="edits")
        self.assertEqual((st, d), (200, {"ok": True, "session": "w1"}))
        self.hub.wait_report("w1", 1)
        head, _ = self.hub.sdir("w1").instruction(1)
        self.assertEqual(head["from"], "human")
        st, d = self.post("/api/session/send", name="w1", text="이것도 해 줘")
        self.assertEqual((st, d["seq"]), (200, 2))
        h2, r2 = self.hub.wait_report("w1", 2)
        self.assertEqual(r2.strip(), "직접 받은 일을 했다")
        sent = json.dumps(self.r.requests["WORKER"][-1]["messages"], ensure_ascii=False)
        self.assertIn("[사람이 허브 화면에서 직접 보낸 지시]\\n이것도 해 줘", sent)     # 일꾼은 사람이 보낸 줄 안다
        self.assertEqual(self.hub.sdir("w1").instruction(2)[0]["from"], "human")
        wait(lambda: self.hub.sdir("w1").status().get("status") == "idle")
        st, d = self.post("/api/session/control", name="w1", action="pause")
        self.assertEqual((st, d["status"]), (200, "paused"))
        self.assertEqual(self.post("/api/session/control", name="w1", action="pause")[0], 200)   # 이미 멈춤이어도 됨
        self.assertEqual(self.post("/api/session/control", name="w1", action="resume")[1]["status"], "starting")
        self.assertEqual(self.post("/api/session/send", name="w1", text="이어서")[0], 200)
        self.hub.wait_report("w1", 3)
        st, d = self.post("/api/session/control", name="w1", action="stop")
        self.assertEqual(d["status"], "stopped")
        self.assertEqual(self.post("/api/session/send", name="w1", text="더")[0], 409)            # 끝낸 세션에는 못 보냄
        self.assertEqual(self.post("/api/session/control", name="w1", action="resume")[0], 200)   # 끝낸 세션도 다시 연다(U2-2 2b)
        self.post("/api/session/control", name="w1", action="stop")
        self.main.tick()
        notes = [m["text"] for m in self.main.ui.log if m["kind"] == "hub"]
        for want in ("사람이 화면에서 세션 w1 을 열었다", "사람이 w1 에 직접 지시 0002", "사람이 화면에서 w1 을 멈췄다",
                     "사람이 화면에서 w1 을 이어 갔다", "사람이 화면에서 w1 을 끝냈다"):
            self.assertTrue(any(want in n for n in notes), want)                         # 메인은 모르는 일이 없다

    def test_new_session_checks(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        for body, err in ((dict(name="bad name", task="x"), "fields"), (dict(name="w2", task=""), "fields"),
                          (dict(name="w2", task="x", mode="auto"), "mode"),               # 메인(edits)보다 넓음
                          (dict(name="w2", task="x", workdir=str(outside)), "workdir"),
                          (dict(name="w2", task="x", workdir="../outside"), "workdir"),
                          (dict(name="w2", task="x", worktree={"repo": str(outside), "branch": "b"}), "worktree")):
            st, d = self.post("/api/session/new", **body)
            self.assertEqual((st, d["error"]), (400, err), body)
        self.assertNotIn("w2", self.hub.registry()["sessions"])
        self.assertEqual(self.post("/api/session/control", name="main", action="stop")[1]["error"], "main")
        self.assertEqual(self.post("/api/session/send", name="nope", text="x")[0], 404)

    def test_worktree_in_project(self):
        ge = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        for c in (["git", "init", "-q", "-b", "main"], ["git", "commit", "-q", "--allow-empty", "-m", "i"]):
            subprocess.run(c, cwd=self.wd, check=True, env=ge)
        self.r.scripts["WORKER"] = [msg("worktree 에서 했다")]
        st, d = self.post("/api/session/new", name="wt", task="일", worktree={"repo": str(self.wd), "branch": "work/wt"})
        self.assertEqual(st, 200, d)
        e = self.hub.registry()["sessions"]["wt"]
        self.assertEqual(e["worktree"]["branch"], "work/wt")
        self.hub.wait_report("wt", 1)

    def test_post_checks_and_get(self):
        body = json.dumps({"name": "w9", "task": "x"})
        for path in ("/api/session/new", "/api/session/send", "/api/session/control"):
            self.assertEqual(self.req("GET", path, headers={"Cookie": self.ck})[0].status, 405, path)
            self.assertEqual(self.req("POST", path, body, {**self.good, "X-Stewart-Token": "x"})[0].status, 403, path)
            self.assertEqual(self.req("POST", path, body, {**self.good, "Origin": "http://evil"})[0].status, 403, path)
            self.assertEqual(self.req("POST", path, body, {**self.good, "Origin": "null"})[0].status, 403, path)
        self.assertNotIn("w9", self.hub.registry()["sessions"])

    def conv(self, name):
        r, d = self.req("GET", "/api/session/conversation?name=" + name, headers={"Cookie": self.ck})
        return r.status, json.loads(d)

    def test_conversation_view(self):
        self.r.scripts["WORKER"] = [msg(calls=[("write_file", {"path": "c.txt", "content": "C" * 3000})]), msg("c.txt 를 썼다"),
                                    msg("이어 간 뒤 답")]
        self.post("/api/session/new", name="w1", task="c.txt 를 써라", workdir=str(self.wd), mode="edits")
        self.hub.wait_report("w1", 1)
        wait(lambda: self.conv("w1")[1]["total"] >= 6)
        st, d = self.conv("w1")
        it = d["items"]
        self.assertEqual([i["kind"] for i in it[:6]], ["env", "user", "tool", "result", "diff", "assistant"])   # 환경 정보는 접어 따로, 고친 파일은 diff 줄(U2-5)
        self.assertTrue(it[0]["text"].startswith("[엔진이 붙인 환경 정보"))
        self.assertEqual(it[1]["text"].strip(), "[사람이 허브 화면에서 직접 보낸 지시]\nc.txt 를 써라")
        self.assertEqual(it[2]["text"], "write_file c.txt")
        self.assertTrue(it[3]["text"].startswith("썼다: c.txt (3000자"))
        self.assertEqual(it[4]["stat"], {"path": "c.txt", "plus": 1, "minus": 0})
        self.assertEqual(it[5]["text"], "c.txt 를 썼다")
        self.assertEqual([it[6]["kind"], it[7]["kind"]], ["files", "info"])       # 이번 차례에 바뀐 파일 → 차례 끝
        self.assertEqual(self.conv("w1")[1]["total"], d["total"])                # 늘어난 것이 없으면 그대로(읽은 자리 기억)
        self.assertEqual(self.req("GET", "/api/session/conversation?name=w1")[0].status, 401)
        self.assertEqual(self.conv("nope")[1], {"error": "no_session"})
        wait(lambda: self.hub.sdir("w1").status().get("status") == "idle")
        self.post("/api/session/control", name="w1", action="pause")
        self.post("/api/session/control", name="w1", action="resume")
        self.post("/api/session/send", name="w1", text="이어서")
        self.hub.wait_report("w1", 2)
        wait(lambda: any(i["text"] == "이어 간 뒤 답" for i in self.conv("w1")[1]["items"]))
        items = self.conv("w1")[1]["items"]
        texts = [i["text"] for i in items]
        self.assertTrue(any("c.txt 를 써라" in t for t in texts))                 # 이어 간 세션은 앞 대화도 보인다
        self.assertTrue(any(t.startswith("[사람이 허브 화면에서 직접 보낸 지시]") for t in texts))

    def test_main_send_through_session_box(self):
        self.r.scripts["MAIN"] = [msg("메인 답")]
        st, d = self.post("/api/session/send", name="main", text="안녕")
        self.assertEqual((st, d), (200, {"result": "started"}))
        self.assertTrue(self.main.wait_idle())


class TRegistryThreads(unittest.TestCase):
    def test_concurrent_writers_keep_registry(self):
        """웹 처리기·허브 고리·화면 스트림이 한 프로세스에서 등록부를 겹쳐 써도 비지 않는다(브라우저 확인에서 본 것)."""
        import shutil
        import tempfile
        import threading
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        hub = H.Hub(tmp / "hub", tmp / "runs")
        hub.register_main(tmp, "edits")
        reg = hub.registry()
        for i in range(5):
            reg["sessions"][f"w{i}"] = {**reg["sessions"]["main"], "name": f"w{i}", "role": "worker", "status": "paused", "pid": None}
        hub._save(reg)
        errors = []

        def work(i):
            try:
                for _ in range(80):
                    hub.refresh()
                    hub._update(f"w{i % 5}", last_event=str(i))
                    hub.tick()
            except Exception as e:   # 시험에서 오류를 모은다
                errors.append(repr(e))
        ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(hub.registry()["sessions"]), ["main", "w0", "w1", "w2", "w3", "w4"])
        self.assertEqual([p.name for p in hub.dir.glob(".sessions.json.*.tmp")], [])   # 임시 파일이 남지 않는다


if __name__ == "__main__":
    unittest.main()
