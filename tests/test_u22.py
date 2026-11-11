"""U2-2: 허브 메인 진행 칸·멈춤, 같은 명령 다시 안 묻기, 시험 명령 후보, ntfy 알림."""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from stewart import chat as C, testcmds
from stewart.config import Backend, Profile
from stewart.notify import Ntfy, NotifyError, split_topic
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_hub3 import MainBase
from tests.test_hub import msg


def resp(content=None, calls=None):
    m = {"role": "assistant", "content": content}
    if calls:
        m["tool_calls"] = [{"id": f"c{i}{time.monotonic_ns()}", "type": "function",
                            "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": m, "finish_reason": "stop"}], "usage": {}}, 0.1


class TSameCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def sess(self, answers):
        ui = FakeUI(answers)
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["run_shell"], workdir=self.tmp, max_steps=10)
        s = C.ChatSession(p, Backend("f", "http://x/v1", "m", "q", 100000), Recorder(self.tmp / "runs", "c"), ui,
                          stream=False, mode="edits")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        self.addCleanup(lambda: C.T.close_env(s.env))
        return s, ui

    def test_same_command_asked_once(self):
        s, ui = self.sess(["y", "y"])
        seq = [resp(calls=[("run_shell", {"command": "uname -s"})]), resp(calls=[("run_shell", {"command": "uname -s"})]),
               resp(calls=[("run_shell", {"command": "uname -m"})]), resp("끝")]
        with mock.patch.object(C, "chat", side_effect=seq):
            s.turn("돌려")
        self.assertEqual(ui.answers, [])                 # 물음 두 번: uname -s 한 번, uname -m 한 번
        self.assertEqual(s.st["same_cmd_skips"], 1)

    def test_denied_not_remembered_and_env_off(self):
        s, ui = self.sess(["n", "y"])
        seq = [resp(calls=[("run_shell", {"command": "uname -s"})]), resp(calls=[("run_shell", {"command": "uname -s"})]),
               resp("끝")]
        with mock.patch.object(C, "chat", side_effect=seq):
            s.turn("돌려")
        self.assertEqual(ui.answers, [])                 # 거부한 것은 다시 물었다
        with mock.patch.dict(os.environ, {"STEWART_ASK_SAME_COMMAND": "1"}):
            s2, ui2 = self.sess(["y", "y"])
        seq = [resp(calls=[("run_shell", {"command": "uname -s"})]), resp(calls=[("run_shell", {"command": "uname -s"})]),
               resp("끝")]
        with mock.patch.object(C, "chat", side_effect=seq):
            s2.turn("돌려")
        self.assertEqual(ui2.answers, [])                # 끔: 두 번 다 물었다
        self.assertNotIn("same_cmd_skips", s2.st)


class TTestCmds(unittest.TestCase):
    def test_find_and_offers(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        (d / "AGENTS.md").write_text("시험: `uv run python -m unittest -q`\n```bash\n$ PYTHONPATH=src python3 -m unittest\n```\n"
                                     "그 밖: `ls -la`, `rm -rf build`\n", encoding="utf-8")
        (d / "Makefile").write_text("check:\n\tpython3 -m unittest\n", encoding="utf-8")
        found = testcmds.find(d)
        self.assertEqual(found, ["uv run python -m unittest -q", "PYTHONPATH=src python3 -m unittest", "make check"])
        offs = {o["command"]: o for o in testcmds.offers(d)}
        self.assertEqual(offs["uv run python -m unittest -q"]["pattern"], "uv run python -m unittest *")
        self.assertEqual(offs["make check"]["pattern"], "make check *")


class Sink:
    """가짜 ntfy: 받은 요청(머리·본문)을 모은다. code 로 실패를 흉내 낸다."""

    def __init__(self, code=200):
        self.got, me = [], self
        self.code = code

        class Hd(BaseHTTPRequestHandler):
            def do_POST(self):
                me.got.append({"path": self.path, "auth": self.headers.get("Authorization"),
                               "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
                self.send_response(me.code)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Hd)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/stewart-approval"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class FakeHub:
    def __init__(self, q):
        self.q = q

    def queue(self):
        return self.q


class TNtfy(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_split_topic(self):
        self.assertEqual(split_topic("https://h.ts.net/stewart-approval"), ("https://h.ts.net/", "stewart-approval"))
        for bad in ("https://h/", "ftp://h/t", "https://h/a/b"):
            with self.assertRaises(ValueError):
                split_topic(bad)

    def test_send_with_token_file(self):
        sink = Sink()
        self.addCleanup(sink.close)
        (self.tmp / "tok").write_text("tk_SECRET123\n")
        q = [{"session": "w1", "seq": 3, "kind": "confirm",
              "args_summary": {"tool": "run_shell", "command": "make test\necho 둘째 줄"}}]
        n = Ntfy(sink.url, str(self.tmp / "tok"), "http://127.0.0.1:8765/", "proj", hub=FakeHub(q))
        n("w1: 셸 실행 확인 승인 대기")
        got = sink.got[0]
        self.assertEqual(got["path"], "/")
        self.assertEqual(got["auth"], "Bearer tk_SECRET123")
        b = got["body"]
        self.assertEqual((b["topic"], b["title"], b["priority"], b["click"]),
                         ("stewart-approval", "stewart proj · w1", 3, "http://127.0.0.1:8765/"))   # 기본 3(약한 진동)
        from stewart.notify import from_config
        self.assertEqual(from_config({"ntfy_url": sink.url, "ntfy_priority": 2}, "p").priority, 2)
        with self.assertRaises(Exception):
            from_config({"ntfy_url": sink.url, "ntfy_priority": 9}, "p")
        self.assertIn("$ make test", b["message"])
        self.assertNotIn("둘째 줄", b["message"])

    def test_failure_hides_token_and_missing_file_ok(self):
        sink = Sink(code=401)
        self.addCleanup(sink.close)
        (self.tmp / "tok").write_text("tk_SECRET123")
        errs = []
        n = Ntfy(sink.url, str(self.tmp / "tok"), hub=FakeHub([]))
        n.on_error = errs.append
        with self.assertRaises(NotifyError) as cm:
            n("w1: x 승인 대기")
        self.assertEqual(str(cm.exception), "ntfy HTTP 401")
        self.assertEqual(errs, ["ntfy HTTP 401"])
        self.assertNotIn("SECRET", str(cm.exception) + "".join(errs))
        sink.code = 200
        n2 = Ntfy(sink.url, str(self.tmp / "없는-파일"), hub=FakeHub([]))
        n2("w1: x 승인 대기")                           # 토큰 파일이 없어도 보낸다(허브는 계속)
        self.assertIsNone(sink.got[-1]["auth"])

    def test_hub_tick_once_per_ask(self):
        from stewart import hub as H
        sink = Sink()
        self.addCleanup(sink.close)
        hub = H.Hub(self.tmp / "hub", self.tmp / "runs")
        n = Ntfy(sink.url, None, hub=hub)
        hub._notify, hub._notify_on = n, True
        hub.register_main(self.tmp, "edits")
        sd = hub.sdir("main")
        H.write_json(sd.ask_dir / H.seq_name(1, "json"), {"seq": 1, "session": "main", "kind": "confirm", "prompt": "p",
                                                          "args_summary": {"tool": "run_shell", "command": "ls"}})
        hub.tick()
        hub.tick()
        self.assertEqual(len(sink.got), 1)


class TMainLive(MainBase):
    def test_live_and_stop(self):
        gate = threading.Event()

        def slow():
            gate.wait(5)
            return msg(calls=[("run_shell", {"command": "uname"})])
        self.r.scripts["MAIN"] = [slow, msg("다음")]
        self.assertEqual(self.main.send("일해"), "started")
        time.sleep(0.3)
        st = self.main.state()
        self.assertTrue(st["busy"])
        self.assertTrue(st["now"]["what"].startswith("모델 응답 기다리는 중"))   # 응답 머리 전(U2-8: 갈라 적기)
        self.assertEqual(self.main.stop_turn(), "stopping")
        self.assertTrue(self.main.state()["stopping"])
        gate.set()
        self.assertTrue(self.main.wait_idle())
        self.assertFalse(self.main.state()["stopping"])
        self.assertIn("화면에서 멈춤", " ".join(x["text"] for x in self.main.ui.log))
        self.assertEqual(self.main.stop_turn(), "idle")

    def test_role_note(self):
        self.r.scripts["MAIN"] = [msg("네")]
        self.main.send("안녕")
        self.assertTrue(self.main.wait_idle())
        self.assertIn("session_spawn 으로 작업자를 띄워 동시에", self.r.requests["MAIN"][0]["messages"][0]["content"])


class TLastMain(unittest.TestCase):
    def test_last_main(self):
        from stewart import hub as H
        from stewart.hubmain import last_main
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        hub = H.Hub(tmp / "hub", tmp / "runs")
        hub.register_main(tmp, "edits")
        self.assertEqual(last_main(hub), (None, None))                  # 처음엔 없다
        run = tmp / "runs" / "x-hub-main"
        run.mkdir(parents=True)
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "앞 대화"}]
        (run / "messages.json").write_text(json.dumps(msgs, ensure_ascii=False))
        hub.sdir("main").set_status(run_dir=str(run))
        got, prev = last_main(hub)
        self.assertEqual((got, prev), (msgs, str(run)))
        (run / "messages.json").write_text("{망가짐")
        self.assertEqual(last_main(hub), (None, None))                  # 망가지면 새로


if __name__ == "__main__":
    unittest.main()
