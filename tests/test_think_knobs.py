"""생각 켬 손잡이(상한·샘플링 묶음)와 결함 기록(도구 오류 유형·버린 툴콜·생각 토큰·같은 호출 결과)."""

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from stewart import backend as B, chat as C, engine, think as TH
from stewart.config import Backend, ConfigError, Profile, load_profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI

_N = [0]


def r(content=None, calls=None, usage=None):
    msg = {"role": "assistant", "content": content}
    if calls:
        _N[0] += 1
        msg["tool_calls"] = [{"id": f"k{_N[0]}-{i}", "type": "function", "function": {"name": n, "arguments": a}}
                             for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": msg, "finish_reason": "stop"}],
            "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5}}, 0.1


def think_backend(on=True, ctx=100000):
    return Backend("fake", "http://x/v1", "m", "q", ctx, params={"temperature": 0.2,
                                                                 "chat_template_kwargs": {"enable_thinking": on}})


class TSettings(unittest.TestCase):
    def p(self, **k):
        return Profile(name="t", path=Path("p"), system="s", tools=[], workdir=Path("."), **k)

    def test_sources(self):
        self.assertEqual(TH.settings(self.p(), {})["max_tokens"], None)                       # 기본 = 지금 그대로
        self.assertEqual(TH.settings(self.p(), {})["params"], {})
        s = TH.settings(self.p(think_max_tokens=16384, think_sampling="qwen"), {})
        self.assertEqual((s["max_tokens"], s["sampling"], s["params"]["top_k"]), (16384, "qwen", 20))
        s = TH.settings(self.p(think_max_tokens=16384), {TH.ENV_MAX: "24576", TH.ENV_SAMPLING: "qwen-nopp"})
        self.assertEqual((s["max_tokens"], s["sampling"], s["source"]), (24576, "qwen-nopp", {"max_tokens": "env", "sampling": "env"}))
        s = TH.settings(self.p(think_max_tokens=16384, think_sampling="qwen"), {TH.ENV_MAX: "0", TH.ENV_SAMPLING: "none"})
        self.assertEqual((s["max_tokens"], s["params"]), (None, {}))
        s = TH.settings(self.p(), {TH.ENV_MAX: "12", TH.ENV_SAMPLING: "greedy"})
        self.assertEqual(s["ignored"], [TH.ENV_MAX, TH.ENV_SAMPLING])

    def test_profile_keys(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "x.toml").write_text('system = "s"\nthink_max_tokens = 16384\nthink_sampling = "qwen"\n', encoding="utf-8")
        p = load_profile(str(tmp / "x.toml"))
        self.assertEqual((p.think_max_tokens, p.think_sampling), (16384, "qwen"))
        for bad in ("think_max_tokens = 100", 'think_sampling = "hot"'):
            (tmp / "x.toml").write_text(f'system = "s"\n{bad}\n', encoding="utf-8")
            with self.assertRaises(ConfigError, msg=bad):
                load_profile(str(tmp / "x.toml"))
        self.assertIsNone(load_profile("code").think_max_tokens)

    def test_fit_and_on(self):
        self.assertEqual(TH.fit(16384, 65536, 30000), 16384)
        self.assertEqual(TH.fit(16384, 65536, 60000), 65536 - 60000 - 256)                  # 문맥에 맞게 줄임
        self.assertEqual(TH.fit(16384, 65536, 65000), 1024)                                 # 바닥
        self.assertTrue(TH.is_on(think_backend(True), None))
        self.assertFalse(TH.is_on(think_backend(True), False))
        self.assertFalse(TH.is_on(think_backend(False), None))


class TEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        for k in (TH.ENV_MAX, TH.ENV_SAMPLING):
            os.environ.pop(k, None)

    def go(self, seq, bk, **pk):
        p = Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["read_file", "run_shell"], workdir=self.tmp,
                    max_steps=8, **pk)
        rec = Recorder(self.tmp / "runs", "t")
        with mock.patch.object(engine, "chat", side_effect=seq) as m:
            s = engine.run(p, bk, "일해", rec)
        ev = [json.loads(l) for l in (rec.dir / "events.jsonl").read_text().splitlines()]
        return s, m, ev

    def test_knobs_only_when_thinking(self):
        s, m, _ = self.go([({"choices": [{"message": {"role": "assistant", "content": ""}, "finish_reason": "length"}],
                             "usage": {"prompt_tokens": 10, "completion_tokens": 9}}, 0.1), r("답")],
                          think_backend(True), think_max_tokens=16384, think_sampling="qwen")
        first, second = m.call_args_list
        self.assertEqual(first.kwargs["max_tokens"], 16384)
        self.assertEqual(first.kwargs["overrides"]["presence_penalty"], 1.0)
        self.assertIs(second.kwargs["thinking"], False)                     # 빈 잘림 재시도(생각 끔)에는 손잡이 없음
        self.assertNotIn("overrides", second.kwargs)
        self.assertEqual(s["think"]["sampling"], "qwen")
        s, m, _ = self.go([r("답")], think_backend(False), think_max_tokens=16384, think_sampling="qwen")
        self.assertNotIn("overrides", m.call_args_list[0].kwargs)          # 생각 끔 백엔드는 손잡이를 쓰지 않는다
        self.assertEqual(s["think"]["sampling"], "qwen")                    # 설정은 기록된다

    def test_default_unchanged(self):
        s, m, _ = self.go([r("답")], think_backend(True))
        self.assertEqual(m.call_args_list[0].kwargs, {})                     # 손잡이가 없으면 전과 같은 호출
        self.assertNotIn("think", s)

    def test_defect_fields(self):
        (self.tmp / "a.txt").write_text("x")
        seq = [r(calls=[("read_file", '{"path": "a.txt"')]),                 # 깨진 인자 JSON
               r(calls=[("nope_tool", "{}")]),                                # 없는 도구
               r(calls=[("read_file", '{"path": "a.txt"}')]),                 # 성공(연속 오류 상한 4 에 안 걸리게)
               r(calls=[("read_file", '{"pathx": "a.txt"}')]),                # 모르는 인자(스키마)
               r(calls=[("read_file", '{"path": "none.txt"}')]),              # 실행 실패
               r(calls=[("read_file", '{"path": "a.txt"}')], usage={"prompt_tokens": 10, "completion_tokens": 50,
                                                                      "completion_tokens_details": {"reasoning_tokens": 40}}),
               r(calls=[("read_file", '{"path": "a.txt"}')]),                 # 같은 호출·같은 결과
               r('read_file({"path": "a.txt"})'),                             # 본문에 툴콜 꼴
               r("끝")]
        s, _, ev = self.go(seq, think_backend(False))
        self.assertEqual(s["tool_error_kinds"], {"schema": 2, "unknown_tool": 1, "exec_fail": 1})
        self.assertEqual(s["broken_toolcalls"], {"args_json": 1, "in_content": 1})
        b = [e for e in ev if e["kind"] == "broken_toolcall"]
        self.assertEqual([x["form"] for x in b], ["args_json", "in_content"])
        self.assertEqual(b[0]["raw"], '{"path": "a.txt"')
        self.assertEqual(s["reasoning_tokens"], 40)
        self.assertGreaterEqual(s["same_call_same_result"], 2)              # 성공한 같은 읽기 세 번
        steps = [e for e in ev if e["kind"] == "step" and (e.get("response") or {}).get("tool_calls")]
        tc = steps[0]["response"]["tool_calls"][0]
        self.assertTrue(len(tc["call_sha"]) == 16 and len(tc["result_sha"]) == 16)

    def test_reasoning_tokens_null_when_absent(self):
        s, _, _ = self.go([r("답")], think_backend(False))
        self.assertIsNone(s["reasoning_tokens"])


class TDropUnknown(unittest.TestCase):
    def test_server_rejects_field_then_drop(self):
        seen = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append(sorted(body))
                if "top_k" in body:
                    out, code = json.dumps({"error": {"message": "Unrecognized request argument supplied: top_k"}}), 400
                else:
                    out, code = json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"},
                                                         "finish_reason": "stop"}], "usage": {}}), 200
                b = out.encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        bk = Backend("x", f"http://127.0.0.1:{srv.server_address[1]}/v1", "m", "q", 1000)
        data, _ = B.chat(bk, [{"role": "user", "content": "hi"}], overrides={"top_k": 20, "top_p": 0.95})
        self.assertEqual(data["choices"][0]["message"]["content"], "ok")
        self.assertEqual(bk.dropped_params, ["top_k"])
        self.assertIn("top_k", seen[0])
        self.assertNotIn("top_k", seen[1])
        self.assertIn("top_p", seen[1])
        B.chat(bk, [{"role": "user", "content": "hi"}], overrides={"top_k": 20})
        self.assertNotIn("top_k", seen[2])                                   # 이후에는 처음부터 뺀다
        self.assertEqual(len(seen), 3)


class TChat(unittest.TestCase):
    def test_chat_think_on_uses_knobs(self):
        tmp = Path(tempfile.mkdtemp())
        p = Profile(name="chat", path=tmp / "p.toml", system="s", tools=["read_file"], workdir=tmp, max_steps=3,
                    think_max_tokens=16384, think_sampling="qwen")
        sess = C.ChatSession(p, think_backend(False), Recorder(tmp / "runs", "chat-t"), FakeUI(), stream=False, think=True)
        self.addCleanup(lambda: sess.rec._ev.closed or sess.rec._ev.close())
        with mock.patch.object(C, "chat", side_effect=[r("답")]) as m:
            sess.turn("해")
        kw = m.call_args_list[0].kwargs
        self.assertEqual((kw["thinking"], kw["max_tokens"], kw["overrides"]["temperature"]), (True, 16384, 0.6))


if __name__ == "__main__":
    unittest.main()
