"""판 108(SW-43 ②): 동시 작업자 workers(spawn·wait·collect·cancel·list) — 띄우고 바로 돌아옴, 동시 상한(줄 서기), 걸음 사이 끝 알림,
답으로 끝내려는데 도는 작업자가 있으면 한 번 "wait 로 모아라", [멈춤]은 작업자도 취소, STEWART_SPAWN=0 이면 도구 없음."""

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, engine, spawn as SP
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_u60 import resp


class TPool(unittest.TestCase):
    def test_pool(self):
        gate = threading.Event()
        seen = []

        def run(args, wid):
            seen.append(wid)
            gate.wait(5)
            return True, f"{wid} 결과: {args['task']}"
        p = SP.Pool(run, limit=1)
        a = p.spawn({"task": "가"})
        b = p.spawn({"task": "나"})
        time.sleep(0.2)
        self.assertEqual((a["status"], b["status"]), ("running", "queued"))           # 상한 1 — 줄 선다
        self.assertTrue(p.cancel("w2"))
        self.assertEqual(p.w["w2"]["status"], "cancelled")
        self.assertEqual(p.collect(), [p.w["w2"]])                                    # 취소된 것도 거둔다(결과 없음)
        gate.set()
        got, still = p.wait(timeout_s=5)
        self.assertEqual(([r["id"] for r in got], still), (["w1"], []))
        self.assertIn("w1 결과: 가", SP.results_text(got))
        self.assertEqual(seen, ["w1"])                                                  # 취소된 w2 는 돌지 않았다
        self.assertEqual(p.fresh_done(), [])                                           # 이미 거둔 것은 알리지 않음
        h = SP.Pool(lambda a, w: (time.sleep(2), (True, ""))[1], 1, halted=lambda: True)
        h.spawn({"task": "x"})
        with self.assertRaises(KeyboardInterrupt):                                     # [멈춤]은 wait 를 끊는다
            h.wait(timeout_s=5)

    def test_limit(self):
        self.assertEqual(SP.max_workers(Backend("b", "u", "m", "-", 1000, concurrency=1)), 1)
        self.assertEqual(SP.max_workers(Backend("b", "u", "m", "-", 1000, concurrency=8)), 4)
        with mock.patch.dict(os.environ, {"STEWART_SPAWN_MAX": "2"}):
            self.assertEqual(SP.max_workers(Backend("b", "u", "m", "-", 1000, concurrency=1)), 2)


class TChat(unittest.TestCase):
    def sess(self, **env):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "a.txt").write_text("에이", encoding="utf-8")
        p = Profile(name="chat", path=tmp / "p.toml", system="s", tools=["read_file", "delegate"], workdir=tmp, max_steps=10)
        with mock.patch.dict(os.environ, env):
            s = C.ChatSession(p, Backend("f", "http://x/v1", "m", "q", 100000, concurrency=3), Recorder(tmp / "runs", "c"), FakeUI(),
                              stream=False, mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        return s

    def test_spawn_guard_wait(self):
        s = self.sess()
        self.assertIn("workers", s.tool_names)
        seq = [resp(calls=[("workers", {"action": "spawn", "task": "b.txt 가 있는지 보고 알려 줘"})]),
               resp(calls=[("read_file", {"path": "a.txt"})]),
               resp("a.txt 는 에이다"),                                                   # 작업자가 도는 중에 답으로 끝내려 함
               resp(calls=[("workers", {"action": "wait"})]),
               resp("다 모았다: 에이, b.txt 없음")]
        sent = []
        worker_go = threading.Event()

        def wchat(*a, **k):
            worker_go.wait(5)
            return resp("b.txt 는 없다")

        def mchat(b, m, *a, **k):
            sent.append(json.loads(json.dumps(m)))
            if len(sent) == 4:
                worker_go.set()
            return seq[len(sent) - 1]
        with mock.patch.object(C, "chat", side_effect=mchat), mock.patch.object(engine, "chat", side_effect=wchat):
            r = s.turn("a.txt 를 보고, b.txt 는 작업자에게 맡겨")
        self.assertEqual(r, "answered")
        blob = json.dumps(sent, ensure_ascii=False)
        self.assertIn("바로 돈다", blob)                                                # spawn 은 바로 돌아옴
        self.assertIn("아직 돈다", json.dumps(sent[3], ensure_ascii=False))             # 답 앞 한 번 돌려줌
        self.assertIn("b.txt 는 없다", json.dumps(sent[4], ensure_ascii=False))          # wait 결과
        ev = (s.rec.dir / "events.jsonl").read_text(encoding="utf-8")
        for k in ("spawn_start", "spawn_end", "spawn_guard", "spawn_wait"):
            self.assertIn(f'"{k}"', ev)

    def test_off(self):
        self.assertNotIn("workers", self.sess(STEWART_SPAWN="0").tool_names)


if __name__ == "__main__":
    unittest.main()
