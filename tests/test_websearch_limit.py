"""web_search 한도에 닿아도 정리 계획이 남는다. 남은 횟수·같은 검색어 되풀이·미확정 표시·끝 계획."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import engine, tools as T
from stewart.config import Backend, Profile, load_profile
from stewart.organize import plan as PL
from stewart.record import Recorder
from tests.test_engine import _resp


class FakeSearx:
    def __init__(self):
        self.queries = []

    def search(self, query, max_results=8):
        self.queries.append(query)
        return {"query": query, "results": [{"title": f"제목 {query}", "url": f"https://example.org/{len(self.queries)}",
                                             "content": "요약"}][:max_results], "error": None}


def ws(q):
    return ("web_search", json.dumps({"query": q}, ensure_ascii=False))


class TLimit(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fake = FakeSearx()
        self.env = T.ToolEnv(self.tmp, web={"url": "http://x", "max_total": 2, "max_results": 3})
        self.env.searx = self.fake

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_limit_is_guidance_not_error(self):
        ok1, r1 = T.web_search(self.env, "가 나")
        ok2, r2 = T.web_search(self.env, "다")
        self.assertTrue(ok1 and ok2)
        self.assertTrue(r1.endswith("(남은 검색 1/2)"))
        self.assertTrue(r2.endswith("(남은 검색 0/2)"))
        ok3, r3 = T.web_search(self.env, "라")
        self.assertTrue(ok3)                                        # 오류가 아니다(연속 오류로 실행이 끝나지 않게)
        self.assertIn("검색 한도 도달: 2/2회", r3)
        self.assertIn("미확정:", r3)
        self.assertEqual(len(self.env.search_log), 2)
        self.assertEqual(self.fake.queries, ["가 나", "다"])        # 한도 뒤로는 서버에 안 간다
        self.assertEqual(self.env.web_limit_hits, 1)

    def test_same_query_not_counted(self):
        T.web_search(self.env, "Foo  Bar")
        ok, r = T.web_search(self.env, " foo bar ")                # 공백·대소문자만 다름
        self.assertTrue(ok)
        self.assertIn("같은 검색어를 이미 찾았다", r)
        self.assertEqual(len(self.env.search_log), 1)
        self.assertEqual(self.fake.queries, ["Foo  Bar"])
        ok, r = T.web_search(self.env, "다른 것")
        self.assertTrue(r.endswith("(남은 검색 0/2)"))              # 되풀이는 횟수에 안 들어갔다

    def test_errors_still_errors(self):
        self.env.searx.search = lambda q, max_results=8: {"query": q, "results": [], "error": "HTTP Error 429"}
        ok, r = T.web_search(self.env, "x")
        self.assertFalse(ok)
        self.assertIn("검색 실패", r)
        ok, _ = T.web_search(self.env, "x")                         # 실패는 되풀이 표에 넣지 않는다(다시 해 본다)
        self.assertFalse(ok)


class TPlanEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "root"
        for rel in ("사진/a.jpg", "사진/b.jpg", "문서/r.pdf"):
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")
        self.bk = Backend("fake", "http://x/v1", "m", "q", 150000)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def prof(self, max_total=2, max_steps=20):
        p = load_profile("organize")
        p.workdir = self.root
        p.max_steps = max_steps
        p.web_search = {"url": "http://searx.invalid", "max_total": max_total, "max_results": 8}
        return p

    def run_(self, seq, prof):
        fake = FakeSearx()
        rec = Recorder(self.tmp / "runs", "org")
        with mock.patch.object(engine, "chat", side_effect=seq), mock.patch.object(T, "_searx", return_value=fake):
            s = engine.run(prof, self.bk, "정리해", rec)
        return s, rec, fake

    def test_limit_then_plan(self):
        # 한도(2) 뒤로 검색을 6번 더 해도(max_consecutive_errors 4 보다 많이) 실행이 이어져 계획이 남는다
        seq = [_resp(calls=[("folder_scan", "{}")])] + [_resp(calls=[ws(f"q{i}")]) for i in range(8)] + [
            _resp(calls=[("plan_add", json.dumps({"items": [
                {"op": "move", "src": "문서/r.pdf", "dst": "Docs/r.pdf", "reason": "미확정: 문서로 보인다", "confidence": 0.7},
                {"op": "move", "src": "사진", "dst": "Photos", "reason": "사진 폴더", "confidence": 0.9}]}, ensure_ascii=False))]),
            _resp("계획을 마쳤다")]
        s, rec, fake = self.run_(seq, self.prof())
        self.assertEqual(s["stop_reason"], "completed")
        self.assertEqual(len(fake.queries), 2)
        plan = json.loads((rec.dir / "plan.json").read_text())
        self.assertEqual([it["unconfirmed"] for it in plan["items"]], [True, False])
        self.assertEqual((plan["run"]["complete"], plan["run"]["web_limit_hit"], plan["run"]["web_limit_calls"]), (True, True, 6))
        self.assertEqual((plan["run"]["items"], plan["run"]["unconfirmed"]), (2, 1))
        self.assertEqual(s["plan"]["stop_reason"], "completed")

    def test_plan_json_even_without_items(self):
        seq = [_resp(calls=[("folder_scan", "{}")]), _resp(calls=[ws("a")]), _resp(calls=[ws("b")])]
        s, rec, _ = self.run_(seq, self.prof(max_steps=3))
        self.assertEqual(s["stop_reason"], "max_steps")
        plan = json.loads((rec.dir / "plan.json").read_text())        # 항목 없이도 남는다(어떤 종료든)
        self.assertEqual(plan["items"], [])
        self.assertEqual((plan["run"]["stop_reason"], plan["run"]["complete"]), ("max_steps", False))

    def test_no_scan_no_plan_and_other_profiles_unchanged(self):
        s, rec, _ = self.run_([_resp("훑지 않고 끝")], self.prof())
        self.assertFalse((rec.dir / "plan.json").exists())
        self.assertNotIn("plan", s)
        p = Profile(name="code", path=self.tmp / "p.toml", system="s", tools=["read_file"], workdir=self.tmp, max_steps=3)
        with mock.patch.object(engine, "chat", side_effect=[_resp("끝")]):
            s2 = engine.run(p, self.bk, "x", Recorder(self.tmp / "runs", "c"))
        self.assertNotIn("plan", s2)                                   # 정리 밖 프로필의 summary 키는 그대로

    def test_builtin_profile_limits(self):
        p = load_profile("organize")
        self.assertEqual((p.web_search["max_total"], p.web_search["max_results"]), (200, 8))
        self.assertIn("검색 한도 도달", p.system)
        self.assertIn("미확정:", p.system)

    def test_is_unconfirmed(self):
        self.assertTrue(PL.is_unconfirmed({"confidence": 0.49, "reason": "x"}))
        self.assertTrue(PL.is_unconfirmed({"confidence": 0.9, "reason": " 미확정: y"}))
        self.assertFalse(PL.is_unconfirmed({"confidence": 0.5, "reason": "확실"}))


if __name__ == "__main__":
    unittest.main()


class Zero(FakeSearx):
    def __init__(self, hits=None, unresponsive=None):
        super().__init__()
        self.hits, self.unresp = hits or set(), unresponsive or []

    def search(self, query, max_results=8):
        self.queries.append(query)
        res = [{"title": "t", "url": "https://e.org/1", "content": "c"}] if query in self.hits else []
        return {"query": query, "results": res, "error": None, "unresponsive": self.unresp}


class T58b(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = T.ToolEnv(self.tmp, web={"url": "http://x", "max_total": 50, "max_results": 8})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_zero_streak_and_similar(self):
        self.env.searx = z = Zero(hits={"hit me"})
        ok, r = T.web_search(self.env, "alpha beta gamma delta")
        self.assertNotIn("0건이", r)
        ok, r = T.web_search(self.env, "ABC-123 foo")
        self.assertIn("[0건이 2번 이어졌다]", r)                           # 연속 2번이면 안내
        ok, r = T.web_search(self.env, "abc123  FOO")                       # 하이픈·공백·대소문자만 다름 → 묻지 않음
        self.assertIn("묻지 않음", r)
        ok, r = T.web_search(self.env, "alpha beta gamma epsilon")          # 낱말 겹침 3/5 = 0.6 → 묻지 않음
        self.assertIn("묻지 않음", r)
        self.assertIn("[0건이 4번 이어졌다]", r)
        self.assertEqual(z.queries, ["alpha beta gamma delta", "ABC-123 foo"])
        self.assertEqual(len(self.env.search_log), 2)                       # 묻지 않은 것은 횟수에 안 든다
        ok, r = T.web_search(self.env, "hit me")
        self.assertEqual((self.env.zero_streak, self.env.last_search_zero), (0, False))   # 결과가 있으면 다시 0
        ok, r = T.web_search(self.env, "zeta eta")                          # 다른 꼴은 묻는다
        self.assertEqual(z.queries[-1], "zeta eta")

    def test_unresponsive_shown(self):
        self.env.searx = Zero(unresponsive=["google(timeout)", "bing(CAPTCHA)"])
        ok, r = T.web_search(self.env, "x")
        self.assertIn("엔진 응답 없음: google(timeout), bing(CAPTCHA)", r)
        self.assertEqual(self.env.search_log[-1]["unresponsive"], ["google(timeout)", "bing(CAPTCHA)"])

    def test_evidence_and_forced_unconfirmed(self):
        root = self.tmp / "root"
        for rel in ("a/x.mp4", "b/y.mp4", "c/z.mp4", "d/w.mp4"):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("v")
        env = T.ToolEnv(root, artifacts_dir=self.tmp / "runs", web={"url": "http://x", "max_total": 9, "max_results": 8})
        env.searx = Zero()
        self.assertTrue(T.folder_scan(env)[0])
        T.web_search(env, "nothing here")                                    # 0건
        ok, out = T.plan_add(env, [
            {"op": "move", "src": "a/x.mp4", "dst": "AA/x.mp4", "reason": "검색으로", "confidence": 0.9, "evidence": "search"},
            {"op": "move", "src": "b/y.mp4", "dst": "BB/y.mp4", "reason": "훑기로", "confidence": 0.9, "evidence": "scan"},
            {"op": "move", "src": "c/z.mp4", "dst": "CC/z.mp4", "reason": "이름만", "confidence": 0.9, "evidence": "name_only"},
            {"op": "move", "src": "d/w.mp4", "dst": "DD/w.mp4", "reason": "근거 꼴 없음", "confidence": 0.9}])
        self.assertIn("앞 검색이 0건이라 2건을 미확정으로", out)
        items = json.loads((self.tmp / "runs" / "plan.json").read_text())["items"]
        self.assertEqual([(it["evidence"], it["unconfirmed"], it.get("unconfirmed_why", "")) for it in items],
                         [("search", True, "앞 검색이 0건"), ("scan", False, ""), ("name_only", True, ""),
                          ("unspecified", True, "앞 검색이 0건")])
        ok, out = T.plan_add(env, [{"op": "move", "src": "a/x.mp4", "dst": "Q/x.mp4", "reason": "r", "evidence": "guess"}])
        self.assertIn("evidence 는", out)                                    # 모르는 근거 꼴은 거부
        plan = PL.Plan.load(self.tmp / "runs" / "plan.json")
        PL.finalize(plan, {"stop_reason": "completed", "complete": True})   # 끝 정리가 강제 표시를 지우지 않는다
        self.assertEqual([it["unconfirmed"] for it in plan.items], [True, False, True, True])


class TSpacing(unittest.TestCase):
    """c) 한 단계에 web_search 4개가 와도 SearxClient 가 min_interval_s 간격으로 한 줄로 보낸다(스레드로 불러도)."""

    def setUp(self):
        import http.server
        import threading
        import time as _t
        self.times = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/quota.json"):           # brave 셈이 없는 SearXNG
                    self.send_response(404)
                    self.end_headers()
                    return
                outer.times.append(_t.monotonic())
                body = json.dumps({"results": [], "unresponsive_engines": [["duckduckgo", "timeout"]]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        self.srv.shutdown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def gaps(self):
        return [b - a for a, b in zip(self.times, self.times[1:])]

    def test_engine_parallel_calls_queue(self):
        from stewart.websearch import SearxClient
        client = SearxClient(self.url, min_interval_s=0.3)
        p = Profile(name="w", path=self.tmp / "p.toml", system="s", tools=["web_search"], workdir=self.tmp, max_steps=3,
                    web_search={"url": self.url, "max_total": 30})
        seq = [_resp(calls=[ws("one"), ws("two two"), ws("three x"), ws("four y z")]), _resp("끝")]
        with mock.patch.object(engine, "chat", side_effect=seq), mock.patch.object(T, "_searx", return_value=client):
            s = engine.run(p, Backend("fake", "http://x/v1", "m", "q", 4096), "x", Recorder(self.tmp / "runs", "w"))
        self.assertEqual(len(self.times), 4)
        self.assertTrue(all(g >= 0.28 for g in self.gaps()), self.gaps())
        self.assertEqual(s["web_searches"][0]["unresponsive"], ["duckduckgo(timeout)"])   # SearXNG 칸 이름 그대로 읽음

    def test_threads_queue(self):
        """스레드 4개가 동시에 불러도 요청 시각이 min_interval_s 간격. 가상 시계(잠자기 = 시계 앞으로)로 재서 결정적이다.
        (전에는 서버 처리 시각을 실제 시계로 재서 스레드 일정에 따라 가끔 0.28 아래로 나왔다.)"""
        import threading
        from stewart.websearch import SearxClient

        class Clock:
            def __init__(self):
                self.t, self.lock = 100.0, threading.Lock()

            def now(self):
                with self.lock:
                    return self.t

            def sleep(self, s):
                with self.lock:
                    self.t += s

        clk, sent = Clock(), []
        client = SearxClient(self.url, min_interval_s=0.3)
        client._clock, client._sleep = clk.now, clk.sleep

        body = b'{"results": []}'

        class R:
            status, headers = 200, {"Content-Length": str(len(body)), "Content-Encoding": "identity"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return body

        def fake_urlopen(req, timeout=None):
            sent.append(clk.now())
            return R()

        with mock.patch("stewart.websearch.urllib.request.urlopen", side_effect=fake_urlopen):
            ts = [threading.Thread(target=client.search, args=(f"q{i}",)) for i in range(4)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        self.assertEqual(len(sent), 4)
        sent.sort()
        gaps = [round(b - a, 6) for a, b in zip(sent, sent[1:])]
        self.assertTrue(all(g >= 0.3 for g in gaps), gaps)
