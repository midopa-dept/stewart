"""이름 비교. 비교는 유니코드 NFC(+대소문자를 구분하지 않는 파일 시스템이면 casefold)로 하고,
실제 파일 조작에는 디스크의 원래 이름을 쓴다(macOS 는 한글을 NFD 로 돌려줄 수 있다)."""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path

TO_DELETE = "_to_delete"


def case_insensitive(root: Path) -> bool:
    """root 가 대소문자를 구분하지 않는 파일 시스템인지. 쓰기 없이 확인한다(정리를 돌리는 계정은 읽기 전용일 수 있다).
    root 경로나 그 안 항목 중 글자가 있는 이름 하나를 대소문자만 바꿔 같은 파일인지 본다. 모르면 True(더 조심스러운 쪽)."""
    root = Path(root)
    cands = [root] + list(root.parents)
    try:
        cands = [root / e.name for e in os.scandir(root)][:50] + cands
    except OSError:
        pass
    for p in cands:
        name = p.name
        if not name or name.swapcase() == name:
            continue
        other = p.with_name(name.swapcase())
        try:
            return os.path.samefile(p, other)
        except FileNotFoundError:
            return False
        except OSError:
            continue
    return True


def key(rel: str, fold: bool) -> str:
    """비교용 열쇠: NFC, 구분자 '/', 앞뒤 '/' 없음, fold 면 casefold."""
    k = unicodedata.normalize("NFC", rel.replace("\\", "/")).strip("/")
    while "//" in k:
        k = k.replace("//", "/")
    return k.casefold() if fold else k


def parts_ok(rel: str) -> str | None:
    """계획 경로 검사: root 안 상대 경로여야 한다. 문제가 있으면 이유."""
    if not rel or not rel.strip("/"):
        return "빈 경로"
    if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        return "절대 경로는 받지 않는다(root 기준 상대 경로)"
    for part in rel.replace("\\", "/").split("/"):
        if part in ("..", "."):
            return "'.'·'..' 은 받지 않는다"
        if "\x00" in part:
            return "이름에 NUL"
    return None


def under_to_delete(k: str, fold: bool) -> bool:
    t = key(TO_DELETE, fold)
    return k == t or k.startswith(t + "/")
