"""판 131: 깨진 글·연달아 끊긴 글을 다 된 답(answered·completed)으로 받지 않는다.

- 재현 자료(tests/replay/u131_garbled_lane.json): 한 서버 차선이 생각 끔 긴 생성에서 깨진 글을 낸 평가 회차 둘의 모델 응답 차례를
  events 에서 그대로 뽑은 것이다. 생각 글·툴콜 인자는 빠졌다(시험에서는 툴콜을 read_file 로 바꿔 돌린다).
  - 꼴: 생각을 켠 걸음이 생각만 하다 상한에 닿아(빈 잘림) 생각 끄고 다시 보내면, 그 차선은 U+FFFD 가 수백 개 섞인 글이나
    느낌표 하나를 상한까지 되풀이한 글을 finish_reason=length 로 냈다.
  - 고치기 전: 끊긴 글이라 "이어 써라" 를 차례당 두 번 붙였고, 세 번째 끊긴 글은 그대로 최종 답(answered → completed)이 되어
    답 파일에 깨진 글이 들어갔다.
- 고친 꼴
  - 깨진 글은 문맥에도 답에도 넣지 않고 다음 걸음으로 간다. 연달아 GARBLED_MAX 번을 넘기면 차례를 끝낸다(끝 이유 garbled_output).
    사람 없는 차례는 도구 없이 마지막 답을 한 번 받는다. 그 답도 깨지면 받지 못했다고 남긴다.
  - 끊긴 글 이어 쓰기는 연달아 센다(도구를 부른 걸음에서 0). 연달아 이어도 또 끊기면 끝 이유는 cut_answer 다.
  - run 길: 깨진 글이면 끝 이유 garbled_output 이다(completed 아님).
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C
from stewart import cutoff as CO
from stewart import engine
from stewart import reflect as RF
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_u122_repeat_stop import READ, Base, r
from tests.test_u128_time_budget import TB_Base

REPLAY = json.loads((Path(__file__).parent / "replay" / "u131_garbled_lane.json").read_text(encoding="utf-8"))
_N = [0]


def replay(name: str) -> list:
    """실물 응답 차례 → 가짜 응답 목록. 툴콜은 이름만 남아 있어 read_file 하나로 바꾼다(차례·끝 이유·본문·토큰 수는 그대로)."""
    out = []
    for c in REPLAY["seqs"][name]["calls"]:
        msg = {"role": "assistant", "content": c["content"] or None}
        if c["tool_calls"]:
            _N[0] += 1
            msg["tool_calls"] = [{"id": f"rp{_N[0]}-{i}", "type": "function",
                                  "function": {"name": READ[0], "arguments": json.dumps(READ[1])}}
                                 for i, _ in enumerate(c["tool_calls"])]
        out.append(({"choices": [{"message": msg, "finish_reason": c["finish"]}], "usage": c["usage"]}, 1.0))
    return out


def garbage(name="b", nth=-1) -> str:
    return [c["content"] for c in REPLAY["seqs"][name]["calls"] if c["content"]][nth]


def bad_in(messages) -> bool:
    """보낸 메시지에 깨진 글이 들어갔나."""
    return any(CO.garbled(m.get("content")) for m in messages if isinstance(m.get("content"), str))


class TGarbledFn(unittest.TestCase):
    def test_replay_outputs_are_garbled(self):
        outs = [c["content"] for s in REPLAY["seqs"].values() for c in s["calls"] if c["content"]]
        self.assertEqual(len(outs), 6)
        self.assertTrue(all(CO.garbled(t) for t in outs))

    def test_normal_text_not_garbled(self):
        self.assertFalse(CO.garbled(""))
        self.assertFalse(CO.garbled(None))
        self.assertFalse(CO.garbled("판 131 의 `garbled()` 는 cutoff.py 에 둔다. " * 300))   # 한국어·코드 섞인 글
        self.assertFalse(CO.garbled("=" * 999))                                                 # 긴 구분선
        self.assertFalse(CO.garbled("깨진 바이트 � 한두 개 " * 10))                          # 바꿈 글자가 몇 개뿐
        self.assertFalse(CO.garbled(" " * 5000))
        self.assertTrue(CO.garbled("!" * 1000))
        self.assertTrue(CO.garbled("ab�" * 30))


class TChatReplay(Base):
    def test_lane_a_recovers_and_answers(self):
        """실물 회차 a: 깨진 글 셋이 도구 걸음 사이에 끼었다. 고치기 전에는 세 번째에서 깨진 글이 답(completed)이 됐다."""
        s = self.sess(thinking=True)
        seq = replay("a") + [r("Exact Answer: X")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            out = s.run_once("찾아")
        self.assertEqual(out["stop_reason"], "completed")
        self.assertEqual(len(self.sent), 11)                                    # 깨진 걸음마다 버리고 다음 걸음 — 마지막 걸음이 답
        self.assertFalse(any(bad_in(m) for _, _, m in self.sent))               # 어느 요청에도 깨진 글이 문맥으로 가지 않았다
        self.assertIn("깨진 글자", self.sent[-1][2][-1]["content"])
        ev = self.events(s)
        self.assertEqual([e["n"] for e in ev if e["kind"] == "garbled_output"], [1, 1, 1])   # 도구 걸음에서 셈이 0 으로
        self.assertFalse([e for e in ev if e["kind"] in ("length_continue", "cut_answer")])
        summ = s.close()
        self.assertEqual(summ["garbled_outputs"], 3)
        self.assertEqual((s.rec.dir / "output.md").read_text().strip(), "Exact Answer: X")

    def test_lane_b_three_in_a_row_auto(self):
        """실물 회차 b: 깨진 글이 세 걸음 연달아 → garbled_output, 도구 없는 마지막 답도 깨지면 받지 못했다고 남긴다."""
        s = self.sess(thinking=True)
        seq = replay("b") + [({"choices": [{"message": {"role": "assistant", "content": garbage()}, "finish_reason": "length"}],
                               "usage": {"completion_tokens": 8192}}, 1.0)]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            out = s.run_once("찾아")
        self.assertEqual(out["stop_reason"], "garbled_output")
        self.assertEqual(self.tool_less(), [10])
        self.assertIn("모델 출력이 거듭 깨져", self.sent[10][2][-1]["content"])
        self.assertFalse(any(bad_in(m) for _, _, m in self.sent))
        ev = self.events(s)
        self.assertEqual([e["n"] for e in ev if e["kind"] == "garbled_output"], [1, 2, 3])
        fa = [e for e in ev if e["kind"] == "final_answer"]
        self.assertEqual((fa[0]["trigger"], fa[0]["ok"], fa[0]["error"]), ("garbled_output", False, "깨진 글"))
        summ = s.close()
        self.assertFalse(summ["success"])
        text = (s.rec.dir / "output.md").read_text()
        self.assertIn("마지막 답을 받지 못했다: 깨진 글", text)
        self.assertFalse(CO.garbled(text))

    def test_lane_b_final_answer_ok(self):
        s = self.sess(thinking=True)
        with mock.patch.object(C, "chat", side_effect=self.fake(replay("b") + [r("확인 못 함")])):
            out = s.run_once("찾아")
        self.assertEqual(out["stop_reason"], "garbled_output")
        s.close()
        self.assertEqual((s.rec.dir / "output.md").read_text().strip(), "확인 못 함")

    def test_lane_b_asking_mode(self):
        s = self.sess(mode="edits", thinking=True)
        with mock.patch.object(C, "chat", side_effect=self.fake(replay("b"))):
            self.assertEqual(s.turn("찾아"), "garbled_output")
        self.assertEqual(len(self.sent), 10)                                    # 마지막 답 요청 없음
        self.assertIn("깨진 글자로 나왔다", s.ui.out.getvalue())


class TChatCut(Base):
    CUT = "긴 보고의 앞부분이다. " * 20

    def test_continue_count_resets_on_tool_step(self):
        """끊긴 글이 도구 걸음 사이에 세 번 — 차례당 두 번이던 상한 때문에 세 번째 끊긴 글이 답이 되던 것."""
        s = self.sess()
        seq = [r(self.CUT, finish="length"), r(calls=[READ]), r(self.CUT, finish="length"), r(calls=[READ]),
               r(self.CUT, finish="length"), r("끝")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("써"), "answered")
        self.assertEqual(len(self.sent), 6)
        self.assertEqual(s._last_answer(), "끝")

    def test_cut_three_in_a_row_is_cut_answer(self):
        s = self.sess()
        seq = [r(self.CUT, finish="length")] * 3
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            out = s.run_once("써")
        self.assertEqual(out["stop_reason"], "cut_answer")
        self.assertEqual(self.tool_less(), [])                                  # 마지막 답 요청은 없다 — 끊긴 글이 남는다
        self.assertEqual(len([e for e in self.events(s) if e["kind"] == "length_continue"]), 2)
        self.assertEqual([e["n"] for e in self.events(s) if e["kind"] == "cut_answer"], [3])
        self.assertFalse(s.close()["success"])


class TChatGarbledOverBudget(TB_Base):
    def test_over_budget_goes_time_budget(self):
        s = self.budget(self.sess(), 100)
        g = ({"choices": [{"message": {"role": "assistant", "content": garbage()}, "finish_reason": "length"}],
              "usage": {"completion_tokens": 8192}}, 90)
        with mock.patch.object(C, "chat", side_effect=self.fake([g, (r("여기까지: 모른다")[0], 1)])):
            self.assertEqual(s.turn("찾아"), "time_budget")
        self.assertEqual(self.tool_less(), [1])
        self.assertFalse(bad_in(self.sent[1][2]))


class TReflectCall(unittest.TestCase):
    def test_call_rejects_garbled(self):
        resp = {"choices": [{"message": {"content": garbage("a", 0)}, "finish_reason": "length"}], "usage": {}}
        res = RF.call(lambda *a, **k: (resp, 1.0), None, [], {}, 100)
        self.assertEqual((res["ok"], res["content"], res["error"]), (False, "", "깨진 글"))


class TRunGarbled(unittest.TestCase):
    def test_run_garbled_not_completed(self):
        tmp = Path(tempfile.mkdtemp())
        p = Profile(name="t", path=tmp / "p.toml", system="s", tools=["read_file"], workdir=tmp, max_steps=4)
        rec = Recorder(tmp / "runs", "t")
        g = ({"choices": [{"message": {"role": "assistant", "content": garbage()}, "finish_reason": "length"}],
              "usage": {"completion_tokens": 8192}}, 1.0)
        with mock.patch.object(engine, "chat", side_effect=[g]):
            s = engine.run(p, Backend("fake", "http://x/v1", "m", "q", 100000), "do", rec)
        self.assertEqual((s["stop_reason"], s["success"], s["garbled_outputs"]), ("garbled_output", False, 1))


if __name__ == "__main__":
    unittest.main()
