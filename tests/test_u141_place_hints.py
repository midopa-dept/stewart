"""판 141: 자리 정체성 줄·도구 설명 덧붙임(STEWART_PLACE_HINTS, 기본 켬) · 장면 판정의 보류·둘째 걸음·되물음 글.

- 코드 자리: 지시문 첫 줄 아래에 "바깥 것은 web_search 부터"·"이 컴퓨터의 앱·브라우저를 열지 않는다" 두 줄(그 도구가 있을 때만).
- parlando: "이름만 던진 말도 모르는 이름이면 web_search" 한 줄.
- 도구 설명: list_dir·glob·run_shell 끝에 한 구절(실사용 chat 길만). STEWART_PLACE_HINTS=0 이면 모두 예전 그대로.
- 장면 판정: 첫 걸음이 폴더만이면 보류 — 둘째 걸음까지 웹이면 맞음. say_any 는 ask_user 의 물음 글도 본다.
"""

import dataclasses
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, scenes as SC
from stewart.config import Backend, load_profile
from stewart.record import Recorder
from tests.test_u60 import resp

ROOT = Path(__file__).resolve().parents[1]


def session(prof, env, tmp):
    with mock.patch.dict(os.environ, env):
        p = dataclasses.replace(load_profile(prof), workdir=tmp)
        return C.ChatSession(p, Backend("b", "http://127.0.0.1:9/v1", "m", "q", 100000), Recorder(tmp / "r", "s"),
                             C.UI(out=io.StringIO(), color=False), stream=False, mode="auto")


class TPlaceLines(unittest.TestCase):
    ENV = {"STEWART_MEMORY": "0", "STEWART_SEARXNG_URL": "http://127.0.0.1:9/search", "STEWART_WEB_FETCH": "1"}

    def sysdesc(self, prof, on):
        tmp = Path(tempfile.mkdtemp())
        s = session(prof, {**self.ENV, "STEWART_PLACE_HINTS": "1" if on else "0"}, tmp)
        self.addCleanup(s.close)
        return s.messages[0]["content"], {d["function"]["name"]: d["function"]["description"] for d in s.tool_defs}, s.meta

    def test_code_place(self):
        sysm, desc, meta = self.sysdesc("chat", True)
        lines = sysm.split("\n")
        self.assertEqual(lines[1], C.CODE_PLACE_WEB)                                             # 정체성 줄 바로 아래
        self.assertEqual(lines[2], C.CODE_PLACE_SCREEN)
        self.assertIn("작업 폴더 안만", desc["list_dir"])
        self.assertIn("앱·브라우저를 열지 않는다", desc["run_shell"])
        self.assertIs(meta["place_hints"], True)

    def test_off_is_as_before(self):
        sysm, desc, meta = self.sysdesc("chat", False)
        self.assertNotIn(C.CODE_PLACE_WEB, sysm)
        self.assertEqual(sysm.split("\n")[0], load_profile("chat").system.strip().split("\n")[0])
        self.assertNotIn("작업 폴더 안만", desc["list_dir"])
        self.assertIs(meta["place_hints"], False)

    def test_talk_place(self):
        sysm, desc, _ = self.sysdesc("talk", True)
        self.assertEqual(sysm.split("\n")[1], C.TALK_PLACE_WEB)
        self.assertNotIn(C.CODE_PLACE_SCREEN, sysm)                                              # parlando 에는 셸이 없다

    def test_lines_follow_tools(self):
        self.assertEqual(C.with_place_lines("머리\n본문", "code", []), "머리\n본문")                # 웹·셸이 없으면 넣지 않는다
        self.assertEqual(C.with_place_lines("머리", "code", ["run_shell"]), "머리\n" + C.CODE_PLACE_SCREEN)


def msgs(*steps, text=""):
    out = [{"role": "system", "content": "s"}, {"role": "user", "content": "x"}]
    for calls in steps:
        out.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": "c", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}} for n, a in calls]})
        out.append({"role": "tool", "content": "r"})
    if text:
        out.append({"role": "assistant", "content": text})
    return out


class TJudge(unittest.TestCase):
    def setUp(self):
        self.by = {s["id"]: s for s in SC.load()}

    def test_hold_then_second_step(self):
        sc = self.by["dev-bare-name"]
        self.assertEqual(SC.judge(sc, msgs([("web_search", {})]))[0], True)
        ok, why = SC.judge(sc, msgs([("list_dir", {})], [("web_search", {})]))
        self.assertTrue(ok)
        self.assertTrue(why.startswith("보류"), why)
        ok, why = SC.judge(sc, msgs([("list_dir", {})], [("read_file", {})]))
        self.assertFalse(ok)
        self.assertTrue(why.startswith("보류"), why)
        self.assertFalse(SC.judge(sc, msgs([("run_shell", {})]))[0])                           # 셸은 보류가 아니다

    def test_worth_shell_with_web(self):
        sc = self.by["dev-worth-it"]
        self.assertTrue(SC.judge(sc, msgs([("run_shell", {}), ("web_search", {})]))[0])         # 셸로 버전을 함께 보는 것은 됨
        self.assertFalse(SC.judge(sc, msgs([("run_shell", {})]))[0])                           # 셸만은 틀림

    def test_ask_user_question_counts(self):
        sc = self.by["web-named-but-off"]
        self.assertTrue(SC.judge(sc, msgs([("ask_user", {"question": "웹 검색이 꺼져 있어요. 칩을 켜 줄까요?"})]))[0])
        self.assertFalse(SC.judge(sc, msgs([("ask_user", {"question": "어느 판을 찾을까요?"})]))[0])


class TMeasureHubRole(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("scene_measure_141", ROOT / "tools" / "scenes" / "measure.py")
        self.M = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.M)
        e = mock.patch.dict(os.environ, TPlaceLines.ENV)
        e.start()
        self.addCleanup(e.stop)
        self.b = Backend("b", "http://127.0.0.1:9/v1", "m", "q", 100000)

    def test_hub_role_and_live_read_tools(self):
        from stewart.hubmain import HUB_MAIN_NOTE
        sc = {s["id"]: s for s in SC.load()}["dev-bare-name"]
        seen = []

        def fake(backend, messages, tools=None, *a, **k):
            seen.append(json.loads(json.dumps(messages)))
            return resp(calls=[("list_dir", {"path": "."})]) if len(seen) == 1 else resp(calls=[("web_search", {"query": "x"})])
        with mock.patch.object(C, "chat", side_effect=fake):
            ok, why, first = self.M.one(sc, self.b, 1, run_tools=False)
        self.assertTrue(ok, why)
        self.assertTrue(why.startswith("보류"))
        self.assertIn(HUB_MAIN_NOTE.split("\n")[0], seen[0][0]["content"])                       # 허브 꼴(역할 글)
        tool = [m for m in seen[1] if m.get("role") == "tool"][-1]["content"]
        self.assertNotIn("측정 중이라", tool)                                                     # 읽기 도구는 실제로 돌았다(빈 폴더)


if __name__ == "__main__":
    unittest.main()
