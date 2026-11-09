"""chat 차례당 단계 상한: 닿으면 묻는 모드는 계속할지 묻고, auto 는 끝내고 안내. 반복 실패 중단. /think 덮어쓰기."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import backend as B, chat as C
from stewart.config import Backend, Profile, load_profile
from stewart.engine import REPEAT_STOP
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_engine import _resp


def _read():
    return _resp(calls=[("read_file", '{"path": "a.py"}')])


class TSteps(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a.py").write_text("x = 1\n")
        self.bk = Backend("fake", "http://x/v1", "m", "q", 4096,
                          params={"temperature": 0.2, "chat_template_kwargs": {"enable_thinking": False, "k": 1}})

    def sess(self, ui, mode="edits", steps=2, **kw):
        self.n = getattr(self, "n", 0) + 1
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.tmp, max_steps=steps)
        s = C.ChatSession(p, self.bk, Recorder(self.tmp / "runs", f"chat-s{self.n}"), ui, mode=mode, **kw)
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        return s

    def events(self, s, kind):
        return [json.loads(l) for l in (s.rec.dir / "events.jsonl").read_text().splitlines() if json.loads(l)["kind"] == kind]

    def test_chat_profile_default_200(self):
        self.assertEqual(load_profile("chat").max_steps, 200)
        self.assertEqual(load_profile("code").max_steps, 30)     # run(평가) 프로필은 그대로

    def test_limit_asks_and_continues(self):
        ui = FakeUI(answers=["y"])
        s = self.sess(ui)
        with mock.patch.object(C, "chat", side_effect=[_read(), _read(), _read(), _resp("끝.")]) as m:
            self.assertEqual(s.turn("읽어"), "answered")
        self.assertEqual(m.call_count, 4)          # 2단계 → 물음 → 2단계 더
        self.assertEqual(ui.answers, [])
        self.assertEqual([e["more"] for e in self.events(s, "steps_limit")], [True])

    def test_limit_asks_no_keeps_conversation(self):
        ui = FakeUI(answers=["n"])
        s = self.sess(ui)
        seq = [_read(), _read(), _resp("이어서 했다.")]
        with mock.patch.object(C, "chat", side_effect=seq) as m:
            self.assertEqual(s.turn("읽어"), "max_steps")
            self.assertIn("'계속'", ui.out.getvalue())
            self.assertEqual(s.turn("계속"), "answered")
            msgs = m.call_args_list[-1][0][1]
        # 끊긴 차례 뒤에도 툴콜·결과 짝이 맞고, 다음 사람 입력이 이어 붙는다
        ids = [c["id"] for x in msgs if x.get("tool_calls") for c in x["tool_calls"]]
        self.assertEqual(ids, [x["tool_call_id"] for x in msgs if x["role"] == "tool"])
        self.assertEqual((msgs[-1]["role"], msgs[-1]["content"]), ("user", "계속"))

    def test_limit_eof_is_no(self):
        s = self.sess(FakeUI())                    # 답이 없으면 EOFError → 그만
        with mock.patch.object(C, "chat", side_effect=[_read(), _read()]):
            self.assertEqual(s.turn("읽어"), "max_steps")

    def test_auto_does_not_ask(self):
        ui = FakeUI(answers=["y"])
        s = self.sess(ui, mode="auto")
        with mock.patch.object(C, "chat", side_effect=[_read(), _read()]):
            self.assertEqual(s.turn("읽어"), "max_steps")
        self.assertEqual(ui.answers, ["y"])        # 묻지 않았다
        self.assertEqual(self.events(s, "steps_limit")[0]["asked"], False)

    def test_repeated_failure_stops_and_resets_per_turn(self):
        bad = lambda: _resp(calls=[("read_file", '{"path": "없음.py"}')])
        s = self.sess(FakeUI(), mode="auto", steps=50)
        # 모두 실패면 연속 오류 한도(4)가 먼저 걸린다. 성공을 사이에 끼운 같은 실패 되풀이를 반복 감지가 잡는다
        seq = [x for _ in range(REPEAT_STOP) for x in (bad(), _read())][:-1]
        with mock.patch.object(C, "chat", side_effect=seq + [bad(), _resp("다른 방법.")]):
            self.assertEqual(s.turn("읽어"), "repeated_failure")
            self.assertEqual(s.st["steps"], 2 * REPEAT_STOP - 1)
            self.assertEqual(s.turn("다른 걸로"), "answered")   # 새 차례에서는 다시 센다

    def test_think_toggle_passed(self):
        s = self.sess(FakeUI(), mode="auto")
        with mock.patch.object(C, "chat", side_effect=[_resp("a"), _resp("b")]) as m:
            s.turn("하나")
            s.think = True
            s.turn("둘")
        self.assertNotIn("thinking", m.call_args_list[0].kwargs)
        self.assertIs(m.call_args_list[1].kwargs["thinking"], True)

    def test_thinking_kwargs_body(self):
        self.assertEqual(B.thinking_kwargs(self.bk, None), {})
        body = B._body(self.bk, [], **B.thinking_kwargs(self.bk, True))
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": True, "k": 1})
        self.assertEqual(self.bk.params["chat_template_kwargs"]["enable_thinking"], False)   # 백엔드 설정은 안 바뀜
        self.assertEqual(body["temperature"], 0.2)


if __name__ == "__main__":
    unittest.main()
