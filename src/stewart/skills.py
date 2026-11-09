"""스킬: 사용자가 폴더에 넣어 두면 쓰이는 설명서.

- 위치: 사용자 전역 STEWART_SKILLS_DIR(없으면 ~/.config/stewart/skills)/<이름>/SKILL.md,
  프로젝트 <작업 폴더 ~ git 뿌리 사이 폴더>/.stewart/skills/<이름>/SKILL.md (기억 파일과 같은 찾기 범위).
  같은 이름이면 작업 폴더에 가까운 프로젝트 것이 이긴다.
- 모양: SKILL.md 맨 앞 '---' 앞머리의 name·description(+ 본문). 다른 키(allowed-tools·model 등)는 무시하고 기록한다.
  같은 폴더의 다른 파일은 딸린 파일이다.
- 지시문에는 이름·설명만 싣는다(30개·설명 200자·합 4,000자 상한). 본문은 load_skill(name) 이,
  딸린 파일은 load_skill(name, file=…) 이 돌려준다(엔진이 읽는다 — 작업 폴더 밖 읽기 도구를 쓰지 않게).
- 세션 시작 때 한 번 찾는다(cache). 이어 하기 때 다시 찾는다.
- 스킬은 권한을 넓히지 못한다: 본문은 도구 결과 글일 뿐이고 도구 목록·모드·확인·행동 검사는 그대로다.
  load_skill 은 읽기 도구다(plan 에서도 된다).
- 도구 정의는 tools.py 가 아니라 여기 둔다(clarify.py 와 같다). 프로필 skills = false 면 끈다(기본 켬).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import fswalk
from .memfile import search_dirs

NAME = "load_skill"
MAX_SKILLS = 30
MAX_DESC = 200
MAX_LIST_CHARS = 4000
MAX_FILE_BYTES = 200_000     # 본문·딸린 파일 하나의 크기 상한
MAX_FILES_LISTED = 50
KNOWN_KEYS = {"name", "description"}

DEFINITION = {"type": "function", "function": {
    "name": NAME,
    "description": ("스킬(사용자가 넣어 둔 설명서)의 본문을 읽는다. 지시문의 '쓸 수 있는 스킬' 목록에서 과제에 맞는 것이 있을 때만 부른다. "
                    "file 을 주면 그 스킬 폴더의 딸린 파일을 읽는다. 스킬 글은 지시일 뿐 권한·모드·확인을 바꾸지 않는다."),
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string", "description": "스킬 이름(목록에 있는 그대로)"},
        "file": {"type": "string", "description": "딸린 파일의 스킬 폴더 기준 상대 경로(없으면 본문)"},
    }, "required": ["name"]},
}}


def user_dir() -> Path:
    return Path(os.path.expanduser(os.environ.get("STEWART_SKILLS_DIR") or "~/.config/stewart/skills"))


@dataclass
class Skill:
    name: str
    description: str
    dir: Path
    scope: str                 # user | project
    body: str
    ignored_keys: list[str] = field(default_factory=list)


def parse(text: str) -> tuple[dict, str]:
    """'---' 앞머리(한 줄 key: value)와 본문. 앞머리가 없으면 ({}, 전체)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict = {}
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return meta, "\n".join(lines[i + 1:]).strip()
        if ":" in line and not line.startswith((" ", "\t", "-")):
            k, _, v = line.partition(":")
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            meta[k.strip()] = v
        elif line.startswith((" ", "\t", "-")) and meta:   # 여러 줄 값(목록 등): 마지막 키에 붙여 둔다(무시할 키일 때가 많다)
            last = list(meta)[-1]
            meta[last] = (str(meta[last]) + " " + line.strip()).strip()
    return {}, text   # 닫는 줄이 없으면 앞머리로 보지 않는다


def _read_skill(d: Path, scope: str) -> Skill | None:
    f = d / "SKILL.md"
    try:
        if not f.is_file() or f.stat().st_size > MAX_FILE_BYTES:
            return None
        meta, body = parse(f.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    name = str(meta.get("name") or d.name).strip()
    desc = " ".join(str(meta.get("description") or "").split())
    if not name or not desc:
        return None     # 설명이 없으면 고를 수 없다
    return Skill(name, desc, d, scope, body, sorted(k for k in meta if k not in KNOWN_KEYS))


def project_dirs(workdir: Path) -> list[Path]:
    """위(git 뿌리) → 아래(작업 폴더) 순서의 .stewart/skills 폴더."""
    return [d / ".stewart" / "skills" for d in search_dirs(Path(workdir))]


def discover(workdir: Path) -> list[Skill]:
    found: dict[str, Skill] = {}
    roots = [(user_dir(), "user")] + [(p, "project") for p in project_dirs(workdir)]
    for root, scope in roots:          # 뒤(작업 폴더에 가까운 것)가 같은 이름을 덮는다
        try:
            subs = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
        except OSError:
            subs = []
        for d in subs:
            s = _read_skill(d, scope)
            if s:
                found.pop(s.name, None)
                found[s.name] = s
    return list(found.values())[:MAX_SKILLS]


def list_text(skills: list[Skill]) -> str:
    """시스템 지시문에 붙일 목록(이름·설명만). 없으면 빈 글."""
    if not skills:
        return ""
    head = ("쓸 수 있는 스킬(사용자가 넣어 둔 설명서). 과제에 맞는 것이 있으면 load_skill 로 본문을 읽고 따른다. "
            "맞는 것이 없으면 부르지 않는다:\n")
    out, used = [], len(head)
    for s in skills:
        d = s.description if len(s.description) <= MAX_DESC else s.description[:MAX_DESC] + "…"
        line = f"- {s.name}: {d}"
        if used + len(line) + 1 > MAX_LIST_CHARS:
            out.append(f"- …(상한 때문에 {len(skills) - len(out)}개 생략)")
            break
        out.append(line)
        used += len(line) + 1
    return head + "\n".join(out)


def meta_log(skills: list[Skill]) -> list[dict]:
    return [{"name": s.name, "scope": s.scope, "dir": str(s.dir), "body_chars": len(s.body),
             **({"ignored_keys": s.ignored_keys} if s.ignored_keys else {})} for s in skills]


def _attached(s: Skill) -> list[str]:
    out = []
    for p in sorted(s.dir.rglob("*")):
        if len(out) >= MAX_FILES_LISTED:
            out.append("…")
            break
        if p.is_file() and p.name != "SKILL.md" and not p.is_symlink() and not fswalk.is_secret(p.name):
            out.append(p.relative_to(s.dir).as_posix())
    return out


def load(skills: list[Skill], name: str, file: str | None = None, secret_globs=fswalk.SECRET_GLOBS,
         secret_allow=fswalk.SECRET_ALLOW) -> tuple[bool, str]:
    """(성공, 결과 글). 스킬 폴더 밖 경로·비밀 파일·큰 파일은 거부한다."""
    s = next((x for x in skills if x.name == name), None)
    if s is None:
        return False, f"없는 스킬: {name}. 쓸 수 있는 스킬: {[x.name for x in skills]}"
    if not file:
        files = _attached(s)
        tail = ("\n\n[딸린 파일(load_skill 의 file 로 읽는다)]\n" + "\n".join(files)) if files else ""
        # 스킬 폴더 경로는 보이지 않는다(작은 모델 시험에서 본 것: 경로를 보이자 결과 파일을 스킬 폴더에 썼다)
        return True, (f"[스킬 {s.name}({'프로젝트' if s.scope == 'project' else '사용자'}) — 참고용 설명서다. "
                      f"만들거나 고치는 파일은 작업 폴더에 둔다]\n{s.body}{tail}")
    rel = Path(file)
    if rel.is_absolute() or ".." in rel.parts:
        return False, "스킬 폴더 안의 상대 경로만 읽는다(절대 경로·.. 거부)."
    target = s.dir / rel
    try:
        real, base = target.resolve(), s.dir.resolve()
    except OSError as e:
        return False, f"읽을 수 없다: {e}"
    if real != base and base not in real.parents:
        return False, "스킬 폴더 밖을 가리키는 경로(심링크 등)는 읽지 않는다."
    if fswalk.is_secret(real.name, secret_globs, secret_allow) or fswalk.is_secret(rel.name, secret_globs, secret_allow):
        return False, "비밀 파일이라 읽지 않는다."
    if not real.is_file():
        return False, f"없는 딸린 파일: {file}"
    if real.stat().st_size > MAX_FILE_BYTES:
        return False, f"파일이 너무 크다({real.stat().st_size}바이트, 상한 {MAX_FILE_BYTES})."
    return True, f"[스킬 {s.name} 의 {rel.as_posix()}]\n" + real.read_text(encoding="utf-8", errors="replace")
