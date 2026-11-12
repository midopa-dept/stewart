"""판 99: 도구 켬·끔 손잡이(프로필 toggle_tools — 기본 끔·toggle_on 이면 켬, 꺼지면 모델 요청의 tools 에서 빠지고 불러도 실행 안 함,
켜면 다음 걸음부터 보임), 허브 상태 tool_toggles·웹 POST /api/tools/toggle."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests import test_hub_ui as HU
from tests.test_cloud import FakeUI
from tests.test_hub import msg
from tests.test_u60 import resp


def sess(raw):
    tmp = Path(tempfile.mkdtemp())
    p = Profile(name="chat", path=tmp / "p.toml", system="s", tools=["read_file", "list_dir"], workdir=tmp, max_steps=4, raw=raw)
    s = C.ChatSession(p, Backend("f", "http://x/v1", "m", "q", 100000), Recorder(tmp / "runs", "c"), FakeUI(), stream=False, mode="auto")
    return s


class TChat(unittest.TestCase):
    def test_toggle(self):
        s = sess({"toggle_tools": ["list_dir", "nope"]})
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        self.assertEqual(s.toggleable, ["list_dir"])                              # 프로필에 없는 이름은 뺀다
        self.assertNotIn("list_dir", s.tool_names)
        sent = []
        seq = [resp(calls=[("list_dir", {"path": "."})]), resp("끝")]
        with mock.patch.object(C, "chat", side_effect=lambda b, m, tools=None, *a, **k: (sent.append([d["function"]["name"] for d in tools or []]), seq.pop(0))[1]):
            s.turn("목록")
        self.assertNotIn("list_dir", sent[0])
        self.assertIn("꺼져 있다", json.dumps(s.messages, ensure_ascii=False))       # 불러도 실행 안 함
        self.assertTrue(s.set_tool("list_dir", True))
        self.assertIn("list_dir", s.tool_names)
        self.assertFalse(s.set_tool("read_file", False))                          # 손잡이 대상이 아님
        self.assertIn('"tool_toggle"', (s.rec.dir / "events.jsonl").read_text())
        on = sess({"toggle_tools": ["list_dir"], "toggle_on": ["list_dir"]})
        self.addCleanup(lambda: on.rec._ev.closed or on.rec._ev.close())
        self.assertIn("list_dir", on.tool_names)

    def test_profile_keys_load(self):
        from stewart.config import load_profile
        d = Path(tempfile.mkdtemp())
        (d / "t.toml").write_text('name = "t"\nsystem = "s"\ntools = ["read_file", "list_dir"]\ntoggle_tools = ["list_dir"]\ntoggle_on = []\n', encoding="utf-8")
        p = load_profile(str(d / "t.toml"))
        self.assertEqual(p.raw["toggle_tools"], ["list_dir"])


class TWeb(HU.TWebSessions):
    locals().update({k: None for k in dir(HU.TWebSessions) if k.startswith("test")})

    def test_state_and_post(self):
        st = self.main.state()
        self.assertNotIn("tool_toggles", st)
        s = self.main.sess
        s.toggleable, s.disabled_tools, s._all_defs = ["run_shell"], {"run_shell"}, list(s.tool_defs)
        s.tool_defs = [d for d in s._all_defs if d["function"]["name"] != "run_shell"]
        self.assertEqual(self.main.state()["tool_toggles"], [{"name": "run_shell", "on": False}])
        self.assertEqual(self.post("/api/tools/toggle", name="run_shell", on=True), (200, {"ok": True}))
        self.assertEqual(self.main.state()["tool_toggles"], [{"name": "run_shell", "on": True}])
        self.assertEqual(self.post("/api/tools/toggle", name="run_shell", on="yes")[0], 400)


if __name__ == "__main__":
    unittest.main()
