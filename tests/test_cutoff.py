"""생성 상한에서 잘린 응답(U2-1): 깨진 툴콜 인자를 보낼 때 고치기, 잘렸다는 도구 결과, 끊긴 글 답 잇기, heredoc·인터프리터 검사."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import backend as B, chat as C, engine
from stewart.config import Backend, Profile
from stewart.record import Recorder
from stewart.shell import Shell, open_heredoc, interp_hint
from tests.test_cloud import FakeUI


def resp(content=None, calls=None, finish="stop"):
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [{"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": a}}
                             for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": msg, "finish_reason": finish}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, 0.1


CUT = ("write_file", '{"path": "a.py", "content": "def f():\\n    retur')   # 상한에서 끊긴 인자(llama-server 실측 꼴)


class TSafe(unittest.TestCase):
    def test_safe_messages(self):
        msgs = [{"role": "user", "content": "x"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "a", "type": "function", "function": {"name": "write_file", "arguments": CUT[1]}},
                    {"id": "b", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a"}'}}]}]
        out = B.safe_messages(msgs)
        self.assertEqual(out[1]["tool_calls"][0]["function"]["arguments"], "{}")
        self.assertEqual(out[1]["tool_calls"][1]["function"]["arguments"], '{"path": "a"}')
        self.assertEqual(msgs[1]["tool_calls"][0]["function"]["arguments"], CUT[1])   # 원본은 그대로(기록·이어 하기)
        ok = [{"role": "user", "content": "x"}]
        self.assertIs(B.safe_messages(ok), ok)
        body = B._body(Backend("f", "http://x/v1", "m", "q", 1000), msgs)
        self.assertEqual(body["messages"][1]["tool_calls"][0]["function"]["arguments"], "{}")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.wd = self.tmp / "w"
        self.wd.mkdir()
        p = mock.patch.dict(os.environ, {"STEWART_REFLECT": "0"})
        p.start()
        self.addCleanup(p.stop)
        self.sent = []

    def prof(self):
        return Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["read_file", "write_file", "run_shell"],
                       workdir=self.wd, max_steps=8, max_tokens=500)

    def fake(self, seq):
        def f(backend, messages, tools=None, *a, **k):
            self.sent.append(json.loads(json.dumps(messages)))
            return seq[len(self.sent) - 1]
        return f


class TChat(Base):
    def sess(self):
        s = C.ChatSession(self.prof(), Backend("fake", "http://x/v1", "m", "q", 100000), Recorder(self.tmp / "runs", "c"),
                          FakeUI(), stream=False, mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        return s

    def test_cut_toolcall_note_then_split(self):
        s = self.sess()
        seq = [resp(calls=[CUT], finish="length"),
               resp(calls=[("write_file", '{"path": "a.py", "content": "x = 1\\n"}')]), resp("다 썼다")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("길게 써"), "answered")
        tool = [m["content"] for m in self.sent[1] if m.get("role") == "tool"][0]
        self.assertIn("생성 상한(max_tokens 500)", tool)
        self.assertIn("write_file", tool)
        self.assertNotIn("인자 JSON 파싱 실패", tool)
        self.assertEqual(s.st["cut_toolcalls"], 1)
        self.assertEqual((self.wd / "a.py").read_text(), "x = 1\n")

    def test_plain_parse_error_unchanged(self):
        s = self.sess()
        seq = [resp(calls=[CUT], finish="tool_calls"), resp("끝")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            s.turn("해")
        tool = [m["content"] for m in self.sent[1] if m.get("role") == "tool"][0]
        self.assertIn("인자 JSON 파싱 실패", tool)

    def test_cut_text_continues(self):
        s = self.sess()
        seq = [resp("앞부분 설명이 이어지다", finish="length"), resp("뒷부분까지 끝.")]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("설명해"), "answered")
        self.assertIn("끊긴 자리부터 이어 써라", self.sent[1][-1]["content"])
        self.assertEqual(s.messages[-1]["content"], "뒷부분까지 끝.")

    def test_cut_text_continue_cap(self):
        s = self.sess()
        seq = [resp(f"조각 {i}", finish="length") for i in range(3)]
        with mock.patch.object(C, "chat", side_effect=self.fake(seq)):
            self.assertEqual(s.turn("설명해"), "cut_answer")   # 두 번 이은 뒤 셋째도 끊기면 다 된 답으로 받지 않는다(판 131)
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(s.messages[-1]["content"], "조각 2")   # 끊긴 글은 남긴다


class TRun(Base):
    def test_run_cut_note(self):
        seq = [resp(calls=[CUT], finish="length"), resp("끝")]
        with mock.patch.object(engine, "chat", side_effect=self.fake(seq)):
            s = engine.run(self.prof(), Backend("fake", "http://x/v1", "m", "q", 100000), "써", Recorder(self.tmp / "runs", "t"))
        tool = [m["content"] for m in self.sent[1] if m.get("role") == "tool"][0]
        self.assertIn("생성 상한", tool)
        self.assertEqual(s["cut_toolcalls"], 1)


class TShell(unittest.TestCase):
    def test_open_heredoc(self):
        self.assertIsNone(open_heredoc("python3 - <<'EOF'\nprint(1)\nEOF"))
        self.assertIsNone(open_heredoc("cat <<-X\n\tline\n\tX"))
        self.assertIsNone(open_heredoc("cat <<< 'here string'"))
        self.assertIsNone(open_heredoc("echo 'a << b'"))
        self.assertIsNone(open_heredoc("cat > a <<A\n1\nA\ncat > b <<B\n2\nB"))
        self.assertEqual(open_heredoc("python3 - <<'EOF'\nprint(1)\n  EOF"), "EOF")
        self.assertEqual(open_heredoc("cat <<END\nno end"), "END")

    def test_shell_refuses_open_heredoc_fast(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        sh = Shell(d)
        rc, out, note = sh.run("python3 - <<'EOF'\nprint('x')\n  EOF", 5)
        self.assertEqual(rc, 2)
        self.assertIn("끝 표지", out)
        rc, out, _ = sh.run("echo ok", 5)            # 셸은 멀쩡하다
        self.assertEqual((rc, out.strip()), (0, "ok"))
        rc, out, _ = sh.run("python3\nprint('bare')", 5)
        self.assertIn("python3 - <<'EOF'", out)
        sh.close()

    def test_interp_hint(self):
        self.assertEqual(interp_hint("python3 -c 'print(1)'"), "")
        self.assertIn("heredoc", interp_hint("python3\nprint(1)"))


if __name__ == "__main__":
    unittest.main()
