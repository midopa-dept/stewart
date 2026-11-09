"""끝내기 앞뒤 자기 점검 손잡이: review_on_stop·ask_check·baseline_diff(모두 기본 끔, env 0|1 이 이김)."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import engine, selfcheck as SC
from stewart.config import Backend, ConfigError, Profile, load_profile
from stewart.record import Recorder

PY = sys.executable
_N = [0]


def r(content=None, calls=None):
    msg = {"role": "assistant", "content": content}
    if calls:
        _N[0] += 1
        msg["tool_calls"] = [{"id": f"s{_N[0]}-{i}", "type": "function",
                              "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}} for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": msg, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, 0.1


def write(path, text):
    return r(calls=[("write_file", {"path": path, "content": text})])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.env = mock.patch.dict(os.environ, {"PYTHONDONTWRITEBYTECODE": "1"}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        for v in list(SC.ENVS.values()) + ["STEWART_CHECK_ON_STOP"]:
            os.environ.pop(v, None)

    def prof(self, **k):
        d = dict(name="t", path=self.tmp / "p.toml", system="s", tools=["read_file", "write_file", "run_shell", "ask_user"],
                 workdir=self.tmp, max_steps=12, verify_command=f"{PY} -m unittest -q", check_on_stop=True)
        return Profile(**{**d, **k})

    def go(self, seq, prof=None):
        sent = []

        def fake(backend, messages, *a, **k):
            sent.append(json.loads(json.dumps(messages)))
            return seq[len(sent) - 1]

        rec = Recorder(self.tmp / "runs", "t")
        with mock.patch.object(engine, "chat", side_effect=fake):
            s = engine.run(prof or self.prof(), Backend("fake", "http://x/v1", "m", "q", 100000), "f 를 고쳐라", rec)
        ev = [json.loads(l) for l in (rec.dir / "events.jsonl").read_text().splitlines()]
        return s, sent, ev


class TFlags(Base):
    def test_sources(self):
        p = self.prof()
        f = SC.flags(p, {})
        self.assertEqual([f[k] for k in SC.ENVS], [False, False, False, False])    # 기본 끔
        f = SC.flags(self.prof(review_on_stop=True), {"STEWART_REVIEW_ON_STOP": "0", "STEWART_ASK_CHECK": "1",
                                                       "STEWART_BASELINE_DIFF": "yes"})
        self.assertEqual((f["review_on_stop"], f["ask_check"], f["baseline_diff"]), (False, True, False))
        self.assertEqual(f["ignored"], ["STEWART_BASELINE_DIFF"])
        self.assertEqual(SC.ENVS, {"review_on_stop": "STEWART_REVIEW_ON_STOP", "ask_check": "STEWART_ASK_CHECK",
                                   "baseline_diff": "STEWART_BASELINE_DIFF", "todo_check": "STEWART_TODO_CHECK"})
        for name in ("code", "chat", "organize"):
            p = load_profile(name)
            self.assertEqual((p.review_on_stop, p.ask_check, p.baseline_diff, p.todo_check), (None, None, None, None))
        (self.tmp / "x.toml").write_text('system = "s"\nreview_on_stop = "yes"\n', encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_profile(str(self.tmp / "x.toml"))


class TReview(Base):
    def test_once_after_check_passes(self):
        os.environ["STEWART_REVIEW_ON_STOP"] = "1"
        s, sent, ev = self.go([write("m.py", "x = 1\n"), r("다 했다"), write("m2.py", "y = 2\n"), r("다시 확인했다"), r("안 닿음")])
        self.assertEqual(s["stop_reason"], "completed")
        self.assertEqual(len(sent), 4)                                              # 한 번만 되돌려 보냄
        self.assertEqual(sent[2][-1], {"role": "user", "content": SC.REVIEW})
        self.assertEqual(s["review"], {"ran": True, "at_step": 2, "steps_after": 2, "files_changed_after": 1})
        self.assertEqual([e["step"] for e in ev if e["kind"] == "review_on_stop"], [2])
        self.assertEqual(s["selfcheck"]["source"]["review_on_stop"], "env")

    def test_not_when_check_fails_or_no_change(self):
        os.environ["STEWART_REVIEW_ON_STOP"] = "1"
        (self.tmp / "test_m.py").write_text("import unittest\nfrom m import f\nclass T(unittest.TestCase):\n"
                                           "    def test(self):\n        self.assertEqual(f(), 2)\n")
        s, sent, _ = self.go([write("m.py", "def f():\n    return 1\n"), r("다 했다"),
                              r(calls=[("read_file", {"path": "m.py"})]), write("m.py", "def f():\n    return 2\n"),
                              r("고쳤다"), r("대조했다")])
        self.assertIn("끝내기 전에 확인 명령", sent[2][-1]["content"])                # 확인 실패가 먼저
        self.assertEqual(sent[5][-1]["content"], SC.REVIEW)                            # 확인 통과 뒤 대조
        self.assertEqual(s["review"]["at_step"], 5)
        s, sent, _ = self.go([r("읽기만 했다")])
        self.assertEqual((len(sent), s["review"]), (1, {"ran": False, "steps_after": None, "files_changed_after": None}))

    def test_default_off(self):
        s, sent, _ = self.go([write("m.py", "x = 1\n"), r("다 했다")])
        self.assertEqual(len(sent), 2)
        self.assertNotIn("review", s)
        self.assertNotIn("selfcheck", s)


class TAskCheck(Base):
    def setUp(self):
        super().setUp()
        os.environ["STEWART_ASK_CHECK"] = "1"
        (self.tmp / "a.txt").write_text("a")

    def test_hold_then_ask(self):
        s, sent, ev = self.go([write("m.py", "x\n"), r(calls=[("ask_user", {"question": "값 N 은 몇인가?"})])])
        self.assertFalse((self.tmp / "m.py").exists())                               # 붙잡힌 쓰기는 돌지 않았다
        self.assertEqual([m["content"] for m in sent[1] if m["role"] == "tool"][-1], SC.ASK_CHECK)
        self.assertEqual(s["stop_reason"], "asked")
        self.assertEqual(s["ask_check"], {"ran": True, "at_step": 1, "tool": "write_file", "asked": True, "proceeded": False})
        self.assertEqual([e["tool"] for e in ev if e["kind"] == "ask_check"], ["write_file"])

    def test_hold_then_proceed_once(self):
        s, sent, _ = self.go([r(calls=[("read_file", {"path": "a.txt"}), ("write_file", {"path": "m.py", "content": "1"}),
                                       ("write_file", {"path": "n.py", "content": "2"})]),
                              write("m.py", "1"), write("n.py", "2"), r("끝")])
        tools = [m["content"] for m in sent[1] if m["role"] == "tool"]
        self.assertIn("a", tools[0])                                                  # 앞의 읽기는 돌았다
        self.assertEqual(tools[1:], [SC.ASK_CHECK, SC.ASK_HOLD])                      # 같은 응답의 뒤 쓰기도 붙잡음
        self.assertTrue((self.tmp / "m.py").exists() and (self.tmp / "n.py").exists())   # 두 번째부터는 그냥 돈다
        self.assertEqual((s["ask_check"]["asked"], s["ask_check"]["proceeded"]), (False, True))
        self.assertEqual(s["tool_errors"], 0)

    def test_shell_forms_and_off_cases(self):
        s, sent, _ = self.go([r(calls=[("run_shell", {"command": "ls; cat a.txt"})]),
                              r(calls=[("run_shell", {"command": "echo hi > b.txt"})]), r("끝")])
        self.assertEqual(s["ask_check"]["tool"], "run_shell")
        self.assertEqual(s["ask_check"]["at_step"], 2)                                # 읽기 셸은 지나가고 쓰기 셸에서
        self.assertFalse((self.tmp / "b.txt").exists())
        s, sent, _ = self.go([write("m.py", "1"), r("끝")], prof=self.prof(tools=["write_file"]))
        self.assertFalse(s["ask_check"]["ran"])                                       # ask_user 가 없으면 안 건다
        os.environ["STEWART_ASK_CHECK"] = "0"
        s, sent, _ = self.go([write("z.py", "1"), r("끝")])
        self.assertNotIn("ask_check", s)
        self.assertTrue((self.tmp / "z.py").exists())


OLD = "import unittest\nclass Old(unittest.TestCase):\n    def test_old(self):\n        self.assertTrue(False)\n"
KEEP = "import unittest\nfrom m import g\nclass Keep(unittest.TestCase):\n    def test_keep(self):\n        self.assertEqual(g(), 7)\n"
M0 = "def g():\n    return 7\n"


class TBaseline(Base):
    def setUp(self):
        super().setUp()
        (self.tmp / "test_old.py").write_text(OLD)      # 처음부터 실패(과제와 상관없음)
        (self.tmp / "test_keep.py").write_text(KEEP)    # 처음에는 통과
        (self.tmp / "m.py").write_text(M0)

    def test_parse(self):
        out = ("FAIL: test_a (pkg.mod.T.test_a)\nERROR: test_b (pkg.mod.T)\nFAILED tests/t.py::test_c - x\n")
        self.assertEqual(SC.failing_tests(out, 1), ["pkg.mod.T.test_a", "pkg.mod.T.test_b", "tests/t.py::test_c"])
        self.assertEqual(SC.failing_tests("", 0), [])
        self.assertIsNone(SC.failing_tests("ImportError: boom", 1))                     # 이름을 못 읽으면 모름

    def test_only_old_failures_do_not_block(self):
        os.environ["STEWART_BASELINE_DIFF"] = "1"
        seq = [r(calls=[("read_file", {"path": "m.py"})]), write("m.py", M0 + "def h():\n    return 1\n"), r("끝")]
        s, sent, ev = self.go(seq)
        self.assertEqual((s["stop_reason"], len(sent)), ("completed", 3))               # 막지 않았다
        b = s["baseline"]
        self.assertEqual((b["base_failing"], b["new_failing"], b["still_failing"], b["unblocked"]), (1, 0, 1, 1))
        self.assertEqual([e["exit"] != 0 for e in ev if e["kind"] == "baseline"], [True])
        os.environ["STEWART_BASELINE_DIFF"] = "0"
        s, sent, _ = self.go([r(calls=[("read_file", {"path": "m.py"})]), write("m.py", M0 + "def h():\n    return 2\n"),
                              r("끝"), r("그대로 둔다"), r("정말 끝")])
        self.assertIn("끝내기 전에 확인 명령", sent[3][-1]["content"])                     # 끄면 전처럼 막는다
        self.assertNotIn("baseline", s)

    def test_new_failure_named(self):
        os.environ["STEWART_BASELINE_DIFF"] = "1"
        seq = [r(calls=[("read_file", {"path": "m.py"})]), write("m.py", "def g():\n    return 8\n"), r("끝"),
               r(calls=[("read_file", {"path": "m.py"})]), write("m.py", M0), r("고쳤다")]
        s, sent, _ = self.go(seq)
        fb = sent[3][-1]["content"]
        self.assertIn("새로 깨진 시험: test_keep.Keep.test_keep", fb)
        self.assertIn("처음부터 실패하던 시험(과제와 상관없으면 그대로 둔다): test_old.Old.test_old", fb)
        self.assertEqual((s["stop_reason"], s["baseline"]["new_failing"], s["baseline"]["unblocked"]), ("completed", 0, 1))


def todos(*items):
    return r(calls=[("todo_write", {"items": [{"content": c, "status": st} for c, st in items]})])


class TTodo(Base):
    def prof(self, **k):
        return super().prof(**{"tools": ["read_file", "write_file", "todo_write"], "check_on_stop": None, **k})

    def test_final_nudge_once(self):
        os.environ["STEWART_TODO_CHECK"] = "1"
        s, sent, ev = self.go([todos(("시험 고치기", "in_progress"), ("문서 고치기", "pending")), r("다 했다"),
                               todos(("시험 고치기", "done"), ("문서 고치기", "pending")), r("문서는 하지 않았다"), r("안 닿음")])
        self.assertIn("끝나지 않은 항목이 2개", sent[2][-1]["content"])
        self.assertEqual((s["stop_reason"], len(sent)), ("completed", 4))           # 두 번째 최종 답은 그대로(상한 1)
        tc = s["todo_check"]
        self.assertEqual((tc["final_nudged"], tc["at_step"], tc["pending_at_nudge"], tc["pending_at_end"], tc["used_todos"]),
                         (True, 2, 2, 1, True))
        self.assertEqual([e["pending"] for e in ev if e["kind"] == "todo_check"], [2])

    def test_no_todos_or_all_done(self):
        os.environ["STEWART_TODO_CHECK"] = "1"
        s, sent, _ = self.go([r("끝")])
        self.assertEqual((len(sent), s["todo_check"]["final_nudged"], s["todo_check"]["used_todos"]), (1, False, False))
        s, sent, _ = self.go([todos(("a", "done")), r("끝")])
        self.assertEqual((len(sent), s["todo_check"]["final_nudged"]), (2, False))

    def test_remind_every_n(self):
        os.environ["STEWART_TODO_CHECK"] = "1"
        (self.tmp / "a.txt").write_text("a")
        reads = [r(calls=[("read_file", {"path": "a.txt"})]) for _ in range(7)]
        s, sent, ev = self.go([todos(("일", "pending"))] + reads + [todos(("일", "done")), r("끝")],
                              prof=self.prof(todo_remind_every=3, max_steps=12))
        steps = [e["step"] for e in ev if e["kind"] == "todo_remind"]
        self.assertEqual(steps, [4, 7])                                                  # todo_write(1단계) 뒤 3단계마다
        tool_msgs = [m["content"] for m in sent[4] if m["role"] == "tool"]
        self.assertIn("할 일 목록 상기: 남은 것 1개", tool_msgs[-1])
        self.assertEqual(s["todo_check"]["reminds"], 2)

    def test_default_off_and_config(self):
        s, sent, _ = self.go([todos(("일", "pending")), r("끝")])
        self.assertEqual(len(sent), 2)
        self.assertNotIn("todo_check", s)
        (self.tmp / "x.toml").write_text('system = "s"\ntodo_remind_every = 1\n', encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_profile(str(self.tmp / "x.toml"))
        self.assertEqual(load_profile("code").todo_remind_every, 8)


if __name__ == "__main__":
    unittest.main()
