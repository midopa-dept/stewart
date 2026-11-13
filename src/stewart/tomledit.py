"""판 125: backends 표를 줄 단위로 고친다 — 표 머리 [a.b] 아래의 키만 바꾸거나 더하고, 주석·다른 표·줄 순서는 그대로 둔다.

허브 설정 시트와 `stewart setup` 이 쓴다. 파일이 정본이고, 고치기 전에 같은 폴더에 <이름>.bak-<날짜시각> 을 남긴다(최근 KEEP 개).
고친 글은 쓰기 전에 tomllib 로 읽어 보고, 백엔드 표로 풀어 본다(실패하면 쓰지 않는다).
"""

from __future__ import annotations

import json
import re
import shutil
import time
import tomllib
from pathlib import Path

KEEP = 10
_HEAD = re.compile(r"^\s*\[\s*([^\[\]]+?)\s*\]\s*(#.*)?$")


def fmt(v) -> str:
    """TOML 값 한 개(문자열·정수·실수·참거짓·문자열 목록·작은 표)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)   # 기본 문자열 꼴(이스케이프가 TOML 과 같다)
    if isinstance(v, list):
        return "[" + ", ".join(fmt(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {fmt(x)}" for k, x in v.items()) + " }"
    raise ValueError(f"적을 수 없는 값: {type(v).__name__}")


def set_keys(text: str, table: str, kv: dict) -> str:
    """table(예: "tiers.presto") 안의 키를 kv 로 바꾼다. 값이 None 이면 그 줄을 지운다. 표가 없으면 끝에 더한다."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if (m := _HEAD.match(ln)) and m.group(1).replace(" ", "") == table), None)
    if start is None:
        add = [f"{k} = {fmt(v)}" for k, v in kv.items() if v is not None]
        if not add:
            return text
        tail = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        return text + tail + f"[{table}]\n" + "\n".join(add) + "\n"
    end = next((i for i in range(start + 1, len(lines)) if _HEAD.match(lines[i])), len(lines))
    body = lines[start + 1:end]
    for k, v in kv.items():
        pat = re.compile(r"^\s*" + re.escape(k) + r"\s*=")
        idx = next((i for i, ln in enumerate(body) if pat.match(ln)), None)
        if v is None:
            if idx is not None:
                del body[idx]
            continue
        new = f"{k} = {fmt(v)}"
        if idx is not None:
            body[idx] = new
        else:
            last = max((i for i, ln in enumerate(body) if ln.strip() and not ln.lstrip().startswith("#")), default=-1)
            body.insert(last + 1, new)
    out = lines[:start + 1] + body + lines[end:]
    return "\n".join(out) + ("\n" if text.endswith("\n") or not text else "")


def backups(path: Path) -> list[Path]:
    return sorted(Path(path).parent.glob(Path(path).name + ".bak-*"), reverse=True)


def write(path: Path, new_text: str) -> Path | None:
    """검사한 뒤 백업을 남기고 쓴다 → 백업 경로(원래 파일이 없었으면 None). 검사 실패는 ValueError(파일은 그대로)."""
    from .config import ConfigError, load_backends
    path = Path(path)
    try:
        tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"표 꼴이 깨진다: {e}") from None
    probe = path.with_name(path.name + ".check")
    try:
        probe.write_text(new_text, encoding="utf-8")
        load_backends(probe)
    except (ConfigError, KeyError, ValueError) as e:
        raise ValueError(f"백엔드 표로 풀리지 않는다: {e}") from None
    finally:
        probe.unlink(missing_ok=True)
    bak = None
    if path.exists():
        bak = path.with_name(path.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
        n = 1
        while bak.exists():
            bak = path.with_name(path.name + f".bak-{time.strftime('%Y%m%d-%H%M%S')}-{n}")
            n += 1
        shutil.copy2(path, bak)
        for old in backups(path)[KEEP:]:
            old.unlink(missing_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(path)
    return bak


def revert(path: Path) -> Path:
    """가장 최근 백업으로 되돌린다(지금 것은 다시 백업으로 남긴다) → 되돌린 백업 경로."""
    path = Path(path)
    bs = backups(path)
    if not bs:
        raise ValueError("되돌릴 백업이 없다")
    src = bs[0]
    text = src.read_text(encoding="utf-8")
    write(path, text)
    src.unlink(missing_ok=True)
    return src
