"""같은 호출 되풀이로 멈출 때: 되짚기 상한, 사람 없는 chat 의 마지막 답 한 차례, 한 응답 안 같은 호출 한 번만.

- 되짚기·마지막 답은 도구 없는 요청이다. 생각을 켠 백엔드에서 상한이 생각에 다 먹혀 빈 답이 되던 것을 재현하고 고친 꼴을 본다.
- auto(--once 포함)는 물을 사람이 없어 멈출 때 도구 없이 한 번 답을 받는다. 묻는 모드에서 사람이 멈추라 하면 받지 않는다.
- 한 응답 안의 글자까지 같은 읽기 호출은 한 번만 돈다(가드도 한 번만 센다). 셸 같은 호출은 그대로 두 번 돈다.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C
from stewart import guard as G
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI

_N = [0]


def r(content=None, calls=None, finish="stop", reasoning=0):
    msg = {"role": "assistant", "content": content}
    if calls:
        _N[0] += 1
        msg["tool_calls"] = [{"id": f"q{_N[0]}-{i}", "type": "function",
                              "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
                             for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5 + reasoning,
                      "completion_tokens_details": {"reasoning_tokens": reasoning}}}, 0.1


READ = ("read_file", {"path": "a.txt"})


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
        self.sent = []        # (도구 정의를 보냈나, 보낸 키워드, 메시지)

    def backend(self, thinking=False, max_tokens=None):
        params = {"chat_template_kwargs": {"enable_thinking": thinking}}
        if max_tokens:
            params["max_tokens"] = max_tokens
        return Backend("fake", "http://x/v1", "m", "q", 100000, params=params)

    def sess(self, mode="auto", **bk):
        p = Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["read_file", "run_shell"],
                    workdir=self.wd, max_steps=20)
        s = C.ChatSession(p, self.backend(**bk), Recorder(self.tmp / "runs", "chat-t"), FakeUI(), stream=False, mode=mode)
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        self.addCleanup(lambda: C.T.close_env(s.env))
        return s

    def fake(self, seq):
        def f(backend, messages, tools=None, *a, **k):
            self.sent.append((tools is not None, k, json.loads(json.dumps(messages))))
            x = seq[len(self.sent) - 1]
            return x(k) if callable(x) else x
        return f

    def tool_less(self):
        return [i for i, (t, _, _) in enumerate(self.sent) if not t]

    def events(self, s):
        return [json.loads(l) for l in (s.rec.dir / "events.jsonl").read_text().splitlines()]


class TFinalAnswer(Base):
    """사람 없는 chat 이 같은 호출 되풀이로 멈추면 도구 없이 한 번 답한다."""

    def test_auto_stop_gets_final_answer(self):
        s = self.sess()
        seq = [r(calls=[READ]) for _ in range(G.STOP)] + [r("찾은 것으로는 X 다. 확인하지 못한 것: Y.")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("찾아 줘"), "repeated_call")
        self.assertEqual(self.tool_less(), [G.STOP])                     # 멈춘 뒤 도구 없는 요청 한 번
        last_user = self.sent[-1][2][-1]
        self.assertEqual(last_user["role"], "user")
        self.assertIn("도구는 더 부를 수 없다", last_user["content"])
        self.assertIn("모르면 모른다", last_user["content"])
        self.assertEqual(s._last_answer(), "찾은 것으로는 X 다. 확인하지 못한 것: Y.")
        self.assertEqual([e["ok"] for e in self.events(s) if e["kind"] == "final_answer"], [True])

    def test_once_output_has_answer(self):
        s = self.sess()
        seq = [r(calls=[READ]) for _ in range(G.STOP)] + [r("모른다 — 위 결과에는 답이 없다.")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            out = s.run_once("찾아 줘")
        self.assertEqual(out["stop_reason"], "repeated_call")             # 멈춘 까닭은 그대로 적는다
        summ = s.close()
        self.assertFalse(summ["success"])
        self.assertIn("모른다", (s.rec.dir / "output.md").read_text())

    def test_human_stop_gets_no_extra_answer(self):
        s = self.sess(mode="edits")
        s.ui.answers = ["n"]
        seq = [r(calls=[READ]) for _ in range(G.STOP)]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("찾아 줘"), "repeated_call")
        self.assertEqual(self.tool_less(), [])                            # 사람이 멈추라 했다 — 더 묻지 않는다

    def test_empty_final_answer_is_noted(self):
        s = self.sess()
        seq = [r(calls=[READ]) for _ in range(G.STOP)] + [r("", finish="length")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("찾아 줘"), "repeated_call")
        self.assertEqual([e["ok"] for e in self.events(s) if e["kind"] == "final_answer"], [False])
        self.assertIn("마지막 답을 받지 못했다", s.messages[-1]["content"])


class TDupInResponse(Base):
    """한 응답 안의 글자까지 같은 읽기 호출."""

    def test_same_read_twice_runs_once(self):
        s = self.sess()
        seq = [r(calls=[READ, READ, ("read_file", {"path": "b.txt"})]), r("됐다")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("읽어"), "answered")
        tools = [m for m in s.messages if m.get("role") == "tool"]
        self.assertEqual(len(tools), 3)                                    # 툴콜마다 결과 자리는 있다
        self.assertIn("안 파일", tools[0]["content"])
        self.assertEqual(tools[1]["content"], C.DUP_NOTE)
        self.assertEqual(s.st["tool_calls"], 2)                            # 실제로 돈 것은 둘
        self.assertEqual(s.st["dup_calls"], 1)
        self.assertEqual(len([e for e in self.events(s) if e["kind"] == "dup_call"]), 1)

    def test_guard_counts_once(self):
        """같은 읽기를 한 응답에 둘씩 세 번 = 가드는 3(전에는 6 — 응답 세 번에 멈춤)."""
        s = self.sess()
        seq = [r(calls=[READ, READ]) for _ in range(3)] + [r("됐다")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("읽어"), "answered")
        self.assertEqual(s.guard.recent[-1][0].split("\0")[0], "read_file")
        self.assertEqual(len(s.guard.recent), 3)

    def test_shell_twice_runs_twice(self):
        s = self.sess()
        cmd = ("run_shell", {"command": "echo x >> log.txt"})
        seq = [r(calls=[cmd, cmd]), r("됐다")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("돌려"), "answered")
        self.assertEqual((self.wd / "log.txt").read_text(), "x\nx\n")
        self.assertNotIn("dup_calls", s.st)


def thinker(text):
    """생각을 끄지 않으면 상한을 생각에 다 써 빈 답(finish=length)을 내는 가짜 모델의 도구 없는 응답."""
    def f(k):
        if k.get("thinking") is False:
            return r(text)
        return r("", finish="length", reasoning=k.get("max_tokens") or 0)
    return f


class TReflectThinkingOff(Base):
    """생각을 백엔드 params 로 켠 세션: 되짚기가 생각에 먹혀 빈 답이던 것."""
    env = {"STEWART_REFLECT": "1"}

    def test_chat_reflect_sent_without_thinking(self):
        s = self.sess(thinking=True, max_tokens=8192)
        seq = [r(calls=[READ]) for _ in range(G.REFLECT_AT)] + [thinker("1) 같은 파일을 읽었다\n다음: 답한다"), r("답")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("읽어"), "answered")
        i = self.tool_less()[0]
        self.assertIs(self.sent[i][1]["thinking"], False)
        self.assertEqual(self.sent[i][1]["max_tokens"], 1200)
        ev = [e for e in self.events(s) if e["kind"] == "reflect"]
        self.assertTrue(ev[0]["ok"])

    def test_final_answer_also_without_thinking(self):
        os.environ["STEWART_REFLECT"] = "0"
        s = self.sess(thinking=True, max_tokens=8192)
        seq = [r(calls=[READ]) for _ in range(G.STOP)] + [thinker("모른다")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("읽어"), "repeated_call")
        self.assertEqual(s._last_answer(), "모른다")

    def test_fixed_thinking_backend_keeps_room(self):
        from stewart import reflect as RF
        b = self.backend(thinking=True, max_tokens=8192)
        self.assertEqual(RF.notool_kw({"max_tokens": 1200}, backend=b), ({"thinking": False}, 1200))
        b.thinking_control = "none"                                            # 생각을 끌 수 없는 API
        self.assertEqual(RF.notool_kw({"max_tokens": 1200}, backend=b)[1], 1200 + 8192)
        self.assertEqual(RF.notool_kw({"max_tokens": 1200}, backend=b, think_max=4096)[1], 1200 + 4096)

    def test_run_reflect_without_thinking(self):
        from stewart import engine
        p = Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["read_file"], workdir=self.wd,
                    max_steps=12, reflect_on_fail=True)
        miss = [r(calls=[("read_file", {"path": f"없는-{i}.txt"})]) for i in range(4)]
        seq = miss + [thinker("1) 없는 파일\n다음: 끝"), r("끝")]
        with mock.patch.object(engine, "chat", side_effect=self.fake(seq)):
            out = engine.run(p, self.backend(thinking=True, max_tokens=8192), "일해", Recorder(self.tmp / "runs", "t"))
        self.assertEqual(out["stop_reason"], "completed")
        self.assertTrue(out["reflect"]["log"][0]["ok"])


EMPTY = r("", finish="length", reasoning=8192)   # 생각만 하다 상한에 닿은 빈 응답


class TLengthStreak(Base):
    """연속 잘림 끊기 손잡이(STEWART_LENGTH_STREAK, 기본 끔)."""

    def seq_two_streak(self, tail):
        return [EMPTY, r(calls=[READ]),                                   # 걸음 1: 잘림 → 생각 끄고 다시 → 호출
                EMPTY] + tail                                             # 걸음 2: 연달아 잘림

    def test_default_off_keeps_retrying(self):
        s = self.sess(thinking=True)
        seq = [EMPTY, r(calls=[READ]), EMPTY, r(calls=[("read_file", {"path": "b.txt"})]), EMPTY, r("답")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("읽어"), "answered")
        self.assertEqual(s.st["length_empty_retries"], 3)
        self.assertFalse([e for e in self.events(s) if e["kind"] == "length_streak"])

    def test_streak_auto_final_answer(self):
        s = self.sess(thinking=True)
        with mock.patch.dict(os.environ, {"STEWART_LENGTH_STREAK": "2"}), \
                mock.patch.object(C, "chat", side_effect=self.fake(self.seq_two_streak([r("본 것으로는 모른다")]))):
            s.len_streak_at = C.length_streak_at()
            self.assertEqual(s.turn("읽어"), "length_empty")
        self.assertEqual([e["streak"] for e in self.events(s) if e["kind"] == "length_streak"], [2])
        self.assertEqual(self.tool_less(), [3])                             # 다시 보내지 않고 바로 도구 없는 마지막 답
        self.assertIs(self.sent[3][1]["thinking"], False)
        self.assertIn("연달아", self.sent[3][2][-1]["content"])
        self.assertEqual(s._last_answer(), "본 것으로는 모른다")

    def test_streak_reflects_first_when_on(self):
        os.environ["STEWART_REFLECT"] = "1"
        s = self.sess(thinking=True)
        tail = [r("1) 같은 생각을 되풀이했다\n다음: 답한다"), r("답")]
        with mock.patch.dict(os.environ, {"STEWART_LENGTH_STREAK": "2"}), \
                mock.patch.object(C, "chat", side_effect=self.fake(self.seq_two_streak(tail))):
            s.len_streak_at = C.length_streak_at()
            self.assertEqual(s.turn("읽어"), "answered")
        self.assertEqual([e["trigger"] for e in self.events(s) if e["kind"] == "reflect"], ["length_streak"])

    def test_knob_values(self):
        self.assertEqual(C.length_streak_at({}), 0)
        self.assertEqual(C.length_streak_at({"STEWART_LENGTH_STREAK": "x"}), 0)
        self.assertEqual(C.length_streak_at({"STEWART_LENGTH_STREAK": "3"}), 3)


if __name__ == "__main__":
    unittest.main()
