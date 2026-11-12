"""허브 웹 화면(로컬). 127.0.0.1 에만 묶는다.

인증
- 허브가 시작할 때 무작위 토큰을 만들어 <허브 폴더>/hub-token(0600)에 쓴다.
- /login 에 그 토큰을 붙여 넣으면(POST) 세션 쿠키를 준다(HttpOnly, SameSite=Strict).
- 모든 요청: Host 머리가 127.0.0.1:<포트>·localhost:<포트>(또는 설정한 이름)가 아니면 거부(DNS rebinding 막기).
- 상태를 바꾸는 요청은 POST 만 받는다. 쿠키 + X-Stewart-Token 머리(화면에 심은 값) + Origin 이 같은 곳이어야 한다
  (Origin: null 도 거부. Referrer-Policy 는 same-origin — no-referrer 면 폼 POST 의 Origin 이 null 이 된다).
- CORS 머리를 내보내지 않는다.

승인 답: POST /api/answer 가 Hub.answer 를 부르는 유일한 자리다.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, urlparse

COOKIE = "stewart_hub"
IMAGES_MAX = 4   # 한 번에 붙이는 이미지 장수
UPLOAD_MAX = 20 * 1024 * 1024   # 올리는 파일 하나(판 106)
BODY_MAX = 64 * 1024


LOST_MSG = {   # 로그인 화면에 보이는 "왜 다시 로그인하나"
    "token": "<p>허브의 로그인 토큰이 새로 만들어져(--new-token) 예전 로그인이 끝났다. 새 토큰: <code>stewart hub url</code></p>",
    "new_state": "<p>허브가 처음 보는 상태 폴더로 켜졌다(다른 폴더에서 켰거나 --state-dir 이 바뀜) — 그 폴더의 토큰으로 들어간다: <code>stewart hub url</code></p>",
    "unknown": "<p>이 브라우저의 로그인을 이 허브가 모른다(만료되었거나 다른 허브의 로그인). 토큰: <code>stewart hub url</code></p>",
}
SESSION_DAYS = 30   # 한 번 로그인하면 브라우저가 이만큼 기억한다(허브를 다시 켜도). 끊으려면 --new-token


def make_token(state_dir: Path, reuse: bool = False) -> str:
    """로그인 토큰. reuse 면 상태 폴더의 것을 그대로 쓴다(허브를 다시 켜도 같은 토큰 — 매번 터미널에서 찾지 않게).
    파일은 0600, 모양이 이상하면 새로 만든다."""
    p = Path(state_dir) / "hub-token"
    if reuse and p.is_file():
        try:
            old = p.read_text(encoding="utf-8").strip()
        except OSError:
            old = ""
        if len(old) >= 32 and all(c.isalnum() or c in "-_" for c in old):
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
            return old
    tok = secrets.token_urlsafe(32)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok + "\n")
    os.chmod(p, 0o600)
    return tok


UI_DIRS = {"new": "web_ui"}   # 새 화면 빌드(최신 브라우저). 옛 브라우저 판은 이 저장소에 없다


def _page(kind: str = "new") -> str:
    """화면 HTML. new = 새 화면(web_ui/), old = 예전 한 파일 화면(되돌릴 길)."""
    if kind in UI_DIRS:
        p = resources.files("stewart").joinpath(UI_DIRS[kind], "index.html")
        if p.is_file():   # 빌드 지문을 ?v= 로 붙여 옛 app.js 를 브라우저가 붙잡지 않게(판 65)
            h = resources.files("stewart").joinpath(UI_DIRS[kind], "SOURCE_HASH")
            v = h.read_text(encoding="utf-8").strip()[:12] if h.is_file() else ""
            page = p.read_text(encoding="utf-8")
            if v:
                page = page.replace('/ui/app.js"', f'/ui/app.js?v={v}"').replace('/ui/app.css"', f'/ui/app.css?v={v}"')
            return page
    return resources.files("stewart").joinpath("web_page.html").read_text(encoding="utf-8")


def ui_kind(query: str, user_agent: str = "", default: str | None = None) -> str:
    """고르는 차례: ?ui=new|old → 설정 [hub] ui → new. (user_agent 는 받기만 — 브라우저 판으로 고르기는 그 가지에만)"""
    if query in ("new", "old"):
        return query
    if default in ("new", "old"):
        return default
    return "new"


UI_ASSETS = {"app.js": "text/javascript; charset=utf-8", "app.css": "text/css; charset=utf-8"}   # /ui/<이름>(이 이름만)


def _asset(name: str, kind: str = "new") -> bytes | None:
    if name not in UI_ASSETS:
        return None
    p = resources.files("stewart").joinpath(UI_DIRS[kind], name)
    return p.read_bytes() if p.is_file() else None


LOGIN = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>stewart hub 로그인</title>
<style>body{font-family:system-ui,sans-serif;max-width:28rem;margin:3rem auto;padding:0 1rem}
input,button{font-size:1rem;padding:.6rem;width:100%;box-sizing:border-box;margin:.4rem 0}</style></head>
<body><h1>stewart hub</h1><p>허브를 띄운 터미널에 나온 토큰을 붙여 넣으십시오.</p>
<form method="post" action="/login"><input name="token" type="password" autocomplete="off" autofocus>
<button type="submit">들어가기</button></form>{msg}</body></html>"""


SW_JS = """// stewart hub 서비스 워커: 알림을 띄우고(폰 Chrome 은 페이지에서 new Notification 이 안 된다) 누르면 허브 화면으로.
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({type: 'window'}).then(cs => cs.length ? cs[0].focus() : self.clients.openWindow('/')));
});
"""


LOGIN_ACCOUNTS = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>stewart hub 로그인</title>
<style>body{font-family:system-ui,sans-serif;max-width:28rem;margin:3rem auto;padding:0 1rem}
input,button{font-size:1rem;padding:.6rem;width:100%;box-sizing:border-box;margin:.4rem 0}</style></head>
<body><h1>stewart hub</h1><p>사용자 이름과 비밀번호를 넣으십시오.</p>
<form method="post" action="/login"><input name="user" autocomplete="username" autofocus placeholder="사용자 이름">
<input name="password" type="password" autocomplete="current-password" placeholder="비밀번호">
<button type="submit">들어가기</button></form>{msg}</body></html>"""
LOOPBACK = ("127.0.0.1", "::1", "localhost")


class WebApp:
    def __init__(self, main, hub, token: str | None, host: str = "127.0.0.1", port: int = 0, extra_hosts=(),
                 accounts=None, users=None, state_dir=None):
        """accounts(accounts.Sessions)·users(hubmain.UserHubs)를 주면 사용자 계정 모드(U2-7): 사람마다 따로 도는 허브를
        로그인한 사람에게만 보인다. 그때만 127.0.0.1 밖 주소(정한 주소 하나)에 묶을 수 있다."""
        if host not in LOOPBACK and accounts is None:
            raise ValueError("웹 화면은 loopback 에만 묶는다(원격은 tailscale serve 같은 앞단으로, 또는 [hub] accounts = true)")
        if host in ("0.0.0.0", "::", ""):
            raise ValueError("듣는 주소는 정한 주소 하나만(0.0.0.0·:: 처럼 모든 주소는 받지 않는다)")
        import threading as _th
        self._tl = _th.local()                 # 요청마다 로그인한 사람의 메인·허브(계정 모드)
        self._main0, self._hub0, self.token = main, hub, token
        self.accounts, self.users = accounts, users
        self.sessions: dict[str, str] = {}   # 쿠키 값 → 화면 토큰(CSRF)
        self._exp: dict[str, float] = {}      # 쿠키 값 → 끝나는 때(epoch)
        # state_dir 가 있으면 로그인을 파일(0600)에 남겨 허브를 다시 켜도 그대로 들어간다(토큰이 바뀌면 버린다)
        self._sess_file = Path(state_dir) / "web-sessions.json" if state_dir else None
        self.lost_reason = "" if (self._sess_file and self._sess_file.is_file()) or not state_dir else "new_state"   # 처음 보는 상태 폴더
        self._load_sessions()
        self._conv: dict[str, dict] = {}     # 기록 파일 → 읽은 자리·대화 줄(세션 대화 보기)
        self._conv_lock = threading.Lock()
        self.extra_hosts = tuple(extra_hosts)
        self.ui_default = None   # [hub] ui = "old" 면 예전 한 파일 화면
        app = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "stewart-hub"

            def log_message(self, *a):
                pass

            def do_GET(self):
                app.handle(self, "GET")

            def do_POST(self):
                app.handle(self, "POST")

            def do_PUT(self):
                app.handle(self, "PUT")

            def do_DELETE(self):
                app.handle(self, "DELETE")

            def do_OPTIONS(self):
                app.handle(self, "OPTIONS")

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.cookie = f"{COOKIE}_{self.port}"   # 쿠키는 포트를 가리지 않는다 — 같은 브라우저의 두 허브(8765·8766)가 서로 덮지 않게
        self.allowed_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}", *self.extra_hosts}
        if host not in LOOPBACK:   # 계정 모드의 정한 주소(사무실 망 주소 등)
            self.allowed_hosts.add(f"[{host}]:{self.port}" if ":" in host else f"{host}:{self.port}")
        self.origins = {f"http://{h}" for h in self.allowed_hosts} | {f"https://{h}" for h in self.extra_hosts}

    @property
    def main(self):
        return getattr(self._tl, "main", None) if self.accounts is not None else self._main0

    @main.setter
    def main(self, v):
        self._main0 = v

    @property
    def hub(self):
        return getattr(self._tl, "hub", None) if self.accounts is not None else self._hub0

    @hub.setter
    def hub(self, v):
        self._hub0 = v

    # ── 응답 ──
    @staticmethod
    def _send(h, code: int, body: bytes | str, ctype: str = "application/json; charset=utf-8", headers: dict | None = None):
        data = body.encode() if isinstance(body, str) else body
        h.send_response(code)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(data)))
        h.send_header("Cache-Control", "no-store")
        h.send_header("X-Content-Type-Options", "nosniff")
        h.send_header("X-Frame-Options", "DENY")
        # no-referrer 이면 브라우저가 폼 POST 에 Origin: null 을 실어 로그인이 막힌다. same-origin 은 같은 곳에만 보낸다
        h.send_header("Referrer-Policy", "same-origin")
        h.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline' 'self'; "
                                                 "style-src 'unsafe-inline' 'self'; connect-src 'self'; img-src 'self' data: blob:; "
                                                 "frame-ancestors 'none'")   # img-src: 사진 붙이기 전처리가 data: 그림을 디코드한다(판 74)
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        h.end_headers()
        h.wfile.write(data)

    def _json(self, h, code: int, obj, headers: dict | None = None) -> None:
        self._send(h, code, json.dumps(obj, ensure_ascii=False), headers=headers)

    def _cookie(self, h) -> str | None:
        for part in (h.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k not in (self.cookie, COOKIE):   # 옛 이름(포트 없는)도 읽어, 올린 뒤 한 번은 다시 로그인하지 않게
                continue
            if self.accounts is not None:   # 계정 모드는 이 포트 이름만(옛 이름은 다른 허브 것일 수 있다)
                if k == self.cookie:
                    return v
                continue
            if v in self.sessions:
                if self._exp.get(v, float("inf")) < time.time():
                    self.sessions.pop(v, None)
                    self._exp.pop(v, None)
                    return None
                return v
        return None

    def _tok_id(self) -> str:
        import hashlib
        return hashlib.sha256(self.token.encode()).hexdigest()[:16]

    def _load_sessions(self) -> None:
        if self._sess_file is None or not self._sess_file.is_file():
            return
        try:
            d = json.loads(self._sess_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if d.get("token") != self._tok_id():      # 토큰을 바꿨으면(--new-token) 예전 로그인은 모두 끝
            self.lost_reason = "token"
            return
        now = time.time()
        for sid, x in (d.get("sessions") or {}).items():
            if isinstance(x, dict) and isinstance(x.get("csrf"), str) and float(x.get("exp") or 0) > now:
                self.sessions[sid], self._exp[sid] = x["csrf"], float(x["exp"])

    def _save_sessions(self) -> None:
        if self._sess_file is None:
            return
        now = time.time()
        d = {"token": self._tok_id(), "sessions": {sid: {"csrf": c, "exp": self._exp.get(sid, now)}
                                                   for sid, c in self.sessions.items() if self._exp.get(sid, now) > now}}
        tmp = self._sess_file.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.chmod(tmp, 0o600)
        from . import osdep
        osdep.replace_retry(tmp, self._sess_file)

    def _stale_cookie(self, h) -> str:
        """이 브라우저가 로그인 쿠키를 들고 왔는데 이 허브가 모르는 까닭: token(토큰을 새로 만듦) · new_state(다른 상태 폴더로 켜짐)
        · unknown(그 밖 — 다른 허브의 쿠키 등). 쿠키가 없으면 ""."""
        names = {p.strip().partition("=")[0] for p in (h.headers.get("Cookie") or "").split(";")}
        if not names & {self.cookie, COOKIE}:
            return ""
        return self.lost_reason or "unknown"

    def _who(self, h) -> tuple[str | None, str | None, str]:
        """(쿠키 값, CSRF 값, 사용자 이름) — 로그인 안 됐으면 (None, None, ""). 계정 모드면 그 사람의 메인·허브를 이 요청에 건다."""
        sid = self._cookie(h)
        if sid is None:
            return None, None, ""
        if self.accounts is None:
            return sid, self.sessions[sid], ""
        s = self.accounts.get(sid)
        if s is None:
            return None, None, ""
        self._tl.main, self._tl.hub = self.users.get(s["user"])
        return sid, s["csrf"], s["user"]

    def _body(self, h, limit: int = BODY_MAX) -> bytes | None:
        n = int(h.headers.get("Content-Length") or 0)
        if n > limit:
            return None
        return h.rfile.read(n) if n else b""

    # ── 처리 ──
    def handle(self, h, method: str) -> None:
        if (h.headers.get("Host") or "") not in self.allowed_hosts:
            return self._json(h, 421, {"error": "host"})
        url = urlparse(h.path)
        path = url.path
        if method not in ("GET", "POST"):
            return self._json(h, 405, {"error": "method"})
        if method == "POST" and (h.headers.get("Origin") or "") not in self.origins:
            return self._json(h, 403, {"error": "origin"})
        if path == "/login":
            return self.login(h, method)
        self._tl.main = self._tl.hub = None
        sid, csrf, user = self._who(h)
        if sid is None:
            stale = self._stale_cookie(h)
            if stale and self._hub0 is not None and not getattr(self, "_lost_logged", False):
                self._lost_logged = True
                try:
                    self._hub0.event("login_lost", reason=stale, path=path)
                except Exception:
                    pass
            if method == "GET" and path == "/":
                return self._send(h, 303, b"", headers={"Location": "/login" + (f"?why={stale}" if stale else "")})
            return self._json(h, 401, {"error": "login"})
        if method == "POST":
            if not hmac.compare_digest(h.headers.get("X-Stewart-Token") or "", csrf):
                return self._json(h, 403, {"error": "token"})
            if path == "/logout":   # 계정 모드: 서버 쪽 세션을 지우고 쿠키를 비운다
                if self.accounts is not None:
                    self.accounts.logout(sid)
                else:
                    self.sessions.pop(sid, None)
                return self._json(h, 200, {"ok": True}, headers={"Set-Cookie": f"{self.cookie}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
            from .images import max_bytes
            # 메인에게 보내는 글에는 이미지가 붙을 수 있다(base64 는 4/3 배, 장수 IMAGES_MAX)
            body = self._body(h, BODY_MAX + IMAGES_MAX * (max_bytes() * 4 // 3 + 1024) if path == "/api/main"
                              else UPLOAD_MAX * 4 // 3 + 4096 if path == "/api/main/upload" else BODY_MAX)
            if body is None:
                return self._json(h, 413, {"error": "too_large"})
            try:
                data = json.loads(body or b"{}")
            except ValueError:
                return self._json(h, 400, {"error": "json"})
            if path == "/api/main":
                return self.main_send(h, data)
            if path == "/api/main/upload":   # 판 106: 그림 밖 파일 올리기 → 작업 폴더 inbox/
                return self.upload(h, data)
            if path == "/api/main/undo_turn":   # 판 105: {turn, cut} — cut=false 면 파일만, true 면 차례를 지우고 말을 돌려줌
                if not isinstance(data.get("turn"), int) or not isinstance(data.get("cut"), bool):
                    return self._json(h, 400, {"error": "fields"})
                r = self.main.conv(data.get("chat") if isinstance(data.get("chat"), str) else None).undo_turn(data["turn"], data["cut"])
                return self._json(h, 200 if r.get("ok") else 409, r)
            if path == "/api/main/mode":   # 판 103: 머리줄 모드(켤 때 모드보다 넓히지 못함)
                if not isinstance(data.get("mode"), str):
                    return self._json(h, 400, {"error": "fields"})
                r = self.main.conv(data.get("chat") if isinstance(data.get("chat"), str) else None).set_mode(data["mode"])
                return self._json(h, 200 if r.get("ok") else 400, r)
            if path == "/api/main/budget":   # 판 105: 대화별 문맥 예산 {tokens: 정수 | null}
                r = self.main.conv(data.get("chat") if isinstance(data.get("chat"), str) else None).set_budget(data.get("tokens"))
                return self._json(h, 200 if r.get("ok") else 400, r)
            if path == "/api/main/think":   # 판 103: 생각 켬·끔·기본(null)
                r = self.main.conv(data.get("chat") if isinstance(data.get("chat"), str) else None).set_think(data.get("think"))
                return self._json(h, 200 if r.get("ok") else 400, r)
            if path == "/api/tools/toggle":   # 판 99: 도구 켬·끔 손잡이
                if not isinstance(data.get("on"), bool) or not isinstance(data.get("name"), str):
                    return self._json(h, 400, {"error": "fields"})
                cv = self.main.conv(data.get("chat") if isinstance(data.get("chat"), str) else None)
                r = cv.set_tool(data["name"], data["on"])
                return self._json(h, 200 if r.get("ok") else 400, r)
            if path == "/api/train/label":   # 답 줄 👍👎·교정됨 떼기(판 89): {turn, label: good|bad|clear|uncorrect, chat?}
                if not isinstance(data.get("turn"), int) or not isinstance(data.get("label"), str):
                    return self._json(h, 400, {"error": "fields"})
                cv = self.main.conv(data.get("chat") if isinstance(data.get("chat"), str) else None)
                r = cv.train_label(data["turn"], data["label"])
                return self._json(h, 200 if r.get("ok") else 400, r)
            if path == "/api/improve/apply":   # 배워 두기 카드(판 93): accept = [번호…] 또는 "all"
                acc = data.get("accept")
                if acc != "all" and not (isinstance(acc, list) and all(isinstance(x, int) for x in acc)):
                    return self._json(h, 400, {"error": "fields", "detail": "accept 는 번호 목록 또는 \"all\""})
                cv = self.main.conv(data.get("chat") if isinstance(data.get("chat"), str) else None)
                r = cv.improve_apply(None if acc == "all" else acc)
                return self._json(h, 200 if "error" not in r else 409, r)
            if path == "/api/memory/consolidate":   # 기억 정리 카드(판 88): accept = [번호…] 또는 "all"
                acc = data.get("accept")
                if acc != "all" and not (isinstance(acc, list) and all(isinstance(x, int) for x in acc)):
                    return self._json(h, 400, {"error": "fields", "detail": "accept 는 번호 목록 또는 \"all\""})
                cv = self.main.conv(data.get("chat") if isinstance(data.get("chat"), str) else None)
                r = cv.memory_apply(None if acc == "all" else acc)
                return self._json(h, 200 if "error" not in r else 409, r)
            if path in ("/api/memory/forget", "/api/memory/update", "/api/memory/deny", "/api/memory/add", "/api/memory/confirm"):   # 기억 자리(판 52·83)
                mem = getattr(self.main.sess, "mem", None)
                if mem is None:
                    return self._json(h, 409, {"error": "memory_off", "detail": "기억이 꺼져 있다(STEWART_MEMORY=0 또는 프로필 memory = false)"})
                i, t = data.get("id"), data.get("text")
                if path == "/api/memory/forget":
                    ok = isinstance(i, str) and mem.forget(i)
                elif path == "/api/memory/update":   # 판 83: kind 를 주면 갈래 바꾸기
                    k = data.get("kind") if data.get("kind") in ("policy", "profile", "knowledge") else None
                    ok = isinstance(i, str) and isinstance(t, str) and mem.update(i, t, kind=k) is not None
                elif path == "/api/memory/deny":
                    ok = mem.deny(i if isinstance(i, str) else None, t if isinstance(t, str) else None)
                elif path == "/api/memory/confirm":   # 묻는 기억 카드: [허용](keep) · [이번만]
                    r = mem.confirm(i, bool(data.get("keep"))) if isinstance(i, str) else None
                    if r:
                        self.main.ui.memory_op("add", r)
                    ok = r is not None
                else:
                    kind = data.get("kind") if data.get("kind") in ("user", "feedback", "project", "reference", "policy", "profile", "knowledge") else "explicit"
                    scope = data.get("scope") if data.get("scope") in ("user", "project") else ("project" if kind in ("project", "reference") else "user")
                    ok = isinstance(t, str) and mem.remember(kind, t, source="human", scope=scope) is not None
                return self._json(h, 200 if ok else 404, {"ok": bool(ok)})
            if path == "/api/board/close":   # 사람이 화면에서 자기 할 일을 닫는다(했음·안 함)
                tid, st = data.get("id"), data.get("status", "done")
                if not isinstance(tid, str) or st not in ("done", "dropped"):
                    return self._json(h, 400, {"error": "fields"})
                try:
                    t = self.hub.board.close_todo(tid, st, str(data.get("outcome") or ""), by="사용자")
                except ValueError as e:
                    return self._json(h, 404, {"error": "board", "detail": str(e)})
                self._notice(f"사람이 현황판 {t['id']}({t['title']})을 {'했다' if st == 'done' else '안 하기로 했다'}" +
                             (f": {t['outcome']}" if t.get("outcome") else ""))
                return self._json(h, 200, {"ok": True})
            if path in ("/api/main/stop", "/api/main/compact", "/api/main/new", "/api/main/reopen", "/api/chat/new", "/api/chat/close", "/api/main/unban"):
                try:   # 어느 대화인가(SW-29): body 의 chat(없으면 메인)
                    cv = self.main.conv(data.get("chat") if isinstance(data.get("chat"), str) else None)
                except KeyError:
                    return self._json(h, 404, {"error": "no_chat", "detail": "닫혔거나 없는 대화다 — 화면을 새로 고친다"})
            if path == "/api/chat/new":   # 새 대화: fresh(격리)·fork(지금 대화 복사해 갈라짐)·reopen(지난 기록을 새 칸으로)
                try:
                    c = self.main.new_chat(str(data.get("how") or "fresh"), source=str(data.get("from") or "main"),
                                           run_dir=str(data.get("run_dir") or ""), title=str(data.get("title") or "")[:80])
                except (ValueError, KeyError) as e:
                    return self._json(h, 400, {"error": "chat", "detail": str(e)})
                except RuntimeError as e:
                    return self._json(h, 409, {"error": "chat", "detail": str(e)})
                return self._json(h, 200, {"ok": True, "chat": c.name, "title": c.title})
            if path == "/api/chat/close":
                try:
                    return self._json(h, 200, {"ok": True, "run_dir": self.main.close_chat(str(data.get("chat") or ""))})
                except KeyError:
                    return self._json(h, 404, {"error": "no_chat"})
                except RuntimeError as e:
                    return self._json(h, 409, {"error": "chat", "detail": str(e)})
            if path == "/api/main/unban":   # 되짚기가 건 금지를 사람이 푼다
                ok = isinstance(data.get("id"), str) and cv.sess.unban(data["id"])
                if ok:
                    self._notice("사람이 되짚기 금지 하나를 풀었다 — 그 호출을 다시 할 수 있다")
                return self._json(h, 200 if ok else 404, {"ok": ok})
            if path == "/api/main/compact":   # 화면 [줄이기]·[요약해 접기](판 65) — 일하는 중이면 409
                try:
                    return self._json(h, 200, cv.compact_now(str(data.get("mode") or "clip")))
                except (ValueError, RuntimeError) as e:
                    return self._json(h, 409, {"error": "compact", "detail": str(e)})
            if path == "/api/main/stop":   # 화면 [멈춤]: 메인 차례 끊기
                return self._json(h, 200, {"result": cv.stop_turn()})
            if path == "/api/main/new":   # 화면 [새 대화]·/clear: 지금 대화는 기록에 남기고 새로(일하는 중이면 409)
                try:
                    return self._json(h, 200, {"ok": True, "run_dir": cv.new_main()})
                except RuntimeError as e:
                    return self._json(h, 409, {"error": "new_main", "detail": str(e)})
            if path == "/api/main/reopen":   # 지난 메인 대화로 다시 열기(목록에 든 기록만)
                try:
                    return self._json(h, 200, {"ok": True, "run_dir": cv.reopen_main(str(data.get("run_dir") or ""))})
                except (ValueError, RuntimeError) as e:
                    return self._json(h, 409, {"error": "reopen", "detail": str(e)})
            if path == "/api/answer":
                return self.answer_handler(h, data)
            if path == "/api/session/new":
                return self.new_session(h, data)
            if path == "/api/session/send":
                return self.session_send(h, data)
            if path == "/api/session/control":
                return self.session_control(h, data)
            if path == "/api/rules/test_allow":   # 프로젝트 시험 명령 한 번에 늘 허용(후보에 든 꼴만)
                return self.test_allow(h, data)
            if path == "/api/sessions/manage":   # 지난 세션 지우기(휴지통)·되돌리기·비우기·이름·보관
                return self.manage_sessions(h, data)
            if path == "/api/rules/add":   # 사람이 손으로 적는 늘 허용(정확한 명령 · "앞부분 *" · "글*" · Bash(…:*))
                pat, scope = data.get("pattern"), data.get("scope", "project")
                if not isinstance(pat, str) or scope not in ("session", "project"):
                    return self._json(h, 400, {"error": "fields"})
                from .allowrules import broad_note, check_always
                why = check_always(pat, broad_ok=True)
                if why:
                    return self._json(h, 400, {"error": "pattern", "detail": why})
                try:
                    r = self.hub.add_allow(pat, scope)
                except ValueError as e:
                    return self._json(h, 400, {"error": "pattern", "detail": str(e)})
                self._notice(f"사람이 허브 화면에서 늘 허용 규칙을 더했다: {r.pattern} ({'이 세션만' if scope == 'session' else '이 프로젝트 늘'})")
                return self._json(h, 200, {"ok": True, "id": r.id, "pattern": r.pattern, "warn": broad_note(r.pattern)})
            if path == "/api/rules/delete":
                rid = data.get("id")
                if not isinstance(rid, str):
                    return self._json(h, 400, {"error": "fields"})
                return self._json(h, 200, {"ok": self.hub.remove_allow(rid)})
            return self._json(h, 404, {"error": "not_found"})
        # GET
        if path == "/":
            import html as _html
            kind = ui_kind((parse_qs(url.query).get("ui") or [""])[0], h.headers.get("User-Agent") or "", self.ui_default)
            page = _page(kind).replace("__CSRF__", csrf).replace("__USER__", _html.escape(user))
            return self._send(h, 200, page, "text/html; charset=utf-8")
        if path.startswith("/ui/"):
            name = path[4:]
            data = _asset(name)
            if data is None:
                return self._json(h, 404, {"error": "not_found"})
            return self._send(h, 200, data, UI_ASSETS[name])
        if path == "/api/state":
            try:
                return self._json(h, 200, self.main.conv(self._chat_q(url)).state())
            except KeyError:
                return self._json(h, 200, {**self.main.state(), "chat_gone": True})
        if path == "/api/past_search":   # 판 112: 지난 대화 본문 찾기(사람용)
            from .trash import past_search
            q = (parse_qs(url.query).get("q") or [""])[0][:200]
            m = self.main
            return self._json(h, 200, {"hits": past_search(m._runs_dir, Path(m._profile.workdir), q)})
        if path == "/api/status_info":   # 판 111: [상태] 탭
            return self._json(h, 200, self._conv_q(url).status_info())
        if path == "/api/file":   # 판 104: 작업 폴더 파일 열기·내려받기(읽기 전용, @파일과 같은 경계, HTML·SVG 는 내려받기만)
            return self.serve_file(h, url)
        if path == "/api/img":   # 작업 카드 그림(판 91): 이 화면이 받은 이름만(images.store 이름 꼴), 그 밖은 404
            from .hubmain import IMG_NAME_RX
            name = (parse_qs(url.query).get("n") or [""])[0]
            cv = self._conv_q(url)
            f = getattr(cv.ui, "imgs", {}).get(name) if IMG_NAME_RX.match(name) else None
            if not f and IMG_NAME_RX.match(name):   # 허브를 다시 켠 뒤·지난 대화: 이 허브 기록 폴더의 images/ 에서(이름 꼴 그대로만)
                runs = getattr(cv, "_runs_dir", None)
                hit = next((x for x in sorted(Path(runs).glob(f"*/images/{name}"), reverse=True)), None) if runs else None
                f = str(hit) if hit else None
            if not f or not Path(f).is_file():
                return self._json(h, 404, {"error": "not_found"})
            ext = name.rsplit(".", 1)[1]
            return self._send(h, 200, Path(f).read_bytes(), {"jpg": "image/jpeg"}.get(ext, "image/" + ext),
                              headers={"Cache-Control": "private, max-age=3600"})
        if path == "/sw.js":
            return self._send(h, 200, SW_JS, "text/javascript; charset=utf-8", headers={"Service-Worker-Allowed": "/"})
        if path == "/api/session":
            name = (parse_qs(url.query).get("name") or [""])[0]
            return self._json(h, 200, self.session_detail(name))
        if path == "/api/session/conversation":
            q = parse_qs(url.query)
            name = (q.get("name") or [""])[0]
            num = lambda k: int((q.get(k) or ["-1"])[0]) if (q.get(k) or [""])[0].lstrip("-").isdigit() else None   # noqa: E731
            return self._json(h, 200, self.session_conversation(name, before=num("before"), limit=num("limit"),
                                                                 prior=(q.get("prior") or ["0"])[0] == "1"))
        if path == "/api/stream":
            return self.stream(h, self._chat_q(url))
        if path == "/api/commands":   # 입력 칸 /명령 자동완성: 프로젝트·사용자 명령(.stewart/commands 등)
            from .hubmain import HUB_COMMANDS
            return self._json(h, 200, {"commands": [{"name": "/" + n, "description": d, "builtin": True} for n, d in HUB_COMMANDS]
                                       + [{"name": c.name, "description": c.description}
                                          for c in getattr(self._conv_q(url).sess, "commands", [])]})
        if path == "/api/files":      # 입력 칸 @파일: 작업 폴더 안 파일 이름(비밀 파일·.gitignore 빼고)
            q = (parse_qs(url.query).get("q") or [""])[0]
            return self._json(h, 200, {"files": self.file_names(q, self._conv_q(url))})
        if path == "/api/past_sessions":
            return self._json(h, 200, self.past_sessions(archived=(parse_qs(url.query).get("archived") or ["0"])[0] == "1"))
        if path == "/api/test_commands":
            return self._json(h, 200, {"offers": self.test_offers()})
        if path == "/api/board/ledger":   # 결정 장부 내보내기(마크다운 내려받기, 판 53)
            board = getattr(self.hub, "board", None)
            if board is None:
                return self._json(h, 404, {"error": "board"})
            return self._send(h, 200, board.ledger_md(), "text/markdown; charset=utf-8",
                              headers={"Content-Disposition": 'attachment; filename="stewart-ledger.md"'})
        if path == "/api/memory":   # 기억 목록(종류·범위·근거·언제)
            mem = getattr(self.main.sess, "mem", None)
            if mem is None:
                return self._json(h, 200, {"on": False, "items": [], "deny": []})
            items = [x for x in mem.remembered() if x.get("scope", "user") == "user" or x.get("place") == mem.place["key"]]
            _, ilog = mem._user_part()
            return self._json(h, 200, {"on": True, "items": items[::-1], "deny": mem.denied()[::-1], "place": mem.place["name"],
                                       "md": mem.md(), "pending": mem.pending()[::-1], "dir": str(mem.store.dir) if mem.md() else None,
                                       "inject": {k: ilog.get(k) for k in ("policy", "profile", "knowledge", "profile_over", "bytes")}})
        if path == "/api/rules":
            from .allowrules import broad_note
            return self._json(h, 200, {"rules": [{"id": r.id, "pattern": r.pattern, "warn": broad_note(r.pattern),
                                                  "scope": "session" if r.origin == "session" else "project"}
                                                 for r in self.hub.allow_rules()]})
        if path in ("/api/answer", "/api/main", "/api/main/stop", "/api/main/compact", "/api/main/new", "/api/main/reopen", "/api/rules/delete", "/logout", "/api/rules/add", "/api/sessions/manage", "/api/main/unban", "/api/board/close", "/api/memory/forget", "/api/memory/update", "/api/memory/deny", "/api/memory/add", "/api/memory/confirm", "/api/memory/consolidate", "/api/improve/apply", "/api/train/label", "/api/tools/toggle", "/api/main/mode", "/api/main/think", "/api/main/undo_turn", "/api/main/budget", "/api/main/upload", "/api/rules/test_allow", "/api/session/new", "/api/session/send",
                    "/api/session/control"):
            return self._json(h, 405, {"error": "method"})
        return self._json(h, 404, {"error": "not_found"})

    def login(self, h, method: str) -> None:
        if self.accounts is not None:
            return self.login_accounts(h, method)
        if method == "GET":
            why = (parse_qs(urlsplit(h.path).query).get("why") or [""])[0]
            return self._send(h, 200, LOGIN.replace("{msg}", LOST_MSG.get(why, "")), "text/html; charset=utf-8")
        body = self._body(h) or b""
        tok = (parse_qs(body.decode("utf-8", "replace")).get("token") or [""])[0].strip()
        if not hmac.compare_digest(tok, self.token):
            time.sleep(0.5)
            return self._send(h, 403, LOGIN.replace("{msg}", "<p>토큰이 맞지 않습니다.</p>"), "text/html; charset=utf-8")
        sid = secrets.token_urlsafe(24)
        self.sessions[sid] = secrets.token_urlsafe(24)
        age = SESSION_DAYS * 86400 if self._sess_file is not None else None
        if age:
            self._exp[sid] = time.time() + age
            try:
                self._save_sessions()
            except OSError:
                pass
        return self._send(h, 303, b"", headers={"Location": "/",
                                                 "Set-Cookie": f"{self.cookie}={sid}; HttpOnly; SameSite=Strict; Path=/"
                                                 + (f"; Max-Age={age}" if age else "")})

    def login_accounts(self, h, method: str) -> None:
        """계정 모드 로그인: 이름·비밀번호(scrypt 대조). 연속 실패면 응답 전에 지연. 없는 이름도 같은 글·같은 지연."""
        if method == "GET":
            return self._send(h, 200, LOGIN_ACCOUNTS.replace("{msg}", ""), "text/html; charset=utf-8")
        body = self._body(h) or b""
        q = parse_qs(body.decode("utf-8", "replace"))
        name = (q.get("user") or [""])[0].strip().lower()
        pw = (q.get("password") or [""])[0]
        addr = h.client_address[0] if getattr(h, "client_address", None) else ""
        time.sleep(self.accounts.delay_for(name, addr))
        sid = self.accounts.login(name, pw, addr)
        if sid is None:
            return self._send(h, 403, LOGIN_ACCOUNTS.replace("{msg}", "<p>이름이나 비밀번호가 맞지 않습니다.</p>"),
                              "text/html; charset=utf-8")
        return self._send(h, 303, b"", headers={"Location": "/",
                                                 "Set-Cookie": f"{self.cookie}={sid}; HttpOnly; SameSite=Strict; Path=/"})

    def answer_handler(self, h, data: dict) -> None:
        """대기열 답: 사람이 화면에서 고른 y / n / 모델에 할 말. Hub.answer 를 부르는 유일한 자리."""
        name, seq, text = data.get("session"), data.get("seq"), data.get("answer")
        always = data.get("always")
        if not isinstance(name, str) or not isinstance(seq, int) or not isinstance(text, str):
            return self._json(h, 400, {"error": "fields"})
        q = next((x for x in self.hub.queue() if x["session"] == name and x["seq"] == seq), None)
        if q is None:
            return self._json(h, 409, {"error": "not_waiting"})
        if always is not None:   # "이 꼴 늘 허용": 셸 실행 확인에서만, 답은 허용(y). 이은 명령은 조각마다 꼴 하나씩
            pats = [always] if isinstance(always, str) else always
            scope = data.get("scope", "session")
            if not isinstance(pats, list) or not pats or not all(isinstance(x, str) for x in pats) or len(pats) > 20 \
                    or text != "y" or q.get("kind") != "confirm" or (q.get("args_summary") or {}).get("tool") != "run_shell":
                return self._json(h, 400, {"error": "always_only_for_shell_confirm"})
            if scope not in ("session", "project"):
                return self._json(h, 400, {"error": "scope"})
            from .allowrules import check_always
            bad = [(p, check_always(p, broad_ok=True)) for p in pats if check_always(p, broad_ok=True)]
            if bad:   # 하나라도 못 받으면 아무것도 더하지 않고 답도 하지 않는다
                return self._json(h, 400, {"error": "pattern", "detail": f"{bad[0][0]}: {bad[0][1]}"})
            for p in pats:
                self.hub.add_allow(p, scope)
        try:
            self.hub.answer(name, seq, text, by="human", via="web")
        except RuntimeError as e:
            return self._json(h, 409, {"error": str(e)})
        return self._json(h, 200, {"ok": True})

    def upload(self, h, data: dict) -> None:
        """사람이 올린 파일 하나를 작업 폴더 inbox/ 에 둔다(덮지 않음, 비밀 꼴 이름 거부). 모델 문맥은 사람 말 끝 경로 한 줄뿐(화면이 붙인다)."""
        import base64
        import re as _re
        name = str(data.get("name") or "")
        name = _re.sub(r"[\x00-\x1f\x7f/\\:]", "_", name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]).strip().lstrip(".")[:120] or "file"
        try:
            raw = base64.b64decode(str(data.get("data") or ""), validate=True)
        except ValueError:
            return self._json(h, 400, {"error": "fields", "detail": "자료가 base64 가 아니다"})
        if len(raw) > UPLOAD_MAX:
            return self._json(h, 413, {"error": "too_large", "detail": f"파일 하나 {UPLOAD_MAX // (1024 * 1024)}MB 까지"})
        try:
            cv = self.main.conv(data.get("chat") if isinstance(data.get("chat"), str) else None)
        except KeyError:
            return self._json(h, 404, {"error": "no_chat"})
        env = cv.sess.env
        root = Path(env.workdir).resolve()
        d = root / "inbox"
        if env.is_secret(d / name):
            return self._json(h, 400, {"error": "secret", "detail": "비밀 꼴 이름(.env·키 등)은 올리지 않는다 — 이름을 바꿔 올린다"})
        d.mkdir(exist_ok=True)
        if root not in d.resolve().parents and d.resolve() != root / "inbox":
            return self._json(h, 400, {"error": "inbox"})
        stem, dot, ext = name.rpartition(".")
        f, n = d / name, 1
        while f.exists():
            n += 1
            f = d / (f"{stem}-{n}.{ext}" if dot and stem else f"{name}-{n}")
        f.write_bytes(raw)
        rel = str(f.relative_to(root))
        cv.ui._add("info", f"파일을 올렸다: {rel} ({len(raw):,} 바이트)")
        return self._json(h, 200, {"ok": True, "path": rel, "size": len(raw)})

    def main_send(self, h, data: dict) -> None:
        """메인에게 말: 글과(있으면) 이미지 [{name, data(base64)}]. 이미지는 vision 백엔드·메인이 쉴 때만."""
        import base64
        from .images import ImageError
        imgs = data.get("images") or []
        if not isinstance(imgs, list) or len(imgs) > IMAGES_MAX:
            return self._json(h, 400, {"error": "images", "detail": f"이미지는 한 번에 {IMAGES_MAX}장까지"})
        got = []
        for x in imgs:
            try:
                item = (str(x.get("name") or "")[:200], base64.b64decode(str(x.get("data") or ""), validate=True))
                if x.get("orig"):   # 화면이 줄여 보낸 사진의 원본(부분 확대용, 판 82) — 상한은 images.check 가 본다
                    item += (base64.b64decode(str(x["orig"]), validate=True),)
                got.append(item)
            except (ValueError, AttributeError):
                return self._json(h, 400, {"error": "images", "detail": "이미지 자료가 base64 가 아니다"})
        try:
            try:
                cv = self.main.conv(data.get("chat") if isinstance(data.get("chat"), str) else None)
            except KeyError:
                return self._json(h, 404, {"error": "no_chat", "detail": "닫혔거나 없는 대화다 — 화면을 새로 고친다"})
            r = cv.send(str(data.get("text") or ""), images=got or None)
        except ImageError as e:
            return self._json(h, 400, {"error": "images", "detail": str(e)})
        if r == "no_vision":
            return self._json(h, 400, {"error": "images", "detail": "이 백엔드는 이미지를 받지 않는다(backends 표 vision = false)"})
        if r == "busy_images":
            return self._json(h, 409, {"error": "images", "detail": "메인이 일하는 중에는 이미지를 붙이지 못한다. 끝나거나 멈춘 뒤 보낸다"})
        return self._json(h, 200, {"result": r})

    def _project(self) -> Path:
        return Path(self.hub.registry()["sessions"]["main"]["workdir"]).resolve()

    FILES_MAX = 30
    FILES_SCAN = 20000

    @staticmethod
    def _chat_q(url) -> str | None:
        return (parse_qs(url.query).get("chat") or [None])[0]

    def _conv_q(self, url):
        try:
            return self.main.conv(self._chat_q(url))
        except KeyError:
            return self.main

    FILE_MAX = 20 * 1024 * 1024
    FILE_INLINE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
                   ".pdf": "application/pdf"}
    FILE_TEXT = {".md", ".txt", ".csv", ".tsv", ".json", ".toml", ".yaml", ".yml", ".py", ".js", ".ts", ".jsx", ".tsx", ".sh",
                 ".log", ".ini", ".cfg", ".rs", ".go", ".java", ".c", ".h", ".cpp", ".css", ".sql", ".rst", ".tex"}

    def serve_file(self, h, url):
        from urllib.parse import quote
        from . import fswalk
        q = parse_qs(url.query)
        rel = (q.get("path") or [""])[0]
        conv = self._conv_q(url)
        env = conv.sess.env
        f = fswalk.path_ok(Path(env.workdir), rel, env.is_secret)
        if f is None:
            return self._json(h, 404, {"error": "not_found", "detail": "작업 폴더 안의 내줄 수 있는 파일이 아니다"})
        size = f.stat().st_size
        if size > self.FILE_MAX:
            return self._json(h, 413, {"error": "too_large", "detail": f"{size} 바이트 — 상한 {self.FILE_MAX}"})
        ext = f.suffix.lower()
        dl = (q.get("dl") or ["0"])[0] == "1" or ext not in self.FILE_INLINE and ext not in self.FILE_TEXT
        ctype = "application/octet-stream" if dl else self.FILE_INLINE.get(ext, "text/plain; charset=utf-8")
        disp = ("attachment" if dl else "inline") + f"; filename*=UTF-8''{quote(f.name)}"
        return self._send(h, 200, f.read_bytes(), ctype,
                          headers={"Content-Disposition": disp, "Cache-Control": "no-store",
                                   **({} if ctype == "application/pdf" else {"Content-Security-Policy": "sandbox"})})   # PDF 보기는 sandbox 면 막힌다

    def file_names(self, q: str, conv=None) -> list[str]:
        """@파일 고르기 후보: 작업 폴더 안 파일의 상대 경로 가운데 q 가 든 것(대소문자 무시). 비밀 파일 이름·.gitignore·
        .git 등은 빼고(읽기 판정과 같은 is_secret), 작업 폴더 밖(링크로 밖)은 뺀다. 내용은 읽지 않는다."""
        from . import fswalk
        env = (conv or self.main).sess.env
        root = Path(env.workdir).resolve()
        ql = q.strip().lower()
        out, n = [], 0
        for f in fswalk.walk(root, skip=lambda p, is_dir: (not is_dir) and env.is_secret(p)):
            n += 1
            if n > self.FILES_SCAN:
                break
            try:
                rp = f.resolve()
                if root not in rp.parents:
                    continue
                rel = str(f.relative_to(root))
            except (OSError, ValueError):
                continue
            if not ql or ql in rel.lower():
                out.append(rel)
                if len(out) >= self.FILES_MAX:
                    break
        return out

    def past_sessions(self, archived: bool = False) -> dict:
        """지난 세션(U2-3 2c, U2-8): 쉬는·끝낸 작업자(다시 열기 = session control resume)와 이 프로젝트의 지난 메인 대화,
        이름·보관 표시, 휴지통. 보관한 것은 archived=1 일 때만."""
        from .hub import seqs
        mg = self.main.manager()
        workers = []
        for w in mg.workers():
            if w["archived"] != archived:
                continue
            sd = self.hub.sdir(w["name"])
            first = ""
            n = (seqs(sd.inbox, "md") or [None])[0]
            if n is not None:
                try:
                    first = sd.instruction(n)[1].strip().splitlines()[0][:120]
                except (OSError, ValueError, IndexError):
                    first = ""
            workers.append({**w, "first": first})
        allm = mg.mains()
        mains = [m for m in allm if m["archived"] == archived]
        n_arch = sum(1 for w in mg.workers() if w["archived"]) + sum(1 for m in allm if m["archived"])
        return {"workers": workers, "mains": mains, "archived_count": n_arch, "trash": mg.trash()}

    def manage_sessions(self, h, data: dict) -> None:
        """지난 세션 정리: op = delete(휴지통으로) · restore · empty · rename · archive · unarchive.
        items = [{kind: worker|main, key}] (delete·archive 는 여럿), older_days(delete: 그보다 오래된 것 모두)."""
        op = data.get("op")
        mg = self.main.manager()
        try:
            if op == "empty":
                od = data.get("older_days")
                n = mg.empty(float(od) if isinstance(od, (int, float)) else None)
                return self._json(h, 200, {"ok": True, "count": n})
            if op == "restore":
                tid = data.get("id")
                if not isinstance(tid, str):
                    return self._json(h, 400, {"error": "fields"})
                r = mg.restore(tid)
                self._notice(f"사람이 지난 세션을 휴지통에서 되돌렸다: {r['label']}")
                return self._json(h, 200, {"ok": True, **r})
            items = data.get("items") or []
            if op == "delete" and isinstance(data.get("older_days"), (int, float)):
                items = [{"kind": k, "key": v} for k, v in mg.older_than(float(data["older_days"]))]
            if not isinstance(items, list) or not all(isinstance(x, dict) and x.get("kind") in ("worker", "main")
                                                      and isinstance(x.get("key"), str) for x in items) or len(items) > 500:
                return self._json(h, 400, {"error": "fields"})
            if op == "rename":
                if len(items) != 1 or not isinstance(data.get("title"), str):
                    return self._json(h, 400, {"error": "fields"})
                mg.rename(items[0]["kind"], items[0]["key"], data["title"])
                return self._json(h, 200, {"ok": True})
            if op in ("archive", "unarchive"):
                for x in items:
                    mg.archive(x["kind"], x["key"], op == "archive")
                return self._json(h, 200, {"ok": True, "count": len(items)})
            if op == "delete":
                done, failed = [], []
                for x in items:
                    try:
                        done.append(mg.delete(x["kind"], x["key"]))
                    except (ValueError, RuntimeError, OSError) as e:
                        failed.append({"key": x["key"], "detail": str(e)})
                if done:
                    self._notice(f"사람이 지난 세션 {len(done)}개를 휴지통으로 옮겼다(대화 기록만, 작업 폴더는 그대로)")
                return self._json(h, 200, {"ok": not failed, "trashed": done, "failed": failed})
        except (ValueError, RuntimeError, OSError) as e:
            return self._json(h, 409, {"error": "manage", "detail": str(e)})
        return self._json(h, 400, {"error": "op"})

    def test_offers(self) -> list[dict]:
        from .allowrules import RuleSet
        from .testcmds import offers
        proj = self._project()
        return offers(proj, self.hub.allow_rules() + RuleSet(proj, compat=True).all())

    def test_allow(self, h, data: dict) -> None:
        pat, scope = data.get("pattern"), data.get("scope", "project")
        if scope not in ("session", "project"):
            return self._json(h, 400, {"error": "scope"})
        if not isinstance(pat, str) or pat not in {o["pattern"] for o in self.test_offers() if o["pattern"]}:
            return self._json(h, 400, {"error": "not_offered", "detail": "시험 명령 후보에 든 꼴만 여기서 늘 허용한다"})
        self.hub.add_allow(pat, scope)
        return self._json(h, 200, {"ok": True})

    # ── 사람이 화면에서 세션을 열고·말하고·멈춘다(메인에게는 알림) ──
    def _notice(self, text: str) -> None:
        self.hub.notices.append("[허브] " + text)

    def new_session(self, h, data: dict) -> None:
        """[+ 새 세션]: 프로젝트 안 폴더 또는 새 worktree, 모드는 허브 메인 모드 이하."""
        from .hub import MODE_RANK, NAME_RE
        name, task = data.get("name"), data.get("task")
        if not isinstance(name, str) or not NAME_RE.match(name) or not isinstance(task, str) or not task.strip():
            return self._json(h, 400, {"error": "fields", "detail": "이름(영숫자·-)과 첫 말이 있어야 한다"})
        tools = self.main.tools
        mode = data.get("mode") or tools.main_mode
        if mode not in MODE_RANK or MODE_RANK[mode] > MODE_RANK.get(tools.main_mode, -1):
            return self._json(h, 400, {"error": "mode", "detail": f"모드는 메인({tools.main_mode})보다 넓을 수 없다"})
        project = Path(self.hub.registry()["sessions"]["main"]["workdir"]).resolve()
        from .memfile import git_root
        roots = [project] + ([git_root(project)] if git_root(project) else [])

        def inside(p) -> bool:
            rp = Path(os.path.expanduser(str(p)))
            rp = (rp if rp.is_absolute() else project / rp).resolve()
            return any(rp == r or r in rp.parents for r in roots)
        worktree, workdir = data.get("worktree"), data.get("workdir") or None
        if worktree:
            if not isinstance(worktree, dict) or not inside(worktree.get("repo") or ""):
                return self._json(h, 400, {"error": "worktree", "detail": "worktree 저장소는 프로젝트(또는 그 git 뿌리) 안이어야 한다"})
            workdir = None
        else:
            workdir = workdir or str(project)
            if not inside(workdir):
                return self._json(h, 400, {"error": "workdir", "detail": "작업 폴더는 프로젝트 안이어야 한다"})
            wd = Path(os.path.expanduser(str(workdir)))
            workdir = str((wd if wd.is_absolute() else project / wd).resolve())
        profile = data.get("profile") or tools.default_profile
        try:
            e = self.hub.spawn(name, task, workdir, profile, tools.default_backend, mode=mode, worktree=worktree,
                               sender="human")
        except (ValueError, OSError) as ex:
            return self._json(h, 400, {"error": "spawn", "detail": str(ex)})
        self._notice(f"사람이 화면에서 세션 {name} 을 열었다(작업 폴더 {e['workdir']}, 모드 {mode}, 첫 지시 0001).")
        return self._json(h, 200, {"ok": True, "session": name})

    def session_send(self, h, data: dict) -> None:
        """그 세션에 사람이 직접 말한다. 메인이면 메인 칸과 같고, 일꾼이면 inbox 에 from=human(긴급이면 끼워 넣기)."""
        name, text, kind = data.get("name"), data.get("text"), data.get("kind") or "지시"
        if not isinstance(name, str) or not isinstance(text, str) or not text.strip() or kind not in ("지시", "긴급"):
            return self._json(h, 400, {"error": "fields"})
        reg = self.hub.registry()["sessions"]
        if name not in reg:
            return self._json(h, 404, {"error": "no_session"})
        if reg[name].get("role") in ("main", "chat"):
            return self._json(h, 200, {"result": self.main.conv(name).send(text)})
        if reg[name]["status"] in ("stopped", "failed"):
            return self._json(h, 409, {"error": "not_running", "detail": f"{name} 은 {reg[name]['status']}"})
        n = self.hub.send(name, text, kind, sender="human")
        self._notice(f"사람이 {name} 에 직접 {kind} {n:04d} 를 보냈다. 보고는 session_read_report(name={name!r}, seq={n}).")
        return self._json(h, 200, {"ok": True, "seq": n})

    def session_control(self, h, data: dict) -> None:
        """[멈춤] [이어 가기] [끝내기]. worktree 지우기는 없다(사람이 터미널에서)."""
        name, action = data.get("name"), data.get("action")
        reg = self.hub.registry()["sessions"]
        if not isinstance(name, str) or name not in reg or action not in ("pause", "resume", "stop"):
            return self._json(h, 400, {"error": "fields"})
        if reg[name].get("role") in ("main", "chat"):
            return self._json(h, 400, {"error": "main", "detail": "대화는 [새 대화]·[닫기]로 다룬다(작업자만 멈춤·이어 가기)"})
        try:
            e = {"pause": self.hub.pause, "resume": self.hub.resume, "stop": self.hub.stop}[action](name)
        except (ValueError, RuntimeError, KeyError) as ex:
            return self._json(h, 409, {"error": "state", "detail": str(ex)})
        self._notice(f"사람이 화면에서 {name} 을 {'멈췄다' if action == 'pause' else '이어 갔다' if action == 'resume' else '끝냈다'}"
                     f"(상태 {e['status']}).")
        return self._json(h, 200, {"ok": True, "status": e["status"]})

    # ── 세션 대화 보기: 그 세션 기록(events.jsonl)에서 사람·지시·모델 답·도구 호출·결과를 다시 짠다 ──
    RESULT_MAX = 2000
    CONV_MAX = 300

    def _conv_items(self, ev: dict) -> list[dict]:
        from .chat import _arg_summary
        out = []

        def user(text: str, **extra):
            # 엔진이 붙인 환경 정보 묶음은 접어 따로 보인다(모델이 받은 글은 그대로 두되 화면이 시끄럽지 않게)
            if text.startswith("[엔진이 붙인 환경 정보") and "\n\n" in text:
                env, _, rest = text.partition("\n\n")
                out.append({"kind": "env", "text": env})
                text = rest
            out.append({"kind": "user", "text": text, **extra})

        def msgs(ms):
            for m in ms or []:
                role = m.get("role")
                if role == "user" and isinstance(m.get("content"), str):
                    user(m["content"])
                elif role == "user" and isinstance(m.get("content"), list):   # 이미지가 붙은 사람 말(자리표는 "[이미지]")
                    from .images import path_of, text_of
                    names = [x.name for x in (path_of(q) for q in m["content"] if isinstance(q, dict)) if x]
                    user(text_of(m["content"]), **({"imgs": names} if names else {}))
                elif role == "assistant":
                    if m.get("content"):
                        out.append({"kind": "assistant", "text": str(m["content"])})
                    for tc in m.get("tool_calls") or []:
                        fn = tc.get("function") or {}
                        try:
                            args = json.loads(fn.get("arguments") or "{}")
                        except ValueError:
                            args = {}
                        out.append({"kind": "tool", "text": f"{fn.get('name', '')} "
                                    + (_arg_summary(fn.get("name", ""), args) if isinstance(args, dict) else "")})
                elif role == "tool":
                    t = str(m.get("content") or "")
                    out.append({"kind": "result", "text": t[:self.RESULT_MAX]
                                + (f"\n…(결과 {len(t)}자 중 {self.RESULT_MAX}자)" if len(t) > self.RESULT_MAX else "")})
        k = ev.get("kind")
        if k == "run_start":
            msgs([m for m in ev.get("messages") or [] if m.get("role") != "system"])   # 이어 간 앞 대화
        elif k == "user":
            if ev.get("img_names"):   # 판 98: 그림 이름이 있으면 줄 아래 작은 그림으로
                user(str(ev.get("content") or ""), imgs=list(ev["img_names"]))
            else:
                user(str(ev.get("content") or "") + (f"\n[이미지 {ev['images']}장]" if ev.get("images") else ""))
        elif k == "interject":
            user(str(ev.get("content") or ""), interject=True)
        elif k == "step":
            msgs([m for m in ev.get("new_messages") or [] if m.get("role") != "user"])
            for tc in (ev.get("response") or {}).get("tool_calls") or []:   # 고친 파일의 diff(접힌 한 줄, U2-5)
                if tc.get("diff"):
                    out.append({"kind": "diff", "text": tc["diff"], "stat": tc.get("diff_stat")})
            if ev.get("error"):
                out.append({"kind": "warn", "text": f"오류: {ev['error']}"})
        elif k == "turn_end":
            if ev.get("files"):
                out.append({"kind": "files", "text": f"이번 차례에 바뀐 파일 {len(ev['files'])}개: " + ", ".join(ev["files"][:20])})
            out.append({"kind": "info", "text": f"[차례 끝: {ev.get('reason')}]"})
        return out

    def session_conversation(self, name: str, before: int | None = None, limit: int | None = None, prior: bool = False) -> dict:
        """고른 세션의 대화. 기본은 최근 CONV_MAX 줄. before·limit 로 앞쪽을 나눠 부른다(U2-5).
        prior=True 면 이어 받은 앞 대화(기록 첫 run_start 의 메시지)만 — 허브 메인 칸의 "앞 대화"."""
        reg = self.hub.registry()["sessions"]
        if name not in reg:
            return {"error": "no_session"}
        rd = self.hub.sdir(name).status().get("run_dir") or reg[name].get("run_dir")   # 이어 가면 새 기록 폴더(status 가 먼저 안다)
        f = Path(rd) / "events.jsonl" if rd else None
        if f is None or not f.is_file():
            return {"items": [], "total": 0}
        with self._conv_lock:
            r = self._read_conv(f)
            cache = self._conv[str(f)]
        pool = cache["items"][:cache.get("prior", 0)] if prior else cache["items"]
        lim = limit if limit and limit > 0 else self.CONV_MAX
        end = len(pool) if before is None or before < 0 or before > len(pool) else before
        start = max(0, end - lim)
        return {**r, "items": pool[start:end], "total": len(pool), "start": start}

    def _read_conv(self, f: Path) -> dict:
        cache = self._conv.setdefault(str(f), {"offset": 0, "items": []})
        size = f.stat().st_size
        if size < cache["offset"]:
            cache.update(offset=0, items=[])
            cache.pop("prior", None)
        if size > cache["offset"]:
            with f.open("rb") as fh:
                fh.seek(cache["offset"])
                chunk = fh.read(size - cache["offset"])
            last_nl = chunk.rfind(b"\n")
            if last_nl >= 0:   # 다 쓴 줄까지만(쓰는 중인 마지막 줄은 다음에)
                for line in chunk[:last_nl].splitlines():
                    try:
                        ev = json.loads(line)
                        cache["items"] += self._conv_items(ev)
                        if ev.get("kind") == "run_start" and "prior" not in cache:   # 이어 받은 앞 대화가 여기까지
                            cache["prior"] = len(cache["items"])
                    except ValueError:
                        continue
                cache["offset"] += last_nl + 1
        items = cache["items"]
        return {"items": items[-self.CONV_MAX:], "total": len(items)}

    def session_detail(self, name: str) -> dict:
        reg = self.hub.registry()["sessions"]
        if name not in reg:
            return {"error": "no_session"}
        e = reg[name]
        sd = self.hub.sdir(name)
        st = sd.status()
        live = st.get("live") if st.get("status") == "working" else None   # 작업자가 받는 중인 글(진행 표시)
        events = []
        rd = e.get("run_dir")
        if rd and (Path(rd) / "events.jsonl").is_file():
            for line in (Path(rd) / "events.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()[-60:]:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                events.append({"ts": ev.get("ts"), "kind": ev.get("kind"), "step": ev.get("step"),
                               "name": ev.get("name"), "action": ev.get("action")})
        diff = ""
        if Path(e["workdir"]).is_dir():
            try:
                p = subprocess.run(["git", "diff", "--stat"], cwd=e["workdir"], capture_output=True, encoding="utf-8", errors="replace",
                                   timeout=5, stdin=subprocess.DEVNULL)
                diff = p.stdout[-4000:] if p.returncode == 0 else ""
            except (OSError, subprocess.TimeoutExpired):
                diff = ""
        reports = []
        from .hub import seqs
        for n in seqs(sd.outbox, "md")[-10:]:
            head, body = sd.read_report(n)
            reports.append({"seq": n, "kind": head.get("report_kind"), "reason": head.get("reason"), "text": body[:4000]})
        todos = []
        if e.get("role") in ("main", "chat"):
            todos = list(getattr(self.main.conv(name).sess.env, "todos", []) or [])
        return {"session": e, "events": events, "diff_stat": diff, "reports": reports, "todos": todos, "live": live,
                "server_time": time.time()}

    def stream(self, h, chat: str | None = None) -> None:
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream")
        h.send_header("Cache-Control", "no-store")
        h.end_headers()
        last = None
        try:
            for _ in range(3600):
                try:
                    cv = self.main.conv(chat)
                except KeyError:   # 대화가 닫혔다: 표시 하나 보내고 닫는다(화면이 메인으로 돌아가 다시 붙는다)
                    h.wfile.write(b'data: {"chat_gone": true}\n\n')
                    h.wfile.flush()
                    return
                st = json.dumps(cv.state(), ensure_ascii=False)
                if st != last:
                    h.wfile.write(f"data: {st}\n\n".encode())
                    h.wfile.flush()
                    last = st
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def serve(self) -> threading.Thread:
        t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        t.start()
        return t

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
