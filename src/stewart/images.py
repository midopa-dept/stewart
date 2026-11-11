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
D. 판 82(사진 글자 실측 B 채택): 허브가 2.0MP 로 줄여 보내면 원본도 함께 받아 images/<sha>.orig.<확장자> 로 둔다(keep_original).
   모델은 view_image(path, region) 으로 원본에서 잘라 크게 본다(crop — 맥 sips). 한 요청에 그림이 backend.max_images 장을
   넘으면 최근 것만 보내고 앞 것은 경로를 적은 글 자리표로(서버가 요청을 통째로 거절하지 않게).
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


def path_of(part: dict) -> Path | None:
    url = str(((part or {}).get("image_url") or {}).get("url") or "")
    return Path(url[len(SCHEME):]) if url.startswith(SCHEME) else None


def keep_original(part: dict, orig: bytes) -> Path | None:
    """줄인 그림(part)의 원본을 옆에 둔다: <줄인 것 이름>.orig.<확장자>. 받지 않는 꼴이면 None."""
    p = path_of(part)
    try:
        mime = check(orig)
    except ImageError:
        return None
    if p is None:
        return None
    q = p.with_name(f"{p.stem}.orig.{EXT[mime]}")
    if not q.exists():
        q.write_bytes(orig)
    return q


def original_of(path: Path) -> Path:
    """그림 경로 → 원본이 따로 있으면 원본, 없으면 그대로."""
    for q in sorted(Path(path).parent.glob(Path(path).stem + ".orig.*")):
        return q
    return Path(path)


class CropError(Exception):
    pass


def crop(src: Path, region, out_dir: Path) -> tuple[Path, str]:
    """src 의 region 을 잘라 PNG 로 → (경로, 설명). region = [x0, y0, x1, y1] — 모두 1 이하면 비율(0~1), 아니면 원본 화소.
    잘린 것이 작으면 긴 변 1024 까지 키운다. 맥 기본 sips 만 쓴다(새 의존성 없음) — 그 밖 OS 는 CropError."""
    import shutil
    import subprocess
    import sys
    if sys.platform != "darwin" or not shutil.which("sips"):
        raise CropError("부분 확대(region)는 맥(sips)에서만 된다 — region 없이 통째로 본다")
    data = Path(src).read_bytes()
    d = dims(data, sniff(data) or "")
    if not d:
        raise CropError("그림 크기를 읽지 못했다")
    w, h = d
    try:
        x0, y0, x1, y1 = (float(v) for v in region)
    except (TypeError, ValueError):
        raise CropError("region 은 [x0, y0, x1, y1] 숫자 넷") from None
    if max(x0, y0, x1, y1) <= 1.0:
        x0, x1, y0, y1 = x0 * w, x1 * w, y0 * h, y1 * h
    x0, x1 = sorted((max(0, min(w, x0)), max(0, min(w, x1))))
    y0, y1 = sorted((max(0, min(h, y0)), max(0, min(h, y1))))
    cw, ch = int(x1 - x0), int(y1 - y0)
    if cw < 8 or ch < 8:
        raise CropError(f"region 이 너무 작다({cw}×{ch}) — 그림은 {w}×{h}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"crop-{hashlib.sha256(data + repr((x0, y0, cw, ch)).encode()).hexdigest()[:12]}.png"
    args = ["sips", "-s", "format", "png", "-c", str(ch), str(cw), "--cropOffset", str(int(y0)), str(int(x0))]
    r = subprocess.run(args + [str(src), "--out", str(out)], capture_output=True, timeout=60)
    if r.returncode != 0 or not out.is_file():
        raise CropError("sips 가 자르지 못했다: " + r.stderr.decode("utf-8", "replace")[-200:])
    if max(cw, ch) < 1024:
        subprocess.run(["sips", "-Z", "1024", str(out)], capture_output=True, timeout=60)
    return out, f"{Path(src).name} 의 ({int(x0)},{int(y0)})~({int(x0) + cw},{int(y0) + ch}) — 원본 {w}×{h} 에서 {cw}×{ch}"


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


def expand(messages, vision: bool, max_images: int = 0):
    """보낼 메시지 사본: 자리표를 data URL 로(vision), 아니면 글 자리표로. 이미지가 없으면 같은 것을 돌려준다.
    max_images 가 있으면 뒤(최근)에서부터 그 장수만 보내고 앞 것은 경로를 적은 글로(판 82)."""
    n = count(messages)
    if not n:
        return messages
    skip = max(0, n - max_images) if max_images and vision else 0
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
                if skip:
                    skip -= 1
                    where = url[len(SCHEME):] if url.startswith(SCHEME) else ""
                    parts.append({"type": "text", "text": f"[앞 그림 — 한 요청 {max_images}장 상한이라 이번에는 뺐다"
                                                          + (f". 다시 보려면 view_image 로: {where}]" if where else "]")})
                    continue
                du = _data_url(url) if url.startswith(SCHEME) else url
                parts.append({"type": "image_url", "image_url": {"url": du}} if du
                             else {"type": "text", "text": "[이미지 파일이 없어 빼고 보냈다]"})
            else:
                parts.append(x)
        out.append({**m, "content": parts})
    return out
