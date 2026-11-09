"""모델 없이 엔진 루프·도구·스키마를 확인하는 최소 시험. uv run python -m unittest -q"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import engine, tools as T
from stewart.config import Backend, Profile
from stewart.record import Recorder
from stewart.schema import validate


def _resp(content=None, calls=None):
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [{"id": f"c{i}", "type": "function",
                              "function": {"name": n, "arguments": a}} for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": msg, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "timings": {"predicted_n": 5, "predicted_ms": 100, "prompt_n": 10, "prompt_ms": 50}}, 0.1


class T1(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a.txt").write_text("hello\nworld\n")
        self.backend = Backend("fake", "http://x/v1", "m", "fp16", 4096)

    def prof(self, **kw):
        base = dict(name="t", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.tmp, max_steps=6)
        base.update(kw)
        return Profile(**base)

    def test_tools(self):
        env = T.ToolEnv(self.tmp)
        self.assertTrue(T.edit_file(env, "a.txt", "world", "there")[0])
        self.assertIn("there", T.read_file(env, "a.txt")[1])
        self.assertIn("a.txt:2", T.search(env, "there")[1])
        ok, out = T.run_shell(env, "sleep 5", timeout_s=0.5)
        self.assertFalse(ok)
        self.assertIn("시간 초과", out)

    def test_loop_json(self):
        schema = {"type": "object", "required": ["x"], "properties": {"x": {"type": "integer"}}}
        seq = [_resp(calls=[("read_file", '{"path": "a.txt"}'), ("read_file", "{bad json")]),
               _resp(content="read_file(path='a.txt')"),          # 본문 툴콜 → 파싱 실패
               _resp(content='{"x": "no"}'),                       # 스키마 실패 → 형식 강제
               _resp(content='```json\n{"x": 3}\n```')]
        with mock.patch.object(engine, "chat", side_effect=seq):
            rec = Recorder(self.tmp / "runs", "t")
            s = engine.run(self.prof(output_format="json", output_schema=schema), self.backend, "do", rec)
        self.assertEqual(s["stop_reason"], "completed")
        self.assertEqual((s["steps"], s["parse_failures"], s["format_failures"]), (4, 2, 1))
        self.assertEqual(json.loads((rec.dir / "output.json").read_text()), {"x": 3})
        self.assertTrue((rec.dir / "transcript.md").exists())
        kinds = [json.loads(l)["kind"] for l in (rec.dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual(kinds[0], "run_start")
        self.assertEqual(kinds[-1], "run_end")

    def test_consecutive_errors(self):
        seq = [_resp(calls=[("nope", "{}")])] * 5
        with mock.patch.object(engine, "chat", side_effect=seq):
            s = engine.run(self.prof(max_consecutive_errors=3), self.backend, "do", Recorder(self.tmp / "runs", "t"))
        self.assertEqual((s["stop_reason"], s["steps"]), ("consecutive_errors", 3))

    def test_schema(self):
        s = {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1}}
        self.assertEqual(validate([0.5, 1], s), [])
        self.assertEqual(len(validate([2, "a"], s)), 2)

    def test_repeat_failure_stops(self):
        # B-2 재현: 이미 반영된 같은 edit 실패와 성공하는 read_file이 번갈아 나온다 → 연속 오류로는 안 멈춤
        edit = ("edit_file", '{"path": "a.txt", "old": "nothere", "new": "x"}')
        read = ("read_file", '{"path": "a.txt"}')
        seq = [_resp(calls=[edit]), _resp(calls=[read])] * 10
        with mock.patch.object(engine, "chat", side_effect=seq):
            rec = Recorder(self.tmp / "runs", "t")
            s = engine.run(self.prof(max_steps=30, max_consecutive_errors=3), self.backend, "do", rec)
        self.assertEqual(s["stop_reason"], "repeated_failure")
        self.assertEqual(s["steps"], 11)              # 6번째 같은 실패(단계 11)에서 중단
        self.assertEqual(s["repeat_nudges"], 4)       # 3·4·5·6번째 실패에 경고
        steps = [json.loads(l) for l in (rec.dir / "events.jsonl").read_text().splitlines() if '"kind": "step"' in l]
        self.assertIn("[엔진] 똑같은 호출이 3번", steps[4]["response"]["tool_calls"][0]["result"])

    def test_korean_report(self):
        seq = [_resp(content="All tests passed after fixing `calc/stats.py`."),
               _resp(content="`calc/stats.py`의 중앙값 계산을 고쳐 모든 테스트가 통과했다.")]
        with mock.patch.object(engine, "chat", side_effect=seq):
            s = engine.run(self.prof(language="ko"), self.backend, "do", Recorder(self.tmp / "runs", "t"))
        self.assertEqual((s["stop_reason"], s["steps"], s["language_retries"]), ("completed", 2, 1))
        self.assertGreater(engine.hangul_ratio("`calc/x.py`를 고쳤다 OK"), 0.5)

    def test_korean_json_fields(self):
        schema = {"type": "object", "properties": {"items": {"type": "array"}}}
        seq = [_resp(content='{"items": [{"file": "a.mkv", "reason": "Movie with year."}]}'),
               _resp(content='{"items": [{"file": "a.mkv", "reason": "연도가 있는 영화다."}]}')]
        with mock.patch.object(engine, "chat", side_effect=seq):
            s = engine.run(self.prof(language="ko", language_fields=["reason"], output_format="json", output_schema=schema),
                           self.backend, "do", Recorder(self.tmp / "runs", "t"))
        self.assertEqual((s["stop_reason"], s["language_retries"]), ("completed", 1))


class TMedia(unittest.TestCase):
    def test_rules(self):
        from stewart.media import classify, original_name
        cases = {
            "엔젤 비트 (2010) - S01E12 - [[Blu-ray]엔젤비트! 12 (BD 1904x1072 H.264 AAC)].mkv": "Anime/엔젤 비트 (2010)/Season 01",
            "케이온! (2009) - S00E06 - [K-ON! - B-Side - 06 (BDRIP 1920x1080 H264 AAC chap)].mp4": "Anime/케이온! (2009)/Season 00",
            "리갈 하이 (2012) - S01E11 - [Legal.High.E11end.720p.HDTV.x264.AAC-YYeTs].mkv": "Shows/리갈 하이 (2012)/Season 01",
            "하나와 앨리스 살인사건 (2015).mp4": "Movies/하나와 앨리스 살인사건 (2015)",
            "Dune.Part.Two.2024.2160p.mkv.part": "_incomplete/",
            "Thumbs.db": "_junk/",
            "Rock Show - Halestorm.mp3": "Music/일반",
            "02 - Bodies.mp3": "Music/",
            "cover.jpg": "Music/",
            "103.jpg": "_review/",
        }
        for name, dest in cases.items():
            r, _ = classify(name)
            self.assertEqual(r and r["destination"], dest, name)
        for name in ("ep05.mp4", "CDTV - インフルエンサー (2017) - [170319 COUNTDOWN TV].ts",
                     "[Yousei-raws] K-On!! 12 (Audio commentary 1).flac", "Okitegami Kyoko no Biboroku EP02 720p HDTV.mkv"):
            self.assertIsNone(classify(name)[0], name)
        self.assertEqual(original_name("엔젤 비트 (2010) - S01E12 - [[Blu-ray]엔젤비트! 12 (BD)].mkv"), "[Blu-ray]엔젤비트! 12 (BD).mkv")
        self.assertEqual(original_name("[Moozzi2] Ore no Imouto - 01 (BD).mkv"), "[Moozzi2] Ore no Imouto - 01 (BD).mkv")

    def test_destination_check(self):
        from stewart.media import check_destination
        bad = ["Shows/Unknown Series/Season 05/", "Music/Unknown Artist/Singles/", "Music/알 수 없는 아티스트/Singles/",
               "Movies/Untitled (2020)/", "Library/x/", "_review/Unknown/", "", "Anime/기타/Season 01/", "TV/x/"]
        ok = ["Music/일반", "Music/", "Music/아이유/Singles/", "Comics/작품/1", "_review/",
              "Shows/The Other Two (2019)/Season 01/"]
        for d in bad:
            self.assertIsNotNone(check_destination(d), d)
        for d in ok + ["Music/기타 연주곡/Singles/", "TV/Other/Season 01/"[:0] + "Shows/Others Show/Season 01/"]:
            self.assertIsNone(check_destination(d), d)
        self.assertIsNotNone(check_destination("Shows/Other/Season 01/"))  # 폴더 이름 전체가 other면 걸림

    def test_prune_old(self):
        import os, tempfile
        from stewart import cli
        root = Path(tempfile.mkdtemp())
        work = root / "calc"
        work.mkdir()
        for t in ("20261106-100000", "20261106-110000", "20261106-120000", "20261106-130000", "20261106-140000"):
            (root / f"calc.old-{t}").mkdir()
        (root / "calc.old-keepme").mkdir()
        with mock.patch.object(cli, "PRUNE_ROOT", root):
            gone = cli.prune_old(work, 3)
        self.assertEqual([p.name for p in gone], ["calc.old-20261106-100000", "calc.old-20261106-110000"])
        self.assertEqual(sorted(os.listdir(root)), ["calc", "calc.old-20261106-120000", "calc.old-20261106-130000",
                                                    "calc.old-20261106-140000", "calc.old-keepme"])
        with mock.patch.object(cli, "PRUNE_ROOT", root / "elsewhere"):
            self.assertEqual(cli.prune_old(work, 0), [])  # 캐시 루트 밖이면 지우지 않음

    def test_score(self):
        from stewart.media import score
        items = [{"file": "a", "destination": "Movies/A (2010)/", "source": "rule"},
                 {"file": "b", "destination": "Shows/B/Season 01/", "source": "model"},
                 {"file": "c", "destination": "_review/", "source": "model"}]
        truth = ["/srv/library/Movies/a (2010)", "Shows/B/Season 02", "Comics/C"]
        sc = score(items, truth)
        self.assertEqual(sc["by_source"]["전체"], {"n": 3, "exact": 1, "top": 2, "review": 1, "wrong": 1})
        self.assertEqual([r["file"] for r in sc["rows"] if not r["exact"] and not r["review"]], ["b"])
        ind = __import__("stewart.media", fromlist=["indeterminate"]).indeterminate(
            ["cover.jpg", "cover.jpg", "x.mp3", "12.jpg"], ["Music/a", "Music/b", "Music/c", "Comics/d"])
        self.assertEqual(ind, ["같은 이름에 정답이 여럿", "같은 이름에 정답이 여럿", None, "일반 이름 이미지"])

    def test_run_media_end_to_end(self):
        from stewart.config import load_profile
        from stewart.folders import FolderIndex
        from stewart.media import run_media
        tmp = Path(tempfile.mkdtemp())
        inp = tmp / "in.tsv"
        inp.write_text("2017-10-02 ep124 - [171002 乃木坂工事中 ep124].ts\tVariety/乃木坂46/乃木坂工事中\n"
                       "nanase7-1629-original-002.jpg\tPhotos/乃木坂46/西野七/짤\nmystery.mkv\tMovies/X (2001)\nThumbs.db\t_junk\n", encoding="utf-8")
        idx = FolderIndex(["Variety/乃木坂46/乃木坂工事中", "Photos/乃木坂46/西野七/짤"])
        answer = json.dumps({"items": [
            {"file": "2017-10-02 ep124 - [171002 乃木坂工事中 ep124].ts", "destination": "Variety/乃木坂46/乃木坂工事中",
             "reason": "후보와 같다.", "confidence": 0.9},
            {"file": "nanase7-1629-original-002.jpg", "destination": "_junk/", "reason": "쓸모없는 이미지다.", "confidence": 0.6},
            {"file": "mystery.mkv", "destination": "Movies/<제목> (<연도>)", "reason": "영화 같다.", "confidence": 0.4}]},
            ensure_ascii=False)
        prompts = []

        def fake_chat(backend, messages, tools=None, rf=None):
            prompts.append(messages[-1]["content"])
            return _resp(content=answer)

        prof = load_profile("media")
        with mock.patch.object(engine, "chat", side_effect=fake_chat), mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("STEWART_SEARXNG_URL", None)
            run_media(prof, Backend("fake", "http://x/v1", "m", "fp16", 4096), inp, tmp / "runs", 10, False,
                      folders=idx, k=5)
        self.assertIn("기존 폴더 후보: Variety/乃木坂46/乃木坂工事中", prompts[0])
        self.assertNotIn("web_search", prompts[0])
        out = json.loads(next((tmp / "runs").glob("media-*/result.json")).read_text())
        it = {x["file"]: x for x in out["items"]}
        self.assertEqual(it["nanase7-1629-original-002.jpg"]["destination"], "_review/")          # 모델 _junk 금지
        self.assertEqual(it["nanase7-1629-original-002.jpg"]["source"], "model→review")
        self.assertEqual(it["mystery.mkv"]["destination"], "_review/")          # 꺾쇠 자리 표시
        self.assertEqual(it["Thumbs.db"]["destination"], "_junk/")              # 규칙은 _junk 가능
        self.assertFalse(it["2017-10-02 ep124 - [171002 乃木坂工事中 ep124].ts"]["new_folder"])
        sc = json.loads(next((tmp / "runs").glob("media-*/score.json")).read_text())
        self.assertEqual(sc["by_source"]["전체"]["exact"], 2)


class TWeb(unittest.TestCase):
    """가짜 SearXNG(127.0.0.1)로 web_search·파일당 상한·기록 확인."""

    def setUp(self):
        import http.server
        import threading
        self.queries = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                from urllib.parse import parse_qs, urlparse
                if urlparse(self.path).path == "/quota.json":     # brave 셈이 없는 SearXNG: 404, 셈에 안 넣음
                    self.send_response(404)
                    self.end_headers()
                    return
                q = parse_qs(urlparse(self.path).query)
                outer.queries.append((urlparse(self.path).path, q.get("q", [""])[0], q.get("format", [""])[0],
                                      q.get("categories", [""])[0]))
                body = json.dumps({"results": [
                    {"title": f"결과 {i}", "url": f"https://example.org/{i}?q={q.get('q', [''])[0]}",
                     "content": "요약 " * 200, "engine": "x", "extra": "버림"} for i in range(8)]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        self.srv.shutdown()

    def test_search_loop(self):
        schema = {"type": "object", "properties": {"items": {"type": "array"}}}
        prof = Profile(name="m", path=self.tmp / "p.toml", system="s", tools=["web_search"], workdir=self.tmp,
                       max_steps=10, output_format="json", output_schema=schema,
                       web_search={"url": self.url, "max_per_file": 2, "max_results": 3, "categories": "general,videos"})
        ws = lambda q, f: ("web_search", json.dumps({"query": q, "for_file": f}, ensure_ascii=False))
        seq = [_resp(calls=[ws("nogizaka 工事中", "a.ts"), ws("嫉妬の権利", "b.ts")]),
               _resp(calls=[ws("乃木坂工事中 ep124", "a.ts")]),      # a 두 번째(상한 2)
               _resp(calls=[ws("乃木坂46 工事中", "a.ts")]),          # a 세 번째 → 상한 거부
               _resp(content='{"items": []}')]
        captured = []

        def fake_chat(backend, messages, tools=None, rf=None):
            captured.append(([t["function"]["name"] for t in tools or []], messages[-1]))
            return seq[len(captured) - 1]

        with mock.patch.object(engine, "chat", side_effect=fake_chat):
            rec = Recorder(self.tmp / "runs", "m")
            s = engine.run(prof, Backend("fake", "http://x/v1", "m", "fp16", 4096), "do", rec)
        self.assertEqual(captured[0][0], ["web_search"])
        self.assertEqual(len(self.queries), 3)                     # 상한 거부는 서버에 안 감
        self.assertEqual(self.queries[0], ("/search", "nogizaka 工事中", "json", "general,videos"))
        self.assertEqual([e["query"] for e in s["web_searches"]], ["nogizaka 工事中", "嫉妬の権利", "乃木坂工事中 ep124"])
        self.assertEqual(len(s["web_searches"][0]["urls"]), 3)     # max_results
        self.assertEqual(len(s["web_result_urls"]), 9)
        tool_msg = captured[1][1]["content"]
        self.assertIn("1. 결과 0", tool_msg)
        self.assertNotIn("버림", tool_msg)                          # 제목·주소·요약문만
        self.assertLessEqual(max(len(l) for l in tool_msg.splitlines()), 310)
        self.assertIn("상한(2회)", captured[3][1]["content"])
        ev = [json.loads(l) for l in (rec.dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual(sum(1 for e in ev if e["kind"] == "web_search"), 3)

    def test_web_candidates(self):
        """후보가 약한 파일만 검색하고, 결과 낱말로 후보를 다시 뽑는다."""
        from stewart.folders import FolderIndex
        from stewart.media import search_candidates
        from stewart.websearch import SearxClient, make_queries
        idx = FolderIndex(["Variety/乃木坂46/乃木坂って、どこ？", "Variety/乃木坂46/乃木坂工事中", "Music/Doko Band/Album"])
        client = SearxClient(self.url, "general,videos", min_interval_s=0)
        logs = []
        # 가짜 서버는 "결과 i" 제목만 준다 → 약한 후보 그대로, 검색 3번
        cands, info = search_candidates(idx, client, "120819 Nogizaka46 - Nogizakatte Doko ep46 (1280x720 x264).mp4",
                                        "120819 Nogizaka46 - Nogizakatte Doko ep46", 5, 3, logs.append)
        self.assertTrue(info["weak"])
        self.assertEqual(len(info["searches"]), 3)
        self.assertEqual(len(logs), 3)
        self.assertEqual(self.queries[0][3], "general,videos")
        self.assertEqual(client.stats["requests"], 3)
        # 강한 후보는 검색하지 않는다
        cands, info = search_candidates(idx, client, "171002 乃木坂工事中 ep124.ts", "171002 乃木坂工事中 ep124", 5, 3, logs.append)
        self.assertEqual((info["weak"], info["searches"]), (False, []))
        self.assertEqual(cands[0], "Variety/乃木坂46/乃木坂工事中")
        self.assertEqual(make_queries("120819 Nogizaka46 - Nogizakatte Doko ep46 (1280x720 x264).mp4")[:2],
                         ["Nogizaka46 - Nogizakatte Doko", "Nogizakatte Doko"])

    def test_result_terms_improve(self):
        from stewart.folders import FolderIndex
        from stewart.websearch import result_terms
        idx = FolderIndex(["Variety/乃木坂46/乃木坂って、どこ？", "Music/Doko Band/Album"])
        res = [{"title": "Nogizakatte, Doko? (乃木坂って、どこ??)", "content": "乃木坂46's TV show"},
               {"title": "乃木坂って、どこ？ - Wikipedia", "content": "乃木坂46の冠番組"}]
        t = result_terms(res)
        sc = idx.scored("Nogizakatte Doko " + t, k=3)
        self.assertEqual(sc[0][0], "Variety/乃木坂46/乃木坂って、どこ？")
        self.assertTrue(sc[0][2])

    def test_429_retry(self):
        import http.server, threading
        hits = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(1)
                if len(hits) == 1:
                    self.send_response(429); self.end_headers(); return
                body = json.dumps({"results": [{"title": "t", "url": "u", "content": "c", "engines": ["naver"]}],
                                   "unresponsive_engines": [["google", "access denied"]]}).encode()
                self.send_response(200); self.end_headers(); self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        from stewart.websearch import SearxClient
        c = SearxClient(f"http://127.0.0.1:{srv.server_port}", min_interval_s=0, retry_wait_s=0.01)
        r = c.search("x")
        srv.shutdown()
        self.assertEqual((len(r["results"]), c.stats["http_429"], c.stats["requests"]), (1, 1, 2))
        self.assertEqual([x["result"] for x in r["attempts"]], ["http_429", "ok"])
        self.assertTrue(all(x["end_ms"] >= x["start_ms"] for x in r["attempts"]))
        self.assertGreater(r["attempts"][1]["bytes"], 0)
        self.assertEqual(c.summary()["results_by_engine"], {"naver": 1})
        self.assertEqual(c.summary()["unresponsive"], {"google: access denied": 1})
        cache = Path(tempfile.mkdtemp()) / "c.jsonl"
        c2 = SearxClient("http://127.0.0.1:1", min_interval_s=0, cache_path=cache)   # 닿지 않는 주소
        c2.cache[json.dumps(["x", "", 8], ensure_ascii=False)] = r
        self.assertTrue(c2.search("x")["cached"])
        self.assertEqual(c2.stats["requests"], 0)

    def test_groups_and_generic(self):
        from stewart.folders import FolderIndex
        from stewart.media import _generic_image, _genre
        from stewart.websearch import korean_query, make_queries, result_terms
        idx = FolderIndex(["Concerts/乃木坂46", "Clips/乃木坂46", "MusicVideos/乃木坂46", "Variety/乃木坂46/乃木坂工事中",
                           "Concerts/ITZY", "Concerts/IU", "Concerts/嵐×乃木坂46", "Clips/_미분류", "Music/IU/Album"])
        self.assertEqual(idx.groups["乃木坂46"], ["Concerts/乃木坂46", "Clips/乃木坂46", "MusicVideos/乃木坂46", "Variety/乃木坂46"])
        self.assertNotIn("_미분류", idx.groups)
        self.assertEqual(idx.find_groups("CDTV インフルエンサー 乃木坂46 乃木坂46 ITZY")[:2], ["乃木坂46", "ITZY"])
        self.assertEqual(idx.find_groups("Opinion piece about biu values"), [])      # 짧은 로마자 오탐 없음
        self.assertEqual(idx.find_groups("아이유 IU 콘서트"), ["IU"])
        self.assertEqual(idx.find_groups("itzy 직캠"), ["ITZY"])
        for n in ("207", "IMG16", "Scan_0007", "be3034e2-aa7f-4675-b270-f76b5641b28b", "16 copy", "다운로드 (2) (7)",
                  "ë¦Äî2", "MM_4217", "Screenshot_2016-04-23-19-36-37"):
            self.assertTrue(_generic_image(n), n)
        for n in ("NOGIBINGO!6 31", "TokyoWalker 2014 No.24 21", "nanase7-1629-original-002", "cover", "SKE12", "Café Ölmühle"):
            self.assertFalse(_generic_image(n), n)
        self.assertEqual(_genre("[Zero-Raws] Saki - 24 (BD 1280x720 x264 AAC).mkv"), "애니")
        self.assertEqual(_genre("Legal.High.E11end.720p.HDTV.x264.AAC-YYeTs.mkv"), "드라마")
        self.assertEqual(make_queries("Charlotte - 05 (BD 1280x720 x264 AACx2).mp4")[0], "Charlotte")
        self.assertEqual(korean_query("Charlotte - 05 (BD 1280x720 x264 AACx2).mp4", "애니"), "Charlotte 애니")
        self.assertEqual(korean_query("Charlotte - 05.mp4", ""), "")
        q = korean_query("[Moozzi2] Ore no Imouto S1 [SP04] Character Commentary Video - 01 (BD 1920x1080 x.264 Flac).mkv", "애니")
        self.assertTrue(q.startswith("Ore no Imouto") and q.endswith(" 애니"), q)
        t = result_terms([{"title": "샬롯 (애니메이션) - 나무위키", "content": "Charlotte is an anime"},
                          {"title": "Charlotte (TV series)", "content": "P.A.Works"}])
        self.assertIn("샬롯", t.split())

    def test_media_rules_generic_image(self):
        from stewart.media import classify
        r, _ = classify("MM_4217.jpg")
        self.assertEqual((r["destination"], r["rule"]), ("_review/", "image-generic"))
        self.assertIsNone(classify("NOGIBINGO!6 31.jpg")[0])

    def test_incomplete_read_logged(self):
        import http.server, threading
        n = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                n.append(1)
                body = json.dumps({"results": [{"title": "t", "url": "u", "content": "c" * 500}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body) + (1000 if len(n) == 1 else 0)))
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
                self.close_connection = True

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        from stewart.websearch import SearxClient
        c = SearxClient(f"http://127.0.0.1:{srv.server_port}", min_interval_s=0)
        r = c.search("x")
        srv.shutdown()
        self.assertEqual([x["result"] for x in r["attempts"]], ["IncompleteRead", "retry_ok"])
        self.assertEqual(r["attempts"][0]["expected_more"], 1000)
        self.assertEqual(r["attempts"][0]["content_length"], r["attempts"][0]["bytes"] + 1000)

    def test_episode_season_fix(self):
        from stewart.folders import FolderIndex
        from stewart.media import episode_numbers, fix_episode_season
        idx = FolderIndex(["Anime/엔젤 비트 (2010)/Season 01", "Anime/엔젤 비트 (2010)/extras",
                           "Shows/나의 아저씨 (2018)/Season 01", "Anime/케이온! (2009)/Season 01", "Anime/케이온! (2009)/Season 02"])
        self.assertEqual(episode_numbers("[Blu-ray]엔젤비트! 12 (BD 1904x1072 H.264 AAC).mkv"), {12})
        self.assertIn(9, episode_numbers("나의 아저씨.E09.180418.1080p-TEA.mp4"))
        self.assertEqual(fix_episode_season("Anime/엔젤 비트 (2010)/Season 12", "[Blu-ray]엔젤비트! 12 (BD 1904x1072).mkv", idx)[0],
                         "Anime/엔젤 비트 (2010)/Season 01")
        self.assertEqual(fix_episode_season("Shows/나의 아저씨 (2018)/Season 09/", "나의 아저씨.E09.180418.1080p-TEA.mp4", idx)[0],
                         "Shows/나의 아저씨 (2018)/Season 01")
        self.assertEqual(fix_episode_season("Anime/케이온! (2009)/Season 06", "K-ON! - 06 (BD).mp4", idx)[0], "_review/")
        self.assertIsNone(fix_episode_season("Anime/엔젤 비트 (2010)/Season 01", "엔젤비트! 01.mkv", idx))   # 이미 있음
        self.assertIsNone(fix_episode_season("Anime/엔젤 비트 (2010)/Season 02", "엔젤비트! 12.mkv", idx))   # 회차와 다름
        self.assertIsNone(fix_episode_season("Anime/새 작품 (2020)/Season 03", "새 작품 03.mkv", idx))       # 작품 폴더 없음

    def test_gzip_and_truncation_retries(self):
        import gzip as gz, http.server, threading
        n = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                n.append(self.headers.get("Accept-Encoding"))
                raw = json.dumps({"results": [{"title": "乃木坂46", "url": "u", "content": "c" * 2000}]}).encode()
                body = gz.compress(raw)
                cut = len(n) <= 3                                  # 처음 3번은 끊어 보낸다
                self.send_response(200)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body[: len(body) // 2] if cut else body)
                self.wfile.flush()
                self.close_connection = True

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        from stewart.websearch import SearxClient
        c = SearxClient(f"http://127.0.0.1:{srv.server_port}", min_interval_s=0)
        c.TRUNC_WAITS = (0.01, 0.01, 0.01)
        r = c.search("x")
        self.assertEqual(r["error"], None)
        self.assertEqual([x["result"] for x in r["attempts"]], ["IncompleteRead"] * 3 + ["retry_ok"])
        self.assertEqual(n[0], "gzip")
        last = r["attempts"][-1]
        self.assertEqual(last["encoding"], "gzip")
        self.assertLess(last["bytes"], last["decoded_bytes"])
        self.assertEqual(r["results"][0]["title"], "乃木坂46")
        n.clear()
        n.extend([0, 0, 0, 0, 0, 0])                                # 이제 계속 끊긴다고 치고: 4번 다 실패
        H_cut = H.do_GET

        def always_cut(self):
            n.append(1)
            body = gz.compress(b'{"results": []}' * 50)
            self.send_response(200); self.send_header("Content-Length", str(len(body) + 10)); self.end_headers()
            self.wfile.write(body); self.wfile.flush(); self.close_connection = True

        H.do_GET = always_cut
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        c2 = SearxClient(f"http://127.0.0.1:{srv.server_port}", min_interval_s=0)
        c2.TRUNC_WAITS = (0.01, 0.01, 0.01)
        r2 = c2.search("y")
        srv.shutdown()
        H.do_GET = H_cut
        self.assertIsNotNone(r2["error"])                           # 그 검색만 오류로 남기고 계속
        self.assertEqual(len(r2["attempts"]), 4)

    def test_disabled_without_url(self):
        prof = Profile(name="m", path=self.tmp / "p.toml", system="s", tools=["read_file", "web_search"], workdir=self.tmp,
                       web_search={"url_env": "STEWART_TEST_NO_SUCH_ENV"})
        seen = []

        def fake_chat(backend, messages, tools=None, rf=None):
            seen.append([t["function"]["name"] for t in tools or []])
            return _resp(content="끝났다")

        with mock.patch.object(engine, "chat", side_effect=fake_chat):
            s = engine.run(prof, Backend("fake", "http://x/v1", "m", "fp16", 4096), "do", Recorder(self.tmp / "r", "m"))
        self.assertEqual(seen[0], ["read_file"])
        self.assertEqual(engine.web_search_enabled(prof), 0)


class TFolders(unittest.TestCase):
    def test_index(self):
        from stewart.folders import FolderIndex
        idx = FolderIndex(["Variety/乃木坂46/乃木坂工事中", "Variety/乃木坂46/NOGIBINGO!", "Concerts/乃木坂46",
                           "Anime/케이온! (2009)/Season 01", "Anime/케이온! (2009)/extras", "Music/일반", "Music/GLAY/Album"])
        self.assertTrue(idx.exists("variety/乃木坂46/乃木坂工事中/"))
        self.assertTrue(idx.exists("Anime/케이온! (2009)"))        # 부모 폴더도 들어간다
        self.assertFalse(idx.exists("Variety/乃木坂工事中"))
        c = idx.candidates("2017-10-02 ep124 - [171002 乃木坂工事中 ep124].ts")
        self.assertEqual(c[0], "Variety/乃木坂46/乃木坂工事中")
        self.assertIn("Variety/乃木坂46/NOGIBINGO!", idx.candidates("130723 NOGIBINGO! 04.mkv", k=3))
        self.assertEqual(idx.candidates("zzz qqq.mp4"), [])
        self.assertLessEqual(len(idx.candidates("乃木坂46", k=2)), 2)

    def test_checks(self):
        from stewart.media import check_destination
        self.assertIsNotNone(check_destination("Movies/<제목> (<연도>)"))
        self.assertIsNotNone(check_destination("Concerts/<그룹>"))
        self.assertIsNone(check_destination("Music/「H△G × Mili」vol.2"))


if __name__ == "__main__":
    unittest.main()
