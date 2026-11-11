"""그림 속 글자 뽑기(판 82, SW-36) — 손잡이, 기본 끔.

켜기: STEWART_IMAGE_OCR=1, 또는 허브 설정 hub.toml [images] ocr = true. 켜면 사람이 붙인 그림마다 글자를 뽑아
"[그림 N 글자(OCR — 틀릴 수 있음, 그림을 함께 봐라)]" 를 같은 메시지 글에 덧붙인다(그림도 그대로 간다).
- 맥: 기본 osascript(JavaScript for Automation)로 Vision 프레임워크의 글자 인식(한국어·영어, 정확 모드). 새 꾸러미 없음.
- 그 밖: PATH 의 tesseract(있으면, kor+eng). 없으면 끔 — 새 의존성은 넣지 않는다.
- 그림당 MAX_CHARS 자, 비밀 꼴 글은 가린다(backend.redact). 실패하면 조용히 빈 글(그림은 그대로 간다).
실측(판 77~78 사진 시험): 그림과 OCR 글을 함께 준 꼴은 그림만(2.0MP) 보다 나아지지 않았다 — 그래서 기본 끔.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

MAX_CHARS = 4000
TIMEOUT_S = 60

JXA = r"""
ObjC.import('Foundation'); ObjC.import('Vision');
function run(argv) {
  var data = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
  var handler = $.VNImageRequestHandler.alloc.initWithDataOptions(data, $.NSDictionary.dictionary);
  var req = $.VNRecognizeTextRequest.alloc.init;
  req.recognitionLevel = 0; req.usesLanguageCorrection = true; req.recognitionLanguages = $(["ko-KR", "en-US"]);
  var err = Ref();
  if (!handler.performRequestsError($.NSArray.arrayWithObject(req), err)) return "\u0000ERR";
  var res = req.results, lines = [];
  for (var i = 0; i < res.count; i++) { var o = res.objectAtIndex(i), c = o.topCandidates(1).firstObject, b = o.boundingBox;
    if (c) lines.push({y: 1 - (b.origin.y + b.size.height), x: b.origin.x, t: ObjC.unwrap(c.string)}); }
  lines.sort(function (a, b) { return Math.abs(a.y - b.y) > 0.012 ? a.y - b.y : a.x - b.x; });
  return lines.map(function (l) { return l.t; }).join("\n");
}
"""


def enabled(hub_setting=None, env=None) -> bool:
    env = os.environ if env is None else env
    v = env.get("STEWART_IMAGE_OCR")
    if v in ("0", "1"):
        return v == "1"
    return bool(hub_setting)


def engine() -> str:
    """쓸 수 있는 길: "vision"(맥) · "tesseract" · ""(없음)."""
    if sys.platform == "darwin" and shutil.which("osascript"):
        return "vision"
    if shutil.which("tesseract"):
        return "tesseract"
    return ""


def text_of(data: bytes) -> str:
    """그림 바이트 → 뽑은 글(없거나 실패하면 "")."""
    how = engine()
    try:
        if how == "vision":
            r = subprocess.run(["osascript", "-l", "JavaScript", "-e", JXA], input=data, capture_output=True, timeout=TIMEOUT_S)
        elif how == "tesseract":
            r = subprocess.run(["tesseract", "stdin", "stdout", "-l", "kor+eng"], input=data, capture_output=True, timeout=TIMEOUT_S)
        else:
            return ""
    except (OSError, subprocess.SubprocessError):
        return ""
    out = r.stdout.decode("utf-8", "replace").strip()
    if r.returncode != 0 or out.startswith("\u0000ERR"):
        return ""
    from .backend import redact
    out = redact(out)
    return out if len(out) <= MAX_CHARS else out[:MAX_CHARS] + f"\n…({len(out) - MAX_CHARS}자 생략)"


def note(i: int, text: str) -> str:
    return f"\n\n[그림 {i} 글자(OCR — 틀릴 수 있음, 그림을 함께 봐라)]\n{text}" if text else ""
