"""허브 사용자 계정(U2-7, 기본 끔 — hub.toml [hub] accounts = true 일 때만).

쓰임: 사무실 안 한 워크스테이션에서 허브와 모델 서버를 켜 두고, 같은 망의 다른 PC 가 브라우저로 들어와 각자 자기 일을 이어 쓴다.

- 저장: <허브 폴더>/accounts.json(0600 으로 쓴다). 사용자마다 scrypt(n=2**14, r=8, p=1, 32 바이트) 해시와 사용자별 소금 16 바이트.
  평문·되돌릴 수 있는 꼴은 남기지 않는다. 표준 라이브러리 hashlib.scrypt 만 쓴다.
- 이름: 소문자·숫자·'-' 1~32자(작업 폴더·기록 폴더 이름으로도 쓴다).
- 로그인 세션: 서버 메모리의 무작위 쿠키 값 → {사용자, CSRF 값, 만든 시각, 마지막 시각}. 절대 만료 SESSION_MAX_S, 쉼 만료 SESSION_IDLE_S.
  허브를 끄면 모두 사라진다(다시 로그인). 막힌 사용자는 바로 쫓겨난다(요청마다 계정 상태를 본다).
- 실패 지연: 같은 이름 또는 같은 주소의 연속 실패 수 k 에 대해 응답 전 min(0.5 × 2^(k-1), FAIL_DELAY_MAX) 초. 성공하면 그 이름·주소의 셈을 지운다.
  없는 이름도 같은 지연·같은 글로 답한다(이름이 있는지 알려 주지 않는다). 해시는 없는 이름에도 한 번 돌려 시간을 맞춘다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}
PASSWORD_MIN = 8
SESSION_MAX_S = 12 * 3600
SESSION_IDLE_S = 2 * 3600
FAIL_DELAY_MAX = 30.0
FILE = "accounts.json"


class AccountError(ValueError):
    pass


def _hash(password: str, salt: bytes, p: dict) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=p["n"], r=p["r"], p=p["p"], dklen=p["dklen"],
                          maxmem=64 * 1024 * 1024)


class Store:
    """accounts.json 읽고 쓰기. 쓰기는 임시 파일 + 바꿔치기(osdep.replace_retry — Windows 의 잠깐 잠김도 견딘다)."""

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / FILE
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"users": {}}

    def _save(self, d: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + f".tmp-{secrets.token_hex(4)}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        from .osdep import replace_retry
        replace_retry(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def users(self) -> dict:
        return self._load().get("users", {})

    def add(self, name: str, password: str) -> None:
        check_name(name)
        check_password(password)
        with self._lock:
            d = self._load()
            if name in d["users"]:
                raise AccountError(f"이미 있는 사용자: {name}")
            d["users"][name] = _record(password)
            self._save(d)

    def set_password(self, name: str, password: str) -> None:
        check_password(password)
        with self._lock:
            d = self._load()
            if name not in d["users"]:
                raise AccountError(f"없는 사용자: {name}")
            d["users"][name].update(_record(password), changed=_now())
            self._save(d)

    def set_disabled(self, name: str, disabled: bool) -> None:
        with self._lock:
            d = self._load()
            if name not in d["users"]:
                raise AccountError(f"없는 사용자: {name}")
            d["users"][name]["disabled"] = bool(disabled)
            self._save(d)

    def active(self, name: str) -> bool:
        u = self.users().get(name)
        return bool(u) and not u.get("disabled")

    def verify(self, name: str, password: str) -> bool:
        u = self.users().get(name)
        if not u:
            _hash(password, b"\0" * 16, SCRYPT)   # 없는 이름도 같은 시간
            return False
        got = _hash(password, bytes.fromhex(u["salt"]), u.get("kdf") or SCRYPT)
        ok = hmac.compare_digest(got.hex(), u["hash"])
        return ok and not u.get("disabled")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _record(password: str) -> dict:
    salt = secrets.token_bytes(16)
    return {"salt": salt.hex(), "hash": _hash(password, salt, SCRYPT).hex(), "kdf": dict(SCRYPT), "created": _now(),
            "disabled": False}


def check_name(name: str) -> None:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise AccountError("사용자 이름은 소문자·숫자·'-' 1~32자(첫 글자는 소문자·숫자)")


def check_password(pw: str) -> None:
    if not isinstance(pw, str) or len(pw) < PASSWORD_MIN:
        raise AccountError(f"비밀번호는 {PASSWORD_MIN}자 이상")


class Sessions:
    """로그인 세션(서버 메모리) + 실패 지연."""

    def __init__(self, store: Store, now=time.monotonic):
        self.store, self._now = store, now
        self._s: dict[str, dict] = {}
        self._fails: dict[str, int] = {}
        self._lock = threading.Lock()

    def delay_for(self, name: str, addr: str) -> float:
        with self._lock:
            k = max(self._fails.get("u:" + name, 0), self._fails.get("a:" + addr, 0))
        return 0.0 if k == 0 else min(0.5 * 2 ** (k - 1), FAIL_DELAY_MAX)

    def login(self, name: str, password: str, addr: str) -> str | None:
        """맞으면 새 쿠키 값, 아니면 None. 지연은 부르는 쪽이 delay_for 로 건다(앞 실패 수 기준)."""
        ok = self.store.verify(name, password)
        with self._lock:
            if not ok:
                for k in ("u:" + name, "a:" + addr):
                    self._fails[k] = self._fails.get(k, 0) + 1
                return None
            self._fails.pop("u:" + name, None)
            self._fails.pop("a:" + addr, None)
            sid = secrets.token_urlsafe(24)
            t = self._now()
            self._s[sid] = {"user": name, "csrf": secrets.token_urlsafe(24), "born": t, "last": t}
            return sid

    def get(self, sid: str | None) -> dict | None:
        """살아 있는 세션(만료·막힌 사용자는 지우고 None). 볼 때마다 마지막 시각을 늘린다."""
        if not sid:
            return None
        with self._lock:
            s = self._s.get(sid)
            if s is None:
                return None
            t = self._now()
            if t - s["born"] > SESSION_MAX_S or t - s["last"] > SESSION_IDLE_S:
                self._s.pop(sid, None)
                return None
        if not self.store.active(s["user"]):
            with self._lock:
                self._s.pop(sid, None)
            return None
        s["last"] = self._now()
        return s

    def logout(self, sid: str | None) -> None:
        with self._lock:
            self._s.pop(sid or "", None)
