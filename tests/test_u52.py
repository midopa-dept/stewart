"""판 70: (SW-23) 내장 스킬 — 실사용(chat·허브) 켬·평가 길 끔, 사용자·프로젝트의 같은 이름이 이김, 앞머리 꼴·내용 규칙.
(SW-22) 사진 붙이기 — 큰 폰 사진·HEIC 를 브라우저에서 줄여 JPEG 로(빌드에 들어갔는지)."""

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import skills as SK

ROOT = Path(__file__).resolve().parent.parent


class TBuiltin(unittest.TestCase):
    def test_set(self):
        names = sorted(p.name for p in SK.BUILTIN_DIR.iterdir() if p.is_dir())
        self.assertEqual(names, ["blocked-report", "code-review", "deep-research", "documents", "error-diagnosis", "git-work",
                                 "handoff-docs", "memory-consolidate", "plan-checkpoints", "project-init", "run-and-check", "security-review", "simplify",
                                 "skill-creator", "stewart-config", "test-fix-loop", "web-research"])
        for n in names:
            meta, body = SK.parse((SK.BUILTIN_DIR / n / "SKILL.md").read_text(encoding="utf-8"))
            self.assertEqual(meta["name"], n)
            self.assertTrue(10 < len(meta["description"]) <= SK.MAX_DESC, n)
            self.assertIn("쓴다", meta["description"], n)                       # 언제 쓰는지 적었나(자동 선택은 설명에 달렸다)
            self.assertTrue(body.strip(), n)
            text = (SK.BUILTIN_DIR / n / "SKILL.md").read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"맥미니|홈서버|Termius|\bS-\d+\b|\d+차(?![가-힣])", text), n)   # 기기 이름·내부 번호 없음

    def test_on_off_and_override(self):
        wd = Path(tempfile.mkdtemp())
        user = Path(tempfile.mkdtemp())
        (user / "git-work").mkdir()
        (user / "git-work" / "SKILL.md").write_text("---\nname: git-work\ndescription: 내 git 방식\n---\n본문", encoding="utf-8")
        with mock.patch.dict(os.environ, {"STEWART_SKILLS_DIR": str(user)}):
            os.environ.pop("STEWART_BUILTIN_SKILLS", None)
            try:
                self.assertTrue(SK.builtin_on(True))
                self.assertFalse(SK.builtin_on(False))                  # 평가 길(run)은 끔
                got = {s.name: s for s in SK.discover(wd, builtin=True)}
                self.assertEqual(len(got), 17)   # 판 88: memory-consolidate 더함
                self.assertEqual(got["git-work"].description, "내 git 방식")   # 사용자 것이 이김
                self.assertEqual(got["code-review"].scope, "builtin")
                self.assertNotIn("code-review", {s.name for s in SK.discover(wd, builtin=False)})
            finally:
                os.environ["STEWART_BUILTIN_SKILLS"] = "0"


class TPhoto(unittest.TestCase):
    def test_build_has_prep(self):
        js = (ROOT / "src" / "stewart" / "web_ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("image/*,.heic,.heif", js)                         # 폰 사진 고르기(HEIC 포함)
        self.assertIn("image/jpeg", js)
        self.assertIn("toDataURL", js)                                   # 브라우저에서 줄이기
        src = (ROOT / "ui" / "src" / "imgprep.js").read_text(encoding="utf-8")
        self.assertIn("MAX_PIXELS = 2000000", src)                       # 판 82: 서버 화소 상한 아래 2.0MP(긴 변 1600 아님)



from tests.test_hub import msg  # noqa: E402
from tests.test_hub3 import MainBase  # noqa: E402


class TPick(MainBase):
    """스킬 고르기 왕복: 허브 메인 지시문에 내장 스킬 목록이 실리고, 모델이 load_skill 을 부르면 본문이 돌아온다."""
    def setUp(self):
        p = mock.patch.dict(os.environ, {"STEWART_BUILTIN_SKILLS": "1"})
        p.start()
        self.addCleanup(p.stop)
        super().setUp()

    def test_list_and_load(self):
        self.r.scripts["MAIN"] = [msg(calls=[("load_skill", {"name": "test-fix-loop"})]), msg("시험부터 돌리겠다")]
        self.main.send("시험이 실패한다. 고쳐 줘")
        self.assertTrue(self.main.wait_idle())
        sys0 = self.r.requests["MAIN"][0]["messages"][0]["content"]
        self.assertIn("- test-fix-loop: 시험이 실패하거나", sys0)
        self.assertIn("- security-review:", sys0)
        tool = [m for m in self.r.requests["MAIN"][1]["messages"] if m.get("role") == "tool"][0]
        self.assertIn("# 시험 돌리고 고치기", tool["content"])


if __name__ == "__main__":
    unittest.main()
