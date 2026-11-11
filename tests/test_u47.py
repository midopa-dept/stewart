"""판 66 평가 손잡이(SW-18 압축 비교): STEWART_COMPACT_MODE = off|mask|mask+sum|sum|mask-roll, STEWART_COMPACT_WINDOW.
손잡이를 주지 않으면 평가 길(engine.run) 동작이 예전 그대로(문턱 0.7·글자 셈·요약 없음)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import engine
from stewart.compact import MASKED, mask_roll
from stewart.config import Backend, Profile
from stewart.record import Recorder


def run(env_over, prompt_tokens=10, steps=14):
    tmp = Path(tempfile.mkdtemp())
    for i in range(steps + 2):
        (tmp / f"a{i}.txt").write_text(f"{i}\n" + "가" * 3000, encoding="utf-8")
    prof = Profile(name="t", path=tmp / "p.toml", system="s", tools=["read_file"], workdir=tmp, max_steps=steps + 2)
    calls = {"agent": 0, "summary": 0}

    def fake(backend, messages, tools=None, **k):
        if tools is None:
            calls["summary"] += 1
            return {"choices": [{"message": {"role": "assistant", "content": "## 목표\n- 요약"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 5}}, 0.1
        calls["agent"] += 1
        n = calls["agent"]
        msg = {"role": "assistant", "content": None if n <= steps else "끝",
               **({"tool_calls": [{"id": f"c{n}", "type": "function", "function": {"name": "read_file",
                                                                                   "arguments": json.dumps({"path": f"a{n}.txt"})}}]}
                  if n <= steps else {})}
        return {"choices": [{"message": msg, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 5}}, 0.1
    env = {k: v for k, v in os.environ.items() if not k.startswith("STEWART_COMPACT")}
    env.update(env_over)
    with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(engine, "chat", fake):
        rec = Recorder(tmp / "runs", "t")
        engine.run(prof, Backend("fake", "http://x/v1", "m", "q", 10000), "읽어라", rec)
    evs = [json.loads(l) for l in (rec.dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    return evs, calls, rec.dir


def kinds(evs, k):
    return [e for e in evs if e.get("type", e.get("event")) == k or e.get("kind") == k]


class TModes(unittest.TestCase):
    def test_none_is_old(self):
        self.assertIsNone(engine.compact_mode({}))
        evs, calls, _ = run({}, prompt_tokens=9000)
        self.assertEqual(calls["summary"], 0)
        self.assertFalse([e for e in kinds(evs, "compact") if "mode" in e])
        self.assertFalse(kinds(evs, "summarize"))

    def test_parse(self):
        self.assertEqual(engine.compact_mode({"STEWART_COMPACT_MODE": "sum"})["mode"], "sum")
        m = engine.compact_mode({"STEWART_COMPACT_MODE": "xx", "STEWART_COMPACT_WINDOW": "40000"})
        self.assertEqual((m["mode"], m["window"], m["ignored"]), ("mask+sum", 40000, ["STEWART_COMPACT_MODE"]))

    def test_off(self):
        evs, calls, _ = run({"STEWART_COMPACT_MODE": "off", "STEWART_COMPACT_WINDOW": "2000"}, prompt_tokens=1900)
        self.assertEqual(calls["summary"], 0)
        self.assertFalse(kinds(evs, "compact"))

    def test_sum(self):
        evs, calls, d = run({"STEWART_COMPACT_MODE": "sum", "STEWART_COMPACT_WINDOW": "2000"}, prompt_tokens=1500)
        self.assertGreaterEqual(calls["summary"], 1)
        s = kinds(evs, "summarize")
        self.assertEqual((s[0]["mode"], s[0]["nth"]), ("sum", 1))
        self.assertTrue((d / "folds" / "001.json").is_file())

    def test_mask_roll(self):
        evs, calls, _ = run({"STEWART_COMPACT_MODE": "mask-roll"})
        c = [e for e in kinds(evs, "compact") if e.get("mode") == "mask-roll"]
        self.assertTrue(c)
        msgs = [{"role": "system"}, {"role": "user"}] + [m for i in range(12) for m in (
            {"role": "assistant", "tool_calls": [{"id": str(i)}]}, {"role": "tool", "content": "x" * 50})]
        self.assertEqual(mask_roll(msgs, 10), 2)
        self.assertTrue(msgs[3]["content"].startswith(MASKED))
        self.assertEqual(mask_roll(msgs, 10), 0)                    # 다시 고치지 않는다


if __name__ == "__main__":
    unittest.main()
