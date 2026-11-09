"""web_fetch 본체: 주소 하나를 받아 본문 텍스트만 돌려준다.

바깥 글이 모델에 들어가는 주입 경로이고, stewart 가 도는 기기에서 나가는 요청이라 SSRF 경로이기도 하다.
- http·https 만. 리다이렉트는 직접 따라가며 5회까지, 단계마다 스킴·주소를 다시 검사한다.
- 사설 주소 차단: 이름을 한 번 풀어(getaddrinfo) 나온 주소가 하나라도 공인(is_global)이 아니면 거부한다.
  검사한 바로 그 IP 로 접속한다(검사 뒤 다시 풀지 않는다 — DNS rebinding 막기). https 는 SNI·인증서 검사를 원래 이름으로.
  IPv6(::1, fc00::/7, fe80::/10, ::), IPv4 대응 주소(::ffff:10.0.0.1)도 막는다. CGNAT 100.64/10(테일넷)도 공인이 아니라 막힌다.
- 받는 양 2 MB, 시간 제한, 글 형식만(html 은 본문 텍스트로, text/*·json·xml 은 그대로).
- 사설 차단을 끄는 길은 이 모듈 전역 _TEST_ALLOW_LOOPBACK(시험 전용, 루프백만) 하나뿐이다. 프로필·명령줄에서는 켤 수 없다.
- 환경의 HTTP(S)_PROXY 는 쓰지 않는다(검사한 IP 로 직접 붙어야 하므로).
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import urllib.parse
from html.parser import HTMLParser

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 5
TEXT_TYPES = ("text/", "application/json", "application/xml", "application/xhtml+xml", "application/ld+json")
USER_AGENT = "stewart-web-fetch/0.1"
_TEST_ALLOW_LOOPBACK = False   # 시험 전용. 프로필·명령줄로 바꾸는 길을 두지 않는다


class FetchError(Exception):
    pass


def ip_blocked(ip: str) -> str | None:
    """막아야 하면 이유, 아니면 None."""
    try:
        a = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return f"주소를 해석할 수 없다: {ip}"
    for x in (a, getattr(a, "ipv4_mapped", None), getattr(a, "sixtofour", None)):
        if x is None:
            continue
        if _TEST_ALLOW_LOOPBACK and x.is_loopback:
            continue
        if x.is_multicast or not x.is_global:
            return f"사설·내부 주소라 막는다: {ip}"
    return None


def resolve(host: str, port: int) -> str:
    """이름을 한 번 풀어 검사하고, 접속할 IP 하나를 돌려준다."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise FetchError(f"이름을 풀 수 없다: {host} ({e})")
    ips = []
    for *_, sa in infos:
        if sa[0] not in ips:
            ips.append(sa[0])
    if not ips:
        raise FetchError(f"이름을 풀 수 없다: {host}")
    for ip in ips:                     # 하나라도 내부 주소면 거부(골라 붙는 꼼수를 막는다)
        why = ip_blocked(ip)
        if why:
            raise FetchError(why + f" (이름 {host})")
    return ips[0]


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, ip, **kw):
        super().__init__(host, **kw)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, ip, **kw):
        super().__init__(host, context=ssl.create_default_context(), **kw)
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _check_url(url: str) -> urllib.parse.SplitResult:
    u = urllib.parse.urlsplit(url.strip())
    if u.scheme not in ("http", "https"):
        raise FetchError(f"http·https 주소만 받는다: {url[:200]}")
    if not u.hostname:
        raise FetchError(f"주소에 호스트가 없다: {url[:200]}")
    if u.username or u.password:
        raise FetchError("주소에 사용자 이름·비밀번호를 넣지 않는다")
    return u


def get(url: str, timeout_s: float = 20) -> dict:
    """{url, final_url, status, content_type, bytes, truncated, body(bytes), charset, redirects}"""
    redirects = []
    orig = url
    for _ in range(MAX_REDIRECTS + 1):
        u = _check_url(url)
        port = u.port or (443 if u.scheme == "https" else 80)
        ip = resolve(u.hostname, port)
        cls = _PinnedHTTPS if u.scheme == "https" else _PinnedHTTP
        conn = cls(u.hostname, ip, port=port, timeout=timeout_s)
        path = urllib.parse.urlunsplit(("", "", u.path or "/", u.query, ""))
        try:
            conn.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept": "text/html, text/*;q=0.9, */*;q=0.1",
                                               "Accept-Encoding": "identity"})
            r = conn.getresponse()
            if r.status in (301, 302, 303, 307, 308):
                loc = r.getheader("Location")
                r.close()
                if not loc:
                    raise FetchError(f"리다이렉트({r.status})에 Location 이 없다")
                url = urllib.parse.urljoin(url, loc)
                redirects.append(url)
                continue
            body = r.read(MAX_BYTES + 1)
            ctype = (r.getheader("Content-Type") or "").lower()
            m = re.search(r"charset=([\w.-]+)", ctype)
            return {"url": orig, "final_url": url, "status": r.status,
                    "content_type": ctype.split(";")[0].strip(), "charset": m.group(1) if m else None,
                    "bytes": min(len(body), MAX_BYTES), "truncated": len(body) > MAX_BYTES,
                    "body": body[:MAX_BYTES], "redirects": redirects}
        except (OSError, http.client.HTTPException) as e:
            raise FetchError(f"받기 실패: {type(e).__name__}: {e}")
        finally:
            conn.close()
    raise FetchError(f"리다이렉트가 {MAX_REDIRECTS}번을 넘는다")


_SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "nav", "footer", "iframe", "form", "button", "select"}
_BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "header", "main", "aside",
               "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "dd", "dt", "hr", "figcaption"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.title: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self.skip += 1
        elif tag == "title":
            self.in_title = True
        elif tag in _BLOCK_TAGS:
            self.out.append("\n")
        if tag in ("h1", "h2", "h3") and not self.skip:
            self.out.append("#" * int(tag[1]) + " ")
        if tag == "li" and not self.skip:
            self.out.append("- ")

    def handle_startendtag(self, tag, attrs):
        if tag in ("br", "hr"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        elif tag == "title":
            self.in_title = False
        elif tag in _BLOCK_TAGS:
            self.out.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        elif not self.skip:
            self.out.append(data)


def html_text(html: str) -> tuple[str, str]:
    """(제목, 본문 텍스트). 줄마다 공백을 접고 빈 줄은 하나로."""
    p = _Text()
    try:
        p.feed(html)
        p.close()
    except Exception:  # 깨진 html 도 받은 데까지는 쓴다
        pass
    lines = [re.sub(r"[ \t\r\f\v\xa0]+", " ", l).strip() for l in "".join(p.out).split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return re.sub(r"\s+", " ", "".join(p.title)).strip(), text


def decode(res: dict) -> tuple[str, str]:
    """(제목, 글). 글 형식이 아니면 FetchError."""
    ct = res["content_type"]
    if ct and not ct.startswith(TEXT_TYPES):
        raise FetchError(f"글이 아닌 형식이라 읽지 않는다: {ct}")
    body = res["body"]
    cs = res["charset"]
    if not cs and (ct in ("text/html", "application/xhtml+xml") or not ct):
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w.-]+)", body[:4096], re.I)
        cs = m.group(1).decode("ascii", "replace") if m else None
    try:
        text = body.decode(cs or "utf-8", "replace")
    except LookupError:
        text = body.decode("utf-8", "replace")
    if ct in ("text/html", "application/xhtml+xml") or (not ct and "<html" in text[:2000].lower()):
        return html_text(text)
    return "", text
