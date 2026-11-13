"""판 128: 엔진 안 시간 예산 — 바깥 시간 제한에 끊기기 전에 지금까지로 마지막 답.

- 손잡이가 없으면 지금과 같다(시간 예산 없음, 생성 상한 그대로).
- 예산의 비율에 닿으면 새 도구 걸음을 시작하지 않고 도구 없이 마지막 답 한 차례, 끝 이유 time_budget.
- 걸음 도중: 생성 상한을 남은 시간 × 잰 생성 속도로 줄인다.
- 시간 예산에서 잘린 빈 걸음은 생각 끄고 다시 보내지 않고 마지막 답으로.
- 가짜 느린 백엔드: 요청마다 가짜 시계를 dt 만큼 앞으로 민다.
"""

import json
import subprocess
import sys
import unittest
from unittest import mock

from stewart import chat as C
from stewart import timebudget as TB
from tests.test_u122_repeat_stop import READ, Base

_N = [0]


def slow(content=None, calls=None, finish="stop", completion=100, dt=100.0):
    msg = {"role": "assistant", "content": content}
    if calls:
        _N[0] += 1
        msg["tool_calls"] = [{"id": f"tb{_N[0]}-{i}", "type": "function",
                              "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
                             for i, (n, a) in enumerate(calls)]
    return ({"choices": [{"message": msg, "finish_reason": finish}],
             "usage": {"prompt_tokens": 10, "completion_tokens": completion}}, dt)


class TB_Base(Base):
    env = {"STEWART_REFLECT": "0", "STEWART_TIME_BUDGET_S": "", "STEWART_TIME_BUDGET_FRAC": ""}

    def setUp(self):
        super().setUp()
        self.now = [0.0]

    def budget(self, s, seconds, frac=0.85):
        s.time_budget = TB.TimeBudget(seconds, frac, t0=0.0, clock=lambda: self.now[0])
        s._tb_each_turn = False
        return s

    def fake(self, seq):
        """요청마다 가짜 시계를 그 응답의 dt 만큼 민다(느린 백엔드)."""
        def f(backend, messages, tools=None, *a, **k):
            self.sent.append((tools is not None, k, json.loads(json.dumps(messages))))
            x = seq[len(self.sent) - 1]
            resp, dt = x(k) if callable(x) else x
            self.now[0] += dt
            return resp, dt
        return f


class TOffByDefault(TB_Base):
    def test_no_budget_no_change(self):
        s = self.sess()
        self.assertIsNone(s.time_budget)
        seq = [slow(calls=[READ], dt=2000), slow(calls=[READ], dt=2000), slow("끝")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("읽어"), "answered")
        self.assertEqual(self.tool_less(), [])
        self.assertTrue(all("max_tokens" not in k for _, k, _ in self.sent))   # 생성 상한을 건드리지 않는다
        self.assertFalse([e for e in self.events(s) if e["kind"].startswith("time_budget")])


class TFinalAtBudget(TB_Base):
    def test_stops_new_steps_and_answers(self):
        s = self.budget(self.sess(), 300)                    # 85% = 255초
        seq = [slow(calls=[READ]), slow(calls=[("read_file", {"path": "b.txt"})]), slow(calls=[("read_file", {"path": "c.txt"})]),
               slow("지금까지로는 X 다. 확인 못 한 것: Y.", dt=5)]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("찾아"), "time_budget")
        self.assertEqual(self.tool_less(), [3])                                  # 걸음 셋(300초) 뒤 넷째는 도구 없는 마지막 답
        self.assertIn("도구는 더 부를 수 없다", self.sent[-1][2][-1]["content"])
        self.assertIn("시간이 거의 다", self.sent[-1][2][-1]["content"])
        self.assertEqual(self.sent[-1][1].get("thinking"), False)               # 생각 끔(판 122 마지막 답 꼴)
        ev = self.events(s)
        self.assertEqual([e["trigger"] for e in ev if e["kind"] == "final_answer"], ["time_budget"])
        self.assertEqual([e["budget_s"] for e in ev if e["kind"] == "time_budget"], [300.0])
        self.assertEqual([e["reason"] for e in ev if e["kind"] == "turn_end"], ["time_budget"])
        self.assertEqual(s._last_answer(), "지금까지로는 X 다. 확인 못 한 것: Y.")
        self.assertTrue(s.st["time_budget_hit"]["final_ok"])
        self.assertIn("시간 예산(300초의 85%)에 닿아", s.ui.out.getvalue())          # 화면 줄
        self.assertIn("시간 예산에 닿아 도구 없이", s.ui.out.getvalue())
        self.assertIn("시간 예산에 닿아 도구 없이 받은 마지막 답", "".join(s.rec._md))   # 사람용 기록 조각

    def test_once_reports_time_budget(self):
        s = self.budget(self.sess(), 150)
        seq = [slow(calls=[READ]), slow(calls=[("read_file", {"path": "b.txt"})]), slow("모른다 — 시간 안에 찾지 못했다.", dt=5)]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            out = s.run_once("찾아")
        self.assertEqual(out["stop_reason"], "time_budget")                     # 되풀이 호출·잘림과 따로
        s.close()
        self.assertIn("모른다", (s.rec.dir / "output.md").read_text())

    def test_any_mode_answers(self):
        """사람이 시간 예산을 걸었으면 묻는 모드에서도 마지막 답을 받는다(되풀이 호출과 다름 — 예산은 사람이 정한 것)."""
        s = self.budget(self.sess(mode="edits"), 100)
        seq = [slow(calls=[READ]), slow("여기까지.", dt=5)]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("읽어"), "time_budget")
        self.assertEqual(self.tool_less(), [1])

    def test_answer_before_budget_is_plain(self):
        s = self.budget(self.sess(), 300)
        seq = [slow(calls=[READ]), slow("다 했다")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("읽어"), "answered")
        self.assertEqual(self.tool_less(), [])


class TCapStep(TB_Base):
    def test_cap_fits_remaining_time(self):
        s = self.budget(self.sess(max_tokens=8000), 100)    # 85초, 걸음 하나 10초·1000토큰 = 100 tok/s
        seq = [slow(calls=[READ], completion=1000, dt=10), slow("끝", completion=10, dt=1)]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("읽어"), "answered")
        self.assertNotIn("max_tokens", self.sent[0][1])                          # 첫 걸음은 속도를 아직 모른다
        self.assertEqual(self.sent[1][1]["max_tokens"], int(75 * 100 * TB.CAP_MARGIN))   # 남은 75초 × 100 tok/s × 0.9
        caps = [e for e in self.events(s) if e["kind"] == "time_budget_cap"]
        self.assertEqual((caps[0]["was"], caps[0]["max_tokens"]), (8000, 6750))

    def test_no_cap_when_room(self):
        s = self.budget(self.sess(max_tokens=500), 1000)
        seq = [slow(calls=[READ], completion=1000, dt=10), slow("끝")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            s.turn("읽어")
        self.assertNotIn("max_tokens", self.sent[1][1])                          # 남은 시간으로 넉넉하다 — 그대로

    def test_cap_has_floor(self):
        b = TB.TimeBudget(100, 0.85, t0=0.0, clock=lambda: 84.9)
        self.assertEqual(b.cap_tokens(8000, 1.0), TB.CAP_FLOOR)
        self.assertIsNone(b.cap_tokens(8000, None))


class TCutStep(TB_Base):
    def test_empty_length_over_budget_goes_final(self):
        s = self.budget(self.sess(thinking=True), 100)
        seq = [slow(calls=[READ], dt=50), slow("", finish="length", dt=50), slow("여기까지 본 것: …", dt=5)]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("읽어"), "time_budget")
        self.assertEqual(len(self.sent), 3)                                      # 생각 끄고 같은 걸음 다시 보내지 않았다
        self.assertEqual(self.tool_less(), [2])
        self.assertFalse([e for e in self.events(s) if e["kind"] == "length_retry"])


class TNoReflectOverBudget(TB_Base):
    env = {"STEWART_REFLECT": "1", "STEWART_TIME_BUDGET_S": "", "STEWART_TIME_BUDGET_FRAC": ""}

    def test_failures_over_budget_skip_reflect(self):
        """예산을 넘긴 뒤 실패 문턱에 닿아도 되짚기(도구 없는 한 번)에 시간을 쓰지 않고 곧바로 마지막 답(끝까지 돌려 본 꼴)."""
        s = self.budget(self.sess(), 8)                       # 85% = 6.8초, 걸음 2초씩 — 넷째 걸음(6–8초) 뒤 실패 문턱
        miss = [slow(calls=[("read_file", {"path": f"없음{i}.txt"})], dt=2) for i in range(6)]
        seq = miss[:4] + [slow("본 것으로는 모른다.", dt=2)]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("찾아"), "time_budget")
        self.assertEqual(self.tool_less(), [4])                                  # 걸음 넷 뒤 곧바로 마지막 답 하나(되짚기 없음)
        self.assertEqual([e["why"] for e in self.events(s) if e["kind"] == "reflect_skip"], ["time_budget"])
        self.assertEqual([e["trigger"] for e in self.events(s) if e["kind"] == "final_answer"], ["time_budget"])


class TPerTurn(TB_Base):
    def test_chat_resets_each_turn(self):
        s = self.sess()
        s.time_budget = TB.TimeBudget(100, 0.85, clock=lambda: self.now[0])
        seq = [slow(calls=[READ], dt=60), slow("하나", dt=1), slow(calls=[READ], dt=60), slow("둘", dt=1)]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("하나"), "answered")
            self.assertEqual(s.turn("둘"), "answered")                          # 앞 차례의 61초를 들고 가지 않는다
        self.assertEqual(self.tool_less(), [])


from tests.test_hub import msg                # noqa: E402
from tests.test_hub3 import MainBase           # noqa: E402

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


class THubScreen(MainBase):
    """허브 화면: 시간 예산에서 받은 마지막 답이 대화에 답으로, 까닭 줄(경고)·차례 끝 이유가 보인다. 묶음에 끝 이유 이름."""

    def test_hub_chat_shows_time_budget(self):
        self.r.scripts["MAIN"] = [msg(calls=[("read_file", {"path": "a.txt"})]), msg(calls=[("read_file", {"path": "b.txt"})]),
                                  msg("시간 안에 본 것: a.txt 는 없다.")]
        c = self.main.new_chat("fresh")
        c.sess.time_budget = TB.TimeBudget(150, 0.85, clock=lambda: len(self.r.requests.get("MAIN", [])) * 100.0)
        c.sess._tb_each_turn = False
        c.send("찾아라")
        self.assertTrue(c.wait_idle())
        log = c.state()["main_log"]
        self.assertEqual([e["text"] for e in log if e["kind"] == "assistant"][-1], "시간 안에 본 것: a.txt 는 없다.")
        self.assertTrue(any(e["kind"] == "warn" and "시간 예산(150초의 85%)에 닿아" in e["text"] for e in log))
        self.assertTrue(any("time_budget" in e.get("text", "") for e in log if e["kind"] == "meta"))
        self.assertEqual(len(self.r.requests["MAIN"]), 3)                        # 걸음 둘 + 도구 없는 마지막 답
        self.assertNotIn("tools", self.r.requests["MAIN"][-1])

    def test_bundle_names_reason(self):
        js = (ROOT / "src" / "stewart" / "web_ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("시간 예산에 닿아 지금까지로 답함", js)


class TKnobs(unittest.TestCase):
    def test_env(self):
        self.assertIsNone(TB.from_env({}))
        self.assertIsNone(TB.from_env({"STEWART_TIME_BUDGET_S": "0"}))
        self.assertIsNone(TB.from_env({"STEWART_TIME_BUDGET_S": "abc"}))
        b = TB.from_env({"STEWART_TIME_BUDGET_S": "2700", "STEWART_TIME_BUDGET_FRAC": "0.8"})
        self.assertEqual((b.seconds, b.frac), (2700.0, 0.8))
        self.assertEqual(TB.from_env({"STEWART_TIME_BUDGET_S": "10", "STEWART_TIME_BUDGET_FRAC": "7"}).frac, TB.DEFAULT_FRAC)
        self.assertEqual(TB.make(600, {"STEWART_TIME_BUDGET_S": "10"}).seconds, 600.0)   # 손잡이가 환경 변수보다 앞
        self.assertIsNone(TB.make(0, {}))

    def test_cli_flag(self):
        r = subprocess.run([sys.executable, "-m", "stewart", "chat", "--help"], capture_output=True, text=True, timeout=60)
        self.assertIn("--time-budget", r.stdout)


if __name__ == "__main__":
    unittest.main()
