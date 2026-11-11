"""폰 알림(ntfy, U2-2): 승인 대기가 새로 생기면 ntfy 로 한 번 보낸다. Hub 의 notify 자리에 끼운다(hub.py 는 그대로).

설정(~/.config/stewart/hub.toml 의 [hub]):
    ntfy_url        = "https://<ntfy 서버>/<주제>"     # 주제까지 적은 주소(POST 받을 곳)
    ntfy_token_file = "~/.config/stewart/ntfy-token"  # 한 줄 토큰(끝 줄바꿈 허용). 없으면 토큰 없이 보낸다
    hub_url         = "http://127.0.0.1:8765/"        # 알림을 누르면 열 주소(Click). 없으면 Click 없음
    ntfy_priority   = 3                               # ntfy 우선순위 1~5(기본 3 = 보통, 약한 진동). 전에는 4(high)라 진동이 셌다

- 언제: Hub.tick 이 새 승인 대기마다 한 번 부른다(같은 대기에 한 번, 승인되면 다시 울리지 않는다 — notified_asks).
- 무엇: 제목 "stewart <프로젝트> · <세션>", 본문 "<종류> 승인 대기" + 명령 앞 한 줄(nostore.scrub 를 거친 것), 우선순위 ntfy_priority(기본 3), 태그 bell.
  ntfy JSON 발행(서버 뿌리에 POST, topic 칸)이라 한글 제목이 머리글 인코딩에 막히지 않는다.
- 토큰: 보낼 때마다 파일에서 읽어 Authorization 머리에만 싣는다. 인자·환경 변수·기록·화면·오류 글에 내지 않는다.
  파일이 없거나 못 읽으면 토큰 없이 보내고(서버가 401 이면 그 상태 코드만 남는다), 허브는 계속 돈다.
- 실패: NotifyError("ntfy HTTP <코드>") 만 올린다(Hub 는 notify_fail 에 그 종류만 적는다).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from .nostore import scrub

TIMEOUT_S = 5
CMD_CHARS = 200


class NotifyError(RuntimeError):
    pass


def _token(path: str | None) -> str | None:
    if not path:
        return None
    try:
        t = Path(os.path.expanduser(path)).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return t or None


def split_topic(url: str) -> tuple[str, str]:
    """"https://h/주제" → ("https://h/", "주제"). 주제가 없으면 ValueError."""
    u = urlparse(url)
    topic = u.path.strip("/")
    if u.scheme not in ("http", "https") or not u.netloc or not topic or "/" in topic:
        raise ValueError("ntfy_url 은 http(s)://서버/주제 꼴")
    return f"{u.scheme}://{u.netloc}/", topic


class Ntfy:
    def __init__(self, url: str, token_file: str | None = None, click: str | None = None, project: str = "",
                 hub=None, opener=None, priority: int = 3, tag: str = ""):
        self.base, self.topic = split_topic(url)
        self.tag = tag                      # 알림 제목 앞 꼬리표(예: "[EXAONE]") — 허브가 여럿일 때 가름
        self.token_file, self.click, self.project = token_file, click, project
        self.priority = priority
        self.hub = hub                      # 세션·명령 앞 줄을 찾으려고(허브가 만든 뒤 붙인다)
        self._open = opener or urllib.request.urlopen
        self.on_error = None                # 실패 글(상태 코드만)을 남길 곳 — 허브가 사건 기록으로 붙인다
        self._sent: set[tuple[str, int]] = set()

    def _find(self, session: str) -> dict | None:
        """방금 알림 차례가 된 대기(그 세션의 아직 안 보낸 가장 앞 것)."""
        if self.hub is None:
            return None
        for q in self.hub.queue():
            if q["session"] == session and (session, q["seq"]) not in self._sent:
                return q
        return None

    def message(self, text: str) -> dict:
        session = text.split(":", 1)[0].strip()
        q = self._find(session)
        body = text
        if q is not None:
            self._sent.add((session, q["seq"]))
            cmd = ((q.get("args_summary") or {}).get("command") or "").strip().splitlines()
            paths = (q.get("args_summary") or {}).get("paths") or []
            if cmd:
                body += "\n$ " + scrub(cmd[0])[:CMD_CHARS]
            elif paths:
                body += "\n" + scrub(", ".join(paths))[:CMD_CHARS]
        msg = {"topic": self.topic, "title": (f"[{self.tag}] " if self.tag else "") + f"stewart {self.project} · {session}".strip(), "message": body,
               "priority": self.priority, "tags": ["bell"]}
        if self.click:
            msg["click"] = self.click
        return msg

    def __call__(self, text: str) -> None:
        data = json.dumps(self.message(text), ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        tok = _token(self.token_file)
        if tok:
            headers["Authorization"] = "Bearer " + tok
        req = urllib.request.Request(self.base, data=data, method="POST", headers=headers)
        try:
            with self._open(req, timeout=TIMEOUT_S) as r:
                r.read(1)
        except urllib.error.HTTPError as e:
            self._fail(f"ntfy HTTP {e.code}")
        except (OSError, ValueError):
            self._fail("ntfy 연결 실패")

    def _fail(self, why: str) -> None:
        if self.on_error is not None:
            try:
                self.on_error(why)
            except Exception:
                pass
        raise NotifyError(why) from None


def from_config(cfg: dict, project: str) -> Ntfy | None:
    url = cfg.get("ntfy_url")
    if not url:
        return None
    pr = cfg.get("ntfy_priority", 3)
    if isinstance(pr, bool) or not isinstance(pr, int) or not 1 <= pr <= 5:
        from .config import ConfigError
        raise ConfigError("[hub] ntfy_priority 는 1~5 정수(기본 3)")
    return Ntfy(str(url), cfg.get("ntfy_token_file"), cfg.get("hub_url"), project, priority=pr,
                tag=str(cfg.get("notify_tag") or cfg.get("label") or ""))
