"""실패가 이어진 차례: 두 멈춤 사유(consecutive_errors·repeated_failure)와 그 앞의 도구 없는 되짚기 한 번(reflect.py).

A. 되짚기를 끄면(STEWART_REFLECT=0) 지금 동작 그대로: 문턱에서 바로 끝나고, chat 에서 "계속" 해도 같은 사유로 또 끝난다.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, engine
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI

_N = [0]


def r(content=None, calls=None):
    msg = {"role": "assistant", "content": content}
    if calls:
        _N[0] += 1
        msg["tool_calls"] = [{"id": f"r{_N[0]}-{i}", "type": "function",
                              "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
                             for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": msg, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, 0.1


FAIL = ("read_file", {"path": "없는.txt"})          # 늘 같은 실패
OK = ("read_file", {"path": "a.txt"})


def miss(i):                                       # 서로 다른 실패
    return ("read_file", {"path": f"없는-{i}.txt"})


class Base(unittest.TestCase):
    env = {"STEWART_REFLECT": "0"}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.wd = self.tmp / "work"
        self.wd.mkdir()
        (self.wd / "a.txt").write_text("안 파일")
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)
        self.sent = []        # (도구 정의를 보냈나, 메시지)

    def prof(self, **k):
        k.setdefault("max_steps", 12)
        return Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["read_file", "run_shell"],
                       workdir=self.wd, **k)

    def fake(self, seq):
        def f(backend, messages, tools=None, *a, **k):
            self.sent.append((tools is not None, json.loads(json.dumps(messages))))
            return seq[len(self.sent) - 1]
        return f

    def run_(self, seq, **pk):
        with mock.patch.object(engine, "chat", side_effect=self.fake(seq)):
            return engine.run(self.prof(**pk), Backend("fake", "http://x/v1", "m", "q", 100000), "일해",
                              Recorder(self.tmp / "runs", "t"))

    def sess(self, **pk):
        s = C.ChatSession(self.prof(**pk), Backend("fake", "http://x/v1", "m", "q", 100000),
                          Recorder(self.tmp / "runs", "chat-t"), FakeUI(), stream=False, mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        return s

    def events(self, rec_dir: Path) -> list[dict]:
        return [json.loads(l) for l in (rec_dir / "events.jsonl").read_text().splitlines()]


REPEAT6 = [r(calls=[FAIL, OK]) for _ in range(6)]             # 성공이 끼어 consecutive 는 0, 같은 실패 6번
CONSEC4 = [r(calls=[miss(i)]) for i in range(4)]              # 모두 실패한 단계 4번


class TOff(Base):
    """되짚기 끔 = 지금 동작."""

    def test_run_repeated_failure(self):
        s = self.run_(REPEAT6)
        self.assertEqual(s["stop_reason"], "repeated_failure")
        self.assertEqual(s["steps"], 6)
        self.assertTrue(all(t for t, _ in self.sent))            # 도구 없는 요청이 없다

    def test_run_consecutive_errors(self):
        s = self.run_(CONSEC4)
        self.assertEqual(s["stop_reason"], "consecutive_errors")
        self.assertEqual(s["steps"], 4)
        self.assertTrue(all(t for t, _ in self.sent))

    def test_chat_again_same_reason(self):
        """사용자 현상: 오류로 끝난 뒤 "계속" 하면 같은 실패를 되풀이해 같은 사유로 또 끝난다."""
        s = self.sess()
        with mock.patch.object(C, "chat", side_effect=self.fake(CONSEC4 + CONSEC4)):
            self.assertEqual(s.turn("일해"), "consecutive_errors")
            self.assertEqual(s.turn("계속"), "consecutive_errors")
        s2 = self.sess()
        self.sent = []
        with mock.patch.object(C, "chat", side_effect=self.fake(REPEAT6 + REPEAT6)):
            self.assertEqual(s2.turn("일해"), "repeated_failure")
            self.assertEqual(s2.turn("계속"), "repeated_failure")
        self.assertTrue(all(t for t, _ in self.sent))


REFLECT_TEXT = "1) 없는.txt 를 읽으려 함 2) 그 파일이 없다 3) 같은 호출 반복 4) list_dir 로 있는 파일을 본다"


class TOn(Base):
    """되짚기 켬."""
    env = {"STEWART_REFLECT": "1"}

    def tool_less(self):
        return [i for i, (t, _) in enumerate(self.sent) if not t]

    def test_run_consecutive_reflect_then_recover(self):
        s = self.run_(CONSEC4 + [r(REFLECT_TEXT), r(calls=[OK]), r("다 했다")])
        self.assertEqual(s["stop_reason"], "completed")
        self.assertEqual(self.tool_less(), [4])                    # 다섯째 요청이 도구 없는 되짚기
        msgs = self.sent[4][1]
        self.assertIn("[엔진] 실패가 이어져", msgs[-1]["content"])
        self.assertIn("없는-3.txt", msgs[-1]["content"])           # 엔진이 센 실패가 붙는다
        after = self.sent[5][1]
        self.assertEqual(after[-2], {"role": "assistant", "content": REFLECT_TEXT})
        self.assertIn("되짚기를 마쳤다", after[-1]["content"])
        rf = s["reflect"]
        self.assertEqual((rf["used"], rf["recovered"], rf["log"][0]["trigger"], rf["source"]),
                         (1, True, "consecutive_errors", "env"))
        ev = [e["kind"] for e in self.events(Path(s["run_dir"]))]
        for k in ("reflect_start", "reflect", "reflect_end"):
            self.assertIn(k, ev)

    def test_run_repeated_reflect_then_same_call_stops(self):
        """되짚기 뒤 앞에서 거듭 실패한 똑같은 호출을 또 하면 한 번에 끝난다."""
        s = self.run_(REPEAT6 + [r(REFLECT_TEXT), r(calls=[FAIL, OK])])
        self.assertEqual(s["stop_reason"], "repeated_failure")
        self.assertEqual(s["steps"], 7)
        self.assertEqual(s["reflect"]["used"], 1)
        self.assertFalse(s["reflect"]["recovered"])
        self.assertEqual(s["reflect"]["skipped"][0]["why"], "used_up")

    def test_run_second_threshold_stops(self):
        s = self.run_(CONSEC4 + [r(REFLECT_TEXT)] + [r(calls=[miss(10 + i)]) for i in range(4)])
        self.assertEqual(s["stop_reason"], "consecutive_errors")
        self.assertEqual(s["steps"], 8)
        self.assertEqual(self.tool_less(), [4])

    def test_run_default_off_profile_on(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STEWART_REFLECT", None)
            s = self.run_(CONSEC4)                                  # run 기본은 끔
            self.assertEqual(s["stop_reason"], "consecutive_errors")
            self.assertNotIn("reflect", s)
            self.sent = []
            s = self.run_(CONSEC4 + [r(REFLECT_TEXT), r("끝")], reflect_on_fail=True)
            self.assertEqual(s["stop_reason"], "completed")
            self.assertEqual(s["reflect"]["source"], "profile")

    def test_empty_reflection_stops_as_before(self):
        s = self.run_(CONSEC4 + [r("")])
        self.assertEqual(s["stop_reason"], "consecutive_errors")
        self.assertFalse(s["reflect"]["log"][0]["ok"])

    def test_tool_calls_in_reflection_dropped(self):
        s = self.run_(CONSEC4 + [r(REFLECT_TEXT, calls=[OK]), r("끝")])
        self.assertEqual(s["stop_reason"], "completed")
        self.assertEqual(s["tool_calls"], 4)                        # 되짚기 응답의 툴콜은 실행하지 않았다

    def test_chat_reflect_default_on(self):
        os.environ.pop("STEWART_REFLECT", None)
        s = self.sess()
        with mock.patch.object(C, "chat", side_effect=self.fake(CONSEC4 + [r(REFLECT_TEXT), r(calls=[OK]), r("고쳤다")])):
            self.assertEqual(s.turn("일해"), "answered")
        self.assertEqual(self.tool_less(), [4])
        self.assertEqual(s._reflect_log[0]["recovered"], True)
        self.assertEqual(s.summary()["reflects"], 1)

    def test_chat_reflect_once_per_turn(self):
        """U2-8: chat 은 같은 호출 되풀이(4번)에서 되짚고, 그 뒤 같은 호출은 막는다. 다른 길로 가면 차례가 이어진다."""
        s = self.sess()
        seq = (REPEAT6[:4] + [r(REFLECT_TEXT), r(calls=[OK]), r("됐다")]        # 차례 1: 되풀이한 a.txt 읽기 4번 → 되짚기 → 같은 호출은 막힘
               + REPEAT6[:4] + [r(REFLECT_TEXT), r("됐다")])                       # 차례 2: 되짚기 횟수는 차례마다 새로
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("일해"), "answered")
            self.assertEqual(s.turn("계속"), "answered")
        self.assertEqual([x["used"] for x in s._reflect_log], [1, 1])
        tool_msgs = [m for m in s.messages if m.get("role") == "tool"]
        self.assertTrue(any("되짚기 뒤라" in m["content"] for m in tool_msgs))           # 막은 호출은 돌리지 않았다
        ev = self.events(s.rec.dir)
        # 차례 2: 차례 1 의 금지(a.txt 읽기 꼴)가 세션에 남아 그 호출이 막히고 → 모두 실패한 단계로 되짚기
        self.assertEqual([e["trigger"] for e in ev if e["kind"] == "reflect_start"], ["repeated_call", "consecutive_errors"])

    def test_all_denied_skips(self):
        """문턱 단계 실패가 모두 거부면 되짚지 않고 지금처럼 끝낸다."""
        s = self.sess()
        s.mode = "ask"
        s.ui.answers = ["n"] * 4
        seq = [r(calls=[("run_shell", {"command": f"touch x{i}"})]) for i in range(4)]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("일해"), "consecutive_errors")
        self.assertEqual(self.tool_less(), [])
        self.assertEqual(s._reflect_log[0]["skipped"][0]["why"], "all_denied")

    def test_parse_failure_path(self):
        from stewart.backend import BackendError
        s = self.sess()
        bad = BackendError("500", status=500, body="Failed to parse tool call")
        seq = [bad] * 4 + [r(REFLECT_TEXT), r("끝")]
        def f(backend, messages, tools=None, *a, **k):
            self.sent.append((tools is not None, None))
            x = seq[len(self.sent) - 1]
            if isinstance(x, Exception):
                raise x
            return x
        with mock.patch.object(C, "chat", side_effect=f):
            self.assertEqual(s.turn("일해"), "answered")
        self.assertEqual(self.tool_less(), [4])


class TWorkerReport(unittest.TestCase):
    def test_turn_report_no_stale(self):
        from stewart.worker import turn_report
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "지시 1"},
                {"role": "assistant", "content": "지시 1 보고"}, {"role": "user", "content": "지시 2"}]
        m0 = len(msgs) - 1
        msgs += [{"role": "assistant", "content": None, "tool_calls": [{"id": "a"}]},
                 {"role": "tool", "tool_call_id": "a", "content": "오류"}]
        t = turn_report(msgs, m0, "consecutive_errors")
        self.assertNotIn("지시 1 보고", t)                            # 전에는 앞 지시 보고가 다시 나갔다
        self.assertIn("종료 consecutive_errors", t)
        msgs += [{"role": "assistant", "content": "되짚은 글"}, {"role": "user", "content": "go"}]
        t = turn_report(msgs, m0, "repeated_failure", {"log": [{"ok": True}]})
        self.assertIn("[되짚기 글]\n되짚은 글", t)
        msgs.append({"role": "assistant", "content": "지시 2 보고"})
        self.assertEqual(turn_report(msgs, m0, "answered"), "지시 2 보고")


if __name__ == "__main__":
    unittest.main()
