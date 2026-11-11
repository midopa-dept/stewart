"""U2-3: 말만 하고 멈춤 되돌리기, write_file append, 지난 메인 대화 목록·다시 열기."""

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, intent, tools as T
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_hub import msg
from tests.test_hub3 import MainBase


def resp(content=None, calls=None):
    m = {"role": "assistant", "content": content}
    if calls:
        m["tool_calls"] = [{"id": f"c{i}{time.monotonic_ns()}", "type": "function",
                            "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}} for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": m, "finish_reason": "stop"}], "usage": {}}, 0.1


class TIntent(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        (self.tmp / "a.txt").write_text("A")
        self.sent = []

    def sess(self):
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["read_file", "list_dir"], workdir=self.tmp,
                    max_steps=10)
        s = C.ChatSession(p, Backend("f", "http://x/v1", "m", "q", 100000), Recorder(self.tmp / "runs", "c"), FakeUI(),
                          stream=False, mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        return s

    def go(self, s, text, seq):
        def f(backend, messages, tools=None, *a, **k):
            self.sent.append(json.loads(json.dumps(messages)))
            return seq[len(self.sent) - 1]
        with mock.patch.object(C, "chat", side_effect=f):
            return s.turn(text)

    def test_nudge_once_then_acts(self):
        s = self.sess()
        r = self.go(s, "a.txt 를 읽고 요약하라.", [resp("먼저 `read_file` 로 a.txt 를 읽겠습니다."),
                                              resp(calls=[("read_file", {"path": "a.txt"})]), resp("요약: A 한 글자다.")])
        self.assertEqual(r, "answered")
        self.assertIn("말로만 적고 도구를 부르지 않은", self.sent[1][-1]["content"])
        self.assertEqual(s.st["intent_nudges"], 1)

    def test_only_once_per_turn(self):
        with mock.patch.dict(os.environ, {"STEWART_REFLECT": "0"}):   # 되짚기 끔: 되돌리기는 차례에 한 번만
            s = self.sess()
            r = self.go(s, "a.txt 를 읽고 요약하라.", [resp("읽겠습니다."), resp("이제 정말 읽겠습니다.")])
        self.assertEqual((r, len(self.sent)), ("answered", 2))

    def test_second_intent_only_reflects(self):
        """U2-8: 되돌린 뒤에도 또 말만 하고 멈추면 되짚기(도구 없이) 한 번 → 이어 간다."""
        with mock.patch.dict(os.environ, {"STEWART_REFLECT": "1"}):
            s = self.sess()
            r = self.go(s, "a.txt 를 읽고 요약하라.", [resp("읽겠습니다."), resp("이제 정말 읽겠습니다."),
                                                    resp("1) 말만 했다.\n금지: 없음\n다음: read_file a.txt"), resp("요약: 메모다.")])
        self.assertEqual((r, len(self.sent)), ("answered", 4))

    def test_no_nudge_for_question_or_done(self):
        for text, answer in (("이 파일이 무엇인지 설명해 줘", "이 파일은 메모입니다. 원하시면 더 살펴보겠습니다."),
                             ("어떻게 고치면 될까?", "이렇게 고치면 됩니다. 말씀하시면 고치겠습니다."),
                             ("고마워", "천만에요.")):
            self.sent = []
            s = self.sess()
            self.go(s, text, [resp(answer)])
            self.assertEqual(len(self.sent), 1, text)

    def test_off(self):
        with mock.patch.dict(os.environ, {"STEWART_INTENT_NUDGE": "0"}):
            s = self.sess()
            self.go(s, "a.txt 를 읽고 요약하라.", [resp("읽겠습니다.")])
        self.assertEqual(len(self.sent), 1)

    def test_rules(self):
        A = "`session_spawn` 으로 세 작업자를 띄우겠습니다.\n\n먼저 `list_dir` 로 확인하겠습니다."
        self.assertTrue(intent.intent_only("세 폴더의 버그를 각각 고치고 시험을 확인하라.", A, 0))
        self.assertFalse(intent.intent_only("세 폴더의 버그를 각각 고치고 시험을 확인하라.", A, 1))
        self.assertFalse(intent.intent_only("고쳐라", "고쳤습니다. 다음엔 시험을 돌리겠습니다.", 0))


class TAppend(unittest.TestCase):
    def test_append(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        env = T.ToolEnv(workdir=d, shell_timeout_s=5)
        self.assertTrue(T.write_file(env, "a.py", "x = 1\n")[0])
        ok, r = T.write_file(env, "a.py", "y = 2\n", append=True)
        self.assertTrue(ok, r)
        self.assertEqual((d / "a.py").read_text(), "x = 1\ny = 2\n")
        self.assertIn("이어 썼다", r)
        (d / "b.py").write_text("old\n")
        self.assertFalse(T.write_file(env, "b.py", "z\n", append=True)[0])      # 읽지 않은 파일에는 이어 쓰지 않는다
        self.assertEqual((d / "b.py").read_text(), "old\n")
        defs = {x["function"]["name"]: x for x in T.definitions(["write_file"])}
        self.assertIn("append", defs["write_file"]["function"]["parameters"]["properties"])


class TPastMains(MainBase):
    def test_list_and_reopen(self):
        self.r.scripts["MAIN"] = [msg("첫 대화 답")]
        self.main.send("첫 대화: 기억할 낱말은 귤")
        self.assertTrue(self.main.wait_idle())
        old_dir = str(self.main.sess.rec.dir)
        # 허브를 다시 띄운 것처럼 새 메인(새로 시작)을 만든다
        from stewart import hub as H
        from stewart.cli import resolve
        from stewart.hubmain import HubMain
        self.main.close()
        profile, backend = resolve(str(self.tmp / "main.toml"), "fake", str(self.wd))
        self.hub = H.Hub(self.tmp / "hub2", self.tmp / "runs")
        time.sleep(1.1)   # 기록 폴더 이름(초 단위)이 겹치지 않게
        self.main = HubMain(self.hub, profile, backend, Recorder(self.tmp / "runs", "hub-main"), mode="edits")
        self.addCleanup(self.main.close)
        past = self.main.past_mains()
        self.assertEqual([p["run_dir"] for p in past], [old_dir])
        self.assertIn("기억할 낱말은 귤", past[0]["first"])
        with self.assertRaises(ValueError):
            self.main.reopen_main(str(self.tmp))                           # 목록 밖은 거절
        self.main.reopen_main(old_dir)
        self.r.scripts["MAIN"] = [msg("귤이다")]
        self.main.send("아까 낱말이 뭐였지?")
        self.assertTrue(self.main.wait_idle())
        self.assertIn("기억할 낱말은 귤", json.dumps(self.r.requests["MAIN"][-1]["messages"], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
