"""multi_edit + 쓰기 안전장치: 모두 적용 또는 모두 취소, 읽지 않은 기존 파일 덮어쓰기 거부,
읽은 뒤 바뀐 파일 거부(정확 일치·퍼지 매칭·multi_edit·notebook_edit 모두)."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from stewart import tools as T

SRC = "def f(x):\n    return x + 1\n\n\ndef g(y):\n    return y * 2\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = self.tmp / "m.py"
        self.p.write_text(SRC)
        self.env = T.ToolEnv(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def touch_outside(self, text):
        """도구 밖에서 파일을 바꾼다(수정 시각이 확실히 달라지게)."""
        self.p.write_text(text)
        st = self.p.stat()
        os.utime(self.p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


class MultiEdit(Base):
    def test_all_applied_in_order(self):
        ok, out = T.execute(self.env, "multi_edit", {"path": "m.py", "edits": [
            {"old": "return x + 1", "new": "return x + 10"},
            {"old": "x + 10", "new": "x + 100"},                      # 앞 고치기가 적용된 글에서 찾는다
            {"old": "def g(y):", "new": "def h(y):"}]})
        self.assertTrue(ok, out)
        self.assertIn("3개 모두 적용", out)
        self.assertEqual(self.p.read_text(), SRC.replace("x + 1", "x + 100").replace("def g", "def h"))

    def test_all_or_nothing(self):
        before = self.p.read_bytes()
        for edits in ([{"old": "return x + 1", "new": "A"}, {"old": "nope", "new": "B"}],
                      [{"old": "return", "new": "R"}],                  # 두 번 나옴
                      [{"old": "return x + 1", "new": "A"}, {"new": "B"}],
                      [{"old": "return x + 1", "new": "A", "x": 1}],
                      []):
            ok, out = T.multi_edit(self.env, "m.py", edits)
            self.assertFalse(ok, edits)
            self.assertEqual(self.p.read_bytes(), before, edits)
        ok, out = T.multi_edit(self.env, "m.py", [{"old": "return x + 1", "new": "A"}, {"old": "nope", "new": "B"}])
        self.assertIn("2번째 고치기 실패", out)
        self.assertIn("앞 1개도 취소", out)

    def test_fuzzy_and_lineno_inside(self):
        ok, out = T.multi_edit(self.env, "m.py", [
            {"old": "    2\t    return x + 1", "new": "    2\t    return x - 1"},      # 줄 번호 접두어
            {"old": "def g(y):\n  return y * 2", "new": "def g(y):\n  return y * 3"},   # 들여쓰기만 다름(퍼지)
            {"old": "def", "new": "def", "replace_all": True}])
        self.assertTrue(ok, out)
        self.assertIn("return x - 1", self.p.read_text())
        self.assertIn("return y * 3", self.p.read_text())               # 들여쓰기 맞춤은 edit-fuzzy 규칙(첫 줄 기준) 그대로
        self.assertFalse(T.multi_edit(self.env, "m.py", [{"old": "a", "new": "b"}] * (T.MULTI_EDIT_MAX + 1))[0])

    def test_secret(self):
        (self.tmp / "k.pem").write_text("KEY")
        self.assertFalse(T.multi_edit(self.env, "k.pem", [{"old": "KEY", "new": "X"}])[0])
        self.assertEqual((self.tmp / "k.pem").read_text(), "KEY")


class Guards(Base):
    def test_write_existing_needs_read(self):
        ok, out = T.write_file(self.env, "m.py", "new")
        self.assertFalse(ok)
        self.assertIn("read_file 로 먼저 읽는다", out)
        self.assertEqual(self.p.read_text(), SRC)
        self.assertTrue(T.write_file(self.env, "new.py", "x = 1\n")[0])          # 새 파일은 그냥
        self.assertTrue(T.write_file(self.env, "new.py", "x = 2\n")[0])          # 우리가 쓴 파일은 읽은 것으로 친다
        T.read_file(self.env, "m.py", limit=1)                                   # 일부만 읽어도 읽은 것
        self.assertTrue(T.write_file(self.env, "m.py", "new")[0])

    def test_edit_without_read_allowed(self):
        self.assertTrue(T.edit_file(self.env, "m.py", "x + 1", "x + 2")[0])     # 읽기 강제 안 함
        self.assertTrue(T.edit_file(self.env, "m.py", "x + 2", "x + 3")[0])     # 우리가 쓴 상태라 바뀜 아님

    def test_changed_after_read(self):
        T.read_file(self.env, "m.py")
        self.touch_outside(SRC.replace("y * 2", "y * 9"))
        for name, args in (("edit_file", {"old": "x + 1", "new": "x + 5"}),
                           ("edit_file", {"old": "def g(y):\n  return y * 9", "new": "def g(y):\n  return 0"}),   # 퍼지 경로
                           ("multi_edit", {"edits": [{"old": "x + 1", "new": "x + 5"}]}),
                           ("write_file", {"content": "all new"})):
            ok, out = T.execute(self.env, name, {"path": "m.py", **args})
            self.assertFalse(ok, name)
            self.assertIn("읽은 뒤 바뀐 파일", out, name)
        self.assertIn("y * 9", self.p.read_text())
        T.read_file(self.env, "m.py")                                            # 다시 읽으면 된다
        self.assertTrue(T.edit_file(self.env, "m.py", "x + 1", "x + 5")[0])

    def test_shell_change_detected(self):
        T.read_file(self.env, "m.py")
        T.run_shell(self.env, "sleep 0.01; printf 'z = 0\\n' >> m.py")
        ok, out = T.edit_file(self.env, "m.py", "x + 1", "x + 2")
        self.assertFalse(ok)
        self.assertIn("run_shell", out)

    def test_notebook(self):
        nb = self.tmp / "n.ipynb"
        nb.write_text(json.dumps({"cells": [{"cell_type": "code", "metadata": {}, "source": ["a = 1"], "outputs": [],
                                             "execution_count": None}], "metadata": {}, "nbformat": 4, "nbformat_minor": 4}))
        T.read_file(self.env, "n.ipynb")
        self.assertTrue(T.notebook_edit(self.env, "n.ipynb", 1, "a = 2")[0])
        self.assertTrue(T.notebook_edit(self.env, "n.ipynb", 1, "a = 3")[0])    # 우리가 쓴 상태
        d = json.loads(nb.read_text())
        d["cells"][0]["source"] = ["changed"]
        nb.write_text(json.dumps(d))
        st = nb.stat()
        os.utime(nb, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        ok, out = T.notebook_edit(self.env, "n.ipynb", 1, "a = 4")
        self.assertFalse(ok)
        self.assertIn("읽은 뒤 바뀐 파일", out)

    def test_fuzzy_marks_seen(self):
        T.read_file(self.env, "m.py")
        ok, out = T.edit_file(self.env, "m.py", "def g(y):\n  return y * 2", "def g(y):\n  return y * 4")
        self.assertTrue(ok, out)
        self.assertTrue(T.edit_file(self.env, "m.py", "x + 1", "x + 7")[0])     # 퍼지로 쓴 뒤에도 바뀜으로 보지 않는다


if __name__ == "__main__":
    unittest.main()
