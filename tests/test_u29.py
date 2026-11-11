"""U2-9 새 화면: 허브가 빌드 결과(web_ui/)를 주는지, 옛 화면 되돌릴 길, 빌드가 소스와 맞는지, 작업 카드·토큰 자료."""

import hashlib
import http.client
import json
import re
import unittest
from pathlib import Path
from urllib.parse import urlencode

from stewart.web import WebApp, make_token
from tests.test_hub import msg
from tests.test_hub3 import MainBase

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
OUT = ROOT / "src" / "stewart" / "web_ui"


def source_hash() -> str:
    """ui/stamp.mjs 와 같은 셈."""
    files = ["index.html", "vite.config.js", "package.json", "package-lock.json"]
    files += [p.relative_to(UI).as_posix() for p in (UI / "src").rglob("*") if p.is_file()]
    h = hashlib.sha256()
    for f in sorted(files):
        h.update(f.encode() + b"\0")
        h.update((UI / f).read_bytes())
        h.update(b"\0")
    return h.hexdigest()


class TBuild(unittest.TestCase):
    def test_build_output_present_and_offline(self):
        idx = (OUT / "index.html").read_text(encoding="utf-8")
        self.assertIn('src="/ui/app.js"', idx)
        self.assertIn('href="/ui/app.css"', idx)
        self.assertIn('name="csrf" content="__CSRF__"', idx)
        for name in ("index.html", "app.js", "app.css"):
            t = (OUT / name).read_text(encoding="utf-8")       # 글 파일(텍스트)
            for bad in ("https://cdn", "fonts.googleapis", "unpkg.com", "jsdelivr"):
                self.assertNotIn(bad, t, f"{name} 에 바깥 주소 {bad}")

    @unittest.skipUnless((UI / "package.json").is_file(), "ui 소스 없음(설치본)")
    def test_build_matches_sources(self):
        """ui/ 를 고치고 빌드(npm run build)를 잊으면 여기서 걸린다."""
        self.assertEqual((OUT / "SOURCE_HASH").read_text().strip(), source_hash(),
                         "ui/ 소스가 빌드 결과와 다르다: cd ui && npm run build 후 web_ui/ 를 함께 커밋")


class TServe(MainBase):
    def setUp(self):
        super().setUp()
        self.token = make_token(self.hub.dir)
        self.web = WebApp(self.main, self.hub, self.token)
        self.web.serve()
        self.addCleanup(self.web.close)
        self.host = f"127.0.0.1:{self.web.port}"
        r, _ = self.req("POST", "/login", urlencode({"token": self.token}),
                        {"Content-Type": "application/x-www-form-urlencoded", "Origin": f"http://{self.host}"})
        self.ck = r.getheader("Set-Cookie").split(";")[0]

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=30)
        c.request(method, path, body=body, headers={"Host": self.host, **(headers or {})})
        r = c.getresponse()
        d = r.read()
        c.close()
        return r, d

    def test_new_ui_default_and_old_fallback(self):
        r, page = self.req("GET", "/", headers={"Cookie": self.ck})
        self.assertIn(b'src="/ui/app.js"', page)
        self.assertNotIn(b"__CSRF__", page)
        r, old = self.req("GET", "/?ui=old", headers={"Cookie": self.ck})
        self.assertIn(b'id="askbar"', old)                        # 옛 한 파일 화면(되돌릴 길)
        r, js = self.req("GET", "/ui/app.js", headers={"Cookie": self.ck})
        self.assertEqual((r.status, r.getheader("Content-Type")), (200, "text/javascript; charset=utf-8"))
        self.assertGreater(len(js), 10000)
        r, _ = self.req("GET", "/ui/app.css", headers={"Cookie": self.ck})
        self.assertEqual(r.getheader("Content-Type"), "text/css; charset=utf-8")
        for bad in ("/ui/index.html", "/ui/SOURCE_HASH", "/ui/../web.py", "/ui/x.js"):
            self.assertEqual(self.req("GET", bad, headers={"Cookie": self.ck})[0].status, 404, bad)
        self.assertEqual(self.req("GET", "/ui/app.js")[0].status, 401)          # 로그인 전엔 안 준다

    def test_state_tokens_pending_backend(self):
        st = self.main.state()
        self.assertEqual(st["tokens"]["window"], 32768)
        self.assertEqual(st["pending"], [])
        self.assertEqual(st["backend"]["model"], "m")
        self.assertIsNone(st["resume_note"])
        self.r.scripts["MAIN"] = [msg(calls=[("read_file", {"path": "없는.txt"})]), msg("없다")]
        self.main.send("읽어")
        self.assertTrue(self.main.wait_idle())
        st = self.main.state()
        self.assertIn("turn", st["tokens"])
        self.assertGreaterEqual(st["tokens"]["turn"]["steps"], 1)
        json.dumps(st)                                                  # SSE 로 보낼 수 있다

    def test_tool_cards_ok_and_same_error_count(self):
        bad = ("read_file", {"path": "없는.txt"})
        self.r.scripts["MAIN"] = [msg(calls=[bad]), msg(calls=[("read_file", {"path": "없는2.txt"})]), msg(calls=[bad]),
                                  msg(calls=[("run_shell", {"command": "echo hi"})]), msg("끝")]
        self.main.send("여러 번")
        self.assertTrue(self.main.wait_idle())
        tools = [m for m in self.main.state()["main_log"] if m["kind"] == "tool"]
        self.assertEqual([t.get("ok") for t in tools], [False, False, False, True])
        self.assertTrue(all("elapsed" in t for t in tools))
        reps = [t.get("rep") for t in tools[:3]]
        self.assertEqual(reps[0], 1)
        self.assertEqual(reps[2], 2)                        # 같은 도구·같은 오류 첫 줄 → 2번째

    def test_early_return_tools_also_marked(self):
        """없는 도구·인자 파싱 실패처럼 일찍 끝나는 길도 ✓/✕ 가 붙는다(tool_done 은 모든 길에서)."""
        self.r.scripts["MAIN"] = [msg(calls=[("없는_도구", {})]), msg("끝")]
        self.main.send("해")
        self.assertTrue(self.main.wait_idle())
        tools = [m for m in self.main.state()["main_log"] if m["kind"] == "tool"]
        self.assertEqual([t.get("ok") for t in tools], [False])



class TPickPublic(unittest.TestCase):
    """공개판(판 59): 화면은 new·old 둘. 옛 Chromium 판(legacy)은 따로 가지(airgap/main)에만 — 브라우저 판으로 고르지 않는다."""
    def test_pick(self):
        from stewart.web import UI_DIRS, ui_kind
        edge92 = "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/92.0.4515.131 Safari/537.36 Edg/92.0.902.67"
        self.assertEqual(ui_kind("", edge92), "new")
        self.assertEqual(ui_kind("legacy", edge92), "new")
        self.assertEqual(ui_kind("old", edge92), "old")
        self.assertEqual(ui_kind("", edge92, "old"), "old")
        self.assertEqual(list(UI_DIRS), ["new"])
        self.assertFalse((ROOT / "src" / "stewart" / "web_ui_legacy").exists())


if __name__ == "__main__":
    unittest.main()

