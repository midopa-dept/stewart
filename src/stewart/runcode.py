"""판 118: 대화 자리의 run_code — 계산·표·그래프용 파이썬을 울타리 안에서 돌린다(이 컴퓨터의 셸을 주지 않는다).

- 실행 칸: <대화 폴더>/.code/<시각-번호>/ (실행마다 새로). 쓰기는 이 칸만, 홈·/Volumes 읽기 막음(파이썬 설치 자리만 다시 엶),
  망 막음, 앱 열기(Launch Services·Apple Events) 막음, 자식 프로세스 막음(파이썬만 실행).
- macOS 는 sandbox-exec, 리눅스는 bwrap 이 있을 때만. 울타리를 못 만드는 곳에서는 도구를 싣지 않는다(available()).
- 상한: 시간 30초(프로필 run_code_timeout, 최대 120)·CPU 같은 값·출력 20,000자·만든 파일 합 50MB·50개.
  메모리는 RLIMIT_AS·RLIMIT_DATA 를 걸어 보되 macOS 는 받아 주지 않는 경우가 있다(그때는 시간 상한뿐 — README).
- 만든 파일은 "만든 파일" 카드로(env.turn_extra_files). 코드 글에 이 컴퓨터 화면 조작 꼴이 있으면 실행 전에 거절(hostgui).
- 파이썬: 프로필 code_python(그 꾸러미가 깔린 파이썬, 예: matplotlib) 또는 stewart 를 돌리는 파이썬(표준 라이브러리).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

NAME = "run_code"
SANDBOX = "/usr/bin/sandbox-exec"
OUT_MAX, HEAD = 20000, 2000
FILES_MAX_BYTES, FILES_MAX_N = 50 * 1024 * 1024, 50
KEEP_RUNS = 50
DEFINITION = {"type": "function", "function": {
    "name": NAME,
    "description": ("계산·표·그래프를 위한 파이썬 코드를 울타리 안에서 돌린다. 인터넷·사람의 파일·이 컴퓨터의 앱에는 닿지 못한다. "
                    "만든 파일(그림·CSV 등)은 지금 폴더(실행 칸)에 저장하면 사람에게 건네진다. 바깥 자료가 필요하면 먼저 web_fetch 로 "
                    "가져와 그 글을 코드에 넣는다. 출력이 길면 요약해 찍는다."),
    "parameters": {"type": "object", "properties": {
        "code": {"type": "string", "description": "파이썬 코드"},
        "files": {"type": "array", "items": {"type": "string"}, "description": "대화 폴더 안 파일 — 실행 칸에 복사해 읽게 한다"},
    }, "required": ["code"]},
}}
OFF_WHY = "이 환경에서는 울타리(sandbox-exec·bwrap)를 만들 수 없어 코드 실행이 꺼져 있다"


def code_gui_hit(code: str) -> str:
    """파이썬 코드 글에서 이 컴퓨터의 화면을 여는 꼴(webbrowser, 셸 부르기 안의 open·osascript 등). 울타리도 막지만 겹으로."""
    import re
    m = re.search(r"\bwebbrowser\b", code or "")
    if m:
        return "webbrowser"
    if re.search(r"\b(subprocess|os\.system|os\.popen|os\.exec\w*|os\.spawn\w*|pty)\b", code or ""):
        m = re.search(r"[\[('\"]\s*['\"]?(open|osascript|xdg-open|automator|screencapture|start)\b", code or "")
        if m:
            return m.group(1)
    return ""


def _kind() -> str | None:
    if sys.platform == "darwin" and os.path.exists(SANDBOX):
        return "sandbox-exec"
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        return "bwrap"
    return None


_PROBE: dict = {}
LAST_WHY = ""


def available(python: str | None = None, probe: bool = True) -> str | None:
    """울타리 종류(sandbox-exec | bwrap) 또는 None(도구를 싣지 않는다).
    판 120.2: 울타리 안에서 파이썬이 실제로 뜨는지 한 번 대조한다(인터프리터마다 한 번, 프로세스 동안 기억). 못 뜨면 None 과 까닭(off_why)."""
    global LAST_WHY
    kind = _kind()
    if kind is None:
        LAST_WHY = OFF_WHY
        return None
    if not probe:
        return kind
    py = interpreter(python)
    if (kind, py) not in _PROBE:
        import tempfile
        from types import SimpleNamespace
        ok, out = run(SimpleNamespace(workdir=tempfile.mkdtemp(prefix="rc-probe-")), "print('probe-ok')", kind=kind, python=py,
                      timeout_s=20)
        _PROBE[(kind, py)] = (ok and "probe-ok" in out, out.strip().splitlines()[-1][:160] if out.strip() else "")
    ok, why = _PROBE[(kind, py)]
    if not ok:
        LAST_WHY = f"이 환경에서는 울타리 안에서 파이썬을 띄우지 못해 코드 실행이 꺼져 있다({why})"
        return None
    return kind


def off_why() -> str:
    return LAST_WHY or OFF_WHY


def interpreter(python: str | None = None) -> str:
    """울타리 안에서 돌릴 파이썬. 설정 code_python 이 있으면 그것. 없으면 stewart 를 돌리는 파이썬의 바탕 인터프리터
    (venv 면 그 venv 를 만든 파이썬, 심볼릭 링크를 푼 실제 경로) — 프로젝트 .venv(의존성·프로젝트 코드 경로)를 울타리 안에 열지 않는다."""
    if python:
        return str(python)
    return os.path.realpath(getattr(sys, "_base_executable", None) or sys.executable)


def _q(p: str) -> str:
    return '"' + str(p).replace("\\", "\\\\").replace('"', '\\"') + '"'


def python_dirs(py: str) -> tuple[list[str], list[str]]:
    """(읽기를 열 자리, 실행을 열 파일). 파이썬 실행 파일·그 실제 경로와 prefix·base_prefix."""
    exe = [py, str(Path(py).resolve())]
    try:
        out = subprocess.run([py, "-I", "-c", "import sys;print(sys.prefix);print(sys.base_prefix)"], capture_output=True,
                             timeout=20).stdout.decode("utf-8", "replace").split()
    except (OSError, subprocess.SubprocessError):
        out = []
    dirs = sorted({str(Path(x).resolve()) for x in out} | {str(Path(e).resolve().parent.parent) for e in exe})
    return dirs, sorted(set(exe))


VARIANTS = ("full", "no-exec-rule", "no-read-rule", "bare")   # 점검용(tools/run-code-check.py) — 실행기는 늘 full


def sbpl(run_dir: Path, read_dirs: list[str], exe: list[str], home: str, variant: str = "full") -> str:
    """macOS sandbox-exec 규칙. 뒤 규칙이 앞 규칙을 이긴다.
    판 120.2: 실행 허용은 실행 파일 경로(literal)와 파이썬 자리(subpath) 둘 다로, 자식 프로세스는 process-fork 로도 막는다.
    variant 는 점검용: no-exec-rule(실행 제한 뺌)·no-read-rule(홈·볼륨 읽기 막기 뺌)·bare(쓰기·망만)."""
    rd = str(Path(run_dir).resolve())
    lines = ["(version 1)", "(allow default)",
             "; 쓰기: 실행 칸만", '(deny file-write* (subpath "/"))',
             f'(allow file-write* (subpath {_q(rd)}) (literal "/dev/null") (literal "/dev/tty"))', "; 망", "(deny network*)"]
    if variant == "bare":
        return "\n".join(lines) + "\n"
    if variant != "no-read-rule":
        lines += ["; 읽기: 홈·외장 볼륨 막고 실행 칸·파이썬 자리만 다시", f"(deny file-read* (subpath {_q(home)}) (subpath \"/Volumes\"))",
                  f"(allow file-read* (subpath {_q(rd)}) " + " ".join(f"(subpath {_q(d)})" for d in read_dirs) + ")"]
    lines += ["; 앱 열기", '(deny mach-lookup (global-name "com.apple.coreservices.launchservicesd") (global-name "com.apple.lsd.mapdb") '
              '(global-name "com.apple.coreservices.appleevents") (global-name "com.apple.SecurityServer") '
              '(global-name "com.apple.securityd"))', "(deny appleevent-send)",
              "; 자식 프로세스", "(deny process-fork)"]
    if variant != "no-exec-rule":
        lines += ["(deny process-exec*)",
                  "(allow process-exec* " + " ".join(f"(literal {_q(e)})" for e in exe) + " " + " ".join(f"(subpath {_q(d)})" for d in read_dirs) + ")"]
    return "\n".join(lines) + "\n"


def _limits(cpu: int):
    import resource

    def f():
        os.setsid()
        for lim, val in ((resource.RLIMIT_CPU, cpu), (resource.RLIMIT_FSIZE, FILES_MAX_BYTES)):
            try:
                resource.setrlimit(lim, (val, val))
            except (ValueError, OSError):
                pass
        for lim in (getattr(resource, "RLIMIT_AS", None), getattr(resource, "RLIMIT_DATA", None)):
            if lim is not None:
                try:
                    resource.setrlimit(lim, (2 * 1024 ** 3, 2 * 1024 ** 3))   # 2GB — macOS 가 받아 주지 않으면 그냥 넘어간다
                except (ValueError, OSError):
                    pass
    return f


def _dec(b) -> str:
    return b.decode("utf-8", "replace") if isinstance(b, bytes) else (b or "")


def _clip(s: str) -> str:
    if len(s) <= OUT_MAX:
        return s
    return s[:HEAD] + f"\n…(출력 {len(s)}자 중 가운데를 줄임)…\n" + s[-(OUT_MAX - HEAD):]


def _prune(base: Path) -> None:
    runs = sorted([p for p in base.iterdir() if p.is_dir()], key=lambda p: p.name)
    for p in runs[:-KEEP_RUNS]:
        shutil.rmtree(p, ignore_errors=True)


def run(env, code: str, files=None, kind: str | None = None, python: str | None = None, timeout_s: float = 30,
        _runner=subprocess.run, variant: str = "full") -> tuple[bool, str]:
    """울타리 안에서 code 를 돈다. (성공, 결과 글)."""
    from . import hostgui as HG
    kind = kind or available(python)
    if kind is None:
        return False, off_why()
    w = code_gui_hit(code)
    if w:
        return False, HG.refuse(w)
    wd = Path(env.workdir).resolve()
    base = wd / ".code"
    base.mkdir(parents=True, exist_ok=True)
    rd = base / f"{time.strftime('%Y%m%d-%H%M%S')}-{len(list(base.iterdir())) + 1}"
    rd.mkdir()
    copied = set()
    for f in files or []:
        src = (wd / str(f)).resolve()
        if wd not in src.parents or not src.is_file():
            return False, f"files 는 대화 폴더 안 파일만: {f}"
        shutil.copy2(src, rd / src.name)
        copied.add(src.name)
    (rd / "run.py").write_text(code, encoding="utf-8")
    before = {p.name for p in rd.iterdir()}
    py = interpreter(python)
    t = max(1, min(int(timeout_s or 30), 120))
    child_env = {"PATH": "/usr/bin:/bin", "HOME": str(rd), "TMPDIR": str(rd), "MPLBACKEND": "Agg", "PYTHONDONTWRITEBYTECODE": "1",
                 "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8"}
    if kind == "sandbox-exec":
        dirs, exe = python_dirs(py)
        sb = base / ".run.sb"
        sb.write_text(sbpl(rd, dirs, exe, os.path.expanduser("~"), variant), encoding="utf-8")
        cmd = [SANDBOX, "-f", str(sb), py, "-I", "-B", "run.py"]
    else:   # bwrap
        dirs, _ = python_dirs(py)
        cmd = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib",
               "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
        for d in dirs:
            cmd += ["--ro-bind", d, d]
        cmd += ["--bind", str(rd), str(rd), "--chdir", str(rd), py, "-I", "-B", "run.py"]
    try:
        p = _runner(cmd, cwd=str(rd), env=child_env, capture_output=True, timeout=t, stdin=subprocess.DEVNULL,
                    preexec_fn=_limits(t))
        rc, out = p.returncode, _dec(p.stdout) + _dec(p.stderr)
    except subprocess.TimeoutExpired as e:
        rc, out = None, _dec(e.stdout) + f"\n[시간 초과 {t}초]"
    made = [p for p in sorted(rd.iterdir()) if p.name not in before and p.name not in copied and p.is_file()]
    total = sum(p.stat().st_size for p in made)
    note = ""
    if len(made) > FILES_MAX_N or total > FILES_MAX_BYTES:
        for p in made:
            p.unlink(missing_ok=True)
        note, made = f"\n(만든 파일이 상한({FILES_MAX_N}개·50MB)을 넘어 지웠다)", []
    if made:
        extra = list(getattr(env, "turn_extra_files", None) or [])
        env.turn_extra_files = extra + [str(p) for p in made]
        note += "\n만든 파일: " + ", ".join(os.path.relpath(p, wd) for p in made)
    _prune(base)
    head = f"[exit {rc}]" if rc is not None else "[시간 초과]"
    return rc == 0, _clip(f"{head}\n{out}") + note
