"""프로필 extends, web_search 범주·brave 이달 셈, 접기 환경 변수."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import compact as CP, config as C, tools as T
from stewart.websearch import parse_quota


class TExtends(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, name, text):
        p = self.tmp / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_server_copy_shape(self):
        p = self.write("organize-private.toml", 'extends = "organize"\n\n[secret_files]\nextra = ["NPKI*", "signCert.der"]\n')
        prof = C.load_profile(str(p))
        base = C.load_profile("organize")
        self.assertEqual(prof.name, "organize-private")                  # 이름은 물려받지 않는다
        self.assertEqual(prof.system, base.system)
        self.assertEqual(prof.tools, base.tools)
        self.assertEqual((prof.compact_ratio, prof.compact_keep_recent, prof.compact_fold), (0.55, 8, True))
        self.assertEqual(prof.web_search, {"max_total": 200, "max_results": 8})
        self.assertEqual(prof.secret_files, {"extra": ["NPKI*", "signCert.der"]})
        self.assertEqual(prof.raw["extends"], "organize")

    def test_tables_merge_scalars_lists_replace(self):
        p = self.write("o2.toml", 'extends = "organize"\nmax_steps = 99\ntools = ["folder_scan"]\n\n[web_search]\nmax_total = 40\n')
        prof = C.load_profile(str(p))
        self.assertEqual(prof.max_steps, 99)
        self.assertEqual(prof.tools, ["folder_scan"])                     # 목록은 통째로 덮는다
        self.assertEqual(prof.web_search, {"max_total": 40, "max_results": 8})   # 표는 키 단위로 합친다

    def test_secret_extra_appends(self):
        merged = C.merge_profile({"secret_files": {"extra": ["a", "b"], "allow": ["x"]}},
                                 {"secret_files": {"extra": ["b", "c"], "allow": ["y"]}})
        self.assertEqual(merged["secret_files"], {"extra": ["a", "b", "c"], "allow": ["y"]})

    def test_refusals(self):
        for body in ('extends = "../x"\n', 'extends = "없는것"\n', 'extends = "code.toml"\n', 'extends = 3\n'):
            with self.assertRaises(C.ConfigError, msg=body):
                C.load_profile(str(self.write("bad.toml", body)))
        with self.assertRaises(C.ConfigError):                           # 모르는 키는 합친 뒤에도 잡는다
            C.load_profile(str(self.write("bad2.toml", 'extends = "organize"\nfoo = 1\n')))

    def test_no_extends_unchanged(self):
        self.assertNotIn("extends", C.load_profile("code").raw)


class FakeQ:
    def __init__(self, q):
        self.q, self.reads, self.queries = q, 0, []

    def search(self, query, max_results=8, categories=None):
        self.queries.append((query, categories))
        return {"query": query, "results": [{"title": "t", "url": "https://e.org/1", "content": "c"}], "error": None}

    def quota(self, url=None):
        self.reads += 1
        return parse_quota(self.q) if self.q is not None else None        # 진짜 SearxClient.quota 처럼 풀어서


class TSearch59(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = T.ToolEnv(self.tmp, web={"url": "http://x", "max_total": 100, "max_results": 8})

    def test_categories(self):
        self.env.searx = f = FakeQ(None)
        ok, _ = T.web_search(self.env, "rust borrow checker", categories="it")
        ok2, _ = T.web_search(self.env, "rust borrow checker")             # 범주가 다르면 다른 검색
        self.assertEqual(f.queries, [("rust borrow checker", "it"), ("rust borrow checker", None)])
        self.assertEqual(self.env.search_log[0]["categories"], "it")
        ok, out = T.web_search(self.env, "x", categories="images")
        self.assertFalse(ok)
        self.assertIn("general, it, science, news", out)
        self.assertIn("categories", T.definitions(["web_search"])[0]["function"]["parameters"]["properties"])

    def test_quota_line_and_rereads(self):
        self.env.searx = f = FakeQ({"limit": 950, "used": 925, "remaining": 25, "enabled": True})
        ok, out = T.web_search(self.env, "q0")
        self.assertIn("brave 남은 약 25/950", out)
        self.assertEqual(f.reads, 1)                                        # 실행 시작에 한 번
        for i in range(1, 5):
            T.web_search(self.env, f"q{i}")
        self.assertEqual(f.reads, 1)                                        # 어림 21 → 아직 안 읽음
        ok, out = T.web_search(self.env, "q5")                              # 어림 20(문턱) + 5번 지남 → 다시 읽음
        self.assertEqual(f.reads, 2)

    def test_quota_exhausted_and_unreadable(self):
        self.env.searx = FakeQ({"limit": 950, "used": 950})
        ok, out = T.web_search(self.env, "a")
        self.assertIn("brave 이달 한도 도달 또는 꺼짐(무료 엔진만)", out)
        env2 = T.ToolEnv(self.tmp, web={"url": "http://x", "max_total": 9})
        env2.searx = FakeQ(None)
        ok, out = T.web_search(env2, "a")
        self.assertNotIn("brave", out)                                      # 못 읽으면 표시를 뺀다
        self.assertIsNone(env2.quota)

    def test_f_shape_60(self):
        """F 의 실제 꼴: brave 칸이 글. '키 없음'·'꺼짐' 이면 꺼짐, '켜짐' 이면 남은 수."""
        self.env.searx = FakeQ({"month": "2026-11", "used": 0, "limit": 950, "remaining": 950, "brave": "키 없음"})
        ok, out = T.web_search(self.env, "a")
        self.assertIn("brave 이달 한도 도달 또는 꺼짐", out)
        self.assertEqual((self.env.quota["state"], self.env.quota["month"]), ("키 없음", "2026-11"))
        env2 = T.ToolEnv(self.tmp, web={"url": "http://x", "max_total": 9})
        env2.searx = FakeQ({"month": "2026-11", "used": 10, "limit": 950, "remaining": 940, "brave": "켜짐"})
        ok, out = T.web_search(env2, "a")
        self.assertIn("brave 남은 약 940/950", out)
        self.assertFalse(parse_quota({"remaining": 900, "brave": "꺼짐"})["enabled"])

    def test_profile_limits_60(self):
        self.assertEqual(C.load_profile("code").web_search["max_total"], 10)
        self.assertEqual(C.load_profile("chat").web_search["max_total"], 30)
        self.assertEqual(C.load_profile("organize").web_search["max_total"], 200)
        r = C.load_profile("research")
        self.assertEqual((r.web_search["max_total"], r.web_search["max_results"]), (60, 8))
        self.assertTrue({"web_search", "web_fetch"} <= set(r.tools))
        self.assertFalse({"write_file", "edit_file", "run_shell"} & set(r.tools))   # 파일은 읽기만
        for w in ("it", "science", "news", "web_fetch"):
            self.assertIn(w, r.system)

    def test_parse_quota_shapes(self):
        self.assertEqual(parse_quota({"braveapi": {"count": 10, "cap": 950, "on": True}})["remaining"], 940)
        self.assertFalse(parse_quota({"limit": 950, "used": 100, "enabled": False})["enabled"])
        self.assertIsNone(parse_quota({"foo": 1}))
        self.assertIsNone(parse_quota([1, 2]))


class TFoldEnv(unittest.TestCase):
    def test_env_overrides(self):
        p = C.load_profile("code")
        s = CP.settings(p, 0.7, 8, env={})
        self.assertEqual((s["fold"], s["ratio"], s["keep_recent"], s["env"]), (False, 0.7, 8, []))
        s = CP.settings(p, 0.7, 8, env={"STEWART_COMPACT_FOLD": "1", "STEWART_COMPACT_RATIO": "0.55", "STEWART_COMPACT_KEEP": "8"})
        self.assertEqual((s["fold"], s["ratio"], s["keep_recent"]), (True, 0.55, 8))
        self.assertEqual(s["env"], ["STEWART_COMPACT_FOLD", "STEWART_COMPACT_RATIO", "STEWART_COMPACT_KEEP"])
        s = CP.settings(p, 0.7, 8, env={"STEWART_COMPACT_FOLD": "yes", "STEWART_COMPACT_RATIO": "2"})
        self.assertEqual((s["fold"], s["ratio"], s["ignored"]), (False, 0.7, ["STEWART_COMPACT_FOLD", "STEWART_COMPACT_RATIO"]))
        o = C.load_profile("organize")
        self.assertFalse(CP.settings(o, 0.7, 8, env={"STEWART_COMPACT_FOLD": "0"})["fold"])   # 정리도 끌 수 있다

    def test_engine_records_env(self):
        from stewart import engine
        from stewart.config import Backend, Profile
        from stewart.record import Recorder
        from tests.test_engine import _resp
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        p = Profile(name="code", path=tmp / "p.toml", system="s", tools=["read_file"], workdir=tmp, max_steps=2)
        with mock.patch.dict(os.environ, {"STEWART_COMPACT_FOLD": "1"}), mock.patch.object(engine, "chat", side_effect=[_resp("끝")]):
            s = engine.run(p, Backend("fake", "http://x/v1", "m", "q", 4096), "x", Recorder(tmp / "runs", "c"))
        self.assertEqual(s["compact_settings"]["fold"], True)
        self.assertIn("compact_folded", s)
        with mock.patch.object(engine, "chat", side_effect=[_resp("끝")]):
            s2 = engine.run(p, Backend("fake", "http://x/v1", "m", "q", 4096), "x", Recorder(tmp / "runs", "c2"))
        self.assertNotIn("compact_settings", s2)                            # 환경 변수가 없으면 summary 키는 그대로
        self.assertNotIn("compact_folded", s2)


if __name__ == "__main__":
    unittest.main()
