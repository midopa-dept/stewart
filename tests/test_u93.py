"""판 116: 등급 — 모델 칸을 안 적은 등급은 기본 모델을 물려받음, 없는 등급은 거절·안내(대신 돌리지 않음), 작업자(엔진 길)도 등급 예산으로 압축,
등급 생각 칸(think·think_max_tokens·_first·reasoning_effort·_at·think_sampling), env 실험 손잡이, 생각 되보내기 current_turn.
등급 없이 도는 실행(평가 길)은 그대로."""

import argparse
import dataclasses
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import engine, spawn as SP, think as TH, toolrepair as TR
from stewart.config import Backend, ConfigError, Profile
from stewart.hubmain import main_tier

TABLE = ('default = "big"\n[backends.big]\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "big-m"\nmax_context = 100000\nconcurrency = 4\n'
         '[backends.side]\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "side-m"\nmax_context = 32768\nconcurrency = 3\n'
         '[tiers.light]\nslim = true\nbudget = 16000\nuse = "곧."\n'
         '[tiers.wide]\nbackend = "side"\nthink = true\nthink_max_tokens = 4096\nthink_max_tokens_first = 8192\n'
         'reasoning_effort = "medium"\nthink_sampling = { temperature = 0.6, bogus = 1 }\n')


def B(**kw):
    return Backend("big", "http://127.0.0.1:9/v1", "big-m", "q", 100000, **kw)


class TTiers(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "b.toml").write_text(TABLE, encoding="utf-8")
        e = mock.patch.dict(os.environ, {"STEWART_BACKENDS": str(self.tmp / "b.toml")})
        e.start()
        self.addCleanup(e.stop)

    def test_inherit_and_refuse(self):
        t = SP.load_tiers()
        self.assertEqual(list(t), ["light", "wide"])                         # backend·profile 없어도 등급
        self.assertIsNone(t["light"]["max"])
        p, b = main_tier(argparse.Namespace(tier="light", profile="chat", backend=None), str(self.tmp))
        self.assertEqual((b.name, b.budget()), ("big", 16000))                # 기본 모델 물려받기
        self.assertEqual(p.raw["tier"]["name"], "light")
        with self.assertRaises(ConfigError) as cm:
            main_tier(argparse.Namespace(tier="outside", profile="chat", backend=None), str(self.tmp))
        self.assertIn("있는 등급: light, wide", str(cm.exception))
        self.assertIn("대신하지 않는다", str(cm.exception))
        self.assertIn("모르는 등급 'outside'", SP.unknown_note(t, "outside"))
        self.assertEqual(SP.unknown_note(t, "light"), "")

    def test_tier_think_settings(self):
        t = SP.load_tiers()["wide"]
        prof = Profile(name="x", path=self.tmp / "p.toml", system="s", tools=["read_file"], workdir=self.tmp,
                       raw={"tier": SP.tier_raw(t)})
        s = TH.settings(prof, env={})
        self.assertEqual((s["max_tokens"], s["max_first"], s["effort"], s["effort_at"]), (4096, 8192, "medium", "body"))
        self.assertEqual(s["params"], {"temperature": 0.6})
        kw = TH.call_kwargs(B(params={"chat_template_kwargs": {"enable_thinking": True}}), s, None, 1000, first=True)
        self.assertEqual(kw["max_tokens"], 8192)
        self.assertEqual(kw["overrides"], {"reasoning_effort": "medium", "temperature": 0.6})
        self.assertEqual(TH.call_kwargs(B(params={"chat_template_kwargs": {"enable_thinking": True}}), s, None, 1000)["max_tokens"], 4096)
        s2 = TH.settings(prof, env={"STEWART_REASONING_EFFORT": "low", "STEWART_REASONING_EFFORT_AT": "template",
                                    "STEWART_THINK_MAX_TOKENS_FIRST": "0"})   # env 가 이긴다
        self.assertNotIn("max_first", s2)
        kw2 = TH.call_kwargs(B(params={"chat_template_kwargs": {"x": 1}}), s2, False, 1000)
        self.assertEqual(kw2, {})                                             # 생각을 끈 요청에는 노력도 안 싣는다
        kw3 = TH.call_kwargs(B(params={"chat_template_kwargs": {"x": 1}}), s2, None, 1000)
        self.assertEqual(kw3["overrides"]["chat_template_kwargs"], {"x": 1, "reasoning_effort": "low"})

    def test_no_tier_unchanged(self):
        prof = Profile(name="x", path=self.tmp / "p.toml", system="s", tools=["read_file"], workdir=self.tmp)
        s = TH.settings(prof, env={})
        self.assertEqual(s, {"max_tokens": None, "sampling": None, "params": {}, "source": {"max_tokens": None, "sampling": None},
                             "ignored": []})                                  # 판 115 와 같은 꼴
        self.assertEqual(TH.call_kwargs(B(params={"chat_template_kwargs": {"enable_thinking": True}}), s, None, 10, first=True), {})
        self.assertEqual(engine.compact_base(B(), prof), 100000)
        with mock.patch.dict(os.environ, {"STEWART_CONTEXT_BUDGET": "20000"}):
            self.assertEqual(engine.compact_base(B(), prof), 100000)          # run 길은 예전처럼 CONTEXT_BUDGET 을 안 본다
        tp = dataclasses.replace(prof, raw={"tier": {"name": "light", "budget": 16000}})
        self.assertEqual(engine.compact_base(B(), tp), 16000)                # 등급 작업자는 등급 예산

    def test_features_tier_only_when_tier(self):
        from stewart.record import Recorder
        from tests.test_u60 import resp
        prof = Profile(name="x", path=self.tmp / "p.toml", system="s", tools=["read_file"], workdir=self.tmp, max_steps=2)
        metas = []
        for p in (prof, dataclasses.replace(prof, raw={"tier": {"name": "light", "budget": 16000}})):
            rec = Recorder(self.tmp / "runs", "r")
            with mock.patch.object(engine, "chat", side_effect=[resp("끝")]):
                engine.run(p, B(), "읽어", rec)
            import json
            first = json.loads((rec.dir / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
            metas.append(first["meta"]["features"])
        self.assertNotIn("tier", metas[0])
        self.assertEqual(metas[1]["tier"], {"name": "light", "budget_source": "tier", "think": {}})

    def test_passback_current_turn(self):
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "a"},
                {"role": "assistant", "content": "", "reasoning_content": "옛 생각", "tool_calls": []},
                {"role": "user", "content": "b"},
                {"role": "assistant", "content": "", "reasoning_content": "지금 생각", "tool_calls": []}]
        out = TR.strip_old_turns(msgs)
        self.assertNotIn("reasoning_content", out[2])
        self.assertEqual(out[4]["reasoning_content"], "지금 생각")
        with mock.patch.dict(os.environ, {"STEWART_REASONING_PASSBACK": "current_turn"}):
            self.assertEqual(TR.passback(), "current_turn")
        self.assertTrue(TR.keep_reasoning("current_turn", "x", True))
        self.assertFalse(TR.keep_reasoning("current_turn", "x", False))


if __name__ == "__main__":
    unittest.main()
