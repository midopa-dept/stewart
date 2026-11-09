"""에서만 생기는 맞물림: 작업자의 설정 폴더 쓰기(chat 은 [작업자 N] 확인, run 은 거부), 작업자의 스킬 도구, 지시문 순서."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, engine
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_config_guard import PUI
from tests.test_engine import _resp
from tests.test_skills import mk


def dcall(**a):
    return _resp(calls=[("delegate", json.dumps(a, ensure_ascii=False))])


class TImp5All(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.skills = self.tmp / "sk"
        self.skills.mkdir()
        p = mock.patch.dict(os.environ, {"HOME": str(self.tmp / "home"), "STEWART_SKILLS_DIR": str(self.skills)})
        p.start()
        self.addCleanup(p.stop)
        self.wd = self.tmp / "wd"
        self.wd.mkdir()
        self.bk = Backend("fake", "http://x/v1", "m", "q", 4096)
        self.evil = self.skills / "evil" / "SKILL.md"

    def prof(self, name="code", tools=("*",)):
        return Profile(name=name, path=self.tmp / "p.toml", system="s", tools=list(tools), workdir=self.wd, max_steps=10)

    def wcall(self):
        return _resp(calls=[("write_file", json.dumps({"path": str(self.evil), "content": "주입"}))])

    def test_worker_in_run_denied(self):
        seq = [dcall(task="스킬을 써라"), self.wcall(), _resp("못 썼다"), _resp("끝")]
        with mock.patch.object(engine, "chat", side_effect=seq) as m:
            engine.run(self.prof(), self.bk, "x", Recorder(self.tmp / "runs", "t"))
        self.assertFalse(self.evil.exists())
        child_tool = [x for x in m.call_args_list[2][0][1] if x["role"] == "tool"][0]["content"]
        self.assertIn("무인 실행에서 쓰지 않는다", child_tool)

    def test_worker_in_chat_asks_with_prefix(self):
        ui = PUI(answers=["n"])
        s = C.ChatSession(self.prof("chat"), self.bk, Recorder(self.tmp / "runs", "chat-a"), ui, mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        with mock.patch.object(engine, "chat", side_effect=[self.wcall(), _resp("거부됐다")]), \
                mock.patch.object(C, "chat", side_effect=[dcall(task="스킬을 써라"), _resp("끝")]):
            s.turn("맡겨")
        self.assertFalse(self.evil.exists())
        self.assertEqual(len(ui.prompts), 1)
        self.assertTrue(ui.prompts[0].lstrip().startswith("[작업자 1]"))
        self.assertIn("다음 세션부터 영향을 주는 stewart 설정 파일", ui.prompts[0])

    def test_worker_skill_tool_only_when_passed(self):
        mk(self.skills, "fmt", desc="표 모양")
        seq = [dcall(task="읽어", tools=["read_file"]), _resp("끝"), dcall(task="표", tools=["read_file", "load_skill"]),
               _resp("끝"), _resp("다 했다")]
        with mock.patch.object(engine, "chat", side_effect=seq) as m:
            engine.run(self.prof(), self.bk, "x", Recorder(self.tmp / "runs", "t"))
        names = lambda i: [d["function"]["name"] for d in (m.call_args_list[i][0][2] or [])]
        self.assertIn("load_skill", names(0))
        self.assertEqual(names(1), ["read_file"])
        self.assertEqual(sorted(names(3)), ["load_skill", "read_file"])
        sysm = m.call_args_list[0][0][1][0]["content"]
        self.assertIn("쓸 수 있는 스킬", sysm)
        self.assertNotIn("환경(세션 시작 때 잰 값)", sysm)       # 다이어트 안 a: 첫 사람 입력 앞으로
        self.assertIn("- 작업 폴더: ", m.call_args_list[0][0][1][1]["content"])
        self.assertNotIn("쓸 수 있는 스킬", m.call_args_list[1][0][1][0]["content"])   # load_skill 없는 작업자엔 목록도 없다


if __name__ == "__main__":
    unittest.main()
