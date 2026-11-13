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


class TReplayImages(MainBase):
    """판 127.2: 허브를 다시 켜거나 지난 대화를 다시 열 때 다시 그린 도구 줄에도 그 도구가 본 그림(view_image 등)과 성공 표시가 실린다."""

    def test_replay_keeps_images_and_ok(self):
        from stewart import images as IMG
        from stewart.hubmain import _replay
        run = self.tmp / "oldrun"
        run.mkdir()
        png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a4c50000000049454e44ae426082")
        part = IMG.store(run, png, "image/png")
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "화면 봐"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "view_image", "arguments": "{\"path\": \"a.png\"}"}}]},
                {"role": "tool", "tool_call_id": "c1", "name": "view_image", "content": "a.png 를 다음 메시지에 붙였다"},
                {"role": "user", "content": IMG.content("[엔진] view_image 로 연 이미지 1장", [part])},
                {"role": "assistant", "content": "봤다"}]
        (run / "events.jsonl").write_text(json.dumps({"kind": "run_start", "meta": {}}) + "\n" + json.dumps(
            {"kind": "step", "response": {"tool_calls": [{"name": "view_image", "ok": True, "elapsed_s": 0.2, "result": "붙였다"}]}}) + "\n", encoding="utf-8")
        ui = self.main.ui
        n0 = len(ui.log)
        _replay(ui, msgs, run)
        tools = [e for e in list(ui.log)[n0:] if e["kind"] == "tool"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].get("ok"), True)                                 # "·" 가 아니라 ✓
        self.assertTrue(tools[0].get("imgs"))                                      # 그림이 다시 실린다
        self.assertIn(tools[0]["imgs"][0], ui.imgs)                                # /api/img 가 내줄 수 있다
        self.assertNotIn("[엔진]", json.dumps([e["text"] for e in list(ui.log)[n0:]], ensure_ascii=False))


class TImagesFourWays(MainBase):
    """판 127.2: 도구가 본 그림이 ① 도는 중(살아 있는 화면 기록) ② 차례 끝난 뒤 ③ 페이지를 새로 연 뒤(상태 다시 받기) ④ 허브를 다시 켜거나 다시 연 뒤 모두 실린다."""

    def _run(self):
        from stewart import images as IMG
        run = self.tmp / "imgrun"
        run.mkdir(exist_ok=True)
        png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a4c50000000049454e44ae426082")
        part = IMG.store(run, png, "image/png")
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "그림 봐"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "view_image", "arguments": "{\"path\": \"b.png\"}"}}]},
                {"role": "tool", "tool_call_id": "c1", "name": "view_image", "content": "b.png 를 다음 메시지에 붙였다"},
                {"role": "user", "content": IMG.content("[엔진] view_image 로 연 이미지 1장", [part])},
                {"role": "assistant", "content": "됐다"}]
        (run / "events.jsonl").write_text(json.dumps({"kind": "run_start", "meta": {}}) + "\n" + json.dumps(
            {"kind": "step", "response": {"tool_calls": [{"name": "view_image", "ok": True, "elapsed_s": 1.0, "result": "OK"}]}}) + "\n", encoding="utf-8")
        return run, msgs, part

    def test_live_and_state(self):
        from stewart import images as IMG
        run, msgs, part = self._run()
        ui = self.main.ui
        ui.tool("view_image", "b.png")                                               # ① 도는 중: 도구 줄 뒤 엔진이 그림을 붙인다
        ui.tool_images([str(IMG.path_of(part))])
        live = [e for e in ui.log if e["kind"] == "tool"][-1]
        self.assertTrue(live.get("imgs"))
        st = self.main.state()                                                     # ②·③ 상태를 새로 받아도 그대로
        self.assertTrue(any(e.get("imgs") for e in st["main_log"] if e["kind"] == "tool"))

    def test_hub_restart_resume(self):
        from stewart.cli import resolve
        from stewart.hubmain import HubMain
        from stewart.record import Recorder
        run, msgs, _ = self._run()
        profile, backend = resolve(str(self.tmp / "main.toml"), "fake", str(self.wd))
        m2 = HubMain(self.hub, profile, backend, Recorder(self.tmp / "runs", "hub-main"), mode="edits", history=msgs, resumed_from=str(run))
        self.addCleanup(m2.close)
        tools = [e for e in m2.ui.log if e["kind"] == "tool"]                      # ④ 허브를 다시 켜 이어 받은 대화
        self.assertEqual((len(tools), tools[0].get("ok")), (1, True))
        self.assertTrue(tools[0].get("imgs"))


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
