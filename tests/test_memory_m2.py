"""기억 M2: stewart dream(쓰는 자 하나) — 새 메모만, 근거 확인·내리기, 꼴·크기 검사, MEMORY.md 는 제안 → --accept, 잠금, memory_read."""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from stewart import cli, dream as DR, memory as MEM
from stewart.config import Profile

KEY = "dir-work-12345678"


def page(*facts, traps=("- 테스트는 python3 -m unittest 로만 돈다 (run r1)",), decisions=("- 빌드는 make 를 쓰지 않는다",)):
    return "\n".join(["# work", "## 요약", "작은 계산기 저장소.", "## 확인된 사실", *facts, "## 함정", *traps,
                      "## 결정", *decisions, "## 미확인", "- 없음"])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.root = self.tmp / "mem"
        self.runs = self.tmp / "runs"
        self.runs.mkdir()
        (self.runs / "index.jsonl").write_text("\n".join(json.dumps({"run": r}) for r in ("r1", "r2")) + "\n")
        self.sdir = self.root / KEY
        (self.sdir / "notes").mkdir(parents=True)
        self.note("r1", by="engine", claim="판정 명령 → exit 0", status="confirmed")
        self.note("r1", by="model", claim="설정은 calc/conf.py 에 있다", status="confirmed")
        self.note("ghost", by="model", claim="유령 실행의 주장", status="confirmed")
        self.calls = []

    def note(self, run, **k):
        with open(self.sdir / "notes" / f"{run}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"run": run, "evidence": "e", **k}, ensure_ascii=False) + "\n")

    def ask(self, text):
        def f(system, user):
            self.calls.append(user)
            return text
        return f

    def go(self, text, **k):
        return DR.dream_subject(self.root, KEY, self.ask(text), self.runs, **k)


class TDream(Base):
    def test_page_index_proposed_and_demote(self):
        r = self.go(page("- 설정은 calc/conf.py 에 있다 (run r1)", "- 유령 실행의 주장 (run ghost)", "- 근거 없는 줄"))
        self.assertIn("근거 없음", self.calls[0])                              # 유령 실행 메모는 근거 없음으로 보인다
        p = (self.sdir / "page.md").read_text()
        conf = p.split("## 함정")[0]
        self.assertIn("calc/conf.py 에 있다 (run r1)", conf)
        self.assertNotIn("ghost", conf)
        unconf = p.split("## 미확인")[1]
        self.assertIn("유령 실행의 주장 (run ghost) (근거 확인 못 함 — 꿈꾸기가 내림)", unconf)
        self.assertIn("근거 없는 줄", unconf)
        self.assertIn("빌드는 make 를 쓰지 않는다", unconf)                    # 결정도 근거가 있어야
        self.assertFalse((self.sdir / "MEMORY.md").exists())                  # 색인은 제안만
        idx = (self.sdir / "MEMORY.md.proposed").read_text().splitlines()
        self.assertEqual(idx[0], f"- 쪽: memory_read(subject={KEY}) — 작은 계산기 저장소.")
        self.assertEqual(idx[1], "- [함정] 테스트는 python3 -m unittest 로만 돈다 (run r1)")   # 함정이 먼저
        self.assertTrue(any("[확인된 사실]" in x for x in idx))
        self.assertIn("쪽 바꿈", r["status"])
        self.assertTrue(list((self.sdir / "dream-log").glob("*-diff.diff")))
        r2 = self.go("안 불림")
        self.assertEqual((r2["status"], len(self.calls)), ("새 메모 없음", 1))   # 새 메모가 없으면 모델을 안 부른다
        self.note("r2", by="model", claim="새 사실", status="confirmed")
        self.go(page("- 새 사실 (run r2)", "- 설정은 calc/conf.py 에 있다 (run r1)"))
        self.assertIn("새 메모(1개)", self.calls[-1])                           # 새로 생긴 것만
        self.assertIn("calc/conf.py", self.calls[-1])                           # 기존 쪽은 입력에 들어간다

    def test_accept_and_injection(self):
        self.go(page("- 설정은 calc/conf.py 에 있다 (run r1)"))
        out = io.StringIO()
        with mock.patch.dict(os.environ, {MEM.ENV_DIR: str(self.root)}), redirect_stdout(out):
            self.assertEqual(cli.main(["dream", "--accept", KEY]), 0)
        self.assertIn("색인 들임", out.getvalue())
        self.assertFalse((self.sdir / "MEMORY.md.proposed").exists())
        m = MEM.Memory.__new__(MEM.Memory)
        m.dir, m.subjects = self.root, [{"key": KEY, "kind": "dir", "name": "work", "path": "/w"}]
        text, log = m.inject()
        self.assertIn("memory_read(subject=", text)                             # M1 주입이 이 색인을 읽는다
        self.assertLessEqual(log[0]["lines"], MEM.INJECT_LINES)

    def test_yes_and_dry_run(self):
        r = self.go(page("- 설정은 calc/conf.py 에 있다 (run r1)"), dry_run=True)
        self.assertEqual(r["status"], "보기만(쓰지 않음)")
        self.assertIn("+++ b/page.md", r["diff"])
        self.assertFalse((self.sdir / "page.md").exists())
        self.assertFalse((self.sdir / "dream-state.json").exists())             # 보기만이면 처리 표시도 안 남김
        self.go(page("- 설정은 calc/conf.py 에 있다 (run r1)"), yes=True)
        self.assertTrue((self.sdir / "MEMORY.md").exists())

    def test_reject_bad_or_big(self):
        r = self.go("그냥 글이다")
        self.assertEqual(r["status"], "쪽을 받지 않음(그대로 둠)")
        self.assertFalse((self.sdir / "page.md").exists())
        self.assertFalse((self.sdir / "dream-state.json").exists())             # 다음에 다시 시도
        r = self.go(page(*[f"- 사실 {i} " + "가" * 100 + " (run r1)" for i in range(120)]))
        self.assertIn("상한", " ".join(r["notices"]))
        self.assertTrue(list((self.sdir / "dream-log").glob("*-rejected.diff")))

    def test_index_caps(self):
        self.go(page(*[f"- 사실 {i} " + "나" * 60 + " (run r1)" for i in range(50)]), yes=True)
        t = (self.sdir / "MEMORY.md").read_text()
        self.assertLessEqual(len(t.splitlines()), MEM.INJECT_LINES)
        self.assertLessEqual(len(t.encode()), MEM.INJECT_BYTES)

    def test_lock(self):
        (self.sdir / ".dream.lock").write_text("1")
        rows = DR.run([KEY], None, self.runs, root=self.root, ask_model=self.ask("x"))
        self.assertIn("다른 꿈꾸기", rows[0]["status"])
        self.assertEqual(self.calls, [])
        rows = DR.run(["../etc"], None, self.runs, root=self.root, ask_model=self.ask("x"))
        self.assertEqual(rows[0]["status"], "없는 대상")


class TRead(Base):
    def test_memory_read_tool(self):
        wd = self.tmp / "work"
        wd.mkdir()
        p = Profile(name="code", path=self.tmp / "p.toml", system="s", tools=["read_file"], workdir=wd, memory=True)
        with mock.patch.dict(os.environ, {MEM.ENV_DIR: str(self.root)}):
            m = MEM.Memory(p, self.runs / "rX")
            key = m.place["key"]
            ok, msg = MEM.execute(m, "memory_read", {})
            self.assertFalse(ok)
            self.assertIn("stewart dream 이 만든다", msg)
            (self.root / key).mkdir(parents=True, exist_ok=True)
            (self.root / key / "page.md").write_text(page("- x (run r1)"))
            ok, msg = MEM.execute(m, "memory_read", {})
            self.assertTrue(ok)
            self.assertIn("작은 계산기 저장소", msg)
            ok, msg = MEM.execute(m, "memory_read", {"subject": "../x"})
            self.assertFalse(ok)
        self.assertIn("memory_read", [d["function"]["name"] for d in MEM.tool_defs()])
        self.assertIn("memory_read", MEM.PURPOSE)


if __name__ == "__main__":
    unittest.main()
