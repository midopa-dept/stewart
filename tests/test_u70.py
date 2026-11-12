"""판 91: 도구가 본 그림(view_image 처럼 그림을 돌려주는 도구)을 허브 작업 카드에 — chat 이 ui.tool_images 로 넘기고,
허브는 이름만 기록에 달고 /api/img 로 그 화면이 받은 그림만 내준다(이름 꼴·로그인·없는 이름 404)."""

import json
import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from stewart import chat as C
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests import test_hub_ui as HU
from tests.test_cloud import FakeUI
from tests.test_hub import msg
from tests.test_u60 import resp


def png(w=10, h=10) -> bytes:
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class UI(FakeUI):
    def __init__(self):
        super().__init__()
        self.got = []

    def tool_images(self, paths):
        self.got.append(paths)


class TChatHook(unittest.TestCase):
    def test_view_image_reaches_ui_and_point(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "a.png").write_bytes(png())
        p = Profile(name="chat", path=tmp / "p.toml", system="s", tools=["view_image"], workdir=tmp, max_steps=4)
        ui = UI()
        s = C.ChatSession(p, Backend("f", "http://x/v1", "m", "q", 100000, vision=True), Recorder(tmp / "runs", "c"), ui,
                          stream=False, mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        seq = [resp(calls=[("view_image", {"path": "a.png"})]), resp("빨간 그림이다")]
        with mock.patch.object(C, "chat", side_effect=lambda *a, **k: seq.pop(0)):
            s.turn("a.png 를 봐")
        (paths,) = ui.got
        self.assertTrue(paths[0].endswith(".png") and Path(paths[0]).is_file())


class TWebImg(HU.TWebSessions):
    locals().update({k: None for k in dir(HU.TWebSessions) if k.startswith("test")})

    def test_attach_and_serve(self):
        d = Path(tempfile.mkdtemp())
        f = d / "0123456789abcdef.png"
        f.write_bytes(png(20, 10))
        bad = d / "secret.png"
        bad.write_bytes(png())
        ui = self.main.ui
        ui.tool("view_image", "a.png")
        ui.tool_images([str(f), str(bad)])
        e = [m for m in self.main.state()["main_log"] if m["kind"] == "tool"][-1]
        self.assertEqual(e["imgs"], ["0123456789abcdef.png"])                 # 이름 꼴이 아닌 것은 안 단다
        r, data = self.req("GET", "/api/img?n=0123456789abcdef.png", headers={"Cookie": self.ck})
        self.assertEqual((r.status, r.getheader("Content-Type")), (200, "image/png"))
        self.assertEqual(data, f.read_bytes())
        for q in ("n=secret.png", "n=unknownimage.png", "n=..%2F0123456789abcdef.png"):
            self.assertEqual(self.req("GET", "/api/img?" + q, headers={"Cookie": self.ck})[0].status, 404, q)
        self.assertNotEqual(self.req("GET", "/api/img?n=0123456789abcdef.png")[0].status, 200)   # 로그인 없이는 안 됨


if __name__ == "__main__":
    unittest.main()
