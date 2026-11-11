"""판 81 견고성: 본문 툴콜 직접 파싱(가-1)·깨진 인자 JSON 복구(가-2)·편집 되먹임(가-3)·생각 되돌려 보내기 손잡이(가-5).
실사용(chat) 기본 켬, 평가 길(engine.run) 기본 끔 — 둘 다 STEWART_TOOLCALL_REPAIR·STEWART_EDIT_FEEDBACK=0|1 이 이긴다."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, engine, toolrepair as TR
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI

NAMES = ["run_shell", "read_file", "write_file", "edit_file"]
_N = [0]


def r(content=None, calls=None, think=None, fin="stop"):
    msg = {"role": "assistant", "content": content}
    if think:
        msg["reasoning_content"] = think
    if calls:
        _N[0] += 1
        msg["tool_calls"] = [{"id": f"c{_N[0]}-{i}", "type": "function", "function": {"name": n, "arguments": a}}
                             for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": msg, "finish_reason": fin}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, 0.1


class TRepairArgs(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(TR.repair_args('{"path": "a.py",}'), {"path": "a.py"})                      # 끝 쉼표
        self.assertEqual(TR.repair_args('```json\n{"path": "a.py"}\n```'), {"path": "a.py"})         # 코드 울타리
        self.assertEqual(TR.repair_args("{'path': 'a.py', 'append': True}"), {"path": "a.py", "append": True})   # 파이썬 꼴
        self.assertEqual(TR.repair_args('{"content": "a\nb"}'), {"content": "a\nb"})                 # 글 안 날 줄바꿈
        self.assertEqual(TR.repair_args('"{\\"path\\": \\"a.py\\"}"'), {"path": "a.py"})             # 두 번 감쌈
        self.assertEqual(TR.repair_args("{“path”: “a.py”}"), {"path": "a.py"})  # 둥근 따옴표
        self.assertIsNone(TR.repair_args('{"path": "a.py", "content": "def f():'))                    # 잘린 것은 고치지 않는다
        self.assertIsNone(TR.repair_args("[1, 2]"))
        self.assertIsNone(TR.repair_args("__import__('os').system('x')"))                             # 실행하지 않는다


class TExtract(unittest.TestCase):
    def test_qwen_json_block(self):
        rest, calls = TR.extract('먼저 봅니다.\n<tool_call>\n{"name": "read_file", "arguments": {"path": "a.py"}}\n</tool_call>', NAMES)
        self.assertEqual(rest, "먼저 봅니다.")
        self.assertEqual(calls[0]["function"]["name"], "read_file")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"path": "a.py"})

    def test_qwen_coder_xml(self):
        text = "<tool_call>\n<function=run_shell>\n<parameter=command>\nls -la\n</parameter>\n<parameter=timeout>\n30\n</parameter>\n</function>\n</tool_call>"
        rest, calls = TR.extract(text, NAMES)
        self.assertEqual(rest, "")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"command": "ls -la", "timeout": 30})

    def test_fence_and_bare_and_two(self):
        _, c = TR.extract('```json\n{"name": "run_shell", "arguments": {"command": "pwd"}}\n```', NAMES)
        self.assertEqual(c[0]["function"]["name"], "run_shell")
        _, c = TR.extract('{"name": "read_file", "parameters": {"path": "x"}}', NAMES)
        self.assertEqual(json.loads(c[0]["function"]["arguments"]), {"path": "x"})
        _, c = TR.extract('<tool_call>{"name": "read_file", "arguments": {"path": "a"}}</tool_call>'
                          '<tool_call>{"name": "read_file", "arguments": {"path": "b"}}</tool_call>', NAMES)
        self.assertEqual(len(c), 2)

    def test_leaves_unsure_alone(self):
        for t in ['예시: {"name": "delete_all", "arguments": {}}',                       # 없는 도구
                  "```json\n{\"a\": 1}\n```",                                          # 툴콜 아님
                  '<tool_call>{"name": "read_file", "arguments": {"path": "a"}}</tool_call><tool_call>{"name": "nope"}</tool_call>',
                  "read_file 로 a.py 를 읽겠습니다."]:
            self.assertEqual(TR.extract(t, NAMES), (t, []), t)


class TEditFeedback(unittest.TestCase):
    def test_context_and_syntax(self):
        d = Path(tempfile.mkdtemp())
        (d / "m.py").write_text("".join(f"x{i} = {i}\n" for i in range(20)) + "def f(:\n", encoding="utf-8")
        fb = TR.edit_feedback(d, "edit_file", {"path": "m.py", "old": "x", "new": "x9 = 9\n"})
        self.assertIn("고친 자리(줄 10~11", fb)
        self.assertIn("    7\tx6 = 6", fb)
        self.assertIn("문법 검사(.py): 오류 — 21줄", fb)
        (d / "c.json").write_text('{"a": 1}', encoding="utf-8")
        self.assertIn("통과", TR.edit_feedback(d, "write_file", {"path": "c.json", "content": "x"}))
        (d / "c.toml").write_text('a = [', encoding="utf-8")
        self.assertIn("오류", TR.edit_feedback(d, "write_file", {"path": "c.toml", "content": "x"}))
        (d / "n.txt").write_text("x", encoding="utf-8")
        self.assertEqual(TR.edit_feedback(d, "write_file", {"path": "n.txt", "content": "x"}), "")
        self.assertEqual(TR.edit_feedback(d, "read_file", {"path": "m.py"}), "")

    def test_knobs(self):
        self.assertTrue(TR.on("K", True, {}))
        self.assertFalse(TR.on("K", False, {}))
        self.assertFalse(TR.on("K", True, {"K": "0"}))
        self.assertTrue(TR.on("K", False, {"K": "1"}))
        self.assertEqual(TR.passback({}), "off")
        self.assertEqual(TR.passback({"STEWART_REASONING_PASSBACK": "tool_turns"}), "tool_turns")
        self.assertEqual(TR.passback({"STEWART_REASONING_PASSBACK": "x"}), "off")
        self.assertFalse(TR.keep_reasoning("tool_turns", "생각", False))
        self.assertTrue(TR.keep_reasoning("tool_turns", "생각", True))
        self.assertTrue(TR.keep_reasoning("all", "생각", False))


class TChat(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a.py").write_text("x = 1\n", encoding="utf-8")

    def sess(self, **env):
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.tmp, max_steps=6)
        with mock.patch.dict(os.environ, env):
            s = C.ChatSession(p, Backend("f", "http://x/v1", "m", "q", 100000), Recorder(self.tmp / "runs", "c"), FakeUI(),
                              stream=False, mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        self.addCleanup(lambda: C.T.close_env(s.env))
        return s

    def go(self, s, seq):
        sent = []

        def f(backend, messages, tools=None, *a, **k):
            sent.append(json.loads(json.dumps(messages)))
            return seq[min(len(sent) - 1, len(seq) - 1)]
        with mock.patch.object(C, "chat", side_effect=f):
            s.turn("a.py 를 고쳐 줘")
        return sent

    def test_in_content_and_args_and_feedback(self):
        s = self.sess()
        seq = [r('<tool_call>\n{"name": "read_file", "arguments": {"path": "a.py"}}\n</tool_call>', think="본다"),
               r(calls=[("edit_file", '{"path": "a.py", "old": "x = 1", "new": "x = 2",}')]),
               r("고쳤다")]
        sent = self.go(s, seq)
        tools = [m for m in sent[-1] if m["role"] == "tool"]
        self.assertIn("x = 1", tools[0]["content"])                       # 본문 툴콜이 실제로 돌았다
        self.assertEqual((self.tmp / "a.py").read_text(), "x = 2\n")       # 끝 쉼표 인자를 고쳐 편집
        self.assertIn("고친 자리(줄 1~1", tools[1]["content"])
        self.assertIn("문법 검사(.py): 통과", tools[1]["content"])
        ev = (s.rec.dir / "events.jsonl").read_text()
        self.assertEqual(ev.count('"repaired": true'), 4)                   # broken_toolcall 사건 둘 + component 둘
        self.assertEqual(s.st["toolcall_repaired"], 2)
        self.assertNotIn("reasoning_content", json.dumps(sent[-1]))         # 생각 되돌려 보내기 기본 끔

    def test_off_and_passback(self):
        s = self.sess(STEWART_TOOLCALL_REPAIR="0", STEWART_EDIT_FEEDBACK="0", STEWART_REASONING_PASSBACK="tool_turns")
        seq = [r(calls=[("edit_file", '{"path": "a.py", "old": "x = 1", "new": "x = 2",}')], think="고친다"),
               r(calls=[("edit_file", '{"path": "a.py", "old": "x = 1", "new": "x = 2"}')], think="다시"),
               r("끝", think="마무리")]
        sent = self.go(s, seq)
        tools = [m for m in sent[-1] if m["role"] == "tool"]
        self.assertIn("인자 JSON 파싱 실패", tools[0]["content"])
        self.assertNotIn("고친 자리", tools[1]["content"])
        asst = [m for m in sent[-1] if m["role"] == "assistant"]
        self.assertEqual([m.get("reasoning_content") for m in asst], ["고친다", "다시"])
        self.assertNotIn("reasoning_content", s.messages[-1])              # 툴콜 없는 마지막 답은 tool_turns 에서 빼다


class TEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    def run_(self, **env):
        seq = [r('<tool_call>{"name": "read_file", "arguments": {"path": "m.py"}}</tool_call>'), r("끝")]
        n = [0]

        def fake(*a, **k):
            n[0] += 1
            return seq[min(n[0] - 1, len(seq) - 1)]
        prof = Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["read_file"], workdir=self.tmp, max_steps=4)
        with mock.patch.dict(os.environ, env), mock.patch.object(engine, "chat", side_effect=fake):
            return engine.run(prof, Backend("fake", "http://x/v1", "m", "q", 100000), "m.py 를 읽어", Recorder(self.tmp / "runs", "t"))

    def test_eval_default_off_on_with_env(self):
        s = self.run_()
        self.assertEqual(s["broken_toolcalls"], {"in_content": 1})          # 예전처럼 다시 요청(고치지 않음)
        self.assertEqual(s["toolcall_repaired"], {})
        s = self.run_(STEWART_TOOLCALL_REPAIR="1")
        self.assertEqual(s["toolcall_repaired"], {"in_content": 1})
        self.assertEqual(s["broken_toolcalls"], {"in_content": 1})          # 깨진 수는 그대로 세어 전후 비교가 된다


if __name__ == "__main__":
    unittest.main()
