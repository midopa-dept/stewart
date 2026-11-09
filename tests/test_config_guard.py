"""stewart 사용자 설정 폴더 쓰기 통제: chat 은 모드와 관계없이 묻기, run 은 거부, 프로젝트 .stewart/skills 는 그대로,
심링크·.. 로 돌려도 realpath 로 잡힘."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, config_guard as CG, engine
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_engine import _resp


class PUI(FakeUI):
    def __init__(self, answers=()):
        super().__init__(answers)
        self.prompts = []

    def ask(self, prompt):
        self.prompts.append(prompt)
        return super().ask(prompt)


class TConfigGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.home = self.tmp / "home"
        self.skills = self.tmp / "my-skills"
        (self.home / ".config" / "stewart" / "profiles").mkdir(parents=True)
        self.skills.mkdir()
        self.wd = self.tmp / "wd"
        (self.wd / ".stewart" / "skills" / "p").mkdir(parents=True)
        p = mock.patch.dict(os.environ, {"HOME": str(self.home), "STEWART_SKILLS_DIR": str(self.skills)})
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("STEWART_PROFILES_DIR", None)
        self.bk = Backend("fake", "http://x/v1", "m", "q", 4096)

    def prof(self):
        return Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.wd, max_steps=5)

    def w(self, path):
        return ("write_file", '{"path": "%s", "content": "주입"}' % path)

    def test_hit_paths(self):
        cfg = self.home / ".config" / "stewart" / "profiles" / "x.toml"
        self.assertEqual(CG.hit(self.wd, "write_file", {"path": str(cfg)}), cfg)
        self.assertIsNotNone(CG.hit(self.wd, "edit_file", {"path": "~/.config/stewart/profiles/x.toml"}))
        self.assertIsNotNone(CG.hit(self.wd, "multi_edit", {"path": str(self.skills / "a" / "SKILL.md")}))
        self.assertIsNotNone(CG.hit(self.wd, "write_file", {"path": "../my-skills/a/SKILL.md"}))   # .. 로 돌려도
        os.symlink(self.skills, self.wd / "link")
        self.assertIsNotNone(CG.hit(self.wd, "write_file", {"path": "link/a/SKILL.md"}))           # 심링크로 돌려도
        self.assertIsNone(CG.hit(self.wd, "write_file", {"path": ".stewart/skills/p/SKILL.md"}))   # 프로젝트 것은 그대로
        self.assertIsNone(CG.hit(self.wd, "read_file", {"path": str(cfg)}))                          # 읽기는 상관없음
        self.assertIsNone(CG.hit(self.wd, "run_shell", {"command": f"echo x > {cfg}"}))             # 셸은 이 장치 밖(한계)

    def test_chat_edits_asks_and_no_denies(self):
        target = self.skills / "evil" / "SKILL.md"
        for mode, answers in (("edits", ["n"]), ("auto", [""])):
            ui = PUI(answers=list(answers))
            s = C.ChatSession(self.prof(), self.bk, Recorder(self.tmp / "runs", f"chat-{mode}"), ui, mode=mode)
            self.addCleanup(lambda s=s: s.rec._ev.closed or s.rec._ev.close())
            with mock.patch.object(C, "chat", side_effect=[_resp(calls=[self.w(target)]), _resp("안 썼다")]) as m:
                s.turn("스킬을 만들어")
            self.assertEqual(ui.answers, [], mode)                     # edits·auto 모두 물었다(Enter 도 거부)
            self.assertIn("다음 세션부터 영향을 주는 stewart 설정 파일", ui.prompts[0])
            self.assertFalse(target.exists(), mode)
            tool = [x for x in m.call_args_list[-1][0][1] if x["role"] == "tool"][0]["content"]
            self.assertIn("설정 파일 쓰기를 거부", tool)

    def test_chat_yes_writes_and_project_not_asked(self):
        ui = FakeUI(answers=["y"])
        s = C.ChatSession(self.prof(), self.bk, Recorder(self.tmp / "runs", "chat-y"), ui, mode="edits")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        seq = [_resp(calls=[self.w(self.skills / "ok" / "SKILL.md"), self.w(".stewart/skills/p/SKILL.md")]), _resp("썼다")]
        with mock.patch.object(C, "chat", side_effect=seq):
            s.turn("둘 다 써")
        self.assertEqual(ui.answers, [])                               # 한 번(전역 것)만 물었다
        self.assertTrue((self.skills / "ok" / "SKILL.md").exists())
        self.assertTrue((self.wd / ".stewart" / "skills" / "p" / "SKILL.md").exists())

    def test_run_denies(self):
        cfg = self.home / ".config" / "stewart" / "profiles" / "x.toml"
        p = Profile(name="code", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.wd, max_steps=5)
        with mock.patch.object(engine, "chat", side_effect=[_resp(calls=[self.w(cfg)]), _resp("못 썼다")]) as m:
            s = engine.run(p, self.bk, "프로필을 고쳐", Recorder(self.tmp / "runs", "t"))
        self.assertFalse(cfg.exists())
        tool = [x for x in m.call_args_list[-1][0][1] if x["role"] == "tool"][0]["content"]
        self.assertIn("무인 실행에서 쓰지 않는다", tool)
        self.assertEqual(s["denied"], 1)


if __name__ == "__main__":
    unittest.main()
