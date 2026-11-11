"""판 85: 허브 할 일 목록이 걸음을 따라오게 — 목록은 도구가 도는 즉시 바뀐다(화면 길 확인), 진행 중 항목이 있는데 도구 4번 넘게
목록이 그대로면 한 번 끼우기, 사람에게 답하기 전 목록이 늦었거나 "단계 N 끝" 과 어긋나면 갱신 차례 한 번, 실행기가 대신 체크하지 않음,
STEWART_TODO_NUDGE=0 으로 끔, 마지막 갱신 시각(todos_at)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI


def r(content=None, calls=None):
    m = {"role": "assistant", "content": content}
    if calls:
        m["tool_calls"] = [{"id": f"c{os.urandom(3).hex()}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
                           for n, a in calls]
    return {"choices": [{"message": m, "finish_reason": "stop"}], "usage": {}}, 0.1


def todos(*st):
    return ("todo_write", {"items": [{"content": f"단계 {i + 1}", "status": s} for i, s in enumerate(st)]})


SH = ("run_shell", {"command": "true"})


def sh(i):   # 걸음마다 다른 명령(같은 호출 되풀이 막이에 걸리지 않게)
    return ("run_shell", {"command": f"echo {i}"})


class TTodo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def run_(self, seq, **env):
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["run_shell", "todo_write", "todo_read"],
                    workdir=self.tmp, max_steps=12)
        with mock.patch.dict(os.environ, env):
            s = C.ChatSession(p, Backend("f", "http://x/v1", "m", "q", 100000), Recorder(self.tmp / "runs", "c"), FakeUI(),
                              stream=False, mode="auto")
            self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
            self.addCleanup(lambda: C.T.close_env(s.env))
            seen, sent = [], []

            def f(backend, messages, tools=None, *a, **k):
                seen.append([t["status"] for t in (s.env.todos or [])])   # 허브 state()["todos"] 가 읽는 바로 그 목록
                sent.append(json.loads(json.dumps(messages)))
                return seq[min(len(sent) - 1, len(seq) - 1)]
            with mock.patch.object(C, "chat", side_effect=f):
                s.turn("단계별로 해 줘")
        return s, seen, sent

    def test_list_changes_mid_turn(self):   # (b) 가르기: 갱신을 부르면 차례 중간에 바로 바뀐다
        s, seen, _ = self.run_([r(calls=[todos("in_progress", "pending")]), r(calls=[SH]),
                                r(calls=[todos("done", "in_progress")]), r(calls=[SH]), r(calls=[todos("done", "done")]), r("끝")])
        self.assertEqual(seen[1], ["in_progress", "pending"])
        self.assertEqual(seen[3], ["done", "in_progress"])
        self.assertIsNotNone(s.env.todos_at)

    def test_stale_list_nudge(self):         # (a) 모델이 갱신을 안 부르면 도구 4번 뒤 한 번
        s, _, sent = self.run_([r(calls=[todos("in_progress", "pending")])] + [r(calls=[sh(i)]) for i in range(5)]
                               + [r(calls=[todos("done", "done")]), r("끝")])
        notes = [m["content"] for m in sent[-1] if m["role"] == "user" and "할 일 목록이 도구" in str(m["content"])]
        self.assertEqual(len(notes), 1)
        self.assertIn("'단계 1' 진행 중", notes[0])
        self.assertEqual([t["status"] for t in s.env.todos], ["done", "done"])   # 실행기는 대신 체크하지 않았다(모델이 고침)

    def test_before_answer_once(self):
        s, _, sent = self.run_([r(calls=[todos("in_progress", "pending")]), r(calls=[SH]),
                                r("단계 1 끝났습니다. 다음 진행할까요?"), r(calls=[todos("done", "pending")]), r("목록 고침")])
        msgs = sent[3]
        self.assertIn("사람에게 보이기 전에 할 일 목록", msgs[-1]["content"])
        self.assertIn("단계 1 를 끝냈다고", msgs[-1]["content"])
        self.assertEqual([t["status"] for t in s.env.todos], ["done", "pending"])
        self.assertEqual(len(sent), 5)                                            # 차례당 한 번만

    def test_no_nudge_when_list_current_or_off(self):
        _, _, sent = self.run_([r(calls=[todos("in_progress", "pending")]), r(calls=[SH]), r(calls=[todos("done", "in_progress")]),
                                r("단계 1 끝. 진행할까요?")])
        self.assertEqual(len(sent), 4)                                            # 목록이 맞으면 끼우지 않는다
        _, _, sent = self.run_([r(calls=[todos("in_progress", "pending")])] + [r(calls=[sh(i)]) for i in range(5)] + [r("끝")],
                               STEWART_TODO_NUDGE="0")
        self.assertFalse(any("할 일 목록" in str(m.get("content")) for m in sent[-1] if m["role"] == "user"))


if __name__ == "__main__":
    unittest.main()
