"""판 149: 맡기기 장면 실측 도구(tools/scenes/delegate_measure.py) — 판정·시험 저장소·칸(허브 역할·넛지)·앞 대화를 가짜 모델로 본다."""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, engine as E
from stewart.config import Backend
from tests.test_u60 import resp

ROOT = Path(__file__).resolve().parents[1]


def tool():
    spec = importlib.util.spec_from_file_location("dg_measure_149", ROOT / "tools" / "scenes" / "delegate_measure.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def asst(*calls):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(calls)]}


class TJudge(unittest.TestCase):
    def setUp(self):
        self.M = tool()

    def test_should_and_quality(self):
        sc = {"id": "x", "kind": "should"}
        msgs = [{"role": "user", "content": "x"}, asst(("read_file", {"path": "a"})), {"role": "tool", "content": "r"},
                asst(("workers", {"action": "spawn", "task": "src/ledgerly/handlers 아래 파일에서 load_config 쓰임을 표로 정리해 돌려라"}))]
        r = self.M.judge(sc, msgs, nudges=1, limit=12)
        self.assertEqual((r["ok"], r["first_step"], r["scope"], r["ret"], r["nudge"]), (True, 2, True, True, 1))
        self.assertFalse(self.M.judge(sc, msgs, 0, limit=1)["ok"])                                  # 상한 밖이면 안 센다
        self.assertFalse(self.M.judge(sc, msgs[:3], 0, 12)["ok"])

    def test_should_not_false_call(self):
        sc = {"id": "y", "kind": "should_not"}
        msgs = [asst(("delegate", {"task": "고쳐라"}))]
        r = self.M.judge(sc, msgs, 0, 12)
        self.assertEqual((r["ok"], r["n_calls"], r["scope"]), (False, 1, False))
        self.assertTrue(self.M.judge(sc, [asst(("edit_file", {"path": "a"}))], 0, 12)["ok"])
        self.assertTrue(self.M.judge(sc, [asst(("workers", {"action": "wait"}))], 0, 12)["ok"])   # wait 은 맡기기가 아니다

    def test_scenes_and_fixture(self):
        sc = self.M.load()
        self.assertEqual(sorted({s["kind"] for s in sc}), ["should", "should_not"])
        self.assertGreaterEqual(min(sum(1 for s in sc if s["kind"] == k) for k in ("should", "should_not")), 6)
        self.assertTrue(any(s.get("history") for s in sc))
        d = self.M.make_repo(Path(tempfile.mkdtemp()))
        files = [p for p in d.rglob("*") if p.is_file()]
        self.assertGreaterEqual(len(files), 40)
        self.assertGreaterEqual(sum(1 for p in files if "load_config" in p.read_text(encoding="utf-8")), 20)


class TOne(unittest.TestCase):
    """칸·앞 대화·실제 작업자(위임)·넛지 사건 세기를 가짜 모델로."""

    def setUp(self):
        self.M = tool()
        e = mock.patch.dict(os.environ, {"STEWART_SEARXNG_URL": "http://127.0.0.1:9/search", "STEWART_SEARXNG_MIN_INTERVAL": "0"})
        e.start()
        self.addCleanup(e.stop)
        self.b = Backend("b", "http://127.0.0.1:9/v1", "m", "q", 100000)

    def run_with(self, scene, cell, script, dry=False):
        seen = []
        self.tools_seen = []

        def fake(backend, messages, tools=None, *a, **k):
            seen.append(json.loads(json.dumps(messages)))
            self.tools_seen.append([t["function"]["name"] for t in tools or []])
            return script(messages)
        with mock.patch.object(C, "chat", side_effect=fake), mock.patch.object(E, "chat", side_effect=fake):
            r = self.M.one(scene, self.b, cell, 12, dry)
        return r, seen

    def test_delegate_runs_child_and_counts(self):
        def script(ms):
            if str(ms[0]["content"]).startswith("너는 부모 에이전트가 맡긴"):
                return resp("handlers 22개 가운데 load_config 21곳")
            n = sum(1 for m in ms if m.get("role") == "tool")
            if n == 0:
                return resp(calls=[("delegate", {"task": "src/ledgerly 아래에서 load_config 쓰임을 모두 찾아 파일별로 정리해 돌려라"})])
            return resp("정리했다")
        r, seen = self.run_with({"id": "dg", "kind": "should", "said": "load_config 쓰임 다 정리"}, "hub-nudge", script)
        self.assertEqual((r["ok"], r["first_step"], r["scope"], r["ret"]), (True, 1, True, True), r)
        self.assertTrue(any(str(m[0]["content"]).startswith("너는 부모 에이전트가 맡긴") for m in seen))   # 작업자가 실제로 돌았다
        from stewart.hubmain import HUB_MAIN_NOTE
        self.assertIn(HUB_MAIN_NOTE.split("\n")[0], seen[0][0]["content"])                        # 허브 칸 = 역할 글

    def test_chat_cell_history_and_nudge(self):
        def script(ms):
            n = sum(1 for m in ms if m.get("role") == "tool")
            return resp(calls=[("read_file", {"path": f"src/ledgerly/handlers/h{n + 1:02d}.py"})]) if n < 10 else resp("다 읽었다")
        sc = {"id": "dgn", "kind": "should_not", "said": "방금 그 함수 고쳐", "history": [
            {"role": "user", "content": "parse_amount 어디 있어?"}, {"role": "assistant", "content": "money.py 에 있다"}]}
        r, seen = self.run_with(sc, "chat-nudge", script)
        from stewart.hubmain import HUB_MAIN_NOTE
        self.assertNotIn(HUB_MAIN_NOTE.split("\n")[0], seen[0][0]["content"])                     # chat 칸 = 역할 글 없음
        self.assertIn("parse_amount 어디 있어?", json.dumps(seen[0], ensure_ascii=False))          # 앞 대화가 실렸다
        self.assertTrue(r["ok"])
        self.assertGreaterEqual(r["nudge"], 1)                                                      # 읽기 8번 넘어 넛지가 떴다
        r2, _ = self.run_with(sc, "chat-nonudge", script)
        self.assertEqual(r2["nudge"], 0)


    def test_hub_cell_is_hub_main_form(self):
        """판 151: hub 칸 = `chat --once --hub-main` 과 같은 길 — 허브 도구 정의가 실리고, 부르면 거절 글·hub_refused 로 센다."""
        from stewart.hubmain import HUB_ABSENT

        def script(ms):
            if not any(m.get("role") == "tool" for m in ms):
                return resp(calls=[("session_spawn", {"name": "w", "task": "src 아래 정리"})])
            return resp("끝")
        r, seen = self.run_with({"id": "h", "kind": "should_not", "said": "README 첫 줄"}, "hub-nonudge", script)
        self.assertIn("session_spawn", self.tools_seen[0])
        self.assertIn("board", self.tools_seen[0])
        self.assertIn(HUB_ABSENT, json.dumps(seen[-1], ensure_ascii=False))
        self.assertEqual(r["hub_refused"], 1)
        self.run_with({"id": "c", "kind": "should_not", "said": "README 첫 줄"}, "chat-nonudge", lambda ms: resp("끝"))
        self.assertNotIn("session_spawn", self.tools_seen[0])                                      # chat 칸은 허브 도구 없음

    def test_dry(self):
        """판 151 --dry: 읽기만 실제로, 쓰기·셸은 실행 안 함, delegate 는 작업자를 띄우지 않고 부름·맡긴 글만 기록."""
        def script(ms):
            if str(ms[0]["content"]).startswith("너는 부모 에이전트가 맡긴"):
                return resp("작업자가 돌았다")
            n = sum(1 for m in ms if m.get("role") == "tool")
            if n == 0:
                return resp(calls=[("read_file", {"path": "README.md"}), ("write_file", {"path": "x.txt", "content": "x"}),
                                   ("bash", {"command": "touch y.txt"}),
                                   ("delegate", {"task": "src/ledgerly 아래 load_config 쓰임을 파일별로 정리해 돌려라"})])
            return resp("끝")
        r, seen = self.run_with({"id": "dg", "kind": "should", "said": "정리"}, "chat-nonudge", script, dry=True)
        self.assertFalse(any(str(m[0]["content"]).startswith("너는 부모 에이전트가 맡긴") for m in seen))   # 작업자 안 띄움
        self.assertEqual((r["ok"], r["first_step"], r["scope"], r["ret"]), (True, 1, True, True), r)    # 판정은 같다
        tool_msgs = [str(m.get("content")) for m in seen[-1] if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 4)
        self.assertIn("Ledgerly", tool_msgs[0])                                                      # 읽기는 실제로
        self.assertTrue(all(self.M.DRY_RESULT in t for t in tool_msgs[1:]), tool_msgs)


if __name__ == "__main__":
    unittest.main()
