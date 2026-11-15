"""지시문 절 켬끔(판 154): 등급 칸 `prompt = { … }` 로 지시문의 절을 빼기만 한다. 남긴 글은 한 글자도 고치지 않는다.

칸(적지 않은 절은 지금 그대로):
- role: "full" | "line" | "off" — 허브 역할 글(HUB_MAIN_NOTE). line 은 머리 줄과 첫 맡기기 줄만 남긴다
- board: true | false — 현황판 지시
- engine_core: true | false — 내장 chat 지시문의 코딩·확인 절(CORE_PREFIXES 로 시작하는 항목). 정체·안전·승인·답 꼴 줄은 남는다
- place_hints: true | false — 자리 안내(판 141, 기본 끔). 적으면 그 값, STEWART_PLACE_HINTS=0 이 이긴다
- nudges: true | false — 대화 길의 넛지(의도·할 일·웹·맡기기)와 허브 목표 알림
- examples: true | false — 예시 은행 붙이기
- memory_guide: true | false — 기억 안내와 색인(기억 도구는 그대로 — 도구는 tools 칸으로)
- user_memory: true | false — 사용자 기억 글(사용자 설정 폴더의 STEWART.md)
- claude_md: true | false — CLAUDE.md 호환 글(작업 폴더·사용자 전역의 CLAUDE.md)
- skills_index: "full" | "names" | "off" — 스킬 색인(이름·설명) · 이름만 · 뺌(load_skill 도구는 그대로)
- filler_file: "<경로>" — (측정용) 그 파일의 글을 프로필 지시 끝(기억 글·스킬 색인 앞)에 그대로 붙인다. 길이만 맞추는 칸을 만들 때 쓴다.
  상대 경로는 백엔드 표가 있는 폴더 기준. 읽지 못하면 시작하지 않는다. 기능 지문에 sha·글자 수가 남는다
작업 폴더의 AGENTS.md·STEWART.md(프로젝트 규칙)는 늘 남는다.
"""

from __future__ import annotations

KEY = "prompt"
CHOICES = {"role": ("full", "line", "off"), "skills_index": ("full", "names", "off")}
BOOLS = ("board", "engine_core", "place_hints", "nudges", "examples", "memory_guide", "user_memory", "claude_md")
PATHS = ("filler_file",)
FIELDS = tuple(CHOICES) + BOOLS + PATHS

# 내장 chat 지시문의 코딩·확인 절 — 이 머리로 시작하는 항목(이어지는 들여 쓴 줄 포함)을 뺀다
CORE_PREFIXES = ("- 요청을 받으면 필요한 만큼만 읽고", "- 고칠 때는 edit_file", "- 고친 뒤에는 테스트나 실행으로 확인한다",
                 "- 확인하고 말한다(판 62)", "- 오류 코드·메시지는", "- 맡기기(판 97·108)", "- 단계 세우고 묻기(판 67)")


def check(v) -> tuple[dict, list[str]]:
    """표 값 → (쓸 값, 쓰지 않는 칸·값 알림). 표·칸이 없으면 ({}, [])."""
    if v is None:
        return {}, []
    if not isinstance(v, dict):
        return {}, [f"{KEY} 는 {{ role = \"line\", … }} 꼴의 표여야 한다 — 이 값은 쓰지 않는다"]
    out, notes = {}, []
    for k, x in v.items():
        if k in CHOICES:
            if x in CHOICES[k]:
                out[k] = x
            else:
                notes.append(f"{KEY}.{k} 는 {' · '.join(CHOICES[k])} 가운데 하나 — '{x}' 는 쓰지 않는다")
        elif k in PATHS:
            if isinstance(x, str) and x.strip():
                out[k] = x
            else:
                notes.append(f"{KEY}.{k} 는 파일 경로 글 — 이 값은 쓰지 않는다")
        elif k in BOOLS:
            if isinstance(x, bool):
                out[k] = x
            else:
                notes.append(f"{KEY}.{k} 는 true·false — '{x}' 는 쓰지 않는다")
        else:
            notes.append(f"{KEY} 의 모르는 칸 {k} — 쓰지 않는다(아는 칸: {', '.join(FIELDS)})")
    return out, notes


def of(profile) -> dict:
    """프로필 raw["tier"]["prompt"] 의 쓸 값(없으면 {} = 모두 지금 그대로)."""
    t = ((getattr(profile, "raw", None) or {}).get("tier") or {})
    return check(t.get(KEY))[0]


def on(parts: dict, name: str) -> bool:
    return parts.get(name, True) is not False


def strip_role(system: str, parts: dict) -> str:
    """허브 역할 글·현황판 지시를 칸대로 뺀다(with_role 이 붙인 글 그대로일 때만)."""
    from .board import NOTE as BOARD_NOTE
    from .hubmain import HUB_MAIN_NOTE
    role, s0 = parts.get("role", "full"), system
    if role != "full" and HUB_MAIN_NOTE in system:
        keep = "\n".join(HUB_MAIN_NOTE.splitlines()[:2]) if role == "line" else ""
        system = system.replace(HUB_MAIN_NOTE, keep)
    if not on(parts, "board") and BOARD_NOTE in system:
        system = system.replace(BOARD_NOTE, "")
    return _tidy(system) if system != s0 else system


def strip_core(system: str) -> str:
    """CORE_PREFIXES 로 시작하는 항목(뒤에 이어진 들여 쓴 줄까지)을 뺀다."""
    out, skip = [], False
    for ln in system.split("\n"):
        if ln.startswith("- "):
            skip = ln.startswith(CORE_PREFIXES)
        elif not ln.startswith((" ", "\t")):
            skip = False
        if not skip:
            out.append(ln)
    return "\n".join(out)


def apply_base(system: str, parts: dict) -> str:
    """프로필 지시문(역할 글이 붙은 것)에 role·board·engine_core 를 건다. 칸이 없으면 그대로."""
    if not parts:
        return system
    if not on(parts, "engine_core"):   # 역할 글 안의 항목은 role 칸 몫 — 그 앞(프로필 지시)에서만 뺀다
        from .hubmain import HUB_MAIN_NOTE
        i = system.find(HUB_MAIN_NOTE)
        system = strip_core(system) if i < 0 else strip_core(system[:i]) + system[i:]
    return strip_role(system, parts)


def _tidy(s: str) -> str:
    """뺀 자리에 남은 빈 줄을 줄인다(셋 넘는 줄바꿈 → 둘, 끝 공백)."""
    import re
    return re.sub(r"\n{3,}", "\n\n", s).rstrip()


def memfile_skip(parts: dict) -> dict:
    """memfile.collect 에 넘길 뺄 것: user(사용자 STEWART.md)·claude(CLAUDE.md)."""
    return {"user": not on(parts, "user_memory"), "claude": not on(parts, "claude_md")}


def skills(skill_list, parts: dict, full_text, names_text) -> str:
    """스킬 색인 글(칸대로). full_text·names_text 는 글을 만드는 함수."""
    if not skill_list:
        return ""
    m = parts.get("skills_index", "full")
    return "" if m == "off" else names_text(skill_list) if m == "names" else full_text(skill_list)


def filler(parts: dict) -> tuple[str, dict | None]:
    """filler_file 의 글과 기록(sha256 앞 16·글자 수·파일 이름). 칸이 없으면 ("", None). 읽지 못하면 ConfigError(그 칸 없이 돌지 않게)."""
    f = parts.get("filler_file")
    if not f:
        return "", None
    import hashlib
    import os
    from pathlib import Path
    from .config import ConfigError
    try:
        text = Path(os.path.expanduser(f)).read_text(encoding="utf-8").rstrip()
    except OSError as e:
        raise ConfigError(f"등급 {KEY}.filler_file 을 읽지 못한다: {f} ({type(e).__name__})") from None
    return text, {"sha": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], "chars": len(text), "name": Path(f).name}
