"""프로젝트 기억 파일(Claude Code 의 CLAUDE.md 에 해당): 작업 폴더의 AGENTS.md·STEWART.md 를 지시문에 넣는다.

- 찾는 범위: 작업 폴더에서 위로 올라가며 git 저장소 뿌리(.git 이 있는 폴더)까지만. 작업 폴더가 git 저장소 안이 아니면
  작업 폴더 자신만 본다(작업 폴더 밖 파일을 읽지 않는다).
- 순서: 위(뿌리) → 아래(작업 폴더). 더 구체적인 아래 폴더의 글이 뒤에 온다.
- 한 폴더 안에서는 프로필 memory_files 순서(기본 AGENTS.md, STEWART.md).
- 상한: 합쳐 max_chars 자(기본 12,000). 넘으면 뒤쪽(작업 폴더에 가까운 것)을 남기고 앞쪽 파일을 자른다.
- 어떤 파일을 몇 자 넣었는지는 run_start meta 의 memory_files 에 남긴다.
- 파일 내용은 모델에 지시문으로 들어간다(저장소에 든 글 = 프롬프트 주입 경로). 무인·자동 허락 사용은 격리 뒤다.
- 사용자 전역 <사용자 폴더>/STEWART.md(projcfg.user_dir, 곧 ~/.config/stewart/STEWART.md)를 맨 위에 둔다.
- @경로 가져오기(projcfg.expand_imports)를 모든 지시 파일에서 편다. 허용 폴더 밖·비밀 파일은 읽지 않는다.
- 호환(fl.compat): 같은 폴더에 AGENTS.md 가 없을 때만 CLAUDE.md 를 그 자리에 읽는다("@AGENTS.md" 한 줄이거나
  이미 읽은 AGENTS.md 와 같은 내용이면 건너뛴다). fl.user 면 ~/.claude/CLAUDE.md 를 사용자 전역 맨 앞에 더한다.
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


def collect(workdir: Path, names=DEFAULT_NAMES, max_chars: int = DEFAULT_MAX_CHARS, fl=None) -> tuple[str, list[dict]]:
    """(지시문에 붙일 글, 기록용 목록). 파일이 없으면 ("", [])."""
    from . import projcfg as PC
    fl = fl or PC.Flags()
    names = tuple(names)
    found: list[tuple[Path, str]] = []
    imports: dict[str, list[dict]] = {}
    skipped: list[dict] = []
    files: list[Path] = []
    if fl.user:
        files.append(PC.claude_home() / "CLAUDE.md")
    if "STEWART.md" in names:
        files.append(PC.user_dir() / "STEWART.md")
    for d in search_dirs(workdir):
        for n in names:
            if n == "AGENTS.md" and not (d / n).is_file() and fl.compat:
                files.append(d / "CLAUDE.md")   # 호환: AGENTS.md 가 없는 폴더에서만
            else:
                files.append(d / n)
    agents_texts: set[str] = set()
    for f in files:
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if f.name == "CLAUDE.md" and f.parent != PC.claude_home() \
                and (text in ("@AGENTS.md", "@./AGENTS.md") or text in agents_texts):
            skipped.append({"path": str(f), "skipped": "same_as_agents"})
            continue
        if f.name == "AGENTS.md":
            agents_texts.add(text)
        log: list[dict] = []
        text = PC.expand_imports(text, f, PC.import_roots(f, workdir), log)
        if log:
            imports[str(f)] = log
        found.append((f, text))
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
        log.append({"path": str(f), "chars": n, "used": len(cut), "truncated": truncated,
                    **({"imports": imports[str(f)]} if imports.get(str(f)) else {})})
        if cut:
            parts.append(f"### {f}\n{cut}")
    log += skipped
    text = ("아래는 작업 폴더의 프로젝트 규칙 파일이다. 과제와 부딪히지 않는 한 따른다.\n\n" + "\n\n".join(parts)) if parts else ""
    return text, log
