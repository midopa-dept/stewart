"""판 104(SW-45 ④·A): 작업 폴더 파일을 허브에서 열기·내려받기 — /api/file(로그인·@파일 경계: 밖·..·링크 밖·.git·.gitignore·비밀 꼴 거부,
글은 text/plain·그림은 그대로·HTML·SVG·모르는 꼴은 내려받기만, 상한), 차례의 바뀐 파일 줄에 paths, 끝 알림에 "파일 N개"."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import fswalk
from tests import test_hub_ui as HU
from tests.test_u70 import png


class TPathOk(unittest.TestCase):
    def test_rules(self):
        root = Path(tempfile.mkdtemp())
        (root / "docs").mkdir()
        (root / "docs" / "r.md").write_text("# 보고", encoding="utf-8")
        (root / ".gitignore").write_text("build/\n*.tmp\n", encoding="utf-8")
        (root / "build").mkdir()
        (root / "build" / "x.txt").write_text("x", encoding="utf-8")
        (root / "a.tmp").write_text("x", encoding="utf-8")
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("x", encoding="utf-8")
        out = Path(tempfile.mkdtemp()) / "secret.txt"
        out.write_text("밖", encoding="utf-8")
        (root / "link.txt").symlink_to(out)
        self.assertEqual(fswalk.path_ok(root, "docs/r.md"), (root / "docs" / "r.md").resolve())
        for bad in ("build/x.txt", "a.tmp", ".git/config", "link.txt", "../x", "/etc/hosts", "docs", "없음.md", ""):
            self.assertIsNone(fswalk.path_ok(root, bad), bad)
        self.assertIsNone(fswalk.path_ok(root, "docs/r.md", is_secret=lambda p: p.name == "r.md"))


class TWeb(HU.TWebSessions):
    locals().update({k: None for k in dir(HU.TWebSessions) if k.startswith("test")})

    def get(self, q, auth=True):
        return self.req("GET", "/api/file?" + q, headers={"Cookie": self.ck} if auth else {})

    def test_serve(self):
        wd = Path(self.main.sess.env.workdir)
        (wd / "보고서.md").write_text("# 결과\n표", encoding="utf-8")
        (wd / "page.html").write_text("<script>alert(1)</script>", encoding="utf-8")
        (wd / "p.png").write_bytes(png())
        (wd / ".env").write_text("K=1", encoding="utf-8")
        r, body = self.get("path=%EB%B3%B4%EA%B3%A0%EC%84%9C.md")
        self.assertEqual((r.status, r.getheader("Content-Type")), (200, "text/plain; charset=utf-8"))
        self.assertIn("inline", r.getheader("Content-Disposition"))
        self.assertIn("sandbox", [v for k, v in r.getheaders() if k.lower() == "content-security-policy"])   # 두 CSP 가 함께 걸린다
        self.assertEqual(body.decode("utf-8"), "# 결과\n표")
        r, _ = self.get("path=page.html")
        self.assertEqual((r.status, r.getheader("Content-Type")), (200, "application/octet-stream"))   # HTML 은 내려받기만
        self.assertIn("attachment", r.getheader("Content-Disposition"))
        self.assertEqual(self.get("path=p.png")[0].getheader("Content-Type"), "image/png")
        self.assertIn("attachment", self.get("path=p.png&dl=1")[0].getheader("Content-Disposition"))
        self.assertEqual(self.get("path=.env")[0].status, 404)                       # 비밀 꼴
        self.assertEqual(self.get("path=../x")[0].status, 404)
        self.assertNotEqual(self.get("path=p.png", auth=False)[0].status, 200)     # 로그인 없이는 안 됨
        with mock.patch.object(type(self.web), "FILE_MAX", 3):
            self.assertEqual(self.get("path=p.png")[0].status, 413)

    def test_files_line_and_notify(self):
        ui = self.main.ui
        ui.turn_files(["a.py", "docs/r.md"])
        e = [m for m in self.main.state()["main_log"] if m["kind"] == "files"][-1]
        self.assertEqual(e["paths"], ["a.py", "docs/r.md"])
        sent = []
        self.hub._notify_on = True
        with mock.patch.object(self.hub, "_notify", side_effect=sent.append), mock.patch.dict(os.environ, {"STEWART_BOARD_QUIET": "0"}):
            self.main._notify_turn_end("answered", 120)
        self.assertIn("파일 2개", sent[0])


if __name__ == "__main__":
    unittest.main()
