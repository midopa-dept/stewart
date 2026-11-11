"""SW-33: 어떤 AI API 로도 — 글 꼴 도구 호출(tool_mode=text), 키체인 키, 키 숨김, 바깥 API 판정·표시, 가격표, 생각 고정 API."""

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from stewart import backend as B, config as C, engine, modelstat, shell, texttools as TT
from stewart.config import Backend, Profile, load_backends
from stewart.record import Recorder

ROOT = Path(__file__).resolve().parents[1]


class Fake:
    """OpenAI 꼴 가짜 서버: tools 를 받으면 400(함수 호출 칸이 없는 API 흉내), 대본대로 답한다. /models 는 404."""

    def __init__(self, script, reject_tools=True):
        self.script, self.requests, self.headers = list(script), [], []
        me = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                me.requests.append(body)
                me.headers.append(dict(self.headers))
                if reject_tools and body.get("tools"):
                    out, code = {"error": {"message": "tools not supported"}}, 400
                else:
                    content = me.script.pop(0) if me.script else "끝"
                    out, code = {"choices": [{"message": {"role": "assistant", "content": content, "reasoning_content": "생각"},
                                              "finish_reason": "stop"}],
                                 "usage": {"prompt_tokens": 1000, "completion_tokens": 200}}, 200
                data = json.dumps(out).encode()
                self.send_response(code)
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


class TTextTools(unittest.TestCase):
    def test_round_trip(self):
        tools = [{"type": "function", "function": {"name": "read_file", "description": "파일 읽기",
                                                   "parameters": {"properties": {"path": {"type": "string"}}, "required": ["path"]}}}]
        msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "읽어"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function",
                                                                       "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}]},
                {"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": "안녕"}]
        out = TT.to_text(msgs, tools)
        self.assertIn("read_file(path: string)", out[0]["content"])
        self.assertIn("```tool_call", out[2]["content"])
        self.assertNotIn("tool_calls", out[2])
        self.assertEqual(out[3]["role"], "user")
        self.assertIn("[도구 결과 read_file c1]", out[3]["content"])
        self.assertEqual(msgs[0]["content"], "S")                      # 원본 그대로
        rest, calls = TT.parse('먼저 읽는다.\n```tool_call\n{"name": "read_file", "arguments": {"path": "b.txt"}}\n```')
        self.assertEqual(rest, "먼저 읽는다.")
        self.assertEqual(calls[0]["function"]["name"], "read_file")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"path": "b.txt"})
        _, bad = TT.parse("```tool_call\n{깨짐\n```")
        self.assertEqual(bad[0]["function"]["name"], "")              # 엔진이 오류로 돌려준다

    def test_engine_with_text_tools(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "a.txt").write_text("사과", encoding="utf-8")
        f = Fake(['읽어 본다.\n```tool_call\n{"name": "read_file", "arguments": {"path": "a.txt"}}\n```', "a.txt 에는 사과라고 쓰여 있다."])
        self.addCleanup(f.close)
        bk = Backend("t", f.url, "m", "api", 32768, tool_mode="text", thinking_control="none", price={"input": 1.0, "output": 2.0})
        prof = Profile(name="t", path=tmp / "p.toml", system="S", tools=["read_file"], workdir=tmp, max_steps=4)
        rec = Recorder(tmp / "runs", "t")
        s = engine.run(prof, bk, "a.txt 를 읽어라", rec)
        self.assertEqual(s["stop_reason"], "completed")
        self.assertTrue(all("tools" not in r for r in f.requests))      # tools 칸을 보내지 않았다
        self.assertTrue(all("chat_template_kwargs" not in r for r in f.requests))   # 생각 고정 API 에는 안 보냄
        self.assertIn("[도구 부르기", f.requests[0]["messages"][0]["content"])
        self.assertIn("사과", json.dumps(f.requests[1]["messages"], ensure_ascii=False))   # 도구 결과가 글로 갔다
        self.assertAlmostEqual(s["cost_usd"], 2 * (1000 * 1.0 + 200 * 2.0) / 1e6, places=6)


class TKeys(unittest.TestCase):
    def test_keychain(self):
        C._KEYCHAIN.clear()
        bk = Backend("x", "https://api.example.com/v1", "m", "api", 1000, api_key_env="NOPE_KEY_ENV", api_key_keychain="svc-a")
        r = mock.Mock(returncode=0, stdout=b"kc-secret\n")
        with mock.patch.object(C.sys if hasattr(C, "sys") else __import__("sys"), "platform", "darwin"), \
                mock.patch("shutil.which", return_value="/usr/bin/security"), \
                mock.patch("subprocess.run", return_value=r) as run, mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NOPE_KEY_ENV", None)
            self.assertEqual(bk.api_key(), "kc-secret")
            self.assertEqual(run.call_args[0][0], ["security", "find-generic-password", "-s", "svc-a", "-w"])
        with mock.patch.dict(os.environ, {"NOPE_KEY_ENV": "env-first"}):
            self.assertEqual(bk.api_key(), "env-first")                # 환경 변수가 먼저
        self.assertNotIn("kc-secret", json.dumps(bk.public()))

    def test_missing_key_message(self):
        C._KEYCHAIN.clear()
        C._KEYCHAIN["svc-none"] = None
        bk = Backend("x", "https://api.example.com/v1", "m", "api", 1000, api_key_env="NOPE2", api_key_keychain="svc-none")
        with self.assertRaises(B.BackendError) as cm:
            B.chat(bk, [{"role": "user", "content": "x"}])
        self.assertIn("API 키가 없다: 환경 변수 NOPE2 또는 키체인 항목 svc-none", str(cm.exception))

    def test_provider_keys_hidden_from_tools(self):
        with mock.patch.dict(os.environ, {"EXAONE_API_KEY": "v1", "OPENAI_API_KEY": "v2", "GEMINI_API_KEY": "v3", "PATH": os.environ["PATH"]}):
            env = shell.tool_env()
        for k in ("EXAONE_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
            self.assertNotIn(k, env)


class TExternal(unittest.TestCase):
    def test_external_judgement(self):
        mk = lambda u, **kw: Backend("x", u, "m", "api", 1000, **kw)   # noqa: E731
        self.assertTrue(mk("https://api.exaone.ai/v1").external())
        self.assertFalse(mk("http://127.0.0.1:8080/v1").external())
        self.assertFalse(mk("http://" + ".".join(["100", "101", "1", "2"]) + ":8000/v1").external())   # 공유 주소 대역
        self.assertFalse(mk("https://box." + "tailxyz." + "ts.net/v1").external())
        self.assertFalse(mk("http://" + ".".join(["192", "168", "0", "5"]) + ":8000/v1").external())
        self.assertTrue(mk("http://127.0.0.1:9/v1", provider="시험").external())
        self.assertEqual(mk("https://api.exaone.ai/v1").provider_label(), "api.exaone.ai")

    def test_probe_external_without_models_list(self):
        f = Fake([])
        self.addCleanup(f.close)
        bk = Backend("x", f.url, "m1", "api", 1000, provider="시험 API", api_key_env="PROBE_KEY")
        with mock.patch.dict(os.environ, {"PROBE_KEY": "k"}):
            st = modelstat.probe(bk, timeout=3)
        self.assertEqual(st["state"], "ok")
        self.assertIsNone(st["pod"])                                    # 회사 주소의 장비 감시 포트는 찌르지 않는다

    def test_thinking_control_none(self):
        bk = Backend("x", "https://a.example/v1", "m", "api", 1000, thinking_control="none")
        self.assertEqual(B.thinking_kwargs(bk, False), {})

    def test_providers_example_loads(self):
        b, d = load_backends(ROOT / "backends.providers.example.toml")
        self.assertEqual(d, "exaone")
        ex = b["exaone"]
        self.assertEqual((ex.base_url, ex.model, ex.max_context), ("https://api.exaone.ai/v1", "k-exaone_v2.5", 524288))
        self.assertTrue(ex.external())
        self.assertFalse(b["llamacpp"].external())
        text = (ROOT / "backends.providers.example.toml").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"(sk-|ghp_|AIza)[A-Za-z0-9]{8,}")      # 키 값 없음

    def test_hub_chip_built(self):
        js = (ROOT / "src/stewart/web_ui/app.js").read_text(encoding="utf-8")
        self.assertIn("바깥 API: ", js)


if __name__ == "__main__":
    unittest.main()
