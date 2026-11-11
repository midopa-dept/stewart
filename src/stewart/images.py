"""이미지 입력(U2-5, 기본 끔 — 백엔드 표의 vision = true 일 때만).

A. 사람이 붙이는 이미지: 허브 화면(붙여넣기·끌어다 놓기·파일 고르기), CLI run --image·chat /image.
B. 모델이 작업 폴더의 이미지를 보기: view_image 도구(도구 결과는 글, 엔진이 다음 user 메시지에 이미지를 붙인다).
   read_file 은 이미지를 만나면 깨진 글 대신 형식·크기 한 줄(vision 켬일 때).
C. 기록·문맥·이어 하기: 메시지 안 이미지는 자리표 {"type":"image_url","image_url":{"url":"stewart-image:<절대 경로>"}} 로 두고,
   파일은 기록 폴더 images/<sha>.<확장자> 에 둔다(base64 는 기록·messages.json 에 쓰지 않는다). 보낼 때만 backend 가
   data:<mime>;base64,… 로 펼친다(expand). 이어 하기는 자리표 그대로 읽어 다시 펼친다. 파일이 없으면 글 자리표로 바뀐다.
- 형식은 앞 바이트로만 본다: PNG·JPEG·WebP·GIF. 줄이기·변환은 하지 않는다(새 의존성 없음). 크기 상한 MAX_BYTES(STEWART_IMAGE_MAX_BYTES).
- 토큰 어림: 이미지당 IMAGE_TOKENS(compact.est_tokens 가 자리표 수로 더한다).
- 사람이 준 이미지는 작업 폴더 밖이어도 묻지 않는다. 비밀 꼴 이름(fswalk.is_secret)만 거부한다. EXIF 는 그대로 간다.
"""

from __future__ import annotations

import base64
import hashlib
import os
import struct
from pathlib import Path

SCHEME = "stewart-image:"
IMAGE_TOKENS = 1200
MAX_BYTES_DEFAULT = 8 * 1024 * 1024
EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}


class ImageError(ValueError):
    pass


def max_bytes() -> int:
    v = os.environ.get("STEWART_IMAGE_MAX_BYTES", "")
    return int(v) if v.isdigit() and int(v) > 0 else MAX_BYTES_DEFAULT


def sniff(data: bytes) -> str | None:
    """앞 바이트로 형식(mime). 모르면 None."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def dims(data: bytes, mime: str) -> tuple[int, int] | None:
    """가로·세로(못 읽으면 None). 머리만 본다."""
    try:
        if mime == "image/png" and data[12:16] == b"IHDR":
            return struct.unpack(">II", data[16:24])
        if mime == "image/gif":
            return struct.unpack("<HH", data[6:10])
        if mime == "image/webp":
            kind = data[12:16]
            if kind == b"VP8X":
                return (int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1)
            if kind == b"VP8L":
                b = int.from_bytes(data[21:25], "little")
                return ((b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1)
            if kind == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return (w & 0x3FFF, h & 0x3FFF)
        if mime == "image/jpeg":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return (w, h)
                i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    except (struct.error, IndexError):
        return None
    return None


def describe(data: bytes, mime: str) -> str:
    d = dims(data, mime)
    return f"{mime.split('/')[1].upper()}{f' {d[0]}×{d[1]}' if d else ''}, {len(data)} 바이트"


def check(data: bytes, name: str = "") -> str:
    """형식·크기 검사. 맞으면 mime, 아니면 ImageError(까닭)."""
    from .fswalk import is_secret
    if name and is_secret(Path(name).name):
        raise ImageError(f"비밀 파일 꼴 이름이라 받지 않는다: {Path(name).name}")
    if not data:
        raise ImageError("빈 파일")
    if len(data) > max_bytes():
        raise ImageError(f"이미지가 너무 크다({len(data)} 바이트, 상한 {max_bytes()}). 줄여서 다시 붙인다")
    mime = sniff(data)
    if mime is None:
        raise ImageError("PNG·JPEG·WebP·GIF 가 아니다(앞 바이트로 판정)")
    return mime


def store(store_dir: Path, data: bytes, mime: str) -> dict:
    """기록 폴더 images/ 에 두고 자리표 부분(content part)을 돌려준다."""
    d = Path(store_dir) / "images"
    d.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(data).hexdigest()[:16]
    p = d / f"{sha}.{EXT[mime]}"
    if not p.exists():
        p.write_bytes(data)
    return {"type": "image_url", "image_url": {"url": SCHEME + str(p.resolve())}}


def from_file(path: str | Path, store_dir: Path) -> tuple[dict, str]:
    """사람이 준 이미지 파일 → (자리표 부분, 설명 한 줄). 작업 폴더 밖이어도 된다."""
    p = Path(os.path.expanduser(str(path)))
    try:
        data = p.read_bytes()
    except OSError as e:
        raise ImageError(f"읽지 못했다: {p} ({e.strerror or e})") from None
    mime = check(data, p.name)
    return store(store_dir, data, mime), f"{p.name} ({describe(data, mime)})"


def from_bytes(data: bytes, name: str, store_dir: Path) -> tuple[dict, str]:
    mime = check(data, name)
    return store(store_dir, data, mime), f"{name or '붙인 이미지'} ({describe(data, mime)})"


def content(text: str, parts: list[dict]) -> str | list:
    """사람 메시지 content: 이미지가 없으면 글 그대로, 있으면 [{text}, 이미지 자리표…]."""
    return [{"type": "text", "text": text}] + list(parts) if parts else text


def text_of(c) -> str:
    """content(글 또는 부분 목록)의 글만. 이미지는 "[이미지]" 로."""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        out = []
        for x in c:
            if isinstance(x, dict) and x.get("type") == "text":
                out.append(str(x.get("text") or ""))
            elif isinstance(x, dict) and x.get("type") == "image_url":
                out.append("[이미지]")
        return "\n".join(out)
    return str(c or "")


def count(messages) -> int:
    n = 0
    for m in messages or []:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list):
            n += sum(1 for x in c if isinstance(x, dict) and x.get("type") == "image_url")
    return n


def _data_url(url: str) -> str | None:
    p = Path(url[len(SCHEME):])
    try:
        data = p.read_bytes()
    except OSError:
        return None
    mime = sniff(data)
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}" if mime else None


def expand(messages, vision: bool):
    """보낼 메시지 사본: 자리표를 data URL 로(vision), 아니면 글 자리표로. 이미지가 없으면 같은 것을 돌려준다."""
    if not count(messages):
        return messages
    out = []
    for m in messages:
        c = m.get("content") if isinstance(m, dict) else None
        if not isinstance(c, list):
            out.append(m)
            continue
        parts = []
        for x in c:
            if isinstance(x, dict) and x.get("type") == "image_url":
                url = str((x.get("image_url") or {}).get("url") or "")
                if not vision:
                    parts.append({"type": "text", "text": "[이미지: 이 백엔드는 이미지를 받지 않아 빼고 보냈다]"})
                    continue
                du = _data_url(url) if url.startswith(SCHEME) else url
                parts.append({"type": "image_url", "image_url": {"url": du}} if du
                             else {"type": "text", "text": "[이미지 파일이 없어 빼고 보냈다]"})
            else:
                parts.append(x)
        out.append({**m, "content": parts})
    return out
