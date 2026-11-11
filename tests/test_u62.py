"""판 82(SW-36 사진): 요청당 그림 상한(최근 것만)·줄여 받은 사진의 원본 보관·view_image region(맥 sips)·허브 그림 경로 안내·
OCR 손잡이(기본 끔)·조각 손잡이 상태·backends max_images. 화면 쪽(2.0MP PNG·JPEG 0.9·원본 함께·조각)은 ui/test/imgprep.test.mjs."""

import base64
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from stewart import images as IMG, ocr as OCR, tools as T
from stewart.config import load_backends


def png(w, h, rgb=(200, 30, 30)):
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def sips_ok() -> bool:
    if sys.platform != "darwin" or not shutil.which("sips"):
        return False
    d = Path(tempfile.mkdtemp())
    (d / "a.png").write_bytes(png(20, 20))
    r = subprocess.run(["sips", "-s", "format", "png", "-c", "10", "10", str(d / "a.png"), "--out", str(d / "b.png")], capture_output=True)
    return r.returncode == 0 and (d / "b.png").is_file()


class TExpand(unittest.TestCase):
    def test_keeps_latest_n(self):
        d = Path(tempfile.mkdtemp())
        parts = [IMG.store(d, png(4 + i, 4), "image/png") for i in range(6)]
        msgs = [{"role": "user", "content": IMG.content(f"그림 {i}", [p])} for i, p in enumerate(parts)]
        out = IMG.expand(msgs, True, 4)
        kinds = [[x["type"] for x in m["content"]] for m in out]
        self.assertEqual(kinds[0], ["text", "text"])                 # 앞 두 장은 글 자리표
        self.assertEqual(kinds[1], ["text", "text"])
        self.assertIn("view_image", out[0]["content"][1]["text"])
        self.assertIn(str(IMG.path_of(parts[0])), out[0]["content"][1]["text"])
        self.assertTrue(all(k == ["text", "image_url"] for k in kinds[2:]))
        self.assertEqual(sum(1 for m in IMG.expand(msgs, True, 0) for x in m["content"] if x["type"] == "image_url"), 6)

    def test_backend_field(self):
        d = Path(tempfile.mkdtemp())
        (d / "b.toml").write_text('default = "a"\n[backends.a]\nbase_url = "http://x/v1"\nmodel = "m"\nvision = true\nmax_images = 2\n'
                                  '[backends.b]\nbase_url = "http://x/v1"\nmodel = "m"\n', encoding="utf-8")
        bs, _ = load_backends(d / "b.toml")
        self.assertEqual((bs["a"].max_images, bs["b"].max_images), (2, 4))


class TOriginalAndCrop(unittest.TestCase):
    def test_keep_and_find_original(self):
        d = Path(tempfile.mkdtemp())
        part = IMG.store(d, png(10, 10), "image/png")
        q = IMG.keep_original(part, png(40, 40))
        self.assertTrue(q.name.endswith(".orig.png"))
        self.assertEqual(IMG.original_of(IMG.path_of(part)), q)
        self.assertIsNone(IMG.keep_original(part, b"not an image"))
        other = IMG.store(d, png(12, 12), "image/png")
        self.assertEqual(IMG.original_of(IMG.path_of(other)), IMG.path_of(other))

    @unittest.skipUnless(sips_ok(), "맥 sips 가 이 자리에서 돌지 않는다")
    def test_view_image_region_uses_original(self):
        d = Path(tempfile.mkdtemp())
        part = IMG.store(d, png(100, 50), "image/png")
        IMG.keep_original(part, png(400, 200))
        env = T.ToolEnv(workdir=d, artifacts_dir=d / "run")
        env.vision = True
        ok, msg = T.view_image(env, str(IMG.path_of(part)), region=[0.5, 0.5, 1.0, 1.0])
        self.assertTrue(ok, msg)
        self.assertIn("원본 400×200 에서 200×100", msg)
        crop = IMG.path_of(env.pending_images[-1])
        self.assertEqual(IMG.dims(crop.read_bytes(), "image/png")[0], 1024)   # 작은 조각은 긴 변 1024 로 키움

    def test_region_errors(self):
        d = Path(tempfile.mkdtemp())
        (d / "a.png").write_bytes(png(50, 50))
        env = T.ToolEnv(workdir=d, artifacts_dir=d / "run")
        env.vision = True
        with mock.patch.object(IMG.sys if hasattr(IMG, "sys") else sys, "platform", "linux"):
            ok, msg = T.view_image(env, "a.png", region=[0, 0, 0.5, 0.5])
        self.assertFalse(ok)
        self.assertIn("맥(sips)", msg)
        ok, msg = T.view_image(env, "a.png")
        self.assertTrue(ok)                                               # region 없으면 예전과 같다


class TOcrKnob(unittest.TestCase):
    def test_default_off_and_note(self):
        self.assertFalse(OCR.enabled(None, {}))
        self.assertTrue(OCR.enabled(True, {}))
        self.assertFalse(OCR.enabled(True, {"STEWART_IMAGE_OCR": "0"}))
        self.assertTrue(OCR.enabled(False, {"STEWART_IMAGE_OCR": "1"}))
        self.assertEqual(OCR.note(1, ""), "")
        self.assertIn("[그림 2 글자(OCR", OCR.note(2, "합계 33,280"))

    def test_text_redacts_and_caps(self):
        fake = mock.Mock(returncode=0, stdout=("토큰 sk-" + "a" * 30 + "\n" + "가" * 5000).encode())
        with mock.patch.object(OCR, "engine", return_value="vision"), mock.patch.object(OCR.subprocess, "run", return_value=fake):
            t = OCR.text_of(b"x")
        self.assertIn("[가림]", t)
        self.assertIn("자 생략", t)
        with mock.patch.object(OCR, "engine", return_value=""):
            self.assertEqual(OCR.text_of(b"x"), "")


from tests.test_u25 import MainBase, msg   # noqa: E402


class THubSend(MainBase):
    def test_paths_original_and_ocr(self):
        self.main.sess.backend.vision = True
        self.r.scripts["MAIN"] = [msg("봤다")]
        with mock.patch.object(OCR, "text_of", return_value="합계 33,280") as ocr:
            self.assertEqual(self.main.send("영수증 합계?", images=[("r.png", png(10, 10), png(40, 40))]), "started")
            self.assertTrue(self.main.wait_idle())
        ocr.assert_not_called()                                              # OCR 기본 끔
        c = self.r.requests["MAIN"][-1]["messages"][-1]["content"]
        self.assertIn("view_image(path, region=", c[0]["text"])
        p = Path(c[0]["text"].split("1) ")[1].split("]")[0].strip())
        self.assertTrue(IMG.original_of(p).name.endswith(".orig.png"))       # 원본을 옆에 뒀다
        self.assertNotIn("OCR", c[0]["text"])
        log = [e.get("text", "") for e in self.main.ui.log if e.get("kind") == "user"]
        self.assertEqual(log[-1].split("  [")[0], "영수증 합계?")              # 화면 말 줄은 사람 말 그대로
        self.main.image_ocr = True
        self.r.scripts["MAIN"] = [msg("봤다")]
        with mock.patch.object(OCR, "text_of", return_value="합계 33,280"):
            self.main.send("다시", images=[("r.png", png(10, 10))])
            self.assertTrue(self.main.wait_idle())
        c = self.r.requests["MAIN"][-1]["messages"][-1]["content"]
        self.assertIn("[그림 1 글자(OCR", c[0]["text"])
        self.assertIn("합계 33,280", c[0]["text"])
        st = self.main.state()
        self.assertFalse(st["image_tiles"])


if __name__ == "__main__":
    unittest.main()
