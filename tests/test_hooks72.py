"""hooks 최소판(SessionStart·PreToolUse·PostToolUse·Stop, exit 2 = 막음, 표준 입력 JSON)."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, engine, hooks as HK
from stewart.config import Backend, ConfigError, Profile, load_profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI

PY = sys.executable
# 훅 스크립트: 받은 JSON 을 log.jsonl 에 적고, 인자로 받은 꼴대로 끝난다.
#   block            → 표준 오류에 까닭, exit 2
#   block-once       → 첫 번째만 막고(표지 파일) 그 뒤 통과
#   say <글>          → 표준 출력에 글, exit 0
#   fail             → exit 1(막지 않는 오류)
HOOK = r'''
import json, os, sys
d = json.load(sys.stdin)
d["_env"] = os.environ.get("STEWART_HOOK_EVENT")
open("log.jsonl", "a").write(json.dumps(d, ensure_ascii=False) + "\n")
mode = sys.argv[1]
if mode == "block":
    print("정책: 이 명령은 안 된다", file=sys.stderr); sys.exit(2)
if mode == "block-once":
    if not os.path.exists(".once"):
        open(".once", "w").write("1"); print("README 를 먼저 고쳐라", file=sys.stderr); sys.exit(2)
    sys.exit(0)
if mode == "say":
    print(" ".join(sys.argv[2:])); sys.exit(0)
sys.exit(1)
'''

_N = [0]


def r(content=None, calls=None):
    msg = {"role": "assistant", "content": content}
    if calls:
        _N[0] += 1
        msg["tool_calls"] = [{"id": f"h{_N[0]}-{i}", "type": "function", "function": {"name": n, "arguments": a}}
                             for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": msg, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, 0.1


class TParse(unittest.TestCase):
    def test_parse_ok_and_bad(self):
        t = HK.parse({"Stop": "a", "PreToolUse": [{"command": "b", "matcher": "run_shell", "timeout_s": 5}]})
        self.assertEqual((t["Stop"][0].command, t["PreToolUse"][0].matcher, t["PreToolUse"][0].timeout_s), ("a", "run_shell", 5.0))
        for bad in ({"Nope": ["a"]}, {"Stop": [{"command": "a", "matcher": "x"}]}, {"PreToolUse": [{"command": "a", "matcher": "("}]},
                    {"Stop": [{"command": "a", "timeout_s": 0}]}, {"Stop": [{"cmd": "a"}]}, {"Stop": [""]}, "x"):
            with self.assertRaises(ValueError, msg=str(bad)):
                HK.parse(bad)

    def test_profile_config(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "h.toml").write_text('system = "s"\n[hooks]\nStop = ["echo hi"]\n', encoding="utf-8")
        self.assertEqual(load_profile(str(tmp / "h.toml")).hooks, {"Stop": ["echo hi"]})
        (tmp / "h.toml").write_text('system = "s"\n[hooks]\nAfter = ["x"]\n', encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_profile(str(tmp / "h.toml"))
        self.assertEqual(load_profile("code").hooks, {})                    # 내장 프로필은 hooks 없음(평가에 영향 없음)


class TEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        (self.tmp / "hook.py").write_text(HOOK, encoding="utf-8")
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        for k in (HK.ENV_HOOKS, HK.ENV_CHECK):
            os.environ.pop(k, None)

    def h(self, mode, **k):
        return {"command": f"{PY} hook.py {mode}", **k}

    def prof(self, hooks):
        return Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["write_file", "read_file", "run_shell"],
                       workdir=self.tmp, max_steps=8, hooks=hooks)

    def go(self, seq, hooks):
        sent = []

        def fake(backend, messages, *a, **k):
            sent.append(json.loads(json.dumps(messages)))
            return seq[len(sent) - 1]

        rec = Recorder(self.tmp / "runs", "t")
        with mock.patch.object(engine, "chat", side_effect=fake):
            s = engine.run(self.prof(hooks), Backend("fake", "http://x/v1", "m", "q", 100000), "일해", rec)
        log = [json.loads(l) for l in (self.tmp / "log.jsonl").read_text(encoding="utf-8").splitlines()] \
            if (self.tmp / "log.jsonl").exists() else []
        return s, sent, log, rec

    def test_pre_blocks_matched_tool_only(self):
        hooks = {"PreToolUse": [self.h("block", matcher="^run_shell$")]}
        seq = [r(calls=[("run_shell", json.dumps({"command": "touch made.txt"}))]),
               r(calls=[("write_file", json.dumps({"path": "a.txt", "content": "x"}))]), r("끝")]
        s, sent, log, _ = self.go(seq, hooks)
        self.assertFalse((self.tmp / "made.txt").exists())                   # 막힌 셸은 돌지 않았다
        self.assertTrue((self.tmp / "a.txt").exists())                       # matcher 밖 도구는 그대로
        tool_msg = [m for m in sent[1] if m["role"] == "tool"][-1]["content"]
        self.assertIn("hook(PreToolUse) 이 이 호출을 막았다: 정책: 이 명령은 안 된다", tool_msg)
        self.assertEqual(len(log), 1)
        self.assertEqual((log[0]["event"], log[0]["_env"], log[0]["tool_name"], log[0]["tool_input"]),
                         ("PreToolUse", "PreToolUse", "run_shell", {"command": "touch made.txt"}))
        self.assertEqual(s["hooks"]["runs"], 1)

    def test_post_note(self):
        hooks = {"PostToolUse": [self.h("block")]}
        s, sent, log, _ = self.go([r(calls=[("write_file", json.dumps({"path": "a.txt", "content": "x"}))]), r("끝")], hooks)
        tool_msg = [m for m in sent[1] if m["role"] == "tool"][-1]["content"]
        self.assertIn("[hook PostToolUse] 정책: 이 명령은 안 된다", tool_msg)
        self.assertTrue((self.tmp / "a.txt").exists())                       # Post 는 이미 돈 뒤
        self.assertEqual(log[0]["tool_response"]["ok"], True)

    def test_stop_block_once_then_finish(self):
        s, sent, log, _ = self.go([r("다 했다"), r("README 도 고쳤다")], {"Stop": [self.h("block-once")]})
        self.assertEqual((s["stop_reason"], s["hooks"]["stop_blocks"], len(sent)), ("completed", 1, 2))
        self.assertIn("[hook Stop] 끝내기 전 hook 이 막았다:\nREADME 를 먼저 고쳐라", sent[1][-1]["content"])
        self.assertEqual([(x["event"], x["final_output"]) for x in log], [("Stop", "다 했다"), ("Stop", "README 도 고쳤다")])

    def test_stop_cap(self):
        s, sent, _, _ = self.go([r("a"), r("b"), r("c")], {"Stop": [self.h("block")]})
        self.assertEqual((s["stop_reason"], s["hooks"]["stop_blocks"], len(sent)), ("completed", 2, 3))   # 2번 막은 뒤 받는다

    def test_session_start_context_and_nonblocking_error(self):
        hooks = {"SessionStart": [{"command": f"{PY} hook.py say 오늘은 배포 금지"}, self.h("fail")]}
        s, sent, log, _ = self.go([r("끝")], hooks)
        first_user = sent[0][-1]["content"]
        self.assertTrue(first_user.startswith("[hook SessionStart 출력]\n오늘은 배포 금지\n\n"))
        self.assertTrue(first_user.endswith("일해"))
        self.assertEqual([x["source"] for x in log], ["run", "run"])         # exit 1 은 막지 않고 기록만

    def test_env_off(self):
        os.environ[HK.ENV_HOOKS] = "0"
        s, sent, log, _ = self.go([r(calls=[("run_shell", json.dumps({"command": "touch made.txt"}))]), r("끝")],
                                  {"PreToolUse": [self.h("block")], "Stop": [self.h("block")]})
        self.assertTrue((self.tmp / "made.txt").exists())
        self.assertEqual((log, s["hooks"]), ([], {"runs": 0, "stop_blocks": 0, "off": True}))

    def test_chat_pre_and_stop(self):
        ui = FakeUI()
        sess = C.ChatSession(self.prof({"PreToolUse": [self.h("block", matcher="run_shell")], "Stop": [self.h("block-once")]}),
                             Backend("fake", "http://x/v1", "m", "q", 100000), Recorder(self.tmp / "runs", "chat-t"), ui,
                             stream=False, mode="auto")
        self.addCleanup(lambda: sess.rec._ev.closed or sess.rec._ev.close())
        seq = [r(calls=[("run_shell", json.dumps({"command": "touch made.txt"}))]), r("끝"), r("고쳤다")]
        with mock.patch.object(C, "chat", side_effect=seq):
            self.assertEqual(sess.turn("해"), "answered")
        self.assertFalse((self.tmp / "made.txt").exists())
        self.assertIn("hook 이 막음", ui.out.getvalue())
        self.assertIn("[hook Stop]", sess.messages[-2]["content"])
        self.assertEqual(sess.messages[-1], {"role": "assistant", "content": "고쳤다"})


if __name__ == "__main__":
    unittest.main()
