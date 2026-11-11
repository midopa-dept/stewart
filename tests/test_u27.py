"""U2-7: 허브 사용자 계정(기본 끔) — 저장·해시, 실패 지연·만료, 사람별 나눔, CSRF·나가기·막기, 듣는 주소, 관리 명령."""

import http.client
import json
import os
import stat
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

from stewart import accounts as A
from stewart.web import WebApp
from tests.test_hub import msg
from tests.test_hub3 import MainBase


class TStore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.st = A.Store(self.tmp)

    def test_hash_not_plain_and_verify(self):
        self.st.add("alice", "correct horse")
        raw = (self.tmp / "accounts.json").read_text()
        self.assertNotIn("correct horse", raw)
        u = json.loads(raw)["users"]["alice"]
        self.assertEqual((len(bytes.fromhex(u["salt"])), u["kdf"]["n"]), (16, 2 ** 14))
        self.assertTrue(self.st.verify("alice", "correct horse"))
        self.assertFalse(self.st.verify("alice", "wrong pass!"))
        self.assertFalse(self.st.verify("nobody", "correct horse"))
        self.st.add("bob", "correct horse")
        self.assertNotEqual(self.st.users()["alice"]["hash"], self.st.users()["bob"]["hash"])   # 사용자별 소금
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE((self.tmp / "accounts.json").stat().st_mode), 0o600)

    def test_rules_passwd_disable(self):
        for bad in ("Alice", "a b", "", "-x", "x" * 33):
            with self.assertRaises(A.AccountError):
                self.st.add(bad, "longenough")
        with self.assertRaises(A.AccountError):
            self.st.add("carol", "short")
        self.st.add("carol", "first-pass")
        with self.assertRaises(A.AccountError):
            self.st.add("carol", "again-pass")
        self.st.set_password("carol", "second-pass")
        self.assertFalse(self.st.verify("carol", "first-pass"))
        self.assertTrue(self.st.verify("carol", "second-pass"))
        self.st.set_disabled("carol", True)
        self.assertFalse(self.st.verify("carol", "second-pass"))
        self.st.set_disabled("carol", False)
        self.assertTrue(self.st.verify("carol", "second-pass"))


class TSessions(unittest.TestCase):
    def test_delay_expiry_disable(self):
        st = A.Store(Path(tempfile.mkdtemp()))
        st.add("alice", "correct horse")
        t = [1000.0]
        ss = A.Sessions(st, now=lambda: t[0])
        self.assertEqual(ss.delay_for("alice", "1.2.3.4"), 0.0)
        for want in (0.5, 1.0, 2.0):
            self.assertIsNone(ss.login("alice", "nope-nope", "1.2.3.4"))
            self.assertEqual(ss.delay_for("alice", "1.2.3.4"), want)
        self.assertEqual(ss.delay_for("someone", "1.2.3.4"), 2.0)          # 같은 주소의 다른 이름도 지연
        self.assertEqual(ss.delay_for("alice", "9.9.9.9"), 2.0)            # 같은 이름의 다른 주소도 지연
        for _ in range(20):
            ss.login("alice", "nope-nope", "1.2.3.4")
        self.assertEqual(ss.delay_for("alice", "1.2.3.4"), A.FAIL_DELAY_MAX)
        sid = ss.login("alice", "correct horse", "1.2.3.4")
        self.assertTrue(sid)
        self.assertEqual(ss.delay_for("alice", "1.2.3.4"), 0.0)            # 성공하면 셈을 지운다
        self.assertEqual(ss.get(sid)["user"], "alice")
        t[0] += A.SESSION_IDLE_S + 1
        self.assertIsNone(ss.get(sid))                                      # 쉼 만료
        sid = ss.login("alice", "correct horse", "x")
        for _ in range(6):                                                   # 2시간 안쪽마다 쓰면 11.9시간까지 산다
            t[0] += A.SESSION_IDLE_S - 60
            self.assertIsNotNone(ss.get(sid))
        t[0] += A.SESSION_IDLE_S - 60
        self.assertIsNone(ss.get(sid))                                      # 절대 만료(12시간)
        sid = ss.login("alice", "correct horse", "x")
        st.set_disabled("alice", True)
        self.assertIsNone(ss.get(sid))                                      # 막으면 바로 쫓겨난다
        st.set_disabled("alice", False)
        sid = ss.login("alice", "correct horse", "x")
        ss.logout(sid)
        self.assertIsNone(ss.get(sid))


class TListen(unittest.TestCase):
    def test_webapp_host_rules(self):
        with self.assertRaises(ValueError):
            WebApp(None, None, "t", host="10.0.0.5")                       # 계정 없이 밖 주소는 안 된다
        with self.assertRaises(ValueError):
            WebApp(None, None, None, host="0.0.0.0", accounts=object(), users=object())   # 모든 주소는 안 된다

    def test_run_hub_refuses(self):
        from stewart.hubmain import run_hub
        tmp = Path(tempfile.mkdtemp())
        a = types.SimpleNamespace(config=str(tmp / "hub.toml"), project=str(tmp), state_dir=str(tmp / "st"), runs_dir=str(tmp / "runs"),
                                  profile="chat", backend=None, mode="edits", worker_profile=None, port=0, new_main=False)
        cases = [('[hub]\nlisten = "10.0.0.5"\n', "계정 모드"), ('[hub]\naccounts = true\n', "user_root"),
                 (f'[hub]\naccounts = true\nuser_root = "{tmp / "u"}"\n', "사용자가 없다")]
        for toml, why in cases:
            (tmp / "hub.toml").write_text(toml, encoding="utf-8")
            err = []
            with mock.patch.object(sys, "stderr", types.SimpleNamespace(write=err.append, flush=lambda: None)):
                self.assertEqual(run_hub(a), 2, toml)
            self.assertIn(why, "".join(err))


class TCli(unittest.TestCase):
    def test_hub_user_cli(self):
        from stewart import cli
        tmp = Path(tempfile.mkdtemp())
        base = ["hub-user", "--state-dir", str(tmp), "--config", str(tmp / "none.toml")]
        with mock.patch.object(sys, "stdin", types.SimpleNamespace(readline=lambda: "good-password\n")):
            self.assertEqual(cli.main(base[:1] + ["add", "dana"] + base[1:] + ["--password-stdin"]), 0)
        self.assertTrue(A.Store(tmp).verify("dana", "good-password"))
        self.assertEqual(cli.main(base[:1] + ["disable", "dana"] + base[1:]), 0)
        self.assertFalse(A.Store(tmp).verify("dana", "good-password"))
        self.assertEqual(cli.main(base[:1] + ["passwd", "nope"] + base[1:] + ["--password-stdin"]), 2)


class TAccountsWeb(MainBase):
    """두 사람(alice·bob)이 같은 허브에 로그인해 서로의 것을 못 본다."""

    def setUp(self):
        super().setUp()
        from stewart.hubmain import UserHubs
        self.st = A.Store(self.tmp / "acc")
        self.st.add("alice", "alice-pass")
        self.st.add("bob", "bob-pass1")
        a = types.SimpleNamespace(profile=str(self.tmp / "main.toml"), backend="fake", mode="edits",
                                  worker_profile=str(self.tmp / "w.toml"))
        self.users = UserHubs(self.tmp / "acc", self.tmp / "runs", self.tmp / "people", a)
        self.addCleanup(self.users.close_all)
        self.sleeps = []
        p = mock.patch("stewart.web.time.sleep", side_effect=lambda s: self.sleeps.append(s))
        p.start()
        self.addCleanup(p.stop)
        self.web = WebApp(None, None, None, port=0, accounts=A.Sessions(self.st), users=self.users)
        self.web.serve()
        self.addCleanup(self.web.close)
        self.host = f"127.0.0.1:{self.web.port}"

    def req(self, method, path, body=None, cookie=None, csrf=None, form=False):
        c = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=10)
        h = {"Host": self.host, "Origin": f"http://{self.host}"}
        if cookie:
            h["Cookie"] = f"stewart_hub={cookie}"
        if csrf:
            h["X-Stewart-Token"] = csrf
        data = None
        if body is not None:
            data = urlencode(body).encode() if form else json.dumps(body).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        return r, r.read()

    def login(self, user, pw):
        r, _ = self.req("POST", "/login", {"user": user, "password": pw}, form=True)
        if r.status != 303:
            return None, None
        sid = r.getheader("Set-Cookie").split(";")[0].split("=", 1)[1]
        r, page = self.req("GET", "/", cookie=sid)
        import re
        csrf = re.search(r'name="csrf" content="([^"]+)"', page.decode()).group(1)
        self.assertIn(user, page.decode())                                  # 머리에 사용자 이름
        return sid, csrf

    def test_login_isolation_csrf_logout(self):
        r, _ = self.req("GET", "/")
        self.assertEqual((r.status, r.getheader("Location")), (303, "/login"))
        self.assertEqual(self.login("alice", "wrong-pass")[0], None)
        self.assertEqual(self.login("alice", "wrong-pass")[0], None)
        self.assertEqual(self.sleeps[-1], 0.5)                              # 둘째 실패 앞에 지연
        a_sid, a_csrf = self.login("alice", "alice-pass")
        b_sid, b_csrf = self.login("bob", "bob-pass1")
        self.assertTrue(a_sid and b_sid)
        # 작업 폴더: 사람마다 따로
        (self.tmp / "people" / "bob" / "bob-secret.txt").write_text("밥 것")
        (self.tmp / "people" / "alice" / "alice-note.txt").write_text("앨리스 것")
        files_a = json.loads(self.req("GET", "/api/files?q=", cookie=a_sid)[1])["files"]
        self.assertEqual(files_a, ["alice-note.txt"])
        # 메인 대화도 따로
        self.r.scripts["MAIN"] = [msg("앨리스에게 답")]
        r, _ = self.req("POST", "/api/main", {"text": "앨리스 말"}, cookie=a_sid, csrf=a_csrf)
        self.assertEqual(r.status, 200)
        alice_main = self.users.get("alice")[0]
        self.assertTrue(alice_main.wait_idle())
        st_a = json.loads(self.req("GET", "/api/state", cookie=a_sid)[1])
        st_b = json.loads(self.req("GET", "/api/state", cookie=b_sid)[1])
        self.assertIn("앨리스 말", json.dumps(st_a, ensure_ascii=False))
        self.assertNotIn("앨리스 말", json.dumps(st_b, ensure_ascii=False))
        self.assertNotIn("앨리스", json.dumps(json.loads(self.req("GET", "/api/session/conversation?name=main", cookie=b_sid)[1]),
                                              ensure_ascii=False))
        # 남의 폴더를 작업 폴더로 열기 거절
        r, body = self.req("POST", "/api/session/new", {"name": "w1", "task": "x", "workdir": str(self.tmp / "people" / "bob")},
                           cookie=a_sid, csrf=a_csrf)
        self.assertEqual(r.status, 400, body)
        # CSRF: 남의 토큰·토큰 없음 거절
        self.assertEqual(self.req("POST", "/api/main", {"text": "x"}, cookie=a_sid, csrf=b_csrf)[0].status, 403)
        self.assertEqual(self.req("POST", "/api/main", {"text": "x"}, cookie=a_sid)[0].status, 403)
        # 나가기 뒤에는 그 쿠키가 안 된다
        r, _ = self.req("POST", "/logout", {}, cookie=a_sid, csrf=a_csrf)
        self.assertEqual(r.status, 200)
        self.assertEqual(self.req("GET", "/api/state", cookie=a_sid)[0].status, 401)
        # 막으면 bob 의 세션이 바로 끊긴다
        self.st.set_disabled("bob", True)
        self.assertEqual(self.req("GET", "/api/state", cookie=b_sid)[0].status, 401)

    def test_user_folder_names(self):
        with self.assertRaises(A.AccountError):
            self.users.folder("../alice")


if __name__ == "__main__":
    unittest.main()


class TAccountsMemory(TAccountsWeb):
    """계정 모드: 스스로 기억(판 50)도 사람마다 따로 — 같은 OS 계정을 여럿이 써도 섞이지 않는다."""

    def test_login_isolation_csrf_logout(self):   # 부모 시험은 되풀이하지 않는다
        pass

    def test_user_folder_names(self):
        pass

    def test_memory_per_person(self):
        with mock.patch.dict(os.environ, {"STEWART_MEMORY": "1"}):
            a_main, _ = self.users.get("alice")
            b_main, _ = self.users.get("bob")
        self.assertNotEqual(a_main.sess.mem.dir, b_main.sess.mem.dir)
        self.assertIn("users/alice/memory", str(a_main.sess.mem.dir))
        a_main.sess.mem.remember("feedback", "앞으로는 보고를 짧게")
        self.assertEqual(b_main.sess.mem.remembered(), [])
