"""todo 도구: 통째로 쓰기, 상태 검사, 진행 중 하나, summary 에 남김."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import engine, tools as T
from stewart.config import Backend, Profile
from stewart.record import Recorder


class Todo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = T.ToolEnv(self.tmp)

    def test_write_read(self):
        self.assertEqual(T.todo_read(self.env), (True, "(할 일 목록 비어 있음)"))
        ok, out = T.execute(self.env, "todo_write", {"items": [
            {"content": "시험 읽기", "status": "done"}, {"content": "고치기", "status": "in_progress"},
            {"content": "시험 돌리기", "status": "pending"}]})
        self.assertTrue(ok)
        self.assertEqual(out.splitlines(), ["할 일 3개: 끝 1, 진행 중 1, 남음 1",
                                            "1. [x] 시험 읽기", "2. [~] 고치기", "3. [ ] 시험 돌리기"])
        self.assertEqual(T.execute(self.env, "todo_read", {})[1], out)

    def test_rejects_keep_old(self):
        T.todo_write(self.env, [{"content": "a", "status": "pending"}])
        bad = [
            [{"content": "a", "status": "in_progress"}, {"content": "b", "status": "in_progress"}],
            [{"content": "a", "status": "doing"}],
            [{"content": "", "status": "pending"}],
            [{"content": "a", "status": "pending", "prio": 1}],
            [{"content": str(i), "status": "pending"} for i in range(T.TODO_MAX + 1)],
            "a,b",
        ]
        for items in bad:
            self.assertFalse(T.todo_write(self.env, items)[0], items)
        self.assertEqual(self.env.todos, [{"content": "a", "status": "pending"}])   # 그대로

    def test_summary(self):
        from tests.test_engine import _resp
        prof = Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["todo_write"], workdir=self.tmp, max_steps=4)
        seq = [_resp(calls=[("todo_write", json.dumps({"items": [{"content": "하나", "status": "done"}]}))]),
               _resp(content="끝")]
        with mock.patch.object(engine, "chat", side_effect=seq):
            s = engine.run(prof, Backend("fake", "http://x/v1", "m", "fp16", 4096), "do", Recorder(self.tmp / "runs", "t"))
        self.assertEqual(s["todos"], [{"content": "하나", "status": "done"}])


if __name__ == "__main__":
    unittest.main()
