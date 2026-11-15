"""판 153: 사용자가 꾸미는 꼴(comodo) — 표 줄·사람만 기본·설정 시트 칸(생각 깊이·도구)·새 대화 적용(도구 부분집합, 더 주지 않음).

- 표에 칸이 없으면 rubato 뒤에 "설정 없음"으로 보이고 고를 수 없다.
- 시트로 꾸미면(모델·생각 깊이·문맥 창·도구) 새 대화 본문의 도구·reasoning_effort 가 그대로다.
- 도구는 그 자리 도구의 부분집합만: parlando 에 run_shell 을 적어도 생기지 않는다.
- 작업자·자동 분배는 고르지 않는다(사람만).
"""

import unittest
from pathlib import Path

from stewart import settings as ST, spawn as SP, tierview as TV
from tests.test_hub import msg
from tests.test_realuse_shape import FakeModel, Hub, make_user_shape
from tests.test_realuse_tier_bodies import TASK, view


class TUnit(unittest.TestCase):
    def test_settings_fields(self):
        import tempfile
        p = Path(tempfile.mkdtemp()) / "backends.toml"
        p.write_text('[backends.m]\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "m"\nmax_context = 32768\n', encoding="utf-8")
        ST.edit(p, "comodo", {"tools": ["read_file", "search", "read_file"], "reasoning_effort": "low", "budget": 8192})
        t = SP.load_tiers(p)["comodo"]
        self.assertEqual(t["tools"], ["read_file", "search"])                                      # 겹친 이름은 하나로
        self.assertEqual((t["think"].get("reasoning_effort"), t["budget"]), ("low", 8192))
        self.assertTrue(TV.is_manual(t, None))                                                      # 사람만이 기본
        auto, manual = SP.split_manual(SP.load_tiers(p), {})
        self.assertIn("comodo", manual)
        self.assertNotIn("comodo", auto)
        self.assertEqual(SP.tier_raw(t)["tools"], ["read_file", "search"])
        for bad in (["Read File"], "read_file", [1]):
            with self.assertRaises(ValueError):
                ST.edit(p, "comodo", {"tools": bad})


class THub(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.root = Path(tempfile.mkdtemp(prefix="comodo-"))
        self.model = FakeModel(route=lambda b: msg("네"))
        self.addCleanup(self.model.close)
        self.env = make_user_shape(self.root, self.model.url)
        (self.root / "project").mkdir(exist_ok=True)
        self.h = Hub(self.root, self.env)
        self.addCleanup(self.h.close)

    def first(self, kind, tier):
        h = self.h
        c = h.post("/api/chat/new", {"how": "fresh", "kind": kind, "title": "새 대화", "from": "main", "tier": tier})["chat"]
        n0 = len(self.model.requests)
        h.post("/api/main", {"text": TASK, "chat": c})
        self.assertTrue(h.wait(lambda: not h.get(f"/api/state?chat={c}")["busy"]))
        return view([b for b in self.model.requests[n0:] if b.get("tools")][0], self.root)

    def test_unset_then_customize(self):
        h = self.h
        rows = {r["tier"]: r for r in h.get("/api/tiers")["rows"]}
        self.assertEqual([r["tier"] for r in h.get("/api/tiers")["rows"]][:5], ["presto", "moderato", "largo", "rubato", "comodo"])
        self.assertEqual((rows["comodo"]["status"], rows["comodo"]["connected"]), ("unset", False))   # 설정 없음 — 고를 수 없다
        v = h.get("/api/settings")
        self.assertIn("read_file", v["place_tools"]["code"])
        self.assertNotIn("run_shell", v["place_tools"]["talk"])
        self.assertIn("low", next(b for b in v["backends"] if b["name"] == "cloud")["effort_values"])
        h.post("/api/settings/tier", {"tier": "comodo", "tier_fields": {"backend": "cloud", "reasoning_effort": "low",
                                                                         "tools": ["read_file", "search", "run_shell"]}})
        rows = {r["tier"]: r for r in h.get("/api/tiers")["rows"]}
        self.assertTrue(rows["comodo"]["connected"] and rows["comodo"]["manual"])
        self.assertEqual(rows["comodo"]["tools"], ["read_file", "search", "run_shell"])
        code = self.first("code", "comodo")
        self.assertEqual(code["keys"].get("reasoning_effort"), "low")
        mine = [t for t in code["tools"] if not t.startswith("session_") and t != "board"]   # 허브 도구는 허브 몫(그대로)
        self.assertEqual(sorted(mine), ["read_file", "run_shell", "search"])
        talk = self.first("talk", "comodo")
        self.assertNotIn("run_shell", talk["tools"])                                                 # 더 주지 않는다(parlando 는 셸 없음)
        self.assertIn("read_file", talk["tools"])


if __name__ == "__main__":
    unittest.main()
