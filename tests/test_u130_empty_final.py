"""판 130: 생각만 내고 본문·도구 호출 없이 멈춘 걸음을 답으로 받지 않는다.

- 재현 꼴: 생각을 켠 백엔드가 생성 상한 전에(finish_reason=stop) 생각만 내고 본문도 툴콜도 비운다.
  전에는 이 빈 글을 최종 답으로 받아 끝 이유가 completed(answered)였고, 한 번 돌리기의 답 파일에는 앞 걸음의 말("이제 더 찾아보겠다")이 들어갔다.
- 고친 꼴: 생각 끄고 알림 한 줄로 같은 걸음을 한 번 다시 보낸다(빈 잘림과 같은 길). 다시 보내도 비면
  사람 없는 차례는 도구 없이 마지막 답 한 차례를 받고 끝 이유를 empty_answer 로 따로 적는다. 묻는 모드는 경고만 하고 멈춘다.
- 시간 예산을 넘긴 뒤의 빈 걸음은 다시 보내지 않고 시간 예산 길(time_budget)로 간다.
- run 길도 같다: 한 번 다시 보내고, 또 비면 empty_answer(completed 아님).
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C
from stewart import engine
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_u122_repeat_stop import READ, Base, r
from tests.test_u128_time_budget import TB_Base, slow

THINK = "단서를 하나씩 따져 본다. " * 50


def empty(reasoning=6000, think=THINK):
    """생각만 내고 본문·툴콜 없이 멈춘 응답(finish_reason=stop, 상한 전)."""
    resp, dt = r("", reasoning=reasoning)
    resp["choices"][0]["message"]["reasoning_content"] = think
    return resp, dt


SAY = "이제 더 좁혀서 찾아보겠다."


class TEmptyStopFn(unittest.TestCase):
    def test_shapes(self):
        self.assertTrue(engine.empty_stop(empty()[0]))
        self.assertTrue(engine.empty_stop(r("  \n")[0]))                         # 빈칸만 있는 본문도 빈 답
        self.assertFalse(engine.empty_stop(r("", finish="length")[0]))           # 상한 잘림은 empty_length 의 몫
        self.assertFalse(engine.empty_stop(r("답")[0]))
        self.assertFalse(engine.empty_stop(r(None, calls=[READ])[0]))


class TChatEmptyStop(Base):
    def test_repro_retry_then_answer(self):
        """앞 걸음에 말 + 도구, 다음 걸음이 생각만 내고 빈 본문 → 생각 끄고 다시 보내 답을 받는다(끝 이유 answered, 답은 새 답)."""
        s = self.sess(thinking=True)
        seq = [r(SAY, calls=[READ]), empty(), r("답: X")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("찾아"), "answered")
        self.assertEqual(len(self.sent), 3)
        self.assertTrue(self.sent[2][0])                                         # 같은 걸음 다시 — 도구는 그대로 준다
        self.assertIs(self.sent[2][1].get("thinking"), False)                   # 생각 끔
        self.assertEqual(self.sent[2][2][-1]["role"], "user")
        self.assertIn("본문도 도구 호출도 없이 멈췄다", self.sent[2][2][-1]["content"])
        self.assertEqual(s._last_answer(), "답: X")
        ev = self.events(s)
        self.assertEqual([e["finish_reason"] for e in ev if e["kind"] == "empty_retry"], ["stop"])
        self.assertEqual(s.st["empty_stop_retries"], 1)

    def test_still_empty_auto_gets_final_answer(self):
        s = self.sess(thinking=True)
        seq = [r(SAY, calls=[READ]), empty(), empty(reasoning=0, think=""), r("확인 못 함 — 본 결과로는 모른다.")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            out = s.run_once("찾아")
        self.assertEqual(out["stop_reason"], "empty_answer")                     # completed 가 아니다
        self.assertEqual(self.tool_less(), [3])                                  # 다시 보낸 뒤에도 비어 도구 없는 마지막 답 하나
        self.assertIn("본문 없이 멈춰", self.sent[3][2][-1]["content"])
        self.assertIn("도구는 더 부를 수 없다", self.sent[3][2][-1]["content"])
        ev = self.events(s)
        self.assertEqual([e["trigger"] for e in ev if e["kind"] == "final_answer"], ["empty_answer"])
        self.assertEqual(len([e for e in ev if e["kind"] == "empty_answer"]), 1)
        summ = s.close()
        self.assertFalse(summ["success"])
        self.assertEqual(summ["stop_reason"], "empty_answer")
        self.assertEqual((summ["empty_stop_retries"], summ["empty_answers"]), (1, 1))
        self.assertEqual((s.rec.dir / "output.md").read_text().strip(), "확인 못 함 — 본 결과로는 모른다.")

    def test_final_also_empty_output_not_old_narration(self):
        """마지막 답마저 비면 답 파일에 앞 걸음의 말("찾아보겠다")을 답처럼 남기지 않는다."""
        s = self.sess(thinking=True)
        seq = [r(SAY, calls=[READ]), empty(), empty(), r("")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            out = s.run_once("찾아")
        self.assertEqual(out["stop_reason"], "empty_answer")
        s.close()
        text = (s.rec.dir / "output.md").read_text()
        self.assertNotIn(SAY, text)
        self.assertIn("마지막 답을 받지 못했다", text)

    def test_asking_mode_warns_no_extra_request(self):
        s = self.sess(mode="edits", thinking=True)
        seq = [empty(), empty()]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("찾아"), "empty_answer")
        self.assertEqual(len(self.sent), 2)                                      # 다시 보낸 한 번뿐, 마지막 답 요청 없음
        self.assertIn("본문도 도구 호출도 없이 멈췄다", s.ui.out.getvalue())

    def test_length_retry_then_empty_stop(self):
        """생각 잘림으로 다시 보냈더니 이번엔 상한 전에 빈 본문 → 그것도 답으로 받지 않는다."""
        s = self.sess(thinking=True)
        seq = [r("", finish="length", reasoning=8000), empty(reasoning=0, think=""), r("모른다.")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("찾아"), "empty_answer")
        self.assertEqual(self.tool_less(), [2])
        self.assertEqual(s._last_answer(), "모른다.")

    def test_normal_answer_untouched(self):
        s = self.sess(thinking=True)
        seq = [r(calls=[READ]), r("답")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("읽어"), "answered")
        self.assertEqual(len(self.sent), 2)
        self.assertFalse([e for e in self.events(s) if e["kind"] in ("empty_retry", "empty_answer")])


class TChatEmptyOverBudget(TB_Base):
    def test_over_budget_goes_time_budget(self):
        s = self.budget(self.sess(thinking=True), 100)                    # 85% = 85초
        e, _ = empty()
        seq = [slow(calls=[READ], dt=50), (e, 50), slow("여기까지 본 것: …", dt=5)]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("읽어"), "time_budget")
        self.assertEqual(len(self.sent), 3)                                      # 생각 끄고 다시 보내지 않고 곧바로 마지막 답
        self.assertEqual(self.tool_less(), [2])
        self.assertFalse([x for x in self.events(s) if x["kind"] == "empty_retry"])


class TRunEmptyStop(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["read_file"], workdir=self.tmp, max_steps=4)

    def go(self, seq):
        rec = Recorder(self.tmp / "runs", "t")
        with mock.patch.object(engine, "chat", side_effect=seq) as m:
            s = engine.run(self.p, Backend("fake", "http://x/v1", "m", "q", 100000), "do", rec)
        ev = [json.loads(l) for l in (rec.dir / "events.jsonl").read_text().splitlines()]
        return s, m, ev

    def test_retry_then_answer(self):
        s, m, ev = self.go([empty(), r("답")])
        self.assertEqual((s["stop_reason"], s["steps"], s["empty_stop_retries"]), ("completed", 1, 1))
        self.assertIs(m.call_args_list[1].kwargs["thinking"], False)
        self.assertIn("본문도 도구 호출도 없이 멈췄다", m.call_args_list[1].args[1][-2]["content"])
        self.assertEqual(len([e for e in ev if e["kind"] == "empty_retry"]), 1)
        self.assertFalse([e for e in ev if e["kind"] == "component" and e["name"] == "length_retry"])

    def test_second_empty_is_empty_answer(self):
        s, m, _ = self.go([empty(), empty(), r("안 닿음")])
        self.assertEqual((s["stop_reason"], m.call_count, s["success"]), ("empty_answer", 2, False))


if __name__ == "__main__":
    unittest.main()
