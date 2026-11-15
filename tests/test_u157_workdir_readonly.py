"""읽기 전용 말뭉치 폴더를 작업 폴더로 줄 때 엔진이 그 안에 쓰는 것이 없다(쓰기 0).

- `chat --once --worker read`(허브가 띄우는 읽기 작업자와 같은 설정)와 `chat --once -p chat` 을 실사용 꼴 환경(빈 HOME·기본 켬 장치 그대로)에서 돌린다.
- 모델은 읽기 도구를 모두 부른다(list_dir·read_file·search·glob·doc_search·todo_write·web_fetch 가운데 그 자리에 있는 것).
- 작업 폴더를 읽기 전용(폴더 0555·파일 0444)으로 두고, 앞뒤로 모든 항목(경로·크기·수정 시각·권한)이 같고 새 항목이 없음을 본다.
  쓸 수 있는 폴더에서도 같은 대조를 한다(읽기 전용이라 조용히 실패한 쓰기가 숨지 않게).
"""

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_hub import msg
from tests.test_realuse_shape import FakeModel, is_worker, make_user_shape

CALLS = [("list_dir", {"path": "."}), ("read_file", {"path": "a.md"}), ("search", {"pattern": "needle"}),
         ("glob", {"pattern": "**/*.md"}), ("doc_search", {"query": "needle"}), ("todo_write", {"items": [{"text": "읽기", "status": "doing"}]}),
         ("web_fetch", {"url": "http://127.0.0.1:9/x"})]


def snapshot(root: Path) -> dict:
    out = {}
    for p in [root, *root.rglob("*")]:
        st = p.lstat()
        out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns, stat.S_IMODE(st.st_mode))
    return out


class TWorkdirReadonly(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="u157-"))
        self.model = FakeModel(route=self.route)
        self.addCleanup(self.model.close)
        self.env = make_user_shape(self.root, self.model.url)
        c = self.corpus = self.root / "corpus"
        (c / "sub" / "deep").mkdir(parents=True)
        (c / "a.md").write_text("# 가\nneedle 하나\n", encoding="utf-8")
        (c / "sub" / "b.txt").write_text("둘 needle\n", encoding="utf-8")
        (c / "sub" / "deep" / "c.md").write_text("셋\n", encoding="utf-8")
        self.addCleanup(lambda: [p.chmod(0o755) for p in [c, *c.rglob("*")] if p.is_dir()])
        self.called, self.results = [], []

    def lock(self):
        c = self.corpus
        for p in sorted(c.rglob("*"), reverse=True):
            p.chmod(0o555 if p.is_dir() else 0o444)
        c.chmod(0o555)

    def route(self, body):
        tools = [str(m.get("content")) for m in body.get("messages") or [] if m.get("role") == "tool"]
        if tools:
            self.results.append(tools)
            return msg("다 읽었다")
        have = {t["function"]["name"] for t in body.get("tools") or []}
        calls = [(n, a) for n, a in CALLS if n in have]
        self.called.append(sorted(n for n, _ in calls))
        return msg(None, calls)

    def run_once(self, *args):
        before = snapshot(self.corpus)
        r = subprocess.run([sys.executable, "-m", "stewart", "chat", "--once", "--backend", "cloud", *args,
                            "--workdir", str(self.corpus), "말뭉치에서 needle 을 찾아 줘"], env=self.env, cwd=str(self.root),
                           capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        self.assertEqual(snapshot(self.corpus), before)                                              # 작업 폴더 쓰기 0
        self.assertTrue(self.results and "needle 하나" in "\n".join(self.results[0]), self.results)       # 읽기는 실제로 됐다
        return r

    def test_read_worker(self):
        self.lock()
        self.run_once("-p", "chat", "--worker", "read")
        self.assertTrue(self.called and {"read_file", "list_dir", "search"} <= set(self.called[0]), self.called)

    def test_read_worker_writable(self):   # 쓸 수 있는 폴더에서도 아무것도 생기지 않는다(조용히 실패한 쓰기가 없다)
        self.run_once("-p", "chat", "--worker", "read")

    def test_chat_once(self):
        self.lock()
        self.run_once("-p", "chat")

    def test_chat_once_writable(self):
        self.run_once("-p", "chat")
        self.assertTrue(self.called and {"read_file", "doc_search", "todo_write"} <= set(self.called[0]), self.called)


if __name__ == "__main__":
    unittest.main()
