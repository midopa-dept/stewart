"""판 133: 장면 실측 도구(tools/scenes/measure.py) — 도구를 실행하지 않고 첫 걸음만 보는지, 결과 표 머리·건너뜀·갈래 합.

- 개발 장면(set = dev)이 낱말 규칙(웹 넛지)에 걸리지 않는 꼴인지도 본다 — 규칙과 무관하게 모델이 웹을 고르는지 재려는 장면이다.
"""

import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, scenes as SC, webnudge as W
from stewart.config import Backend
from tests.test_u60 import resp

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("scene_measure", ROOT / "tools" / "scenes" / "measure.py")
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


class TMeasure(unittest.TestCase):
    def setUp(self):
        e = mock.patch.dict(os.environ, {"STEWART_MEMORY": "0", "STEWART_SEARXNG_URL": "http://127.0.0.1:9/search", "STEWART_WEB_FETCH": "1"})
        e.start()
        self.addCleanup(e.stop)
        self.b = Backend("b", "http://127.0.0.1:9/v1", "m", "q", 100000)
        self.by = {s["id"]: s for s in SC.load()}

    def test_dry_first_step(self):
        sc = self.by["open-browser-on-this-computer"]
        with mock.patch.object(C, "chat", side_effect=[resp(calls=[("run_shell", {"command": "open https://www.naver.com"})])]), \
                mock.patch.object(C.ChatSession, "_exec", side_effect=AssertionError("진짜 도구 실행을 부르면 안 된다")):
            ok, why, first = M.one(sc, self.b, 1, run_tools=False)
        self.assertEqual((ok, first), (False, ["run_shell"]))
        self.assertIn("셸로 이 컴퓨터 화면", why)
        with mock.patch.object(C, "chat", side_effect=[resp(calls=[("web_search", {"query": "Ghostty"})]), resp("찾아봤다")]):   # 이 장면은 걸음 상한 2
            ok, why, first = M.one(self.by["dev-bare-name"], self.b, 1, run_tools=False)
        self.assertEqual((ok, first), (True, ["web_search"]))

    def test_requires_skips(self):
        sc = {**self.by["dev-bare-name"], "requires": ["no_such_tool"]}
        with mock.patch.object(C, "chat", side_effect=AssertionError("부르면 안 된다")):
            self.assertEqual(M.one(sc, self.b, 1, run_tools=False), (None, "도구 없음: no_such_tool", []))

    def test_table(self):
        rows = [{"id": "a", "set": "real", "said": "가", "good": 1, "n": 2, "skipped": 0,
                 "runs": [{"ok": True, "why": "", "first": ["web_search"]}, {"ok": False, "why": "", "first": []}]},
                {"id": "b", "set": "dev", "said": "나", "good": 0, "n": 2, "skipped": 2,
                 "runs": [{"ok": None, "why": "도구 없음: x", "first": []}] * 2}]
        md = M.table_md({"model": "m", "when": "t", "lanes": ["u"], "n": 2, "max_steps": 1, "run_tools": False, "rows": rows})
        self.assertIn(M.DEV_HEAD, md)
        self.assertIn("| real |  | a | 가 | 1/2 |  | web_search 1 · 도구 없음 1 |", md)
        self.assertIn("건너뜀 2 — 도구 없음: x", md)
        self.assertIn("- real 합: 1/2", md)
        self.assertIn("- dev 합: 0/0", md)

    def test_dev_scenes_dodge_word_rules(self):
        dev = [s for s in SC.load() if s["set"] == "dev"]
        self.assertGreaterEqual(len(dev), 4)
        for s in dev:
            self.assertEqual(W.recent_hit(s["said"]), "", s["id"])
        main = [s for s in SC.load() if s["_file"] == "scenes.toml"]
        self.assertTrue(all(s["set"] == "real" for s in main if "dev-" not in s["id"]))   # 실사용 장면 여섯(과 그 parlando 짝)은 real


class TSceneEnv(unittest.TestCase):
    def test_scene_env_is_test_place(self):
        """판 137: 장면 조건의 사용자·스킬·프로필 폴더는 한 시험 자리 안이고 스킬·프로필은 비어 있다(실행하는 사람의 설정이 안 실림)."""
        env = SC.scene_env()
        base = Path(env["STEWART_USER_DIR"])
        self.assertTrue((base / "STEWART.md").is_file())
        for k in ("STEWART_SKILLS_DIR", "STEWART_PROFILES_DIR"):
            d = Path(env[k])
            self.assertEqual(d.parent, base)
            self.assertEqual(list(d.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
