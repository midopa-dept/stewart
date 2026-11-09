"""환경 블록: 시스템 지시문에 OS·셸·작업 폴더·오늘 날짜·git 여부·파이썬 명령 이름을 넣는다.

- 세션(run 하나, chat 하나) 시작 때 한 번 재고 그 동안 바꾸지 않는다(시스템 지시문 앞부분 cache 유지).
  이어 하기(--continue·--resume)는 지시문을 새로 만들므로 다시 잰다.
- 파이썬 명령은 run_shell 의 셸과 같은 PATH(이 프로세스의 PATH, bash --noprofile)로 찾는다.
- 넣지 않는 것: 환경 변수 값, 키·토큰, 작업 폴더 밖 파일 목록.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from datetime import date
from pathlib import Path

from .shell import BASH


def _first_line(argv: list[str], cwd: Path | None = None) -> str | None:
    try:
        p = subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=5,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    out = (p.stdout or p.stderr).strip().splitlines()
    return out[0].strip() if out else ""


def _os_name() -> str:
    sysname, rel, mach = platform.system(), platform.release(), platform.machine()
    if sysname == "Darwin":
        mac = platform.mac_ver()[0]
        return f"macOS {mac} (Darwin {rel}, {mach})" if mac else f"Darwin {rel} ({mach})"
    return f"{sysname} {rel} ({mach})"


def _python(name: str) -> dict:
    path = shutil.which(name)
    if not path:
        return {"name": name, "path": None, "version": None}
    v = _first_line([path, "--version"])
    return {"name": name, "path": path, "version": (v or "").replace("Python ", "") or None}


def measure(workdir: Path, today: date | None = None) -> dict:
    """한 번 잰 값(meta 에도 남긴다)."""
    workdir = Path(workdir)
    bash_v = _first_line([BASH, "--version"])
    git = None
    if shutil.which("git") and workdir.is_dir():
        git = _first_line(["git", "rev-parse", "--is-inside-work-tree"], workdir) == "true"
    return {
        "os": _os_name(),
        "shell": BASH + (f" ({bash_v.split(' (')[0]})" if bash_v else ""),
        "workdir": str(workdir),
        "date": (today or date.today()).isoformat(),
        "git": git,
        "python": [_python("python3"), _python("python")],
    }


def block(info: dict) -> str:
    def py(p):
        return f"{p['name']} 있음({p['version'] or '버전 모름'})" if p["path"] else f"{p['name']} 없음"
    pys = info["python"]
    have = [p["name"] for p in pys if p["path"]]
    hint = (f" → 파이썬은 `{have[0]}` 로 부른다(예: `{have[0]} -m unittest`)." if have and len(have) < len(pys)
            else " → 파이썬 명령이 없다." if not have else "")
    git = {True: "예", False: "아니오", None: "모름"}[info["git"]]
    return ("환경(세션 시작 때 잰 값):\n"
            f"- OS: {info['os']}\n"
            f"- 셸: run_shell 은 {info['shell']} 에서 돈다\n"
            f"- 작업 폴더: {info['workdir']}\n"
            f"- git 저장소 안: {git}\n"
            f"- 오늘 날짜: {info['date']}\n"
            f"- 파이썬: {' · '.join(py(p) for p in pys)}{hint}")


def user_note(info: dict) -> str:
    """첫 사람 입력 앞에 붙일 글(다이어트 안 a). 과제가 아니라는 표시를 단다."""
    return "[엔진이 붙인 환경 정보 — 과제가 아니다]\n" + block(info)


def strip_user_note(text: str) -> str:
    """user_note 를 붙인 사람 입력에서 그 머리를 뗀 글(기록·시험용)."""
    if text.startswith("[엔진이 붙인 환경 정보"):
        return text.split("\n\n", 1)[1] if "\n\n" in text else ""
    return text
