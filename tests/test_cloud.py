"""클라우드 시범용 기능 모의 시험: 백엔드 동시 상한, bench -j, 대기열, chat, ping·loadtest(가짜 OpenAI 서버)."""

import io
import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests import LOCAL
from unittest import mock

from stewart import backend as B, chat as C, cli, engine, probe
from stewart.batch import load_queue, run_queue
from stewart.config import Backend, Profile
from stewart.record import Recorder

from .test_engine import _resp


BACKENDS_TOML = """
default = "mac"
[backends.mac]
base_url = "http://127.0.0.1:9/v1"
model = "m"
concurrency = 1
[backends.cloud]
base_url = "{url}"
model = "fake-27b"
precision = "W4A16"
max_context = 65536
concurrency = 4
api_key_env = "STEWART_TEST_KEY"
"""


class FakeOpenAI(BaseHTTPRequestHandler):
    """/v1/models, /v1/chat/completions(일반·스트리밍, 툴콜), /metrics"""
    active = 0
    peak = 0
    lock = threading.Lock()
    seen_auth: list = []
    stop_failed = None

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        FakeOpenAI.seen_auth.append(self.headers.get("Authorization"))
        if self.path == "/v1/models":
            return self._json({"data": [{"id": "fake-27b"}]})
        if self.path == "/status":
            return self._json({"uptime_s": 10, "stop_failed": FakeOpenAI.stop_failed})
        if self.path == "/metrics":
            b = b"vllm:num_requests_running 0\nvllm:kv_cache_usage_perc 0.1\nother 1\n"
            self.send_response(200)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            return self.wfile.write(b)
        self._json({"error": "no"}, 404)

    def do_POST(self):
        FakeOpenAI.seen_auth.append(self.headers.get("Authorization"))
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with FakeOpenAI.lock:
            FakeOpenAI.active += 1
            FakeOpenAI.peak = max(FakeOpenAI.peak, FakeOpenAI.active)
        try:
            time.sleep(0.05)
            if body.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                n = min(int(body.get("max_tokens", 8)), 20)
                for i in range(n):
                    ev = {"choices": [{"delta": {"content": f"t{i} "}}]}
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(0.002)
                self.wfile.write(f"data: {json.dumps({'choices': [], 'usage': {'prompt_tokens': 30, 'completion_tokens': n}})}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                return
            if body.get("tools"):
                msg = {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "add", "arguments": '{"a": 40, "b": 2}'}}]}
            else:
                msg = {"role": "assistant", "content": "5"}
            self._json({"choices": [{"message": msg, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 12, "completion_tokens": 3}})
        finally:
            with FakeOpenAI.lock:
                FakeOpenAI.active -= 1


class TBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        FakeOpenAI.peak = FakeOpenAI.active = 0
        FakeOpenAI.seen_auth = []
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAI)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"
        bt = self.tmp / "backends.toml"
        bt.write_text(BACKENDS_TOML.format(url=self.url))
        self.env = mock.patch.dict(os.environ, {"STEWART_BACKENDS": str(bt), "STEWART_TEST_KEY": "k-test"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.srv.shutdown()
        self.srv.server_close()


class TConcurrency(TBase):
    def test_slot_limits_parallel_requests(self):
        bk = Backend("cloud2", self.url, "fake-27b", "W4A16", 4096, concurrency=2)
        ths = [threading.Thread(target=B.chat, args=(bk, [{"role": "user", "content": "x"}])) for _ in range(6)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        self.assertEqual(FakeOpenAI.peak, 2)

    def test_concurrency_loaded(self):
        from stewart.config import load_backends
        bs, _ = load_backends()
        self.assertEqual((bs["mac"].concurrency, bs["cloud"].concurrency), (1, 4))


class TProbe(TBase):
    def test_ping(self):
        from stewart.config import load_backends
        bk = load_backends()[0]["cloud"]
        r = probe.ping(bk)
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["checks"][0]["model_listed"])
        self.assertEqual(r["checks"][2]["name"], "add")
        self.assertIn("Bearer k-test", FakeOpenAI.seen_auth)
        self.assertNotIn("k-test", json.dumps(r))  # 키 값은 결과에 없다

    def test_loadtest(self):
        from stewart.config import load_backends
        bk = load_backends()[0]["cloud"]
        with mock.patch("sys.stdout", io.StringIO()):
            r = probe.loadtest(bk, [1, 3], 10, 1, 0, self.tmp / "runs")
        l1, l3 = r["levels"]
        self.assertEqual((l1["ok"], l3["ok"]), (1, 3))
        self.assertEqual(l3["completion_tokens"], 30)
        self.assertEqual(FakeOpenAI.peak, 3)   # 측정은 백엔드 상한과 무관하게 한꺼번에
        self.assertTrue((Path(r["dir"]) / "loadtest.md").exists())
        self.assertTrue(any("kv_cache_usage_perc" in l for l in r["metrics_after"]))


class TPodStatus(TBase):
    def run_ps(self):
        port = str(self.srv.server_address[1])
        with mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()) as err:
            rc = cli.main(["podstatus", "-b", "cloud", "--port", port, "--runs-dir", str(self.tmp / "runs")])
        return rc, err.getvalue()

    def test_ok_and_stop_failed_warning(self):
        rc, err = self.run_ps()
        self.assertEqual(rc, 0)
        self.assertNotIn("스스로 멈추지 못했다", err)
        FakeOpenAI.stop_failed = {"reason": "idle 30m", "count": 2, "last_error": "api HTTPError 401", "last_ts": "t"}
        try:
            rc, err = self.run_ps()
        finally:
            FakeOpenAI.stop_failed = None
        self.assertEqual(rc, 3)
        self.assertIn("스스로 멈추지 못했다", err)
        self.assertIn("실패 2회", err)
        self.assertEqual(len((self.tmp / "runs" / "pod-status.jsonl").read_text().splitlines()), 2)


class TBenchParallel(TBase):
    def test_bench_j(self):
        prof = self.tmp / "p.toml"
        prof.write_text('name = "pj"\ntools = ["read_file"]\nworkdir = "."\nverify_command = "test -f ok.txt"\n'
                        'language = "ko"\nsystem = "s"\n')
        task = self.tmp / "task.md"
        task.write_text("해라")
        seen = []

        def fake_chat(backend, messages, tools=None, rf=None):
            seen.append(messages[1]["content"].split("작업 폴더: ")[1].split("\n")[0])   # 환경 블록은 첫 사람 입력 앞(다이어트 안 a)
            return _resp(content="다 했다")

        with mock.patch.object(engine, "chat", side_effect=fake_chat), mock.patch("sys.stdout", io.StringIO()), \
                mock.patch("sys.stderr", io.StringIO()):
            rc = cli.main(["bench", "-p", str(prof), "-t", str(task), "-n", "4", "-j", "4", "--backend", "cloud",
                           "--workdir", str(self.tmp / "r{i}"), "--setup", f"mkdir -p {self.tmp}/r{{i}} && touch {self.tmp}/r{{i}}/ok.txt",
                           "--runs-dir", str(self.tmp / "runs")])
        self.assertEqual(rc, 0)
        bj = json.loads(next((self.tmp / "runs").glob("bench-*/bench.json")).read_text())
        self.assertEqual((bj["success"], bj["n"], bj["jobs"], bj["backend_concurrency"]), (4, 4, 4, 4))
        self.assertEqual(sorted(seen), sorted(str((self.tmp / f"r{i}").resolve()) for i in range(1, 5)))
        self.assertEqual([r["run"] for r in bj["runs"]], [1, 2, 3, 4])

    def test_bench_j_needs_template(self):
        prof = self.tmp / "p.toml"
        prof.write_text('name = "pj"\ntools = []\nworkdir = "."\nsystem = "s"\n')
        with mock.patch("sys.stderr", io.StringIO()) as err:
            rc = cli.main(["bench", "-p", str(prof), "--task", "x", "-n", "2", "-j", "2", "--runs-dir", str(self.tmp / "runs")])
        self.assertEqual(rc, 2)
        self.assertIn("{i}", err.getvalue())


class TQueue(TBase):
    def test_queue_limits_and_records(self):
        q = self.tmp / "q.toml"
        q.write_text("""
[[job]]
name = "a"
args = ["backends"]
repeat = 2
[[job]]
name = "c"
args = ["ping", "-b", "cloud"]
slots = 2
""")
        jobs = load_queue(q)
        self.assertEqual([j.name for j in jobs], ["a-1", "a-2", "c"])
        self.assertEqual((jobs[0].backend, jobs[0].args), ("mac", ["backends"]))  # 기본 백엔드, 인자는 그대로
        with mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            rc = run_queue(q, self.tmp / "runs", poll_s=0.05)
        qj = json.loads(next((self.tmp / "runs").glob("queue-*/queue.json")).read_text())
        self.assertEqual(rc, 0)
        self.assertEqual(qj["ok"], 3)
        self.assertTrue(any("loadtest" in x or "ping" in x for x in json.dumps(qj["rows"]).split(",")))
        a1, a2 = [r for r in qj["rows"] if r["name"].startswith("a-")]
        self.assertLessEqual(a1["ended"], a2["started"])  # mac 상한 1 → 겹치지 않음
        self.assertTrue((next((self.tmp / "runs").glob("queue-*")) / "queue.md").exists())

    def test_slots_from_args(self):
        q = self.tmp / "q.toml"
        q.write_text('[[job]]\nargs = ["bench", "-j", "8", "--backend", "cloud"]\n')
        j = load_queue(q)[0]
        self.assertEqual((j.backend, j.slots, j.name), ("cloud", 8, "bench"))
        q.write_text('[[job]]\nargs = ["ping", "-b", "cloud"]\n')
        self.assertEqual(load_queue(q)[0].backend, "cloud")


class FakeUI(C.UI):
    def __init__(self, answers=()):
        super().__init__(out=io.StringIO(), color=False)
        self.answers = list(answers)

    def ask(self, prompt):
        if not self.answers:
            raise EOFError
        a = self.answers.pop(0)
        if isinstance(a, BaseException):
            raise a
        return a


class TChat(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a.py").write_text("x = 1\ny = 2\n")
        self.bk = Backend("fake", "http://x/v1", "m", "q", 1000)

    def sess(self, ui, confirm=False):
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.tmp, max_steps=5)
        s = C.ChatSession(p, self.bk, Recorder(self.tmp / "runs", "chat-t"), ui, confirm=confirm)
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        return s

    def test_turns_diff_and_record(self):
        ui = FakeUI()
        s = self.sess(ui)
        seq = [_resp(calls=[("edit_file", '{"path": "a.py", "old": "y = 2", "new": "y = 3"}')]),
               _resp(content="y를 3으로 바꿨다."),
               _resp(content="두 번째 차례 답.")]
        with mock.patch.object(C, "chat", side_effect=seq) as m:
            self.assertEqual(s.turn("y를 3으로"), "answered")
            self.assertEqual(s.turn("고마워"), "answered")
            # 둘째 차례 요청에 첫 차례 대화가 들어 있다(대화 이어짐)
            last_msgs = m.call_args_list[-1][0][1]
        self.assertEqual([x["role"] for x in last_msgs], ["system", "user", "assistant", "tool", "assistant", "user"])
        out = ui.out.getvalue()
        self.assertIn("-y = 2", out)
        self.assertIn("+y = 3", out)
        self.assertEqual((self.tmp / "a.py").read_text(), "x = 1\ny = 3\n")
        summ = s.close()
        self.assertEqual((summ["turns"], summ["steps"]), (2, 3))
        self.assertEqual(summ["files_changed"], [str(self.tmp / "a.py")])
        kinds = [json.loads(l)["kind"] for l in (s.rec.dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual(kinds.count("user"), 2)
        self.assertIn("diff", (s.rec.dir / "events.jsonl").read_text())
        self.assertIn("## 사람 (차례 2)", (s.rec.dir / "transcript.md").read_text())

    def test_interrupt_keeps_conversation(self):
        ui = FakeUI()
        s = self.sess(ui)
        seq = [_resp(calls=[("read_file", '{"path": "a.py"}')]), KeyboardInterrupt(), _resp(content="이어서 답.")]
        with mock.patch.object(C, "chat", side_effect=seq) as m:
            self.assertEqual(s.turn("읽어"), "interrupted")
            self.assertEqual(s.turn("계속"), "answered")
            msgs = m.call_args_list[-1][0][1]
        roles = [x["role"] for x in msgs]
        # 모든 tool_calls 에 tool 결과가 짝지어져 있어야 한다
        ids = [c["id"] for x in msgs if x.get("tool_calls") for c in x["tool_calls"]]
        self.assertEqual(ids, [x["tool_call_id"] for x in msgs if x["role"] == "tool"])
        self.assertEqual(roles[-1], "user")
        self.assertIn("중단", ui.out.getvalue())

    def test_interrupt_during_tool_fills_results(self):
        ui = FakeUI()
        s = self.sess(ui)
        s.messages.append({"role": "user", "content": "x"})
        s.messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": "z1", "type": "function", "function": {"name": "run_shell", "arguments": "{}"}}]})
        s._close_open_calls("중단")
        self.assertEqual(s.messages[-1], {"role": "tool", "tool_call_id": "z1", "name": "run_shell", "content": "중단"})

    def test_confirm_deny_with_message(self):
        ui = FakeUI(answers=["그 파일 말고 b.py를 만들어"])
        s = self.sess(ui, confirm=True)
        seq = [_resp(calls=[("write_file", '{"path": "a.py", "content": "z"}')]), _resp(content="알겠다.")]
        with mock.patch.object(C, "chat", side_effect=seq) as m:
            s.turn("덮어써")
            tool_msg = [x for x in m.call_args_list[-1][0][1] if x["role"] == "tool"][0]
        self.assertIn("b.py를 만들어", tool_msg["content"])
        self.assertEqual((self.tmp / "a.py").read_text(), "x = 1\ny = 2\n")
        self.assertEqual(s.st["denied"], 1)

    def test_compact_and_repl(self):
        ui = FakeUI(answers=["/status", "안녕", "/compact", "/exit"])
        s = self.sess(ui)
        s.messages += [{"role": "tool", "tool_call_id": f"t{i}", "content": "x" * 1000} for i in range(12)]
        with mock.patch.object(C, "chat", side_effect=[_resp(content="안녕하다.")]):
            summ = C.repl(s)
        self.assertEqual(summ["turns"], 1)
        self.assertGreater(s.st["compactions"], 0)
        self.assertTrue((s.rec.dir / "summary.json").exists())

    def test_multiline_input(self):
        ui = FakeUI(answers=['"""', "첫 줄", "둘째 줄", '"""'])
        self.assertEqual(C.read_input(ui), "첫 줄\n둘째 줄")
        ui = FakeUI(answers=["가\\", "나"])
        self.assertEqual(C.read_input(ui), "가\n나")


class TCloudDir(unittest.TestCase):
    """기록 폴더 기본값은 집 경로가 아니라 환경 변수 → hub.toml [cloud] dir → XDG 캐시."""
    def test_order(self):
        from stewart import cloud
        with tempfile.TemporaryDirectory() as d:
            hub = Path(d) / "hub.toml"
            env = {k: v for k, v in os.environ.items() if k not in ("STEWART_CLOUD_DIR",)}
            env.update(STEWART_HUB_CONFIG=str(hub), XDG_CACHE_HOME=str(Path(d) / "c"))
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(cloud.cloud_dir(), Path(d) / "c" / "stewart-cloud")
                hub.write_text('[cloud]\ndir = "%s/x"\n' % d, encoding="utf-8")
                self.assertEqual(cloud.cloud_dir(), Path(d) / "x")
                os.environ["STEWART_CLOUD_DIR"] = str(Path(d) / "e")
                self.assertEqual(cloud.cloud_dir(), Path(d) / "e")


if __name__ == "__main__":
    unittest.main()
