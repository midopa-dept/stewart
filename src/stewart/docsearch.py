"""doc_search(판 64, SW-15 단계 1): 작업 폴더의 문서·코드와 한 번 읽은 웹 글을 낱말 순위(BM25)로 찾는다 — 파이썬만, 의존성 없음.

- 쪽: 파일을 CHUNK_LINES 줄씩(앞뒤 OVERLAP 줄 겹침) 나눈다. 순위가 높은 쪽의 경로:줄 범위와 맞은 낱말이 든 줄 몇 개를 돌려준다.
- 낱말: 영문·숫자 낱말(소문자), 한글은 낱말 그대로 + 두 글자씩(조사가 붙어도 걸리게).
- 색인: 세션(ToolEnv) 안에 한 번 만들고, 파일 크기·mtime 이 바뀐 파일만 다시 읽는다. 비밀 파일·.gitignore·바이너리·큰 파일은 뺀다.
- 웹 글 캐시: web_fetch 가 받은 본문을 기억 폴더 web-cache/ 에 남기면(실사용만) 그것도 "web:<주소>" 로 찾는다(30일·200개 상한).
- 결과는 찾은 자리일 뿐이다 — 주장하기 전에 read_file 로 그 줄을 읽게 한다.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import Counter
from pathlib import Path

CHUNK_LINES, OVERLAP = 40, 5
MAX_FILE_BYTES = 1_000_000
MAX_FILES = 5000
TOP = 8
K1, B = 1.5, 0.75
TEXT_EXT = {".md", ".markdown", ".txt", ".rst", ".adoc", ".org", ".tex", ".csv", ".tsv",
            ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".go", ".rs", ".java", ".kt", ".c", ".h", ".cc", ".cpp", ".hpp",
            ".cs", ".rb", ".php", ".swift", ".sh", ".zsh", ".bash", ".ps1", ".cmd", ".bat", ".sql", ".lua", ".r",
            ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".conf", ".html", ".htm", ".css", ".xml", ".svelte", ".vue"}
WEB_KEEP_DAYS, WEB_KEEP_MAX, WEB_MAX_CHARS = 30, 200, 200_000

def memory_items(d):
    """자료 기억(판 83) 파일들 → (열쇠 "memory:<주제>", 크기, mtime, 읽기). 줄 끝 표 주석은 빼고 읽는다."""
    if not d:
        return
    d = Path(d)
    if not d.is_dir():
        return
    for f in sorted(d.glob("*.md")):
        try:
            st = f.stat()
        except OSError:
            continue

        def read(f=f):
            try:
                return re.sub(r"\s*<!--.*?-->", "", f.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                return ""
        yield ("memory:" + f.stem, st.st_size, st.st_mtime, read)


_WORD = re.compile(r"[A-Za-z0-9_]+|[가-힣]+")


def tokens(text: str) -> list[str]:
    out = []
    for w in _WORD.findall(text or ""):
        if w[0] >= "가":                       # 한글: 낱말 + 두 글자씩
            out.append(w)
            out += [w[i:i + 2] for i in range(len(w) - 1)] if len(w) > 2 else []
        else:
            w = w.lower()
            if len(w) > 1:
                out.append(w)
                if "_" in w:
                    out += [p for p in w.split("_") if len(p) > 1]
    return out


class Index:
    def __init__(self):
        self.files: dict[str, tuple] = {}      # 열쇠 → (크기, mtime)
        self.chunks: list[dict] = []           # {key, start, end, tf: Counter, n}
        self.df: Counter = Counter()

    def _drop(self, key: str) -> None:
        keep = []
        for c in self.chunks:
            if c["key"] == key:
                for t in c["tf"]:
                    self.df[t] -= 1
            else:
                keep.append(c)
        self.chunks = keep

    def _add(self, key: str, text: str) -> None:
        lines = text.splitlines()
        step = CHUNK_LINES - OVERLAP
        for s in range(0, max(1, len(lines)), step):
            seg = lines[s:s + CHUNK_LINES]
            if not seg:
                break
            tf = Counter(tokens("\n".join(seg)))
            if not tf:
                continue
            self.chunks.append({"key": key, "start": s + 1, "end": s + len(seg), "tf": tf, "n": sum(tf.values()), "lines": seg})
            for t in tf:
                self.df[t] += 1
            if s + CHUNK_LINES >= len(lines):
                break

    def update(self, items) -> int:
        """items: (열쇠, 크기, mtime, 글 읽는 함수). 바뀐 것만 다시 읽는다. 사라진 열쇠는 뺀다. 다시 읽은 수."""
        seen, n = set(), 0
        for key, size, mtime, read in items:
            seen.add(key)
            if self.files.get(key) == (size, mtime):
                continue
            self._drop(key)
            text = read()
            self.files[key] = (size, mtime)
            if text:
                self._add(key, text)
            n += 1
        for key in [k for k in self.files if k not in seen]:
            self._drop(key)
            del self.files[key]
        return n

    def search(self, query: str, top: int = TOP, prefix: str = "") -> list[dict]:
        q = list(dict.fromkeys(tokens(query)))
        cs = [c for c in self.chunks if c["key"].startswith(prefix)] if prefix else self.chunks
        if not q or not cs:
            return []
        N = len(self.chunks)
        avg = sum(c["n"] for c in self.chunks) / max(1, N)
        idf = {t: math.log(1 + (N - self.df.get(t, 0) + 0.5) / (self.df.get(t, 0) + 0.5)) for t in q}
        scored = []
        for c in cs:
            s = 0.0
            for t in q:
                f = c["tf"].get(t)
                if f:
                    s += idf[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * c["n"] / avg))
            if s > 0:
                scored.append((s, c))
        scored.sort(key=lambda x: -x[0])
        out, used = [], set()
        for s, c in scored:                      # 같은 파일의 겹친 쪽은 하나만
            k = (c["key"], c["start"] // CHUNK_LINES)
            if k in used:
                continue
            used.add(k)
            out.append({"key": c["key"], "start": c["start"], "end": c["end"], "score": round(s, 2),
                        "hits": _hit_lines(c, q)})
            if len(out) >= top:
                break
        return out


def _hit_lines(c: dict, q: list[str], n: int = 3) -> list[tuple[int, str]]:
    qs = set(q)
    rows = [(c["start"] + i, ln) for i, ln in enumerate(c["lines"]) if qs & set(tokens(ln))]
    rows.sort(key=lambda r: -len(qs & set(tokens(r[1]))))
    return sorted(rows[:n])


# ── 웹 글 캐시 ──
def _web_name(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24] + ".json"


def web_save(cache_dir: Path | None, url: str, title: str, text: str) -> None:
    if not cache_dir:
        return
    d = Path(cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    rec = {"url": url, "title": title, "at": time.time(), "text": (text or "")[:WEB_MAX_CHARS]}
    (d / _web_name(url)).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    web_prune(d)


def web_prune(d: Path) -> None:
    fs = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    old = time.time() - WEB_KEEP_DAYS * 86400
    for i, f in enumerate(fs):
        if i >= WEB_KEEP_MAX or f.stat().st_mtime < old:
            f.unlink(missing_ok=True)


def web_items(cache_dir: Path | None):
    if not cache_dir or not Path(cache_dir).is_dir():
        return
    for f in sorted(Path(cache_dir).glob("*.json")):
        st = f.stat()

        def read(f=f):
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
                return (r.get("title") or "") + "\n" + (r.get("text") or "")
            except (OSError, ValueError):
                return ""
        try:
            url = json.loads(f.read_text(encoding="utf-8")).get("url") or f.stem
        except (OSError, ValueError):
            continue
        yield "web:" + url, st.st_size, st.st_mtime, read
