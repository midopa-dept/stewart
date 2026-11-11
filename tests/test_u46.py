"""판 65 실사용 지적 SW-4: (1) 복사 — 승인 칸 명령·도구 줄 [명령 복사](명령 전문), 화면 파일 ?v=빌드 지문(캐시 깨기)
(2) 문맥 줄이기 — chat 문턱 0.5, 줄이고도 70% 넘으면 앞 대화를 요약 한 쪽으로 접기(자동·허브 단추), 허브 화면 한 줄."""

import json
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C
from tests.test_hub import msg
from tests.test_hub3 import MainBase

ROOT = Path(__file__).resolve().parent.parent


def _resp(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5}}, 0.1


class TSummary(MainBase):
    def _fill(self):
        s = self.main.sess
        s.messages = [s.messages[0]]
        for i in range(4):
            s.messages += [{"role": "user", "content": f"사람 {i}: 앞으로는 테스트를 먼저 돌려라" if i == 0 else f"사람 {i}"},
                           {"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}", "type": "function",
                            "function": {"name": "read_file", "arguments": json.dumps({"path": "a.txt"})}}]},
                           {"role": "tool", "tool_call_id": f"c{i}", "content": "내용 " * 50},
                           {"role": "assistant", "content": f"답 {i}"}]
        return s

    def test_manual_fold(self):
        s = self._fill()
        seen = []

        def fake(backend, messages, tools=None, **k):
            seen.append(messages)
            return _resp("## 목표·사람 요구\n- 테스트를 먼저 돌린다")
        with mock.patch.object(C, "chat", fake):
            r = self.main.compact_now("summary")
        self.assertTrue(r["ok"])
        self.assertIn("사람 0: 앞으로는 테스트를 먼저 돌려라", seen[0][1]["content"])     # 요약 입력에 사람 지시
        self.assertEqual(s.messages[1]["role"], "user")
        self.assertTrue(s.messages[1]["content"].startswith("[엔진] 앞 대화 요약"))
        self.assertEqual([m["content"] for m in s.messages if m["role"] == "user"][1:], ["사람 2", "사람 3"])   # 최근 두 차례는 원문
        ids = {m["tool_call_id"] for m in s.messages if m["role"] == "tool"}
        calls = {tc["id"] for m in s.messages for tc in m.get("tool_calls") or []}
        self.assertEqual(ids, calls)                                                       # 툴콜 짝이 깨지지 않음
        self.assertTrue(any(m["kind"] == "compact" and m["text"].startswith("압축함(앞 대화") for m in self.main.state()["main_log"]))
        self.assertEqual(self.main.state()["tokens"]["summaries"], 1)
        fold = json.loads((s.rec.dir / "folds" / "001.json").read_text(encoding="utf-8"))   # 접은 원문 보관
        self.assertEqual(fold["messages"][0]["content"], "사람 0: 앞으로는 테스트를 먼저 돌려라")
        self.assertIn("실패 기록", C.SUMMARY_SYSTEM)
        with mock.patch.object(C, "chat", fake):
            self.assertFalse(self.main.compact_now("summary")["ok"])                       # 더 접을 것 없음

    def test_auto_fold_only_after_clip_measured(self):
        s = self._fill()
        s.summary_on = True
        win = s.comp.full_context or s.backend.max_context
        s.st["last_prompt_tokens"] = int(win * 0.8)
        s._clip_step = s.st["steps"]                          # 방금 줄였다 — 다시 잰 값이 아니면 접지 않는다
        with mock.patch.object(s.comp, "maybe", return_value=None), mock.patch.object(C, "chat", lambda *a, **k: _resp("요약")):
            s.compact()
            self.assertEqual(s.st.get("summaries", 0), 0)
            s.st["steps"] += 1
            s.compact()
        self.assertEqual(s.st.get("summaries", 0), 1)

    def test_busy_refused(self):
        self.main._busy.acquire()
        try:
            with self.assertRaises(RuntimeError):
                self.main.compact_now("clip")
        finally:
            self.main._busy.release()

    def test_ratio_and_command_kept(self):
        self.assertEqual(C.COMPACT_RATIO, 0.5)
        self.main.sess.mode = "auto"
        long_cmd = "echo " + "x" * 300 + " && echo   두 칸"
        self.r.scripts["MAIN"] = [msg(calls=[("run_shell", {"command": long_cmd})]), msg("끝")]
        self.main.send("돌려")
        self.assertTrue(self.main.wait_idle())
        t = [m for m in self.main.state()["main_log"] if m["kind"] == "tool"][0]
        self.assertEqual(t["command"], long_cmd)                                           # [명령 복사]는 전문
        js = (ROOT / "src" / "stewart" / "web_ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("명령 복사", js)
        self.assertIn("명령 전문 복사", js)                                                 # 승인 칸



class TCompactFix(MainBase):
    """사용자 실측(허브 83%·자동 압축 없음)의 두 까닭: 차례 시작 판정 없음, 최근 몫을 글자/3 으로 잼(한글에서 창 대부분)."""
    def test_turn_start_compacts(self):
        s = self.main.sess
        s.messages = [s.messages[0], {"role": "user", "content": "가"}, {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "한글 결과 " * 3000}, {"role": "assistant", "content": "답"}]
        win = s.comp.max_context
        s.st["last_prompt_tokens"] = int(win * 0.9)
        self.r.scripts["MAIN"] = [msg("그냥 답")]                # 도구 없이 답만 하는 차례
        self.main.send("다음")
        self.assertTrue(self.main.wait_idle())
        self.assertGreaterEqual(s.st["compactions"], 1)
        self.assertLess(len(s.messages[3]["content"]), 1000)
        self.assertTrue(any(m["kind"] == "compact" for m in self.main.state()["main_log"]))

    def test_keep_share_in_tokens(self):
        from stewart.compact import Compactor
        c = Compactor(1000, ratio=0.5, keep_recent=8, start=1, tail_tokens=True)   # chat·허브
        msgs = [{"role": "system", "content": "s"}] + [{"role": "tool", "tool_call_id": str(i), "content": "한" * 400} for i in range(8)]
        r = c.maybe(msgs, 600)
        self.assertIsNotNone(r)                                   # 글자/3 이면 최근 8개가 다 "작아" 보여 줄일 것이 없었다
        self.assertGreater(r["to"], 1)
        old = Compactor(1000, ratio=0.5, keep_recent=8, start=1)                    # 평가 길 기본: 예전 글자 셈 그대로
        self.assertFalse(old.tail_tokens)

    def test_hub_compact_command(self):
        self.assertEqual(self.main.send("/compact"), "compacted")
        self.assertTrue(any(m["kind"] == "compact" for m in self.main.state()["main_log"]))


if __name__ == "__main__":
    unittest.main()
