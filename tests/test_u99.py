"""판 121: STEWART_FETCH_ALLOW_HOSTS — 고정 말뭉치 평가용. 비면 지금과 같음, 목록 호스트[:포트]만 사설·루프백 막기를 건너뜀,
목록 밖 사설 주소·리다이렉트 우회는 계속 막음, 지문에 개수만(이름 없음)."""

import os
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from stewart import features as FT, webfetch as W


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/doc/1":
            body = "문서 하나".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/away":
            self.send_response(302)
            self.send_header("Location", "http://169.254.0.1/secret")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


class TAllow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_parse(self):
        self.assertEqual(W.allow_hosts({}), set())
        self.assertEqual(W.allow_hosts({W.ENV_ALLOW: "Corpus.Local:8000, docs.lan  *.x 169.254.0.0/16"}),
                         {("corpus.local", 8000), ("docs.lan", None)})        # 별표·CIDR 은 버린다

    def test_empty_is_unchanged(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(W.ENV_ALLOW, None)
            with self.assertRaises(W.FetchError) as cm:
                W.get(f"http://127.0.0.1:{self.port}/doc/1", timeout_s=5)
            self.assertIn("사설·내부 주소", str(cm.exception))
            self.assertNotIn("fetch_allow_hosts", FT.fingerprint("s", [], "run"))

    def test_listed_host_only(self):
        with mock.patch.dict(os.environ, {W.ENV_ALLOW: f"127.0.0.1:{self.port}"}):
            r = W.get(f"http://127.0.0.1:{self.port}/doc/1", timeout_s=5)
            self.assertEqual(r["status"], 200)
            self.assertIn("문서 하나", r["body"].decode())
            with self.assertRaises(W.FetchError):                              # 같은 호스트라도 다른 포트는 막힘
                W.get(f"http://127.0.0.1:{self.port + 1}/doc/1", timeout_s=5)
            with self.assertRaises(W.FetchError) as cm:                        # 리다이렉트로 목록 밖 사설 주소 — 막힘
                W.get(f"http://127.0.0.1:{self.port}/away", timeout_s=5)
            self.assertIn("169.254.0.1", str(cm.exception))
            f = FT.fingerprint("s", [], "run")
            self.assertEqual(f["fetch_allow_hosts"], 1)
            self.assertNotIn("127.0.0.1", str(f))                               # 이름은 남기지 않는다

    def test_name_resolving_to_private(self):   # 목록에 없는 이름이 사설 IP 로 풀리면 막힘(재결합 막기 그대로)
        with mock.patch.dict(os.environ, {W.ENV_ALLOW: "corpus.local:80"}), \
                mock.patch.object(socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("100.64.0.5", 80))]):
            self.assertEqual(W.resolve("corpus.local", 80, allowed=True), "100.64.0.5")
            with self.assertRaises(W.FetchError):
                W.resolve("other.local", 80, allowed=False)


if __name__ == "__main__":
    unittest.main()
