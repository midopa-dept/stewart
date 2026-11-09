"""Brave Search 결과는 실행 중 메모리에서만 쓰고 기록(사건·대화록·summary·messages.json)·디스크 캐시에 남기지 않는다."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import engine, nostore as NS, tools as T
from stewart.config import Backend, load_profile
from stewart.record import Recorder
from stewart.websearch import SearxClient

BRAVE = {"title": "브레이브제목XYZ", "url": "https://brave.example/xyz", "content": "브레이브요약QQQ", "engines": ["braveapi"]}
BING = {"title": "빙제목ABC", "url": "https://bing.example/abc", "content": "빙요약", "engines": ["bing"]}


class TScrub(unittest.TestCase):
    def test_scrub(self):
        t = "앞 " + NS.wrap("비밀 결과") + " 뒤"
        out = NS.scrub(t)
        self.assertNotIn("비밀 결과", out)
        self.assertTrue(out.startswith("앞 ") and out.endswith(" 뒤"))
        self.assertIn("약관상 기록에 남기지 않음", out)
        cut = NS.scrub("앞 " + NS.BEGIN + "\n잘린 결과…(압축)")        # 끝 표지가 잘려 나간 경우
        self.assertEqual(cut.split("〔")[0], "앞 ")
        self.assertNotIn("잘린 결과", cut)
        self.assertEqual(NS.scrub({"a": [t, 1, {"b": t}]})["a"][2]["b"], out)
        self.assertEqual(NS.scrub("그대로"), "그대로")
        self.assertTrue(NS.is_brave(["braveapi"]) and NS.is_brave(["Brave"]) and not NS.is_brave(["bing"]))


class Fake:
    def __init__(self, rows):
        self.rows = rows

    def search(self, q, max_results=8, **k):
        return {"query": q, "results": list(self.rows), "error": None, "unresponsive": []}

    def quota(self, *a, **k):
        return None


class TEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def run_with(self, rows):
        p = load_profile("chat")
        p.workdir, p.max_steps, p.tools = self.tmp, 3, ["web_search"]
        p.web_search = {"url": "http://searx.invalid", "max_total": 5, "max_results": 5}
        sent = []
        seq = [({"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": '{"query": "q"}'}}]},
                 "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 5}}, 0.1),
               ({"choices": [{"message": {"role": "assistant", "content": "답이다"}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 5, "completion_tokens": 5}}, 0.1)]

        def fake_chat(backend, messages, *a, **k):
            sent.append(json.dumps(messages, ensure_ascii=False))
            return seq[len(sent) - 1]

        rec = Recorder(self.tmp / "runs", "t")
        with mock.patch.object(engine, "chat", side_effect=fake_chat), mock.patch.object(T, "_searx", return_value=Fake(rows)):
            s = engine.run(p, Backend("fake", "http://x/v1", "m", "q", 100000), "찾아", rec)
        disk = "".join(f.read_text(encoding="utf-8") for f in rec.dir.iterdir() if f.is_file())
        return s, sent, disk

    def test_brave_only_in_memory(self):
        s, sent, disk = self.run_with([BRAVE, BING])
        self.assertIn("브레이브제목XYZ", sent[1])                         # 모델은 받는다(실행 중 메모리)
        for secret in ("브레이브제목XYZ", "brave.example/xyz", "브레이브요약QQQ", "빙제목ABC"):
            self.assertNotIn(secret, disk, secret)                         # 섞이면 그 검색 결과 글 전체를 남기지 않는다
        self.assertIn("약관상 기록에 남기지 않음", disk)
        log = s["web_searches"][0]
        self.assertEqual((log["urls"], log["brave_results"]), (["https://bing.example/abc"], 1))   # 다른 엔진 주소는 남김

    def test_without_brave_unchanged(self):
        s, sent, disk = self.run_with([BING])
        self.assertIn("빙제목ABC", disk)
        self.assertNotIn(NS.BEGIN, sent[1])
        self.assertNotIn("brave_results", s["web_searches"][0])


class TDiskCache(unittest.TestCase):
    def test_brave_not_cached_on_disk(self):
        tmp = Path(tempfile.mkdtemp())
        cache = tmp / "c.jsonl"
        c = SearxClient("http://searx.invalid", cache_path=cache)
        data = {"results": [BRAVE, BING], "unresponsive_engines": []}
        with mock.patch.object(c, "_get", return_value=data):
            r = c.search("q1")
        self.assertEqual(r["brave"], 1)
        self.assertFalse(cache.exists() and cache.read_text().strip())   # Brave 가 섞인 결과는 디스크에 안 씀
        with mock.patch.object(c, "_get", return_value={"results": [BING]}):
            c.search("q2")
        self.assertIn("빙제목ABC", cache.read_text(encoding="utf-8"))      # 다른 엔진만이면 전처럼 캐시
        self.assertNotIn("브레이브", cache.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
