"""연결 오류의 예외 사슬: 단계 error·events 에 종류·메시지 전체(비밀 가림). openai 꼴(urllib)·Anthropic SDK 둘 다."""

import json
import os
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from stewart import backend as B, engine
from stewart.config import Backend, Profile
from stewart.record import Recorder


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _Plain(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.send_response(200)
        self.end_headers()


class TChain(unittest.TestCase):
    def test_redact(self):
        t = B.redact("Bearer sk-ant-abc123456789 https://u:pw@proxy.x:3128/ ?api_key=zzz&x=1 ghp_" + "a" * 20)
        for bad in ("sk-ant-abc", "u:pw", "zzz", "ghp_aaa"):
            self.assertNotIn(bad, t)
        self.assertIn("proxy.x:3128", t)

    def test_chain_walks_cause_and_context(self):
        try:
            try:
                raise OSError(61, "Connection refused")
            except OSError as inner:
                raise RuntimeError("Connection error.") from inner
        except RuntimeError as e:
            ch = B.exc_chain(e)
        self.assertEqual([c["type"] for c in ch], ["RuntimeError", "ConnectionRefusedError"])
        self.assertIn("Connection refused", ch[1]["msg"])

    def test_openai_tls_mismatch(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Plain)   # https 로 평문 서버에 붙으면 TLS 오류
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        bk = Backend("t", f"https://127.0.0.1:{srv.server_address[1]}/v1", "m", "q", 4096, timeout_s=5)
        with self.assertRaises(B.BackendError) as cm:
            B.chat(bk, [{"role": "user", "content": "x"}])
        e = cm.exception
        self.assertTrue(str(e).startswith("연결 실패: "))
        self.assertTrue(any("SSL" in c["type"] or "SSL" in c["msg"] for c in e.chain), e.chain)
        self.assertIn(" ← ", str(e))

    def test_engine_records_chain(self):
        tmp = Path(tempfile.mkdtemp())
        bk = Backend("t", f"http://127.0.0.1:{free_port()}/v1", "m", "q", 4096, timeout_s=5)
        prof = Profile(name="t", path=tmp / "p.toml", system="s", tools=["read_file"], workdir=tmp, max_steps=2)
        rec = Recorder(tmp / "runs", "t")
        engine.run(prof, bk, "do", rec)
        steps = [json.loads(l) for l in (rec.dir / "events.jsonl").read_text().splitlines() if '"kind": "step"' in l]
        st = next(s for s in steps if s.get("error"))
        self.assertIn("refused", st["error"].lower())
        self.assertTrue(st["error_chain"])


@unittest.skipUnless(B and __import__("importlib").util.find_spec("anthropic"), "anthropic SDK 없음")
class TAnthropic(unittest.TestCase):
    def test_connection_error_chain(self):
        from stewart import anthropic_backend as AB
        bk = Backend("claude", f"http://127.0.0.1:{free_port()}", "claude-opus-5-5", "api", 1000000,
                     api_key_env="STEWART_TEST_ANTHROPIC_KEY", kind="anthropic", timeout_s=5, params={"max_retries": 0})
        with mock.patch.dict(os.environ, {"STEWART_TEST_ANTHROPIC_KEY": "sk-ant-test-" + "x" * 20}):
            AB._CLIENTS.clear()
            with self.assertRaises(B.BackendError) as cm:
                B.chat(bk, [{"role": "user", "content": "x"}])
        e = cm.exception
        self.assertIn("Connection error", str(e))
        self.assertGreaterEqual(len(e.chain), 2, e.chain)              # SDK 겉 + 속(httpx·OS)
        self.assertTrue(any("refused" in c["msg"].lower() or "connect" in c["type"].lower() for c in e.chain[1:]), e.chain)
        self.assertNotIn("x" * 20, json.dumps(e.chain))


if __name__ == "__main__":
    unittest.main()
