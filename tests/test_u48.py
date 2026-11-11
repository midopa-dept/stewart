"""허브 메인 길 압축 재현(실사용 지적 SW-4 "자동 compact 안 하는 것 같다"): 가짜 서버가 큰 prompt_tokens 를 돌려주는 차례 여럿,
도구 있는/없는 차례 섞기, 이어 받은 옛 대화 — 문턱을 넘으면 실제로 줄이거나 접고 화면에 한 줄이 뜨는지, 화면 % 와 판정이 같은 창을 보는지."""

import json
import os
from unittest import mock

from tests.test_hub import msg
from tests.test_hub3 import MainBase

WIN = 32768


def big(content=None, calls=None, prompt=0):
    r = msg(content, calls)
    r["usage"] = {"prompt_tokens": prompt, "completion_tokens": 5}
    return r


class THubCompact(MainBase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"STEWART_SUMMARY": "1"})
        p.start()
        self.addCleanup(p.stop)
        super().setUp()
        (self.wd / "a.txt").write_text("한글 내용 " * 2000, encoding="utf-8")

    def lines(self):
        return [m["text"] for m in self.main.state()["main_log"] if m["kind"] in ("compact", "warn")]

    def test_mixed_turns_clip_then_fold(self):
        s = self.main.sess
        self.assertEqual(s.comp.full_context, WIN)
        t = self.main.state()["tokens"]
        self.assertEqual(t["window"], WIN)                                   # 화면 % 의 창 = 판정의 창(서빙 창)
        # 차례 1: 도구(큰 결과) — 문맥 30%
        self.r.scripts["MAIN"] = [big(calls=[("read_file", {"path": "a.txt"})], prompt=int(WIN * 0.3)), big("읽었다", prompt=int(WIN * 0.35))]
        self.main.send("a.txt 읽어")
        self.assertTrue(self.main.wait_idle())
        # 차례 2·3: 도구 없이 답만 — 문맥 60% → 차례 시작 판정으로 줄인다
        for i, p in enumerate((0.6, 0.62)):
            self.r.scripts["MAIN"] = [big(f"답 {i}", prompt=int(WIN * p))]
            self.main.send(f"질문 {i}")
            self.assertTrue(self.main.wait_idle())
        self.assertGreaterEqual(s.st["compactions"], 1, self.lines())
        self.assertTrue(any(x.startswith("압축함(오래된 도구 결과") for x in self.lines()), self.lines())
        # 차례 4·5: 줄인 뒤에도 80% — 요약 접기(요약 호출은 시스템이 MAIN 이 아니라 WORKER 대본으로 간다)
        self.r.scripts["WORKER"] = [msg("## 목표·사람 요구\n- a.txt 를 읽는다")]
        for i in range(2):
            self.r.scripts["MAIN"] = [big(f"답 더 {i}", prompt=int(WIN * 0.8))]
            self.main.send(f"더 {i}")
            self.assertTrue(self.main.wait_idle())
        self.assertGreaterEqual(s.st.get("summaries", 0), 1, self.lines())
        self.assertTrue(any(x.startswith("압축함(앞 대화") for x in self.lines()), self.lines())
        self.assertTrue(s.messages[1]["content"].startswith("[엔진] 앞 대화 요약"))

    def test_summary_fail_is_visible(self):
        s = self.main.sess
        s.messages += [m for i in range(5) for m in ({"role": "user", "content": f"사람 {i}"}, {"role": "assistant", "content": f"답 {i}"})]
        s.st["last_prompt_tokens"] = int(WIN * 0.8)
        with mock.patch("stewart.chat.chat", side_effect=RuntimeError("서버 끊김")):
            s.compact()
        self.assertTrue(any("요약해 접기를 못 했다" in x for x in self.lines()), self.lines())

    def test_resumed_history_compacts(self):
        hist = [{"role": "system", "content": "MAIN 지시"}] + [m for i in range(6) for m in (
            {"role": "user", "content": f"옛 질문 {i}"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": f"o{i}", "type": "function",
                                                                   "function": {"name": "read_file", "arguments": json.dumps({"path": "a.txt"})}}]},
            {"role": "tool", "tool_call_id": f"o{i}", "content": "옛 결과 " * 800},
            {"role": "assistant", "content": f"옛 답 {i}"})]
        rd = self.tmp / "runs" / "old"
        rd.mkdir(parents=True)
        (rd / "messages.json").write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")
        from stewart.resume import load_messages
        self.main.sess.messages = load_messages(rd)
        self.r.scripts["MAIN"] = [big("이어 답", prompt=int(WIN * 0.6)), big("또 답", prompt=int(WIN * 0.6))]
        self.main.sess.comp.next_at = 1000           # 이 시험의 옛 대화(약 5천 토큰)가 문턱을 넘게
        self.main.send("이어서")                      # 이어 받은 첫 차례(도구 없는 답): 잰 값이 없어도 어림으로 판정(SW-19)
        self.assertTrue(self.main.wait_idle())
        self.assertGreaterEqual(self.main.sess.st["compactions"], 1, self.lines())
        self.main.send("또")
        self.assertTrue(self.main.wait_idle())
        self.assertGreaterEqual(self.main.sess.st["compactions"], 1, self.lines())


class TLongTurn(MainBase):
    """한 차례가 아주 긴 경우(사람 차례 하나): 최근 10단계 앞을 접고 지금 차례 사람 말은 원문 그대로."""
    def test_long_single_turn_fold(self):
        s = self.main.sess
        s.messages.append({"role": "user", "content": "큰 일: 모든 파일을 고쳐라"})
        for i in range(14):
            s.messages += [{"role": "assistant", "content": None, "tool_calls": [{"id": f"x{i}", "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"}}]},
                           {"role": "tool", "tool_call_id": f"x{i}", "content": f"결과 {i}"}]
        with mock.patch("stewart.chat.chat", lambda *a, **k: ({"choices": [{"message": {"content": "요약"}}]}, 0.1)):
            r = s.summarize("manual")
        self.assertIsNotNone(r)
        self.assertIn("[지금 차례 사람 말(원문)]\n큰 일: 모든 파일을 고쳐라", s.messages[1]["content"])
        self.assertEqual(s.messages[2]["role"], "assistant")
        self.assertEqual(sum(1 for m in s.messages if m["role"] == "assistant"), 10)
        ids = {m["tool_call_id"] for m in s.messages if m["role"] == "tool"}
        self.assertEqual(ids, {tc["id"] for m in s.messages for tc in m.get("tool_calls") or []})


class TCompactingLine(MainBase):
    def test_spinner_then_result(self):
        s = self.main.sess
        s.messages += [m for i in range(5) for m in ({"role": "user", "content": f"사람 {i}"}, {"role": "assistant", "content": f"답 {i}"})]
        seen = []

        def fake(*a, **k):
            seen.append(self.main.state()["now"])
            return {"choices": [{"message": {"content": "요약"}}]}, 0.1
        with mock.patch("stewart.chat.chat", fake):
            self.main.compact_now("summary")
        self.assertTrue(seen[0]["what"].startswith("문맥 압축 중"))     # 접는 동안 돌기 줄
        self.assertTrue(any(x.startswith("압축함(앞 대화") and "→" in x for x in self.lines()), self.lines())
        self.assertNotEqual((self.main.ui.now or {}).get("what", ""), seen[0]["what"])

    def lines(self):
        return [m["text"] for m in self.main.state()["main_log"] if m["kind"] in ("compact", "warn")]
