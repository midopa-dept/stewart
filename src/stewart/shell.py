"""셸: 실행(세션)마다 오래 사는 셸 하나(상태 유지) + 백그라운드 작업.

- 어떤 셸인지는 osdep.shell_spec 이 정한다(POSIX 는 /bin/bash, Windows 는 동봉한 busybox-w32 의 sh, 설정으로 바꿈).
  프로세스 묶음·파이프 읽기의 플랫폼 차이도 osdep 에 있다.

- 셸을 띄우는 곳은 spawn() 하나다. 나중에 셸 격리가 이 함수만 감싸면 된다(지금은 없음).
- 상태 유지: 명령을 `{ 명령\n} < /dev/null` 로 감싸 그 셸 안에서 돌린다 → cd·export·변수가 다음 호출에 남는다.
  끝은 무작위 경계 표지(명령마다 번호) 한 줄로 알고, 그 줄에 종료 코드와 PWD 를 싣는다. 끝날 때마다 export -p 를 상태 파일에 남긴다.
- 시간 초과: 그 셸의 프로세스 묶음(그 명령과 자식들)을 SIGKILL 하고, 직전 상태(PWD·export 한 변수)로 새 셸을 띄운다.
  (샌드박스에서 프로세스 목록을 못 봐 명령의 자식만 골라 죽일 수 없다. 내보내지 않은 변수·함수·alias 는 새 셸에서 사라진다.)
- 명령이 exit 하면 셸이 끝난다 → 같은 방식으로 새 셸을 띄운다.
- 문법 오류(닫히지 않은 따옴표 등)는 보내기 전에 bash -n 으로 걸러 셸이 다음 입력을 삼키지 않게 한다.
- 백그라운드: 따로 띄운 bash(spawn)로 돌리고 출력은 기록 파일로. 동시 상한 BG_MAX. 실행 끝에 close() 가 모두 정리한다.
"""

from __future__ import annotations

import atexit
import os
import re
import secrets
import shlex
import subprocess
import tempfile
import time
import weakref
from pathlib import Path

from . import osdep

UNKNOWN_CWD = "//?/stewart-unknown-cwd"   # Windows 에서 셸 위치를 알 수 없을 때(어느 작업 폴더 안도 아니다 → 묻는다)
BASH = osdep.POSIX_BASH     # 옛 이름(POSIX 기본 셸). 실제로 쓰는 셸은 osdep.shell_spec()


def own_venv() -> str | None:
    """stewart 자신이 도는 가상 환경(uv run·venv 로 띄웠을 때). 시스템 파이썬이면 None."""
    import sys
    return os.path.realpath(sys.prefix) if sys.prefix != getattr(sys, "base_prefix", sys.prefix) else None


def tool_env(base: dict | None = None) -> dict:
    """모델의 셸·확인 명령에 넘길 환경: stewart 자신의 가상 환경과 stewart 설정이 새지 않게 뺀다.
    - PATH 에서 stewart 가상 환경의 bin 을 뺀다. VIRTUAL_ENV 가 그 가상 환경이면 지운다(사용자가 따로 켠 다른 venv 는 그대로).
    - uv run 이 넣는 UV_RUN_RECURSION_DEPTH, stewart 자신의 설정 변수(STEWART_*: 백엔드 파일·키 이름 등)를 뺀다.
    - 값에 stewart 가상 환경이나 stewart 설치 폴더 경로가 든 변수도 뺀다(모델이 셸에서 stewart 위치를 알지 못하게)."""
    env = dict(os.environ if base is None else base)
    own = own_venv()
    pkg_root = os.path.realpath(str(Path(__file__).resolve().parents[2]))
    hide = [x for x in (own, pkg_root) if x]
    if own:
        bins = {os.path.join(own, "bin"), os.path.join(own, "Scripts")}
        parts = [p for p in env.get("PATH", "").split(os.pathsep) if p and os.path.realpath(p) not in bins]
        env["PATH"] = os.pathsep.join(parts)
        ve = env.get("VIRTUAL_ENV")
        if ve and os.path.realpath(ve) == own:
            env.pop("VIRTUAL_ENV", None)
            env.pop("VIRTUAL_ENV_PROMPT", None)
    env.pop("UV_RUN_RECURSION_DEPTH", None)
    for k in list(env):
        if k == "PATH":
            continue
        if k.startswith("STEWART_") or any(h and h in env[k] for h in hide):
            env.pop(k)
    if osdep.IS_WIN and not env.get("HOME"):
        # WIN-HOME(VM 11-10): busybox-w32 는 HOME 을 채우지 않아 ~ 가 비었다. 경계 판정(파이썬 expanduser = USERPROFILE)과
        # 같은 자리를 가리키게 C:/… 꼴로 넣는다.
        env["HOME"] = osdep.sh_path(os.path.expanduser("~"))
    return env


HEREDOC_HINT = ("\n→ heredoc 끝 표지 줄이 없다(또는 들여쓰기·공백이 붙었다). 끝 표지는 줄 맨 앞에 그 낱말만 둔다. "
                "긴 코드는 명령에 넣지 말고 write_file 로 파일을 쓴 뒤 실행하라.")
INTERPRETERS = ("python3", "python", "node", "ruby", "perl", "bash", "sh")


_HEREDOC = re.compile(r"(?<![<\\])<<(-?)[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")


def open_heredoc(command: str) -> str | None:
    """끝 표지 줄이 뒤에 없는 heredoc 의 표지 낱말(없으면 None). 따옴표 안의 << 는 대충 거른다(줄에서 앞 따옴표 수가 홀수면 건너뜀)."""
    lines = command.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        pending = []
        for m in _HEREDOC.finditer(line):
            before = line[:m.start()]
            if before.count("'") % 2 or before.count('"') % 2 or "<<<" in line[max(0, m.start() - 1):m.end()]:
                continue
            pending.append((m.group(1) == "-", m.group(3)))
        i += 1
        for dash, word in pending:   # 한 줄에 여럿이면 차례로 본문이 이어진다
            while i < len(lines) and (lines[i].lstrip("\t") if dash else lines[i]) != word:
                i += 1
            if i >= len(lines):
                return word
            i += 1
    return None


def interp_hint(command: str) -> str:
    """`python3` 한 줄 뒤에 코드를 바로 붙인 꼴(대화형 입력을 기대함)이면 고칠 꼴을 알려 준다."""
    first = command.lstrip().split("\n", 1)[0].strip()
    if first in INTERPRETERS and "\n" in command.strip():
        return (f"\n→ `{first}` 한 줄 뒤의 줄은 {first} 가 아니라 셸이 읽는다. 코드는 `{first} - <<'EOF'` … `EOF`(heredoc), "
                f"`{first} -c '…'`, 또는 write_file 로 파일을 쓴 뒤 `{first} 파일` 로 실행하라.")
    return ""


OUT_KEEP = 5_000_000      # 명령 하나의 출력을 메모리에 이만큼까지 모은다(넘으면 가운데를 버린다)
BG_MAX = 4
_LIVE: "weakref.WeakSet[Shell]" = weakref.WeakSet()


def spawn(argv: list[str], cwd: Path, env: dict | None = None, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
          stderr=subprocess.STDOUT) -> subprocess.Popen:
    """셸 프로세스를 띄우는 유일한 곳. 새 프로세스 묶음으로 띄워 묶음째 끝낼 수 있게 한다
    (POSIX 새 세션, Windows 새 프로세스 그룹 + Job Object — osdep)."""
    p = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=stdin, stdout=stdout, stderr=stderr,
                         **osdep.group_popen_kwargs())
    osdep.attach_group(p)
    SPAWNED.append(p)
    del SPAWNED[:-200]          # 끝난 것은 오래 들고 있지 않는다
    return p


SPAWNED: list = []   # 띄운 셸·백그라운드 작업(강한 참조) — 셸 객체가 버려져도 살아 있는 묶음을 찾을 수 있게(시험 감시, 판 68)


def _killpg(p: subprocess.Popen, hard: bool = True) -> None:
    osdep.kill_group(p, hard)


class Shell:
    def __init__(self, workdir: Path, logdir: Path | None = None):
        self.spec = osdep.shell_spec()     # 못 찾으면 ShellNotFound(임시 파일을 만들기 전에)
        self.workdir = Path(workdir)
        self.cwd = str(self.workdir)
        self.logdir = Path(logdir) if logdir else None
        fd, self.state = tempfile.mkstemp(prefix="stewart-shell-", suffix=".env")   # export -p (작업 폴더 밖, 600)
        os.close(fd)
        self.token = secrets.token_hex(8)
        self.n = 0
        self.proc: subprocess.Popen | None = None
        self.reader: osdep.PipeReader | None = None
        self.restarts = 0
        self.bg: dict[str, dict] = {}
        self.bg_n = 0
        self.closed = False
        _LIVE.add(self)

    # ── 오래 사는 셸 ──
    def _start(self) -> None:
        env = tool_env()
        self.proc = spawn([*self.spec.argv, *self.spec.login_args], self.workdir, env=env, stdin=subprocess.PIPE)
        self.reader = osdep.PipeReader(self.proc.stdout)
        q = lambda p: shlex.quote(osdep.sh_path(p))   # noqa: E731
        init = ""
        if os.path.getsize(self.state):
            init += f". {q(self.state)} 2>/dev/null\n"
        init += f"cd {q(self.cwd)} 2>/dev/null || cd {q(self.workdir)}\n"
        self.proc.stdin.write(init.encode())
        self.proc.stdin.flush()

    def _alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _restart(self) -> None:
        if self.proc is not None:
            _killpg(self.proc)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            try:
                self.proc.stdin.close()      # stdout 은 PipeReader 가 끝에 닿으면 닫는다
            except OSError:
                pass
        self.proc = None
        self.reader = None
        self.restarts += 1

    def syntax_error(self, command: str) -> str | None:
        r = subprocess.run([*self.spec.argv, "-n", "-c", command], capture_output=True, encoding="utf-8",
                           errors="replace", timeout=10, stdin=subprocess.DEVNULL)
        if r.returncode:
            return (r.stderr.strip() or "문법 오류") + interp_hint(command)
        miss = open_heredoc(command)   # 끝 표지 없는 heredoc: bash -n 은 통과(3.2 는 경고도 없음)하지만 오래 사는 셸에선 다음 입력을 삼켜 멈춘다
        if miss:
            return f"heredoc 끝 표지 `{miss}` 줄이 없다" + HEREDOC_HINT
        return None

    def run(self, command: str, timeout_s: float) -> tuple[int | None, str, str]:
        """(종료 코드 | None(시간 초과·셸 끝남), 출력, 알림)"""
        if (err := self.syntax_error(command)):
            return 2, err.replace(self.spec.path + ": -c: ", ""), "문법 오류라 보내지 않았다(셸 상태는 그대로)"
        if not self._alive():
            if self.proc is not None:
                self._restart()
            self._start()
        self.n += 1
        mark = f"__STEWART_{self.token}_{self.n}__"
        pwd = '"$(pwd -W 2>/dev/null || pwd)"' if self.spec.kind == "msys" else '"$PWD"'   # msys: C:/… 꼴로
        script = (f"{{ {command}\n}} < /dev/null\n"
                  f"__stw_rc=$?; export -p > {shlex.quote(osdep.sh_path(self.state))} 2>/dev/null; "
                  f"printf '\\n{mark} %d %s\\n' \"$__stw_rc\" {pwd}\n")
        try:
            self.proc.stdin.write(script.encode())
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self._restart()
            return None, "", "셸에 명령을 보내지 못해 새 셸을 띄웠다. 다시 실행한다."
        reader = self.reader
        buf = bytearray()
        dropped = 0
        deadline = time.monotonic() + timeout_s
        needle = f"\n{mark} ".encode()
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                self._restart()
                return None, self._text(buf, dropped), (f"시간 초과 {timeout_s:g}초 — 그 명령과 셸을 끝내고 새 셸을 띄움"
                                                        f"(cd·export 는 직전 상태로, 내보내지 않은 변수·함수는 사라짐)")
            chunk = reader.read(min(left, 0.5))
            if chunk is None:
                continue
            if not chunk:                                   # 셸이 끝남(exit 등)
                rc = self.proc.wait()
                self._restart()
                return rc, self._text(buf, dropped), f"셸이 끝났다(exit {rc}) — 새 셸을 띄움(cd·export 는 직전 상태로)"
            buf += chunk
            i = buf.find(needle)
            if i >= 0:
                j = buf.find(b"\n", i + len(needle))
                while j < 0:                                # 표지 줄이 아직 덜 왔다
                    more = reader.read(max(1.0, deadline - time.monotonic()))
                    if not more:
                        break
                    buf += more
                    j = buf.find(b"\n", i + len(needle))
                tail = buf[i + len(needle): j if j >= 0 else len(buf)].decode("utf-8", "replace")
                rc_s, _, pwd = tail.partition(" ")
                if pwd:
                    # WIN-PWD 전제(osdep): busybox-w32 의 $PWD 는 C:/… 꼴. 아니면 위치를 "모름"으로 두어 판정이 묻는 쪽으로 간다
                    self.cwd = pwd if not osdep.IS_WIN or re.match(r"^[A-Za-z]:[/\\]", pwd) else UNKNOWN_CWD
                out = self._text(buf[:i], dropped)
                return (int(rc_s) if rc_s.lstrip("-").isdigit() else None), out, ""
            if len(buf) > OUT_KEEP:                          # 가운데를 버린다(앞 1MB 는 남김)
                cut = len(buf) - OUT_KEEP
                del buf[1_000_000: 1_000_000 + cut]
                dropped += cut

    @staticmethod
    def _text(b: bytes, dropped: int) -> str:
        s = bytes(b).decode("utf-8", "replace")
        if dropped:
            s = s[:1_000_000] + f"\n…(출력이 너무 커 가운데 {dropped} 바이트를 버림)…\n" + s[1_000_000:]
        return s

    # ── 백그라운드 ──
    def bg_start(self, command: str) -> tuple[str | None, str]:
        running = [k for k, v in self.bg.items() if v["proc"].poll() is None]
        if len(running) >= BG_MAX:
            return None, f"백그라운드 작업은 동시에 {BG_MAX}개까지(돌고 있는 것: {', '.join(running)}). shell_kill 로 끝내거나 기다린다."
        if (err := self.syntax_error(command)):
            return None, f"문법 오류: {err}"
        self.bg_n += 1
        bid = f"bg{self.bg_n}"
        d = self.logdir or Path(tempfile.gettempdir())
        d.mkdir(parents=True, exist_ok=True)
        log = d / f"shell-{bid}.log"
        f = open(log, "wb")
        pre = f". {shlex.quote(osdep.sh_path(self.state))} 2>/dev/null; " if os.path.getsize(self.state) else ""
        p = spawn(self.spec.run_argv(pre + command), Path(self.cwd) if Path(self.cwd).is_dir() else self.workdir,
                  env=tool_env(), stdout=f)
        f.close()
        self.bg[bid] = {"proc": p, "command": command, "log": log, "started": time.time(), "cwd": self.cwd, "killed": False}
        return bid, str(log)

    def bg_status(self, bid: str) -> dict | None:
        b = self.bg.get(bid)
        if not b:
            return None
        rc = b["proc"].poll()
        return {"id": bid, "command": b["command"], "running": rc is None, "exit": rc, "killed": b["killed"],
                "elapsed_s": round(time.time() - b["started"], 1), "log": str(b["log"])}

    def bg_kill(self, bid: str) -> bool:
        b = self.bg.get(bid)
        if not b or b["proc"].poll() is not None:
            return False
        _killpg(b["proc"], hard=False)
        try:
            b["proc"].wait(timeout=2)
        except subprocess.TimeoutExpired:
            _killpg(b["proc"])
            b["proc"].wait(timeout=5)
        b["killed"] = True
        return True

    def close(self) -> list[dict]:
        """남은 백그라운드 작업과 셸을 모두 끝낸다. 백그라운드 작업 기록(정리 전 상태 포함)을 돌려준다."""
        if self.closed:
            return []
        self.closed = True
        out = []
        for bid in list(self.bg):
            was_running = self.bg[bid]["proc"].poll() is None
            if was_running:
                self.bg_kill(bid)
            st = self.bg_status(bid)
            st["cleaned_up"] = was_running
            out.append(st)
        if self.proc is not None:
            _killpg(self.proc)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        try:
            os.unlink(self.state)                      # 우리가 만든 임시 상태 파일(작업 폴더 밖)
        except OSError:
            pass
        return out


@atexit.register
def _close_all() -> None:                              # 엔진이 close 를 못 불렀을 때의 안전망
    for s in list(_LIVE):
        try:
            s.close()
        except Exception:
            pass
