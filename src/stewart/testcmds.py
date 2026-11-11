"""프로젝트 시험 명령 찾기(U2-2 4b): 허브 화면에서 "시험 명령 늘 허용" 을 한 번에 누르게 후보를 낸다.

- 찾는 곳: 프로젝트 뿌리의 AGENTS.md·CLAUDE.md·STEWART.md·README.md 안의 `코드` 와 코드 블록 줄, Makefile 의 test·check 대상.
- 시험 명령 꼴(TEST_RE)에 맞는 것만: unittest·pytest·npm/pnpm/yarn test·make test/check·cargo test·go test 등.
- 후보마다 늘 허용 꼴(하위 명령까지, 예 `python3 -m unittest *`)을 allowrules.candidates 로 만들고 넓이 검사를 거친다.
- 파일은 읽기만 한다(비밀 파일 이름은 처음부터 보지 않는다). 사람이 단추를 눌러야 규칙이 생긴다.
"""

from __future__ import annotations

import re
from pathlib import Path

FILES = ("AGENTS.md", "CLAUDE.md", "STEWART.md", "README.md")
MAX_BYTES = 200_000
MAX_CANDIDATES = 8
TEST_RE = re.compile(r"^(?:[A-Z_][A-Z0-9_]*=\S+\s+)*(?:uv run\s+)?(?:python3?\s+-m\s+(?:unittest|pytest)\b|pytest\b|"
                     r"(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test\b|make\s+(?:test|check)\b|cargo\s+test\b|go\s+test\b|"
                     r"tox\b|nox\b|\./(?:run_)?tests?\.sh\b)")
_CODE = re.compile(r"`([^`\n]{3,200})`")


def _lines(text: str) -> list[str]:
    out, block = [], False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            block = not block
            continue
        if block:
            out.append(line.lstrip("$ ").strip())
        out += [m.strip() for m in _CODE.findall(raw)]
    return out


def find(project: Path) -> list[str]:
    """시험 명령 꼴의 글(중복 없이, 찾은 차례)."""
    seen: list[str] = []
    for name in FILES:
        p = Path(project) / name
        try:
            if p.is_file() and p.stat().st_size <= MAX_BYTES:
                for c in _lines(p.read_text(encoding="utf-8", errors="replace")):
                    if TEST_RE.match(c) and c not in seen:
                        seen.append(c)
        except OSError:
            continue
    mk = Path(project) / "Makefile"
    try:
        if mk.is_file() and mk.stat().st_size <= MAX_BYTES:
            for t in re.findall(r"^(test|check)\s*:", mk.read_text(encoding="utf-8", errors="replace"), re.M):
                c = f"make {t}"
                if c not in seen:
                    seen.append(c)
    except OSError:
        pass
    return seen[:MAX_CANDIDATES]


def offers(project: Path, rules=None) -> list[dict]:
    """[{command, pattern, allowed, reason}] — pattern 은 넓이 검사를 지난 늘 허용 꼴(없으면 reason)."""
    from .allowrules import candidates, check_always
    out = []
    for c in find(project):
        cand = candidates(c, rules or [])
        if not cand["ok"] or len(cand["pieces"]) != 1:
            out.append({"command": c, "pattern": None, "allowed": False,
                        "reason": cand.get("reason") or "이은 명령은 승인 칸에서 조각마다 고른다"})
            continue
        piece = cand["pieces"][0]
        if piece["allowed"]:
            out.append({"command": c, "pattern": None, "allowed": True, "reason": None})
            continue
        opts = {o["kind"]: o["pattern"] for o in piece["options"]}
        pat = opts.get("subcommand") or opts.get("exact")
        why = check_always(pat) if pat else "꼴을 만들지 못했다"
        out.append({"command": c, "pattern": None if why else pat, "allowed": False, "reason": why})
    return out
