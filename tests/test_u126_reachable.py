"""판 126: 과정은 다 닿게 — "조용한 종이"는 접어 두는 것이지 없애는 것이 아니다. 화면 개편 때 과정 요소가 빠지지 않게 화면 코드·빌드를 본다.
(생각·도구 원문·도구 그림·차례 숫자·이어 받은 앞 대화 원문·스킬 목록·실제 간 지시문·단계 탭의 요약 줄·자세히 보기 손잡이·작업 내역 탭 목록)"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "ui" / "src"


def read(name):
    return (SRC / name).read_text(encoding="utf-8")


class TReachable(unittest.TestCase):
    def test_turn_process_and_detail(self):
        t = read("thread.jsx")
        self.assertIn("toggleTurn(tix)", t)                                    # 답 아래 ⋯ = 그 차례 과정 펼치기
        self.assertIn("force={turnShown(g.tix)}", t)                           # 작업 카드·생각이 그 차례를 따라 펼쳐진다
        self.assertIn("<StepImgs names={shot} />", t)                          # 접혀도 마지막 그림
        self.assertIn("{det && <TokenLine />}", t)                             # 자세히 보기: 토큰 줄
        self.assertIn("nar && !det ?", t)                                      # 자세히 보기: 스킬 줄을 거르지 않음
        self.assertIn("!detail.value ? (", t)                                  # 자세히 보기: 진행 원문·받은 토큰·tok/s
        self.assertIn("type: 'meta'", t)                                       # 답 없이 끝난 차례의 요약도 닿는다
        d = read("detail.js")
        self.assertIn("stewart-detail", d)

    def test_sheets_and_panel(self):
        m = read("main.jsx")
        self.assertIn("setDetail(e.currentTarget.checked)", m)                 # "이 대화" 판의 자세히 보기
        self.assertIn("PANEL_TABS", m)                                         # 작업 내역 탭 목록(폰 두 번)
        self.assertIn("SKILLS_RX.test", m)                                     # 스킬 목록 줄
        self.assertIn("prior=1", m)                                            # 이어 받은 앞 대화 원문
        self.assertIn("m.detail", m)                                           # 모델 상태 자세히
        self.assertIn("setDetail", read("settings.jsx"))                       # 설정 시트의 자세히 보기
        pn = read("panel.jsx")
        self.assertIn("d.system", pn)                                          # 실제 간 지시문 본문
        self.assertIn("d.tool_defs", pn)
        self.assertNotIn("if (m.kind === 'meta' || m.kind === 'turn_end') continue", pn)   # 단계 탭이 요약 줄을 버리지 않는다

    def test_build_has_them(self):
        js = (ROOT / "src" / "stewart" / "web_ui" / "app.js").read_text(encoding="utf-8")
        for k in ("stewart-detail", "tool-shot", "prior=1", "tb-keep"):
            self.assertIn(k, js, k)


class TStatusInfo(unittest.TestCase):
    def test_status_info_has_system_and_tools(self):
        from tests.test_hub3 import MainBase

        class T(MainBase):
            def runTest(self):
                d = self.main.status_info()
                self.assertIn("MAIN 지시", d["system"])                        # 이 대화에서 모델로 실제 가는 지시문
                self.assertTrue(any(x["name"] == "read_file" for x in d["tool_defs"]))
                self.assertGreaterEqual(d["messages"], 1)
                json.dumps(d, ensure_ascii=False)
        r = unittest.TextTestRunner(stream=open("/dev/null", "w")).run(T())
        self.assertTrue(r.wasSuccessful(), r.failures + r.errors)


if __name__ == "__main__":
    unittest.main()
