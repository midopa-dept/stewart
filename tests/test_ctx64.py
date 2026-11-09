"""2): 실제 문맥 길이(서버 max_model_len), 접기 판단에 응답 상한, scan_read 토큰 어림, 넘침 뒤 한 번 접고 다시."""

import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from stewart import backend as B, chat as C, engine, tools as T
from stewart.backend import BackendError
from stewart.compact import Compactor, est_tokens, reply_reserve
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_compact import _r, convo


def models_server(payload):
    """GET /v1/models 만 답하는 가짜 서버. payload None 이면 404."""
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            if payload is None or self.path != "/v1/models":
                self.send_response(404)
                self.end_headers()
                return
            b = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1", seen


def overflow():
    return BackendError("HTTP 400", 400, json.dumps({"object": "error", "message":
        "This model's maximum context length is 65536 tokens. However, you requested 70000 tokens."}))


class TServedContext(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"STEWART_SERVED_CONTEXT": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def serve(self, payload):
        srv, url, seen = models_server(payload)
        self.addCleanup(srv.shutdown)
        return url, seen

    def test_server_value_wins_and_warns(self):
        url, seen = self.serve({"data": [{"id": "other", "max_model_len": 4096},
                                         {"id": "qwen3.8-27b", "max_model_len": 65536}]})
        bk = Backend("cloud", url, "qwen3.8-27b", "w4a16", 150000)
        c = B.sync_context(bk)
        self.assertEqual((c["configured"], c["served"], c["used"], c["source"]), (150000, 65536, 65536, "server"))
        self.assertEqual(bk.max_context, 65536)
        self.assertIn("65,536", c["warn"])
        self.assertIn("150,000", c["warn"])
        self.assertEqual(B.sync_context(bk)["used"], 65536)               # 두 번째는 묻지 않는다
        self.assertEqual(len(seen), 1)
        self.assertEqual((bk.public()["configured_context"], bk.public()["context_source"]), (150000, "server"))

    def test_same_value_no_warn_single_entry(self):
        url, _ = self.serve({"data": [{"id": "served-name", "max_model_len": 32768}]})   # id 가 달라도 하나뿐이면 그것
        bk = Backend("b", url, "m", "q", 32768)
        c = B.sync_context(bk)
        self.assertEqual((c["used"], c["source"], c["warn"]), (32768, "server", ""))

    def test_fallbacks_keep_backends(self):
        for payload in (None, {"data": [{"id": "m"}]}, {"data": [{"id": "a", "max_model_len": 1}, {"id": "b", "max_model_len": 2}]},
                        {"data": [{"id": "m", "max_model_len": "65536"}]}, {"x": 1}):
            url, _ = self.serve(payload)
            bk = Backend("b", url, "m", "q", 150000)
            c = B.sync_context(bk)
            self.assertEqual((c["used"], c["source"], c["served"], c["warn"]), (150000, "backends", None, ""), payload)
        bk = Backend("b", "http://127.0.0.1:9/v1", "m", "q", 8192)            # 닿지 않음
        self.assertEqual(B.sync_context(bk, timeout=1)["used"], 8192)

    def test_env_off_does_not_ask(self):
        url, seen = self.serve({"data": [{"id": "m", "max_model_len": 1000}]})
        with mock.patch.dict(os.environ, {"STEWART_SERVED_CONTEXT": "0"}):
            c = B.sync_context(Backend("b", url, "m", "q", 150000))
        self.assertEqual((c["used"], seen), (150000, []))

    def test_engine_and_chat_use_server_value(self):
        url, _ = self.serve({"data": [{"id": "m", "max_model_len": 65536}]})
        tmp = Path(tempfile.mkdtemp())
        bk = Backend("cloud", url, "m", "q", 150000)
        p = Profile(name="t", path=tmp / "p.toml", system="s", tools=["*"], workdir=tmp, max_steps=3)
        rec = Recorder(tmp / "runs", "t")
        with mock.patch.object(engine, "chat", side_effect=[_r("끝", prompt=10)]):
            s = engine.run(p, bk, "do", rec)
        self.assertEqual(s["context"]["used"], 65536)
        start = json.loads((rec.dir / "events.jsonl").read_text().splitlines()[0])
        self.assertEqual(start["meta"]["context"]["used"], 65536)
        self.assertEqual(start["meta"]["backend"]["max_context"], 65536)
        ui = FakeUI()
        bk2 = Backend("cloud", url, "m", "q", 150000)
        sess = C.ChatSession(p, bk2, Recorder(tmp / "runs", "chat-t"), ui)
        self.addCleanup(lambda: sess.rec._ev.closed or sess.rec._ev.close())
        self.assertEqual((sess.comp.full_context, sess.ctx["used"]), (65536, 65536))
        with mock.patch.object(ui, "read_line", side_effect=EOFError):
            C.repl(sess)
        self.assertIn("max_model_len 65,536", ui.out.getvalue())


class TReserve(unittest.TestCase):
    def test_window_is_context_minus_max_tokens(self):
        c = Compactor(65536, ratio=0.55, reserve=8192)
        self.assertEqual((c.full_context, c.max_context), (65536, 65536 - 8192))
        self.assertAlmostEqual(c.threshold(), 0.55 * (65536 - 8192))
        self.assertEqual(Compactor(10000, reserve=9000).max_context, 5000)       # 응답 상한은 절반까지만
        self.assertEqual(Compactor(10000, reserve=0).max_context, 10000)

    def test_reply_reserve_source(self):
        p = Profile(name="t", path=Path("p"), system="s", tools=[], workdir=Path("."), max_tokens=4096)
        bk = Backend("b", "http://x/v1", "m", "q", 1000, params={"max_tokens": 8192})
        self.assertEqual(reply_reserve(p, bk), 4096)                              # 프로필이 먼저
        p.max_tokens = None
        self.assertEqual(reply_reserve(p, bk), 8192)                              # 없으면 백엔드 params
        self.assertEqual(reply_reserve(p, Backend("b", "http://x/v1", "m", "q", 1000)), 0)

    def test_engine_folds_earlier_with_max_tokens(self):
        """프롬프트 6,000 tok: 창 10,000 이면 문턱(0.7) 아래, max_tokens 4,000 을 빼면 문턱(0.7×6,000=4,200) 위 → 접는다."""
        out = {}
        for mt in (None, 4000):
            tmp = Path(tempfile.mkdtemp())
            p = Profile(name="t", path=tmp / "p.toml", system="s", tools=["*"], workdir=tmp, max_steps=10, max_tokens=mt)
            bk = Backend("fake", "http://x/v1", "m", "q", 10000)
            seq = [_r(calls=[("read_file", json.dumps({"path": f"f{i}.txt"}))], prompt=6000 + i) for i in range(6)] \
                + [_r("끝", prompt=6300)]
            for i in range(6):
                (tmp / f"f{i}.txt").write_text("x" * 2000)
            with mock.patch.object(engine, "chat", side_effect=seq):
                out[mt] = engine.run(p, bk, "do", Recorder(tmp / "runs", "t"))
        self.assertEqual(out[None]["compactions"], 0)
        self.assertEqual(out[None]["stop_reason"], "completed")
        self.assertEqual(out[4000]["stop_reason"], "completed")
        self.assertGreaterEqual(out[4000]["compactions"], 1)
        self.assertEqual(out[4000]["compact_log"][0]["before_tokens"] >= 0.7 * 6000, True)


class TScanTokens(unittest.TestCase):
    def test_est_tokens(self):
        self.assertEqual(est_tokens(""), 0)
        self.assertEqual(est_tokens("1234567890"), 10)                            # 숫자는 한 자리씩
        self.assertEqual(est_tokens("가나다라마"), 6)                              # 1.2 × 5
        self.assertGreater(est_tokens("2024-03-01\t1.2 GB"), len("2024-03-01\t1.2 GB") / 3)

    def test_scan_read_cut_by_tokens(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        root = tmp / "root"
        for i in range(400):                                                      # 한글 이름: 글자보다 토큰이 먼저 찬다
            p = root / "가족사진" / f"한글로된아주긴사진파일이름입니다번호{i:04d}.jpg"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("v")
        env = T.ToolEnv(root, artifacts_dir=tmp / "runs")
        T.folder_scan(env)
        ok, out = T.scan_read(env, limit=500, type="file")
        self.assertTrue(ok)
        body = out.split("\n", 2)[2].rsplit("\n[바깥 자료 끝]", 1)[0]
        self.assertLess(len(body), T.SCAN_READ_CHARS)                             # 글자 상한 전에
        self.assertIn("토큰 어림 상한 4,000에서 끊음", out)
        main = body.split("\n…(더 있다")[0]
        self.assertLessEqual(est_tokens(main), T.SCAN_READ_TOKENS + 400)
        nxt = int(out.split("더 있다: offset=")[1].split(" ")[0].split(")")[0])
        names = [ln for ln in main.splitlines() if ln.startswith("  ")]
        self.assertEqual(nxt, len(names))


class TOverflowRetry(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.p = Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.tmp, max_steps=8)
        for i in range(4):
            (self.tmp / f"f{i}.txt").write_text("z" * 4000)

    def calls(self, n):
        return [_r(calls=[("read_file", json.dumps({"path": f"f{i}.txt"}))], prompt=1000 + i) for i in range(n)]

    def test_engine_folds_once_and_retries_same_step(self):
        seq = self.calls(4) + [overflow(), _r("끝", prompt=2000)]
        sent = []

        def fake(backend, messages, *a, **k):
            sent.append(json.loads(json.dumps(messages)))
            r = seq[len(sent) - 1]
            if isinstance(r, Exception):
                raise r
            return r

        rec = Recorder(self.tmp / "runs", "t")
        with mock.patch.object(engine, "chat", side_effect=fake):
            s = engine.run(self.p, Backend("fake", "http://x/v1", "m", "q", 150000), "do", rec)
        self.assertEqual(s["stop_reason"], "completed")
        self.assertEqual((s["steps"], s["overflow_retries"], s["compactions"]), (5, 1, 1))
        self.assertEqual(len(sent[4]), len(sent[5]))                              # 같은 단계(메시지 수 그대로)를 줄여서 다시
        self.assertLess(len(json.dumps(sent[5], ensure_ascii=False)), len(json.dumps(sent[4], ensure_ascii=False)))
        last_tool = [m for m in sent[5] if m["role"] == "tool"][-1]
        self.assertEqual(last_tool["content"].count("z"), 4000)                   # 최근 1단계는 원문
        ev = [json.loads(l) for l in (rec.dir / "events.jsonl").read_text().splitlines()]
        self.assertTrue([e for e in ev if e["kind"] == "compact" and e.get("overflow_retry") and e.get("keep") == 1])
        self.assertIn("문맥 넘침", (rec.dir / "transcript.md").read_text())

    def test_engine_second_overflow_ends(self):
        seq = self.calls(4) + [overflow(), overflow(), _r("안 닿음")]
        with mock.patch.object(engine, "chat", side_effect=seq) as m:
            s = engine.run(self.p, Backend("fake", "http://x/v1", "m", "q", 150000), "do", Recorder(self.tmp / "runs", "t"))
        self.assertEqual(s["stop_reason"], "context_overflow")
        self.assertEqual(m.call_count, 6)

    def test_engine_nothing_to_fold_ends(self):
        with mock.patch.object(engine, "chat", side_effect=[overflow(), _r("안 닿음")]) as m:
            s = engine.run(self.p, Backend("fake", "http://x/v1", "m", "q", 150000), "do", Recorder(self.tmp / "runs", "t"))
        self.assertEqual((s["stop_reason"], m.call_count), ("context_overflow", 1))
        self.assertNotIn("overflow_retries", s)

    def test_other_errors_not_retried(self):
        seq = self.calls(3) + [BackendError("HTTP 500", 500, "boom"), _r("안 닿음")]
        with mock.patch.object(engine, "chat", side_effect=seq) as m:
            s = engine.run(self.p, Backend("fake", "http://x/v1", "m", "q", 150000), "do", Recorder(self.tmp / "runs", "t"))
        self.assertEqual((s["stop_reason"], m.call_count), ("backend_error", 4))

    def test_chat_retries_once(self):
        ui = FakeUI()
        sess = C.ChatSession(self.p, Backend("fake", "http://x/v1", "m", "q", 150000), Recorder(self.tmp / "runs", "chat-t"), ui,
                             stream=False)
        self.addCleanup(lambda: sess.rec._ev.closed or sess.rec._ev.close())
        seq = self.calls(4) + [overflow(), _r("끝", prompt=2000)]
        with mock.patch.object(C, "chat", side_effect=seq):
            self.assertEqual(sess.turn("do"), "answered")
        self.assertEqual(sess.st["overflow_retries"], 1)
        seq = self.calls(4) + [overflow(), overflow()]
        with mock.patch.object(C, "chat", side_effect=seq):
            self.assertEqual(sess.turn("again"), "context_overflow")


if __name__ == "__main__":
    unittest.main()
