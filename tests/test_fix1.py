"""되돌리기 좁히기(backend_error·verify 통과면 안 되돌림), delegate 결과 글에 기록 경로 없음."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import engine
from stewart.backend import BackendError
from stewart.config import Backend, Profile, load_profile
from stewart.record import Recorder
from tests.test_engine import _resp

EDIT = ("edit_file", '{"path": "a.py", "old": "x = 1", "new": "x = 2"}')


class TFix1(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.wd = self.tmp / "wd"
        self.wd.mkdir()
        (self.wd / "a.py").write_text("x = 1\n")
        p = mock.patch.dict(os.environ, {"STEWART_SKILLS_DIR": str(self.tmp / "ns"), "HOME": str(self.tmp / "home")})
        p.start()
        self.addCleanup(p.stop)
        self.bk = Backend("fake", "http://x/v1", "m", "q", 150000)

    def prof(self, verify=None):
        return Profile(name="code", path=self.tmp / "p.toml", system="s", tools=["edit_file", "read_file"], workdir=self.wd,
                       max_steps=2, rollback_unfinished=True, verify_command=verify)

    def run_(self, prof, seq, tag):
        rec = Recorder(self.tmp / "runs", tag)
        with mock.patch.object(engine, "chat", side_effect=seq):
            s = engine.run(prof, self.bk, "고쳐", rec)
        return s, json.loads((rec.dir / "facts.json").read_text()), (rec.dir / "facts.md").read_text(), rec

    def test_backend_error_not_rolled_back(self):
        s, f, md, _ = self.run_(self.prof(), [_resp(calls=[EDIT]), BackendError("연결 실패: timed out")], "be")
        self.assertEqual(s["stop_reason"], "backend_error")
        self.assertEqual((self.wd / "a.py").read_text(), "x = 2\n")
        self.assertIsNone(s["rolled_back"])
        self.assertTrue(s["not_rolled_back_reason"].startswith("backend_error"))
        self.assertEqual(f["files"], [{"path": "a.py", "change": "고침", "tool": "edit_file"}])
        self.assertIn("## 되돌리지 않음", md)

    def test_verify_pass_not_rolled_back(self):
        s, f, md, rec = self.run_(self.prof("grep -q 'x = 2' a.py"), [_resp(calls=[EDIT]), _resp(calls=[EDIT])], "vp")
        self.assertEqual(s["stop_reason"], "max_steps")
        self.assertEqual((self.wd / "a.py").read_text(), "x = 2\n")
        self.assertEqual(s["not_rolled_back_reason"], "verify_command 통과(exit 0)")
        self.assertTrue(s["verify"]["passed"] and s["success"])
        self.assertIn("verify_command 통과", md)
        ev = [json.loads(l) for l in (rec.dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual([e.get("stage") for e in ev if e["kind"] == "verify"], ["before_rollback"])   # 한 번만 돈다

    def test_verify_fail_rolled_back(self):
        s, f, md, rec = self.run_(self.prof("grep -q 'x = 9' a.py"), [_resp(calls=[EDIT]), _resp(calls=[EDIT])], "vf")
        self.assertEqual((self.wd / "a.py").read_text(), "x = 1\n")
        self.assertEqual(s["rolled_back"]["restored"], 1)
        self.assertIsNone(s["not_rolled_back_reason"])
        self.assertFalse(s["verify"]["passed"])
        self.assertEqual(f["files"][0]["change"], "고침 → 되돌림")
        ev = [json.loads(l) for l in (rec.dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual([e.get("stage") for e in ev if e["kind"] == "verify"], ["before_rollback", "final"])

    def test_no_verify_rolls_back_as_before(self):
        s, *_ = self.run_(self.prof(), [_resp(calls=[EDIT]), _resp(calls=[EDIT])], "nv")
        self.assertEqual((self.wd / "a.py").read_text(), "x = 1\n")
        self.assertEqual(s["rolled_back"]["restored"], 1)

    def test_delegate_result_has_no_record_path(self):
        prof = load_profile("manager")
        prof.workdir, prof.verify_command = self.wd, None
        seq = [_resp(calls=[("delegate", json.dumps({"task": "고쳐", "kind": "worker"}, ensure_ascii=False))]),
               _resp(calls=[EDIT]), _resp("고쳤다"), _resp("끝")]
        rec = Recorder(self.tmp / "runs", "m")
        with mock.patch.object(engine, "chat", side_effect=seq) as m:
            s = engine.run(prof, self.bk, "고쳐", rec)
        tool_msgs = [x for x in m.call_args_list[-1][0][1] if x.get("role") == "tool"]
        text = tool_msgs[-1]["content"]
        self.assertIn("[작업자 1: 종료 completed", text)
        self.assertNotIn(str(rec.dir), text)
        self.assertNotIn("/sub/", text)
        self.assertNotIn("기록", text.splitlines()[0])
        self.assertTrue(s["delegations"][0]["dir"].startswith(str(rec.dir)))   # 경로는 summary 에만


if __name__ == "__main__":
    unittest.main()
