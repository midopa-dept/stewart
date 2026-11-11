"""원격 저장소(forge) 설정(판 48): 새 저장소의 원격·첫 push 뒤 협력자·다른 곳에 올리지 않기.

설정(허브 설정 파일 hub.toml 의 [forge] 표 — 주소·계정은 코드에 두지 않는다):
    [forge]
    url = "https://<호스트>:<포트>"          # Forgejo/Gitea 뿌리(https)
    owner = "<저장소 주인 계정>"
    user = "<로그인 계정>"                    # 생략하면 owner. 원격 주소에 user@ 로 넣어 키체인이 그 계정을 고르게
    credential = "keychain"                   # git credential(키체인)에서 토큰을 꺼내 API 에만 쓴다. "none" = API 안 씀
    collaborators = ["<사람>:read"]           # 첫 push 뒤 넣을 협력자(read|write|admin)
    private = true                            # push 로 생긴 저장소를 비공개로 맞춘다

있으면 chat·허브(메인·작업자)에 도구 둘이 열리고 지시문에 한 단락이 붙는다:
- forge_remote(path, name): 작업 폴더 안 git 저장소에 origin = <url>/<owner>/<name>.git (이미 다른 origin 이면 바꾸지 않는다)
- forge_share(name): push 로 생긴 저장소를 비공개로 맞추고 협력자를 넣는다(PUT /api/v1/repos/<owner>/<name>/collaborators/<사람>)
- 셸 막이: git push·git remote add/set-url·gh repo create 가 forge 밖(GitHub 등)을 가리키면 돌리지 않는다.
토큰은 묻지도·찾지도·주소에 넣지도 않는다. git credential fill 로 그 순간만 꺼내 API 머리에만 쓰고, 결과 글·기록에 남기지 않는다.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

NAMES = ("forge_remote", "forge_share")
PERMS = ("read", "write", "admin")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


@dataclass
class Forge:
    url: str
    owner: str
    user: str
    credential: str = "keychain"
    collaborators: list = field(default_factory=list)   # [(사람, 권한)]
    private: bool = True

    @property
    def host(self) -> str:
        return urlsplit(self.url).netloc

    @property
    def base(self) -> str:   # 저장소 주소 앞부분(계정 없이) — 셸 막이가 비교한다
        u = urlsplit(self.url)
        return f"{u.scheme}://{u.hostname}{':' + str(u.port) if u.port else ''}/{self.owner}/"

    def remote(self, name: str) -> str:
        u = urlsplit(self.url)
        return f"{u.scheme}://{self.user}@{u.hostname}{':' + str(u.port) if u.port else ''}/{self.owner}/{name}.git"


class ForgeError(ValueError):
    pass


def config_path(path: str | None = None) -> Path:
    return Path(os.path.expanduser(path or os.environ.get("STEWART_HUB_CONFIG")
                                   or os.path.join(os.environ.get("XDG_CONFIG_HOME") or "~/.config", "stewart", "hub.toml")))


def load(path: str | None = None) -> Forge | None:
    """hub.toml 의 [forge]. 없으면 None. 틀린 값은 ForgeError(무엇이 틀렸는지)."""
    p = config_path(path)
    if not p.is_file():
        return None
    try:
        d = tomllib.loads(p.read_text(encoding="utf-8")).get("forge")
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ForgeError(f"{p}: 읽지 못함 — {e}") from None
    if not d:
        return None
    known = {"url", "owner", "user", "credential", "collaborators", "private"}
    bad = sorted(set(d) - known)
    if bad:
        raise ForgeError(f"{p}: [forge] 에 모르는 키 {bad} (쓸 수 있는 것: {sorted(known)})")
    url = str(d.get("url") or "").rstrip("/")
    u = urlsplit(url)
    if u.scheme not in ("https", "http") or not u.hostname or u.username or u.password or u.path not in ("", "/"):
        raise ForgeError(f"{p}: [forge] url 은 https://호스트[:포트] 꼴(계정·경로 없이) — 지금 {url!r}")
    owner = str(d.get("owner") or "")
    if not _NAME.match(owner):
        raise ForgeError(f"{p}: [forge] owner 가 비었거나 꼴이 틀렸다")
    user = str(d.get("user") or owner)
    if not _NAME.match(user):
        raise ForgeError(f"{p}: [forge] user 꼴이 틀렸다")
    cred = str(d.get("credential") or "keychain")
    if cred not in ("keychain", "git", "none"):
        raise ForgeError(f"{p}: [forge] credential 은 keychain|git|none")
    collab = []
    for x in d.get("collaborators") or []:
        who, _, perm = str(x).partition(":")
        perm = perm or "read"
        if not _NAME.match(who) or perm not in PERMS:
            raise ForgeError(f"{p}: [forge] collaborators 의 {x!r} — '사람:read|write|admin' 꼴")
        collab.append((who, perm))
    return Forge(url=url, owner=owner, user=user, credential=cred, collaborators=collab, private=bool(d.get("private", True)))


def note(fg: Forge) -> str:
    """지시문에 붙이는 단락(주소는 설정 값)."""
    who = ", ".join(f"{w}({p})" for w, p in fg.collaborators) or "없음"
    return (f"원격 저장소(설정의 forge): 새 git 저장소의 원격은 forge_remote 로 건다(origin = {fg.base}<이름>.git). "
            f"`git push -u origin <가지>` 로 올리면 저장소가 생기고, 그 뒤 forge_share 로 비공개 확인·협력자({who})를 넣는다. "
            "다른 곳(GitHub 등)에는 올리지 않는다(엔진이 막는다). 토큰을 묻거나 찾거나 주소·명령에 넣지 않는다 — 인증은 키체인이 한다.")


DEFINITIONS = [
    {"type": "function", "function": {
        "name": "forge_remote",
        "description": "작업 폴더 안 git 저장소에 설정의 forge 원격(origin)을 건다. 저장소를 미리 만들 필요 없다(push 하면 생긴다).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "git 저장소 폴더(작업 폴더 기준, 기본 '.')"},
            "name": {"type": "string", "description": "원격 저장소 이름(영숫자 . _ -)"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "forge_share",
        "description": "push 로 생긴 forge 저장소를 비공개로 맞추고 설정의 협력자를 넣는다(첫 push 뒤 한 번).",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "원격 저장소 이름"}}, "required": ["name"]}}},
]


def _git(args: list[str], cwd: Path, timeout: float = 20, stdin: str | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout, input=stdin, env=env)


def token(fg: Forge, cwd: Path) -> str | None:
    """git credential fill(키체인) — 그 순간만 꺼낸다. 없으면 None(사람이 한 번 push 해 키체인에 넣어야 한다)."""
    if fg.credential == "none":
        return None
    u = urlsplit(fg.url)
    p = _git(["credential", "fill"], cwd, stdin=f"protocol={u.scheme}\nhost={fg.host}\nusername={fg.user}\n\n")
    if p.returncode != 0:
        return None
    for line in p.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password="):] or None
    return None


def _api(fg: Forge, method: str, path: str, tok: str, body: dict | None = None, timeout: float = 15) -> int:
    req = urllib.request.Request(fg.url + "/api/v1" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "token " + tok, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(1)
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def _repo_dir(workdir: Path, path: str | None) -> Path:
    d = (Path(workdir) / (path or ".")).resolve()
    if d != Path(workdir).resolve() and Path(workdir).resolve() not in d.parents:
        raise ForgeError("작업 폴더 밖 저장소")
    return d


def forge_remote(fg: Forge, workdir: Path, name: str, path: str | None = None) -> tuple[bool, str]:
    if not _NAME.match(name or ""):
        return False, "이름은 영숫자로 시작하고 영숫자 . _ - 만(100자까지)"
    try:
        d = _repo_dir(workdir, path)
    except ForgeError as e:
        return False, str(e)
    top = _git(["rev-parse", "--show-toplevel"], d)
    if top.returncode != 0:
        return False, f"git 저장소가 아니다: {d} — 먼저 git init"
    want = fg.remote(name)
    cur = _git(["remote", "get-url", "origin"], d)
    if cur.returncode == 0:
        have = cur.stdout.strip()
        if have == want:
            return True, f"origin 이 이미 {want} 이다. 다음: git push -u origin <가지> → forge_share(name={name!r})"
        return False, (f"origin 이 이미 다른 곳이다({have}). 바꾸지 않았다 — 사람에게 물어라"
                       " (forge 밖 원격이면 그대로 두고, 바꿀지는 사람이 정한다)")
    p = _git(["remote", "add", "origin", want], d)
    if p.returncode != 0:
        return False, f"git remote add 실패: {p.stderr.strip()[:300]}"
    return True, (f"origin = {want}. 다음: `git push -u origin <가지>`(저장소는 push 하면 생긴다) → 그 뒤 forge_share(name={name!r}).")


def forge_share(fg: Forge, workdir: Path, name: str) -> tuple[bool, str]:
    if not _NAME.match(name or ""):
        return False, "이름 꼴이 틀렸다"
    if fg.credential == "none":
        return False, "설정이 credential = \"none\" 이라 API 를 쓰지 않는다(협력자는 사람이 넣는다)"
    tok = token(fg, Path(workdir))
    if not tok:
        return False, ("키체인에서 forge 인증을 찾지 못했다. 사람이 한 번 그 계정으로 push 해 키체인에 넣어야 한다"
                       " (토큰을 묻거나 찾지 마라)")
    try:
        lines = []
        st = _api(fg, "GET", f"/repos/{fg.owner}/{name}", tok)
        if st == 404:
            return False, f"{fg.owner}/{name} 이 아직 없다 — 먼저 git push -u origin <가지>"
        if st != 200:
            return False, f"저장소를 읽지 못했다(HTTP {st})"
        if fg.private:
            st = _api(fg, "PATCH", f"/repos/{fg.owner}/{name}", tok, {"private": True})
            lines.append(f"비공개로 맞춤: HTTP {st}")
        ok = True
        for who, perm in fg.collaborators:
            st = _api(fg, "PUT", f"/repos/{fg.owner}/{name}/collaborators/{who}", tok, {"permission": perm})
            ok = ok and st in (200, 201, 204)
            lines.append(f"협력자 {who}({perm}): HTTP {st}" + ("" if st in (200, 201, 204) else " — 실패"))
        return ok, f"{fg.base}{name}\n" + "\n".join(lines or ["(넣을 협력자 없음)"])
    finally:
        tok = None   # noqa: F841 — 토큰은 결과·기록에 남기지 않는다


def execute(fg: Forge, workdir: Path, name: str, args: dict) -> tuple[bool, str]:
    try:
        if name == "forge_remote":
            return forge_remote(fg, workdir, str(args.get("name") or ""), args.get("path"))
        if name == "forge_share":
            return forge_share(fg, workdir, str(args.get("name") or ""))
    except (subprocess.TimeoutExpired, OSError, ForgeError) as e:
        return False, f"{type(e).__name__}: {e}"
    return False, f"모르는 forge 도구: {name}"


# ── 셸 막이: forge 밖으로 올리기 ──
_URL = re.compile(r"(?:https?|ssh|git)://\S+|[\w.-]+@[\w.-]+:[\w./-]+")


def outside(fg: Forge, cmd: str, cwd: Path) -> str | None:
    """명령이 forge 밖으로 올리거나 원격을 바꾸려 하면 까닭(한 줄), 아니면 None."""
    pieces = re.split(r"\s*(?:&&|\|\||;|\n)\s*", cmd or "")
    here = Path(cwd)
    for piece in pieces:
        try:
            w = shlex.split(piece)
        except ValueError:
            w = piece.split()
        if not w:
            continue
        if w[0] == "cd" and len(w) > 1:
            here = (here / w[1]).resolve() if not w[1].startswith("/") else Path(w[1])
            continue
        if w[0] == "gh" and w[1:3] == ["repo", "create"]:
            return "gh repo create(GitHub) — 원격 저장소는 설정의 forge 만 쓴다"
        if w[0] != "git":
            continue
        args = [a for a in w[1:]]
        while args and args[0] in ("-C",) and len(args) > 1:
            here = (here / args[1]).resolve() if not args[1].startswith("/") else Path(args[1])
            args = args[2:]
        if not args:
            continue
        if args[0] == "remote" and len(args) >= 4 and args[1] in ("add", "set-url"):
            url = args[-1]
            if not _forge_url(fg, url):
                return f"forge 밖 원격({url}) — 원격은 forge_remote 로 건다"
        if args[0] == "push":
            rest = [a for a in args[1:] if not a.startswith("-")]
            target = rest[0] if rest else None
            if target and _URL.match(target):
                if not _forge_url(fg, target):
                    return f"forge 밖으로 push({target})"
                continue
            remote = target or "origin"
            p = _git(["remote", "get-url", remote], here, timeout=5)
            if p.returncode == 0 and not _forge_url(fg, p.stdout.strip()):
                return f"원격 {remote} = {p.stdout.strip()} 은 forge 밖이다"
    return None


def _forge_url(fg: Forge, url: str) -> bool:
    u = urlsplit(url)
    if not u.scheme:
        return False
    host = f"{u.hostname}{':' + str(u.port) if u.port else ''}"
    fu = urlsplit(fg.url)
    fhost = f"{fu.hostname}{':' + str(fu.port) if fu.port else ''}"
    return host == fhost and u.path.lstrip("/").startswith(fg.owner + "/")


BLOCK_NOTE = ("[엔진] 원격 저장소는 설정의 forge({base})만 쓴다 — 다른 곳(GitHub 등)에 올리지 않는다. 돌리지 않았다({why}). "
              "새 저장소는 forge_remote 로 origin 을 걸고 push 한다.")
