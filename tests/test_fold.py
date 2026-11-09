"""정리 실행의 문맥 접기(문턱마다 한 번에, 한 줄 머리), scan_read 글자 상한·폴더 머리 꼴."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import engine, tools as T
from stewart.compact import Compactor, fold_block
from stewart.config import Backend, load_profile
from stewart.record import Recorder

_N = [0]


def resp(content=None, calls=None, prompt_tokens=10):
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = []
        for n, a in calls:
            _N[0] += 1
            msg["tool_calls"].append({"id": f"call{_N[0]}", "type": "function", "function": {"name": n, "arguments": a}})
    return {"choices": [{"message": msg, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 5}}, 0.1


def conv(steps: int):
    """system, user, 그리고 단계마다 assistant(scan_read 호출) + 긴 tool 결과."""
    m = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    for i in range(steps):
        m.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": "c0", "type": "function", "function": {"name": "scan_read" if i % 3 else "plan_add",
                                                          "arguments": json.dumps({"offset": i * 200})}}]})
        m.append({"role": "tool", "tool_call_id": "c0", "content": f"결과 {i} " + "x" * 3000})
    return m


class TFoldUnit(unittest.TestCase):
    def test_fold_block_heads_and_keeps(self):
        m = conv(12)
        r = fold_block(m, 2, len(m) - 16, plan_items=7)
        tools = [x for x in m if x["role"] == "tool"]
        heads = [x["content"] for x in tools if x["content"].startswith("[접힘]")]
        self.assertEqual(r["folded"], len(heads))
        self.assertTrue(heads)
        self.assertIn('[접힘] scan_read(offset=200) 원래 3,005자, 접을 때 plan 에 7건 반영', heads[0])
        # plan_add 결과(0·3·6… 번째)는 원문, 최근 8 단계도 원문
        self.assertTrue(all(x["content"].startswith("결과") for i, x in enumerate(tools) if i % 3 == 0))
        self.assertTrue(all(x["content"].startswith("결과") for x in tools[-8:]))
        self.assertEqual(len(m), 2 + 24)                                   # 메시지 수·순서는 그대로

    def test_compactor_once_per_threshold(self):
        c = Compactor(10000, ratio=0.55, keep_recent=8, fold=True, plan_count=lambda: 3)
        m = conv(20)
        self.assertIsNone(c.maybe(m, 5000))                              # 문턱 아래
        r = c.maybe(m, 6000)
        self.assertTrue(r and r["folded"] > 0)
        frozen = json.dumps(m[: c.upto], ensure_ascii=False)
        m += conv(2)[2:]
        self.assertIsNone(c.maybe(m, 6100))                              # 다시 자라기 전에는 안 접는다(앞부분 고정)
        self.assertEqual(json.dumps(m[: len(json.loads(frozen))], ensure_ascii=False), frozen)
        self.assertEqual(len([x for x in m if x["role"] == "assistant"][-8:]), 8)


class TFoldEngine(unittest.TestCase):
    """가짜 긴 정리 실행: 문턱을 넘으면 접히고, plan.json·되풀이 막기·남은 검색 줄은 그대로."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "root"
        for i in range(60):
            p = self.root / f"d{i % 6}" / f"f{i:03d}.mp4"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("v")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_long_run(self):
        class Fake:
            def __init__(self):
                self.queries = []

            def search(self, q, max_results=8):
                self.queries.append(q)
                return {"query": q, "results": [{"title": "제목", "url": "https://e.org/1", "content": "요약 " * 50}],
                        "error": None, "unresponsive": []}

        fake = Fake()
        p = load_profile("organize")
        p.workdir, p.max_steps = self.root, 40
        p.web_search = {"url": "http://searx.invalid", "max_total": 30, "max_results": 8}
        bk = Backend("fake", "http://x/v1", "m", "q", 20000)                 # 55% = 11,000 토큰
        wsq = lambda q: ("web_search", json.dumps({"query": q}, ensure_ascii=False))
        seq = [resp(calls=[("folder_scan", "{}")], prompt_tokens=1000)]
        tok = 1000
        for i in range(16):                                                # 단계마다 약 1.5k 씩 자란다(문턱·다시 걸림 둘 다 넘게)
            tok += 1500
            seq.append(resp(calls=[("scan_read", json.dumps({"offset": i * 5, "limit": 5})), wsq(f"검색 {i}")],
                            prompt_tokens=tok))
        seq.append(resp(calls=[("plan_add", json.dumps({"items": [
            {"op": "move", "src": "d0/f000.mp4", "dst": "Video/f000.mp4", "reason": "r", "confidence": 0.9, "evidence": "scan"}]}))],
            prompt_tokens=tok))
        seq.append(resp(calls=[wsq("검색 0")], prompt_tokens=tok))           # 접힌 검색을 다시 부른다(허용, 횟수 안 듦)
        seq.append(resp("끝", prompt_tokens=tok))
        sent = []

        def fake_chat(backend, messages, tools=None, *a, **k):
            sent.append(json.loads(json.dumps(messages)))
            return seq[len(sent) - 1]

        rec = Recorder(self.tmp / "runs", "org")
        with mock.patch.object(engine, "chat", side_effect=fake_chat), mock.patch.object(T, "_searx", return_value=fake):
            s = engine.run(p, bk, "정리해", rec)
        self.assertEqual(s["stop_reason"], "completed")
        self.assertGreaterEqual(s["compactions"], 2)
        self.assertGreater(s["compact_folded"], 0)
        self.assertIn("compact_saved_tokens", s)
        last = sent[-1]
        heads = [m["content"] for m in last if m["role"] == "tool" and m["content"].startswith("[접힘]")]
        self.assertTrue(any(h.startswith("[접힘] scan_read(") for h in heads))
        self.assertTrue(any(h.startswith("[접힘] folder_scan(") for h in heads))
        self.assertTrue(all("다시 필요하면 같은 도구를" in h for h in heads))
        plan_msgs = [m["content"] for m in last if m["role"] == "tool" and m["content"].startswith("넣음 ")]
        self.assertEqual(len(plan_msgs), 1)                                 # plan_add 결과는 원문
        again = [m["content"] for m in last if m["role"] == "tool"][-1]
        self.assertIn("같은 검색어를 이미 찾았다", again)                     # 되풀이 막기 그대로(원문을 다시 준다)
        self.assertIn("남은 검색", again)
        self.assertEqual(len(fake.queries), 16)                             # 다시 부른 것은 서버에 안 감
        plan = json.loads((rec.dir / "plan.json").read_text())
        self.assertEqual(len(plan["items"]), 1)
        self.assertTrue(plan["run"]["complete"])

    def test_code_profile_not_folded(self):
        p = load_profile("code")
        self.assertFalse(bool(p.compact_fold))
        o = load_profile("organize")
        self.assertEqual((o.compact_ratio, o.compact_keep_recent, o.compact_fold), (0.55, 8, True))


class TScanRead(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "root"
        for i in range(900):
            p = self.root / "깊은" / "폴더" / f"묶음{i // 100}" / f"아주-긴-파일-이름-{i:04d}.mkv"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("v")
        self.env = T.ToolEnv(self.root, artifacts_dir=self.tmp / "runs")
        T.folder_scan(self.env)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_char_cap_and_continuation(self):
        seen, off = [], 0
        while True:
            ok, out = T.scan_read(self.env, offset=off, limit=500, type="file")
            self.assertTrue(ok)
            body = out.split("\n", 2)[2].rsplit("\n[바깥 자료 끝]", 1)[0]
            self.assertLessEqual(len(body), T.SCAN_READ_CHARS + 200)
            names = [ln.strip().split("\t")[0] for ln in body.splitlines() if ln.startswith("  ")]
            seen += names
            if "더 있다: offset=" not in out:
                break
            nxt = int(out.split("더 있다: offset=")[1].split(")")[0].split(" ")[0])
            self.assertEqual(nxt, off + len(names))                         # 끊어도 다음 offset 이 정확하다
            off = nxt
        self.assertEqual(len(seen), 900)
        self.assertEqual(len(set(seen)), 900)

    def test_grouped_is_shorter(self):
        ok, out = T.scan_read(self.env, limit=100, type="file")
        old = "\n".join(f"깊은/폴더/묶음0/아주-긴-파일-이름-{i:04d}.mkv\t2B\t2026-11-09\tvideo" for i in range(100))
        self.assertLess(len(out), len(old))
        self.assertIn("[깊은/폴더/묶음0/]", out)


if __name__ == "__main__":
    unittest.main()
