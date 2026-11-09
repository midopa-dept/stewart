"""자동 압축(문턱식): 덩어리 한 번에, 얼린 앞쪽은 다시 안 고침, 형식 보존, 다시 걸리는 조건, engine.run·chat 기록."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, engine
from stewart.compact import Compactor, clip_block
from stewart.config import Backend, ConfigError, Profile, load_profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI


def _r(content=None, calls=None, prompt=10):
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [{"id": f"c{i}-{prompt}", "type": "function", "function": {"name": n, "arguments": a}}
                             for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": msg, "finish_reason": "stop"}], "usage": {"prompt_tokens": prompt, "completion_tokens": 5}}, 0.1


def convo(n_tools=6, size=2000):
    m = [{"role": "system", "content": "s"}, {"role": "user", "content": "과제 " + "x" * 1000}]
    for i in range(n_tools):
        m.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"t{i}", "type": "function", "function": {"name": "write_file",
                                                             "arguments": json.dumps({"path": f"f{i}.py", "content": "y" * size})}}]})
        m.append({"role": "tool", "tool_call_id": f"t{i}", "name": "write_file", "content": "z" * size})
    return m


class TCompact(unittest.TestCase):
    def test_clip_block_keeps_shape(self):
        m = convo()
        ids = [(x["role"], x.get("tool_call_id"), [t["id"] for t in x.get("tool_calls") or []]) for x in m]
        r = clip_block(m, 2, len(m))
        self.assertEqual(ids, [(x["role"], x.get("tool_call_id"), [t["id"] for t in x.get("tool_calls") or []]) for x in m])
        self.assertEqual(r["clipped"], 12)
        self.assertLess(r["chars_after"], r["chars_before"] / 3)
        args = json.loads(m[2]["tool_calls"][0]["function"]["arguments"])    # 인자는 여전히 올바른 JSON
        self.assertEqual(args["path"], "f0.py")
        self.assertIn("압축", args["content"])
        self.assertEqual(m[1]["content"][:5], "과제 xx")                     # 사람 입력은 그대로
        self.assertEqual(len(m[1]["content"]), 1003)

    def test_threshold_once_and_frozen(self):
        c = Compactor(1000, ratio=0.7, keep_recent=4, keep_share=10.0)   # 글자 상한은 따로 시험(여기서는 끔)
        m = convo()
        self.assertIsNone(c.maybe(m, 600))                 # 문턱 아래
        r = c.maybe(m, 800, step=3)
        self.assertEqual((r["from"], r["to"]), (2, len(m) - 4))
        frozen = json.dumps(m[: r["to"]], ensure_ascii=False)
        self.assertEqual(c.observe(500)["after_tokens"], 500)
        self.assertIsNone(c.maybe(m, 590))                 # 줄인 뒤: 500 + 10% 를 넘어야 다시
        m += convo(2)[2:]
        r2 = c.maybe(m, 720, step=5)
        self.assertEqual(r2["from"], r["to"])              # 새 덩어리만
        self.assertEqual(json.dumps(m[: r["to"]], ensure_ascii=False), frozen)   # 얼린 앞쪽은 그대로
        self.assertEqual(len(c.log), 2)

    def test_big_recent_results_still_compacted(self):
        # 작은 모델 시험에서 본 경우: 큰 도구 결과가 다 최근 8개 안에 있다 → 글자 상한으로 경계를 당겨 줄인다
        c = Compactor(16384, ratio=0.7, keep_recent=8)
        m = convo(n_tools=3, size=7000)          # 메시지 8개(시스템·과제 + 3쌍)
        r = c.maybe(m, 13000)
        self.assertIsNotNone(r)
        self.assertGreaterEqual(r["clipped"], 2)
        self.assertEqual(m[-1]["content"], "z" * 7000)                     # 마지막 결과는 그대로

    def test_disabled_and_force(self):
        c = Compactor(1000, ratio=0)
        m = convo()
        self.assertIsNone(c.maybe(m, 999999))
        self.assertTrue(c.maybe(m, 0, force=True)["clipped"])

    def test_engine_run_compacts_and_records(self):
        tmp = Path(tempfile.mkdtemp())
        bk = Backend("fake", "http://x/v1", "m", "q", 10000)
        p = Profile(name="t", path=tmp / "p.toml", system="s", tools=["*"], workdir=tmp, max_steps=12, compact_keep_recent=2)
        big = json.dumps({"path": "a.txt", "content": "q" * 5000})
        seq = [_r(calls=[("write_file", big)], prompt=p_) for p_ in (2000, 5000, 7500, 3000)] + [_r("끝", prompt=3200)]
        rec = Recorder(tmp / "runs", "t")
        with mock.patch.object(engine, "chat", side_effect=seq) as m:
            s = engine.run(p, bk, "do", rec)
        self.assertEqual(s["compactions"], 1)
        log = s["compact_log"][0]
        self.assertEqual((log["before_tokens"], log["after_tokens"]), (7500, 3000))
        sent = m.call_args_list[3][0][1]                   # 압축 뒤 요청: 앞 툴콜 인자가 줄어 있다
        self.assertIn("압축", sent[2]["tool_calls"][0]["function"]["arguments"])
        kinds = [json.loads(l)["kind"] for l in (rec.dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual((kinds.count("compact"), kinds.count("compact_effect")), (1, 1))
        self.assertIn("문맥 압축", (rec.dir / "transcript.md").read_text())

    def test_engine_no_compact_below_threshold(self):
        tmp = Path(tempfile.mkdtemp())
        bk = Backend("fake", "http://x/v1", "m", "q", 150000)       # cloud 크기: v1.2 과제 크기로는 걸리지 않는다
        p = Profile(name="t", path=tmp / "p.toml", system="s", tools=["*"], workdir=tmp, max_steps=6)
        seq = [_r(calls=[("list_dir", "{}")], prompt=40000), _r("끝", prompt=41000)]
        with mock.patch.object(engine, "chat", side_effect=seq):
            s = engine.run(p, bk, "do", Recorder(tmp / "runs", "t"))
        self.assertEqual((s["compactions"], s["compact_log"]), (0, []))

    def test_chat_uses_same_compactor(self):
        tmp = Path(tempfile.mkdtemp())
        p = Profile(name="chat", path=tmp / "p.toml", system="s", tools=["*"], workdir=tmp, max_steps=4)
        s = C.ChatSession(p, Backend("fake", "http://x/v1", "m", "q", 1000), Recorder(tmp / "runs", "chat-t"), FakeUI())
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        s.messages = convo()
        s.st["last_prompt_tokens"] = 900
        self.assertGreater(s.compact(), 0)
        self.assertEqual(s.compact(force=True), 0)          # 이미 줄인 덩어리는 다시 안 고친다(새 것 없음)
        self.assertEqual(s.summary()["compactions"], 1)
        s.reset()
        self.assertEqual(s.comp.upto, 1)

    def test_profile_keys(self):
        tmp = Path(tempfile.mkdtemp())
        f = tmp / "c.toml"
        f.write_text('system = "s"\ncompact_ratio = 0.5\ncompact_keep_recent = 4\n', encoding="utf-8")
        p = load_profile(str(f))
        self.assertEqual((p.compact_ratio, p.compact_keep_recent), (0.5, 4))
        for bad in ("compact_ratio = 1.5", "compact_keep_recent = 0", 'compact_ratio = "x"'):
            f.write_text(f'system = "s"\n{bad}\n', encoding="utf-8")
            with self.assertRaises(ConfigError, msg=bad):
                load_profile(str(f))


if __name__ == "__main__":
    unittest.main()
