"""모델 서버 상태(허브 화면의 "모델 서버" 점)와 백엔드 오류를 사람 말로.

점은 실제로 찔러 본 결과로만 정한다(U2-8 실사용 지적: "연결됨" 이 떠 있는데 백엔드 오류).
- ok      : GET /models 가 200 이고 모델이 하나 이상 — "준비됨"
- loading : 닿지만 아직 모델이 없다(503·빈 목록, 파드 감시기는 답하는데 /models 는 안 됨) — "올라오는 중"
- down    : 닿지 않는다(연결 거부·시간 초과·이름 못 찾음) — "닿지 않음"
- auth    : 401·403 — "인증 거부"
- error   : 그 밖의 HTTP 오류
실제 모델 이름: /models 의 id(서버가 올린 이름)와, 파드 감시기(같은 호스트 :8001/status, loopback 이 아닐 때만)가 주는
별칭(config.model)·어댑터(config.adapter). 설정에 적은 이름(backend.model)과 다르면 둘 다 보인다.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .backend import BackendError, _headers, is_context_overflow

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
POD_STATUS_PORT = 8001


def _get(url: str, headers: dict, timeout: float):
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def _down_reason(e: Exception) -> str:
    s = str(getattr(e, "reason", e))
    if "refused" in s.lower() or "Errno 61" in s or "Errno 111" in s:
        return "연결 거부(서버가 아직 안 떴거나 꺼짐)"
    if "timed out" in s.lower() or isinstance(e, TimeoutError):
        return "시간 초과"
    if "nodename" in s or "Name or service" in s or "getaddrinfo" in s:
        return "주소를 찾지 못함"
    return s[:160]


def probe(backend, timeout: float = 4.0, pod: bool = True) -> dict:
    """한 번 찔러 본다. 돌려주는 것: {state, label, served: [id…], configured, pod: {model, adapter, served_name}|None, detail, checked}."""
    out = {"state": "down", "served": [], "configured": backend.model, "backend": backend.name, "pod": None,
           "detail": "", "checked": time.time()}
    try:
        code, text = _get(backend.base_url + "/models", _headers(backend), timeout)
        try:
            ids = [str(m.get("id")) for m in (json.loads(text).get("data") or []) if m.get("id")]
        except (ValueError, AttributeError):
            ids = []
        out["served"] = ids
        out["state"] = "ok" if ids else "loading"
        if not ids:
            out["detail"] = "서버는 답하지만 올린 모델이 없다"
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        out["state"] = "loading" if e.code == 503 else "auth" if e.code in (401, 403) else "error"
        out["detail"] = f"HTTP {e.code}" + (f": {body}" if body else "")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        out["state"], out["detail"] = "down", _down_reason(e)
    host = urlsplit(backend.base_url).hostname or ""
    if pod and host and host not in LOOPBACK:   # 빌린 장비면 감시기가 별칭·어댑터를 안다(읽기 전용, 인증 없음)
        u = urlsplit(backend.base_url)
        try:
            code, text = _get(f"{u.scheme}://{host}:{POD_STATUS_PORT}/status", {}, min(timeout, 3.0))
            cfg = (json.loads(text).get("config") or {})
            out["pod"] = {k: cfg.get(k) for k in ("model", "adapter", "served_name") if cfg.get(k)} or None
            if out["state"] == "down":          # 감시기는 답하는데 모델 서버만 안 됨 = 올리는 중
                out["state"], out["detail"] = "loading", "장비는 켜져 있고 모델 서버가 올라오는 중"
        except Exception:
            pass
    out["label"] = LABEL[out["state"]]
    out["mismatch"] = bool(out["served"]) and backend.model not in out["served"]
    return out


LABEL = {"ok": "준비됨", "loading": "올라오는 중", "down": "닿지 않음", "auth": "인증 거부", "error": "오류", "unknown": "확인 중"}


def explain(e: BackendError) -> tuple[str, str]:
    """백엔드 오류 → (종류, 사람 말 한 줄). 종류는 probe 의 state 와 같은 말(down·loading·auth·error)과 overflow·timeout."""
    msg, st, body = str(e), e.status, (e.body or "")
    low = (msg + " " + body).lower()
    if is_context_overflow(e):
        return "overflow", "문맥이 모델 서버의 최대 길이를 넘었다 — /compact 로 줄이거나 새 대화로"
    if st is None:
        if "timed out" in low or "timeout" in low:
            return "timeout", "모델 서버가 제때 답하지 않았다(시간 초과) — 서버가 바쁘거나 멈췄다. 잠시 뒤 다시 보내기"
        if "refused" in low or "errno 61" in low or "errno 111" in low:
            return "down", "모델 서버가 아직 안 올라왔다(연결 거부) — 올라오면 다시 보내기"
        if "nodename" in low or "getaddrinfo" in low or "name or service" in low:
            return "down", "모델 서버 주소를 찾지 못했다(망·이름) — 망이 돌아오면 다시 보내기"
        return "down", "모델 서버에 닿지 않는다 — 다시 보내기"
    if st == 503 or "loading model" in low:
        return "loading", "모델 서버가 아직 모델을 올리는 중이다 — 다 올라오면 다시 보내기"
    if st in (401, 403):
        return "auth", "모델 서버가 인증을 거부했다(API 키 확인)"
    if st == 404 and "model" in low:
        return "error", "모델 서버에 그 모델 이름이 없다(설정의 model 과 서버가 올린 이름이 다르다)"
    return "error", f"모델 서버 오류(HTTP {st})" + (f": {body[:160]}" if body else "")


class ModelWatch:
    """허브가 띄우는 감시: 준비됐으면 60초, 아니면 10초마다, 백엔드 오류가 나면 곧바로(poke) 다시 찔러 본다."""

    def __init__(self, backend, every_ok: float = 60, every_bad: float = 10, probe_fn=None):
        self.backend = backend
        self.every_ok, self.every_bad = every_ok, every_bad
        self._probe = probe_fn or probe
        self.status: dict = {"state": "unknown", "label": LABEL["unknown"], "served": [], "configured": backend.model,
                             "backend": backend.name, "pod": None, "detail": "", "checked": None, "mismatch": False}
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    def check(self) -> dict:
        try:
            st = self._probe(self.backend)
        except Exception as e:   # 감시가 허브를 멈추게 하지 않는다
            st = dict(self.status, state="error", label=LABEL["error"], detail=f"{type(e).__name__}: {e}"[:200],
                      checked=time.time())
        self.status = st
        return st

    def poke(self, state: str | None = None, detail: str = "") -> None:
        """백엔드 오류 직후: 화면 점을 곧바로 바꾸고(찔러 보기 전에도), 감시를 깨운다."""
        if state:
            self.status = dict(self.status, state=state, label=LABEL.get(state, state), detail=detail or self.status.get("detail"),
                               checked=time.time())
        self._wake.set()

    def start(self) -> None:
        def loop():
            while not self._stop.is_set():
                st = self.check()
                self._wake.wait(self.every_ok if st.get("state") == "ok" else self.every_bad)
                self._wake.clear()
        self._t = threading.Thread(target=loop, daemon=True)
        self._t.start()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
