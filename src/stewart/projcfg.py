"""프로젝트 구성 파일: stewart 정본 + Claude Code 꼴 호환.

정본(어디서나 읽는다)
- 지시 파일: AGENTS.md(도구 사이 공통) · STEWART.md(stewart 고유, 뒤에 붙어 이긴다). 사용자 전역 <사용자 폴더>/STEWART.md
- .stewart/skills/<이름>/SKILL.md · .stewart/commands/<이름>.md · .stewart/agents/<이름>.md
- 사용자 폴더 = STEWART_USER_DIR(없으면 $XDG_CONFIG_HOME/stewart, 곧 ~/.config/stewart)의 skills·commands·agents
  (스킬 사용자 폴더는 지금처럼 STEWART_SKILLS_DIR 가 이긴다)
- 공통 꼴 .agents/skills 도 읽는다.

호환(Claude Code 로 만든 프로젝트를 그대로 쓰게)
- 프로젝트 CLAUDE.md(같은 폴더에 AGENTS.md 가 없을 때만, "@AGENTS.md" 한 줄이거나 AGENTS.md 와 같은 내용이면 건너뜀),
  .claude/skills · .claude/commands · .claude/agents
- 켜고 끄기: STEWART_CLAUDE_COMPAT(0|1) > 프로필 claude_compat > 기본(chat·허브 켬, run 끔)
- 사용자 전역 ~/.claude(CLAUDE.md·skills·commands·agents)는 호환을 켜도 기본으로 읽지 않는다.
  STEWART_CLAUDE_COMPAT_USER(0|1) > 프로필 claude_compat_user > 기본 끔. 폴더는 STEWART_CLAUDE_HOME(없으면 ~/.claude)
- 같은 이름이면 정본이 이긴다(호환 → 정본 차례로 읽어 뒤가 덮는다). 정본끼리는 작업 폴더에 가까운 것이 이긴다.

@경로 가져오기는 세 지시 파일 모두에서 편다. 권한은 넓히지 않는다: 읽은 글은 지시문·사람 입력·작업자 지시문이 될 뿐,
도구는 부모(바탕 프로필) 안에서만 줄고 경계·모드·확인·행동 검사는 그대로다. .claude/settings.json 은 읽지 않는다.
"""

from __future__ import annotations

import dataclasses
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import fswalk
from .memfile import search_dirs
from .skills import parse

ENV = "STEWART_CLAUDE_COMPAT"
USER_ENV = "STEWART_CLAUDE_COMPAT_USER"
HOME_ENV = "STEWART_CLAUDE_HOME"
USER_DIR_ENV = "STEWART_USER_DIR"
IMPORT_DEPTH = 5
MAX_FILE_BYTES = 200_000
MAX_COMMANDS = 100
MAX_AGENTS = 30
MAX_DESC = 200
MAX_AGENT_LIST = 3000

TOOL_MAP = {"Read": ["read_file"], "Write": ["write_file"], "Edit": ["edit_file"], "MultiEdit": ["multi_edit"],
            "Bash": ["run_shell"], "Grep": ["search"], "Glob": ["search", "list_dir"], "LS": ["list_dir"],
            "WebFetch": ["web_fetch"], "WebSearch": ["web_search"], "TodoWrite": ["todo_write"],
            "NotebookEdit": ["notebook_edit"], "NotebookRead": ["read_file"]}
AGENT_KEYS = {"name", "description", "tools", "model"}
COMMAND_KEYS = {"description", "argument-hint"}


# ── 켜고 끄기 ──────────────────────────────────────────────────────
def _env_flag(name: str):
    v = os.environ.get(name, "").strip()
    return {"0": False, "1": True}.get(v)


def compat_on(profile=None, interactive: bool = True) -> bool:
    """Claude Code 꼴 호환(프로젝트 CLAUDE.md·.claude/). 환경 변수 > 프로필 > 기본(chat·허브 켬, run 끔)."""
    e = _env_flag(ENV)
    if e is not None:
        return e
    v = getattr(profile, "claude_compat", None)
    return interactive if v is None else bool(v)


def compat_user_on(profile=None, interactive: bool = True) -> bool:
    """사용자 전역 ~/.claude 도 읽을지. 호환이 켜져 있어야 하고, 기본은 끔."""
    if not compat_on(profile, interactive):
        return False
    e = _env_flag(USER_ENV)
    if e is not None:
        return e
    return bool(getattr(profile, "claude_compat_user", None) or False)


@dataclass(frozen=True)
class Flags:
    compat: bool = False
    user: bool = False


def flags(profile=None, interactive: bool = True) -> Flags:
    return Flags(compat_on(profile, interactive), compat_user_on(profile, interactive))


def claude_home() -> Path:
    return Path(os.path.expanduser(os.environ.get(HOME_ENV) or "~/.claude"))


def user_dir() -> Path:
    v = os.environ.get(USER_DIR_ENV)
    if v:
        return Path(os.path.expanduser(v))
    return Path(os.path.expanduser(os.environ.get("XDG_CONFIG_HOME") or "~/.config")) / "stewart"


def _read(f: Path) -> str | None:
    try:
        if not f.is_file() or f.stat().st_size > MAX_FILE_BYTES:
            return None
        return f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# ── 찾는 자리(호환 → 정본, 뒤가 이긴다) ─────────────────────────────
def roots(kind: str, workdir: Path, fl: Flags) -> list[tuple[Path, str, str]]:
    """(폴더, scope user|project, 출처 stewart|agents|claude). kind = skills | commands | agents."""
    dirs = search_dirs(Path(workdir))
    compat: list[tuple[Path, str, str]] = []
    if fl.user:
        compat.append((claude_home() / kind, "user", "claude"))
    for d in dirs:
        if kind == "skills":
            compat.append((d / ".agents" / "skills", "project", "agents"))
        if fl.compat:
            compat.append((d / ".claude" / kind, "project", "claude"))
    if kind == "skills":
        from .skills import user_dir as skills_user_dir
        user = skills_user_dir()
    else:
        user = user_dir() / kind
    canon = [(user, "user", "stewart")] + [(d / ".stewart" / kind, "project", "stewart") for d in dirs]
    return compat + canon


def origin_label(origin: str) -> str:
    return {"claude": ".claude", "agents": ".agents"}.get(origin, ".stewart")


# ── @경로 가져오기 ─────────────────────────────────────────────────
_IMPORT = re.compile(r"(?<![\w`@])@((?:~|\.{1,2})?/?[\w.\-]+(?:/[\w.\-]+)*)")


def _inside(p: Path, allowed: list[Path]) -> bool:
    try:
        rp = p.resolve()
    except OSError:
        return False
    return any(rp == r or r in rp.parents for r in allowed)


def expand_imports(text: str, file: Path, allowed: list[Path], log: list[dict], depth: int = 0,
                   seen: frozenset = frozenset()) -> str:
    """@경로 를 그 파일 내용으로 바꾼다(경로는 그 파일 기준 상대·~·절대). ```·`…` 칸 안은 건드리지 않는다.
    허용 폴더 밖·비밀 파일·없는 파일·깊이 초과·되돌아오는 가져오기는 읽지 않고 그 자리에 까닭을 남긴다(log 에도)."""
    if "@" not in text:
        return text
    out_lines, fence = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
            out_lines.append(line)
            continue
        if fence or "@" not in line:
            out_lines.append(line)
            continue
        parts = re.split(r"(`[^`]*`)", line)
        for i, part in enumerate(parts):
            if i % 2 == 0:
                parts[i] = _IMPORT.sub(lambda m: _one(m.group(1), file, allowed, log, depth, seen), part)
        out_lines.append("".join(parts))
    return "\n".join(out_lines)


def _one(ref: str, file: Path, allowed: list[Path], log: list[dict], depth: int, seen: frozenset) -> str:
    if "/" not in ref and "." not in ref:          # @이름(사람 부르기 등)은 경로가 아니다
        return "@" + ref
    p = Path(os.path.expanduser(ref))
    p = p if p.is_absolute() else (file.parent / p)
    entry = {"from": str(file), "ref": ref, "path": str(p)}
    why = None
    if depth >= IMPORT_DEPTH:
        why = "depth"
    elif not _inside(p, allowed):
        why = "outside"
    elif fswalk.is_secret(p.name):
        why = "secret"
    elif str(p.resolve()) in seen:
        why = "cycle"
    body = None if why else _read(p)
    if why is None and body is None:
        why = "missing"
    if why:
        log.append({**entry, "skipped": why})
        label = {"depth": "가져오기 깊이 상한", "outside": "허용 폴더 밖", "secret": "비밀 파일", "cycle": "되돌아오는 가져오기",
                 "missing": "없는 파일"}[why]
        return f"@{ref} (가져오지 않음: {label})"
    log.append({**entry, "chars": len(body)})
    inner = expand_imports(body.strip(), p, allowed, log, depth + 1, seen | {str(p.resolve())})
    return f"\n[가져옴: {p}]\n{inner}\n[가져옴 끝: {p}]\n"


def import_roots(file: Path, workdir: Path) -> list[Path]:
    """가져오기를 허용하는 폴더: 사용자 전역 파일이면 그 폴더(~/.config/stewart 또는 ~/.claude),
    프로젝트 파일이면 지시 파일 찾기 범위의 맨 위(git 뿌리 또는 작업 폴더)."""
    rf = file.resolve()
    for home in (user_dir(), claude_home()):
        try:
            h = home.resolve()
        except OSError:
            continue
        if h in rf.parents:
            return [h]
    return [search_dirs(Path(workdir))[0].resolve()]


# ── 슬래시 명령 ────────────────────────────────────────────────────
@dataclass
class Command:
    name: str                 # "/이름"(하위 폴더면 "/폴더:이름")
    description: str
    body: str
    scope: str                # user | project
    path: Path
    origin: str = "stewart"   # stewart | claude
    ignored_keys: list[str] = field(default_factory=list)
    shadowed: bool = False    # stewart 내장 명령과 이름이 겹쳐 내장이 이긴다


def _command_files(root: Path) -> list[tuple[str, Path]]:
    out = []
    try:
        files = sorted(root.rglob("*.md")) if root.is_dir() else []
    except OSError:
        files = []
    for f in files:
        rel = f.relative_to(root).with_suffix("")
        if any(part.startswith(".") for part in rel.parts):
            continue
        out.append(("/" + ":".join(rel.parts), f))
    return out


def discover_commands(workdir: Path, fl: Flags, builtin=frozenset()) -> list[Command]:
    found: dict[str, Command] = {}
    for root, scope, origin in roots("commands", workdir, fl):
        for name, f in _command_files(root):
            text = _read(f)
            if text is None or not re.fullmatch(r"/[A-Za-z0-9][\w\-]*(?::[A-Za-z0-9][\w\-]*)*", name):
                continue
            meta, body = parse(text)
            if not body.strip():
                continue
            found.pop(name, None)
            found[name] = Command(name, " ".join(str(meta.get("description") or "").split())[:MAX_DESC], body.strip(), scope,
                                  f, origin, sorted(k for k in meta if k not in COMMAND_KEYS), name in builtin)
    return list(found.values())[:MAX_COMMANDS]


def expand_command(commands: list[Command], text: str) -> tuple[Command, str] | None:
    """"/이름 인자" → (명령, 본문의 $ARGUMENTS·$1… 를 바꾼 글). 내장과 겹친 명령·모르는 이름은 None."""
    t = text.strip()
    if not t.startswith("/"):
        return None
    first, _, arg = t.partition(" ")
    c = next((x for x in commands if x.name == first and not x.shadowed), None)
    if c is None:
        return None
    arg = arg.strip()
    words = arg.split()
    positional = bool(re.search(r"\$[1-9]", c.body))
    body = re.sub(r"\$([1-9])", lambda m: words[int(m.group(1)) - 1] if int(m.group(1)) <= len(words) else "", c.body)
    if "$ARGUMENTS" in body:
        body = body.replace("$ARGUMENTS", arg)
    elif arg and not positional:   # 자리 글이 없으면 인자를 뒤에 붙인다
        body += "\n\n" + arg
    return c, body


def commands_help(commands: list[Command]) -> str:
    if not commands:
        return ""
    rows = []
    for c in commands:
        tag = " (내장 명령과 겹쳐 쓰지 않음)" if c.shadowed else ""
        rows.append(f"  {c.name} — {c.description or '(설명 없음)'} [{'프로젝트' if c.scope == 'project' else '사용자'} "
                    f"{origin_label(c.origin)}]{tag}")
    return "프로젝트·사용자 명령(.stewart/commands, 호환 .claude/commands):\n" + "\n".join(rows)


# ── 하위 에이전트 ───────────────────────────────────────────────────
@dataclass
class Agent:
    name: str
    description: str
    body: str
    scope: str
    path: Path
    tools: list[str] | None           # stewart 이름으로 바꾼 것(None = 바탕 도구 그대로)
    model: str | None
    origin: str = "stewart"
    ignored_keys: list[str] = field(default_factory=list)
    unknown_tools: list[str] = field(default_factory=list)


def _map_tools(raw: str) -> tuple[list[str] | None, list[str]]:
    """앞머리 tools: stewart 도구 이름은 그대로, Claude Code 이름(Read·Bash…)은 바꾼다. 모르는 것은 기록."""
    from . import tools as T
    names = [x.strip() for x in re.split(r"[,\s]+", raw or "") if x.strip()]
    if not names:
        return None, []
    known = set(T.TOOLS)
    out, unknown = [], []
    for n in names:
        base = n.split("(")[0]
        mapped = [base] if base in known else TOOL_MAP.get(base)
        if mapped:
            out += [t for t in mapped if t not in out]
        else:
            unknown.append(n)
    return out, unknown


def discover_agents(workdir: Path, fl: Flags) -> list[Agent]:
    found: dict[str, Agent] = {}
    for root, scope, origin in roots("agents", workdir, fl):
        try:
            files = sorted(root.glob("*.md")) if root.is_dir() else []
        except OSError:
            files = []
        for f in files:
            text = _read(f)
            if text is None:
                continue
            meta, body = parse(text)
            name = str(meta.get("name") or f.stem).strip()
            desc = " ".join(str(meta.get("description") or "").split())
            if not re.fullmatch(r"[A-Za-z0-9][\w\-]{0,63}", name) or not desc or not body.strip():
                continue
            tools, unknown = _map_tools(str(meta.get("tools") or ""))
            found.pop(name, None)
            found[name] = Agent(name, desc, body.strip(), scope, f, tools,
                                (str(meta.get("model")).strip() or None) if meta.get("model") else None, origin,
                                sorted(k for k in meta if k not in AGENT_KEYS), unknown)
    return list(found.values())[:MAX_AGENTS]


def find_agent(workdir: Path, name: str, fl: Flags) -> Agent | None:
    return next((a for a in discover_agents(workdir, fl) if a.name == name), None)


def agent_profile(agent: Agent, base):
    """에이전트 정의 → stewart 프로필(바탕 프로필의 상한·설정 그대로, 지시문·도구만 바꿈).
    model 은 쓰지 않는다(백엔드는 부모 것). 도구는 바탕 도구 안에서만 줄어든다."""
    tools = base.tools
    if agent.tools is not None:
        tools = agent.tools if base.tools == ["*"] else [t for t in base.tools if t in agent.tools]
    return dataclasses.replace(base, name=f"agent:{agent.name}", system=agent.body, tools=list(tools))


def agents_text(agents: list[Agent]) -> str:
    """지시문에 붙일 하위 에이전트 목록(이름·설명). 없으면 빈 글."""
    if not agents:
        return ""
    head = ("하위 에이전트(.stewart/agents). delegate 의 kind(허브에서는 session_spawn 의 profile)에 이름을 주면 "
            "그 정의로 맡긴다:\n")
    out, used = [], len(head)
    for a in agents:
        d = a.description if len(a.description) <= MAX_DESC else a.description[:MAX_DESC] + "…"
        line = f"- {a.name}: {d}" + (f" [{origin_label(a.origin)}]" if a.origin != "stewart" else "")
        if used + len(line) + 1 > MAX_AGENT_LIST:
            out.append(f"- …(상한 때문에 {len(agents) - len(out)}개 생략)")
            break
        out.append(line)
        used += len(line) + 1
    return head + "\n".join(out)


def agents_log(agents: list[Agent]) -> list[dict]:
    return [{"name": a.name, "scope": a.scope, "origin": a.origin, "path": str(a.path), "tools": a.tools, "model": a.model,
             **({"ignored_keys": a.ignored_keys} if a.ignored_keys else {}),
             **({"unknown_tools": a.unknown_tools} if a.unknown_tools else {})} for a in agents]
