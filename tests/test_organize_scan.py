"""폴더 정리 ① 훑기·집계·엿보기. 임시 폴더의 가짜 나무로만 시험한다."""

import json
import os
import shutil
import struct
import tempfile
import unicodedata
import unittest
import zlib
from pathlib import Path
from unittest import mock

from stewart import tools as T
from stewart.organize import names, peek as P, scan as S

NFD = unicodedata.normalize("NFD", "사진")
NFC = unicodedata.normalize("NFC", "사진")


def make_pdf(text: str) -> bytes:
    content = zlib.compress(f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET".encode("latin-1"))
    return (b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(content)).encode() + b" /Filter /FlateDecode >>\nstream\n"
            + content + b"\nendstream\nendobj\n%%EOF\n")


def make_jpeg(date: bytes = b"2021:05:06 07:08:09", w: int = 640, h: int = 480) -> bytes:
    # TIFF(II) + IFD0{0x8769 → ExifIFD} + ExifIFD{0x9003 ASCII 20}
    tiff = bytearray(b"II*\x00" + struct.pack("<I", 8))
    ifd0 = struct.pack("<H", 1) + struct.pack("<HHII", 0x8769, 4, 1, 26) + struct.pack("<I", 0)
    tiff += ifd0                                   # 8 + 18 = 26
    exif_ifd = struct.pack("<H", 1) + struct.pack("<HHII", 0x9003, 2, 20, 44) + struct.pack("<I", 0)
    tiff += exif_ifd                               # 26 + 18 = 44
    tiff += date + b"\x00"
    app1 = b"Exif\x00\x00" + bytes(tiff)
    sof = b"\x08" + struct.pack(">HH", h, w) + b"\x03\x01\x11\x00\x02\x11\x01\x03\x11\x01"
    return (b"\xff\xd8" + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
            + b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof + b"\xff\xda\x00\x02" + b"\x00" * 10 + b"\xff\xd9")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "root"
        self.runs = self.tmp / "runs"
        files = {
            f"{NFD}/a.jpg": make_jpeg(), f"{NFD}/b.JPG": b"x" * 2000, "docs/report.pdf": make_pdf("Annual report 2023 draft"),
            "docs/note.txt": "첫 줄\n" + "".join(f"줄 {i}\n" for i in range(100)), "docs/cp.txt": "한글 CP949".encode("cp949"),
            "docs/.env": "TOKEN=zz-secret\n", "music/x.mp3": b"\x00" * 5000, "music/sub/y.flac": b"\x00" * 7000,
            "_to_delete/old.txt": "old", "top.bin": bytes(range(256)) * 4, ".git/config": "[core]",
            "empty/": None,
        }
        for rel, data in files.items():
            p = self.root / rel
            if data is None:
                p.mkdir(parents=True)
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data if isinstance(data, bytes) else data.encode())
        os.symlink(self.tmp, self.root / "link-out")
        for i, rel in enumerate(["music/x.mp3", "music/sub/y.flac", "docs/note.txt"]):
            t = 1_600_000_000 + i * 40_000_000
            os.utime(self.root / rel, (t, t))
        self.env = T.ToolEnv(self.root, artifacts_dir=self.runs)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class Names(unittest.TestCase):
    def test_key(self):
        self.assertEqual(names.key(f"{NFD}/A.JPG", True), names.key(f"{NFC}/a.jpg", True))
        self.assertNotEqual(names.key("A.jpg", False), names.key("a.jpg", False))
        self.assertEqual(names.key(f"{NFD}/a.jpg", False), f"{NFC}/a.jpg")
        self.assertEqual(names.key("/x//y/", False), "x/y")

    def test_parts_ok(self):
        for bad in ("", "/", "/etc/passwd", "a/../b", "./a", "C:x", "a/\x00"):
            self.assertIsNotNone(names.parts_ok(bad), bad)
        self.assertIsNone(names.parts_ok(f"{NFC}/2021/a.jpg"))

    def test_case_insensitive_detect(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "Probe").mkdir()
        expect = (tmp / "probe").exists()
        self.assertEqual(names.case_insensitive(tmp / "Probe"), expect)
        shutil.rmtree(tmp)

    def test_to_delete(self):
        self.assertTrue(names.under_to_delete(names.key("_TO_DELETE/a", True), True))
        self.assertFalse(names.under_to_delete(names.key("_to_delete_x/a", True), True))


class Scan(Base):
    def test_scan_items(self):
        ok, out = T.folder_scan(self.env)
        self.assertTrue(ok, out)
        self.assertTrue(out.startswith("[바깥 자료 시작: 폴더 목록 ."), out[:80])
        items = {it["rel"]: it for it in S.iter_items(self.runs / "scan-s1.jsonl")}
        self.assertEqual(items[f"{NFD}/a.jpg"]["key"][:2], NFC)          # 열쇠는 NFC, rel 은 디스크 이름 그대로
        self.assertEqual(items[f"{NFD}/a.jpg"]["rel"], f"{NFD}/a.jpg")
        self.assertTrue(items["_to_delete/old.txt"]["to_delete"])
        self.assertTrue(items["_to_delete"]["to_delete"])
        self.assertFalse(items["docs/note.txt"]["to_delete"])
        self.assertEqual(items["link-out"]["type"], "link")
        self.assertNotIn("link-out/root", "".join(items))                   # 링크를 따라가지 않는다
        self.assertTrue(items[".git"]["opaque"])
        self.assertNotIn(".git/config", items)
        self.assertEqual((items["music"]["n"], items["music"]["size"]), (2, 12000))
        self.assertIn("docs/.env", items)                                   # 이름은 목록에 넣는다(내용은 안 읽음)
        s = json.loads((self.runs / "scan-s1.summary.json").read_text())
        self.assertEqual(s["files"], 10)
        self.assertEqual((s["to_delete_files"], s["empty_dirs"], s["links"]), (1, 1, 1))
        self.assertIn("이미 _to_delete/ 안: 파일 1개", out)
        self.assertNotIn("zz-secret", out)

    def test_needs_artifacts_and_inside(self):
        self.assertFalse(T.folder_scan(T.ToolEnv(self.root))[0])
        ok, out = T.folder_scan(self.env, "/etc")
        self.assertFalse(ok)
        self.assertIn("밖은 다루지 않는다", out)
        self.assertFalse(T.folder_scan(self.env, "../")[0])

    def test_limit(self):
        with mock.patch.object(S, "SCAN_MAX", 5):
            out = S.scan(self.root, self.runs / "x.jsonl", limit=5)
        self.assertTrue(out["stopped"])

    def test_read_and_stats(self):
        T.folder_scan(self.env)
        ok, out = T.scan_read(self.env, under="music", type="file")
        self.assertIn("맞는 항목 2개", out)
        self.assertIn("[music/sub/]\n  y.flac\t", out)                      # 경로순은 폴더 머리 + 이름
        ok, out = T.scan_read(self.env, glob="**/*.jpg")                    # 대소문자 무시 FS 면 b.JPG 도
        self.assertIn(f"[{NFD}/]\n  a.jpg\t", out)
        ok, out = T.scan_read(self.env, glob=f"{NFC}/*", type="file")       # 모델이 NFC 로 쓴 경로
        self.assertIn(f"[{NFD}/]\n  a.jpg\t", out)
        ok, out = T.scan_read(self.env, sort="size", limit=1, type="file")
        self.assertIn("music/sub/y.flac", out)
        self.assertIn("offset=1", out)
        ok, out = T.scan_read(self.env, offset=2, limit=2)
        self.assertIn("중 2~4", out)
        self.assertFalse(T.scan_read(self.env, id="s9")[0])
        self.assertFalse(T.scan_read(self.env, sort="name")[0])
        ok, out = T.scan_stats(self.env, "ext")
        self.assertIn("mp3\t1", out)
        ok, out = T.scan_stats(self.env, "dir", under="music", depth=1)
        self.assertIn("music/sub\t1", out)
        self.assertIn("(바로 아래 파일)\t1", out)
        ok, out = T.scan_stats(self.env, "year", under="music")
        self.assertIn("2020\t1", out)
        ok, out = T.scan_stats(self.env, "size_bucket")
        lines = [l.split("\t")[0] for l in out.splitlines()[2:-1]]
        self.assertEqual(lines, sorted(lines, key=lambda x: S.BUCKET_ORDER[x]))
        self.assertFalse(T.scan_stats(self.env, "color")[0])

    def test_not_in_star(self):
        names_ = [d["function"]["name"] for d in T.definitions(["*"])]
        for n in T.ORGANIZE_TOOLS:
            self.assertNotIn(n, names_)
        self.assertEqual(len(T.definitions(sorted(T.ORGANIZE_TOOLS))), len(T.ORGANIZE_TOOLS))


class Peek(Base):
    def test_kinds(self):
        ok, out = T.peek(self.env, "docs/note.txt")
        self.assertTrue(ok)
        self.assertIn("줄 38", out)
        self.assertNotIn("줄 39", out)                                      # 앞 40줄
        self.assertTrue(out.endswith(T.OUTSIDE_END))
        self.assertIn("한글 CP949", T.peek(self.env, "docs/cp.txt")[1])
        out = T.peek(self.env, "docs/report.pdf")[1]
        self.assertIn("Annual report 2023 draft", out)
        out = T.peek(self.env, f"{NFD}/a.jpg")[1]
        self.assertIn("가로 640, 세로 480", out)
        self.assertIn("촬영일 2021:05:06 07:08:09", out)
        self.assertIn("내용을 보여 주지 않는 형식", T.peek(self.env, "music/x.mp3")[1])
        self.assertIn("내용을 보여 주지 않는 형식", T.peek(self.env, "top.bin")[1])

    def test_refusals(self):
        ok, out = T.peek(self.env, "docs/.env")
        self.assertFalse(ok)
        self.assertNotIn("zz-secret", out)
        self.assertFalse(T.peek(self.env, "link-out")[0])
        self.assertFalse(T.peek(self.env, "/etc/hosts")[0])
        self.assertFalse(T.peek(self.env, "nope.txt")[0])

    def test_pdf_garbage(self):
        self.assertIsNone(P.pdf_first_page(make_pdf("\\001\\002\\003")))
        self.assertIsNone(P.pdf_first_page(b"%PDF-1.4 no streams"))


if __name__ == "__main__":
    unittest.main()
