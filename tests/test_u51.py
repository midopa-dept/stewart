"""판 69 Anthropic Messages API 백엔드(kind = "anthropic"): 가짜 Anthropic 서버(SSE 녹음 응답)로 망 없이.
도구 왕복(tool_use ↔ tool_result), 프롬프트 캐시(요청 맨 위 cache_control), 생각 블록을 바꾸지 않고 되보냄,
usage(입력·캐시 읽기·쓰기·출력)·비용, 429·529 재시도, 키는 환경 변수 이름만."""

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

try:
    import anthropic  # noqa: F401
    HAVE = True
except ImportError:
    HAVE = False

from stewart import anthropic_backend as AB, engine
from stewart.config import Backend, Profile
from stewart.record import Recorder


def sse(msg: dict) -> bytes:
    """완성된 메시지 하나를 Messages API 스트림 사건들로."""
    ev = []
    start = {**msg, "content": [], "stop_reason": None, "usage": {**msg["usage"], "output_tokens": 0}}
    ev.append(("message_start", {"type": "message_start", "message": start}))
    for i, b in enumerate(msg["content"]):
        if b["type"] == "text":
            ev.append(("content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}}))
            ev.append(("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": b["text"]}}))
        elif b["type"] == "thinking":
            ev.append(("content_block_start", {"type": "content_block_start", "index": i,
                                               "content_block": {"type": "thinking", "thinking": "", "signature": ""}}))
            ev.append(("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "thinking_delta", "thinking": b["thinking"]}}))
            ev.append(("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "signature_delta", "signature": b["signature"]}}))
        elif b["type"] == "tool_use":
            ev.append(("content_block_start", {"type": "content_block_start", "index": i,
                                               "content_block": {"type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}}))
            ev.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                                               "delta": {"type": "input_json_delta", "partial_json": json.dumps(b["input"])}}))
        ev.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
    ev.append(("message_delta", {"type": "message_delta", "delta": {"stop_reason": msg["stop_reason"], "stop_sequence": None},
                                 "usage": {"output_tokens": msg["usage"]["output_tokens"]}}))
    ev.append(("message_stop", {"type": "message_stop"}))
    return b"".join(f"event: {k}\ndata: {json.dumps(v)}\n\n".encode() for k, v in ev)


def message(content, stop="end_turn", inp=100, out=20, cr=0, cw=0):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": content,
            "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}}


class Fake:
    def __init__(self):
        self.script, self.requests = [], []
        me = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                me.requests.append({"path": self.path, "body": body, "key": self.headers.get("x-api-key"), "headers": dict(self.headers)})
                item = me.script.pop(0)
                if isinstance(item, int):   # 오류 상태
                    data = json.dumps({"type": "error", "error": {"type": "overloaded_error" if item == 529 else "rate_limit_error",
                                                                  "message": "잠깐"}}).encode()
                    self.send_response(item)
                    self.send_header("content-type", "application/json")
                    self.send_header("retry-after", "0")
                    self.send_header("content-length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                data = sse(item)
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class TConvert(unittest.TestCase):
    def test_messages_and_tools(self):
        msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "일해"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "t1", "type": "function",
                                                                       "function": {"name": "read_file", "arguments": "{\"path\": \"a\"}"}}]},
                {"role": "tool", "tool_call_id": "t1", "content": "내용"}, {"role": "user", "content": "[엔진] 알림"}]
        system, out = AB.to_messages(msgs)
        self.assertEqual(system, [{"type": "text", "text": "S"}])
        self.assertEqual(out[1]["content"], [{"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a"}}])
        self.assertEqual(out[2]["content"][0], {"type": "tool_result", "tool_use_id": "t1", "content": "내용"})
        t = AB.to_tools([{"type": "function", "function": {"name": "x", "description": "d", "parameters": {"type": "object"}}}])
        self.assertEqual(t, [{"name": "x", "description": "d", "input_schema": {"type": "object"}}])
        self.assertEqual(AB.thinking_param("claude-sonnet-5-5", False, {}), {"type": "between_tools"})
        self.assertIsNone(AB.thinking_param("claude-opus-5-5", False, {}))       # Opus 5.5 는 끌 수 없다
        self.assertEqual(AB.thinking_param("claude-opus-5-5", None, {}), {"type": "adaptive"})

    def test_replay_only_if_unchanged(self):
        raw = [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": "봄"},
               {"type": "tool_use", "id": "t1", "name": "ls", "input": {}}]
        m = {"role": "assistant", "content": "봄", "anthropic_content": raw,
             "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "ls", "arguments": "{}"}}]}
        self.assertIs(AB._assistant_blocks(m), raw)                               # 그대로 되보냄(생각 포함)
        m["content"] = "봄…(압축)"
        self.assertNotIn("thinking", [b["type"] for b in AB._assistant_blocks(m)])  # 엔진이 바꿨으면 다시 꾸림


@unittest.skipUnless(HAVE, "anthropic 꾸러미 없음")
class TWire(unittest.TestCase):
    def setUp(self):
        self.f = Fake()
        self.addCleanup(self.f.close)
        p = mock.patch.dict(os.environ, {"STEWART_TEST_ANTHROPIC_KEY": "test-key-not-real"})
        p.start()
        self.addCleanup(p.stop)
        AB._CLIENTS.clear()
        self.bk = Backend("claude", self.f.url, "claude-opus-5-5", "api", 1000000, api_key_env="STEWART_TEST_ANTHROPIC_KEY",
                          timeout_s=30, params={"effort": "high", "max_retries": 3}, kind="anthropic")

    def test_engine_tool_round_trip_cache_and_cost(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "a.txt").write_text("안녕", encoding="utf-8")
        think = {"type": "thinking", "thinking": "", "signature": "SIG-1"}
        self.f.script = [
            message([think, {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "a.txt"}}],
                    stop="tool_use", inp=50, cw=2000, out=30),
            529,                                                         # 과부하 → SDK 가 다시
            message([{"type": "text", "text": "a.txt 는 안녕이다."}], inp=20, cr=2000, out=10),
        ]
        prof = Profile(name="t", path=tmp / "p.toml", system="SYS", tools=["read_file"], workdir=tmp, max_steps=4)
        s = engine.run(prof, self.bk, "a.txt 를 읽어라", Recorder(tmp / "runs", "t"))
        self.assertEqual(s["stop_reason"], "completed")
        reqs = [r["body"] for r in self.f.requests]
        self.assertEqual(len(reqs), 3)
        b0 = reqs[0]
        self.assertEqual(b0["cache_control"]["type"], "ephemeral")              # 프롬프트 캐시
        self.assertEqual(b0["system"][0]["text"][:3], "SYS")
        self.assertEqual(b0["tools"][0]["name"], "read_file")
        self.assertEqual(b0["thinking"], {"type": "adaptive"})
        self.assertEqual(b0["output_config"], {"effort": "high"})
        h = self.f.requests[0]["headers"]
        self.assertIn("test-key-not-real", (h.get("x-api-key") or "") + (h.get("Authorization") or ""))   # 키는 환경 변수에서(SDK 가 헤더로)
        last = reqs[2]["messages"]
        self.assertEqual(last[1]["content"][0], think)                         # 생각 블록을 바꾸지 않고 되보냄
        self.assertEqual(last[2]["content"][0]["type"], "tool_result")
        self.assertIn("안녕", json.dumps(last[2]["content"][0]["content"], ensure_ascii=False))
        au = s["anthropic_usage"]
        self.assertEqual((au["input_tokens"], au["cache_read_input_tokens"], au["cache_creation_input_tokens"], au["output_tokens"]),
                         (70, 2000, 2000, 40))
        self.assertAlmostEqual(s["cost_usd"], (70 * 4 + 40 * 20 + 2000 * 0.2 + 2000 * 5) / 1e6, places=6)

    def test_429_retry_then_error(self):
        self.f.script = [429, message([{"type": "text", "text": "됐다"}])]
        r, _dt = AB.chat(self.bk, [{"role": "user", "content": "hi"}])
        self.assertEqual(r["choices"][0]["message"]["content"], "됐다")
        self.f.script = [429, 429, 429, 429]
        from stewart.backend import BackendError
        with self.assertRaises(BackendError) as cm:
            AB.chat(self.bk, [{"role": "user", "content": "hi"}])
        self.assertEqual(cm.exception.status, 429)

    def test_no_key(self):
        os.environ.pop("STEWART_TEST_ANTHROPIC_KEY")
        from stewart.backend import BackendError
        with self.assertRaises(BackendError):
            AB.chat(self.bk, [{"role": "user", "content": "hi"}])


    def test_fence_conditions(self):
        """울타리 조건: (a) 키 변수는 모델 셸 환경에 없다 (b) 프록시 환경 변수를 따른다 (c) 단계 usage 에 원 값 넷이 이름 그대로."""
        tmp = Path(tempfile.mkdtemp())
        self.f.script = [
            message([{"type": "tool_use", "id": "toolu_1", "name": "run_shell", "input": {"command": "env"}}], stop="tool_use"),
            message([{"type": "text", "text": "끝"}], cr=10, cw=5)]
        prof = Profile(name="t", path=tmp / "p.toml", system="S", tools=["run_shell"], workdir=tmp, max_steps=3)
        rec = Recorder(tmp / "runs", "t")
        with mock.patch.dict(os.environ, {"STEWART_CHECK_ON_STOP": "0"}):
            engine.run(prof, self.bk, "환경을 봐라", rec)
        sent = json.dumps(self.f.requests[1]["body"]["messages"], ensure_ascii=False)
        self.assertIn("PATH=", sent)                                                  # env 가 실제로 돌았다
        self.assertNotIn("test-key-not-real", sent)                                  # (a) 모델이 친 env 에 키가 없다
        self.assertNotIn("STEWART_TEST_ANTHROPIC_KEY", sent)
        self.assertTrue(AB._client(self.bk)._client.trust_env)                        # (b) HTTPS_PROXY 등 환경을 따른다
        steps = [json.loads(l) for l in (rec.dir / "events.jsonl").read_text(encoding="utf-8").splitlines() if '"kind": "step"' in l]
        u = steps[-1]["response"]["usage"]
        self.assertEqual({k: u[k] for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")},
                         {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 5})   # (c)


if __name__ == "__main__":
    unittest.main()
