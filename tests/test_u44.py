"""판 63 복사(사용자 지적 SW-16): 답 속 코드 블록마다 머리(언어·기기)와 [복사], 도구 결과 [복사], http(테일넷)에서는 textarea 대체 복사,
막히면 '사람 손 카드'·'외부 질문 카드'를 코드 블록 하나로(통째 복사). 화면 그림은 샌드박스에서 못 찍어 빌드 지문·글로 본다."""

import unittest
from pathlib import Path

from stewart import steer as ST
from stewart.config import load_profile

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "src" / "stewart" / "web_ui" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "src" / "stewart" / "web_ui" / "app.css").read_text(encoding="utf-8")


class TCopy(unittest.TestCase):
    def test_build_has_copy(self):
        self.assertIn("cp-btn", JS)                       # 코드 블록 단추(lib.js 그림)
        self.assertIn("isSecureContext", JS)              # clipboard API 는 보안 맥락에서만
        self.assertIn("execCommand", JS)                  # 그 밖(테일넷 http)은 대체 복사
        self.assertIn("복사됨", JS)
        self.assertIn(".cb-head", CSS)
        src = (ROOT / "ui" / "src" / "lib.js").read_text(encoding="utf-8")
        self.assertIn('class="cb"', src)

    def test_cards_in_prompt(self):
        s = load_profile("chat").system
        for w in ("사람 손 카드", "외부 질문 카드", "오류 원문", "해 본 것과 결과", "비밀 값·사용자 파일 내용은 넣지 않는다"):
            self.assertIn(w, s)
        self.assertIn("사람 손 카드", ST.HUMAN_NOTE)


if __name__ == "__main__":
    unittest.main()
