"""프로젝트 기억 파일(Claude Code 의 CLAUDE.md 에 해당): 작업 폴더의 AGENTS.md·STEWART.md 를 지시문에 넣는다.

- 찾는 범위: 작업 폴더에서 위로 올라가며 git 저장소 뿌리(.git 이 있는 폴더)까지만. 작업 폴더가 git 저장소 안이 아니면
  작업 폴더 자신만 본다(작업 폴더 밖 파일을 읽지 않는다).
- 순서: 위(뿌리) → 아래(작업 폴더). 더 구체적인 아래 폴더의 글이 뒤에 온다.
- 한 폴더 안에서는 프로필 memory_files 순서(기본 AGENTS.md, STEWART.md).
- 상한: 합쳐 max_chars 자(기본 12,000). 넘으면 뒤쪽(작업 폴더에 가까운 것)을 남기고 앞쪽 파일을 자른다.
- 어떤 파일을 몇 자 넣었는지는 run_start meta 의 memory_files 에 남긴다.
- 파일 내용은 모델에 지시문으로 들어간다(저장소에 든 글 = 프롬프트 주입 경로). 무인·자동 허락 사용은 격리 뒤다.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_NAMES = ("AGENTS.md", "STEWART.md")
DEFAULT_MAX_CHARS = 12000


def git_root(start: Path) -> Path | None:
    """start 또는 그 위에서 .git(폴더 또는 worktree 의 파일)이 있는 첫 폴더. 없으면 None."""
    p = start.resolve()
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return d
    return None


def search_dirs(workdir: Path) -> list[Path]:
    """위(git 뿌리) → 아래(작업 폴더). git 밖이면 [작업 폴더]."""
    wd = workdir.resolve()
    root = git_root(wd)
    if root is None:
        return [wd]
    dirs = [wd]
    while dirs[-1] != root:
        dirs.append(dirs[-1].parent)
    return list(reversed(dirs))


def collect(workdir: Path, names=DEFAULT_NAMES, max_chars: int = DEFAULT_MAX_CHARS) -> tuple[str, list[dict]]:
    """(지시문에 붙일 글, 기록용 목록). 파일이 없으면 ("", [])."""
    found: list[tuple[Path, str]] = []
    for d in search_dirs(workdir):
        for n in names:
            f = d / n
            if f.is_file():
                try:
                    found.append((f, f.read_text(encoding="utf-8", errors="replace").strip()))
                except OSError:
                    continue
    if not found:
        return "", []
    budget = max_chars
    kept: list[tuple[Path, str, int, bool]] = []
    for f, text in reversed(found):          # 아래(구체적인 것)부터 자리를 준다
        if budget <= 0:
            kept.append((f, "", len(text), True))
            continue
        cut = text if len(text) <= budget else text[:budget] + "\n…(상한 때문에 잘림)"
        kept.append((f, cut, len(text), len(text) > budget))
        budget -= len(text)
    kept.reverse()
    parts, log = [], []
    for f, cut, n, truncated in kept:
        log.append({"path": str(f), "chars": n, "used": len(cut), "truncated": truncated})
        if cut:
            parts.append(f"### {f}\n{cut}")
    text = ("아래는 작업 폴더의 프로젝트 규칙 파일이다. 과제와 부딪히지 않는 한 따른다.\n\n" + "\n\n".join(parts)) if parts else ""
    return text, log
