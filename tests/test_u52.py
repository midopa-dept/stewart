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
        self.assertEqual(names, ["blocked-report", "code-review", "error-diagnosis", "git-work", "handoff-docs",
                                 "plan-checkpoints", "test-fix-loop", "web-research"])
        for n in names:
            meta, body = SK.parse((SK.BUILTIN_DIR / n / "SKILL.md").read_text(encoding="utf-8"))
            self.assertEqual(meta["name"], n)
            self.assertTrue(10 < len(meta["description"]) <= SK.MAX_DESC, n)
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
                self.assertEqual(len(got), 8)
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
        self.assertIn("MAX_SIDE = 1600", src)


if __name__ == "__main__":
    unittest.main()
