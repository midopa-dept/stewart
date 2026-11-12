"""판 119: 스킬 앞머리 tools·surfaces — 자리별 색인, 불러오면 칩으로 꺼진 도구를 켬(자리에 없는 도구는 못 켬·위험 도구는 묻기·
사람이 끈 것은 안 켬·STEWART_SKILL_TOOLS=0 이면 끔), 내장 스킬 자리 표시, 새 스킬 hand-over-files."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, skills as SK
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_u60 import resp

SKILL = "---\nname: probe\ndescription: 시험용 스킬이다. 시험할 때 쓴다.\ntools: [list_dir, edit_file, nosuch]\nsurfaces: [talk]\n---\n본문\n"


class TSkills(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        sd = self.tmp / "skills" / "probe"
        sd.mkdir(parents=True)
        (sd / "SKILL.md").write_text(SKILL, encoding="utf-8")
        e = mock.patch.dict(os.environ, {"STEWART_SKILLS_DIR": str(self.tmp / "skills"), "STEWART_MEMORY": "0"})
        e.start()
        self.addCleanup(e.stop)

    def test_parse_and_surface(self):
        sk = SK.find(SK.discover(self.tmp, surface="talk"), "probe")
        self.assertEqual((sk.tools, sk.surfaces, sk.ignored_keys), (["list_dir", "edit_file", "nosuch"], ["talk"], []))
        self.assertIsNone(SK.find(SK.discover(self.tmp, surface="code"), "probe"))   # 대화 자리 전용
        self.assertIsNotNone(SK.find(SK.discover(self.tmp), "probe"))                # 자리를 안 주면(평가 길) 그대로
        talk = {x.name for x in SK.discover(self.tmp, builtin=True, surface="talk")}
        code = {x.name for x in SK.discover(self.tmp, builtin=True, surface="code")}
        self.assertIn("web-research", talk & code)
        self.assertIn("hand-over-files", talk & code)
        self.assertIn("git-work", code - talk)
        self.assertEqual(SK.find(SK.discover(self.tmp, builtin=True), "web-research").tools, ["web_search", "web_fetch"])

    def sess(self, answer="n"):
        p = Profile(name="pt", path=self.tmp / "p.toml", system="s", tools=["read_file", "list_dir", "edit_file"], workdir=self.tmp,
                    max_steps=4, raw={"surface": "talk", "toggle_tools": ["list_dir", "edit_file"]})
        ui = FakeUI()
        ui.ask = lambda q: answer
        s = C.ChatSession(p, Backend("b", "http://127.0.0.1:9/v1", "m", "q", 100000), Recorder(self.tmp / "runs", "c"), ui,
                          stream=False, mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        return s

    def load(self, s):
        with mock.patch.object(C, "chat", side_effect=[resp(calls=[("load_skill", {"name": "probe"})]), resp("끝")]):
            s.turn("probe 스킬을 써")
        return [m for m in s.messages if m.get("role") == "tool"][-1]["content"]

    def test_skill_turns_on_chips(self):
        s = self.sess(answer="n")
        self.assertIn("list_dir", s.disabled_tools)
        out = self.load(s)
        self.assertNotIn("list_dir", s.disabled_tools)                      # 위험하지 않은 도구는 켠다
        self.assertIn("edit_file", s.disabled_tools)                        # 위험 도구는 묻고, 거절하면 그대로
        self.assertIn("켠 도구: list_dir", out)
        self.assertIn("이 자리에 없는 도구: nosuch", out)                     # 자리에 없는 도구는 못 켠다

    def test_risky_yes_and_human_off(self):
        s = self.sess(answer="y")
        self.assertTrue(s.set_tool("list_dir", False))                      # 사람이 끔
        out = self.load(s)
        self.assertIn("list_dir", s.disabled_tools)                         # 사람이 끈 것은 안 켠다
        self.assertNotIn("edit_file", s.disabled_tools)                     # 위험 도구 — 승인하면 켠다
        self.assertIn("꺼진 채로 둔 도구", out)

    def test_knob_off(self):
        with mock.patch.dict(os.environ, {"STEWART_SKILL_TOOLS": "0"}):
            s = self.sess()
            self.load(s)
        self.assertIn("list_dir", s.disabled_tools)


if __name__ == "__main__":
    unittest.main()
