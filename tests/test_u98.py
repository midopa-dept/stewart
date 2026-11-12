"""판 121: 실사용 장면 묶음(tests/scenes/*.toml) — 가짜 모델로 실행기 장치(엔진 글·거절)가 장면마다 서는지, 판정 함수가 맞게 가르는지."""

import dataclasses
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, scenes as SC
from stewart.config import Backend, load_profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_u60 import resp


def session(scene, tmp):
    p = load_profile(scene["profile"])
    p = dataclasses.replace(p, workdir=tmp, raw=SC.profile_raw(scene, p.raw))
    s = C.ChatSession(p, Backend("b", "http://127.0.0.1:9/v1", "m", "q", 100000), Recorder(tmp / "runs", "s"), FakeUI(),
                      stream=False, mode="auto")
    return s


class TScenes(unittest.TestCase):
    def setUp(self):
        e = mock.patch.dict(os.environ, {"STEWART_MEMORY": "0", "STEWART_SEARXNG_URL": "http://127.0.0.1:9/search"})
        e.start()
        self.addCleanup(e.stop)

    def test_loaded(self):
        sc = SC.load()
        self.assertGreaterEqual(len(sc), 6)
        self.assertEqual(len({s["id"] for s in sc}), len(sc))
        for s in sc:
            self.assertTrue(s["said"] and s["source"], s["id"])

    def test_executor_devices(self):
        for sc in SC.load():
            ex = sc["executor"]
            if not ex:
                continue
            with self.subTest(sc["id"]):
                tmp = Path(tempfile.mkdtemp())
                s = session(sc, tmp)
                self.addCleanup(lambda s=s: s.rec._ev.closed or s.rec._ev.close())
                seq = []
                if ex.get("fake_call"):
                    seq.append(resp(calls=[(ex["fake_call"]["tool"], ex["fake_call"]["args"])]))
                seq.append(resp("끝"))
                with mock.patch.object(C, "chat", side_effect=seq):
                    s.turn(sc["said"])
                user = next(m for m in s.messages if m.get("role") == "user")
                if ex.get("note"):
                    self.assertIn(ex["note"], json.dumps(user["content"], ensure_ascii=False))
                if ex.get("blocked"):
                    tool = [m for m in s.messages if m.get("role") == "tool"][0]["content"]
                    self.assertIn(ex["blocked"], tool)


class TJudge(unittest.TestCase):
    def msgs(self, calls=None, text=""):
        m = {"role": "assistant", "content": text}
        if calls:
            m["tool_calls"] = [{"id": "c", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}} for n, a in calls]
        return [{"role": "system", "content": "s"}, {"role": "user", "content": "x"}, m]

    def test_judge(self):
        by = {s["id"]: s for s in SC.load()}
        self.assertTrue(SC.judge(by["lookup-unknown-name"], self.msgs([("web_search", {"query": "x"})]))[0])
        self.assertFalse(SC.judge(by["lookup-unknown-name"], self.msgs([("search", {"pattern": "fable"})]))[0])
        self.assertFalse(SC.judge(by["lookup-unknown-name"], self.msgs(text="기억으로 답함"))[0])
        self.assertTrue(SC.judge(by["web-named-but-off"], self.msgs(text="웹 검색이 꺼져 있어요. 칩을 켜 주세요"))[0])
        self.assertFalse(SC.judge(by["web-named-but-off"], self.msgs(text="llama.cpp 는 …"))[0])
        self.assertFalse(SC.judge(by["open-browser-on-this-computer"], self.msgs([("run_shell", {"command": "open https://www.naver.com"})]))[0])
        self.assertTrue(SC.judge(by["open-browser-on-this-computer"], self.msgs(text="이 컴퓨터의 브라우저는 열지 않습니다"))[0])


if __name__ == "__main__":
    unittest.main()
