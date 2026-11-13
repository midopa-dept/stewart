"""판 124.3: 작업 카드가 접혀(한 줄) 있어도 도구가 본 마지막 그림(view_image 등)은 줄 아래에 바로 그려진다.
판 124 개편 때 접힌 한 줄이 그 그림을 숨기던 것이 다시 빠지지 않게 — 빌드 결과와 화면 코드를 함께 본다."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TToolShot(unittest.TestCase):
    def test_collapsed_card_draws_last_image(self):
        src = (ROOT / "ui" / "src" / "thread.jsx").read_text(encoding="utf-8")
        start = src.index("if (steps.length && !open) return (")
        end = src.index("if (!steps.length) return (", start)
        folded = src[start:end]
        self.assertIn("<StepImgs names={shot} />", folded)                 # 접힌 한 줄 갈래 안에서 그림을 그린다
        self.assertRegex(src, r"const shot = lastShot\(steps\)")
        js = (ROOT / "src" / "stewart" / "web_ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("tool-shot", js)                                     # 빌드 결과에도 들어 있다

    def test_last_shot_picks_latest(self):
        s = (ROOT / "ui" / "src" / "stepshot.js").read_text(encoding="utf-8")
        self.assertTrue(re.search(r"for \(let i = .*length - 1; i >= 0; i--\)", s))   # 뒤에서부터 = 최신 걸음


if __name__ == "__main__":
    unittest.main()
