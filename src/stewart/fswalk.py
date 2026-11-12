"""파일 훑기 공용 부품: .gitignore 규칙, 경로 패턴(**), 걸러진 파일 목록.

search·glob 도구가 같이 쓴다. rg 를 쓰지 못할 때도 rg 와 같은 결과가 나오게 맞춘 것이 목표다.
- .gitignore: 찾는 폴더의 조상(파일 시스템 루트까지)과 하위 폴더의 .gitignore 를 모두 읽는다
  (rg --no-require-git 과 같다. git 저장소가 아니어도 따른다). 마지막에 맞은 규칙이 이긴다, ! 는 되살림.
- .git/info/exclude·전역 gitignore·.ignore 는 읽지 않는다(기계마다 결과가 달라지지 않게, rg 쪽도 끈다).
- 숨은 파일은 포함한다. SKIP_DIRS 는 늘 건너뛴다.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache"}

# 비밀 파일(도구가 읽지도 쓰지도 않는다). 파일 이름에 맞추고 대소문자는 무시한다. 프로필로 덮어쓸 수 있다(ToolEnv.secret_globs).
SECRET_GLOBS = (".env", ".env.*", "*.pem", "*.key", "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*",
                ".netrc", "*credentials*", ".git-credentials", ".npmrc", ".pypirc", "*.p12", "*.pfx",
                "*.kdbx", "*.keychain-db")
# 위에 맞아도 비밀이 아닌 견본 파일(코딩 과제에서 자주 읽는다)
SECRET_ALLOW = (".env.example", ".env.sample", ".env.template", ".env.dist")


def _translate(pat: str) -> str:
    """gitignore·glob 패턴 → 정규식 본문. ** 는 폴더 여러 단, * ? [..] 는 한 단 안."""
    out, i, n = [], 0, len(pat)
    while i < n:
        c = pat[i]
        if c == "*":
            if pat.startswith("**", i):
                j = i + 2
                if j < n and pat[j] == "/":          # **/ → 0단 이상의 폴더
                    out.append("(?:.*/)?")
                    i = j + 1
                else:                                 # 끝의 ** 또는 /** → 무엇이든
                    out.append(".*")
                    i = j
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = pat.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
            else:
                body = pat[i + 1:j]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = j
        elif c == "\\" and i + 1 < n:
            i += 1
            out.append(re.escape(pat[i]))
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


@lru_cache(maxsize=512)
def glob_rx(pattern: str) -> re.Pattern:
    """경로 패턴. '/' 가 없으면 파일 이름에만 맞춘다(*.py). 있으면 상대 경로 전체에 맞춘다(src/**/*.py)."""
    p = pattern.strip().removeprefix("./")
    if "/" not in p.rstrip("/"):
        return re.compile(r"(?:.*/)?" + _translate(p) + r"\Z")
    return re.compile(_translate(p.lstrip("/")) + r"\Z")


def glob_match(rel: str, pattern: str) -> bool:
    return bool(glob_rx(pattern).match(rel))


@lru_cache(maxsize=512)
def path_rx(pattern: str) -> re.Pattern:
    """보통 glob 뜻: 패턴이 상대 경로 전체에 맞아야 한다. '*.py' 는 맨 위만, '**/*.py' 는 모든 깊이."""
    return re.compile(_translate(pattern.strip().removeprefix("./").lstrip("/")) + r"\Z")


@lru_cache(maxsize=64)
def _name_rx(globs: tuple) -> re.Pattern | None:
    if not globs:
        return None
    return re.compile("(?:" + "|".join(_translate(g) for g in globs) + r")\Z", re.I)


def is_secret(name: str, globs=SECRET_GLOBS, allow=SECRET_ALLOW, win: bool | None = None) -> bool:
    """파일 이름(경로의 마지막 조각)이 비밀 파일 목록에 맞는지.
    Windows(win): 대체 스트림(:…)과 끝 점·공백을 뗀 이름(Windows 가 실제로 여는 이름)으로 본다 — '.env '·'.env::$DATA' 는 .env."""
    from . import osdep
    if osdep.IS_WIN if win is None else win:
        name = osdep.win_norm_name(name)      # Windows 가 실제로 여는 이름 하나로 판정한다
    name = name.rsplit("/", 1)[-1]
    a = _name_rx(tuple(allow or ()))
    if a and a.match(name):
        return False
    rx = _name_rx(tuple(globs or ()))
    return bool(rx and rx.match(name))


class Rule:
    __slots__ = ("base", "rx", "neg", "dir_only")

    def __init__(self, base: Path, line: str):
        self.neg = line.startswith("!")
        if self.neg:
            line = line[1:]
        self.dir_only = line.endswith("/")
        line = line.rstrip("/")
        anchored = "/" in line
        line = line.lstrip("/")
        body = _translate(line)
        self.rx = re.compile((body if anchored else r"(?:.*/)?" + body) + r"\Z")
        self.base = base

    def match(self, path: Path, is_dir: bool) -> bool:
        if self.dir_only and not is_dir:
            return False
        try:
            rel = path.relative_to(self.base).as_posix()
        except ValueError:
            return False
        return bool(self.rx.match(rel))


def read_rules(d: Path) -> list[Rule]:
    f = d / ".gitignore"
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rules = []
    for line in text.splitlines():
        if line.startswith("\\#") or line.startswith("\\!"):
            line = line[1:]
        elif not line.strip() or line.startswith("#"):
            continue
        line = line.rstrip(" ")  # 끝 공백은 무시(\ 로 막은 경우는 드물어 다루지 않는다)
        if line in ("", "!", "/"):
            continue
        rules.append(Rule(d, line))
    return rules


def ignored(rules: list[Rule], path: Path, is_dir: bool) -> bool:
    hit = False
    for r in rules:
        if r.match(path, is_dir):
            hit = not r.neg
    return hit


def ancestor_rules(base: Path) -> list[Rule]:
    """base 의 조상 폴더(루트 → base 바로 위)의 .gitignore 규칙. base 자신의 것은 walk 가 읽는다."""
    rules: list[Rule] = []
    for d in reversed(base.resolve().parents):
        rules += read_rules(d)
    return rules


def walk(base: Path, gitignore: bool = True, skip=None):
    """base(폴더) 아래 파일을 경로 이름순으로 낸다.
    skip(path, is_dir) 가 True 면 건너뛴다(비밀 파일 보호 등 바깥 규칙)."""
    base = Path(base).resolve()          # 조상 규칙(resolve 된 경로)과 맞춰 비교하려고
    root_rules = ancestor_rules(base) if gitignore else []

    def rec(d: Path, rules: list[Rule]):
        if gitignore:
            rules = rules + read_rules(d)
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            p = Path(e.path)
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir and e.name in SKIP_DIRS:
                continue
            if gitignore and ignored(rules, p, is_dir):
                continue
            if skip and skip(p, is_dir):
                continue
            if is_dir:
                yield from rec(p, rules)
            elif e.is_file():
                yield p

    yield from rec(base, root_rules)


def path_ok(root: Path, rel: str, is_secret=None) -> Path | None:
    """작업 폴더 root 안의 보통 파일 rel 을 사람에게 내줘도 되는가(판 104 — @파일 고르기와 같은 경계):
    절대 경로·..·밖으로 가는 링크·.git 등 건너뛰는 폴더·.gitignore·비밀 꼴 이름이면 None. 되면 resolve 된 경로."""
    if not rel or rel.startswith(("/", "\\")) or "\x00" in rel:
        return None
    root = Path(root).resolve()
    p = (root / rel)
    try:
        rp = p.resolve()
    except OSError:
        return None
    if root not in rp.parents or not rp.is_file():
        return None
    parts = rp.relative_to(root).parts
    if any(x == ".." for x in Path(rel).parts) or any(x in SKIP_DIRS for x in parts[:-1]):
        return None
    if is_secret is not None and any(is_secret(root.joinpath(*parts[:i + 1])) for i in range(len(parts))):
        return None
    rules = ancestor_rules(root)
    d = root
    for i, name in enumerate(parts):
        rules = rules + read_rules(d)
        d = d / name
        if ignored(rules, d, i < len(parts) - 1):
            return None
    return rp
