"""search 강화: 앞뒤 줄, files/count, 대소문자, .gitignore, rg 와 파이썬 방식이 같은 결과."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import fswalk, tools as T

HAS_RG = shutil.which("rg") is not None


def _tree(root: Path):
    files = {
        ".gitignore": "build/\n*.log\n!keep.log\n/rootonly.txt\n",
        "src/app.py": "import os\n\ndef main():\n    print('Hello')\n    return 0\n\n\ndef helper():\n    return 'hello'\n",
        "src/sub/util.py": "HELLO = 1\nx = 2\n",
        "src/sub/.gitignore": "gen_*.py\n",
        "src/sub/gen_a.py": "hello generated\n",
        "src/rootonly.txt": "hello not root\n",
        "rootonly.txt": "hello root\n",
        "build/out.py": "hello build\n",
        "a.log": "hello log\n",
        "keep.log": "hello keep\n",
        ".hidden/h.txt": "hello hidden\n",
        ".git/config": "hello git\n",
        "node_modules/m.js": "hello nm\n",
        "docs/readme.md": "nothing here\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (root / "bin.dat").write_bytes(b"\xff\xfe hello \x00\x01")


class SearchBoth(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _tree(self.tmp)
        self.env = T.ToolEnv(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_py(self, *a, **k):
        with mock.patch.dict(os.environ, {"STEWART_NO_RG": "1"}):
            return T.search(self.env, *a, **k)

    def both(self, *a, **k):
        py = self.run_py(*a, **k)
        if HAS_RG:
            rg = T.search(self.env, *a, **k)
            self.assertEqual(rg, py, f"rg 와 파이썬 결과가 다르다: {a} {k}")
        return py

    def test_gitignore_and_skip(self):
        ok, out = self.both("hello", output="files")
        self.assertTrue(ok)
        files = out.splitlines()
        self.assertEqual(files, [".hidden/h.txt", "keep.log", "src/app.py", "src/rootonly.txt"])

    def test_ignore_case_and_count(self):
        _, out = self.both("hello", output="count", ignore_case=True, path="src")
        self.assertEqual(out.splitlines(), ["src/app.py:2", "src/rootonly.txt:1", "src/sub/util.py:1"])

    def test_context(self):
        _, out = self.both("return", path="src/app.py", context=1)
        self.assertEqual(out.splitlines(), [
            "src/app.py-4-    print('Hello')", "src/app.py:5:    return 0", "src/app.py-6-",
            "--",
            "src/app.py-8-def helper():", "src/app.py:9:    return 'hello'"])

    def test_context_across_files(self):
        _, out = self.both("hello", path="src", context=1, ignore_case=True)
        lines = out.splitlines()
        i = lines.index("src/rootonly.txt:1:hello not root")
        self.assertEqual(lines[i - 1], "--")          # 파일 사이에도 구분선(rg 와 같게)
        self.assertEqual(lines.count("--"), 3)

    def test_glob_name_and_path(self):
        _, out = self.both("hello", glob="*.py", ignore_case=True, output="files")
        self.assertEqual(out.splitlines(), ["src/app.py", "src/sub/util.py"])
        _, out = self.both("hello", glob="src/**/*.py", ignore_case=True, output="files")
        self.assertEqual(out.splitlines(), ["src/app.py", "src/sub/util.py"])
        _, out = self.both("hello", glob="src/sub/*.py", ignore_case=True, output="files")
        self.assertEqual(out.splitlines(), ["src/sub/util.py"])

    def test_content_default_format(self):
        _, out = self.both("HELLO")
        self.assertEqual(out, "src/sub/util.py:1:HELLO = 1")

    def test_limit(self):
        (self.tmp / "big.txt").write_text("".join(f"hit {i}\n" for i in range(500)))
        _, out = self.both("hit", path="big.txt")
        lines = out.splitlines()
        self.assertEqual(len(lines), T.SEARCH_MAX + 1)
        self.assertIn("넘어서 멈춤", lines[-1])

    def test_python_only_regex_falls_back(self):
        # 뒤돌아보기는 rg 기본 엔진이 못 받는다 → 파이썬 방식으로 다시
        ok, out = T.search(self.env, r"(?<=def )helper", path="src")
        self.assertTrue(ok)
        self.assertEqual(out, "src/app.py:8:def helper():")

    def test_errors(self):
        self.assertFalse(T.search(self.env, "(")[0])
        self.assertFalse(T.search(self.env, "x", path="nope")[0])
        self.assertFalse(T.search(self.env, "x", output="lines")[0])

    def test_ancestor_gitignore(self):
        # 찾는 폴더 위의 .gitignore 도 따른다(src 에서 찾아도 build/ 규칙이 산다)
        (self.tmp / "src/build").mkdir()
        (self.tmp / "src/build/x.py").write_text("hello\n")
        _, out = self.both("hello", path="src", output="files")
        self.assertNotIn("src/build/x.py", out)

    def test_execute_schema(self):
        ok, out = T.execute(self.env, "search", {"pattern": "hello", "output": "files", "context": 2,
                                                 "ignore_case": True, "glob": "*.md"})
        self.assertTrue(ok)
        self.assertEqual(out, "(일치 없음)")


class Glob(unittest.TestCase):
    def test_glob_rx(self):
        m = fswalk.glob_match
        self.assertTrue(m("a/b/c.py", "*.py"))
        self.assertTrue(m("c.py", "**/*.py"))
        self.assertTrue(m("a/b/c.py", "**/*.py"))
        self.assertTrue(m("a/b/c.py", "a/**/c.py"))
        self.assertTrue(m("a/c.py", "a/**/c.py"))
        self.assertFalse(m("a/b/c.py", "a/*.py"))
        self.assertTrue(m("a/x1.py", "a/x[0-9].py"))
        self.assertFalse(m("a/xa.py", "a/x[!a-z].py"))

    def test_rules(self):
        d = Path("/r")
        rules = [fswalk.Rule(d, l) for l in ["*.log", "!keep.log", "/top", "build/", "docs/**/tmp"]]
        ig = lambda p, isdir=False: fswalk.ignored(rules, d / p, isdir)
        self.assertTrue(ig("x/a.log"))
        self.assertFalse(ig("x/keep.log"))
        self.assertTrue(ig("top"))
        self.assertFalse(ig("x/top"))
        self.assertTrue(ig("x/build", True))
        self.assertFalse(ig("x/build"))
        self.assertTrue(ig("docs/a/b/tmp"))


if __name__ == "__main__":
    unittest.main()
