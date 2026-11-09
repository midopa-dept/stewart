"""glob 도구: 경로 패턴, 수정 시각 정렬, 상한 알림, .gitignore."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import tools as T


class GlobTool(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for i, rel in enumerate(["a.py", "src/b.py", "src/sub/c.py", "src/sub/c.txt", "build/x.py",
                                 "tests/test_a.py", ".venv/lib.py"]):
            p = self.tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")
            os.utime(p, (1_000_000 + i * 10, 1_000_000 + i * 10))
        (self.tmp / ".gitignore").write_text("build/\n")
        self.env = T.ToolEnv(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def g(self, *a, **k):
        ok, out = T.glob_files(self.env, *a, **k)
        self.assertTrue(ok, out)
        return out.splitlines()

    def test_patterns(self):
        self.assertEqual(self.g("*.py", sort="name"), ["a.py"])                       # 맨 위만
        self.assertEqual(self.g("**/*.py", sort="name"), ["a.py", "src/b.py", "src/sub/c.py", "tests/test_a.py"])
        self.assertEqual(self.g("src/**/*.py", sort="name"), ["src/b.py", "src/sub/c.py"])
        self.assertEqual(self.g("**/test_*.py"), ["tests/test_a.py"])
        self.assertEqual(self.g("*.py", path="src", sort="name"), ["src/b.py"])        # path 기준, 결과는 작업 폴더 기준

    def test_mtime_sort(self):
        self.assertEqual(self.g("**/*.py"), ["tests/test_a.py", "src/sub/c.py", "src/b.py", "a.py"])

    def test_limit(self):
        d = self.tmp / "many"
        d.mkdir()
        for i in range(T.GLOB_MAX + 5):
            (d / f"f{i:05}.txt").write_text("")
        out = self.g("many/*.txt", sort="name")
        self.assertEqual(len(out), T.GLOB_MAX + 1)
        self.assertIn(f"전체 {T.GLOB_MAX + 5}개 중 {T.GLOB_MAX}개", out[-1])

    def test_scan_limit(self):
        with mock.patch.object(T, "GLOB_SCAN_MAX", 3):
            out = self.g("**/*")
        self.assertIn("훑고 멈춤", out[-1])

    def test_errors_and_execute(self):
        self.assertFalse(T.glob_files(self.env, "*", path="nope")[0])
        self.assertFalse(T.glob_files(self.env, "*", sort="size")[0])
        ok, out = T.execute(self.env, "glob", {"pattern": "**/*.md"})
        self.assertEqual((ok, out), (True, "(맞는 파일 없음)"))
        self.assertIn("glob", [d["function"]["name"] for d in T.definitions(["*"])])


if __name__ == "__main__":
    unittest.main()
