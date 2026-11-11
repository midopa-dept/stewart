"""판 90: 학습 내보내기 보강(stewart-train/1 — 추론 글·assistant train 표시·보낸 도구 정의·백엔드·약관 등급 거름·64자 과제 해시·
도구 호출·오류 수·sha256, 한 번 실행 기록도 한 차례로) + 웹 끝맺기 넛지(남은 걸음 2 이하) + 0건 안내가 일반 물음에 정리 문구를 안 섞음."""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, train as TRAIN, webnudge as WN, tools as T
from tests.test_hub import msg
from tests.test_hub3 import MainBase
from tests import test_u60 as U60
from tests.test_u60 import resp
from tests.test_u68 import call, write_events

LOCAL = {"name": "q27", "base_url": "http://127.0.0.1:8080/v1", "model": "qwen-27b"}
CLAUDE = {"name": "claude", "base_url": "https://api.anthropic.com", "model": "claude-x", "provider": "Anthropic", "external": True}
DSEEK = {"name": "ds", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "provider": "api.deepseek.com", "external": True}
POD = {"name": "pod", "base_url": "http://203.0.113.9:8000/v1", "model": "qwen-27b", "provider": "203.0.113.9", "external": True}
DEFS = [{"type": "function", "function": {"name": "read_file", "description": "읽기", "parameters": {"type": "object"}}}]


def chat_run(root: Path, name: str, backend: dict, label: str = "good") -> Path:
    return write_events(root / name, [
        {"kind": "run_start", "meta": {"backend": backend, "profile": "chat", "engine_version": "9.9"},
         "messages": [{"role": "system", "content": "지시"}]},
        {"kind": "tool_defs", "step": 1, "defs": DEFS},
        {"kind": "user", "turn": 1, "content": "a.txt 를  읽어라"},
        {"kind": "step", "new_messages": [{"role": "assistant", "content": None, "tool_calls": [call("c1", "read_file", {"path": "a.txt"})]},
                                          {"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": "없음"}],
         "response": {"reasoning_content": "먼저 파일을 본다", "tool_calls": [{"name": "read_file", "ok": False}]}},
        {"kind": "step", "new_messages": [{"role": "assistant", "content": "a.txt 가 없다"}], "response": {"tool_calls": []}},
        {"kind": "turn_end", "turn": 1, "reason": "answered"},
        {"kind": "train_label", "turn": 1, "label": label, "by": "human"},
    ])


class TFormat(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.runs = self.tmp / "runs"

    def lines(self, out):
        return [json.loads(x) for x in (out / "train.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_line_shape(self):
        chat_run(self.runs, "20261112-090000-hub-main", LOCAL)
        out = self.tmp / "o"
        st = TRAIN.export(self.runs, out, "good")
        (r,) = self.lines(out)
        a = [m for m in r["messages"] if m["role"] == "assistant"]
        self.assertEqual(a[0]["reasoning_content"], "먼저 파일을 본다")   # 걸음 응답의 생각 글
        self.assertEqual([m["train"] for m in a], [True, True])
        self.assertTrue(all("train" not in m for m in r["messages"] if m["role"] != "assistant"))
        self.assertEqual(r["tools"], DEFS)
        m = r["meta"]
        self.assertEqual((m["schema"], m["track"], m["license_tier"], m["turn"]), ("stewart-train/1", "live", "train-ok", 1))
        self.assertEqual(m["backend"], {"name": "q27", "model": "qwen-27b", "provider": "local", "kind": "local"})
        self.assertEqual(m["task_hash"], hashlib.sha256("a.txt 를 읽어라".encode()).hexdigest())   # 공백 접기·64자
        self.assertEqual((m["tool_calls"], m["tool_errors"]), (1, 1))
        self.assertEqual(m["run"], hashlib.sha256(b"20261112-090000-hub-main").hexdigest()[:12])
        self.assertNotIn("127.0.0.1", json.dumps(r))                        # 주소는 싣지 않는다
        self.assertEqual(st["sha256"], hashlib.sha256((out / "train.jsonl").read_bytes()).hexdigest())
        self.assertEqual(json.loads((out / "stats.json").read_text())["sha256"], st["sha256"])

    def test_license_tiers(self):
        for i, b in enumerate((LOCAL, CLAUDE, DSEEK, POD)):
            chat_run(self.runs, f"20261112-09000{i}-c", b)
        st = TRAIN.export(self.runs, self.tmp / "o", "good")
        self.assertEqual(st["written"], 2)
        self.assertEqual(st["excluded_tier"], {"private-eval-only": 1, "unknown": 1})
        got = {r["meta"]["backend"]["name"]: r["meta"]["license_tier"] for r in self.lines(self.tmp / "o")}
        self.assertEqual(got, {"q27": "train-ok", "ds": "train-ok"})
        st = TRAIN.export(self.runs, self.tmp / "o2", "good", include_tiers={"train-ok", "private-eval-only", "unknown"})
        pod = [r for r in self.lines(self.tmp / "o2") if r["meta"]["backend"]["name"] == "pod"][0]
        self.assertEqual(pod["meta"]["backend"]["provider"], "ip-host")     # IP 는 싣지 않는다
        self.assertNotIn("203.0.113.9", (self.tmp / "o2" / "train.jsonl").read_text())
        rules = self.tmp / "tiers.toml"
        rules.write_text('[[rule]]\nmatch = "^pod "\ntier = "train-ok"\n', encoding="utf-8")   # 빌린 장비의 자기 모델
        st = TRAIN.export(self.runs, self.tmp / "o3", "good", tier_file=rules)
        self.assertEqual(st["written"], 3)
        self.assertEqual(TRAIN.tier_of({"name": "x", "model": "gpt-5.5", "provider": "openai.com", "kind": "api"}), "private-eval-only")
        self.assertEqual(TRAIN.tier_of({"name": "g", "model": "glm-5", "provider": "api.z.ai", "kind": "api"}), "private-eval-only")

    def test_after_fix_carries_masked_prev_and_hash16_list(self):
        write_events(self.runs / "20261112-100000-c", [
            {"kind": "run_start", "meta": {"backend": LOCAL}, "messages": [{"role": "system", "content": "지시"}]},
            {"kind": "user", "turn": 1, "content": "보고서를 써라"},
            {"kind": "step", "new_messages": [{"role": "assistant", "content": "영어로 썼다"}]},
            {"kind": "turn_end", "turn": 1, "reason": "answered"},
            {"kind": "user", "turn": 2, "content": "아니, 한국어로"},
            {"kind": "train_label", "turn": 1, "label": "corrected", "by": "rule"},
            {"kind": "step", "new_messages": [{"role": "assistant", "content": "한국어로 썼다"}]},
            {"kind": "turn_end", "turn": 2, "reason": "answered"}])
        TRAIN.export(self.runs, self.tmp / "o", "after_fix")
        (r,) = self.lines(self.tmp / "o")
        self.assertEqual([(m["role"], m.get("train")) for m in r["messages"]],
                         [("system", None), ("user", None), ("assistant", False), ("user", None), ("assistant", True)])
        st = TRAIN.export(self.runs, self.tmp / "o2", "after_fix", exclude_hashes={TRAIN.norm_hash("아니, 한국어로")[:16]})
        self.assertEqual(st["excluded_eval"], 1)

    def test_single_run_record_and_test_result(self):
        write_events(self.runs / "20261112-110000-teacher", [
            {"kind": "run_start", "meta": {"backend": DSEEK, "profile": "worker"},
             "messages": [{"role": "system", "content": "지시"}, {"role": "user", "content": "calc 를 고쳐라"}]},
            {"kind": "tool_defs", "step": 0, "defs": DEFS},
            {"kind": "step", "new_messages": [{"role": "assistant", "content": "고쳤다"}], "response": {}},
            {"kind": "run_end", "summary": {"stop_reason": "completed"}},
            {"kind": "test_result", "passed": True}])
        TRAIN.export(self.runs, self.tmp / "o", "all", track="teacher")
        (r,) = self.lines(self.tmp / "o")
        self.assertEqual((r["meta"]["reason"], r["meta"]["test_pass"], r["meta"]["track"]), ("answered", True, "teacher"))
        self.assertEqual([m["role"] for m in r["messages"]], ["system", "user", "assistant"])
        self.assertEqual(r["tools"], DEFS)


class THubDefs(MainBase):
    def test_chat_records_tool_defs_once(self):
        self.r.scripts["MAIN"] = [msg("했다"), msg("또 했다")]
        self.main.send("해 줘")
        self.assertTrue(self.main.wait_idle())
        self.main.send("또 해 줘")
        self.assertTrue(self.main.wait_idle())
        evs = [json.loads(x) for x in (Path(self.main.sess.rec.dir) / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        d = [e for e in evs if e["kind"] == "tool_defs"]
        self.assertEqual(len(d), 1)                                          # 바뀔 때만
        self.assertEqual([x["function"]["name"] for x in d[0]["defs"]],
                         [x["function"]["name"] for x in self.r.requests["MAIN"][0]["tools"]])


class TWrap(U60.TChatNudge):
    locals().update({k: None for k in dir(U60.TChatNudge) if k.startswith("test")})

    def test_unit(self):
        n = WN.Nudger(search=True, fetch=True)
        n.reset_turn()
        self.assertEqual(n.on_budget(3, 2), "")
        self.assertEqual(n.on_budget(2, 0), "")                               # 웹을 안 불렀으면 안 함
        self.assertIn("남은 걸음이 2개", n.on_budget(2, 1))
        self.assertEqual(n.on_budget(1, 1), "")                               # 차례에 한 번
        n.reset_turn()
        self.assertIn("1개", n.on_budget(1, 3))

    def test_chat_wraps_before_limit(self):
        s = self.sess()
        seq = [resp(calls=[("web_search", {"query": q})]) for q in ("uv release", "astral uv changelog", "pypi uv", "uv github tags")] \
            + [resp("찾은 것으로 답한다")]
        sent = self.go(s, "uv 를 알아봐", seq)
        self.assertIn("남은 걸음이 2개다", json.dumps(sent[4], ensure_ascii=False))
        self.assertNotIn("남은 걸음", json.dumps(sent[3], ensure_ascii=False))
        self.assertIn('"why_kind": "wrap"', (s.rec.dir / "events.jsonl").read_text())
        s2 = self.sess(STEWART_WEB_NUDGE="0")
        sent = self.go(s2, "uv 를 알아봐", seq)
        self.assertNotIn("남은 걸음", json.dumps(sent, ensure_ascii=False))


class TZeroMsg(unittest.TestCase):
    def test_generic_unless_plan(self):
        self.assertNotIn("미확정", T._zero_msg(object(), 2))
        self.assertIn("web_fetch", T._zero_msg(object(), 2))

        class E:
            plan = object()
        self.assertIn("미확정으로 계획", T._zero_msg(E(), 3))


if __name__ == "__main__":
    unittest.main()
