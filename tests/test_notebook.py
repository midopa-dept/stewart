"""notebook_edit·read_file 노트북 보기."""

import json
import tempfile
import unittest
from pathlib import Path

from stewart import tools as T

NB = {"cells": [
    {"cell_type": "markdown", "id": "a1", "metadata": {}, "source": ["# 제목\n", "설명"]},
    {"cell_type": "code", "id": "b2", "execution_count": 3, "metadata": {"tags": ["x"]}, "source": ["x = 1\n", "print(x)"],
     "outputs": [{"output_type": "stream", "name": "stdout", "text": ["1\n"]},
                 {"output_type": "display_data", "data": {"image/png": "AAAA"}, "metadata": {}}]},
], "metadata": {"kernelspec": {"name": "python3"}}, "nbformat": 4, "nbformat_minor": 5}


class Notebook(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = self.tmp / "n.ipynb"
        self.p.write_text(json.dumps(NB, indent=1, ensure_ascii=False) + "\n")
        self.env = T.ToolEnv(self.tmp)

    def nb(self):
        return json.loads(self.p.read_text())

    def test_read_render(self):
        ok, out = T.read_file(self.env, "n.ipynb")
        self.assertTrue(ok)
        self.assertIn("칸 2개", out)
        self.assertIn("### 칸 2 [code]", out)
        self.assertIn("print(x)", out)
        self.assertIn("--- 출력 ---", out)
        self.assertIn("[image/png 출력]", out)
        self.assertNotIn("AAAA", out)

    def test_replace_clears_outputs_keeps_rest(self):
        ok, out = T.execute(self.env, "notebook_edit", {"path": "n.ipynb", "cell": 2, "source": "y = 2\nprint(y)\n"})
        self.assertTrue(ok, out)
        c = self.nb()["cells"][1]
        self.assertEqual(c["source"], ["y = 2\n", "print(y)\n"])
        self.assertEqual((c["outputs"], c["execution_count"], c["id"], c["metadata"]), ([], None, "b2", {"tags": ["x"]}))
        self.assertEqual(self.nb()["metadata"], NB["metadata"])
        self.assertTrue(self.p.read_text().startswith('{\n "cells": ['))       # indent 1 유지
        self.assertTrue(self.p.read_text().endswith("}\n"))

    def test_insert_delete_type(self):
        self.assertTrue(T.notebook_edit(self.env, "n.ipynb", 3, "끝 메모", mode="insert", cell_type="markdown")[0])
        self.assertTrue(T.notebook_edit(self.env, "n.ipynb", 1, "import os", mode="insert")[0])
        cells = self.nb()["cells"]
        self.assertEqual([c["cell_type"] for c in cells], ["code", "markdown", "code", "markdown"])
        self.assertEqual((cells[0]["outputs"], len(cells[0]["id"])), ([], 8))
        self.assertTrue(T.notebook_edit(self.env, "n.ipynb", 2, "", mode="delete")[0])
        self.assertEqual(len(self.nb()["cells"]), 3)
        self.assertTrue(T.notebook_edit(self.env, "n.ipynb", 1, "그냥 글", cell_type="markdown")[0])
        c = self.nb()["cells"][0]
        self.assertEqual(c["cell_type"], "markdown")
        self.assertNotIn("outputs", c)

    def test_errors(self):
        for a in ({"cell": 0}, {"cell": 3}, {"cell": 4, "mode": "insert"}, {"cell": 1, "mode": "move"},
                  {"cell": 1, "cell_type": "sql"}):
            self.assertFalse(T.notebook_edit(self.env, "n.ipynb", **a)[0], a)
        (self.tmp / "bad.ipynb").write_text("{not json")
        self.assertFalse(T.notebook_edit(self.env, "bad.ipynb", 1)[0])
        self.assertTrue(T.read_file(self.env, "bad.ipynb")[0])                  # 깨진 노트북은 원문 그대로 읽는다
        self.assertFalse(T.notebook_edit(self.env, "a.py", 1)[0])
        self.assertEqual(self.nb(), NB)                                          # 실패는 파일을 바꾸지 않는다


if __name__ == "__main__":
    unittest.main()
