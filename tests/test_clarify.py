"""되묻기(ask_user): run 은 질문을 최종 출력으로 두고 asked 로 끝, chat 은 질문을 보이고 차례 끝. 정리·media 프로필엔 없음."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, clarify, engine, modes as M
from stewart.config import Backend, Profile, load_profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_engine import _resp

ASK = ("ask_user", json.dumps({"question": "요청은 km 기준인데 코드는 mile 을 쓴다. 어느 단위로 맞출까?",
                               "options": ["km", "mile"]}, ensure_ascii=False))


class TClarify(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a.py").write_text("x = 1\n")
        self.bk = Backend("fake", "http://x/v1", "m", "q", 4096)

    def prof(self, tools=("*",), **kw):
        return Profile(name="code", path=self.tmp / "p.toml", system="s", tools=list(tools), workdir=self.tmp, max_steps=10, **kw)

    def run_seq(self, seq, prof=None, **kw):
        rec = Recorder(self.tmp / "runs", "t")
        with mock.patch.object(engine, "chat", side_effect=seq) as m:
            s = engine.run(prof or self.prof(), self.bk, "단위를 맞춰라", rec, **kw)
        return s, m, rec

    def test_run_asks_and_stops(self):
        s, m, rec = self.run_seq([_resp(calls=[("read_file", '{"path": "a.py"}')]), _resp(calls=[ASK])])
        self.assertEqual(s["stop_reason"], "asked")
        self.assertEqual(m.call_count, 2)
        out = (rec.dir / "output.md").read_text()
        self.assertTrue(out.startswith("요청은 km 기준인데"))
        self.assertIn("- mile", out)
        self.assertEqual(s["asked"], out)
        self.assertEqual((self.tmp / "a.py").read_text(), "x = 1\n")
        self.assertFalse(s["asked_text"])
        msgs = json.loads((rec.dir / "messages.json").read_text())
        self.assertEqual(msgs[-1], {"role": "assistant", "content": out})
        ids = [c["id"] for x in msgs if x.get("tool_calls") for c in x["tool_calls"]]
        self.assertEqual(ids, [x["tool_call_id"] for x in msgs if x["role"] == "tool"])

    def test_ask_at_steps_left_note_not_in_question(self):
        seq = [_resp(calls=[("read_file", '{"path": "a.py"}')]) for _ in range(4)] + [_resp(calls=[ASK])]
        s, _, rec = self.run_seq(seq)   # max_steps 10: 5단계째가 '남은 단계 5' 자리
        self.assertEqual(s["stop_reason"], "asked")
        self.assertNotIn("[엔진]", (rec.dir / "output.md").read_text())

    def test_empty_question_is_error(self):
        s, _, _ = self.run_seq([_resp(calls=[("ask_user", '{"question": " "}')]), _resp("끝냈다.")])
        self.assertEqual((s["stop_reason"], s["tool_errors"]), ("completed", 1))

    def test_asked_text_signal(self):
        s, _, _ = self.run_seq([_resp("어느 파일을 고칠까요?")])
        self.assertEqual((s["stop_reason"], s["asked_text"]), ("completed", True))
        s, _, _ = self.run_seq([_resp(calls=[("write_file", '{"path": "b.py", "content": "y"}')]), _resp("b.py 를 만들었다. 더 할까?")])
        self.assertFalse(s["asked_text"])    # 파일을 바꿨으면 참고 신호도 아님

    def test_defs_where(self):
        names = lambda p: [d["function"]["name"] for d in engine.T.definitions([t for t in clarify.other_tools(p.tools) if t != "delegate"])] \
            + ([clarify.NAME] if clarify.wanted(p.tools, p.output_format) else [])
        self.assertIn("ask_user", names(load_profile("code")))
        self.assertIn("ask_user", names(load_profile("chat")))
        self.assertNotIn("ask_user", names(load_profile("organize")))
        self.assertNotIn("ask_user", names(load_profile("media")))
        self.assertFalse(clarify.wanted(["*"], "json"))
        self.assertEqual(M.decide("plan", "ask_user"), "run")
        # 낱낱이 적은 목록에 ask_user 를 넣어도 된다
        s, m, _ = self.run_seq([_resp(calls=[ASK])], prof=self.prof(tools=("read_file", "ask_user")))
        self.assertEqual(s["stop_reason"], "asked")
        self.assertEqual([d["function"]["name"] for d in m.call_args_list[0][0][2]], ["read_file", "ask_user"])

    def test_run_no_ask_after_change(self):
        seq = [_resp(calls=[("edit_file", '{"path": "a.py", "old": "x = 1", "new": "x = 2"}')]), _resp(calls=[ASK]),
               _resp("x 를 2 로 바꿨다. 시험 명령은 찾지 못했다.")]
        s, _, _ = self.run_seq(seq)
        self.assertEqual((s["stop_reason"], s["asked"]), ("completed", None))
        self.assertEqual((self.tmp / "a.py").read_text(), "x = 2\n")

    def test_conditional_closing_not_question(self):
        self.assertFalse(clarify.looks_like_question("LIMIT 를 20 으로 바꿨다. 추가로 필요한 것이 있으면 알려주세요."))
        self.assertFalse(clarify.looks_like_question("다 고쳤다. 필요하시면 말씀해 주세요."))

    def test_concrete_request_is_question(self):
        self.assertTrue(clarify.looks_like_question("단위를 km 와 mile 중 어느 쪽으로 할지 알려주세요."))

    def test_question_signal_forms(self):
        for t in ("어느 파일인가요?", "단위를 알려주세요.", "값을 명시해 주시겠어요? 알려주시면 진행하겠습니다.",
                  "어떤 값으로 바꿀지 정해 주세요\n"):
            self.assertTrue(clarify.looks_like_question(t), t)
        for t in ("a.py 를 고쳤다.", "시험이 모두 통과했다. 끝."):
            self.assertFalse(clarify.looks_like_question(t), t)

    def test_plan_mode_can_ask(self):
        s, _, _ = self.run_seq([_resp(calls=[ASK])], mode="plan")
        self.assertEqual(s["stop_reason"], "asked")

    def test_profiles_have_instructions(self):
        for name in ("code", "chat"):
            sysm = load_profile(name).system
            self.assertIn("ask_user", sysm)
            self.assertIn("한자", sysm)

    def test_chat_asks_then_answer_continues(self):
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.tmp, max_steps=10)
        ui = FakeUI()
        s = C.ChatSession(p, self.bk, Recorder(self.tmp / "runs", "chat-c"), ui, mode="edits")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        with mock.patch.object(C, "chat", side_effect=[_resp(calls=[ASK]), _resp("km 로 맞췄다.")]) as m:
            self.assertEqual(s.turn("단위를 맞춰"), "asked")
            self.assertIn("질문: 요청은 km", ui.out.getvalue())
            self.assertEqual(s.turn("km"), "answered")
            msgs = m.call_args_list[-1][0][1]
        self.assertEqual([x["role"] for x in msgs[-3:]], ["tool", "assistant", "user"])
        self.assertNotIn("차례 끝", ui.out.getvalue())


if __name__ == "__main__":
    unittest.main()
