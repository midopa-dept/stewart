"""생각 글 기록(reasoning·reasoning_content 둘 다)과 잘린 단계 되풀이 지표."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import engine
from stewart.compact import repeat_stats
from stewart.config import Backend, Profile
from stewart.record import Recorder


def resp(content, reasoning=None, key="reasoning", finish="stop", ct=10):
    msg = {"role": "assistant", "content": content}
    if reasoning is not None:
        msg[key] = reasoning
    return {"choices": [{"message": msg, "finish_reason": finish}], "usage": {"prompt_tokens": 10, "completion_tokens": ct}}, 0.1


LOOP = "먼저 파일을 읽고 함수 이름을 확인한다. " + "잠깐, 다시 생각해 보면 이 값은 3 이어야 한다. 그러니 다시 확인한다. " * 120
LONG = " ".join(f"단계 {i}: 변수 x{i} 를 y{i * 7} 과 비교하고 경계 {i * 13} 을 따진다." for i in range(300))


class TRepeat(unittest.TestCase):
    def test_loop_vs_long(self):
        a, b = repeat_stats(LOOP), repeat_stats(LONG)
        self.assertGreater(a["tail_seen16"], 0.9)
        self.assertGreater(a["dup16"], 0.9)
        self.assertLess(b["tail_seen16"], 0.1)
        self.assertLess(b["dup16"], 0.1)
        self.assertEqual(repeat_stats("")["dup16"], 0.0)


class TRecord(unittest.TestCase):
    def run_one(self, seq):
        tmp = Path(tempfile.mkdtemp())
        p = Profile(name="t", path=tmp / "p.toml", system="s", tools=["read_file"], workdir=tmp, max_steps=3)
        rec = Recorder(tmp / "runs", "t")
        with mock.patch.object(engine, "chat", side_effect=seq):
            s = engine.run(p, Backend("fake", "http://x/v1", "m", "q", 100000), "do", rec)
        steps = [json.loads(l) for l in (rec.dir / "events.jsonl").read_text().splitlines()]
        return s, [e for e in steps if e["kind"] == "step"]

    def test_reasoning_key_recorded(self):
        for key in ("reasoning", "reasoning_content"):
            _, st = self.run_one([resp("끝", "생각 글", key=key)])
            self.assertEqual(st[0]["response"]["reasoning_content"], "생각 글", key)
            self.assertNotIn("length_stats", st[0]["response"])

    def test_length_step_stats(self):
        # 빈 잘림은 버리고 다시 보낸다 — 첫 응답의 지표는 length_retry 사건에, 두 번째 잘림은 step 에
        s, st = self.run_one([resp("", LOOP, finish="length", ct=8192), resp("", LOOP, finish="length", ct=8192)])
        ls = st[0]["response"]["length_stats"]
        self.assertGreater(ls["think"]["tail_seen16"], 0.9)
        self.assertEqual(ls["content"]["chars"], 0)
        self.assertEqual(s["length_stops"], 2)


class TLengthRetry(unittest.TestCase):
    """finish_reason=length 이고 본문·툴콜이 없으면 생각 끔 + 알림으로 같은 단계 한 번 다시."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["read_file"], workdir=self.tmp, max_steps=4)

    def go(self, seq):
        rec = Recorder(self.tmp / "runs", "t")
        with mock.patch.object(engine, "chat", side_effect=seq) as m:
            s = engine.run(self.p, Backend("fake", "http://x/v1", "m", "q", 100000), "do", rec)
        ev = [json.loads(l) for l in (rec.dir / "events.jsonl").read_text().splitlines()]
        return s, m, ev, rec

    def test_retry_then_answer(self):
        s, m, ev, rec = self.go([resp("", LOOP, finish="length", ct=8192), resp("답", None)])
        self.assertEqual((s["stop_reason"], s["steps"], s["length_empty_retries"], s["length_stops"]), ("completed", 1, 1, 1))
        self.assertEqual((rec.dir / "output.md").read_text().strip() if (rec.dir / "output.md").exists() else "답", "답")
        first, second = m.call_args_list
        self.assertNotIn("thinking", first.kwargs)
        self.assertIs(second.kwargs["thinking"], False)                         # 생각 끔
        msgs = second.args[1]                                                   # 같은 목록(뒤에 답이 붙었다)
        self.assertEqual(msgs[-2]["role"], "user")
        self.assertIn("생각이 단계당 생성 상한에 닿아", msgs[-2]["content"])     # 알림은 다시 보낸 요청의 마지막 메시지
        self.assertEqual(msgs[-1], {"role": "assistant", "content": "답"})
        self.assertEqual(second.args[2], first.args[2])                         # 도구 목록은 그대로(같은 단계)
        lr = [e for e in ev if e["kind"] == "length_retry"]
        self.assertEqual(len(lr), 1)
        self.assertGreater(lr[0]["length_stats"]["think"]["tail_seen16"], 0.9)
        self.assertTrue(lr[0]["reasoning_content"].startswith("먼저 파일을"))
        self.assertIn("생각 끔 + 알림으로 같은 단계 다시", (rec.dir / "transcript.md").read_text())

    def test_second_empty_is_length_empty(self):
        s, m, ev, _ = self.go([resp("", "a", finish="length"), resp("", "b", finish="length"), resp("안 닿음")])
        self.assertEqual((s["stop_reason"], m.call_count, s["success"]), ("length_empty", 2, False))

    def test_not_retried_when_content_or_tool(self):
        s, m, _, _ = self.go([resp("잘린 답이지만 글이 있다", "x", finish="length")])
        self.assertEqual((s["stop_reason"], m.call_count), ("completed", 1))           # 글이 있으면 그대로(전과 같음)
        (self.tmp / "a.txt").write_text("hi")
        call = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}]},
            "finish_reason": "length"}], "usage": {"prompt_tokens": 5, "completion_tokens": 9}}, 0.1
        s, m, _, _ = self.go([call, resp("끝")])
        self.assertEqual((s["stop_reason"], m.call_count), ("completed", 2))
        self.assertNotIn("thinking", m.call_args_list[1].kwargs)                 # 툴콜이 있으면 다시 보내기 아님

    def test_chat_same(self):
        from stewart import chat as C
        from tests.test_cloud import FakeUI
        ui = FakeUI()
        sess = C.ChatSession(self.p, Backend("fake", "http://x/v1", "m", "q", 100000), Recorder(self.tmp / "runs", "chat-t"), ui,
                             stream=False)
        self.addCleanup(lambda: sess.rec._ev.closed or sess.rec._ev.close())
        with mock.patch.object(C, "chat", side_effect=[resp("", "x", finish="length"), resp("답")]) as m:
            self.assertEqual(sess.turn("do"), "answered")
        self.assertIs(m.call_args_list[1].kwargs["thinking"], False)
        self.assertEqual(sess.st["length_empty_retries"], 1)
        with mock.patch.object(C, "chat", side_effect=[resp("", "x", finish="length"), resp("", "y", finish="length")]):
            self.assertEqual(sess.turn("again"), "length_empty")
        self.assertIn("답이 비었다", ui.out.getvalue())

if __name__ == "__main__":
    unittest.main()
