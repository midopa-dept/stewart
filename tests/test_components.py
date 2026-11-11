"""구성 요소 사건 기록: 기록만 하고 모델에 가는 메시지·종료 사유·단계 수는 바꾸지 않는다. detail 에는 글·경로 원문이 없다."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import components as CM, engine, tools as T
from stewart.backend import BackendError
from stewart.config import Backend, Profile
from stewart.record import Recorder

PY = sys.executable
CHECK = f"{PY} -c \"import pathlib,sys; sys.exit(0 if pathlib.Path('ok.txt').exists() else 1)\""


def r(content=None, calls=None, finish="stop"):
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [{"id": f"k{i}-{n}", "type": "function",
                              "function": {"name": n, "arguments": a if isinstance(a, str) else json.dumps(a)}}
                             for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50,
                      "completion_tokens_details": {"reasoning_tokens": 40}}}, 0.1


def overflow():
    return BackendError("HTTP 400", 400, json.dumps({"object": "error", "message":
        "This model's maximum context length is 65536 tokens. However, you requested 70000 tokens."}))


# 여러 구성 요소가 한 번씩 움직이는 대본
SCRIPT = [
    r(calls=[("read_file", '{"path": "a.txt"')]),                                     # broken_toolcall(args_json)
    r(calls=[("write_file", {"path": "a.txt", "content": "x"})]),                     # write_denied(unread_overwrite)
    r(calls=[("edit_file", {"path": "b.txt", "old": "hello   \nworld", "new": "hi\nworld"})]),   # edit_match(fuzzy_rstrip)
    r(calls=[("run_shell", {"command": f"{PY} -c \"print('x' * 30000)\""})]),         # output_clip(head_tail)
    r(calls=[("read_file", {"path": "nope.txt"})]),
    r(calls=[("read_file", {"path": "nope.txt"})]),
    r(calls=[("read_file", {"path": "nope.txt"})]),                                    # repeat_nudge(same_fail)
    overflow(),                                                                         # overflow_retry → compact
    r("", finish="length"),                                                             # length_retry
    r("다 했다."),                                                                       # check_on_stop 실패 → 모델로
    r(calls=[("write_file", {"path": "ok.txt", "content": "ok"})]),
    r("I am done."),                                                                    # check_on_stop 통과, lang_retry
    r("다 고쳤다."),                                                                     # 끝(확인은 상한 2번을 다 씀)
]


# 훅: Pre·Post 는 늘 막고(exit 2), Stop 은 처음 한 번만 막는다
HOOK = r"""
import os, sys
mode = sys.argv[1]
if mode == "block":
    print("막음", file=sys.stderr); sys.exit(2)
if mode == "block-once" and not os.path.exists(".once"):
    open(".once", "w").write("1"); print("한 번 더", file=sys.stderr); sys.exit(2)
sys.exit(0)
"""
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def todos(*items):
    return r(calls=[("todo_write", {"items": [{"content": c, "status": st} for c, st in items]})])


# 나머지 손잡이: todo_remind·steps_left_nudge·hook_block(Pre·Post·Stop)·todo_check·format_force
SCRIPT2 = [
    todos(("일", "pending")),
    r(calls=[("run_shell", {"command": "echo hi"})]),                                  # hook_block(pre_tool_use)
    r(calls=[("write_file", {"path": "x.txt", "content": "x"})]),                      # hook_block(post), steps_left, todo_remind
    r("다 했다"),                                                                        # todo_check(open) → 모델로
    todos(("일", "done")),
    r("이제 끝"),                                                                        # todo_check(pass), hook_block(stop)
    r("JSON 아님"),                                                                      # format_force
    r('{"ok": true}'),                                                                  # 끝
]


class TSameMessages(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.base, ignore_errors=True))
        self.wd = self.base / "wd"
        e = mock.patch.dict(os.environ, {"PYTHONDONTWRITEBYTECODE": "1"})
        e.start()
        self.addCleanup(e.stop)
        os.environ.pop("STEWART_CHECK_ON_STOP", None)

    def go(self, on: str, script=None, envs=None, **pk):
        script = SCRIPT if script is None else script
        shutil.rmtree(self.wd, ignore_errors=True)    # 두 번 모두 같은 경로·같은 처음 상태
        self.wd.mkdir()
        (self.wd / "a.txt").write_text("old\n")
        (self.wd / "b.txt").write_text("hello\nworld\n")
        (self.wd / "hook.py").write_text(HOOK)
        sent = []

        def fake(backend, messages, *a, **k):
            sent.append(json.dumps({"messages": messages, "tools": a[0] if a else None, "kw": k},
                                   ensure_ascii=False, sort_keys=True))
            x = script[len(sent) - 1]
            if isinstance(x, Exception):
                raise x
            return x

        base = dict(name="t", path=self.wd / "p.toml", system="s",
                    tools=["read_file", "write_file", "edit_file", "run_shell"], workdir=self.wd, max_steps=30,
                    check_on_stop=CHECK, language="ko")
        prof = Profile(**{**base, **pk})
        rec = Recorder(self.base / f"runs-{on}", "t")
        with mock.patch.dict(os.environ, {CM.ENV: on, **(envs or {})}), mock.patch.object(engine, "chat", side_effect=fake):
            s = engine.run(prof, Backend("fake", "http://x/v1", "m", "q", 100000), "고쳐라", rec)
        ev = [json.loads(l) for l in (rec.dir / "events.jsonl").read_text().splitlines()]
        return s, sent, ev, rec.dir / "final.md"

    def test_on_off_same(self):
        s1, sent1, ev1, _ = self.go("1")
        s0, sent0, ev0, _ = self.go("0")
        self.assertEqual(len(sent1), len(SCRIPT))
        self.assertEqual(sent1, sent0)                                    # 모델에 간 요청이 바이트까지 같다
        for k in ("stop_reason", "steps", "tool_calls", "tool_errors", "denied", "repeat_nudges", "language_retries",
                  "final_output", "success"):
            self.assertEqual(s1.get(k), s0.get(k), k)
        self.assertEqual(s1["stop_reason"], "completed")
        self.assertNotIn("components", s0)
        self.assertFalse([e for e in ev0 if e["kind"] == "component"])
        comp = [e for e in ev1 if e["kind"] == "component"]
        names = {e["name"] for e in comp}
        for n in ("broken_toolcall", "write_denied", "edit_match", "output_clip", "overflow_retry", "compact",
                  "repeat_nudge", "length_retry", "lang_retry", "check_on_stop", "stop_decision"):
            self.assertIn(n, names, n)
        by = {e["name"]: e for e in comp}
        self.assertEqual(by["write_denied"]["detail"], {"reason": "unread_overwrite", "tool": "write_file"})
        self.assertEqual(by["edit_match"]["detail"]["strategy"], "fuzzy_rstrip")
        self.assertEqual(by["output_clip"]["detail"]["mode"], "head_tail")
        self.assertEqual(by["output_clip"]["detail"]["tool"], "run_shell")
        self.assertGreater(by["output_clip"]["detail"]["orig_chars"], 30000)
        self.assertEqual(by["length_retry"]["detail"]["recovered"], True)
        self.assertEqual(by["length_retry"]["detail"]["think_tokens"], 40)
        chk = [e for e in comp if e["name"] == "check_on_stop"]
        self.assertEqual([(e["detail"]["decision"], e["action"]) for e in chk], [("fail", "return_to_model"), ("pass", "stop")])
        self.assertEqual(comp[-1]["name"], "stop_decision")
        self.assertEqual(comp[-1]["detail"]["final_reason"], "completed")
        self.assertEqual(comp[-1]["detail"]["last_component"], "lang_retry")
        self.assertEqual(comp[-1]["detail"]["steps"], s1["steps"])
        self.assertEqual(s1["components"]["check_on_stop"], {"fired": 2, "actions": {"return_to_model": 1, "stop": 1}})
        self.assertEqual(sum(v["fired"] for v in s1["components"].values()), len(comp))
        for e in comp:
            self.assertIn(e["action"], CM.ACTIONS)
            for k, v in e["detail"].items():                              # 숫자·참거짓·None·짧은 코드 글만
                self.assertTrue(v is None or isinstance(v, (bool, int, float))
                                or (isinstance(v, str) and CM._CODE.match(v)), (e["name"], k, v))
            blob = json.dumps(e["detail"], ensure_ascii=False)
            self.assertNotIn(str(self.wd), blob)
            self.assertNotIn("/", blob)
            self.assertNotIn("nope", blob)
            self.assertNotIn("I am done", blob)

    def test_failure_stop(self):
        bad = [r(calls=[("read_file", {"path": "nope.txt"})]) for _ in range(6)]
        s, _, ev, _ = self.go("1", script=bad, max_consecutive_errors=10)
        self.assertEqual(s["stop_reason"], "repeated_failure")
        fs = [e for e in ev if e["kind"] == "component" and e["name"] == "failure_stop"]
        self.assertEqual([(e["detail"]["reason"], e["detail"]["last_k"]) for e in fs], [("repeated_failure", 6)])


    def go2(self, on):
        py = f"{PY} hook.py"
        return self.go(on, script=SCRIPT2, envs={"STEWART_TODO_CHECK": "1"},
                       tools=["read_file", "write_file", "run_shell", "todo_write"], max_steps=8, check_on_stop=None,
                       language=None, todo_remind_every=2, output_format="json", output_schema=SCHEMA,
                       hooks={"PreToolUse": [{"command": f"{py} block", "matcher": "run_shell"}],
                              "PostToolUse": [{"command": f"{py} block", "matcher": "write_file"}],
                              "Stop": [f"{py} block-once"]})

    def test_rest_on_off_same(self):
        s1, sent1, ev1, _ = self.go2("1")
        s0, sent0, ev0, _ = self.go2("0")
        self.assertEqual(len(sent1), len(SCRIPT2))
        self.assertEqual(sent1, sent0)
        for k in ("stop_reason", "steps", "tool_calls", "tool_errors", "format_failures", "final_output", "success"):
            self.assertEqual(s1.get(k), s0.get(k), k)
        self.assertEqual(s1["stop_reason"], "completed")
        self.assertFalse([e for e in ev0 if e["kind"] == "component"])
        comp = [e for e in ev1 if e["kind"] == "component"]
        got = [(e["name"], e["action"], e["detail"].get("hook_event") or e["detail"].get("decision")) for e in comp
               if e["name"] in ("hook_block", "todo_check", "todo_remind", "steps_left_nudge", "format_force")]
        self.assertEqual(got, [
            ("hook_block", "block", "pre_tool_use"),
            ("hook_block", "return_to_model", "post_tool_use"),
            ("steps_left_nudge", "nudge", None),
            ("todo_remind", "nudge", None),
            ("todo_check", "return_to_model", "open"),
            ("todo_check", "stop", "pass"),
            ("hook_block", "return_to_model", "stop"),
            ("todo_check", "stop", "pass"),
            ("format_force", "retry", None),
            ("todo_check", "stop", "pass"),
        ])
        by = {e["name"]: e["detail"] for e in comp}
        self.assertEqual(by["steps_left_nudge"], {"left": 5})
        self.assertEqual(by["todo_remind"], {"open_n": 1})
        self.assertEqual(by["format_force"], {"attempt": 1, "errors": 1})
        self.assertEqual(by["hook_block"], {"hook_event": "stop", "exit": 2})
        self.assertEqual(comp[-1]["name"], "stop_decision")
        self.assertEqual(s1["components"]["hook_block"]["fired"], 3)
        for e in comp:
            self.assertIn(e["action"], CM.ACTIONS)
            for v in e["detail"].values():
                self.assertTrue(v is None or isinstance(v, (bool, int, float)) or (isinstance(v, str) and CM._CODE.match(v)))
            blob = json.dumps(e["detail"], ensure_ascii=False)
            for bad in (str(self.wd), "/", "막음", "JSON 아님", "echo"):
                self.assertNotIn(bad, blob)


class TClean(unittest.TestCase):
    def test_drops_text_and_paths(self):
        d = CM.clean({"n": 3, "ok": True, "x": None, "code": "head_tail", "path": "/etc/passwd", "text": "다 했다",
                      "long": "a" * 41, "obj": {"a": 1}, "Bad Key": 1})
        self.assertEqual(d, {"n": 3, "ok": True, "x": None, "code": "head_tail", "_dropped": 5})

    def test_note_outside_execute_is_noop(self):
        CM.note("output_clip", "max_chars", "clip", orig_chars=1)          # 걸린 목록이 없으면 아무 일도 없다
        env = T.ToolEnv(Path(tempfile.mkdtemp()))
        self.addCleanup(lambda: shutil.rmtree(env.workdir, ignore_errors=True))
        (env.workdir / "big.txt").write_text("y" * (T.MAX_OUT + 10))
        ok, _ = T.execute(env, "read_file", {"path": "big.txt"})
        self.assertTrue(ok)
        self.assertIsNone(CM.NOTES.get())                                    # execute 가 끝나면 풀린다

    def test_unknown_action(self):
        c = CM.Components(mock.Mock(), on=True)
        with self.assertRaises(ValueError):
            c.fire("x", 1, "t", "explode")


if __name__ == "__main__":
    unittest.main()
