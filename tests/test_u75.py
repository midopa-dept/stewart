"""판 96(SW-41 ③): LoRA 어댑터 고르기 — backends 표 adapters·adapter·adapter_style(model = vLLM 요청 model 칸, lora = llama.cpp 요청 lora 칸),
STEWART_ADAPTER=off·판 이름이 이김, 잘못된 표는 ConfigError, 기록·학습 meta 에 판, stewart adapter 보기(서버 목록 대조)."""

import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from stewart import backend as B, train as TRAIN
from stewart.config import ConfigError, load_backends


def table(tmp: Path, body: str) -> Path:
    p = tmp / "backends.toml"
    p.write_text('default = "q"\n[backends.q]\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "base-model"\nmax_context = 8192\n' + body,
                 encoding="utf-8")
    return p


class TBody(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        e = mock.patch.dict(os.environ, {}, clear=False)
        e.start()
        self.addCleanup(e.stop)
        os.environ.pop("STEWART_ADAPTER", None)

    def test_model_style(self):
        b = load_backends(table(self.tmp, 'adapters = { v1 = "my-lora-v1", v2 = "my-lora-v2" }\nadapter = "v1"\n'))[0]["q"]
        self.assertEqual(B._body(b, [{"role": "user", "content": "x"}])["model"], "my-lora-v1")
        with mock.patch.dict(os.environ, {"STEWART_ADAPTER": "off"}):
            self.assertEqual(B._body(b, [])["model"], "base-model")                   # 한 줄 되돌리기
        with mock.patch.dict(os.environ, {"STEWART_ADAPTER": "v2"}):
            self.assertEqual(B._body(b, [])["model"], "my-lora-v2")
        with mock.patch.dict(os.environ, {"STEWART_ADAPTER": "nope"}):
            self.assertEqual(B._body(b, [])["model"], "base-model")                   # 이 백엔드에 없는 판이면 기반
        self.assertEqual(b.public()["adapter"], "v1")
        self.assertEqual(TRAIN.backend_meta(b.public())["adapter"], "v1")
        plain = load_backends(table(self.tmp, ""))[0]["q"]
        self.assertNotIn("adapter", plain.public())
        self.assertNotIn("lora", B._body(plain, []))

    def test_lora_style(self):
        b = load_backends(table(self.tmp, 'adapters = { v1 = 0, v2 = 1 }\nadapter = "v2"\nadapter_style = "lora"\nadapter_scale = 0.8\n'))[0]["q"]
        body = B._body(b, [])
        self.assertEqual(body["model"], "base-model")
        self.assertEqual(body["lora"], [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 0.8}])
        with mock.patch.dict(os.environ, {"STEWART_ADAPTER": "off"}):
            self.assertEqual([x["scale"] for x in B._body(b, [])["lora"]], [0.0, 0.0])   # 실린 어댑터를 모두 끔 = 기반

    def test_bad_tables(self):
        for body in ('adapters = { v1 = "a" }\nadapter = "v9"\n', 'adapters = { v1 = "a" }\nadapter_style = "lora"\n',
                     'adapters = { v1 = 0 }\nadapter_style = "weird"\n'):
            with self.assertRaises(ConfigError):
                load_backends(table(self.tmp, body))


class _Srv(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"data": [{"id": "base-model"}, {"id": "my-lora-v1"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class TCli(unittest.TestCase):
    def test_status_and_probe(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Srv)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        tmp = Path(tempfile.mkdtemp())
        p = tmp / "backends.toml"
        p.write_text(f'default = "q"\n[backends.q]\nbase_url = "http://127.0.0.1:{srv.server_port}/v1"\nmodel = "base-model"\nmax_context = 8192\n'
                     'adapters = { v1 = "my-lora-v1", v2 = "my-lora-v2" }\nadapter = "v1"\n', encoding="utf-8")
        from stewart import cli
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"STEWART_BACKENDS": str(p)}), mock.patch("sys.stdout", buf):
            os.environ.pop("STEWART_ADAPTER", None)
            self.assertEqual(cli.main(["adapter"]), 0)
        out = buf.getvalue()
        self.assertIn("지금 v1", out)
        self.assertIn("v1: my-lora-v1 — 실림", out)
        self.assertIn("v2: my-lora-v2 — 서버 목록에 없음", out)


if __name__ == "__main__":
    unittest.main()
