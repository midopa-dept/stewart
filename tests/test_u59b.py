"""판 79: 로그인 — 포트별 쿠키(같은 브라우저의 두 허브), 허브 재시작 뒤에도 그대로, 풀린 까닭을 로그인 화면·기록에."""

import http.client
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

from stewart.web import COOKIE, WebApp, make_token


class Main:   # state() 만 있으면 되는 가짜 메인
    def state(self):
        return {"ok": True}

    def conv(self, name):
        return self


class Hub:
    def __init__(self):
        self.events = []

    def event(self, kind, **kw):
        self.events.append((kind, kw))


def login(web, token):
    c = http.client.HTTPConnection("127.0.0.1", web.port, timeout=10)
    host = f"127.0.0.1:{web.port}"
    c.request("POST", "/login", urlencode({"token": token}), {"Host": host, "Origin": f"http://{host}",
                                                               "Content-Type": "application/x-www-form-urlencoded"})
    r = c.getresponse(); r.read(); c.close()
    return r.getheader("Set-Cookie")


def get(web, path, cookie):
    c = http.client.HTTPConnection("127.0.0.1", web.port, timeout=10)
    c.request("GET", path, headers={"Host": f"127.0.0.1:{web.port}", "Cookie": cookie})
    r = c.getresponse(); body = r.read(); c.close()
    return r.status, r.getheader("Location"), body.decode("utf-8", "replace")


class TLogin(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.d, ignore_errors=True))

    def app(self, sub, port=0, hub=None, new_token=False):
        sd = self.d / sub
        sd.mkdir(exist_ok=True)
        tok = make_token(sd, reuse=not new_token)
        w = WebApp(Main(), hub or Hub(), tok, port=port, state_dir=sd)
        w.serve()
        return w, tok

    def test_two_hubs_one_browser(self):
        a, ta = self.app("a")
        b, tb = self.app("b")
        try:
            ca, cb = login(a, ta), login(b, tb)
            self.assertTrue(ca.startswith(f"{COOKIE}_{a.port}="))
            self.assertTrue(cb.startswith(f"{COOKIE}_{b.port}="))
            self.assertIn("Max-Age=", ca)
            jar = "; ".join([ca.split(";")[0], cb.split(";")[0]])   # 브라우저는 127.0.0.1 쿠키를 둘 다 보낸다
            self.assertEqual(get(a, "/api/state", jar)[0], 200)
            self.assertEqual(get(b, "/api/state", jar)[0], 200)
        finally:
            a.close(); b.close()

    def test_survives_restart_and_legacy_name(self):
        a, ta = self.app("a")
        port = a.port
        ck = login(a, ta).split(";")[0]
        a.close()
        a2, _ = self.app("a", port=port)
        try:
            self.assertEqual(get(a2, "/api/state", ck)[0], 200)          # 재시작 뒤 그대로
            legacy = COOKIE + "=" + ck.split("=", 1)[1]
            self.assertEqual(get(a2, "/api/state", legacy)[0], 200)      # 옛 이름(포트 없는)도 한 번은 통과
        finally:
            a2.close()

    def test_new_token_says_why(self):
        hub = Hub()
        a, ta = self.app("a")
        port = a.port
        ck = login(a, ta).split(";")[0]
        a.close()
        a2, _ = self.app("a", port=port, hub=hub, new_token=True)
        try:
            st, loc, _ = get(a2, "/", ck)
            self.assertEqual((st, loc), (303, "/login?why=token"))
            _, _, page = get(a2, loc, "")
            self.assertIn("토큰이 새로 만들어져", page)
            self.assertEqual(hub.events[0][0], "login_lost")
        finally:
            a2.close()

    def test_other_state_dir_says_why(self):
        a, ta = self.app("a")
        ck = login(a, ta).split(";")[0]
        b, _ = self.app("b-new")
        try:
            name, val = ck.split("=", 1)
            st, loc, _ = get(b, "/", f"{COOKIE}_{b.port}={val}")
            self.assertEqual(loc, "/login?why=new_state")
        finally:
            a.close(); b.close()




class TContext(unittest.TestCase):
    def test_budget(self):
        import os
        from unittest import mock
        from stewart.config import Backend
        b = Backend("x", "https://a.example/v1", "m", "api", 524288, context_budget=131072)
        self.assertEqual(b.budget(), 131072)
        self.assertEqual(Backend("x", "u", "m", "api", 8192, context_budget=131072).budget(), 8192)   # 창보다 클 수 없다
        with mock.patch.dict(os.environ, {"STEWART_CONTEXT_BUDGET": "65536"}):
            self.assertEqual(b.budget(), 65536)

    def test_chat_uses_budget_and_estimates(self):
        import json as _j
        from unittest import mock
        from stewart import chat as C
        from stewart.config import Backend, Profile
        from stewart.record import Recorder
        from tests.test_cloud import FakeUI
        tmp = Path(tempfile.mkdtemp())
        bk = Backend("x", "http://x/v1", "m", "api", 524288, context_budget=100000)
        p = Profile(name="chat", path=tmp / "p.toml", system="s" * 3000, tools=["read_file"], workdir=tmp, max_steps=3)
        s = C.ChatSession(p, bk, Recorder(tmp / "runs", "c"), FakeUI(), stream=False, mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        self.assertEqual(s.comp.full_context, 100000)            # 압축 문턱이 문맥 몫 기준
        resp = ({"choices": [{"message": {"role": "assistant", "content": "답"}, "finish_reason": "stop"}]}, 0.1)   # usage 없음
        with mock.patch.object(C, "chat", return_value=resp):
            s.turn("안녕")
        self.assertTrue(s.st["prompt_estimated"])
        self.assertGreater(s.st["last_prompt_tokens"], 500)


if __name__ == "__main__":
    unittest.main()
