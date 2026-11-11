"""셸 명령 허용 규칙: 셸 확인 자리(edits·ask 모드)에서 같은 시험·빌드 명령을 매번 묻지 않게 한다.

규칙 꼴(정규식 아님)
- "npm test"                    정확한 명령(공백은 한 칸으로 맞춰 비교)
- "uv run python -m unittest *" 앞부분 + 단어 경계: 명령이 "uv run python -m unittest" 이거나 그 뒤에 공백으로 이어지면 맞는다
  (Claude Code 꼴 "Bash(npm test:*)"·"Bash(npm test)" 도 같은 뜻으로 읽는다)

판정
- 명령을 ; && || | 줄바꿈으로 나눈 조각마다 규칙을 본다. 모든 조각이 allow 에 맞아야 허용, 하나라도 deny 에 맞으면 거부,
  하나라도 ask 에 맞거나 맞는 규칙이 없으면 지금처럼 묻는다. deny 가 allow 를 이긴다.
- 늘 묻는 꼴: $(…)·백틱·<(…)·>(…)(서브셸), 괄호·중괄호 묶음, 대입(A=1 …), 파일로 가는 리다이렉트(> >> &> <), 백그라운드(&),
  닫히지 않은 따옴표. 버리는 곳으로만 가는 2>&1·>/dev/null·2>/dev/null 은 그 조각을 그대로 본다.
- 이 판정은 셸 확인 자리에서만 쓴다. 경계(작업 폴더 밖 쓰기)·config_guard·비밀 파일 판정이 먼저 돌고 그대로다.

출처(allow 는 사람이 준 것만)
- hub: 허브 승인 화면의 "이 꼴 늘 허용". 허브 폴더의 allow-rules.json 에 두고, 작업자에게는 승인 통로로만 보낸다
  (작업자는 그 파일을 읽지 않는다 — 같은 계정의 셸이 파일을 고쳐도 스스로 허용되지 않게).
- user: <사용자 폴더>/rules.toml(곧 ~/.config/stewart/rules.toml)의 allow·ask·deny 목록.
- repo: 저장소의 .stewart/rules.toml, 호환이면 .claude/settings.json·settings.local.json 의 permissions.
  deny·ask 만 반영하고 allow 는 무시하고 기록한다(남의 저장소가 스스로 권한을 주지 못하게).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

EFFECTS = ("allow", "ask", "deny")
PATTERN_MAX = 200
EXACT_MAX = 4000     # 정확한 꼴(여러 줄 python3 -c "…"·heredoc 본문이 든 것)은 길 수 있다
_HARMLESS_REDIRECT = re.compile(r"\s*(?:2>&1|1>&2|>&2|[12]?>\s*/dev/null|&>\s*/dev/null)\s*")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


@dataclass
class Rule:
    pattern: str            # 정규화한 꼴(앞부분 꼴이면 끝이 " *")
    effect: str             # allow | ask | deny
    source: str             # hub | user | repo
    origin: str = ""        # 읽은 파일(기록용)
    id: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = hashlib.sha256(f"{self.source}:{self.effect}:{self.pattern}".encode()).hexdigest()[:10]

    @property
    def prefix(self) -> bool:
        return self.pattern.endswith(" *")

    @property
    def glob(self) -> bool:
        """"npm run test*" 꼴: 낱말 경계 없이 그 글로 시작하면 맞는다(손으로 적는 꼴)."""
        return self.pattern.endswith("*") and not self.prefix

    def matches(self, piece: str) -> bool:
        if self.prefix:
            head = self.pattern[:-2]
            return piece == head or piece.startswith(head + " ")
        if self.glob:
            return piece.startswith(self.pattern[:-1])
        return piece == self.pattern


def norm(s: str) -> str:
    return " ".join(s.split())


def parse_pattern(raw: str) -> str | None:
    """사람이 쓴 꼴 → 정규화한 꼴. 꼴이 아니면 None(연산자·서브셸·리다이렉트가 든 꼴은 받지 않는다)."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    m = re.fullmatch(r"Bash\((.*)\)", s)
    if m:
        s = m.group(1).strip()
        if s.endswith(":*"):
            s = s[:-2].rstrip() + " *"
        elif s == "*" or s == "":
            return None   # Bash 전체 허용 꼴은 받지 않는다
    pre = s.strip()
    s = norm(s)
    if not s or s == "*":
        return None
    if s.endswith("*"):      # 앞부분 꼴("… *" 낱말 경계, "…*" 글 앞부분): 연산자·서브셸·리다이렉트 글은 받지 않는다
        body = s[:-2] if s.endswith(" *") else s[:-1]
        if not body or len(s) > PATTERN_MAX or "*" in body or re.search(r"[;&|<>`]|\$\(|\(|\)|\{|\}", body):
            return None
        return s
    # 정확한 꼴: 조각(split_command 가 이미 안전하다고 본 것)과 글자 그대로 같을 때만 맞으므로 괄호·따옴표·heredoc 본문도 받는다
    if len(s) > EXACT_MAX:
        return None
    raw0 = pre
    m = _HEREDOC.search(raw0)     # heredoc 본문은 글 그대로라 앞머리(표 앞)만 조각 꼴인지 본다(정규화 전 글로 — 줄바꿈 = 명령 둘)
    head = raw0[:m.start()].strip() if m else raw0
    try:
        ok = split_command(head) == [norm(head)] if head else False
    except Unsafe:
        ok = False
    return s if ok else None      # 한 조각이 아닌 꼴(; && | > $( ) …)은 받지 않는다 — 조각과 같을 일이 없어 헷갈리기만 한다


# ── 명령 나누기 ─────────────────────────────────────────────────────
class Unsafe(Exception):
    """규칙으로 판단하지 않고 늘 묻는 꼴."""


def split_command(cmd: str) -> list[str]:
    """; && || | & 줄바꿈으로 나눈 조각(정규화). 늘 묻는 꼴이면 Unsafe."""
    out = []
    for p in split_raw(cmd):
        p = _HARMLESS_REDIRECT.sub(" ", p)
        chk = re.sub(HD + ".*?" + HD, " ", p, flags=re.S)
        chk = _HEREDOC.sub(" ", chk)
        if re.search(r"[<>]", _strip_quoted(chk)):
            raise Unsafe("리다이렉트")
        p = norm(p.replace(HD, " "))
        if not p:
            continue
        if _ASSIGN.match(p):
            raise Unsafe("대입")
        out.append(p)
    return out


def split_raw(cmd: str) -> list[str]:
    """연산자(; && || | 줄바꿈)로만 나눈 날 조각. 서브셸·묶음·백그라운드·닫히지 않은 따옴표는 Unsafe."""
    pieces, cur, i, n = [], [], 0, len(cmd)
    quote = None
    heredocs: list[tuple[str, bool]] = []   # 이 줄에서 연 heredoc(끝 표지, <<- 인지)
    while i < n:
        c = cmd[i]
        if quote == "'":
            if c == "'":
                quote = None
            cur.append(c)
            i += 1
            continue
        if quote == '"':
            if c == "\\" and i + 1 < n:
                cur.append(cmd[i:i + 2])
                i += 2
                continue
            if c == '"':
                quote = None
            elif c == "`" or cmd.startswith("$(", i):
                raise Unsafe("서브셸")
            cur.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            cur.append(cmd[i:i + 2])
            i += 2
            continue
        if c in "'\"":
            quote = c
            cur.append(c)
            i += 1
            continue
        if c == "`" or cmd.startswith("$(", i) or cmd.startswith("<(", i) or cmd.startswith(">(", i):
            raise Unsafe("서브셸")
        if cmd.startswith("<<", i) and not cmd.startswith("<<<", i):   # heredoc: 본문은 풀지 않고 조각에 그대로 붙인다
            m = _HEREDOC.match(cmd, i)
            if not m:
                raise Unsafe("heredoc 꼴")
            heredocs.append((m.group("delim"), bool(m.group("dash"))))
            cur.append(m.group(0))
            i = m.end()
            continue
        if c in "(){}":
            raise Unsafe("묶음")
        if c == "\n" and heredocs:   # 이 줄의 heredoc 본문들을 끝 표지 줄까지 읽는다
            i += 1
            for delim, dash in heredocs:
                body = []
                while True:
                    if i >= n:
                        raise Unsafe("끝 표지 없는 heredoc")
                    j = cmd.find("\n", i)
                    line = cmd[i:] if j < 0 else cmd[i:j]
                    i = n if j < 0 else j + 1
                    if (line.lstrip("\t") if dash else line) == delim:
                        break
                    body.append(line)
                cur.append(HD + "\n".join(body) + "\n" + delim + HD)
            heredocs = []
            pieces.append("".join(cur))
            cur = []
            continue
        if c in ";\n":
            pieces.append("".join(cur))
            cur = []
            i += 1
            continue
        if c in "&|":
            if cmd.startswith("&>", i) or (c == "&" and i > 0 and cmd[i - 1] in "<>"):
                cur.append(c)          # &> 리다이렉트·>& 의 일부(아래에서 본다)
                i += 1
                continue
            if c == "&" and not cmd.startswith("&&", i):
                raise Unsafe("백그라운드")
            pieces.append("".join(cur))
            cur = []
            i += 2 if cmd.startswith(("&&", "||"), i) else 1
            continue
        cur.append(c)
        i += 1
    if quote:
        raise Unsafe("닫히지 않은 따옴표")
    if heredocs:
        raise Unsafe("끝 표지 없는 heredoc")
    pieces.append("".join(cur))
    return pieces


HD = "\x00"   # 조각 안 heredoc 본문의 앞뒤 표(split_command 가 풀어 글로 바꾼다)
_HEREDOC = re.compile(r"<<(?P<dash>-)?[ \t]*(?P<q>['\"]?)(?P<delim>[A-Za-z_][\w.-]*)(?P=q)")


@dataclass
class Ctx:
    """판정 문맥(chat·엔진이 준다). 이것이 있으면 읽기 명령 기본 통과·cd·작업 폴더 안 리다이렉트·안전 변수를 본다."""
    workdir: Path
    cwd: Path
    is_secret: object                 # Path -> bool
    allow_redirect: bool = False      # 작업 폴더 안 파일로 가는 > >> (edits 모드에서만: 편집을 묻지 않는 모드)
    builtin_read: bool = True


@dataclass
class Piece:
    text: str
    cwd: Path
    cd_ok: bool = False               # 작업 폴더 안으로 가는 cd


_REDIR = re.compile(r"(?:^|(?<=\s))(?P<op>>>|>|1>>|1>|2>>|2>|&>>|&>|<)\s*(?P<target>[^\s<>|&;]+)")


def _target_ok(target: str, cwd: Path, ctx: Ctx, write: bool = False) -> bool:
    from .readcmds import path_ok
    if target.startswith(("'", '"')):
        target = target.strip("'\"")
    if not path_ok(target, cwd, ctx.workdir, ctx.is_secret):
        return False
    if write:   # stewart 설정 폴더(config_guard 가 지키는 곳)로 가는 쓰기는 작업 폴더 안이라도 묻는다
        from . import config_guard as CG
        rp = (Path(target) if Path(target).is_absolute() else cwd / target).resolve()
        if any(rp == d or d in rp.parents for d in CG.protected_dirs()):
            return False
    return True


def prepare(cmd: str, ctx: Ctx) -> list[Piece]:
    """날 조각마다 버리는 리다이렉트를 떼고, 작업 폴더 안 리다이렉트·안전 변수 대입을 떼고, cd 로 옮겨 가는 폴더를 따라간다.
    못 떼는 리다이렉트·대입은 Unsafe(늘 묻기)."""
    from .readcmds import SAFE_ENV, cd_target
    root = Path(ctx.workdir).resolve()
    cur = Path(ctx.cwd).resolve()
    out: list[Piece] = []
    for raw in split_raw(cmd):
        p = _HARMLESS_REDIRECT.sub(" ", raw)
        q = _strip_quoted(p)
        if re.search(r"[<>]", q):
            for m in list(_REDIR.finditer(p)):
                op, target = m.group("op"), m.group("target")
                if op != "<" and not ctx.allow_redirect:
                    raise Unsafe("리다이렉트")
                if not _target_ok(target, cur, ctx, write=op != "<"):
                    raise Unsafe("작업 폴더 밖·비밀 파일로 가는 리다이렉트")
            p = _REDIR.sub(" ", p)
            if re.search(r"[<>]", _strip_quoted(p)):
                raise Unsafe("리다이렉트")
        p = norm(p)
        while (m := re.match(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s'\"$`]*)(?:\s+|$)", p)):
            if m.group(1) not in SAFE_ENV:
                raise Unsafe("대입")
            p = p[m.end():]
        if _ASSIGN.match(p):
            raise Unsafe("대입")
        if not p:
            continue
        if p == "cd" or p.startswith("cd "):
            t = cd_target(p, cur, root)
            out.append(Piece(p, cur, cd_ok=t is not None))
            if t is not None:
                cur = t
            continue
        out.append(Piece(p, cur))
    return out


def _strip_quoted(s: str) -> str:
    return re.sub(r"'[^']*'|\"(?:\\.|[^\"\\])*\"", "''", s)


# ── 규칙 모음 ───────────────────────────────────────────────────────
@dataclass
class Decision:
    effect: str | None        # allow | deny | ask | None(맞는 규칙 없음 = 지금처럼 묻기)
    rule_ids: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    reason: str = ""


def decide(cmd: str, rules: list[Rule], ctx: Ctx | None = None) -> Decision:
    """deny → ask 규칙 → (ctx 가 있으면) 작업 폴더 안 cd·읽기 명령 기본 통과 → allow 규칙. 모든 조각이 통과해야 allow."""
    try:
        pieces = prepare(cmd, ctx) if ctx is not None else [Piece(p, Path(".")) for p in split_command(cmd)]
    except Unsafe as e:
        return Decision("ask", reason=str(e))
    if not pieces:
        return Decision(None, reason="빈 명령")
    deny = [r for r in rules if r.effect == "deny"]
    ask = [r for r in rules if r.effect == "ask"]
    allow = [r for r in rules if r.effect == "allow"]
    ids: list[str] = []
    sources: set[str] = set()
    for p in pieces:
        d = next((r for r in deny if r.matches(p.text)), None)
        if d:
            return Decision("deny", [d.id], [d.source], "deny 규칙")
    for p in pieces:
        a = next((r for r in ask if r.matches(p.text)), None)
        if a:
            return Decision("ask", [a.id], [a.source], "ask 규칙")
    for p in pieces:
        if ctx is not None and p.cd_ok:
            sources.add("builtin")
            continue
        if ctx is not None and ctx.builtin_read:
            from .readcmds import read_ok
            if read_ok(p.text, p.cwd, Path(ctx.workdir).resolve(), ctx.is_secret):
                sources.add("builtin")
                if "read" not in ids:
                    ids.append("read")
                continue
        a = next((r for r in allow if r.matches(p.text)), None)
        if a is None:
            return Decision(None, reason="맞는 allow 규칙이 없는 조각")
        if a.id not in ids:
            ids.append(a.id)
        sources.add(a.source)
    return Decision("allow", ids or ["cd"], sorted(sources), "모든 조각이 통과")


def _from_lists(d: dict, source: str, origin: str, allow_ok: bool, ignored: list[dict]) -> list[Rule]:
    out = []
    for eff in EFFECTS:
        for raw in d.get(eff) or []:
            pat = parse_pattern(raw)
            if pat is None:
                ignored.append({"origin": origin, "effect": eff, "why": "꼴 아님"})
                continue
            if eff == "allow" and not allow_ok:
                ignored.append({"origin": origin, "effect": eff, "pattern": pat, "why": "저장소 파일의 allow 는 무시"})
                continue
            out.append(Rule(pat, eff, source, origin))
    return out


def _toml(p: Path) -> dict:
    try:
        return tomllib.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except (OSError, tomllib.TOMLDecodeError):
        return {"_broken": True}


def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except (OSError, ValueError):
        return {"_broken": True}


class RuleSet:
    """사용자·저장소 규칙(파일)과 허브 규칙(메모리). 파일은 바뀌었을 때만 다시 읽는다."""

    def __init__(self, workdir: Path, compat: bool = False, hub_rules=None):
        self.workdir = Path(workdir)
        self.compat = compat
        self.hub_rules = hub_rules or (lambda: [])     # 허브: 메모리·승인 통로로 받은 것
        self._stamp = None
        self._rules: list[Rule] = []
        self.ignored: list[dict] = []

    def files(self) -> list[tuple[Path, str]]:
        from .memfile import search_dirs
        from .projcfg import user_dir
        out = [(user_dir() / "rules.toml", "user")]
        for d in search_dirs(self.workdir):
            out.append((d / ".stewart" / "rules.toml", "repo"))
            if self.compat:
                out += [(d / ".claude" / "settings.json", "repo"), (d / ".claude" / "settings.local.json", "repo")]
        return out

    def _load(self) -> list[Rule]:
        fs = self.files()
        stamp = []
        for p, _ in fs:
            try:
                st = p.stat()
                stamp.append((str(p), st.st_mtime_ns, st.st_size))
            except OSError:
                stamp.append((str(p), None, None))
        if stamp == self._stamp:
            return self._rules
        rules, ignored = [], []
        for p, source in fs:
            if p.suffix == ".toml":
                d = _toml(p)
            else:
                raw = _json(p)
                if raw.get("_broken"):
                    d = raw
                else:
                    d = raw.get("permissions") or {}
                    d = {k: [x for x in (d.get(k) or []) if isinstance(x, str) and x.startswith("Bash(")] for k in EFFECTS} \
                        if isinstance(d, dict) else {}
                    d = d if any(d.values()) else {}
            if not d:
                continue
            if d.get("_broken"):
                ignored.append({"origin": str(p), "why": "읽을 수 없는 파일"})
                continue
            rules += _from_lists(d, source, str(p), allow_ok=(source == "user"), ignored=ignored)
        self._rules, self.ignored, self._stamp = rules, ignored, stamp
        return rules

    def all(self) -> list[Rule]:
        return self._load() + list(self.hub_rules())

    def decide(self, cmd: str, ctx: Ctx | None = None) -> Decision:
        return decide(cmd, self.all(), ctx)


# ── 허브의 "이 꼴 늘 허용" 저장 ────────────────────────────────────
class HubStore:
    """허브 폴더의 allow-rules.json(프로젝트마다 허브 폴더가 따로다). 저장소 안이 아님."""

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / "allow-rules.json"

    def load(self) -> list[Rule]:
        d = _json(self.path)
        out = []
        for r in d.get("rules") or [] if isinstance(d, dict) else []:
            pat = parse_pattern(r.get("pattern", "")) if isinstance(r, dict) else None
            if pat and r.get("effect") == "allow":
                out.append(Rule(pat, "allow", "hub", str(self.path)))
        return out

    def save(self, rules: list[Rule]) -> None:
        from .hub import write_json
        write_json(self.path, {"rules": [{"pattern": r.pattern, "effect": r.effect, "id": r.id} for r in rules]})
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def add(self, raw: str, broad_ok: bool = False) -> Rule:
        pat = parse_pattern(raw)
        if pat is None:
            raise ValueError("규칙 꼴이 아니다(정확한 명령 또는 '앞부분 *'. ; && | > $( ) 같은 것은 넣지 않는다)")
        why = check_always(pat, broad_ok=broad_ok)
        if why:
            raise ValueError(why)
        rules = self.load()
        r = Rule(pat, "allow", "hub", str(self.path))
        if all(x.id != r.id for x in rules):
            rules.append(r)
            self.save(rules)
        return r

    def remove(self, rule_id: str) -> bool:
        rules = self.load()
        keep = [r for r in rules if r.id != rule_id]
        if len(keep) == len(rules):
            return False
        self.save(keep)
        return True


def rules_payload(rules: list[Rule]) -> list[dict]:
    return [asdict(r) for r in rules]


def rules_from_payload(items) -> list[Rule]:
    out = []
    for x in items or []:
        if isinstance(x, dict) and x.get("effect") == "allow" and x.get("source") == "hub":
            pat = parse_pattern(x.get("pattern", ""))
            if pat:
                out.append(Rule(pat, "allow", "hub", ""))
    return out


def suggest(cmd: str) -> str:
    """승인 화면에 미리 넣어 둘 꼴: 첫 조각 그대로(사람이 고쳐 '앞부분 *' 로 넓힐 수 있다)."""
    try:
        pieces = split_command(cmd)
    except Unsafe:
        return ""
    return pieces[0] if len(pieces) == 1 and parse_pattern(pieces[0]) else ""


# ── 늘 허용 후보·넓이 검사(허브 승인 화면) ─────────────────────────────
# 뒤에 오는 글을 코드나 다른 명령으로 돌리는 첫 낱말(이것만으로, 또는 이 자리에서 끝나는 꼴은 너무 넓다)
LAUNCHERS = frozenset("""sh bash zsh dash fish ksh env sudo doas su xargs exec eval command builtin nohup time nice timeout
watch stdbuf python python2 python3 pypy node deno bun ruby perl php lua osascript uv uvx npx pnpx pipx poetry pdm hatch
conda mamba docker podman kubectl ssh scp git""".split())
# 두 낱말이 하나의 실행기인 꼴(이 자리에서 끝나면 너무 넓다)
LAUNCHER_PAIRS = frozenset({"uv run", "uv tool", "npm exec", "npm run", "npm x", "pnpm exec", "pnpm dlx", "pnpm run",
                            "yarn dlx", "yarn run", "yarn exec", "bundle exec", "cargo run", "go run", "poetry run",
                            "pipx run", "pdm run", "hatch run", "conda run", "docker run", "docker exec", "kubectl exec",
                            "git -c", "make -f", "make -C"})
CODE_FLAGS = frozenset({"-c", "-e", "-m", "--eval", "--exec", "-x", "--command", "-"})   # "-" = 표준입력(heredoc)의 코드
# 늘 허용을 내지 않는 명령(한 번 허용만). 앞 낱말 꼴로 본다
NEVER = ("sudo", "doas", "su", "git push", "git reset --hard", "git clean", "git filter-branch", "curl", "wget", "chmod",
         "chown", "chgrp", "dd", "diskutil", "pip install", "pip3 install", "uv pip install", "uv add", "uv tool install",
         "npm install", "npm i", "npm add", "npm ci", "pnpm add", "pnpm install", "yarn add", "yarn install",
         "brew install", "apt", "apt-get", "gem install", "cargo install", "go install", "launchctl", "defaults write",
         "crontab", "kill", "pkill", "killall", "shutdown", "reboot", "security")
# 저장소 코드(스크립트·시험·빌드 정의)를 실행하는 첫 낱말: 늘 허용하면 모델이 고친 코드가 묻지 않고 돈다
CODE_RUNNERS = frozenset("""npm pnpm yarn bun deno make just cmake ninja pytest tox nox python python3 node uv cargo go gradle
gradlew mvn ant rake bundle swift xcodebuild dotnet php composer""".split())


def never_always(piece: str) -> bool:
    """늘 허용을 내지 않는 조각(위험 명령). rm 은 r·R·f 옵션이 하나라도 있으면."""
    w = piece.split()
    for n in NEVER:
        nw = n.split()
        if w[:len(nw)] == nw:
            return True
    if w and w[0] == "rm" and any(x.startswith("-") and any(c in x for c in "rRf") for x in w[1:]):
        return True
    return bool(w) and w[0].startswith("mkfs")


def too_broad(pattern: str) -> str | None:
    """앞부분 꼴("… *")이 너무 넓으면 까닭. 정확한 꼴은 넓이를 따지지 않는다."""
    if not pattern.endswith(" *"):
        return None
    w = pattern[:-2].split()
    if not w:
        return "빈 앞부분"
    if w[-1] in LAUNCHERS:
        return f"'{w[-1]}' 에서 끝나 무엇이든 실행할 수 있다"
    if len(w) >= 2 and " ".join(w[-2:]) in LAUNCHER_PAIRS:
        return f"'{' '.join(w[-2:])}' 에서 끝나 무엇이든 실행할 수 있다"
    if w[-1] in CODE_FLAGS:
        return f"'{w[-1]}' 뒤에 오는 코드를 그대로 실행한다"
    if len(w) == 1 and w[0] in ("npm", "pnpm", "yarn", "make", "cargo", "go", "bundle", "rake"):
        return f"'{w[0]} *' 은 모든 하위 명령을 허용한다"
    return None


def check_always(pattern: str, broad_ok: bool = False) -> str | None:
    """허브 '늘 허용' 으로 받을 수 있는 꼴인지. 못 받으면 까닭, 받으면 None.
    broad_ok: 사람이 승인 화면·규칙 칸에서 직접 고른 꼴 — 넓은 꼴(python3 -c *·bash *)도 받는다(경고는 broad_note 로 보인다).
    위험 명령(NEVER: sudo·git push·curl·rm -rf …)은 어느 쪽이든 받지 않는다(한 번 허용만)."""
    p = parse_pattern(pattern)
    if p is None:
        return "규칙 꼴이 아니다(정확한 명령, '앞부분 *', 또는 '글*'. 앞부분 꼴에는 ; && | > $( ) 를 넣지 않는다)"
    if never_always(p.rstrip("*").rstrip()):
        return "위험 명령이라 늘 허용하지 않는다(한 번 허용만)"
    return None if broad_ok else too_broad(p)


BROAD_NOTE = "이 규칙은 그 프로그램으로 도는 모든 코드를 묻지 않고 돌린다"


def broad_note(pattern: str) -> str:
    """넓은 꼴에 붙이는 한 줄(막지 않는다 — 사람 결정). 좁으면 빈 글."""
    why = too_broad(pattern) if pattern.endswith(" *") else None
    if pattern.endswith("*") and not pattern.endswith(" *"):
        why = why or "글 앞부분만 맞으면 허용한다"
    return f"{BROAD_NOTE}({why})" if why else ""


def _code_prefix(piece: str) -> str | None:
    """코드를 받는 앞머리("python3 -c *", "bash -c *", "python3 - *"(heredoc·표준입력), "uv run python -c *")."""
    w = piece.split()
    for k in range(1, min(len(w), 6)):
        if w[k] in CODE_FLAGS or w[k] == "-":
            if any(re.search(r"['\"<>]", x) for x in w[:k + 1]):
                return None
            return " ".join(w[:k + 1]) + " *"
    return None


def _subcommand_prefix(piece: str) -> str | None:
    """첫 낱말 + 하위 명령까지의 앞부분 꼴. 실행기 자리에서 끝나면 한 낱말씩 늘린다. 못 만들면 None."""
    w = piece.split()
    if len(w) < 2:
        return None
    k = 1
    while k < len(w) and re.fullmatch(r"[A-Za-z][\w.:+-]*", w[k]):
        k += 1
    while k < len(w) and too_broad(" ".join(w[:k]) + " *") is not None:
        if re.search(r"['\"]", w[k]):
            return None
        k += 1
    cand = " ".join(w[:min(k, len(w))]) + " *"   # 하위 명령까지가 명령 전체면(git status) 그 뒤 인자를 열어 둔다
    return cand if check_always(cand) is None else None


def candidates(cmd: str, rules: list[Rule] | None = None) -> dict:
    """승인 화면의 '이 꼴 늘 허용' 판: 조각마다 고를 꼴(정확히 / 하위 명령까지 / 첫 낱말만).
    {"ok", "reason"(못 쓰는 까닭), "pieces": [{"piece", "allowed"(이미 허용), "options": [{"pattern", "kind", "warn"}]}]}"""
    try:
        pieces = split_command(cmd)
    except Unsafe as e:
        return {"ok": False, "reason": f"늘 허용으로 풀 수 없는 꼴({e})", "pieces": []}
    if not pieces:
        return {"ok": False, "reason": "빈 명령", "pieces": []}
    allow = [r for r in rules or [] if r.effect == "allow"]
    out = []
    for p in pieces:
        if any(r.matches(p) for r in allow):
            out.append({"piece": p, "allowed": True, "options": []})
            continue
        if never_always(p):
            return {"ok": False, "reason": f"위험 명령이 들어 있어 늘 허용하지 않는다({p.split()[0]})", "pieces": []}
        warn = "저장소 코드를 실행합니다(모델이 고친 파일도 묻지 않고 돈다)" if p.split()[0] in CODE_RUNNERS else ""
        opts: list[dict] = []
        exact = parse_pattern(p) if not p.endswith("*") else None   # 끝이 * 인 명령(ls *)은 정확한 꼴로 적을 수 없다
        for kind, pat in (("exact", exact), ("subcommand", _subcommand_prefix(p)), ("code_prefix", _code_prefix(p)),
                          ("first_word", p.split()[0] + " *")):
            if pat and check_always(pat, broad_ok=True) is None and pat not in [o["pattern"] for o in opts]:
                note = broad_note(pat)
                opts.append({"pattern": pat, "kind": kind, "warn": note or warn, "broad": bool(note)})
        if not opts:
            return {"ok": False, "reason": f"규칙으로 만들 수 없는 조각({p[:60]})", "pieces": []}
        out.append({"piece": p, "allowed": False, "options": opts})
    return {"ok": True, "reason": "", "pieces": out}
