import tempfile
import unittest
from pathlib import Path

from stewart import tools as T

SRC = "def f(x):\n    if x:\n        return 1   \n    return 0\n\n\ndef g():\n    return 0\n"


class TestEditFuzzy(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.env = T.ToolEnv(workdir=Path(self.d.name))
        self.p = Path(self.d.name) / "m.py"
        self.p.write_text(SRC)

    def tearDown(self):
        self.d.cleanup()

    def edit(self, old, new):
        return T.edit_file(self.env, "m.py", old, new)

    def test_exact_unchanged(self):
        ok, msg = self.edit("return 1", "return 2")
        self.assertTrue(ok)
        self.assertNotIn("맞춤", msg)

    def test_trailing_space(self):
        ok, msg = self.edit("    if x:\n        return 1\n", "    if x:\n        return 2\n")
        self.assertTrue(ok, msg)
        self.assertIn("줄 끝 공백", msg)
        self.assertIn("        return 2\n    return 0", self.p.read_text())

    def test_indent_reapplied(self):
        ok, msg = self.edit("if x:\n    return 1", "if x is not None:\n    return 2")
        self.assertTrue(ok, msg)
        self.assertIn("들여쓰기", msg)
        self.assertIn("    if x is not None:\n        return 2\n    return 0", self.p.read_text())

    def test_ambiguous_refused(self):
        ok, msg = self.edit("  return 0  ", "return 9")
        self.assertFalse(ok)
        self.assertIn("2곳", msg)
        self.assertEqual(self.p.read_text(), SRC)

    def test_not_found(self):
        ok, msg = self.edit("nothing here", "x")
        self.assertFalse(ok)
        self.assertIn("찾지 못했다", msg)
        self.assertEqual(self.p.read_text(), SRC)

    def test_partial_line_not_fuzzy(self):
        ok, msg = self.edit("x:\n  return 1", "y")
        self.assertFalse(ok)
        self.assertEqual(self.p.read_text(), SRC)


if __name__ == "__main__":
    unittest.main()
