"""판 125: 허브 설정 시트·`stewart setup` 이 backends 표를 고치는 길 — 등급 ↔ 백엔드 연결, 백엔드 주소·모델·동시 수, 상한, 키(키체인).

- 파일(backends_path())이 정본이다. tomledit 로 고칠 줄만 바꾸고, 쓰기 전에 검사·백업한다.
- 키 값은 표에 적지 않는다. macOS 는 키체인 항목(api_key_keychain)에 넣는다 — 값은 security 의 표준 입력으로만 넘긴다(명령 줄·기록에 남기지 않음).
- 적용: 새 대화·새 작업자부터(등급 표는 그때마다 읽는다). 열린 대화는 만들 때의 모델 그대로.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
BACKEND_KEYS = {"base_url": str, "model": str, "concurrency": int, "provider": str, "max_context": int,
                "api_key_keychain": str, "api_key_env": str}
TIER_KEYS = {"backend": str, "max_usd_day": float, "max_usd_chat": float, "max_tokens_day": int, "manual": bool, "budget": int, "use": str}


def _clean(fields: dict, spec: dict, what: str) -> dict:
    out = {}
    for k, v in (fields or {}).items():
        if k not in spec:
            raise ValueError(f"{what} 칸 {k} 는 여기서 고치지 않는다(있는 칸: {', '.join(spec)})")
        t = spec[k]
        if v is None or v == "":
            out[k] = None   # 지운다
            continue
        try:
            v = (v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")) if t is bool else t(v)
        except (TypeError, ValueError):
            raise ValueError(f"{what} 칸 {k} 는 {t.__name__} 값이어야 한다") from None
        if t in (int, float) and v < 0:
            raise ValueError(f"{what} 칸 {k} 는 0 이상")
        if k == "base_url" and not re.match(r"^https?://", v):
            raise ValueError("주소는 http:// 또는 https:// 로 시작한다")
        if k in ("api_key_keychain", "api_key_env") and not re.match(r"^[A-Za-z0-9_.-]{1,80}$", v):
            raise ValueError(f"{k} 는 이름만(영숫자·._-) — 키 값을 여기에 넣지 않는다")
        if t is str and ("\n" in v or len(v) > 300):
            raise ValueError(f"{what} 칸 {k} 가 너무 길거나 여러 줄이다")
        out[k] = v
    return out


def edit(path: Path, tier: str | None = None, tier_fields: dict | None = None,
         backend: str | None = None, backend_fields: dict | None = None) -> dict:
    """등급·백엔드 칸을 고친다 → {"backup": 백업 파일 이름 | None}. 검사 실패면 ValueError(파일 그대로)."""
    from . import tomledit as TE
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if backend_fields:
        if not backend or not NAME.match(backend):
            raise ValueError("백엔드 이름은 영숫자·_- 로")
        text = TE.set_keys(text, f"backends.{backend}", _clean(backend_fields, BACKEND_KEYS, "백엔드"))
    if tier_fields:
        if not tier or not NAME.match(tier):
            raise ValueError("등급 이름은 영숫자·_- 로")
        text = TE.set_keys(text, f"tiers.{tier}", _clean(tier_fields, TIER_KEYS, "등급"))
    bak = TE.write(path, text)
    return {"backup": bak.name if bak else None}


def keychain_put(service: str, value: str, account: str = "api", run=subprocess.run) -> None:
    """macOS 키체인 항목에 키를 넣는다(있으면 바꾼다). 값은 security -i 의 표준 입력으로만. 다른 OS 는 ValueError."""
    if not re.match(r"^[A-Za-z0-9_.-]{1,80}$", service):
        raise ValueError("키체인 항목 이름은 영숫자·._- 로")
    if not value or any(c in value for c in '"\\\n\r') or len(value) > 4096:
        raise ValueError("키 값에 따옴표·역빗금·줄바꿈은 넣지 못한다")
    if sys.platform == "win32":   # Windows: 고장이 아니라 이 길을 쓰지 않는다
        raise ValueError("이 PC(Windows)에서는 설정 시트의 키 넣기를 쓰지 않는다 — 바깥 API 키가 필요하면 허브를 켜는 셸의 환경 변수(api_key_env)로 넣는다")
    if sys.platform != "darwin" or not shutil.which("security"):
        raise ValueError("이 기계에는 키체인이 없다 — 허브를 켜는 셸의 환경 변수(api_key_env)로 넣고 다시 켠다")
    line = f'add-generic-password -U -s "{service}" -a "{account}" -w "{value}"\n'
    r = run(["security", "-i"], input=line.encode("utf-8"), capture_output=True, timeout=20)
    if r.returncode != 0:
        raise ValueError("키체인에 넣지 못했다(잠겼거나 막힘) — `security unlock-keychain` 뒤 다시")
    from . import config as C
    C._KEYCHAIN.pop(service, None)   # 다음 요청부터 새 값을 읽게


def editable(env=None) -> tuple[bool, str]:
    """이 허브에서 화면으로 고칠 수 있나 → (가능, 까닭)."""
    e = os.environ if env is None else env
    if e.get("STEWART_INSTANCE"):
        return False, "따로 도는 허브는 설정 폴더를 읽기만 한다 — 바깥 셸에서 `stewart setup --instance <이름>` 또는 그 폴더의 backends.toml 을 고친다"
    return True, ""
