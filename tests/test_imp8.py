"""맞물림: facts 의 되돌림·작업자 칸, 환경 블록은 화면에 안 보임, 확인 질문은 모두 ui.ask(TUI 질문)로."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, engine
from stewart.config import Backend, Profile, load_profile
from stewart.record import Recorder
from tests.test_config_guard import PUI
from tests.test_engine import _resp

EDIT = ("edit_file", '{"path": "a.py", "old": "x = 1", "new": "x = 2"}')


def dcall(**a):
    return _resp(calls=[("delegate", json.dumps(a, ensure_ascii=False))])


class TImp8(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.wd = self.tmp / "wd"
        self.wd.mkdir()
        (self.wd / "a.py").write_text("x = 1\n")
        p = mock.patch.dict(os.environ, {"STEWART_SKILLS_DIR": str(self.tmp / "ns"), "HOME": str(self.tmp / "home")})
        p.start()
        self.addCleanup(p.stop)
        self.bk = Backend("fake", "http://x/v1", "m", "q", 150000)

    def test_facts_rollback_section(self):
        p = Profile(name="code", path=self.tmp / "p.toml", system="s", tools=["edit_file", "write_file"], workdir=self.wd,
                    max_steps=2, rollback_unfinished=True)
        rec = Recorder(self.tmp / "runs", "t")
        with mock.patch.object(engine, "chat", side_effect=[_resp(calls=[EDIT]), _resp(calls=[("write_file", '{"path": "b.py", "content": "y"}')])]):
            s = engine.run(p, self.bk, "고쳐", rec)
        f = json.loads((rec.dir / "facts.json").read_text())
        self.assertEqual(s["stop_reason"], "max_steps")
        self.assertEqual({x["path"]: x["change"] for x in f["files"]}, {"a.py": "고침 → 되돌림", "b.py": "새로 만듦 → 되돌림"})
        self.assertEqual((f["rolled_back"]["restored"], f["rolled_back"]["moved_aside"]), (1, 1))
        self.assertIn("## 되돌림(끝내지 못해 엔진이 되돌림)", (rec.dir / "facts.md").read_text())
        self.assertFalse((rec.dir / "output.md").read_text().startswith("[엔진이 붙인"))

    def test_facts_delegations_with_verdict(self):
        prof = load_profile("manager")
        prof.workdir = self.wd
        prof.verify_command = None
        seq = [dcall(task="고쳐", kind="worker"), _resp(calls=[EDIT]), _resp("고쳤다"),
               dcall(task="확인", kind="verifier"), _resp("판정: 통과"), _resp("끝")]
        rec = Recorder(self.tmp / "runs", "m")
        with mock.patch.object(engine, "chat", side_effect=seq):
            engine.run(prof, self.bk, "고쳐", rec)
        f = json.loads((rec.dir / "facts.json").read_text())
        self.assertEqual([(d["worker_kind"], d["verdict"]) for d in f["delegations"]], [("worker", None), ("verifier", "통과")])
        self.assertEqual(f["files"], [{"path": "a.py", "change": "고침", "tool": "edit_file"}])   # 작업자 쓰기도 부모 체크포인트
        md = (rec.dir / "facts.md").read_text()
        self.assertIn("## 작업자", md)
        self.assertIn("판정 통과", md)

    def test_env_note_not_on_screen_and_questions_via_ask(self):
        ui = PUI(answers=["n", "n"])
        prof = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.wd, max_steps=10)
        s = C.ChatSession(prof, self.bk, Recorder(self.tmp / "runs", "chat-q"), ui, mode="edits")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        cfg = self.tmp / "home" / ".config" / "stewart" / "profiles" / "x.toml"
        # chat 의 위임은 도구를 적어야 셸을 준다(U2-2)
        seq_parent = [dcall(task="ls 를 돌려라", tools=["run_shell"]), _resp(calls=[("write_file", json.dumps({"path": str(cfg), "content": "x"}))]),
                      _resp("끝")]
        with mock.patch.object(engine, "chat", side_effect=[_resp(calls=[("run_shell", '{"command": "uname"}')]), _resp("거부됨")]), \
                mock.patch.object(C, "chat", side_effect=seq_parent):
            s.turn("해 줘")
        self.assertNotIn("[엔진이 붙인 환경 정보", ui.out.getvalue())           # 환경 블록은 화면에 안 보인다
        ev = [json.loads(l) for l in (s.rec.dir / "events.jsonl").read_text().splitlines()]
        self.assertTrue([e for e in ev if e["kind"] == "user"][0]["content"].startswith("[엔진이 붙인 환경 정보"))
        # 작업자 셸 확인과 설정 폴더 확인 모두 ui.ask(= TUI 의 질문 줄)로 왔다
        self.assertTrue(ui.prompts[0].strip().startswith("[작업자 1] 실행할까?"))
        self.assertIn("다음 세션부터 영향을 주는 stewart 설정 파일", ui.prompts[1])
        self.assertFalse(cfg.exists())


if __name__ == "__main__":
    unittest.main()


class TDietB(unittest.TestCase):
    def test_code_tools_list(self):
        import tempfile as _t
        from stewart import engine as E
        wd = Path(_t.mkdtemp())
        for name, gone in (("code", True), ("chat", False)):
            p = load_profile(name)
            p.workdir = wd
            with mock.patch.dict(os.environ, {"STEWART_SKILLS_DIR": str(wd / "ns")}), \
                    mock.patch.object(E, "chat", side_effect=[_resp("끝")]) as m:
                E.run(p, Backend("fake", "http://x/v1", "m", "q", 4096), "x", Recorder(wd / "runs", name))
            names = {d["function"]["name"] for d in m.call_args[0][2]}
            for t in ("multi_edit", "todo_read", "notebook_edit", "shell_kill"):
                self.assertEqual(t in names, not gone, (name, t))
            for t in ("read_file", "edit_file", "glob", "todo_write", "run_shell", "shell_output", "ask_user", "delegate"):
                self.assertIn(t, names, (name, t))


class TManagerRollback(unittest.TestCase):
    def test_manager_max_steps_rolls_back_worker_files(self):
        import tempfile as _t
        tmp = Path(os.path.realpath(_t.mkdtemp()))
        (tmp / "a.py").write_text("x = 1\n")
        prof = load_profile("manager")
        self.assertIs(prof.rollback_unfinished, True)
        prof.workdir, prof.verify_command, prof.max_steps = tmp, None, 2
        seq = [dcall(task="고쳐", kind="worker"), _resp(calls=[EDIT]),
               _resp(calls=[("write_file", '{"path": "new.py", "content": "y"}')]), _resp("고쳤다"),
               _resp(calls=[("read_file", '{"path": "a.py"}')])]
        rec = Recorder(tmp / "runs", "m")
        with mock.patch.dict(os.environ, {"STEWART_SKILLS_DIR": str(tmp / "ns")}), \
                mock.patch.object(engine, "chat", side_effect=seq):
            s = engine.run(prof, Backend("fake", "http://x/v1", "m", "q", 150000), "고쳐", rec)
        self.assertEqual(s["stop_reason"], "max_steps")
        self.assertEqual((tmp / "a.py").read_text(), "x = 1\n")        # 작업자가 고친 것도 되돌림
        self.assertFalse((tmp / "new.py").exists())                        # 작업자가 만든 것은 옮김
        self.assertEqual((s["rolled_back"]["restored"], s["rolled_back"]["moved_aside"]), (1, 1))
        f = json.loads((rec.dir / "facts.json").read_text())
        self.assertEqual({x["path"]: x["change"] for x in f["files"]}, {"a.py": "고침 → 되돌림", "new.py": "새로 만듦 → 되돌림"})
