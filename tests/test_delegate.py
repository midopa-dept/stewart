"""서브에이전트(delegate): 새 문맥 작업자, 도구 범위(부모 ∩ kind ∩ 인자, 깊이 1, plan 은 읽기만), 체크포인트·웹 상한 공유,
작업자 단계 합 상한, 돌려주는 글, chat 의 "[작업자 N]" 확인 질문."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, delegate as DG, engine, tools as T
from stewart.checkpoint import Checkpoints
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_engine import _resp


def dcall(**a):
    return _resp(calls=[("delegate", json.dumps(a, ensure_ascii=False))])


class TDelegate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a.txt").write_text("원래\n")
        self.bk = Backend("fake", "http://x/v1", "m", "q", 4096)

    def prof(self, tools=("*",), steps=10, **kw):
        return Profile(name="code", path=self.tmp / "p.toml", system="부모 지시", tools=list(tools), workdir=self.tmp,
                       max_steps=steps, **kw)

    def go(self, seq, prof=None, **kw):
        rec = Recorder(self.tmp / "runs", "t")
        with mock.patch.object(engine, "chat", side_effect=seq) as m:
            s = engine.run(prof or self.prof(), self.bk, "부모 과제", rec, **kw)
        return s, m, rec

    def names(self, call):
        return [d["function"]["name"] for d in (call[0][2] or [])]

    def test_child_runs_fresh_and_returns(self):
        seq = [dcall(task="b.txt 에 ok 를 써라", tools=["write_file", "read_file"]),
               _resp(calls=[("write_file", '{"path": "b.txt", "content": "ok"}')]),
               _resp("b.txt 를 만들었다."),
               _resp("작업자가 b.txt 를 만들었다.")]
        s, m, rec = self.go(seq)
        self.assertEqual(s["stop_reason"], "completed")
        child_first = m.call_args_list[1][0][1]
        self.assertEqual([x["role"] for x in child_first[:2]], ["system", "user"])  # 부모 대화를 보지 않는다
        self.assertFalse(any("부모 과제" in (x.get("content") or "") for x in child_first))
        from stewart.envinfo import strip_user_note
        self.assertEqual(strip_user_note(child_first[1]["content"]), "b.txt 에 ok 를 써라")
        self.assertTrue(child_first[0]["content"].startswith(DG.WORKER_NOTE))
        self.assertIn("부모 지시", child_first[0]["content"])
        self.assertEqual(sorted(self.names(m.call_args_list[1])), ["read_file", "write_file"])
        self.assertIn("delegate", self.names(m.call_args_list[0]))
        tool_msg = [x for x in m.call_args_list[-1][0][1] if x["role"] == "tool"][0]["content"]
        self.assertTrue(tool_msg.startswith("[작업자 1: 종료 completed · 단계 2 · 바꾼 파일 b.txt"))
        self.assertIn("b.txt 를 만들었다.", tool_msg)
        d = s["delegations"][0]
        self.assertEqual((d["n"], d["steps"], d["files_written"]), (1, 2, ["b.txt"]))
        self.assertTrue(Path(d["dir"]).is_relative_to(rec.dir / "sub"))
        self.assertTrue((Path(d["dir"]) / "summary.json").exists())
        kinds = [json.loads(l)["kind"] for l in (rec.dir / "events.jsonl").read_text().splitlines()]
        self.assertIn("delegate_start", kinds)
        self.assertIn("delegate_end", kinds)
        # 체크포인트 공유: 부모 기록으로 되돌리면 작업자가 만든 b.txt 도 옮겨진다
        rep = Checkpoints(rec.dir, self.tmp).rewind(1)
        self.assertEqual([Path(x).name for x in rep["moved_aside"]], ["b.txt"])

    def test_depth_one_and_plan_reads_only(self):
        seq = [dcall(task="둘러봐"), _resp(calls=[("delegate", '{"task": "또"}')]), _resp("못 맡겼다"), _resp("끝")]
        s, m, _ = self.go(seq)
        self.assertNotIn("delegate", self.names(m.call_args_list[1]))               # 작업자에게는 delegate 가 없다
        seq = [dcall(task="계획용으로 읽어"), _resp("읽었다"), _resp("계획")]
        s, m, _ = self.go(seq, mode="plan")
        child = set(self.names(m.call_args_list[1]))
        self.assertTrue(child)
        self.assertTrue(child <= T_READ)
        self.assertNotIn("write_file", child)

    def test_tools_never_widen(self):
        seq = [dcall(task="x", tools=["write_file", "run_shell"]), _resp("끝"), _resp("끝")]
        s, m, _ = self.go(seq, prof=self.prof(tools=("read_file", "delegate")))
        self.assertEqual(self.names(m.call_args_list[1]), [])                       # 부모에 없는 도구는 못 준다
        seq = [dcall(task="x", kind="media"), _resp("끝"), _resp("끝")]
        s, m, _ = self.go(seq, prof=self.prof(tools=("read_file", "list_dir", "write_file", "delegate")))
        self.assertEqual(sorted(self.names(m.call_args_list[1])), ["list_dir", "read_file"])   # 부모 ∩ kind

    def test_budget_and_errors(self):
        seq = [dcall(task="첫째"), _resp(calls=[("read_file", '{"path": "a.txt"}')]),
               _resp(calls=[("read_file", '{"path": "a.txt"}')]), _resp(calls=[("read_file", '{"path": "a.txt"}')]),
               _resp(calls=[("read_file", '{"path": "a.txt"}')]),
               dcall(task="둘째"), dcall(task=" "), dcall(task="셋", kind="없는프로필"), _resp("끝")]
        # 부모 max_steps 2 → 작업자 합 4: 첫째가 4단계를 다 써서 둘째는 거부
        s, m, _ = self.go(seq, prof=self.prof(steps=2))
        msgs = [x["content"] for x in m.call_args_list[-1][0][1] if x["role"] == "tool"]
        self.assertTrue(msgs[0].startswith("[작업자 1: 종료 max_steps · 단계 4"))
        self.assertIn("단계 합 상한(4)", msgs[1])

    def test_errors(self):
        s, m, _ = self.go([dcall(task=" "), dcall(task="x", kind="없는프로필"), _resp("끝")])
        msgs = [x["content"] for x in m.call_args_list[-1][0][1] if x["role"] == "tool"]
        self.assertIn("task 가 비었다", msgs[0])
        self.assertIn("작업자를 만들 수 없다", msgs[1])

    def test_return_clipped(self):
        s, m, _ = self.go([dcall(task="길게"), _resp("가" * 9000), _resp("끝")])
        msg = [x["content"] for x in m.call_args_list[-1][0][1] if x["role"] == "tool"][0]
        self.assertIn(f"잘림: 전체 9000자 중 {DG.MAX_RETURN}자", msg)

    def test_web_limit_shared(self):
        pe = T.ToolEnv(workdir=self.tmp, web={"url": "http://127.0.0.1:9", "max_total": 2})
        pe.search_log.extend([{"query": "q", "urls": []}, {"query": "q2", "urls": []}])
        prof = self.prof(tools=("web_search",), web_search={"url": "http://127.0.0.1:9", "max_total": 2})
        rec = Recorder(self.tmp / "runs", "c")
        with mock.patch.object(engine, "chat", side_effect=[_resp(calls=[("web_search", '{"query": "x"}')]), _resp("끝")]) as m:
            engine.run(prof, self.bk, "찾아", rec, parent={"env": pe, "cps": Checkpoints(rec.dir, self.tmp)})
        msg = [x["content"] for x in m.call_args_list[-1][0][1] if x["role"] == "tool"][0]
        self.assertIn("2", msg)
        self.assertEqual(len(pe.search_log), 2)          # 부모 수를 그대로 쓴다(상한이라 늘지 않음)


T_READ = __import__("stewart.modes", fromlist=["READ_TOOLS"]).READ_TOOLS


class TDelegateChat(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bk = Backend("fake", "http://x/v1", "m", "q", 4096)

    def test_chat_child_asks_with_prefix(self):
        ui = FakeUI(answers=["n"])
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.tmp, max_steps=10)
        s = C.ChatSession(p, self.bk, Recorder(self.tmp / "runs", "chat-d"), ui, mode="edits")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        seq = [dcall(task="c.txt 를 만들고 ls 를 돌려라", tools=["write_file", "run_shell"]),   # chat 은 도구를 적어야 쓰기를 준다(U2-2)
               _resp(calls=[("write_file", '{"path": "c.txt", "content": "1"}'), ("run_shell", '{"command": "uname"}')]),
               _resp("c.txt 는 만들었고 ls 는 거부됐다."), _resp("작업자가 c.txt 를 만들었다.")]
        with mock.patch.object(engine, "chat", side_effect=seq[1:3]), mock.patch.object(C, "chat", side_effect=[seq[0], seq[3]]):
            self.assertEqual(s.turn("맡겨"), "answered")
        out = ui.out.getvalue()
        self.assertIn("[작업자 1] write_file", out)
        self.assertIn("[작업자 1] run_shell", out)
        self.assertEqual(ui.answers, [])                  # 셸에서만 물었다(edits)
        self.assertTrue((self.tmp / "c.txt").exists())
        self.assertIn(str(self.tmp / "c.txt"), s.summary()["files_changed"])
        self.assertEqual(s.summary()["delegations"][0]["files_written"], ["c.txt"])
        self.assertEqual(s.st["denied"], 1)
        rep = s.rewind()                                  # /undo 가 작업자 쓰기도 덮는다
        self.assertEqual([Path(x).name for x in rep["moved_aside"]], ["c.txt"])


class TReadDefault(unittest.TestCase):
    def test_chat_default_read_only(self):
        from stewart.delegate import Budget, child_profile
        p = Profile(name="chat", path=Path("/x/p.toml"), system="s", tools=["*"], workdir=Path("/x"), max_steps=10)
        tools = ["read_file", "write_file", "run_shell", "search", "list_dir"]
        prof, _ = child_profile(p, tools, "edits", {"task": "t"}, Budget(10), read_default=True)
        self.assertEqual(prof.tools, ["read_file", "search", "list_dir"])          # 읽기 도구만
        prof, _ = child_profile(p, tools, "edits", {"task": "t", "tools": ["write_file"]}, Budget(10), read_default=True)
        self.assertEqual(prof.tools, ["write_file"])                                 # 적으면 그대로
        prof, _ = child_profile(p, tools, "edits", {"task": "t"}, Budget(10))
        self.assertEqual(prof.tools, tools)                                          # run(평가 경로)은 그대로


if __name__ == "__main__":
    unittest.main()
