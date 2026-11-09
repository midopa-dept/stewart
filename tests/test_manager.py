"""관리자 모드: 장부 도구, manager_tools 좁히기(작업자에게는 넘김), delegate_budget, 검증자 판정·git 전후 비교."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, engine, manager as MG
from stewart.config import Backend, load_profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_engine import _resp


def dcall(**a):
    return _resp(calls=[("delegate", json.dumps(a, ensure_ascii=False))])


def lcall(**a):
    return _resp(calls=[("ledger", json.dumps(a, ensure_ascii=False))])


def names(call):
    return [d["function"]["name"] for d in (call[0][2] or [])]


class TLedger(unittest.TestCase):
    def test_ops_and_caps(self):
        tmp = Path(tempfile.mkdtemp())
        lg = MG.Ledger(tmp / "ledger.json", "목표")
        ok, out = lg.op({"op": "set", "items": [{"id": i, "task": "가" * 400, "status": "이상함"} for i in range(25)]})
        self.assertTrue(ok)
        self.assertEqual(len(lg.data["items"]), MG.MAX_ITEMS)
        self.assertEqual(lg.data["items"][0]["status"], "todo")
        self.assertLessEqual(len(lg.data["items"][0]["task"]), MG.MAX_TEXT + 1)
        self.assertIn("20개까지만", out)
        self.assertTrue(lg.op({"op": "note", "text": "메모"})[0])
        self.assertFalse(lg.op({"op": "note"})[0])
        self.assertFalse(lg.op({"op": "set"})[0])
        self.assertFalse(lg.op({"op": "지우기"})[0])
        self.assertIn("메모", lg.op({"op": "show"})[1])
        self.assertEqual(json.loads((tmp / "ledger.json").read_text())["notes"], ["메모"])

    def test_verdict(self):
        self.assertEqual(MG.parse_verdict("시험 3개 OK\n판정: 통과"), "통과")
        self.assertEqual(MG.parse_verdict("판정: 실패 — test_even 이 틀림\n"), "실패 — test_even 이 틀림")
        self.assertIsNone(MG.parse_verdict("다 됐다"))


class TManagerRun(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.wd = self.tmp / "wd"
        self.wd.mkdir()
        (self.wd / "a.py").write_text("x = 1\n")
        p = mock.patch.dict(os.environ, {"STEWART_SKILLS_DIR": str(self.tmp / "noskills")})
        p.start()
        self.addCleanup(p.stop)
        self.bk = Backend("fake", "http://x/v1", "m", "q", 150000)
        self.prof = load_profile("manager")
        self.prof.workdir = self.wd
        self.prof.verify_command = None

    def go(self, seq):
        rec = Recorder(self.tmp / "runs", "m")
        with mock.patch.object(engine, "chat", side_effect=seq) as m:
            s = engine.run(self.prof, self.bk, "x 를 2 로", rec)
        return s, m, rec

    def test_flow(self):
        seq = [lcall(op="set", items=[{"id": 1, "task": "x 를 2 로", "status": "doing"}]),
               dcall(task="a.py 의 x 를 2 로", kind="worker"),
               _resp(calls=[("edit_file", '{"path": "a.py", "old": "x = 1", "new": "x = 2"}')]),
               _resp("a.py 의 x 를 2 로 바꿨다."),
               dcall(task="a.py 의 x 가 2 인지 확인", kind="verifier"),
               _resp(calls=[("run_shell", '{"command": "cat a.py"}')]),
               _resp("x = 2 를 확인했다.\n판정: 통과"),
               lcall(op="set", items=[{"id": 1, "task": "x 를 2 로", "status": "done"}]),
               _resp("x 를 2 로 바꿨고 검증을 통과했다.")]
        s, m, rec = self.go(seq)
        self.assertEqual(s["stop_reason"], "completed")
        self.assertEqual(sorted(names(m.call_args_list[0])), sorted(self.prof.manager_tools))   # 관리자는 쓰기·셸 없음
        self.assertIn("edit_file", names(m.call_args_list[2]))      # 작업자는 넘겨받음
        self.assertNotIn("delegate", names(m.call_args_list[2]))
        self.assertEqual(sorted(names(m.call_args_list[5])), ["glob", "list_dir", "read_file", "run_shell", "search"])
        self.assertEqual((self.wd / "a.py").read_text(), "x = 2\n")
        lg = json.loads((rec.dir / "ledger.json").read_text())
        self.assertEqual([r["verdict"] for r in lg["runs"]], [None, "통과"])
        self.assertEqual(lg["runs"][1]["git_check"], "확인 불가(git 저장소 아님)")
        self.assertEqual(s["ledger"]["items"]["done"], 1)
        self.assertEqual(s["ledger"]["verdicts"], ["통과"])
        tool_msgs = [x["content"] for x in m.call_args_list[-1][0][1] if x["role"] == "tool"]
        self.assertIn("[장부: 할 일 1", tool_msgs[1])
        self.assertIn("마지막 판정 통과", tool_msgs[2])
        self.assertEqual(s["delegations"][1]["git_check"], "확인 불가(git 저장소 아님)")

    def test_verifier_changed_files_invalid(self):
        subprocess.run(["git", "init", "-q"], cwd=self.wd, check=True)
        ge = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        subprocess.run(["git", "add", "-A"], cwd=self.wd, check=True, env=ge)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.wd, check=True, env=ge)
        seq = [dcall(task="확인", kind="verifier"),
               _resp(calls=[("run_shell", '{"command": "echo y = 9 >> a.py; mkdir -p __pycache__; touch __pycache__/z.pyc"}')]),
               _resp("판정: 통과"), _resp("끝")]
        s, m, rec = self.go(seq)
        tool_msg = [x["content"] for x in m.call_args_list[-1][0][1] if x["role"] == "tool"][0]
        self.assertIn("판정은 무효", tool_msg)
        lg = json.loads((rec.dir / "ledger.json").read_text())
        self.assertEqual(lg["runs"][0]["verdict"], "무효(검증자가 파일을 바꿈)")
        self.assertEqual(lg["runs"][0]["git_check"], "검증자가 파일을 바꿈")

    def test_verifier_build_noise_ok(self):
        subprocess.run(["git", "init", "-q"], cwd=self.wd, check=True)
        seq = [dcall(task="확인", kind="verifier"),
               _resp(calls=[("run_shell", '{"command": "mkdir -p __pycache__ && touch __pycache__/z.pyc"}')]),
               _resp("판정: 통과"), _resp("끝")]
        s, m, rec = self.go(seq)
        lg = json.loads((rec.dir / "ledger.json").read_text())
        self.assertEqual((lg["runs"][0]["verdict"], lg["runs"][0]["git_check"]), ("통과", "바뀜 없음"))

    def test_budget_from_profile(self):
        self.prof.delegate_budget = 3
        seq = [dcall(task="일", kind="verifier")] + [_resp(calls=[("read_file", '{"path": "a.py"}')])] * 3 \
            + [dcall(task="또", kind="verifier"), _resp("끝")]
        s, m, _ = self.go(seq)
        tool_msgs = [x["content"] for x in m.call_args_list[-1][0][1] if x["role"] == "tool"]
        self.assertIn("단계 합 상한(3)", tool_msgs[-1])


class TStepsFloor(unittest.TestCase):
    def test_floor_and_hint(self):
        from stewart import delegate as DG
        tmp = Path(tempfile.mkdtemp())
        prof = load_profile("manager")
        prof.workdir = tmp
        bk = Backend("fake", "http://x/v1", "m", "q", 4096)
        seq = [dcall(task="확인", kind="verifier", max_steps=1)] + [_resp(calls=[("read_file", '{"path": "x"}')])] * DG.MIN_STEPS \
            + [_resp("끝")]
        with mock.patch.object(engine, "chat", side_effect=seq) as m, \
                mock.patch.dict(os.environ, {"STEWART_SKILLS_DIR": str(tmp / "ns")}):
            s = engine.run(prof, bk, "x", Recorder(tmp / "runs", "m"))
        self.assertEqual(s["delegations"][0]["steps"], DG.MIN_STEPS)
        msg = [x["content"] for x in m.call_args_list[-1][0][1] if x["role"] == "tool"][0]
        self.assertIn("max_steps 를 빼거나 늘려라", msg)


class TManagerChat(unittest.TestCase):
    def test_chat_narrow_and_ledger(self):
        tmp = Path(tempfile.mkdtemp())
        prof = load_profile("manager")
        prof.workdir = tmp
        s = C.ChatSession(prof, Backend("fake", "http://x/v1", "m", "q", 4096), Recorder(tmp / "runs", "chat-m"), FakeUI())
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        self.assertEqual(sorted(s.tool_names), sorted(prof.manager_tools))
        self.assertIn("edit_file", s.all_tool_names)
        self.assertIsNotNone(s.ledger)
        with mock.patch.object(C, "chat", side_effect=[lcall(op="note", text="첫 메모"), _resp("적었다")]):
            s.turn("메모해")
        self.assertIn("첫 메모", s.ledger.show())
        self.assertEqual(s.summary()["ledger"]["runs"], 0)


if __name__ == "__main__":
    unittest.main()
