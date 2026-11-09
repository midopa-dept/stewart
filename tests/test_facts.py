"""실제로 한 일 기록(facts.json·facts.md): 바꾼 파일·셸 명령과 종료 코드·실패 호출. output.md·summary 는 facts 경로 말고 그대로."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, engine, facts as FX
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_engine import _resp

FINAL = "a.py 를 고쳤고 b.py 를 만들었다. 시험을 10번 돌렸다."


class TFacts(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a.py").write_text("x = 1\n")
        self.bk = Backend("fake", "http://x/v1", "m", "q", 4096)

    def prof(self):
        return Profile(name="code", path=self.tmp / "p.toml", system="s",
                       tools=["read_file", "edit_file", "write_file", "run_shell"], workdir=self.tmp, max_steps=10)

    def seq(self):
        return [_resp(calls=[("edit_file", '{"path": "a.py", "old": "x = 1", "new": "x = 2"}')]),
                _resp(calls=[("write_file", '{"path": ".env", "content": "K=1"}')]),          # 비밀 파일이라 거부(만들지 않음)
                _resp(calls=[("run_shell", '{"command": "echo ok"}')]),
                _resp(calls=[("run_shell", '{"command": "exit 3"}')]),
                _resp(calls=[("read_file", '{"path": "없음.py"}')]),
                _resp(FINAL)]

    def run_once(self, tag):
        (self.tmp / "a.py").write_text("x = 1\n")
        rec = Recorder(self.tmp / f"runs-{tag}", "t")
        with mock.patch.object(engine, "chat", side_effect=self.seq()):
            s = engine.run(self.prof(), self.bk, "고쳐", rec)
        return s, rec

    def test_facts_written(self):
        s, rec = self.run_once("a")
        f = json.loads((rec.dir / "facts.json").read_text())
        self.assertEqual(s["facts"], str(rec.dir / "facts.json"))
        self.assertEqual(f["stop_reason"], "completed")
        self.assertEqual(f["files"], [{"path": "a.py", "change": "고침", "tool": "edit_file"}])   # b.py 는 없다
        self.assertEqual(f["shell"], [{"command": "echo ok", "exit": 0}, {"command": "exit 3", "exit": 3}])
        self.assertEqual([x["tool"] for x in f["failed_calls"]], ["write_file", "run_shell", "read_file"])
        self.assertEqual(f["tool_calls"], 5)
        md = (rec.dir / "facts.md").read_text()
        for part in ("- 고침: a.py", "`exit 3` → exit 3", "## 실패·거부된 도구 호출", "- read_file:"):
            self.assertIn(part, md)
        self.assertNotIn("delegations", f)

    def test_output_and_summary_unchanged(self):
        s1, r1 = self.run_once("a")
        with mock.patch.object(FX, "write", side_effect=OSError("막음")):
            s2, r2 = self.run_once("b")
        self.assertEqual((r1.dir / "output.md").read_text(), FINAL)            # 모델 최종 출력 그대로
        self.assertEqual((r1.dir / "output.md").read_text(), (r2.dir / "output.md").read_text())
        self.assertEqual(set(s1), set(s2))                                      # 더한 키는 facts 하나
        diff = {k for k in s1 if k not in ("facts", "elapsed_s", "started", "run_dir", "model_time_s", "e2e_tok_per_s")
                and s1[k] != s2[k]}
        self.assertEqual(diff, set())
        self.assertTrue(s2["facts"].startswith("(쓰지 못함"))
        on_disk = json.loads((r1.dir / "summary.json").read_text())
        self.assertEqual(on_disk["facts"], s1["facts"])

    def test_facts_on_any_stop(self):
        rec = Recorder(self.tmp / "runs-c", "t")
        p = self.prof()
        p.max_steps = 1
        with mock.patch.object(engine, "chat", side_effect=[_resp(calls=[("run_shell", '{"command": "true"}')])]):
            s = engine.run(p, self.bk, "고쳐", rec)
        self.assertEqual(json.loads((rec.dir / "facts.json").read_text())["stop_reason"], "max_steps")
        self.assertFalse((rec.dir / "output.md").exists())

    def test_chat_facts(self):
        ui = FakeUI()
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["edit_file", "run_shell"], workdir=self.tmp,
                    max_steps=5)
        s = C.ChatSession(p, self.bk, Recorder(self.tmp / "runs-d", "chat-f"), ui, mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        seq = [_resp(calls=[("edit_file", '{"path": "a.py", "old": "x = 1", "new": "x = 5"}'),
                            ("run_shell", '{"command": "exit 2"}')]), _resp("고쳤다")]
        with mock.patch.object(C, "chat", side_effect=seq):
            s.turn("고쳐")
        md = s.facts_md()
        self.assertIn("# 차례 1 에 실제로 한 일", md)
        self.assertIn("- 종료 사유: answered", md)
        self.assertIn("- 고침: a.py", md)
        self.assertIn("`exit 2` → exit 2", md)


if __name__ == "__main__":
    unittest.main()
