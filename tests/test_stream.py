"""chat 스트리밍 + 바로 끊기: 조각 조립(본문·생각·툴콜), 실제 SSE 서버, 끊으면 받은 데까지 남김."""

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from stewart import backend as B, chat as C
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_engine import _resp


def _sse(evs):
    return "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in evs) + "data: [DONE]\n\n"


TOOL_STREAM = [
    {"choices": [{"delta": {"reasoning_content": "읽어 보자"}}]},
    {"choices": [{"delta": {"content": "파일을 "}}]},
    {"choices": [{"delta": {"content": "본다."}}]},
    {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c0", "function": {"name": "read_file", "arguments": "{\"pa"}}]}}]},
    {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "th\": \"a.txt\"}"}}]}}]},
    {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    {"choices": [], "usage": {"prompt_tokens": 30, "completion_tokens": 9}},
]


class SSEHandler(BaseHTTPRequestHandler):
    script: list = []
    bodies: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        SSEHandler.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        evs = SSEHandler.script.pop(0)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(_sse(evs).encode())


class TStream(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a.txt").write_text("hello\n")

    def test_assembler(self):
        a = B.StreamAssembler()
        for e in TOOL_STREAM:
            a.feed(e)
        r = a.response()
        m = r["choices"][0]["message"]
        self.assertEqual(m["content"], "파일을 본다.")
        self.assertEqual(m["reasoning_content"], "읽어 보자")
        self.assertEqual(m["tool_calls"], [{"id": "c0", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}])
        self.assertEqual((r["choices"][0]["finish_reason"], r["usage"]["completion_tokens"]), ("tool_calls", 9))
        b = B.StreamAssembler()
        b.feed({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "x", "arguments": "{}"}}]}}]})
        self.assertNotIn("id", b.message()["tool_calls"][0])   # id 없으면 빼서 엔진이 채우게

    def test_chat_session_over_real_sse(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), SSEHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        SSEHandler.bodies.clear()
        SSEHandler.script[:] = [TOOL_STREAM, [{"choices": [{"delta": {"content": "hello 라고 "}}]},
                                             {"choices": [{"delta": {"content": "써 있다."}, "finish_reason": "stop"}]}]]
        bk = Backend("fake", f"http://127.0.0.1:{srv.server_address[1]}/v1", "m", "q", 4096)
        ui = FakeUI()
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.tmp, max_steps=4)
        s = C.ChatSession(p, bk, Recorder(self.tmp / "runs", "chat-t"), ui, stream=True)
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        self.assertEqual(s.turn("a.txt 읽어"), "answered")
        out = ui.out.getvalue()
        self.assertIn("● 파일을 본다.", out)
        self.assertIn("● hello 라고 써 있다.", out)
        self.assertEqual(out.count("hello 라고 써 있다."), 1)            # 조각으로 쓰고 다시 쓰지 않는다
        self.assertTrue(SSEHandler.bodies[0]["stream"])
        self.assertEqual([m["role"] for m in s.messages], ["system", "user", "assistant", "tool", "assistant"])
        self.assertIn("hello", s.messages[3]["content"])
        self.assertEqual(s.st["completion_tokens"], 9)

    def test_interrupt_keeps_partial(self):
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.tmp, max_steps=4)
        ui = FakeUI()
        s = C.ChatSession(p, Backend("fake", "http://x/v1", "m", "q", 4096), Recorder(self.tmp / "runs", "chat-t"), ui, stream=True)
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())

        def fake_stream(backend, messages, tools=None, response_format=None, max_tokens=None, on_delta=None):
            on_delta("긴 답의 앞", "")
            e = KeyboardInterrupt()
            e.partial = {"role": "assistant", "content": "긴 답의 앞",
                         "tool_calls": [{"id": "x", "type": "function", "function": {"name": "run_shell", "arguments": "{\"comm"}}]}
            raise e

        with mock.patch.object(C, "chat_stream", side_effect=fake_stream):
            self.assertEqual(s.turn("길게 써"), "interrupted")
        roles = [m["role"] for m in s.messages]
        self.assertEqual(roles, ["system", "user", "assistant", "user", "assistant"])
        self.assertTrue(s.messages[2]["content"].startswith("긴 답의 앞"))
        self.assertNotIn("tool_calls", s.messages[2])                   # 끊긴 툴콜은 실행·기록하지 않는다
        self.assertIn("중단", ui.out.getvalue())
        kinds = [json.loads(l)["kind"] for l in (s.rec.dir / "events.jsonl").read_text().splitlines()]
        self.assertIn("partial", kinds)
        # 다음 차례는 정상
        with mock.patch.object(C, "chat_stream", side_effect=[_resp("짧게")]):
            self.assertEqual(s.turn("짧게"), "answered")

    def test_stream_error_event(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), SSEHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        SSEHandler.script[:] = [[{"error": {"message": "boom"}}]]
        bk = Backend("fake", f"http://127.0.0.1:{srv.server_address[1]}/v1", "m", "q", 4096)
        with self.assertRaises(B.BackendError):
            B.chat_stream(bk, [{"role": "user", "content": "x"}])

    def test_non_stream_default_unchanged(self):
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.tmp, max_steps=4)
        s = C.ChatSession(p, Backend("fake", "http://x/v1", "m", "q", 4096), Recorder(self.tmp / "runs", "chat-t"), FakeUI())
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        self.assertFalse(s.stream)


if __name__ == "__main__":
    unittest.main()
