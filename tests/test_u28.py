"""U2-8 긴급: 멈춤이 긴 프롬프트 읽기(응답 머리 전)·생성 중에도 바로 먹는지, 승인 건이 늘 허용 계산이 깨져도 남는지."""

import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from stewart import backend as B
from stewart import hub as H
from stewart.config import Backend
from tests.test_hub import msg
from tests.test_hub3 import MainBase


class Slow:
    """응답 머리를 head_delay 초 늦게 주고(서버가 긴 프롬프트를 읽는 중을 흉내), 그 뒤 조각을 천천히."""

    def __init__(self, head_delay=10.0):
        me = self
        self.head_delay = head_delay

        class Hd(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                time.sleep(me.head_delay)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for i in range(50):
                        ev = {"choices": [{"index": 0, "delta": {"content": f"조각{i} "}}]}
                        self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                        self.wfile.flush()
                        time.sleep(0.2)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Hd)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class TAbort(unittest.TestCase):
    def go(self, head_delay, abort_after):
        s = Slow(head_delay)
        self.addCleanup(s.close)
        bk = Backend("slow", s.url, "m", "q", 100000, timeout_s=30)
        out = {}

        def run():
            t0 = time.monotonic()
            try:
                B.chat_stream(bk, [{"role": "user", "content": "x"}], None)
                out["r"] = "끝까지"
            except KeyboardInterrupt:
                out["r"] = "끊김"
            out["dt"] = time.monotonic() - t0
        th = threading.Thread(target=run)
        th.start()
        time.sleep(abort_after)
        t1 = time.monotonic()
        B.abort_streams()
        th.join(10)
        return out["r"], time.monotonic() - t1

    def test_abort_before_headers(self):
        r, dt = self.go(head_delay=10, abort_after=0.5)
        self.assertEqual(r, "끊김")
        self.assertLess(dt, 1.5)                          # 응답 머리 전(프롬프트 읽는 중)에도 곧바로

    def test_abort_while_streaming(self):
        r, dt = self.go(head_delay=0.1, abort_after=1.0)
        self.assertEqual(r, "끊김")
        self.assertLess(dt, 1.5)


class TQueueRobust(unittest.TestCase):
    def test_candidates_error_keeps_ask(self):
        tmp = Path(tempfile.mkdtemp())
        hub = H.Hub(tmp / "hub", tmp / "runs")
        hub.register_main(tmp, "edits")
        sd = hub.sdir("main")
        H.write_json(sd.ask_dir / H.seq_name(1, "json"), {"seq": 1, "session": "main", "kind": "confirm", "prompt": "p",
                                                          "args_summary": {"tool": "run_shell", "command": 'python3 -c "\nprint(1)\n"'}})
        with mock.patch("stewart.allowrules.candidates", side_effect=RuntimeError("깨짐")):
            q = hub.queue()
        self.assertEqual(len(q), 1)                         # 늘 허용 계산이 깨져도 승인 건은 남는다
        self.assertFalse(q[0]["always"]["ok"])
        self.assertIn("한 번 허용", q[0]["always"]["reason"])


class TMainStopFast(MainBase):
    def test_stop_during_long_prefill(self):
        """허브 메인이 응답 머리를 기다리는 중(긴 프롬프트 읽기)에 [멈춤] — 몇 초 안에 차례가 끝난다."""
        s = Slow(head_delay=30)
        self.addCleanup(s.close)
        self.main.sess.backend.base_url = s.url
        self.assertEqual(self.main.send("길게 읽어"), "started")
        time.sleep(0.8)
        t0 = time.monotonic()
        self.assertEqual(self.main.stop_turn(), "stopping")
        self.assertTrue(self.main.wait_idle(5))
        self.assertLess(time.monotonic() - t0, 3)
        self.assertIn("화면에서 멈춤", " ".join(x["text"] for x in self.main.ui.log))


if __name__ == "__main__":
    unittest.main()
