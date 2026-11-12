"""판 97(SW-43 ①): 맡기기 넛지 — 한 차례에 읽기 도구를 NUDGE_READS 번 넘게 부르고 delegate 를 안 불렀으면 한 번 "맡기면 문맥이 덜 찬다",
차례에 한 번·세션 상한·delegate 를 불렀으면 안 함·STEWART_DELEGATE_NUDGE=0 끔. delegate 설명·chat 지시에 "언제 쓰나"."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, delegate as DG
from stewart.config import Backend, Profile, load_profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_u60 import resp


class TUnit(unittest.TestCase):
    def test_rules(self):
        n = DG.Nudge()
        reads = [{"name": "read_file"}] * (DG.NUDGE_READS - 1)
        self.assertEqual(n.on_step(reads), "")
        self.assertIn("delegate", n.on_step(reads + [{"name": "search"}]))
        self.assertEqual(n.on_step(reads * 2), "")                            # 차례에 한 번
        n.reset_turn()
        self.assertEqual(n.on_step(reads * 2 + [{"name": "delegate"}]), "")   # 이미 맡겼으면 안 함
        for _ in range(5):
            n.reset_turn()
            n.on_step(reads * 2)
        self.assertEqual(n.session_n, DG.NUDGE_PER_SESSION)
        self.assertEqual(DG.Nudge(on=False).on_step(reads * 2), "")

    def test_texts(self):
        self.assertIn("넓게 읽어야", DG.CHAT_DEFINITION["function"]["description"])
        self.assertEqual(DG.DEFINITION["function"]["description"], DG.DESC_SHORT)       # 평가(엔진) 길은 그대로
        self.assertIn("delegate 로 맡기고", load_profile("chat").system)


class TChat(unittest.TestCase):
    def sess(self, **env):
        tmp = Path(tempfile.mkdtemp())
        for i in range(10):
            (tmp / f"f{i}.txt").write_text(str(i), encoding="utf-8")
        p = Profile(name="chat", path=tmp / "p.toml", system="s", tools=["read_file", "delegate"], workdir=tmp, max_steps=12)
        with mock.patch.dict(os.environ, env):
            s = C.ChatSession(p, Backend("f", "http://x/v1", "m", "q", 100000), Recorder(tmp / "runs", "c"), FakeUI(), stream=False, mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        return s

    def go(self, s):
        seq = [resp(calls=[("read_file", {"path": f"f{i}.txt"})]) for i in range(9)] + [resp("다 읽었다")]
        sent = []
        with mock.patch.object(C, "chat", side_effect=lambda b, m, *a, **k: (sent.append(json.loads(json.dumps(m))), seq[len(sent) - 1])[1]):
            s.turn("파일들을 훑어 봐")
        return sent

    def test_nudges_once_and_off(self):
        s = self.sess()
        sent = self.go(s)
        hits = [i for i, m in enumerate(sent) if "delegate(task=…)" in json.dumps(m, ensure_ascii=False)]
        self.assertEqual(hits[0], DG.NUDGE_READS)                               # 여덟째 읽기 뒤 요청부터
        self.assertEqual(json.dumps(sent[-1], ensure_ascii=False).count("delegate(task=…)"), 1)
        self.assertIn('"delegate_nudge"', (s.rec.dir / "events.jsonl").read_text())
        sent = self.go(self.sess(STEWART_DELEGATE_NUDGE="0"))
        self.assertNotIn("delegate(task=…)", json.dumps(sent, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
