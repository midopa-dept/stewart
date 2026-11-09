"""엿보기(정리 프로필 전용): 글 파일 앞부분, PDF 첫 쪽 글자(표준 라이브러리로 되는 만큼), 이미지 메타데이터.

- 글: 앞 40줄·4,000자. NUL 이 있거나 UTF-8·CP949 로 안 풀리면 글이 아닌 것으로 본다.
- PDF: 첫 FlateDecode 내용 스트림들에서 Tj/TJ 글자를 뽑는다. 뽑힌 글자가 거의 없거나 깨졌으면 "글자 없음".
- 이미지: 내용 대신 가로·세로와, JPEG 이면 EXIF 촬영일·기기(있으면)만.
- 그 밖: 크기·형식만.
"""

from __future__ import annotations

import re
import struct
import zlib
from pathlib import Path

TEXT_LINES, TEXT_CHARS = 40, 4000
READ_MAX = 256 * 1024          # 글·이미지 머리는 이만큼만 읽는다
PDF_READ_MAX = 4 * 1024 * 1024
TEXT_EXT = {"txt", "md", "csv", "tsv", "json", "xml", "yaml", "yml", "toml", "ini", "log", "html", "htm", "py", "js", "ts",
            "sh", "c", "h", "cpp", "java", "go", "rs", "rb", "srt", "ass", "smi", "vtt", "tex", "sql", "cfg", "conf", "nfo", "cue"}


def _decode_text(b: bytes) -> str | None:
    if b"\x00" in b[:8192]:
        return None
    cut = len(b) >= READ_MAX               # 읽기 상한에서 잘렸으면 끝의 반쪽 글자는 봐준다
    for enc in ("utf-8", "cp949"):
        for tail in ((0, 1, 2, 3) if cut else (0,)):
            try:
                return (b[:-tail] if tail else b).decode(enc)
            except UnicodeDecodeError:
                continue
    return None


def text_head(b: bytes) -> str | None:
    t = _decode_text(b)
    if t is None:
        return None
    lines = t.splitlines()[:TEXT_LINES]
    out = "\n".join(lines)
    if len(out) > TEXT_CHARS:
        out = out[:TEXT_CHARS] + "…"
    return out


_TJ = re.compile(rb"\((?:\\.|[^\\)])*\)\s*Tj|\[(?:[^\]]*)\]\s*TJ")
_STR = re.compile(rb"\((?:\\.|[^\\)])*\)")
_ESC = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"", b"f": b"", b"(": b"(", b")": b")", b"\\": b"\\"}


def _pdf_str(s: bytes) -> bytes:
    s = s[1:-1]
    out = bytearray()
    i = 0
    while i < len(s):
        c = s[i:i + 1]
        if c == b"\\" and i + 1 < len(s):
            n = s[i + 1:i + 2]
            m = re.match(rb"[0-7]{1,3}", s[i + 1:i + 4])
            if m:
                out.append(int(m.group(), 8) & 0xFF)
                i += 1 + len(m.group())
                continue
            out += _ESC.get(n, n)
            i += 2
            continue
        out += c
        i += 1
    return bytes(out)


def pdf_first_page(data: bytes) -> str | None:
    """첫 쪽 근처 글자. 못 뽑으면 None."""
    texts: list[str] = []
    for m in re.finditer(rb"<<(.{0,400}?)>>\s*stream\r?\n", data, re.S):
        if b"FlateDecode" not in m.group(1) or b"/Image" in m.group(1):
            continue
        start = m.end()
        end = data.find(b"endstream", start)
        if end < 0:
            break
        try:
            raw = zlib.decompress(data[start:end].rstrip(b"\r\n"))
        except zlib.error:
            continue
        if b"BT" not in raw:
            continue
        parts = []
        for t in _TJ.finditer(raw):
            for s in _STR.findall(t.group()):
                parts.append(_pdf_str(s).decode("latin-1"))
        txt = re.sub(r"\s+", " ", "".join(parts)).strip()
        if txt:
            texts.append(txt)
            break                           # 첫 글자 스트림 = 첫 쪽으로 본다
    if not texts:
        return None
    t = texts[0]
    good = sum(ch.isprintable() for ch in t) / max(1, len(t))
    letters = sum(ch.isalnum() for ch in t)
    if good < 0.9 or letters < 10:
        return None                          # CID 글꼴 등으로 깨진 글자
    return t[:TEXT_CHARS]


def _exif(app1: bytes) -> dict:
    """APP1 Exif 에서 촬영일(0x9003)·기록일(0x0132)·기기(0x010f/0x0110)."""
    out = {}
    if not app1.startswith(b"Exif\x00\x00"):
        return out
    t = app1[6:]
    if len(t) < 8:
        return out
    bo = "<" if t[:2] == b"II" else ">"

    def ifd(off, depth=0):
        if off + 2 > len(t) or depth > 2:
            return
        n = struct.unpack(bo + "H", t[off:off + 2])[0]
        for i in range(min(n, 200)):
            e = off + 2 + i * 12
            if e + 12 > len(t):
                return
            tag, typ, cnt = struct.unpack(bo + "HHI", t[e:e + 8])
            val = t[e + 8:e + 12]
            if tag == 0x8769:                 # Exif 하위 IFD
                ifd(struct.unpack(bo + "I", val)[0], depth + 1)
            elif typ == 2 and tag in (0x9003, 0x0132, 0x010F, 0x0110):
                vo = struct.unpack(bo + "I", val)[0] if cnt > 4 else e + 8
                s = t[vo:vo + cnt].split(b"\x00")[0].decode("ascii", "replace").strip()
                if s:
                    out[{0x9003: "촬영일", 0x0132: "기록일", 0x010F: "제조사", 0x0110: "기기"}[tag]] = s

    try:
        ifd(struct.unpack(bo + "I", t[4:8])[0])
    except struct.error:
        pass
    return out


def image_meta(b: bytes) -> dict | None:
    if b.startswith(b"\x89PNG\r\n\x1a\n") and len(b) >= 24:
        w, h = struct.unpack(">II", b[16:24])
        return {"형식": "PNG", "가로": w, "세로": h}
    if b[:6] in (b"GIF87a", b"GIF89a") and len(b) >= 10:
        w, h = struct.unpack("<HH", b[6:10])
        return {"형식": "GIF", "가로": w, "세로": h}
    if b.startswith(b"\xff\xd8"):
        out = {"형식": "JPEG"}
        i = 2
        while i + 4 <= len(b):
            if b[i] != 0xFF:
                break
            mk = b[i + 1]
            if mk in (0xD8, 0x01) or 0xD0 <= mk <= 0xD7:
                i += 2
                continue
            ln = struct.unpack(">H", b[i + 2:i + 4])[0]
            seg = b[i + 4:i + 2 + ln]
            if mk == 0xE1:
                out.update(_exif(seg))
            elif mk in (0xC0, 0xC1, 0xC2) and len(seg) >= 5:
                h, w = struct.unpack(">HH", seg[1:5])
                out["가로"], out["세로"] = w, h
                break
            elif mk == 0xDA:
                break
            i += 2 + ln
        return out
    if b[:2] == b"BM" and len(b) >= 26:
        w, h = struct.unpack("<ii", b[18:26])
        return {"형식": "BMP", "가로": w, "세로": abs(h)}
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return {"형식": "WEBP"}
    return None


def peek(p: Path, ext: str) -> tuple[str, str]:
    """(종류, 보여 줄 글)."""
    size = p.stat().st_size
    with open(p, "rb") as f:
        head = f.read(PDF_READ_MAX if ext == "pdf" else READ_MAX)
    if ext == "pdf" or head.startswith(b"%PDF-"):
        t = pdf_first_page(head)
        if t is None:
            return "pdf", f"PDF {size:,} 바이트 — 첫 쪽 글자를 뽑지 못함(표준 라이브러리 범위 밖이거나 그림·깨진 글꼴)"
        return "pdf", f"PDF {size:,} 바이트, 첫 쪽 글자:\n{t}"
    meta = image_meta(head)
    if meta:
        return "image", "이미지 메타데이터(내용은 보지 않음): " + ", ".join(f"{k} {v}" for k, v in meta.items()) + f", {size:,} 바이트"
    if ext in TEXT_EXT or ext == "":
        t = text_head(head)
        if t is not None:
            return "text", f"글 파일 앞부분(최대 {TEXT_LINES}줄·{TEXT_CHARS}자):\n{t}"
    t = text_head(head) if ext not in ("zip", "rar", "7z", "gz", "mp4", "mkv", "mp3", "flac") else None
    if t is not None and size <= 10 * 1024 * 1024:
        return "text", f"글 파일 앞부분(최대 {TEXT_LINES}줄·{TEXT_CHARS}자):\n{t}"
    return "other", f"내용을 보여 주지 않는 형식(.{ext or '확장자 없음'}), {size:,} 바이트"
