"""따로 도는 stewart 인스턴스(예: 바깥 API 로 도는 허브)를 OS 샌드박스 안에 켠다 — `stewart instance <이름> …`.

인스턴스 하나 = 이름 N 에 대해
  설정  ~/.config/stewart-N/      instance.toml · backends.toml · hub.toml · profiles/ · skills/ · sandbox-allow.txt
  상태  ~/.local/state/stewart-N/ (허브 상태·runs), 기억·대화 자리 폴더 ~/.local/share/stewart-N/(memory/·talk/), 캐시 ~/.cache/stewart-N/
기존(이름 없는) stewart 자리와 섞이지 않는다. 키는 macOS 키체인에서 켤 때 한 번 읽어 환경 변수로만 넘기고(파일·화면에 안 남김),
모델의 셸·도구 자식 환경에서는 지운다(shell.HIDDEN_ENV). 샌드박스 안에서는 키체인 조회도 막는다.

instance.toml:
  project = "/경로/프로젝트"        허브의 작업 폴더
  port = 8766
  backend = "exaone"               backends.toml 의 이름
  key_env = "EXAONE_API_KEY"       키를 넣을 환경 변수(백엔드 api_key_env 와 같게)
  keychain_service = "stewart-exaone"
  keychain_account = "api"         (없으면 계정 조건 없이)
  python = "/경로/.venv/bin/python" stewart 를 돌릴 파이썬(없으면 지금 파이썬)
  stewart_src = ["/경로/stewart/src", …]   샌드박스 안에서 읽어야 하는 stewart 코드·venv 자리(읽기만)
  deny = ["/경로", …]              읽기·쓰기 모두 막을 자리(그 아래 전부)
  mode = "edits"                   허브 메인 모드
sandbox-allow.txt(한 줄에 하나, # 주석): `r /경로`(읽기만 엶) 또는 `rw /경로`(읽기·쓰기 엶) — deny 안의 자리를 사람이 하나씩 푼다.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

SANDBOX = "/usr/bin/sandbox-exec"
# 샌드박스 안에서 막는 mach 서비스: 키체인(키를 꺼내지 못하게)
DENY_MACH = ("com.apple.SecurityServer", "com.apple.securityd", "com.apple.security.agent")
TMP_WRITE = ("/private/tmp", "/private/var/folders", "/dev")


class InstanceError(Exception):
    pass


def dirs(name: str, home: Path | None = None) -> dict[str, Path]:
    h = Path(home or os.path.expanduser("~"))
    n = f"stewart-{name}"
    return {"config": h / ".config" / n, "state": h / ".local" / "state" / n, "share": h / ".local" / "share" / n,
            "cache": h / ".cache" / n}


def check_name(name: str) -> str:
    if not name or not all(c.isalnum() or c in "-_" for c in name) or name.startswith("-"):
        raise InstanceError(f"인스턴스 이름은 영숫자·-·_ 만: {name!r}")
    return name


def load(name: str, home: Path | None = None) -> dict:
    d = dirs(check_name(name), home)
    p = d["config"] / "instance.toml"
    if not p.is_file():
        raise InstanceError(f"설정이 없다: {p}")
    cfg = tomllib.loads(p.read_text(encoding="utf-8"))
    for k in ("project", "port", "backend"):
        if k not in cfg:
            raise InstanceError(f"{p}: {k} 가 없다")
    cfg["_dirs"], cfg["_name"] = d, name
    cfg["_allow"] = read_allow(d["config"] / "sandbox-allow.txt")
    return cfg


def read_allow(p: Path) -> list[tuple[str, str]]:
    out = []
    if not p.is_file():
        return out
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        mode, _, path = line.partition(" ")
        if mode not in ("r", "rw") or not path.strip():
            raise InstanceError(f"{p}:{i}: 'r /경로' 또는 'rw /경로' 꼴이어야 한다: {line!r}")
        out.append((mode, _abs(path.strip())))
    return out


def _abs(p: str) -> str:
    q = os.path.realpath(os.path.expanduser(p))
    if not os.path.isabs(q):
        raise InstanceError(f"절대 경로여야 한다: {p}")
    return q


def _q(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def profile(cfg: dict) -> str:
    """sandbox-exec 규칙(SBPL). 기본 허용 → 쓰기는 모두 막고 자기 자리만 → deny 자리는 읽기·쓰기 막음 → 허용 목록·stewart 코드는 다시 엶.
    뒤 규칙이 앞 규칙을 이긴다."""
    d = cfg["_dirs"]
    own = [str(cfg["project"])] + [str(d[k]) for k in ("state", "share", "cache")]
    deny = [_abs(x) for x in cfg.get("deny") or []]
    src = [_abs(x) for x in cfg.get("stewart_src") or []]
    lines = ["(version 1)", f"; stewart instance {cfg['_name']} — `stewart instance {cfg['_name']} profile` 이 만든다(손으로 고치지 않는다)",
             "(allow default)", "", "; 쓰기: 자기 자리와 임시 폴더만",
             "(deny file-write* (subpath \"/\"))",
             "(allow file-write* " + " ".join(f"(subpath {_q(_abs(x))})" for x in own + list(TMP_WRITE)) + ")"]
    if deny:
        lines += ["", "; 읽기·쓰기 모두 막는 자리", "(deny file-read* file-write* " + " ".join(f"(subpath {_q(x)})" for x in deny) + ")"]
    lines += ["", "; 설정 폴더는 읽기만(키는 여기 두지 않는다)", f"(allow file-read* (subpath {_q(_abs(str(d['config'])))}))"]
    if src:
        lines += ["; stewart 실행에 꼭 필요한 코드·venv(읽기만)", "(allow file-read* " + " ".join(f"(subpath {_q(x)})" for x in src) + ")"]
    for mode, path in cfg.get("_allow") or []:
        acts = "file-read* file-write*" if mode == "rw" else "file-read*"
        lines.append(f"(allow {acts} (subpath {_q(path)}))   ; sandbox-allow.txt")
    lines += ["", "; 키체인을 샌드박스 안에서 꺼내지 못하게", "(deny mach-lookup " + " ".join(f'(global-name "{m}")' for m in DENY_MACH) + ")"]
    return "\n".join(lines) + "\n"


def env(cfg: dict, key: str | None, base: dict | None = None) -> dict:
    """허브에 넘길 환경. 키는 key_env 하나에만."""
    d = cfg["_dirs"]
    e = {k: v for k, v in (base if base is not None else os.environ).items()
         if not k.startswith("STEWART_") and k not in (cfg.get("key_env") or "",)}
    e.update({
        "STEWART_USER_DIR": str(d["config"]), "STEWART_BACKENDS": str(d["config"] / "backends.toml"),
        "STEWART_PROFILES_DIR": str(d["config"] / "profiles"), "STEWART_SKILLS_DIR": str(d["config"] / "skills"),
        "STEWART_HUB_CONFIG": str(d["config"] / "hub.toml"), "STEWART_MEMORY_DIR": str(d["share"] / "memory"),
        "STEWART_TALK_DIR": str(d["share"] / "talk"),   # 대화 자리 폴더도 자기 자리 안(기본 대화 폴더는 울타리 밖)
        "STEWART_RUNS_DIR": str(d["state"] / "runs"), "STEWART_CLOUD_DIR": str(d["cache"] / "cloud"),
        "XDG_CONFIG_HOME": str(d["config"] / "xdg"), "XDG_STATE_HOME": str(d["state"] / "xdg"), "XDG_DATA_HOME": str(d["share"] / "xdg"), "XDG_CACHE_HOME": str(d["cache"] / "xdg"),
        "PYTHONDONTWRITEBYTECODE": "1", "STEWART_INSTANCE": cfg["_name"],
    })
    if key and cfg.get("key_env"):
        e[str(cfg["key_env"])] = key
    return e


def hub_argv(cfg: dict) -> list[str]:
    d = cfg["_dirs"]
    py = str(cfg.get("python") or sys.executable)
    return [py, "-m", "stewart", "hub", "-b", str(cfg["backend"]), "--project", str(cfg["project"]),
            "--state-dir", str(d["state"] / "hub"), "--runs-dir", str(d["state"] / "runs"), "--port", str(int(cfg["port"])),
            "--mode", str(cfg.get("mode") or "edits"), "--config", str(d["config"] / "hub.toml")]


def keychain(service: str, account: str | None = None, run=subprocess.run) -> str | None:
    if sys.platform != "darwin" or not shutil.which("security"):
        return None
    argv = ["security", "find-generic-password", "-s", service] + (["-a", account] if account else []) + ["-w"]
    r = run(argv, capture_output=True, timeout=15)
    return r.stdout.decode("utf-8", "replace").strip() or None if r.returncode == 0 else None


def write_profile(cfg: dict) -> Path:
    p = cfg["_dirs"]["state"] / "sandbox.sb"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(profile(cfg), encoding="utf-8")
    return p


def make_dirs(cfg: dict) -> None:
    d = cfg["_dirs"]
    for k in ("state", "share", "cache"):
        d[k].mkdir(parents=True, exist_ok=True)
    (d["state"] / "runs").mkdir(parents=True, exist_ok=True)
    (d["share"] / "memory").mkdir(parents=True, exist_ok=True)
    (d["share"] / "talk").mkdir(parents=True, exist_ok=True)
    Path(cfg["project"]).mkdir(parents=True, exist_ok=True)


def run(name: str) -> int:
    """샌드박스 안에서 허브를 띄운다(앞에서 — tmux 안에서 부른다)."""
    cfg = load(name)
    make_dirs(cfg)
    key = None
    if cfg.get("keychain_service"):
        key = keychain(str(cfg["keychain_service"]), cfg.get("keychain_account"))
        if not key:
            print(f"stewart instance: 키체인에 키가 없다(항목 {cfg['keychain_service']}). 먼저:\n"
                  f"  security add-generic-password -s {cfg['keychain_service']}"
                  + (f" -a {cfg['keychain_account']}" if cfg.get("keychain_account") else "") + " -w", file=sys.stderr)
            return 2
    sb = write_profile(cfg)
    argv = [SANDBOX, "-f", str(sb)] + hub_argv(cfg)
    os.chdir(str(cfg["project"]))   # 막힌 폴더에서 켜도 파이썬이 시작 폴더를 읽다 멈추지 않게
    os.execve(SANDBOX, argv, env(cfg, key))
    return 0   # 닿지 않음


START_WAIT_S = 20           # start 가 허브의 첫 줄("접속: …")을 기다리는 시간
READY_MARK = "접속: "
DEAD_MARK = "[stewart instance] 허브가 끝났다"


def run_log(cfg: dict) -> Path:
    return cfg["_dirs"]["state"] / "hub-run.log"


def tmux_cmd(name: str, log: Path) -> str:
    """tmux 안에서 돌릴 줄: 허브 출력을 화면과 기록 파일에 함께, 끝나면 까닭을 남기고 창을 닫지 않는다(attach 로 볼 수 있게)."""
    run_cmd = " ".join(shlex.quote(x) for x in [sys.executable, "-m", "stewart", "instance", name, "run"])
    lq = shlex.quote(str(log))
    inner = (f"set -o pipefail; {run_cmd} 2>&1 | tee -a {lq}; rc=$?; "
             f"echo \"{DEAD_MARK}(exit $rc) — 기록 {log}\" | tee -a {lq}; read -r _")
    return "bash -c " + shlex.quote(inner)


def watch_start(log: Path, offset: int, wait_s: float = START_WAIT_S, sleep=None) -> tuple[str, str]:
    """기록 파일의 offset 뒤를 지켜본다 → ("ready"|"dead"|"slow", 새로 쓰인 글)."""
    import time
    sleep = sleep or time.sleep
    end = time.monotonic() + wait_s
    text = ""
    while time.monotonic() < end:
        try:
            with open(log, "rb") as f:
                f.seek(offset)
                text = f.read().decode("utf-8", "replace")
        except OSError:
            text = ""
        if DEAD_MARK in text:
            return "dead", text
        if READY_MARK in text:
            return "ready", text
        sleep(0.5)
    return "slow", text


def start(name: str) -> int:
    """tmux 세션 stewart-<이름> 안에 run 을 띄우고, 허브가 "접속:" 줄을 낼 때까지 지켜본다(죽으면 마지막 줄과 기록 자리를 보인다)."""
    cfg = load(name)
    sess = f"stewart-{name}"
    if not shutil.which("tmux"):
        raise InstanceError("tmux 가 없다")
    if subprocess.run(["tmux", "has-session", "-t", sess], capture_output=True).returncode == 0:
        print(f"이미 떠 있다: tmux attach -t {sess}")
        return 0
    log = run_log(cfg)
    log.parent.mkdir(parents=True, exist_ok=True)
    if not log.exists():   # 허브가 로그인 토큰을 찍으므로 나만 읽게
        os.close(os.open(log, os.O_CREAT | os.O_WRONLY, 0o600))
    offset = log.stat().st_size
    subprocess.run(["tmux", "new-session", "-d", "-s", sess, tmux_cmd(name, log)], check=True)
    state, text = watch_start(log, offset)
    tail = "\n".join(text.strip().splitlines()[-20:])
    if state == "dead":
        print(f"허브가 켜지자마자 끝났다 — 기록 {log}\n{tail}", file=sys.stderr)
        subprocess.run(["tmux", "kill-session", "-t", sess], capture_output=True)
        return 1
    if state == "slow":
        print(f"{START_WAIT_S}초 안에 '접속:' 줄이 안 보였다(아직 뜨는 중일 수 있다). 보기: tmux attach -t {sess} · 기록 {log}")
        return 0
    url = next((ln for ln in text.splitlines() if READY_MARK in ln), "").strip()
    print(f"켰다: tmux 세션 {sess} — {url} · 보기 tmux attach -t {sess} · 기록 {log}")
    return 0


PROBE = r"""
import os, sys
for p in sys.argv[1:]:
    try:
        os.listdir(p) if os.path.isdir(p) else open(p, "rb").read(1)
        print("열림", p)
    except FileNotFoundError:
        print("없음", p)
    except OSError as e:
        print("막힘", p, type(e).__name__)
"""


def check(name: str, paths: list[str]) -> int:
    """같은 샌드박스로 짧은 확인 프로그램을 돌려 각 자리가 열리는지 보인다(키는 넘기지 않는다)."""
    cfg = load(name)
    make_dirs(cfg)
    sb = write_profile(cfg)
    d = cfg["_dirs"]
    probe = paths or (list(cfg.get("deny") or []) + [str(cfg["project"]), str(d["state"]), str(d["config"])]
                      + list(cfg.get("stewart_src") or []))
    py = str(cfg.get("python") or sys.executable)
    r = subprocess.run([SANDBOX, "-f", str(sb), py, "-c", PROBE, *[os.path.expanduser(p) for p in probe]],
                       env=env(cfg, None), capture_output=True, timeout=60, cwd=str(cfg["project"]))
    sys.stdout.write(r.stdout.decode("utf-8", "replace"))
    sys.stderr.write(r.stderr.decode("utf-8", "replace")[-2000:])
    for label, where in (("자기폴더", cfg["project"]), ("대화폴더", d["share"] / "talk")):   # 대화 자리 폴더도 울타리 안이어야 한다
        q = shlex.quote(str(where))
        w = subprocess.run([SANDBOX, "-f", str(sb), "/bin/sh", "-c", f"touch {q}/.stewart-check && rm {q}/.stewart-check && echo 쓰기됨 {label}"],
                           env=env(cfg, None), capture_output=True, timeout=30)
        sys.stdout.write(w.stdout.decode("utf-8", "replace") or f"쓰기안됨 {label}\n")
    k = subprocess.run([SANDBOX, "-f", str(sb), "/usr/bin/security", "find-generic-password", "-s", str(cfg.get("keychain_service") or "x"), "-w"],
                       env=env(cfg, None), capture_output=True, timeout=30)
    print("키체인 " + ("막힘" if k.returncode != 0 else "열림(!)"))
    return r.returncode


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("쓰는 법: stewart instance <이름> start|run|check [경로…]|profile|dirs", file=sys.stderr)
        return 2
    name, cmd, rest = argv[0], argv[1], argv[2:]
    try:
        if cmd == "start":
            return start(name)
        if cmd == "run":
            return run(name)
        if cmd == "check":
            return check(name, rest)
        if cmd == "profile":
            sys.stdout.write(profile(load(name)))
            return 0
        if cmd == "dirs":
            for k, v in dirs(check_name(name)).items():
                print(k, v)
            return 0
    except InstanceError as e:
        print(f"stewart instance: {e}", file=sys.stderr)
        return 2
    print(f"모르는 명령: {cmd}", file=sys.stderr)
    return 2
