"""플랫폼 층: 셸 찾기, 프로세스 묶음 띄우기·끝내기, 파이프 읽기, 확인 명령을 돌릴 셸.

stewart 는 한 벌이다. macOS·Linux 와 Windows 의 차이는 이 파일(과 shell.spawn 이 넘기는 인자) 안에만 둔다.

셸 찾기(shell_spec) 차례
1. 환경 변수 STEWART_SHELL = 셸 실행 파일 경로
2. 사용자 설정 <사용자 폴더>/config.toml 의 [shell] path(·args)  (사용자 폴더 = STEWART_USER_DIR 또는 ~/.config/stewart)
3. POSIX: /bin/bash (지금까지와 같다)
4. Windows: stewart 폴더의 bin/busybox64u.exe · bin/busybox64.exe · bin/busybox.exe 차례
   (Git bash 는 자동으로 고르지 않는다 — 경로 꼴이 /c/… 라 경계 판정 층이 아직 맞추지 못한다. 설정으로 고르면 쓰되 경고를 단다)
- 이름이 busybox* 이면 "sh" 애플릿으로 띄운다(args 를 적으면 그것).

프로세스 묶음
- POSIX: 새 세션(start_new_session)으로 띄우고 os.killpg 로 묶음째 끝낸다(지금까지와 같다).
- Windows: CREATE_NEW_PROCESS_GROUP 로 띄워 콘솔 Ctrl-C 가 모델의 셸에 가지 않게 하고, Job Object(KILL_ON_JOB_CLOSE)에 넣어
  끝낼 때 Job 째 끝낸다(자식·손자 포함). Job 에 넣지 못했으면 taskkill /T /F 로, 그것도 안 되면 그 프로세스만 끝낸다.
  Job 에 넣기 전에 셸이 자식을 띄우는 틈이 없게 CREATE_SUSPENDED 로 띄우고, Job 에 넣은 뒤 NtResumeProcess 로 푼다(WIN-3).

확인 명령(hooks·verify_command·check_on_stop·bench --setup)
- POSIX: 지금처럼 shell=True(/bin/sh).
- Windows: 찾은 셸의 `-c` 로 돌린다(cmd.exe 로 돌지 않게).
"""

from __future__ import annotations

import ctypes
import functools
import os
import re
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

IS_WIN = os.name == "nt"
ENV_SHELL = "STEWART_SHELL"
ENV_HOME = "STEWART_HOME"      # Windows 런처가 주는 설치 뿌리(current.txt·versions·site·bin)
POSIX_BASH = "/bin/bash"
WIN_BUNDLED = ("busybox64u.exe", "busybox64.exe", "busybox.exe")
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_SUSPENDED = 0x00000004


class ShellNotFound(RuntimeError):
    pass


@dataclass(frozen=True)
class ShellSpec:
    argv: tuple[str, ...]            # 셸을 띄우는 앞부분(busybox 면 (경로, "sh"))
    kind: str                        # bash | busybox | msys | sh
    source: str                      # env | config | default | bundled
    login_args: tuple[str, ...] = ()  # 오래 사는 셸에만 붙이는 인자(bash: --noprofile --norc)
    warning: str | None = None

    @property
    def path(self) -> str:
        return self.argv[0]

    def run_argv(self, command: str) -> list[str]:
        """`셸 -c 명령`"""
        return [*self.argv, *self.login_args, "-c", command]


def _user_config(env) -> dict:
    """<사용자 폴더>/config.toml 의 [shell] 표. 없거나 못 읽으면 {}."""
    import tomllib
    v = env.get("STEWART_USER_DIR")
    base = Path(os.path.expanduser(v)) if v else Path(os.path.expanduser(env.get("XDG_CONFIG_HOME") or "~/.config")) / "stewart"
    f = base / "config.toml"
    try:
        data = tomllib.loads(f.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise ShellNotFound(f"셸 설정을 읽지 못했다: {f}: {e}") from None
    sh = data.get("shell") or {}
    if not isinstance(sh, dict):
        raise ShellNotFound(f"{f}: [shell] 은 표여야 한다")
    sh["_file"] = str(f)
    return sh


def _spec_for(path: str, args, source: str, win: bool) -> ShellSpec:
    p = Path(path)
    if not p.is_file():
        raise ShellNotFound(f"셸 실행 파일이 없다: {path} ({source})")
    name = p.name.lower()
    if args is not None:
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ShellNotFound(f"[shell] args 는 글 목록이어야 한다 ({source})")
    if name.startswith("busybox"):
        return ShellSpec((str(p), *(args if args is not None else ["sh"])), "busybox", source)
    if name in ("bash", "bash.exe"):
        msys = win and ((p.parent / "msys-2.0.dll").exists() or (p.parent.parent / "usr" / "bin" / "msys-2.0.dll").exists())
        warn = ("Git bash(msys) 는 경로를 /c/… 꼴로 다뤄 작업 폴더 경계 판정이 아직 맞지 않는다(WIN-6 전). "
                "busybox-w32 를 권한다") if msys else None
        return ShellSpec((str(p), *(args or [])), "msys" if msys else "bash", source, ("--noprofile", "--norc"), warn)
    return ShellSpec((str(p), *(args or [])), "sh", source)


def shell_spec(env=None, win: bool | None = None, repo_root: Path | None = None) -> ShellSpec:
    """run_shell 이 쓸 셸. 못 찾으면 ShellNotFound(무엇을 어디에 두면 되는지 적어서)."""
    env = os.environ if env is None else env
    win = IS_WIN if win is None else win
    v = env.get(ENV_SHELL)
    if v:
        return _spec_for(v, None, f"환경 변수 {ENV_SHELL}", win)
    conf = _user_config(env)
    if conf.get("path"):
        if not isinstance(conf["path"], str):
            raise ShellNotFound(f"{conf['_file']}: [shell] path 는 글이어야 한다")
        return _spec_for(os.path.expanduser(conf["path"]), conf.get("args"), conf["_file"], win)
    if not win:
        return ShellSpec((POSIX_BASH,), "bash", "default", ("--noprofile", "--norc"))
    if repo_root is None:
        from .config import REPO_ROOT as repo_root
    # 동봉 자리: stewart 폴더의 bin, 그다음 런처가 준 설치 뿌리(STEWART_HOME\bin — versions\<판>\ 은 텍스트만 바뀌고
    # 바이너리는 뿌리에 한 번 둔다 — 런처가 STEWART_HOME 을 준다)
    dirs = [Path(repo_root) / "bin"] + ([Path(env[ENV_HOME]) / "bin"] if env.get(ENV_HOME) else [])
    for d in dirs:
        for n in WIN_BUNDLED:
            f = d / n
            if f.is_file():
                return _spec_for(str(f), None, "bundled", win)
    raise ShellNotFound("run_shell 에 쓸 셸이 없다. stewart 폴더(또는 설치 뿌리)의 bin\\busybox64u.exe(busybox-w32)를 두거나, "
                        f"환경 변수 {ENV_SHELL} 또는 사용자 폴더 config.toml 의 [shell] path 로 셸 실행 파일을 적는다.")


def sh_path(p) -> str:
    """셸 명령 글에 넣을 경로. Windows 는 백슬래시를 슬래시로(C:/…) 바꾼다(sh 에서 백슬래시는 이스케이프)."""
    s = str(p)
    return s.replace("\\", "/") if IS_WIN else s


# ── 확인 명령(hooks·verify·check_on_stop·bench setup) ──

def command_args(command: str, win: bool | None = None, spec: ShellSpec | None = None) -> dict:
    """subprocess.run 에 넘길 {args, shell}. POSIX 는 지금처럼 /bin/sh(shell=True), Windows 는 찾은 셸의 -c."""
    win = IS_WIN if win is None else win
    if not win:
        return {"args": command, "shell": True}
    spec = spec or shell_spec(win=True)
    return {"args": [*spec.argv, "-c", command], "shell": False}


def run_command(command: str, **kw) -> subprocess.CompletedProcess:
    """확인 명령 한 번. 글은 utf-8 로 주고받는다(한국어 Windows 의 cp949 로 풀리지 않게)."""
    return subprocess.run(**command_args(command), encoding="utf-8", errors="replace", **kw)


# ── 프로세스 묶음 ──

def group_popen_kwargs() -> dict:
    """새 프로세스 묶음으로 띄우는 Popen 인자. Windows 는 멈춘 채로 띄우므로 반드시 attach_group 을 불러야 돈다."""
    if IS_WIN:
        return {"creationflags": CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED}
    return {"start_new_session": True}


def attach_group(p: subprocess.Popen) -> None:
    """Windows: 멈춘 채 띄운 프로세스를 새 Job Object 에 넣고 푼다(Job 에 못 넣으면 그냥 풀고 끝낼 때 taskkill).
    풀지 못하면 그 프로세스를 끝내고 OSError. POSIX: 할 일 없음."""
    if not IS_WIN:
        return
    try:
        p._stewart_job = _win_job_for(p._handle)          # type: ignore[attr-defined]
    except OSError:
        p._stewart_job = None                              # type: ignore[attr-defined]
    if not _win_resume(p._handle):                          # type: ignore[attr-defined]
        try:
            p.kill()
        except OSError:
            pass
        raise OSError("멈춘 채 띄운 셸을 풀지 못했다(NtResumeProcess)")


def kill_group(p: subprocess.Popen, hard: bool = True) -> None:
    """그 프로세스와 자식들을 끝낸다. hard=False 는 POSIX 에서 SIGTERM(Windows 는 늘 강제 종료)."""
    if not IS_WIN:
        try:
            os.killpg(p.pid, signal.SIGKILL if hard else signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        return
    job = getattr(p, "_stewart_job", None)
    if job and _win_terminate_job(job):
        return
    if p.poll() is not None:
        return
    if not _win_taskkill(p.pid):
        try:
            p.kill()
        except OSError:
            pass


def kill_pid_group(pid: int, hard: bool = True) -> None:
    """Popen 객체 없이 pid 만 알 때(허브가 등록부의 pid 로 끝낼 때)."""
    if not IS_WIN:
        try:
            os.killpg(pid, signal.SIGKILL if hard else signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        return
    _win_taskkill(pid)


def _win_taskkill(pid: int) -> bool:
    exe = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "taskkill.exe")
    try:
        r = subprocess.run([exe, "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


# Windows Job Object(ctypes). 구조체는 어느 플랫폼에서나 정의한다(크기 시험용).
class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                  "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32), ("SchedulingClass", ctypes.c_uint32)]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


_JobObjectExtendedLimitInformation = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_k32 = None


def _kernel32():
    global _k32
    if _k32 is None:
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)   # type: ignore[attr-defined]
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.SetInformationJobObject.restype = wintypes.BOOL
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.AssignProcessToJobObject.restype = wintypes.BOOL
        k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateJobObject.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        _k32 = k
    return _k32


class _Job:
    """Job 핸들. 파이썬 객체가 사라지면 핸들을 닫는다(KILL_ON_JOB_CLOSE 라 남은 프로세스도 끝난다)."""

    def __init__(self, h):
        self.h = h

    def close(self):
        if self.h:
            _kernel32().CloseHandle(self.h)
            self.h = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def _win_job_for(proc_handle) -> _Job:
    k = _kernel32()
    h = k.CreateJobObjectW(None, None)
    if not h:
        raise ctypes.WinError(ctypes.get_last_error())     # type: ignore[attr-defined]
    job = _Job(h)
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k.SetInformationJobObject(h, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)):
        job.close()
        raise ctypes.WinError(ctypes.get_last_error())     # type: ignore[attr-defined]
    if not k.AssignProcessToJobObject(h, int(proc_handle)):
        job.close()
        raise ctypes.WinError(ctypes.get_last_error())     # type: ignore[attr-defined]
    return job


def _win_resume(proc_handle) -> bool:
    """멈춘 채 띄운 프로세스를 푼다. ntdll NtResumeProcess(문서화되지 않았지만 오래 쓰인 함수) — 0 이면 성공."""
    try:
        nt = ctypes.WinDLL("ntdll")                         # type: ignore[attr-defined]
        nt.NtResumeProcess.argtypes = [ctypes.c_void_p]
        nt.NtResumeProcess.restype = ctypes.c_long
        return nt.NtResumeProcess(ctypes.c_void_p(int(proc_handle))) == 0
    except (OSError, AttributeError):
        return False


def _win_terminate_job(job: _Job) -> bool:
    if not job.h:
        return False
    ok = bool(_kernel32().TerminateJobObject(job.h, 1))
    return ok


# ── 파이프 읽기(양쪽 공용) ──

class PipeReader:
    """파이프를 스레드에서 읽어 큐에 담는다. Windows 의 select 는 파이프를 받지 않아서 두 플랫폼 모두 이 방식을 쓴다.

    read(timeout) → 조각(bytes) | None(그 시간 안에 온 것 없음) | b""(끝). 끝에 닿으면 읽던 파일은 이 스레드가 닫는다
    (읽는 중인 fd 를 다른 쪽이 닫으면 그 번호가 다른 파이프에 다시 쓰여 엉뚱한 것을 읽을 수 있어서)."""

    def __init__(self, f):
        self.f = f
        self.fd = f.fileno()
        self.q: queue.Queue = queue.Queue()
        self.eof = False
        self.t = threading.Thread(target=self._loop, name="stewart-pipe", daemon=True)
        self.t.start()

    def _loop(self) -> None:
        while True:
            try:
                b = os.read(self.fd, 65536)
            except OSError:
                b = b""
            self.q.put(b)
            if not b:
                try:
                    self.f.close()
                except OSError:
                    pass
                return

    def read(self, timeout: float) -> bytes | None:
        if self.eof:
            return b""
        try:
            b = self.q.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None
        if not b:
            self.eof = True
        return b



# ── 허브(WIN-5): 살아 있는지·승인 통로·바꿔 쓰기 ──

_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def pid_alive(pid: int | None) -> bool:
    """pid 가 살아 있는지. POSIX 는 os.kill(pid, 0)(+ 거두지 않은 자식은 죽은 것). Windows 는 OpenProcess·GetExitCodeProcess.
    Windows 에서 os.kill(pid, 0) 은 0 = CTRL_C_EVENT 라 묻는 대신 Ctrl-C 를 보낸다 — 절대 쓰지 않는다."""
    if not pid:
        return False
    if IS_WIN:
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)   # type: ignore[attr-defined]
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k.GetExitCodeProcess.restype = wintypes.BOOL
        h = k.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return ctypes.get_last_error() == 5          # ERROR_ACCESS_DENIED: 있지만 남의 것   # type: ignore[attr-defined]
        try:
            code = wintypes.DWORD()
            return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == _STILL_ACTIVE
        finally:
            _kernel32().CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:   # 끝났지만 거두지 않은 자식(좀비)은 죽은 것으로 본다
        wpid, _ = os.waitpid(pid, os.WNOHANG)
        return wpid == 0
    except ChildProcessError:
        return True


def child_pipe() -> tuple[int, int, list[str], dict]:
    """자식 하나에게만 물려줄 파이프: (읽는 fd, 쓰는 fd, 자식 argv 에 붙일 인자, Popen 인자).
    POSIX: pass_fds. Windows: 읽는 끝의 핸들만 상속 가능으로 두고 STARTUPINFO handle_list 로 그 자식에게만 준다
    (Popen 은 pass_fds 를 받지 않는다). 부모는 Popen 뒤에 읽는 fd 를 닫는다."""
    r, w = os.pipe()
    os.set_inheritable(w, False)
    if not IS_WIN:
        return r, w, ["--approval-fd", str(r)], {"pass_fds": (r,)}
    import msvcrt
    h = msvcrt.get_osfhandle(r)
    os.set_handle_inheritable(h, True)
    si = subprocess.STARTUPINFO()                           # type: ignore[attr-defined]
    si.lpAttributeList = {"handle_list": [h]}
    return r, w, ["--approval-handle", str(h)], {"startupinfo": si, "close_fds": True}


def open_child_pipe(fd: int | None = None, handle: int | None = None) -> int | None:
    """자식 쪽: 받은 통로를 fd 로 열고 곧바로 상속 불가로 둔다(셸 도구·확인 명령의 자식에게 물려주지 않는다)."""
    if handle is not None and IS_WIN:
        import msvcrt
        os.set_handle_inheritable(handle, False)
        return msvcrt.open_osfhandle(handle, os.O_RDONLY)
    if fd is not None:
        os.set_inheritable(fd, False)
        return fd
    return None


def replace_retry(src, dst, tries: int = 40, wait: float = 0.025) -> None:
    """os.replace. Windows 는 다른 쪽이 대상 파일을 열고 있으면 PermissionError(WinError 5·32)라 잠깐씩 다시 한다
    (연기 시험 14번). 다 실패하면 마지막 오류를 그대로 던진다. POSIX 는 한 번."""
    for i in range(tries if IS_WIN else 1):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == (tries if IS_WIN else 1) - 1:
                raise
            time.sleep(wait * (1 + i // 8))


# ── 콘솔(WIN-4) ──

_ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
_CTRL_HANDLERS: list = []      # ctypes 콜백이 거둬지지 않게 붙들어 둔다


def win_console_vt() -> str | None:
    """Windows 표준 출력 콘솔이 VT(ANSI) 처리를 켤 수 있으면 None, 아니면 이유. 켤 수 있는지만 보고 원래 모드로 둔다.
    (TUI 를 쓸지 고를 때. Windows 10 1607 이후 conhost·Windows Terminal 은 된다.)"""
    if not IS_WIN:
        return None
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)      # type: ignore[attr-defined]
    k.GetStdHandle.argtypes = [wintypes.DWORD]
    k.GetStdHandle.restype = wintypes.HANDLE
    k.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    h = k.GetStdHandle(wintypes.DWORD(-11 & 0xFFFFFFFF))   # STD_OUTPUT_HANDLE
    mode = wintypes.DWORD()
    if not h or not k.GetConsoleMode(h, ctypes.byref(mode)):
        return "콘솔이 아님(GetConsoleMode 실패)"
    if mode.value & _ENABLE_VIRTUAL_TERMINAL_PROCESSING:
        return None
    if not k.SetConsoleMode(h, mode.value | _ENABLE_VIRTUAL_TERMINAL_PROCESSING):
        return "콘솔이 VT 처리를 켜지 못함"
    k.SetConsoleMode(h, mode.value)
    return None


def win_on_ctrl_c(fn) -> bool:
    """Windows 콘솔 Ctrl-C·Ctrl-Break 때 fn() 을 (그 신호용 스레드에서) 먼저 부른다. 파이썬의 SIGINT 처리는 그대로 이어진다
    (False 를 돌려 다음 처리기로 넘긴다). 붙였으면 True."""
    if not IS_WIN:
        return False
    from ctypes import wintypes
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)  # type: ignore[attr-defined]

    def handler(ev):
        if ev in (0, 1):            # CTRL_C_EVENT · CTRL_BREAK_EVENT
            try:
                fn()
            except Exception:
                pass
        return False

    cb = proto(handler)
    k = ctypes.WinDLL("kernel32", use_last_error=True)      # type: ignore[attr-defined]
    k.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, wintypes.BOOL]
    k.SetConsoleCtrlHandler.restype = wintypes.BOOL
    if not k.SetConsoleCtrlHandler(ctypes.cast(cb, ctypes.c_void_p), True):
        return False
    _CTRL_HANDLERS.append(cb)
    return True


# ── 경로(Windows) — WIN-3 ──
# 판정 층(bounds·readcmds·allowrules·config_guard·is_secret)이 셸 인자·도구 경로를 실제 자리로 풀기 전에 쓴다.
# 헷갈리면 "ask"(묻는 쪽)로 둔다. POSIX 에서는 부르지 않는다(지금 동작 그대로).
#
# 전제(실제 Windows 연기 시험으로 확인, 다르면 여기와 표시한 곳을 고친다)
# - WIN-PWD: busybox-w32 sh 의 $PWD 는 C:/… 꼴이다(시험 3번). 아니면 shell.Shell 이 위치를 "모름"으로 둔다.
# - WIN-ROOT: 드라이브 없는 /x 는 지금 드라이브의 뿌리(C:/x)다 — busybox-w32 README, 파이썬 ntpath.join 도 같다.
# - WIN-DEV: busybox-w32 가 흉내 내는 장치는 /dev/null·/dev/zero·/dev/tty·/dev/urandom 이다(README). 그 밖 /dev/… 는 묻는다.
# - WIN-HOME: ~ 는 파이썬 expanduser(USERPROFILE)로 푼다. busybox 의 $HOME 과 같은지는 시험 3번에서 본다.

WIN_RESERVED = frozenset({"con", "prn", "aux", "nul", "conin$", "conout$"}
                         | {f"com{c}" for c in "123456789¹²³"} | {f"lpt{c}" for c in "123456789¹²³"})
WIN_SH_DEVICES = frozenset({"/dev/null", "/dev/zero", "/dev/tty", "/dev/urandom"})
_DRIVE = re.compile(r"^([A-Za-z]):(.*)$", re.S)


def win_path_kind(token: str) -> str:
    """셸 인자·도구 경로 글을 Windows 뜻으로 가른다.
    'plain' = 보통 경로(realpath 로 풀어 안·밖을 본다) · 'device' = 버리는 장치(/dev/null·NUL 등, 늘 괜찮음) · 'ask' = 판정하지 않고 묻는다.
    ask: UNC·장치 경로(//srv·\\\\?\\·\\\\.\\), 드라이브 상대(C:x·C:), 대체 스트림(a:s), 끝이 점·공백인 이름(Windows 가 떼어 다른 파일이 된다),
    NUL 말고 예약 이름(CON·PRN·AUX·COMn·LPTn, 확장자가 붙어도), 흉내 내지 않는 /dev/…"""
    t = str(token).replace("\\", "/")
    if not t:
        return "ask"
    if t.lower() in WIN_SH_DEVICES:
        return "device"
    if t.lower().startswith("/dev/"):
        return "ask"
    if t.startswith("//"):
        return "ask"
    m = _DRIVE.match(t)
    if m:
        rest = m.group(2)
        if not rest.startswith("/"):
            return "ask"
        t = rest
    if ":" in t:
        return "ask"
    comps = [c for c in t.split("/") if c not in ("", ".", "..")]
    for i, c in enumerate(comps):
        if c != c.rstrip(" ."):
            return "ask"
        base = c.split(".", 1)[0].rstrip(" ").lower()
        if base in WIN_RESERVED:
            if base == "nul" and c.lower() == "nul" and i == len(comps) - 1:
                return "device"
            return "ask"
    return "plain"


def win_norm_name(name: str) -> str:
    """파일 이름 하나를 Windows 가 실제로 여는 이름으로: 대체 스트림(:…)을 떼고 끝의 점·공백을 뗀다(.env:: $DATA·'.env ' → .env)."""
    n = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    n = n.split(":", 1)[0]
    s = n.rstrip(" .")
    return s if s else n


def win_strip_components(path: str) -> str:
    """경로의 각 조각 끝 점·공백을 뗀다(드라이브·뿌리는 그대로). config_guard 가 realpath 전에 쓴다."""
    p = str(path).replace("\\", "/")
    m = _DRIVE.match(p)
    drive, rest = (m.group(1) + ":", m.group(2)) if m else ("", p)
    parts = [c if c in ("", ".", "..") else (c.rstrip(" .") or c) for c in rest.split("/")]
    return drive + "/".join(parts)


@functools.lru_cache(maxsize=8)
def _applets_cached(path: str, mtime: float, size: int) -> frozenset:
    try:
        r = subprocess.run([path, "--list"], capture_output=True, encoding="utf-8", errors="replace", timeout=10,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    return frozenset(r.stdout.split()) if r.returncode == 0 else frozenset()


def busybox_applets(path: str) -> frozenset:
    """busybox --list 의 애플릿 이름들(못 읽으면 빈 집합 = 읽기 기본 통과 없음)."""
    try:
        st = os.stat(path)
    except OSError:
        return frozenset()
    return _applets_cached(path, st.st_mtime, st.st_size)


SH_BUILTINS = frozenset({"echo", "printf", "pwd", "true"})   # ash 내장(외부 프로그램을 찾지 않는다)
WIN_NOT_READ = frozenset({"tree"})   # busybox 에 없고 System32\tree.com 이 잡힌다


def win_read_cmd_ok(name: str, env=None, spec: "ShellSpec | None" = None) -> bool:
    """Windows 에서 읽기 기본 통과 목록의 이름이 동봉 셸 안에서 돌 때만 참.
    busybox-w32 는 같은 이름의 외부 프로그램보다 애플릿을 먼저 쓴다(README). BB_OVERRIDE_APPLETS 에 적힌 이름은 외부 것이 돌므로 뺀다.
    한계: 모델 셸 안에서 export 로 BB_OVERRIDE_APPLETS 를 바꾸면 여기서 보지 못한다(그 export 자체는 대입이라 묻는다)."""
    if name in WIN_NOT_READ:
        return False
    if name in SH_BUILTINS:
        return True
    env = os.environ if env is None else env
    try:
        spec = spec or shell_spec(env)
    except ShellNotFound:
        return False
    if spec.kind != "busybox":
        return False
    if name in (env.get("BB_OVERRIDE_APPLETS") or "").split():
        return False
    return name in busybox_applets(spec.path)
