"""판 110(SW-43): 작업자 등급 — backends 표 [tiers.<이름>](backend·profile·budget·max·use·default_for), 모델에게는 tier 칸과 한 줄
쓰임새만, 안 고르면 실행기 규칙(읽기 → read 등급, 도구 적음 → write 등급), 등급별 동시 상한, 결과 머리에 등급·백엔드."""

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, engine, spawn as SP
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_u60 import resp

TABLE = ('default = "big"\n[backends.big]\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "big-m"\nmax_context = 100000\nconcurrency = 4\n'
         '[backends.small]\nbase_url = "http://127.0.0.1:8/v1"\nmodel = "small-m"\nmax_context = 32768\n'
         '[tiers.light]\nbackend = "small"\nprofile = "code-min"\nbudget = 12000\nmax = 1\nuse = "읽기·찾기·요약"\ndefault_for = ["read"]\n'
         '[tiers.normal]\nbackend = "big"\nmax = 2\nuse = "고치기"\ndefault_for = ["write"]\n')


class TTiers(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "b.toml").write_text(TABLE, encoding="utf-8")
        e = mock.patch.dict(os.environ, {"STEWART_BACKENDS": str(self.tmp / "b.toml")})
        e.start()
        self.addCleanup(e.stop)

    def test_load_pick_def(self):
        t = SP.load_tiers()
        self.assertEqual(list(t), ["light", "normal"])
        self.assertEqual(SP.pick_tier(t, {"task": "x"}), "light")
        self.assertEqual(SP.pick_tier(t, {"task": "x", "tools": ["edit_file"]}), "normal")
        self.assertEqual(SP.pick_tier(t, {"task": "x", "tier": "normal"}), "normal")
        d = SP.definition(t)["function"]["parameters"]["properties"]["tier"]
        self.assertEqual(d["enum"], ["light", "normal"])
        self.assertIn("light: 읽기·찾기·요약", d["description"])
        self.assertNotIn("small", json.dumps(SP.definition(t), ensure_ascii=False))     # 백엔드 이름은 모델에게 안 보인다
        self.assertEqual(SP.definition({}), SP.DEFINITION)

    def test_chat_light_worker_uses_small_backend(self):
        (self.tmp / "a.txt").write_text("에이", encoding="utf-8")
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["read_file", "list_dir", "delegate"], workdir=self.tmp, max_steps=8)
        s = C.ChatSession(p, Backend("big", "http://127.0.0.1:9/v1", "big-m", "q", 100000, concurrency=4), Recorder(self.tmp / "runs", "c"),
                          FakeUI(), stream=False, mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        seen = []

        def wchat(backend, *a, **k):
            seen.append((backend.name, backend.budget()))
            return resp("가볍게 읽었다")
        seq = [resp(calls=[("workers", {"action": "spawn", "task": "a.txt 를 읽어 요약"})]),
               resp(calls=[("workers", {"action": "wait"})]), resp("끝")]
        with mock.patch.object(C, "chat", side_effect=lambda *a, **k: seq.pop(0)), mock.patch.object(engine, "chat", side_effect=wchat):
            s.turn("작업자에게 맡겨")
        self.assertEqual(seen[0], ("small", 12000))                                    # 가벼운 등급 = 작은 백엔드·작은 예산
        self.assertIn("작업자 w1 · light]", json.dumps(s.messages, ensure_ascii=False))   # 결과 머리 꼬리표 "자리 · 등급"(판 115)
        self.assertEqual(s.pool.w["w1"]["tier"], "light")                               # 허브 작업자 띠 "작업자 w1 · light"
        self.assertIn("── 작업자 w1 · light (끝)", json.dumps(s.messages, ensure_ascii=False))   # 판 115.1: 모은 결과 머리도 같은 꼴
        self.assertEqual(s.pool.tier_sem["light"]._value, 1)


if __name__ == "__main__":
    unittest.main()
