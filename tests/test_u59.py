"""SW-34 ①: OpenAI 호환 백엔드 다시 보내기 — 429(Retry-After)·5xx·연결 끊김, 실사용 기본 4·평가 기본 0, 기록."""

import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from stewart import backend as B, engine
from stewart.config import Backend, Profile
from stewart.record import Recorder


class Flaky:
    def __init__(self, codes, retry_after=None):
        self.codes, self.hits = list(codes), []
        me = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                me.hits.append(time.monotonic())
                code = me.codes.pop(0) if me.codes else 200
                if code != 200:
                    self.send_response(code)
                    if retry_after is not None and code == 429:
                        self.send_header("Retry-After", str(retry_after))
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"{}")
                    return
                data = json.dumps({"choices": [{"message": {"role": "assistant", "content": "끝"}, "finish_reason": "stop"}],
                                   "usage": {"prompt_tokens": 5, "completion_tokens": 1}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class TRetry(unittest.TestCase):
    def setUp(self):
        e = mock.patch.dict(os.environ)
        e.start()
        self.addCleanup(e.stop)
        os.environ.pop("STEWART_BACKEND_RETRIES", None)   # 시험 묶음 기본(0)을 풀고 백엔드·프로세스 값으로
        p = mock.patch.object(B, "RETRY_CAP_S", 60.0)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(lambda: B.set_default_retries(0))
        q = mock.patch.object(B.random, "random", return_value=0.0)   # 대기 = 2^n × 0.75 초
        q.start()
        self.addCleanup(q.stop)

    def srv(self, codes, **kw):
        f = Flaky(codes, **kw)
        self.addCleanup(f.close)
        return f

    def test_eval_default_no_retry(self):
        f = self.srv([503])
        bk = Backend("t", f.url, "m", "q", 4096)
        with self.assertRaises(B.BackendError):
            B.chat(bk, [{"role": "user", "content": "x"}])
        self.assertEqual(len(f.hits), 1)

    def test_retry_after_and_5xx(self):
        f = self.srv([429, 503], retry_after=0)
        bk = Backend("t", f.url, "m", "q", 4096, retries=3)
        with mock.patch.object(B, "_sleep_abortable") as sl:
            data, _ = B.chat(bk, [{"role": "user", "content": "x"}])
        self.assertEqual(data["choices"][0]["message"]["content"], "끝")
        self.assertEqual(len(f.hits), 3)
        self.assertEqual([c.args[0] for c in sl.call_args_list], [0.0, 3.0])   # Retry-After 0, 그다음 2·2^1·0.75
        log = B.take_retries()
        self.assertEqual([x["status"] for x in log], [429, 503])
        self.assertEqual(B.take_retries(), [])

    def test_gives_up_and_not_on_400(self):
        f = self.srv([500, 500, 500])
        bk = Backend("t", f.url, "m", "q", 4096, retries=2)
        with mock.patch.object(B, "_sleep_abortable"), self.assertRaises(B.BackendError):
            B.chat(bk, [{"role": "user", "content": "x"}])
        self.assertEqual(len(f.hits), 3)
        g = self.srv([400])
        with mock.patch.object(B, "_sleep_abortable"), self.assertRaises(B.BackendError):
            B.chat(Backend("t", g.url, "m", "q", 4096, retries=2), [{"role": "user", "content": "x"}])
        self.assertEqual(len(g.hits), 1)

    def test_env_wins_and_interactive_default(self):
        f = self.srv([503, 503])
        bk = Backend("t", f.url, "m", "q", 4096)
        B.set_default_retries(4)
        with mock.patch.dict(os.environ, {"STEWART_BACKEND_RETRIES": "1"}), mock.patch.object(B, "_sleep_abortable"), \
                self.assertRaises(B.BackendError):
            B.chat(bk, [{"role": "user", "content": "x"}])
        self.assertEqual(len(f.hits), 2)

    def test_stream_path_retries(self):
        f = self.srv([502])
        bk = Backend("t", f.url, "m", "q", 4096, retries=2)
        with mock.patch.object(B, "_sleep_abortable"):
            try:
                B.chat_stream(bk, [{"role": "user", "content": "x"}])
            except Exception:
                pass   # 가짜 서버는 스트림 꼴이 아니다 — 다시 보낸 횟수만 본다
        self.assertEqual(len(f.hits), 2)
        self.assertEqual(B.take_retries()[0]["status"], 502)

    def test_retryable_kinds(self):
        e = B.BackendError("연결 실패", chain=[{"type": "ConnectionRefusedError", "msg": "[Errno 61] Connection refused"}])
        self.assertTrue(B._retryable(e))
        e2 = B.BackendError("연결 실패", chain=[{"type": "TimeoutError", "msg": "timed out"}])
        self.assertFalse(B._retryable(e2))

    def test_engine_records(self):
        tmp = Path(tempfile.mkdtemp())
        f = self.srv([503])
        bk = Backend("t", f.url, "m", "q", 4096, retries=2)
        prof = Profile(name="t", path=tmp / "p.toml", system="s", tools=["read_file"], workdir=tmp, max_steps=2)
        rec = Recorder(tmp / "runs", "t")
        with mock.patch.object(B, "_sleep_abortable"):
            s = engine.run(prof, bk, "do", rec)
        self.assertEqual(s["stop_reason"], "completed")
        self.assertEqual(s["backend_retries"], 1)
        self.assertIn('"backend_retry"', (rec.dir / "events.jsonl").read_text())


if __name__ == "__main__":
    unittest.main()
