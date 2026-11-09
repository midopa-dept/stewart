"""프로젝트 기억 파일: git 뿌리까지만 올라감, 순서, 상한, meta 기록, 끄기, 두 경로."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, engine, memfile
from stewart.config import Backend, ConfigError, Profile, load_profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_engine import _resp


class TMemfile(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        # tmp/outer/AGENTS.md  ← git 밖(읽으면 안 됨)
        # tmp/outer/repo/.git, AGENTS.md(뿌리)
        # tmp/outer/repo/pkg/sub/STEWART.md, AGENTS.md(작업 폴더)
        self.outer = self.tmp / "outer"
        self.repo = self.outer / "repo"
        self.wd = self.repo / "pkg" / "sub"
        self.wd.mkdir(parents=True)
        (self.repo / ".git").mkdir()
        (self.outer / "AGENTS.md").write_text("밖 규칙", encoding="utf-8")
        (self.repo / "AGENTS.md").write_text("뿌리 규칙", encoding="utf-8")
        (self.wd / "AGENTS.md").write_text("작업 폴더 규칙 A", encoding="utf-8")
        (self.wd / "STEWART.md").write_text("작업 폴더 규칙 S", encoding="utf-8")
        self.bk = Backend("fake", "http://x/v1", "m", "q", 4096)

    def prof(self, wd=None, **kw):
        return Profile(name="t", path=self.tmp / "p.toml", system="기본 지시", tools=["*"], workdir=wd or self.wd, max_steps=3, **kw)

    def test_up_to_git_root_only(self):
        text, log = memfile.collect(self.wd)
        self.assertEqual([Path(m["path"]) for m in log],
                         [self.repo / "AGENTS.md", self.wd / "AGENTS.md", self.wd / "STEWART.md"])
        self.assertNotIn("밖 규칙", text)
        self.assertLess(text.index("뿌리 규칙"), text.index("작업 폴더 규칙 A"))
        self.assertLess(text.index("규칙 A"), text.index("규칙 S"))

    def test_no_git_means_workdir_only(self):
        lone = self.outer / "lone"
        lone.mkdir()
        (lone / "AGENTS.md").write_text("혼자 규칙", encoding="utf-8")
        text, log = memfile.collect(lone)
        self.assertEqual([Path(m["path"]) for m in log], [lone / "AGENTS.md"])
        self.assertNotIn("밖 규칙", text)
        self.assertEqual(memfile.collect(self.tmp)[1], [])     # 파일 없으면 비어 있음

    def test_git_file_worktree(self):
        wt = self.outer / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /elsewhere\n")      # worktree 는 .git 이 파일
        self.assertEqual(memfile.git_root(wt), wt.resolve())

    def test_cap_keeps_specific(self):
        (self.repo / "AGENTS.md").write_text("뿌리" * 100, encoding="utf-8")
        text, log = memfile.collect(self.wd, max_chars=40)
        by = {Path(m["path"]).name + str(Path(m["path"]).parent == self.wd): m for m in log}
        self.assertFalse(by["AGENTS.mdTrue"]["truncated"])
        self.assertFalse(by["STEWART.mdTrue"]["truncated"])
        self.assertTrue(by["AGENTS.mdFalse"]["truncated"])
        self.assertIn("작업 폴더 규칙 A", text)

    def test_engine_system_and_meta(self):
        rec = Recorder(self.tmp / "runs", "t")
        with mock.patch.object(engine, "chat", side_effect=[_resp("끝.")]) as m:
            engine.run(self.prof(), self.bk, "do", rec)
        system = m.call_args[0][1][0]["content"]
        self.assertTrue(system.startswith("기본 지시"))
        self.assertIn("뿌리 규칙", system)
        self.assertIn("작업 폴더 규칙 S", system)
        self.assertNotIn("작업 폴더:", system)          # 환경 블록은 첫 사람 입력 앞(다이어트 안 a)
        start = json.loads((rec.dir / "events.jsonl").read_text().splitlines()[0])
        self.assertEqual(len(start["meta"]["memory_files"]), 3)
        self.assertIn("프로젝트 기억 파일", (rec.dir / "transcript.md").read_text())

    def test_disable_and_names(self):
        with mock.patch.object(engine, "chat", side_effect=[_resp("끝.")]) as m:
            engine.run(self.prof(memory_files=[]), self.bk, "do", Recorder(self.tmp / "runs", "t"))
        self.assertNotIn("규칙", m.call_args[0][1][0]["content"])
        with mock.patch.object(engine, "chat", side_effect=[_resp("끝.")]) as m:
            engine.run(self.prof(memory_files=["STEWART.md"]), self.bk, "do", Recorder(self.tmp / "runs", "t2"))
        c = m.call_args[0][1][0]["content"]
        self.assertIn("규칙 S", c)
        self.assertNotIn("규칙 A", c)

    def test_chat_path(self):
        ui = FakeUI()
        s = C.ChatSession(self.prof(), self.bk, Recorder(self.tmp / "runs", "chat-t"), ui)
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        self.assertIn("뿌리 규칙", s.system)
        self.assertEqual(len(s.meta["memory_files"]), 3)
        self.assertIn("프로젝트 기억 파일", ui.out.getvalue())
        s.reset()
        self.assertIn("뿌리 규칙", s.messages[0]["content"])

    def test_profile_keys(self):
        f = self.tmp / "m.toml"
        f.write_text('system = "s"\nmemory_files = ["RULES.md"]\nmemory_max_chars = 500\n', encoding="utf-8")
        p = load_profile(str(f))
        self.assertEqual((p.memory_files, p.memory_max_chars), (["RULES.md"], 500))
        for bad in ('memory_files = "AGENTS.md"', 'memory_files = ["../x.md"]', "memory_max_chars = 0"):
            f.write_text(f'system = "s"\n{bad}\n', encoding="utf-8")
            with self.assertRaises(ConfigError, msg=bad):
                load_profile(str(f))

    def test_builtin_code_profile_on_eval_like_dir(self):
        # 평가기 작업 폴더처럼 git 밖·규칙 파일 없음 → 지시문이 이전과 같다
        plain = self.tmp / "plain"
        plain.mkdir()
        (plain / "app.py").write_text("x = 1\n")
        p = load_profile("code")
        p.workdir = plain
        self.assertEqual(engine.memory_for(p), ("", []))


if __name__ == "__main__":
    unittest.main()
