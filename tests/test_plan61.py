"""계획은 넣는 즉시 plan.json + 사람용 plan.md 에 저장되고, 모델·사람이 그 사실과 경로를 안다."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from stewart import chat as C, tools as T
from stewart.config import Backend, Profile, load_profile
from stewart.record import Recorder


class TPlanMd(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "root"
        for rel in ("사진/a.jpg", "사진/b.jpg", "문서/r.pdf", "junk.tmp"):
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text("x")
        self.runs = self.tmp / "runs"
        self.env = T.ToolEnv(self.root, artifacts_dir=self.runs)
        T.folder_scan(self.env)

    def test_saved_immediately_with_md(self):
        ok, out = T.plan_add(self.env, [
            {"op": "move", "src": "사진", "dst": "Photos", "reason": "사진 폴더", "confidence": 0.9, "evidence": "scan"},
            {"op": "move", "src": "문서/r.pdf", "dst": "Docs/r.pdf", "reason": "미확정: 문서로 보임", "confidence": 0.7},
            {"op": "to_delete", "src": "junk.tmp", "reason": "임시 파일", "confidence": 0.8}])
        self.assertTrue(ok)
        self.assertIn(f"(저장됨: {self.runs / 'plan.json'} · 사람용 {self.runs / 'plan.md'})", out)
        md = (self.runs / "plan.md").read_text()
        self.assertIn("옮기기 2건", md)
        self.assertIn("_to_delete 로 1건", md)
        self.assertIn("미확정 1건", md)
        self.assertIn("| 미확정 |", md)
        self.assertIn("| Docs/r.pdf | 0.70 | 예 |", md)
        self.assertIn("## _to_delete 로 옮길 것", md)
        self.assertIn("--approve", md)                                   # 승인 명령과 sha 가 지금 plan.json 과 맞다
        from stewart.organize.plan import sha256_file
        self.assertIn(sha256_file(self.runs / "plan.json"), md)
        ok, out = T.plan_drop(self.env, [3])
        self.assertIn("plan.md", out)
        self.assertIn("_to_delete 로 0건", (self.runs / "plan.md").read_text())   # 바뀔 때마다 다시 쓴다
        ok, out = T.plan_show(self.env)
        self.assertTrue(out.splitlines()[-1].startswith(f"계획 파일(이미 저장돼 있다): {self.runs / 'plan.json'}"))
        self.assertIn("plan.md", out.splitlines()[-1])

    def test_finalize_writes_md_with_run_state(self):
        T.plan_add(self.env, [{"op": "move", "src": "사진", "dst": "Photos", "reason": "r", "confidence": 0.9}])
        r = T.finalize_plan(self.env, "max_steps")
        self.assertEqual(r["md"], str(self.runs / "plan.md"))
        self.assertIn("실행 끝내지 못함(max_steps)", (self.runs / "plan.md").read_text())

    def test_wording(self):
        o = load_profile("organize")
        self.assertIn("plan.json 과 사람용 plan.md 에 저장된다", o.system)
        self.assertIn("복사해 저장하라", o.system)                        # 그렇게 말하지 말라는 줄
        defs = {d["function"]["name"]: d["function"]["description"] for d in T.definitions(["plan_add", "plan_show"])}
        self.assertIn("넣는 즉시 기록 폴더의 plan.json 과 사람용 plan.md 에 저장된다", defs["plan_add"])
        self.assertIn("plan.json·plan.md 에 저장돼 있고", defs["plan_show"])

    def test_chat_plan_summary(self):
        p = Profile(name="organize", path=self.tmp / "p.toml", system="s", tools=list(T.ORGANIZE_TOOLS), workdir=self.root,
                    max_steps=5)
        s = C.ChatSession(p, Backend("fake", "http://x/v1", "m", "q", 4096), Recorder(self.tmp / "r2", "chat-o"), C.PlainUI(), mode="edits") \
            if hasattr(C, "PlainUI") else None
        if s is None:
            from tests.test_cloud import FakeUI
            s = C.ChatSession(p, Backend("fake", "http://x/v1", "m", "q", 4096), Recorder(self.tmp / "r2", "chat-o"), FakeUI(), mode="edits")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        self.assertIn("정리 계획 없음", s.plan_summary())
        T.folder_scan(s.env)
        T.plan_add(s.env, [{"op": "move", "src": "사진", "dst": "Photos", "reason": "r", "confidence": 0.3}])
        out = s.plan_summary()
        self.assertIn("정리 계획 1건: 옮기기 1 · _to_delete 0 · 미확정 1", out)
        self.assertIn(str(Path(s.env.artifacts_dir) / "plan.md"), out)
        self.assertIn("/plan on|off", out)

    def test_other_profiles_unchanged(self):
        code = load_profile("code")
        names = set(code.tools)                                            # fix1 code 목록에는 엔진 도구(ask_user 등)도 있다
        self.assertFalse(T.ORGANIZE_TOOLS & names)                         # 정리 도구는 code 에 없다
        env = T.ToolEnv(self.tmp, artifacts_dir=self.tmp / "r3")
        self.assertIsNone(T.finalize_plan(env, "completed"))              # 훑기가 없으면 plan.json·plan.md 를 만들지 않는다
        self.assertFalse((self.tmp / "r3" / "plan.md").exists())


if __name__ == "__main__":
    unittest.main()
