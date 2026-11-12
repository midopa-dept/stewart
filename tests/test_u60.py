"""판 80: 웹 넛지 — 같은 오류 두 번째·처음 보는 이름 오류·최신 사실 물음에 실행기가 "웹으로 확인" 을 끼운다(상한·끄기)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, webnudge as WN
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI


def resp(content=None, calls=None):
    m = {"role": "assistant", "content": content}
    if calls:
        m["tool_calls"] = [{"id": f"c{i}{os.urandom(2).hex()}", "type": "function",
                            "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": m, "finish_reason": "stop"}], "usage": {}}, 0.1


class TNudger(unittest.TestCase):
    def test_rules_and_caps(self):
        n = WN.Nudger(search=True, fetch=True)
        self.assertIn("web_search", n.on_turn("pandas 최신 버전에서 바뀐 점은?"))
        self.assertEqual(n.on_turn("이 함수 이름을 바꿔 줘"), "")
        n.reset_turn()
        self.assertIn("처음 보는", n.on_error("", 0, "ModuleNotFoundError: No module named 'foo'"))
        self.assertEqual(n.on_error("TypeError", 1, "TypeError: x"), "")            # 첫 번째 같은 오류는 아직
        self.assertIn("두 번째", n.on_error("TypeError", 2, "TypeError: x"))
        self.assertEqual(n.on_error("ValueError", 2, "ValueError"), "")               # 차례당 2번 상한
        n.reset_turn()
        self.assertEqual(n.on_error("TypeError", 2, "TypeError: x"), "")              # 같은 꼴은 다시 안 함
        self.assertEqual(len(n.log), 3)
        f = WN.Nudger(search=False, fetch=True)
        self.assertIn("web_fetch", f.on_error("", 0, "error: unrecognized arguments: --foo"))
        self.assertEqual(f.on_error("", 0, "bash: foo: command not found"), "")      # 셸에 명령이 없는 것은 웹 넛지가 아니다(판 122)
        self.assertEqual(WN.Nudger(search=False, fetch=False).on_turn("최신 버전?"), "")   # 웹 도구가 없으면 안 함

    def test_recent_words(self):
        yes = ["uv 의 최신 버전이 뭐야?", "Node.js LTS 버전은 몇이야?", "요즘 많이 쓰는 파이썬 린터", "Claude API 가격 알려 줘",
               "Python 3.14 는 언제 나왔어?", "Python 3.14 에 새로 들어온 기능 세 가지를 알려 줘.", "what is the current version of numpy?"]
        no = ["현재 폴더의 파일 개수를 세 줘", "최근 커밋 보여 줘", "파이썬 버전을 3.12 로 바꿔 줘", "이 함수 이름을 바꿔 줘",
              "pyproject 의 version 을 1.2 로 올려 줘", "지금 몇 단계 남았어? 이 세션 기록 보고"]
        for t in yes:
            self.assertTrue(WN.recent_hit(t), t)
        for t in no:
            self.assertFalse(WN.recent_hit(t), t)

    def test_session_cap(self):
        n = WN.Nudger(search=True, fetch=True)
        for i in range(30):
            n.reset_turn()
            n.on_turn("최신 가격")
        self.assertEqual(n.session_n, WN.PER_SESSION)


class TChatNudge(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        e = mock.patch.dict(os.environ, {"STEWART_SEARXNG_URL": "http://127.0.0.1:9/", "STEWART_WEB_FETCH": "1"})
        e.start()
        self.addCleanup(e.stop)

    def sess(self, **env):
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["run_shell", "web_search", "web_fetch"],
                    workdir=self.tmp, max_steps=6)
        with mock.patch.dict(os.environ, env):
            s = C.ChatSession(p, Backend("f", "http://x/v1", "m", "q", 100000), Recorder(self.tmp / "runs", "c"), FakeUI(),
                              stream=False, mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        self.addCleanup(lambda: C.T.close_env(s.env))
        return s

    def go(self, s, text, seq):
        sent = []

        def f(backend, messages, tools=None, *a, **k):
            sent.append(json.loads(json.dumps(messages)))
            return seq[min(len(sent) - 1, len(seq) - 1)]
        with mock.patch.object(C, "chat", side_effect=f):
            s.turn(text)
        return sent

    def test_same_error_twice_nudges(self):
        s = self.sess()
        seq = [resp(calls=[("run_shell", {"command": "python3 -c 'import nosuchmod_x1'"})]),
               resp(calls=[("run_shell", {"command": "python3 -c 'import nosuchmod_x1 as m'"})]),
               resp("막혔다")]
        sent = self.go(s, "모듈을 불러 봐", seq)
        tools = [m for m in sent[-1] if m["role"] == "tool"]
        self.assertIn("[엔진]", tools[0]["content"])           # 처음 보는 이름 오류(ModuleNotFoundError) — 첫 번에
        self.assertIn("web_search", tools[0]["content"])
        self.assertIn("web_nudge", (s.rec.dir / "events.jsonl").read_text())

    def test_recent_question_nudges_and_can_be_off(self):
        s = self.sess()
        sent = self.go(s, "uv 의 최신 버전이 뭐야?", [resp("확인하겠다")])
        self.assertIn("기억으로 답하지 말고", sent[0][-1]["content"])
        s2 = self.sess(STEWART_WEB_NUDGE="0")
        sent = self.go(s2, "uv 의 최신 버전이 뭐야?", [resp("확인하겠다")])
        self.assertNotIn("기억으로 답하지 말고", sent[0][-1]["content"])


class TSkillDesc(unittest.TestCase):
    def test_web_research_description_names_triggers(self):
        from stewart import skills as SK
        s = {k.name: k for k in SK.discover(Path(tempfile.mkdtemp()), builtin=True)}["web-research"]
        self.assertLessEqual(len(s.description), SK.MAX_DESC)            # 목록에서 잘리지 않게
        for w in ("최신", "오류", "라이브러리"):
            self.assertIn(w, s.description)
        self.assertIn("web-research", SK.list_text(list(s for s in [s])))


if __name__ == "__main__":
    unittest.main()
