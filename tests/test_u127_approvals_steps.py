"""판 127: 전부 볼 수 있게 — 승인 기록(무엇을·어떻게 답했나·얼마나 기다렸나, 화면 기록·대화 기록 폴더, 다시 열어도 남음),
걸음 숫자(입력·캐시·생성·생각 토큰·첫 토큰까지·생성 시간·tok/s, 재지 못한 값은 None → 화면 "서버가 주지 않음", 생각은 어림·tokenize 를 밝힘)."""

import json
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.test_hub import msg
from tests.test_hub3 import MainBase

ROOT = Path(__file__).resolve().parent.parent


def ask_one(hub, name, ans, via="web", wait=0.0):
    q = []
    for _ in range(200):
        q = [x for x in hub.queue() if x["session"] == name]
        if q:
            break
        time.sleep(0.05)
    assert q, "승인 건이 와야 한다"
    time.sleep(wait)
    hub.answer(name, q[0]["seq"], ans, via=via)


class TApprovals(MainBase):
    def test_recorded_and_replayed(self):
        self.r.scripts["MAIN"] = [msg(calls=[("run_shell", {"command": "touch a.txt && echo a"})]), msg(calls=[("run_shell", {"command": "touch b.txt && echo b"})]), msg("끝")]
        c = self.main.new_chat("fresh")
        c.send("돌려라")
        ask_one(self.hub, c.name, "y", via="web-always", wait=0.3)
        ask_one(self.hub, c.name, "n")
        self.assertTrue(c.wait_idle())
        aps = [e for e in c.state()["main_log"] if e["kind"] == "approval"]
        self.assertEqual([a["answer"] for a in aps], ["늘 허용함", "거부함"])
        self.assertIn("echo a", aps[0]["what"])
        self.assertGreaterEqual(aps[0]["secs"], 0.3)
        run = Path(str(c.sess.rec.dir))
        evs = [json.loads(x) for x in (run / "events.jsonl").read_text(encoding="utf-8").splitlines() if '"approval"' in x]
        self.assertEqual([e["answer"] for e in evs], ["늘 허용함", "거부함"])        # 대화 기록 폴더에도
        self.main.close_chat(c.name)
        time.sleep(1.1)
        r2 = self.main.new_chat("reopen", run_dir=str(run))
        re = [e for e in r2.state()["main_log"] if e["kind"] == "approval"]
        self.assertEqual([a["answer"] for a in re], ["늘 허용함", "거부함"])          # 다시 열어도 그 도구 줄 뒤에
        self.assertTrue(all(a.get("replayed") for a in re))


class TStepNumbers(unittest.TestCase):
    def _sess(self, backend):
        from stewart.chat import ChatSession
        s = ChatSession.__new__(ChatSession)
        s.backend, s._ttft = backend, 0.4
        return s

    def test_server_tokenize_estimate(self):
        from stewart import chat as C
        from stewart.config import Backend
        b = Backend(name="local", base_url="http://127.0.0.1:9/v1", model="m", precision="?", max_context=8192)
        s = self._sess(b)
        u = {"prompt_tokens": 100, "completion_tokens": 50, "completion_tokens_details": {"reasoning_tokens": 20}}
        d = s._step_numbers(1, u, {"predicted_ms": 500}, 1.0, "생각", "답", False)
        self.assertEqual((d["reasoning"], d["reasoning_how"], d["gen_s"], d["tps"], d["ttft_s"]), (20, "server", 0.5, 100.0, 0.4))
        self.assertIsNone(d["cached"])                                             # 서버가 주지 않음
        C._TOKENIZE_OK.clear()
        with mock.patch.object(C, "_tokenize_count", return_value=7):
            d2 = s._step_numbers(2, {"completion_tokens": 10}, {}, 1.0, "생각 글", "", True)
        self.assertEqual((d2["reasoning"], d2["reasoning_how"], d2["calls"]), (7, "tokenize", True))
        with mock.patch.object(C, "_tokenize_count", return_value=None):
            d3 = s._step_numbers(3, {}, {}, 1.0, "생각 글 열두 자쯤", "", False)
        self.assertEqual(d3["reasoning_how"], "estimate")
        self.assertIsNone(d3["prompt"])
        self.assertIsNone(d3["tps"])

    def test_tokenize_skips_outside(self):
        from stewart import chat as C
        from stewart.config import Backend
        b = Backend(name="o", base_url="https://api.example.invalid/v1", model="m", precision="?", max_context=8192, provider="X")
        self.assertIsNone(C._tokenize_count(b, "글"))                              # 바깥 API 는 부르지 않는다

    def test_ui_marks_missing(self):
        t = (ROOT / "ui" / "src" / "thread.jsx").read_text(encoding="utf-8")
        self.assertIn("const NA = '서버가 주지 않음'", t)
        self.assertIn("(어림 — 글자", t)
        self.assertIn("<ApprovalLine a={s.approval} />", t)
        self.assertIn("lastAp.text", t)                                            # 접힌 줄에 "허용함 · 12초"


if __name__ == "__main__":
    unittest.main()
