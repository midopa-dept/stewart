"""판 113: 작은 모델용 얇은 허브 메인 — backends 표 등급에 slim = true, stewart hub --tier <이름>. 짧은 지시 하나·핵심 도구만 모델에게
(나머지는 켬·끔 칩, 기본 끔)·스킬 색인은 이름만. slim 등급은 작업자 등급 목록에 안 나온다. 작업자 프로필은 그대로. 고정 앞부분 5k 아래."""

import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import ctxcost as CC, slim as SL, spawn as SP
from stewart.hubmain import HUB_MAIN_NOTE, main_tier
from tests.test_hub3 import MainBase
from tests.test_hub import msg

TABLE = ('default = "big"\n[backends.big]\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "big-m"\nmax_context = 100000\n'
         '[backends.small]\nbase_url = "http://127.0.0.1:8/v1"\nmodel = "small-m"\nmax_context = 32768\n'
         '[tiers.thin]\nbackend = "small"\nslim = true\nbudget = 16000\n'
         '[tiers.light]\nbackend = "small"\nuse = "읽기"\ndefault_for = ["read"]\n')


class TTier(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "b.toml").write_text(TABLE, encoding="utf-8")
        e = mock.patch.dict(os.environ, {"STEWART_BACKENDS": str(self.tmp / "b.toml")})
        e.start()
        self.addCleanup(e.stop)

    def test_tiers_and_main_tier(self):
        self.assertEqual(list(SP.load_tiers()), ["thin", "light"])                  # 판 115: slim 등급도 작업자 목록에 남는다
        self.assertEqual(list(SP.load_tiers(main=True)), ["thin", "light"])
        a = argparse.Namespace(tier="thin", profile="chat", backend=None)
        p, b = main_tier(a, str(self.tmp))
        self.assertEqual((b.name, b.budget()), ("small", 16000))
        self.assertEqual(p.system, SL.SYSTEM)
        self.assertEqual(SL.conf(p), {})
        p2, b2 = main_tier(argparse.Namespace(tier=None, profile="chat", backend=None), str(self.tmp))
        self.assertIsNone(SL.conf(p2))                                              # 등급 없이는 그대로
        self.assertEqual(b2.name, "big")
        with self.assertRaises(Exception):
            main_tier(argparse.Namespace(tier="없음", profile="chat", backend=None), str(self.tmp))

    def test_cost_under_5k(self):
        s = CC.measure("chat", hub=True, clean=True, slim=True)
        h = CC.measure("chat", hub=True, clean=True)
        self.assertLess(s["total"], 5000)
        self.assertGreater(h["total"], 2 * s["total"])
        self.assertIn("얇은 허브 메인", CC.table([s]))


class TSlimMain(MainBase):
    def setUp(self):
        super().setUp()
        self.main.close()
        from stewart.cli import resolve
        from stewart.hubmain import HubMain
        from stewart.record import Recorder
        profile, backend = resolve(str(self.tmp / "main.toml"), "fake", str(self.wd))
        profile = SL.apply(profile, {"slim_system": "MAIN 지시(얇게)", "slim_keep": ["read_file", "run_shell"]})
        self.main = HubMain(self.hub, profile, backend, Recorder(self.tmp / "runs2", "hub-main"), mode="edits",
                            worker_profile=str(self.tmp / "w.toml"), worker_backend="fake")
        self.addCleanup(self.main.close)

    def test_slim_main(self):
        s = self.main.sess
        sysm = s.messages[0]["content"]
        self.assertIn("MAIN 지시(얇게)", sysm)
        self.assertNotIn(HUB_MAIN_NOTE[:20], sysm)                                 # 긴 역할 지시·현황판 지시 없음
        self.assertEqual(sorted(s.tool_names), ["read_file", "run_shell"])
        self.assertIn("session_spawn", s.toggleable)                               # 나머지는 칩(기본 끔)
        self.assertIn("write_file", s.toggleable)
        self.assertTrue(s.set_tool("write_file", True))
        self.assertIn("write_file", s.tool_names)
        tt = {x["name"]: x["on"] for x in self.main.state()["tool_toggles"]}
        self.assertTrue(tt["write_file"])
        self.assertFalse(tt["session_spawn"])
        self.r.scripts["MAIN"] = [msg("네")]
        self.main.send("안녕")
        self.assertTrue(self.main.wait_idle())
        sent = [d["function"]["name"] for d in self.r.requests["MAIN"][-1].get("tools") or []]
        self.assertEqual(sorted(sent), ["read_file", "run_shell", "write_file"])   # 모델에게 가는 도구 정의도 그만큼


class TSkills(unittest.TestCase):
    def test_names_only(self):
        class S:
            def __init__(self, n):
                self.name = n
        self.assertEqual(SL.skills_text([]), "")
        t = SL.skills_text([S("a"), S("b")])
        self.assertIn("a, b", t)
        self.assertIn("load_skill", t)


if __name__ == "__main__":
    unittest.main()
