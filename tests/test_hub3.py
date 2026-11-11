"""허브 H3: 메인 세션을 허브에 붙이기·웹 화면 보안·[hub] 설정·worktree 자리."""

import http.client
import json
import os
import re
import stat
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

from stewart import hub as H
from stewart.cli import resolve
from stewart.config import ConfigError
from stewart.hubmain import HubMain, hub_config
from stewart.record import Recorder
from stewart.web import WebApp, make_token
from tests.test_hub import Base, msg, reply, wait


class Router:
    """시스템 지시 첫 낱말로 메인(MAIN)·작업자(WORKER) 대본을 가른다."""

    def __init__(self):
        self.scripts = {"MAIN": [], "WORKER": []}
        self.requests = {"MAIN": [], "WORKER": []}
        me = self

        class Hd(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                who = "MAIN" if body["messages"][0]["content"].startswith("MAIN") else "WORKER"
                me.requests[who].append(body)
                item = me.scripts[who].pop(0) if me.scripts[who] else msg("(대본 끝)")
                reply(self, body, item() if callable(item) else item)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Hd)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"


class MainBase(Base):
    def setUp(self):
        super().setUp()
        self.r = Router()
        self.addCleanup(self.r.srv.server_close)
        self.addCleanup(self.r.srv.shutdown)
        (self.tmp / "backends.toml").write_text(
            f'default = "fake"\n[backends.fake]\nbase_url = "{self.r.url}"\nmodel = "m"\nmax_context = 32768\n'
            'enabled = true\ntimeout_s = 30\n', encoding="utf-8")
        (self.tmp / "main.toml").write_text('name = "main"\ntools = ["read_file", "write_file", "run_shell"]\n'
                                            'max_steps = 10\nsystem = "MAIN 지시"\n', encoding="utf-8")
        (self.tmp / "w.toml").write_text('name = "w"\ntools = ["read_file", "write_file", "run_shell"]\n'
                                         'max_steps = 10\nsystem = "WORKER 지시"\n', encoding="utf-8")
        profile, backend = resolve(str(self.tmp / "main.toml"), "fake", str(self.wd))
        self.main = HubMain(self.hub, profile, backend, Recorder(self.tmp / "runs", "hub-main"), mode="edits",
                            worker_profile=str(self.tmp / "w.toml"), worker_backend="fake")
        self.addCleanup(self.main.close)


class TMainAttached(MainBase):
    def test_main_spawns_worker_and_gets_report_notice(self):
        self.r.scripts["MAIN"] = [msg(calls=[("session_spawn", {"name": "w1", "task": "a.txt 를 만들고 보고하라",
                                                                "workdir": str(self.wd)})]),
                                  msg("작업자를 띄웠다"), msg("보고를 봤다")]
        self.r.scripts["WORKER"] = [msg(calls=[("write_file", {"path": "a.txt", "content": "A"})]), msg("a.txt 를 만들었다")]
        self.assertEqual(self.main.send("일을 나눠라"), "started")
        self.assertTrue(self.main.wait_idle())
        tool_msgs = [m for m in self.r.requests["MAIN"][1]["messages"] if m["role"] == "tool"]
        self.assertIn("세션 w1 을 띄웠다", tool_msgs[-1]["content"])
        self.hub.wait_report("w1", 1)
        self.main.tick()
        self.assertTrue(any(m["kind"] == "hub" and "w1 보고 0001 도착" in m["text"] for m in self.main.ui.log))
        self.main.send("어떻게 됐나")
        self.assertTrue(self.main.wait_idle())
        sent = json.dumps(self.r.requests["MAIN"][-1]["messages"], ensure_ascii=False)
        self.assertIn("[허브] w1 보고 0001 도착", sent)                      # 다음 차례에 끼워 넣음
        names = [d["function"]["name"] for d in self.r.requests["MAIN"][0]["tools"]]
        self.assertIn("session_spawn", names)
        self.assertFalse([n for n in names if "answer" in n or "approv" in n])
        st = self.main.state()
        self.assertEqual({s["name"] for s in st["sessions"]}, {"main", "w1"})

    def test_main_ask_goes_to_queue_and_memory_answer(self):
        self.r.scripts["MAIN"] = [msg(calls=[("run_shell", {"command": "touch m.txt"})]), msg("돌렸다")]
        self.main.send("셸을 돌려라")
        q = wait(lambda: self.hub.queue())
        self.assertEqual((q[0]["session"], q[0]["kind"], q[0]["args_summary"]["command"]), ("main", "confirm", "touch m.txt"))
        H.write_json(self.hub.sdir("main").answer_dir / "0001.json", {"seq": 1, "answer": "y"})   # 파일은 판정 근거가 아니다
        time.sleep(0.5)
        self.assertTrue(self.main.busy)
        self.assertFalse((self.wd / "m.txt").exists())
        self.hub.answer("main", 1, "y", via="web")
        self.assertTrue(self.main.wait_idle())
        self.assertTrue((self.wd / "m.txt").exists())

    def test_send_while_busy_is_interjected(self):
        gate = threading.Event()

        def slow():
            gate.wait(5)
            return msg(calls=[("read_file", {"path": "x.txt"})])
        self.r.scripts["MAIN"] = [slow, msg("끝")]
        self.main.send("첫 말")
        time.sleep(0.2)
        self.assertEqual(self.main.send("덧붙이는 말"), "queued")
        gate.set()
        self.assertTrue(self.main.wait_idle())
        self.assertIn("덧붙이는 말", json.dumps(self.r.requests["MAIN"][1]["messages"], ensure_ascii=False))


class TMainCommands(MainBase):
    def setUp(self):
        super().setUp()
        cmd = self.wd / ".claude" / "commands"
        cmd.mkdir(parents=True)
        (cmd / "deploy-plan.md").write_text("계획을 세워라: $ARGUMENTS", encoding="utf-8")
        (cmd / "mode.md").write_text("내장과 겹친다", encoding="utf-8")
        self.main.close()
        profile, backend = resolve(str(self.tmp / "main.toml"), "fake", str(self.wd))
        self.hub = H.Hub(self.tmp / "hub-c", self.tmp / "runs")
        self.main = HubMain(self.hub, profile, backend, Recorder(self.tmp / "runs", "hub-main"), mode="edits")
        self.addCleanup(self.main.close)

    def test_custom_expanded_builtin_ignored(self):
        self.r.scripts["MAIN"] = [msg("세웠다")]
        self.assertEqual(self.main.send("/deploy-plan 배포"), "started")
        self.assertTrue(self.main.wait_idle())
        sent = json.dumps(self.r.requests["MAIN"][-1]["messages"], ensure_ascii=False)
        self.assertIn("계획을 세워라: 배포", sent)
        self.assertEqual(self.main.send("/mode auto"), "builtin_ignored")
        self.assertEqual(len(self.r.requests["MAIN"]), 1)


class TWeb(MainBase):
    def setUp(self):
        super().setUp()
        self.token = make_token(self.hub.dir)
        self.web = WebApp(self.main, self.hub, self.token)
        self.web.serve()
        self.addCleanup(self.web.close)
        self.host = f"127.0.0.1:{self.web.port}"
        self.origin = f"http://{self.host}"

    def req(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=10)
        h = {"Host": host or self.host, **(headers or {})}
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def login(self):
        r, _ = self.req("POST", "/login", urlencode({"token": self.token}),
                        {"Content-Type": "application/x-www-form-urlencoded", "Origin": self.origin})
        self.assertEqual(r.status, 303)
        cookie = r.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        ck = cookie.split(";")[0]
        r, page = self.req("GET", "/", headers={"Cookie": ck})
        self.assertEqual(r.status, 200)
        csrf = re.search(rb'name="csrf" content="([^"]+)"', page).group(1).decode()
        return ck, csrf

    def test_token_file_mode(self):
        self.assertEqual(stat.S_IMODE(os.stat(self.hub.dir / "hub-token").st_mode), 0o600)

    def test_loopback_only(self):
        with self.assertRaises(ValueError):
            WebApp(self.main, self.hub, self.token, host="0.0.0.0")

    def test_no_login_and_bad_host(self):
        r, _ = self.req("GET", "/")
        self.assertEqual((r.status, r.getheader("Location")), (303, "/login"))
        self.assertEqual(self.req("GET", "/api/state")[0].status, 401)
        for host in ("evil.example:80", f"evil.example:{self.web.port}", "127.0.0.1:1"):
            self.assertEqual(self.req("GET", "/login", host=host)[0].status, 421, host)
        r, _ = self.req("POST", "/login", urlencode({"token": "wrong"}),
                        {"Content-Type": "application/x-www-form-urlencoded", "Origin": self.origin})
        self.assertEqual(r.status, 403)
        r, _ = self.req("POST", "/login", urlencode({"token": self.token}), {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(r.status, 403)                                       # Origin 없음
        ck, _ = self.login()
        r, _ = self.req("GET", "/api/state", headers={"Cookie": ck}, host="evil.example:80")
        self.assertEqual(r.status, 421)                                       # 쿠키가 있어도 Host 가 다르면 거부

    def test_answer_needs_post_token_origin(self):
        self.r.scripts["MAIN"] = [msg(calls=[("run_shell", {"command": "touch w.txt"})]), msg("돌렸다")]
        self.main.send("셸")
        wait(lambda: self.hub.queue())
        ck, csrf = self.login()
        body = json.dumps({"session": "main", "seq": 1, "answer": "y"})
        good = {"Cookie": ck, "Origin": self.origin, "X-Stewart-Token": csrf, "Content-Type": "application/json"}
        with mock.patch.object(self.hub, "answer", wraps=self.hub.answer) as ans:
            self.assertEqual(self.req("GET", "/api/answer?" + urlencode({"session": "main", "seq": 1, "answer": "y"}),
                                      headers={"Cookie": ck})[0].status, 405)
            self.assertEqual(self.req("PUT", "/api/answer", body, good)[0].status, 405)
            self.assertEqual(self.req("POST", "/api/answer", body, {**good, "X-Stewart-Token": ""})[0].status, 403)
            self.assertEqual(self.req("POST", "/api/answer", body, {**good, "X-Stewart-Token": "x" * 32})[0].status, 403)
            self.assertEqual(self.req("POST", "/api/answer", body, {**good, "Origin": "http://evil.example"})[0].status, 403)
            self.assertEqual(self.req("POST", "/api/answer", body, {k: v for k, v in good.items() if k != "Origin"})[0].status, 403)
            self.assertEqual(self.req("POST", "/api/answer", body, {k: v for k, v in good.items() if k != "Cookie"})[0].status, 401)
            self.assertEqual(ans.call_count, 0)
            self.assertFalse((self.wd / "w.txt").exists())
            r, data = self.req("POST", "/api/answer", json.dumps({"session": "main", "seq": 7, "answer": "y"}), good)
            self.assertEqual(r.status, 409)                                   # 기다리지 않는 질문
            r, data = self.req("POST", "/api/answer", body, good)
            self.assertEqual((r.status, json.loads(data)), (200, {"ok": True}))
            self.assertEqual(ans.call_args.kwargs, {"by": "human", "via": "web"})
        self.assertTrue(self.main.wait_idle())
        self.assertTrue((self.wd / "w.txt").exists())
        self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))

    def test_state_and_main_post(self):
        ck, csrf = self.login()
        self.r.scripts["MAIN"] = [msg("안녕하세요")]
        r, data = self.req("POST", "/api/main", json.dumps({"text": "안녕"}),
                           {"Cookie": ck, "Origin": self.origin, "X-Stewart-Token": csrf, "Content-Type": "application/json"})
        self.assertEqual(json.loads(data), {"result": "started"})
        self.assertTrue(self.main.wait_idle())
        r, data = self.req("GET", "/api/state", headers={"Cookie": ck})
        st = json.loads(data)
        self.assertTrue(any(m["kind"] == "assistant" and m["text"] == "안녕하세요" for m in st["main_log"]))
        self.assertTrue(self.r.requests["MAIN"][-1].get("stream"))           # 메인도 스트리밍(답 글은 기록에 그대로)
        steps = [json.loads(l) for l in (self.main.sess.rec.dir / "events.jsonl").read_text().splitlines() if '"kind": "step"' in l]
        self.assertIsInstance(steps[-1]["response"]["ttft_s"], float)
        self.assertEqual(st["sessions"][0]["name"], "main")
        r, data = self.req("GET", "/api/session?name=main", headers={"Cookie": ck})
        self.assertIn("events", json.loads(data))
        self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))
        self.assertEqual(r.getheader("X-Frame-Options"), "DENY")
        self.assertEqual(r.getheader("Referrer-Policy"), "same-origin")       # no-referrer 면 폼 POST 의 Origin 이 null
        r, _ = self.req("GET", "/login")
        self.assertEqual(r.getheader("Referrer-Policy"), "same-origin")
        r, _ = self.req("POST", "/login", urlencode({"token": self.token}),
                        {"Content-Type": "application/x-www-form-urlencoded", "Origin": "null"})
        self.assertEqual(r.status, 403)                                       # Origin null 은 계속 거부


class TOnlyWebAnswers(unittest.TestCase):
    def test_only_web_handler_calls_hub_answer(self):
        src = Path(H.__file__).parent
        hits = []
        for p in sorted(src.rglob("*.py")):
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"\.answer\(", line) and not line.lstrip().startswith("#"):
                    hits.append((p.name, line.strip()))
        self.assertEqual(hits, [("web.py", 'self.hub.answer(name, seq, text, by="human", via="web")')])


class TConfig(unittest.TestCase):
    def setUp(self):
        import tempfile, shutil
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_worktree_dir_policy(self):
        root = self.tmp / "root"
        (root / "repos" / "proj").mkdir(parents=True)
        (root / "labs").mkdir()
        hub = H.Hub(self.tmp / "hub", self.tmp / "runs")
        self.assertEqual(hub.worktree_dir((root / "repos" / "proj")), root / "labs" / "proj" / "worktrees")
        other = self.tmp / "elsewhere" / "proj2"
        other.mkdir(parents=True)
        self.assertEqual(hub.worktree_dir(other), hub.dir / "worktrees")
        hub2 = H.Hub(self.tmp / "hub2", self.tmp / "runs", worktree_root=str(self.tmp / "wt" / "{repo}"))
        self.assertEqual(hub2.worktree_dir(other), (self.tmp / "wt" / "proj2").resolve())

    def test_hub_config(self):
        p = self.tmp / "hub.toml"
        self.assertEqual(hub_config(str(p)), {})
        p.write_text('[hub]\nnotify_url = "https://ntfy.example/t"\nworktree_root = "~/w/{repo}"\nport = 9000\n')
        self.assertEqual(hub_config(str(p))["port"], 9000)
        p.write_text('[hub]\nnotify = "x"\n')
        with self.assertRaises(ConfigError):
            hub_config(str(p))


if __name__ == "__main__":
    unittest.main()
