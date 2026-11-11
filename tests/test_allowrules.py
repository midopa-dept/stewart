"""셸 허용 규칙: 꼴·나누기·출처(사람이 준 allow 만)·deny 우선·허브 "늘 허용"·ttft_s."""

import http.client
import json
import os
import re
import shutil
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

from stewart import allowrules as AR, chat as C, hub as H, projcfg as PC
from stewart.config import Backend, Profile
from stewart.record import Recorder
from stewart.web import WebApp, make_token
from tests.test_engine import _resp
from tests.test_hub import Base as HubBase, msg, wait
from tests.test_hub3 import MainBase


def w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def R(pat, eff="allow", src="user"):
    return AR.Rule(AR.parse_pattern(pat), eff, src)


class TPattern(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(AR.parse_pattern("  npm   test "), "npm test")
        self.assertEqual(AR.parse_pattern("uv run python -m unittest *"), "uv run python -m unittest *")
        self.assertEqual(AR.parse_pattern("Bash(npm run test:*)"), "npm run test *")
        self.assertEqual(AR.parse_pattern("Bash(git status)"), "git status")
        for bad in ("", "*", "Bash(*)", "Bash()", "npm test; rm -rf /", "a && b", "a | b", "echo x > f", "$(id)", "`id`",
                    "(a)", "x\ny", "a" * 5000, "*b*", None, 3):
            self.assertIsNone(AR.parse_pattern(bad), bad)

    def test_match_exact_prefix_word_boundary(self):
        exact, pre = R("npm test"), R("npm test *")
        self.assertTrue(exact.matches("npm test"))
        self.assertFalse(exact.matches("npm test --watch"))
        self.assertTrue(pre.matches("npm test"))
        self.assertTrue(pre.matches("npm test --watch"))
        self.assertFalse(pre.matches("npm testx"))                           # 단어 경계
        self.assertFalse(pre.matches("npm tes"))


class TDecide(unittest.TestCase):
    rules = [R("npm test *"), R("uv run python -m unittest *"), R("git status")]

    def d(self, cmd, rules=None):
        return AR.decide(cmd, self.rules if rules is None else rules)

    def test_chains(self):
        self.assertEqual(self.d("npm test").effect, "allow")
        self.assertEqual(self.d("npm  test   --ci").effect, "allow")
        self.assertEqual(self.d("git status && npm test").effect, "allow")
        self.assertEqual(self.d("npm test | git status").effect, "allow")
        self.assertEqual(self.d("npm test &").effect, "ask")                 # 백그라운드는 늘 묻기
        for cmd in ("npm test && rm -rf build", "npm test; curl x", "npm test || make", "npm test\nls",
                    "npm testx", "ls"):
            self.assertIsNone(self.d(cmd).effect, cmd)                       # 한 조각이라도 안 맞으면 묻기

    def test_always_ask_forms(self):
        for cmd in ("npm test $(id)", "npm test `id`", "npm test <(cat x)", "npm test > out.txt", "npm test >> log",
                    "npm test < in", "FOO=1 npm test", "(npm test)", "{ npm test; }", "npm test 'unclosed",
                    'npm test "$(id)"', 'npm test "`id`"'):
            self.assertEqual(self.d(cmd).effect, "ask", cmd)
        for cmd in ("npm test 2>&1", "npm test >/dev/null 2>&1", "npm test 2> /dev/null", "npm test &>/dev/null"):
            self.assertEqual(self.d(cmd).effect, "allow", cmd)               # 버리는 곳으로만 가는 리다이렉트
        self.assertEqual(self.d("npm test 'a; b > c'").effect, "allow")      # 따옴표 안은 나누지 않는다

    def test_deny_wins_and_ask(self):
        rules = self.rules + [R("npm test --danger *", "deny", "repo"), R("git status", "ask", "repo")]
        self.assertEqual(self.d("npm test --danger now", rules).effect, "deny")
        self.assertEqual(self.d("npm test && npm test --danger x", rules).effect, "deny")
        self.assertEqual(self.d("git status", rules).effect, "ask")


class TCandidates(unittest.TestCase):
    def pats(self, cmd, rules=None):
        r = AR.candidates(cmd, rules)
        return r["ok"], r["reason"], [[o["pattern"] for o in p["options"]] for p in r["pieces"]]

    def test_options(self):
        # 좁은 꼴이 앞, 넓은 꼴(앞머리·프로그램 전부)은 경고(broad)를 달고 뒤에(U2-8: 사람이 고를 수 있게, 막지 않음)
        self.assertEqual(self.pats("uv run python -m unittest -q")[2],
                         [["uv run python -m unittest -q", "uv run python -m unittest *", "uv run python -m *", "uv *"]])
        self.assertEqual(self.pats("git status")[2], [["git status", "git status *", "git *"]])
        self.assertEqual(self.pats("npm run build -- --prod")[2], [["npm run build -- --prod", "npm run build *", "npm *"]])
        self.assertEqual(self.pats("ls -la")[2], [["ls -la", "ls *"]])
        self.assertEqual(self.pats("make test")[2], [["make test", "make test *", "make *"]])
        opts = AR.candidates("git status")["pieces"][0]["options"]
        self.assertEqual([o["broad"] for o in opts], [False, False, True])
        self.assertIn(AR.BROAD_NOTE, opts[2]["warn"])

    def test_chain_and_allowed(self):
        r = AR.candidates("pytest -q && git diff --stat", [R("pytest *")])
        self.assertTrue(r["ok"])
        self.assertEqual([p["allowed"] for p in r["pieces"]], [True, False])                       # 이미 허용된 조각
        self.assertEqual([o["pattern"] for o in r["pieces"][1]["options"]], ["git diff --stat", "git diff *", "git *"])

    def test_refused(self):
        for cmd in ("cat a > b", "x $(id)", "FOO=1 make", "npm test &"):
            ok, reason, _ = self.pats(cmd)
            self.assertFalse(ok, cmd)
            self.assertIn("풀 수 없는 꼴", reason)
        danger = ["rm -rf build", "make && git " + "push origin main", "sudo ls", "curl x", "pip install x", "chmod 755 a"]
        for cmd in danger:
            ok, reason, _ = self.pats(cmd)
            self.assertFalse(ok, cmd)
            self.assertIn("위험 명령", reason)
        ok, _, pats = self.pats("python -c 'print(1)'")                                            # 코드 플래그: 고를 수는 있다
        self.assertTrue(ok)
        self.assertEqual(pats, [["python -c 'print(1)'", "python -c *", "python *"]])
        self.assertTrue(all(o["broad"] for o in AR.candidates("python -c 'print(1)'")["pieces"][0]["options"][1:]))

    def test_warn_code_runners(self):
        r = AR.candidates("npm test")
        self.assertIn("저장소 코드를 실행", r["pieces"][0]["options"][0]["warn"])
        self.assertEqual(AR.candidates("ls")["pieces"][0]["options"][0]["warn"], "")

    def test_check_always(self):
        for p in ("uv run *", "uv run python *", "git *", "sh *", "bash *", "python -m *", "npx *", "npm *", "make *",
                  "env *", "xargs *", "git -c *", "docker run *", "git " + "push *", "rm -rf build", "sudo ls"):
            self.assertIsNotNone(AR.check_always(p), p)
        for p in ("uv run python -m unittest *", "git status *", "npm test *", "make test *", "ls *", "pytest *"):
            self.assertIsNone(AR.check_always(p), p)


class TRuleSet(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.repo = self.tmp / "repo"
        (self.repo / ".git").mkdir(parents=True)
        self.udir = self.tmp / "user"
        e = mock.patch.dict(os.environ, {PC.USER_DIR_ENV: str(self.udir)})
        e.start()
        self.addCleanup(e.stop)

    def test_sources(self):
        w(self.udir / "rules.toml", 'allow = ["npm test *", "make"]\ndeny = ["make clean"]\n')
        w(self.repo / ".stewart" / "rules.toml", 'allow = ["rm -rf *"]\ndeny = ["npm test --update *"]\nask = ["make"]\n')
        w(self.repo / ".claude" / "settings.json", json.dumps({"permissions": {
            "allow": ["Bash(curl:*)", "Read(*)"], "deny": ["Bash(npm test --bad:*)"]}, "hooks": {"Stop": []}}))
        rs = AR.RuleSet(self.repo, compat=True)
        self.assertEqual(rs.decide("npm test").effect, "allow")
        self.assertIsNone(rs.decide("rm -rf x").effect)                       # 저장소 allow 무시
        self.assertIsNone(rs.decide("curl x").effect)                         # .claude allow 무시
        self.assertEqual(rs.decide("npm test --update now").effect, "deny")   # 저장소 deny 반영, allow 를 이긴다
        self.assertEqual(rs.decide("npm test --bad").effect, "deny")          # .claude deny 반영
        self.assertEqual(rs.decide("make").effect, "ask")                     # 저장소 ask 가 사용자 allow 를 묻기로
        self.assertEqual(rs.decide("make clean").effect, "deny")
        ign = {(x.get("pattern"), x["why"]) for x in rs.ignored if x.get("effect") == "allow"}
        self.assertIn(("rm -rf *", "저장소 파일의 allow 는 무시"), ign)
        self.assertIn(("curl *", "저장소 파일의 allow 는 무시"), ign)
        self.assertNotEqual(AR.RuleSet(self.repo, compat=False).decide("npm test --bad").effect, "deny")   # 호환 끔이면 .claude 안 읽음

    def test_broken_files_ignored(self):
        w(self.udir / "rules.toml", "allow = [")
        w(self.repo / ".claude" / "settings.json", "{not json")
        rs = AR.RuleSet(self.repo, compat=True)
        self.assertIsNone(rs.decide("npm test").effect)
        self.assertEqual(len([x for x in rs.ignored if x["why"] == "읽을 수 없는 파일"]), 2)

    def test_hub_store_outside_repo(self):
        st = AR.HubStore(self.tmp / "hubdir")
        r = st.add("uv run python -m unittest *")
        self.assertEqual(st.path, self.tmp / "hubdir" / "allow-rules.json")
        self.assertNotIn(str(self.repo), str(st.path))
        self.assertEqual(stat.S_IMODE(os.stat(st.path).st_mode), 0o600)
        self.assertEqual([x.pattern for x in st.load()], ["uv run python -m unittest *"])
        st.add("uv run python -m unittest *")
        self.assertEqual(len(st.load()), 1)
        with self.assertRaises(ValueError):
            st.add("make && rm -rf /")
        with self.assertRaises(ValueError):
            st.add("uv run *")                                                    # 넓은 꼴은 저장도 거부
        self.assertTrue(st.remove(r.id))
        self.assertEqual(st.load(), [])


class TChat(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.udir = self.tmp / "user"
        e = mock.patch.dict(os.environ, {PC.USER_DIR_ENV: str(self.udir)})
        e.start()
        self.addCleanup(e.stop)
        w(self.udir / "rules.toml", 'allow = ["/bin/echo ok *"]\ndeny = ["/bin/echo no *"]\n')

    def sess(self):
        prof = Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["run_shell"], workdir=self.tmp, max_steps=4)
        ui = C.UI(out=open(os.devnull, "w"))
        self.addCleanup(ui.out.close)
        s = C.ChatSession(prof, Backend("fake", "http://x/v1", "m", "q", 100000), Recorder(self.tmp / "runs", "chat-t"), ui=ui,
                          mode="edits")
        self.addCleanup(s.close)
        return s, ui

    def test_allow_deny_ask(self):
        s, ui = self.sess()
        asked = []
        ui.ask = lambda p: asked.append(p) or "n"
        seq = [_resp(calls=[("run_shell", json.dumps({"command": "/bin/echo ok one"}))]),
               _resp(calls=[("run_shell", json.dumps({"command": "/bin/echo no two"}))]),
               _resp(calls=[("run_shell", json.dumps({"command": "/bin/echo ok && /bin/echo other"}))]), _resp("끝")]
        with mock.patch.object(C, "chat", side_effect=seq):
            s.turn("셸")
        self.assertEqual(len(asked), 1)                                      # 셋째(한 조각만 맞음)만 물음
        tool_msgs = [m["content"] for m in s.messages if m["role"] == "tool"]
        self.assertIn("ok one", tool_msgs[0])
        self.assertIn("deny", tool_msgs[1])
        ev = [json.loads(l) for l in (s.rec.dir / "events.jsonl").read_text().splitlines()]
        comp = [e for e in ev if e["kind"] == "component" and e["name"] == "allow_rule"]
        self.assertEqual([e["action"] for e in comp], ["allow", "block"])
        self.assertEqual(comp[0]["detail"]["source"], "user")
        self.assertNotIn("echo", json.dumps([e["detail"] for e in comp]))   # 명령 원문 없음

    def test_auto_mode_deny_only(self):
        s, ui = self.sess()
        s.set_mode("auto")
        ui.ask = lambda p: (_ for _ in ()).throw(AssertionError("auto 는 묻지 않는다"))
        seq = [_resp(calls=[("run_shell", json.dumps({"command": "/bin/echo no x"}))]),
               _resp(calls=[("run_shell", json.dumps({"command": "/bin/echo free y"}))]), _resp("끝")]
        with mock.patch.object(C, "chat", side_effect=seq):
            s.turn("셸")
        tool_msgs = [m["content"] for m in s.messages if m["role"] == "tool"]
        self.assertIn("deny", tool_msgs[0])                                  # auto 에서도 deny 는 거부
        self.assertNotIn("no x\n", tool_msgs[0])
        self.assertIn("free y", tool_msgs[1])                                # 규칙 없는 명령은 auto 대로 실행
        ev = [json.loads(l) for l in (s.rec.dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual([e["action"] for e in ev if e["kind"] == "component" and e["name"] == "allow_rule"], ["block"])

    def test_engine_run_deny_and_allow(self):
        from stewart import engine
        prof = Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["run_shell"], workdir=self.tmp, max_steps=4)
        seq = [_resp(calls=[("run_shell", json.dumps({"command": "/bin/echo no z"}))]),
               _resp(calls=[("run_shell", json.dumps({"command": "/bin/echo ok w"}))]), _resp("끝")]
        sent = []
        with mock.patch.object(engine, "chat", side_effect=lambda *a, **k: (sent.append(json.loads(json.dumps(a[1]))), seq[len(sent) - 1])[1]):
            summ = engine.run(prof, Backend("fake", "http://x/v1", "m", "q", 100000), "셸", Recorder(self.tmp / "er", "t"))
        tools = [m["content"] for m in sent[-1] if m["role"] == "tool"]
        self.assertIn("deny", tools[0])                                      # stewart run(auto)에서도 deny
        self.assertIn("ok w", tools[1])
        self.assertEqual(summ["components"]["allow_rule"], {"fired": 1, "actions": {"block": 1}})

    def test_ttft(self):
        s, ui = self.sess()

        def fake_stream(backend, messages, tools, on_delta=None, on_first=None, **k):
            time.sleep(0.05)
            on_first()
            on_delta("안", "")
            on_delta("녕", "")
            return _resp("안녕")[0], 0.1
        s.stream = True
        with mock.patch.object(C, "chat_stream", side_effect=fake_stream):
            s.turn("hi")
        with mock.patch.object(C, "chat", side_effect=[_resp("끝")]):
            s.stream = False
            s.turn("hi")
        steps = [json.loads(l) for l in (s.rec.dir / "events.jsonl").read_text().splitlines() if '"kind": "step"' in l]
        self.assertGreaterEqual(steps[0]["response"]["ttft_s"], 0.04)
        self.assertIsNone(steps[1]["response"]["ttft_s"])


class THubAllow(HubBase):
    def test_worker_gets_rules_only_via_pipe(self):
        self.fake.script = [msg(calls=[("run_shell", {"command": "touch a.txt && touch b.txt"})]), msg("하나"),
                            msg(calls=[("run_shell", {"command": "uname"})]), msg("둘"),
                            msg(calls=[("run_shell", {"command": "id"})]), msg("셋")]   # 읽기 기본 통과 목록 밖 명령
        self.spawn("셸", mode="edits")
        q = wait(lambda: self.hub.queue())
        self.assertEqual(q[0]["suggest"], "")                                # 리다이렉트가 든 명령은 제안하지 않음
        self.hub.answer("w1", 1, "y")
        self.hub.wait_report("w1", 1)
        self.hub.add_allow("uname")
        self.hub.send("w1", "둘")
        self.hub.wait_report("w1", 2, timeout_s=20)                         # 묻지 않고 돌았다
        self.assertEqual(self.hub.pending_asks("w1"), [])
        # 같은 계정의 셸이 규칙 파일에 써도 작업자는 그 파일을 읽지 않는다
        H.write_json(self.hub.allow.path, {"rules": [{"pattern": "id", "effect": "allow"}]})
        self.hub.send("w1", "셋")
        q = wait(lambda: self.hub.queue())
        self.assertEqual((q[0]["args_summary"]["command"], q[0]["suggest"]), ("id", "id"))
        self.hub.answer("w1", q[0]["seq"], "n")
        self.hub.wait_report("w1", 3)


class THubScopes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_session_vs_project(self):
        hub = H.Hub(self.tmp / "hub", self.tmp / "runs")
        self.addCleanup(hub.close)
        hub.add_allow("git status *")                                             # 기본 = 이 세션만
        hub.add_allow("npm test *", "project")
        self.assertEqual(sorted(r.pattern for r in hub.allow_rules()), ["git status *", "npm test *"])
        self.assertEqual([x.pattern for x in AR.HubStore(hub.dir).load()], ["npm test *"])   # 파일에는 프로젝트 것만
        with self.assertRaises(ValueError):
            hub.add_allow("sudo *")                                                # 위험 명령은 사람이 골라도 안 받는다
        with self.assertRaises(ValueError):
            hub.add_allow("git status *", "forever")
        hub2 = H.Hub(hub.dir, self.tmp / "runs")                                 # 허브를 다시 띄움
        self.addCleanup(hub2.close)
        self.assertEqual([r.pattern for r in hub2.allow_rules()], ["npm test *"])  # 이 세션만은 사라짐
        sid = next(r.id for r in hub.allow_rules() if r.pattern == "git status *")
        self.assertTrue(hub.remove_allow(sid))
        self.assertEqual([r.pattern for r in hub.allow_rules()], ["npm test *"])


class TWebAllow(MainBase):
    def setUp(self):
        super().setUp()
        self.token = make_token(self.hub.dir)
        self.web = WebApp(self.main, self.hub, self.token)
        self.web.serve()
        self.addCleanup(self.web.close)
        self.host = f"127.0.0.1:{self.web.port}"
        self.origin = f"http://{self.host}"

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=10)
        c.request(method, path, body=body, headers={"Host": self.host, **(headers or {})})
        r = c.getresponse()
        d = r.read()
        c.close()
        return r, d

    def login(self):
        r, _ = self.req("POST", "/login", urlencode({"token": self.token}),
                        {"Content-Type": "application/x-www-form-urlencoded", "Origin": self.origin})
        ck = r.getheader("Set-Cookie").split(";")[0]
        _, page = self.req("GET", "/", headers={"Cookie": ck})
        return ck, re.search(rb'name="csrf" content="([^"]+)"', page).group(1).decode()

    def test_always_allow_checks(self):
        self.r.scripts["MAIN"] = [msg(calls=[("run_shell", {"command": "uv run python -m unittest -q"})]), msg("돌렸다"),
                                  msg(calls=[("run_shell", {"command": "uv run python -m unittest tests.x"})]), msg("또 돌렸다")]
        self.main.send("시험")
        q = wait(lambda: self.hub.queue())
        self.assertEqual(q[0]["suggest"], "uv run python -m unittest -q")
        ck, csrf = self.login()
        good = {"Cookie": ck, "Origin": self.origin, "X-Stewart-Token": csrf, "Content-Type": "application/json"}
        body = lambda **k: json.dumps({"session": "main", "seq": 1, "answer": "y", **k})
        self.assertEqual(self.req("GET", "/api/answer?" + urlencode({"session": "main", "seq": 1, "answer": "y",
                                                                     "always": "uv run *"}), headers={"Cookie": ck})[0].status, 405)
        self.assertEqual(self.req("POST", "/api/answer", body(always="uv run *"), {**good, "X-Stewart-Token": ""})[0].status, 403)
        self.assertEqual(self.req("POST", "/api/answer", body(always="uv run *"), {**good, "Origin": "http://evil"})[0].status, 403)
        self.assertEqual(self.hub.allow_rules(), [])
        r, d = self.req("POST", "/api/answer", body(always="uv run && rm *"), good)
        self.assertEqual((r.status, json.loads(d)["error"]), (400, "pattern"))
        self.assertEqual(self.req("POST", "/api/answer", body(answer="n", always="uv run *"), good)[0].status, 400)
        self.assertTrue(self.main.busy)                                     # 아직 답하지 않았다
        r, d = self.req("POST", "/api/answer", body(always="uv run python -m unittest *"), good)
        self.assertEqual(r.status, 200)
        self.assertTrue(self.main.wait_idle())
        self.assertEqual([x.pattern for x in self.hub.allow_rules()], ["uv run python -m unittest *"])
        self.main.send("또")
        self.assertTrue(self.main.wait_idle())                               # 둘째는 묻지 않고 돌았다
        self.assertEqual(self.hub.queue(), [])
        r, d = self.req("GET", "/api/rules", headers={"Cookie": ck})
        rid = json.loads(d)["rules"][0]["id"]
        self.assertEqual(self.req("GET", "/api/rules/delete", headers={"Cookie": ck})[0].status, 405)
        self.assertEqual(self.req("POST", "/api/rules/delete", json.dumps({"id": rid}), {**good, "X-Stewart-Token": "x"})[0].status, 403)
        r, d = self.req("POST", "/api/rules/delete", json.dumps({"id": rid}), good)
        self.assertEqual(json.loads(d), {"ok": True})
        self.assertEqual(self.hub.allow_rules(), [])

    def test_picker_chain_and_scope(self):
        self.r.scripts["MAIN"] = [msg(calls=[("run_shell", {"command": "git status && git diff --stat"})]), msg("봤다"),
                                  msg(calls=[("run_shell", {"command": "git status -s && git diff --stat x"})]), msg("또 봤다")]
        self.main.send("상태")
        q = wait(lambda: self.hub.queue())
        al = q[0]["always"]
        self.assertTrue(al["ok"])
        self.assertEqual([p["piece"] for p in al["pieces"]], ["git status", "git diff --stat"])
        ck, csrf = self.login()
        good = {"Cookie": ck, "Origin": self.origin, "X-Stewart-Token": csrf, "Content-Type": "application/json"}
        body = lambda **k: json.dumps({"session": "main", "seq": 1, "answer": "y", **k})
        r, d = self.req("POST", "/api/answer", body(always=["git status *", "git push *"]), good)
        self.assertEqual((r.status, json.loads(d)["error"]), (400, "pattern"))   # 하나라도 위험 명령이면 아무것도 안 더함
        self.assertEqual(self.hub.allow_rules(), [])
        self.assertEqual(self.req("POST", "/api/answer", body(always=["git status *"], scope="forever"), good)[0].status, 400)
        r, d = self.req("POST", "/api/answer", body(always=["git status *", "git diff *"]), good)
        self.assertEqual(r.status, 200)
        self.assertTrue(self.main.wait_idle())
        self.main.send("또")
        self.assertTrue(self.main.wait_idle())
        self.assertEqual(self.hub.queue(), [])                                    # 두 조각 다 허용 → 묻지 않음
        rules = json.loads(self.req("GET", "/api/rules", headers={"Cookie": ck})[1])["rules"]
        self.assertEqual(sorted((x["pattern"], x["scope"]) for x in rules), [("git diff *", "session"), ("git status *", "session")])
        self.assertFalse(AR.HubStore(self.hub.dir).path.exists())                 # 이 세션만은 파일에 안 씀

    def test_queue_refusal_reason(self):
        self.r.scripts["MAIN"] = [msg(calls=[("run_shell", {"command": "rm -rf build"})]), msg("안 함")]
        self.main.send("지워")
        q = wait(lambda: self.hub.queue())
        self.assertEqual((q[0]["always"]["ok"], "위험 명령" in q[0]["always"]["reason"]), (False, True))
        self.hub.answer("main", 1, "n")
        self.assertTrue(self.main.wait_idle())

    def test_only_web_adds_rules(self):
        src = Path(H.__file__).parent
        hits = [(p.name, l.strip()) for p in sorted(src.rglob("*.py")) for l in p.read_text(encoding="utf-8").splitlines()
                if re.search(r"\.(add_allow|remove_allow)\(", l)]
        self.assertEqual(sorted(n for n, _ in hits), ["web.py"] * 4)   # 승인 칸·시험 명령 늘 허용(U2-2)·지우기·손으로 더하기(U2-8)


if __name__ == "__main__":
    unittest.main()
