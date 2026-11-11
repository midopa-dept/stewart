"""tag 45 실사용 지적 셋: 모델 서버 점(찔러 본 결과)·오류 한 줄, 실제 모델 이름, 로그인 유지·hub url."""

import http.client
import io
import json
import socket
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

from stewart import modelstat as MS
from stewart.backend import BackendError
from stewart.config import Backend
from stewart.hubmain import hub_url
from stewart.web import WebApp, make_token
from tests.test_hub import msg
from tests.test_hub3 import MainBase


class Srv:
    def __init__(self, code=200, body=None):
        me = self
        self.code, self.body = code, body if body is not None else {"data": [{"id": "model-a-w4a16"}]}

        class Hd(BaseHTTPRequestHandler):
            def do_GET(self):
                data = json.dumps(me.body).encode()
                self.send_response(me.code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Hd)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class TProbe(unittest.TestCase):
    def bk(self, url, model="qwen3.8-27b"):
        return Backend("b", url, model, "q", 32768, timeout_s=5)

    def test_states(self):
        s = Srv()
        self.addCleanup(s.close)
        st = MS.probe(self.bk(s.url))
        self.assertEqual((st["state"], st["label"]), ("ok", "준비됨"))
        self.assertEqual(st["served"], ["model-a-w4a16"])
        self.assertTrue(st["mismatch"])                                   # 설정 이름과 서버가 올린 이름이 다르다
        self.assertFalse(MS.probe(self.bk(s.url, "model-a-w4a16"))["mismatch"])
        s.code = 503
        self.assertEqual(MS.probe(self.bk(s.url))["state"], "loading")    # llama-server: 모델 올리는 중
        s.code, s.body = 200, {"data": []}
        self.assertEqual(MS.probe(self.bk(s.url))["state"], "loading")
        s.code = 401
        self.assertEqual(MS.probe(self.bk(s.url))["state"], "auth")
        down = MS.probe(self.bk(f"http://127.0.0.1:{free_port()}/v1"))
        self.assertEqual(down["state"], "down")
        self.assertIn("연결 거부", down["detail"])
        self.assertIsNone(down["pod"])                                    # loopback 은 파드 감시기를 찾지 않는다

    def test_pod_alias_adapter(self):
        """빌린 장비(loopback 아님): 감시기 :8001/status 의 별칭·어댑터를 붙이고, 모델 서버만 안 되면 "올라오는 중"."""
        from unittest import mock
        seen = []

        def fake_get(url, headers, timeout):
            seen.append(url)
            if url.endswith("/status"):
                return 200, json.dumps({"config": {"model": "model-a-w4a16", "adapter": "hyperqwen", "served_name": "q"}})
            raise MS.urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
        with mock.patch.object(MS, "_get", fake_get):
            st = MS.probe(self.bk("http://stewart-serve.example.ts.net:8000/v1"))
        self.assertEqual(st["pod"], {"model": "model-a-w4a16", "adapter": "hyperqwen", "served_name": "q"})
        self.assertEqual(st["state"], "loading")
        self.assertIn("http://stewart-serve.example.ts.net:8001/status", seen)

    def test_explain(self):
        k, t = MS.explain(BackendError("연결 실패: <urlopen error [Errno 61] Connection refused>"))
        self.assertEqual(k, "down")
        self.assertIn("아직 안 올라왔다", t)
        self.assertNotIn("urlopen", t)
        self.assertEqual(MS.explain(BackendError("HTTP 503", 503, '{"error":{"message":"Loading model"}}'))[0], "loading")
        self.assertEqual(MS.explain(BackendError("HTTP 401", 401, ""))[0], "auth")
        self.assertEqual(MS.explain(BackendError("연결 실패: timed out"))[0], "timeout")

    def test_watch_poke_changes_dot_now(self):
        w = MS.ModelWatch(self.bk("http://127.0.0.1:1/v1"), probe_fn=lambda b: {"state": "ok", "label": "준비됨"})
        w.check()
        self.assertEqual(w.status["state"], "ok")
        w.poke("down", "연결 거부")
        self.assertEqual((w.status["state"], w.status["label"]), ("down", "닿지 않음"))   # 찔러 보기 전에도 곧바로


class TBackendErrorLine(MainBase):
    def test_one_line_and_retry(self):
        self.main.sess.backend.base_url = f"http://127.0.0.1:{free_port()}/v1"
        self.main.send("시험 돌려")
        self.assertTrue(self.main.wait_idle(20))
        log = self.main.state()["main_log"]
        errs = [m for m in log if m["kind"] == "error"]
        self.assertEqual(len(errs), 1)
        self.assertEqual((errs[0]["cls"], errs[0]["retry"]), ("down", "시험 돌려"))
        self.assertIn("urlopen", errs[0]["detail"])                       # 옛 글은 자세히로
        self.assertFalse([m for m in log if m["kind"] == "warn" and "백엔드 오류" in m["text"]])
        ends = [m for m in log if m["kind"] == "turn_end"]
        self.assertEqual(ends[-1]["reason"], "backend_error")
        self.assertTrue([m for m in log if m["kind"] == "meta"])            # 요약 줄은 meta(화면 대화에선 숨김)
        self.assertEqual(self.main.state()["model"]["state"], "down")      # 오류 직후 점이 바뀐다


class TLogin(unittest.TestCase):
    def test_token_reuse(self):
        d = Path(tempfile.mkdtemp())
        a = make_token(d, reuse=True)
        self.assertEqual(make_token(d, reuse=True), a)
        self.assertEqual((d / "hub-token").stat().st_mode & 0o777, 0o600)
        self.assertNotEqual(make_token(d), a)                             # --new-token

    def test_hub_url(self):
        d = Path(tempfile.mkdtemp())
        self.assertEqual(hub_url(d), 1)
        (d / "hub-url").write_text("http://127.0.0.1:9999/\n")
        tok = make_token(d)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(hub_url(d), 0)
        self.assertIn("접속: http://127.0.0.1:9999/", out.getvalue())
        self.assertIn(tok, out.getvalue())


class TLoginKept(MainBase):
    def serve(self, token, state_dir):
        web = WebApp(self.main, self.hub, token, state_dir=state_dir)
        web.serve()
        self.addCleanup(web.close)
        return web

    def req(self, web, method, path, body=None, headers=None):
        host = f"127.0.0.1:{web.port}"
        c = http.client.HTTPConnection("127.0.0.1", web.port, timeout=10)
        c.request(method, path, body=body, headers={"Host": host, **(headers or {})})
        r = c.getresponse()
        r.read()
        c.close()
        return r

    def test_login_survives_restart(self):
        sd = self.tmp / "hubstate"
        sd.mkdir()
        tok = make_token(sd, reuse=True)
        w1 = self.serve(tok, sd)
        r = self.req(w1, "POST", "/login", urlencode({"token": tok}),
                     {"Content-Type": "application/x-www-form-urlencoded", "Origin": f"http://127.0.0.1:{w1.port}"})
        cookie = r.getheader("Set-Cookie")
        self.assertIn("Max-Age=", cookie)
        ck = cookie.split(";")[0]
        self.assertEqual((sd / "web-sessions.json").stat().st_mode & 0o777, 0o600)
        w2 = self.serve(make_token(sd, reuse=True), sd)                    # 허브를 다시 켬
        self.assertEqual(self.req(w2, "GET", "/api/state", headers={"Cookie": ck}).status, 200)
        w3 = self.serve(make_token(sd), sd)                                # --new-token: 예전 로그인 끝
        self.assertEqual(self.req(w3, "GET", "/api/state", headers={"Cookie": ck}).status, 401)


if __name__ == "__main__":
    unittest.main()
