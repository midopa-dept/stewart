"""환경 블록: 시스템 지시문에 OS·셸·작업 폴더·오늘 날짜·git 여부·파이썬 명령 이름을 넣는다.

- 세션(run 하나, chat 하나) 시작 때 한 번 재고 그 동안 바꾸지 않는다(시스템 지시문 앞부분 cache 유지).
  이어 하기(--continue·--resume)는 지시문을 새로 만들므로 다시 잰다.
- 파이썬 명령은 run_shell 의 셸이 실제로 쓰는 PATH(shell.tool_env — stewart 자신의 가상 환경 bin 을 뺀 것)로 찾는다.
  그래야 "있음"과 판이 셸에서 부를 때와 같다(판 122 — 전에는 이 프로세스의 PATH 라 `uv run` 으로 띄우면 셸에 없는 python 을 있다고 했다).
- 작업 폴더에 .venv 파이썬이 있으면(stewart 자신의 가상 환경이 아니면) 부르는 법 한 줄을 붙인다.
- 셸은 osdep.shell_spec(POSIX /bin/bash, Windows 동봉 busybox-w32 sh 또는 설정한 셸).
- Windows: 판(--version)을 내지 못하는 파이썬(스토어 안내 대역 WindowsApps 의 python3.exe 등)은 "없음"으로 친다.
- 넣지 않는 것: 환경 변수 값, 키·토큰, 작업 폴더 밖 파일 목록.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from datetime import date
from pathlib import Path

from . import osdep


def _first_line(argv: list[str], cwd: Path | None = None) -> str | None:
    try:
        p = subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True, encoding="utf-8", errors="replace",
                           timeout=5, stdin=subprocess.DEVNULL)
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


def _shell_path() -> str | None:
    """run_shell 이 쓰는 PATH(stewart 자신의 가상 환경 bin 을 뺀 것)."""
    from .shell import tool_env
    return tool_env().get("PATH")


def _python(name: str, win: bool | None = None, path_env: str | None = None) -> dict:
    win = osdep.IS_WIN if win is None else win
    path = shutil.which(name, path=path_env)
    if not path:
        return {"name": name, "path": None, "version": None}
    v = _first_line([path, "--version"])
    if win and not v:            # 스토어 안내 대역 등: 있지만 파이썬이 아니다
        return {"name": name, "path": None, "version": None}
    return {"name": name, "path": path, "version": (v or "").replace("Python ", "") or None}


def _shell_label(spec: "osdep.ShellSpec | None", err: str | None = None) -> str:
    """환경 블록의 셸 줄. POSIX bash 는 지금까지와 같은 꼴("/bin/bash (GNU bash, version …)")."""
    if spec is None:
        return f"없음({err})" if err else "없음"
    if spec.kind == "busybox":
        v = _first_line([spec.path])            # 첫 줄 "BusyBox v1.xx … multi-call binary."
        v = v.split(" multi-call")[0] if v else None
        return f"{spec.path} sh" + (f" ({v})" if v else "") + " — POSIX sh(ash): 배열 a=(…) 은 문법 오류, {1..3} 범위는 펼쳐지지 않고 글자 그대로(seq 를 써라)"
    v = _first_line([*spec.argv, "--version"])
    label = " ".join(spec.argv) + (f" ({v.split(' (')[0]})" if v else "")
    return label + (f" — 주의: {spec.warning}" if spec.warning else "")


def _project_venv(workdir: Path) -> str | None:
    """작업 폴더의 .venv 파이썬(작업 폴더 기준 상대 경로). 없거나 stewart 자신의 가상 환경이면 None."""
    from .shell import own_venv
    venv = Path(workdir) / ".venv"
    for rel in ("Scripts/python.exe", "bin/python") if osdep.IS_WIN else ("bin/python",):
        if (venv / rel).exists():
            own = own_venv()
            if own and os.path.realpath(venv) == own:
                return None
            return f".venv/{rel}"
    return None


def measure(workdir: Path, today: date | None = None) -> dict:
    """한 번 잰 값(meta 에도 남긴다)."""
    workdir = Path(workdir)
    try:
        spec, err = osdep.shell_spec(), None
    except osdep.ShellNotFound as e:
        spec, err = None, str(e)
    sp = _shell_path()
    git = None
    if shutil.which("git") and workdir.is_dir():
        git = _first_line(["git", "rev-parse", "--is-inside-work-tree"], workdir) == "true"
    return {
        "os": _os_name(),
        "shell": _shell_label(spec, err),
        "workdir": str(workdir),
        "date": (today or date.today()).isoformat(),
        "git": git,
        "python": [_python("python3", path_env=sp), _python("python", path_env=sp)],
        "project_venv": _project_venv(workdir),
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
            f"- 파이썬: {' · '.join(py(p) for p in pys)}{hint}"
            + (f"\n- 작업 폴더 .venv 파이썬: `{info['project_venv']}`(uv 프로젝트면 `uv run python`)"
               if info.get("project_venv") else ""))


def user_note(info: dict) -> str:
    """첫 사람 입력 앞에 붙일 글(다이어트 안 a). 과제가 아니라는 표시를 단다."""
    return "[엔진이 붙인 환경 정보 — 과제가 아니다]\n" + block(info)


def strip_user_note(text: str) -> str:
    """user_note 를 붙인 사람 입력에서 그 머리를 뗀 글(기록·시험용)."""
    if text.startswith("[엔진이 붙인 환경 정보"):
        return text.split("\n\n", 1)[1] if "\n\n" in text else ""
    return text
