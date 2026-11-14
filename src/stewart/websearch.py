"""SearXNG 클라이언트(속도 제한·429 재시도·엔진 집계)와 검색어 만들기.

서버 제한(분당 20, 순간 10)에 맞춰 요청 사이를 min_interval_s 이상 띄우고, 429를 받으면 기다렸다 다시 한다.
"""

from __future__ import annotations

import gzip
import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter


class SearxClient:
    def __init__(self, url: str, categories: str = "", timeout_s: float = 20, min_interval_s: float | None = None,
                 retry_wait_s: float = 30, max_retries: int = 3, cache_path=None):
        self.url = url.rstrip("/")
        self.categories = categories
        self.timeout_s = timeout_s
        if min_interval_s is None:   # 판 146: 따로 주지 않으면 환경 STEWART_SEARXNG_MIN_INTERVAL(초, 기본 3.2 — 서버 제한 분당 20)
            try:
                min_interval_s = max(0.0, float(os.environ.get("STEWART_SEARXNG_MIN_INTERVAL", "3.2")))
            except ValueError:
                min_interval_s = 3.2
        self.min_interval_s = min_interval_s
        self.retry_wait_s = retry_wait_s
        self.max_retries = max_retries
        self._last = 0.0
        self._clock, self._sleep = time.monotonic, time.sleep   # 시험이 가상 시계로 바꿔 끼운다(간격 판정을 결정적으로)
        self._lock = threading.Lock()    # 여러 스레드가 불러도 요청이 min_interval_s 간격으로 한 줄로 선다
        self.attempts: list[dict] = []   # 마지막 search() 호출의 요청 기록
        self.stats = {"requests": 0, "http_429": 0, "errors": 0, "results_by_engine": Counter(),
                      "unresponsive": Counter(), "wait_s": 0.0, "cache_hits": 0}
        # 검색 결과 캐시(JSONL, 같은 검색어·categories를 다시 보내지 않는다). 오류 결과는 넣지 않는다.
        self.cache_path = cache_path
        self.cache: dict[str, dict] = {}
        if cache_path:
            from pathlib import Path
            p = Path(cache_path)
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    e = json.loads(line)
                    self.cache[e["key"]] = e["value"]

    TRUNC_WAITS = (0.5, 1.0, 2.0)  # 끊김(받은 바이트 ≠ Content-Length) 재시도 간격 — 최대 3회

    def _get(self, params: dict) -> dict:
        with self._lock:
            return self._get_locked(params)

    def _get_locked(self, params: dict) -> dict:
        """gzip으로 받아 푼다. 요청마다 self.attempts에 시작·끝 시각(epoch ms)·encoding·전송 바이트·푼 바이트·
        Content-Length·결과(ok / IncompleteRead / retry_ok / http_N)를 남긴다."""
        url = self.url + "/search?" + urllib.parse.urlencode(params)
        trunc = 0
        n429 = 0
        while True:
            gap = self.min_interval_s - (self._clock() - self._last)
            if gap > 0:
                self._sleep(gap)
                self.stats["wait_s"] += gap
            self._last = self._clock()
            self.stats["requests"] += 1
            rec = {"try": len(self.attempts) + 1, "start_ms": int(time.time() * 1000), "bytes": 0,
                   "decoded_bytes": None, "encoding": None, "content_length": None}
            self.attempts.append(rec)
            req = urllib.request.Request(url, headers={"Accept": "application/json", "Accept-Encoding": "gzip"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    cl = r.headers.get("Content-Length")
                    rec["content_length"] = int(cl) if cl and cl.isdigit() else cl
                    rec["encoding"] = (r.headers.get("Content-Encoding") or "identity").lower()
                    rec["status"] = r.status
                    body = r.read()
                rec["bytes"] = len(body)
                if isinstance(rec["content_length"], int) and len(body) != rec["content_length"]:
                    raise http.client.IncompleteRead(body, rec["content_length"] - len(body))
                if rec["encoding"] == "gzip":
                    body = gzip.decompress(body)
                rec.update(decoded_bytes=len(body), end_ms=int(time.time() * 1000),
                           result="retry_ok" if trunc else "ok")
                return json.loads(body.decode("utf-8", "replace"))
            except (http.client.IncompleteRead, EOFError, gzip.BadGzipFile) as e:
                # 끊김: 받은 바이트가 Content-Length보다 적거나, gzip이 중간에 끝남 — 0.5·1·2초 뒤 최대 3회 다시
                if isinstance(e, http.client.IncompleteRead):
                    rec.update(bytes=len(e.partial or b""), expected_more=e.expected)
                rec.update(end_ms=int(time.time() * 1000), result="IncompleteRead")
                self.stats["incomplete_read"] = self.stats.get("incomplete_read", 0) + 1
                if trunc < len(self.TRUNC_WAITS):
                    time.sleep(self.TRUNC_WAITS[trunc])
                    trunc += 1
                    continue
                raise http.client.IncompleteRead(b"", None) if not isinstance(e, http.client.IncompleteRead) else e
            except urllib.error.HTTPError as e:
                rec.update(end_ms=int(time.time() * 1000), status=e.code, result=f"http_{e.code}")
                if e.code == 429 and n429 < self.max_retries:
                    n429 += 1
                    self.stats["http_429"] += 1
                    time.sleep(self.retry_wait_s)
                    self.stats["wait_s"] += self.retry_wait_s
                    continue
                self.stats["errors"] += 1
                raise
            except Exception as e:
                rec.update(end_ms=int(time.time() * 1000), result=type(e).__name__)
                raise

    def search(self, query: str, max_results: int = 8, categories: str | None = None) -> dict:
        """{query, results:[{title,url,content,engines}], elapsed_s, error}. categories 를 주면 이 한 번만 그 범주로."""
        cats = self.categories if categories in (None, "") else categories
        params = {"q": query, "format": "json"}
        if cats:
            params["categories"] = cats
        key = json.dumps([query, cats, max_results], ensure_ascii=False)
        if key in self.cache:
            self.stats["cache_hits"] += 1
            return {**self.cache[key], "cached": True, "attempts": []}
        t0 = time.monotonic()
        self.attempts = []
        out = {"query": query, "results": [], "error": None, "unresponsive": []}
        try:
            data = self._get(params)
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError, OSError,
                http.client.HTTPException) as e:  # IncompleteRead(응답이 중간에 끊김) 포함
            out["error"] = str(e)
            self.stats["errors"] += 0 if isinstance(e, urllib.error.HTTPError) else 1
        else:
            for row in data.get("unresponsive_engines") or []:   # SearXNG JSON: [[엔진 이름, 까닭], …]
                eng, why = (list(row) + ["", ""])[:2] if isinstance(row, (list, tuple)) else (str(row), "")
                self.stats["unresponsive"][f"{eng}: {why}"] += 1
                out["unresponsive"].append(f"{eng}({why})" if why else str(eng))
            for x in (data.get("results") or [])[:max_results]:
                engines = x.get("engines") or [x.get("engine", "?")]
                for e in engines:
                    self.stats["results_by_engine"][e] += 1
                out["results"].append({"title": (x.get("title") or "").strip(), "url": x.get("url", ""),
                                       "content": re.sub(r"\s+", " ", x.get("content") or "").strip()[:300],
                                       "engines": engines})
        out["elapsed_s"] = round(time.monotonic() - t0, 2)
        out["attempts"] = self.attempts
        out["brave"] = sum(1 for x in out["results"] if any("brave" in str(e).lower() for e in x.get("engines") or []))
        # Brave 결과는 디스크 캐시에 쓰지 않는다(약관: 저장·캐시 금지). 메모리 캐시(이 실행 안)는 그대로
        if self.cache_path and not out["error"] and not out["brave"]:
            self.cache[key] = out
            with open(self.cache_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"key": key, "value": out}, ensure_ascii=False) + "\n")
        return out

    def quota(self, url: str | None = None) -> dict | None:
        """SearXNG 쪽이 /quota.json 을 내면 brave 이달 셈을 읽는다(선택 기능 — 없거나 못 읽으면 None, 표시를 뺀다). 검색 간격·셈에는 넣지 않는다."""
        u = url or (self.url + "/quota.json")
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={"Accept": "application/json"}), timeout=3) as r:
                d = json.loads(r.read(65536).decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError, http.client.HTTPException):
            return None
        return parse_quota(d)

    def summary(self) -> dict:
        s = dict(self.stats)
        s["results_by_engine"] = dict(s["results_by_engine"])
        s["unresponsive"] = dict(s["unresponsive"])
        s["wait_s"] = round(s["wait_s"], 1)
        return s


_TECH = re.compile(r"(?i)\b(\d{3,4}x\d{3,4}|\d{3,4}p|x\.?26[45]|h\.?26[45]|hevc|avc|aac\w*|flac|ac3|dts|bd(rip)?|blu-?ray|"
                   r"web-?dl|webrip|hdtv|hi10p?|10bit|chap|raws?|end|ver|v\d|cd-?rip|\d+k|kbps|mp3|320)\b")


BRAVE_ON = {"켜짐", "on", "enabled", "active", "true", "ok"}


def parse_quota(d) -> dict | None:
    """/quota.json 을 {limit, used, remaining, enabled, month} 로. 칸 이름이 조금 달라도 읽는다(used·count, limit·cap, remaining,
    enabled·on·active). brave 칸이 따로 있으면(brave·braveapi) 그것을 본다. 모르는 꼴이면 None."""
    if not isinstance(d, dict):
        return None
    top = d
    for k in ("braveapi", "brave"):
        if isinstance(d.get(k), dict):
            d = d[k]
            break
    def num(*ks):
        for k in ks:
            v = d.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return int(v)
        return None
    limit, used, rem = num("limit", "cap", "monthly_limit"), num("used", "count", "this_month"), num("remaining", "left")
    if rem is None and limit is not None and used is not None:
        rem = max(0, limit - used)
    if rem is None:
        return None
    en = next((d[k] for k in ("enabled", "on", "active") if isinstance(d.get(k), bool)), rem > 0)
    state = None
    if isinstance(top.get("brave"), str):            # F 의 꼴: "brave": "키 없음" | "켜짐" | "꺼짐" …
        state = top["brave"].strip()
        en = state.lower() in BRAVE_ON
    return {"limit": limit, "used": used, "remaining": rem, "enabled": bool(en) and rem > 0, "state": state,
            "month": top.get("month") if isinstance(top.get("month"), str) else (d.get("month") if isinstance(d.get("month"), str) else None)}


def _clean(name: str) -> str:
    stem = name.rsplit(".", 1)[0] if "." in name[-6:] else name
    s = re.sub(r"^\[[^\]]*(raws?|subs?|moozzi\d*|zero)[^\]]*\]", " ", stem, flags=re.I)  # 앞의 릴리스 그룹
    s = re.sub(r"[\[(（][^\])）]*(\d{3,4}x\d{3,4}|BD|BDRIP|HDTV|x26[45]|AAC|FLAC|1080p|720p)[^\])）]*[\])）]", " ", s, flags=re.I)
    s = re.sub(r"\b(19|20)?\d{6}\b|\b\d{4}-\d{2}-\d{2}\b", " ", s)          # 날짜
    s = re.sub(r"(?i)\b(ep|e|episode|session)\.?\s?\d{1,4}\b", " ", s)     # 회차
    s = _TECH.sub(" ", s)
    s = re.sub(r"[_.\[\]【】()（）]+", " ", s)
    s = re.sub(r"\s-\s\d{1,4}(\s|$)", " ", s)                               # " - 05"
    return re.sub(r"\s+", " ", s).strip(" -")


def _longest(s: str) -> str:
    chunks = [c.strip() for c in re.split(r"\s-\s|[「」『』～〜]", s) if c.strip()]
    return max(chunks, key=len) if chunks else s


def make_queries(name: str) -> list[str]:
    """파일 이름에서 검색어 후보: 정리한 전체 → 가장 긴 덩어리 → 정리한 전체 + wiki."""
    s = _clean(name)
    qs = [s] if s else []
    longest = _longest(s)
    if longest and longest != s and len(longest) >= 3:
        qs.append(longest)
    if s:
        qs.append(s + " wiki")
    return list(dict.fromkeys(qs))


def korean_query(name: str, genre: str) -> str:
    """애니·드라마로 보이면 "<제목> 애니|드라마"(naver가 한국어 제목을 내도록). 아니면 빈 문자열."""
    if genre not in ("애니", "드라마"):
        return ""
    title = _longest(_clean(name))
    title = re.sub(r"(?i)\s+(S\d{1,2}|SP\d*|OVA|OAD)\b.*$", "", title).strip() or title
    return f"{title} {genre}" if title else ""


def result_terms(results: list[dict], min_count: int = 2) -> str:
    """검색 결과 제목·요약문에서 두 결과 이상에 나온 낱말만 모아 후보 찾기 글로 쓴다(잡음 줄이기)."""
    from .folders import keys
    cnt: Counter = Counter()
    for r in results:
        cnt.update(keys(r["title"] + " " + r["content"]))
    terms = {k for k, c in cnt.items() if c >= min_count}
    # 한국어 제목은 결과 제목에 한 번만 나와도 쓴다(naver가 한국어 제목을 한 번만 내는 경우)
    for r in results:
        terms |= {k for k in keys(r["title"]) if re.search(r"[\uac00-\ud7a3]", k)}
    return " ".join(sorted(terms))
