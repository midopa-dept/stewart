"""stewart cloud wait|info — 래퍼(stewart-cloud, 샌드박스 밖)가 빌린 인스턴스를 샌드박스 안에서 기다리고·보여 준다.

빌리기·지우기는 이 모듈이 하지 않는다(키체인을 못 읽는 샌드박스 안이다). 래퍼가 남긴 기록만 읽는다:
  <CLOUD_DIR>/current.json   최근 인스턴스 {instance_id, machine_id, offer_id, dph_total, label, created, state, used_before}
  <CLOUD_DIR>/history.jsonl  up·down(래퍼)과 ready(여기서 씀) 한 줄씩
wait 는 첫 부팅 시간(만듦 → 컨테이너 시작 → /status → /v1/models)을 history 에 남긴다. 전에 쓴 기계가 받기 시간을 줄이는지 잴 때 쓴다.

판 135: 백엔드 주소가 Tailscale 이름(…ts.net)이면, 장비가 사설망에 붙기 전에는 그 이름으로 묻지 않는다.
  붙기 전에 이름을 물으면 운영체제가 "없는 이름" 답을 한동안 기억해, 붙은 뒤에도 이름이 안 풀린다(30분쯤 본 일이 있다).
  그래서 `tailscale ip -4 <이름>` 이 주소를 줄 때까지 기다리고, 그 뒤로는 그 주소로 묻는다.
  tailscale 명령은 PATH 에서 찾고, 환경 변수 STEWART_TAILSCALE 로 경로를 줄 수 있다. 명령이 없으면 예전처럼 이름으로 묻는다(그렇다고 한 줄 알린다).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

def cloud_dir() -> Path:
    """기록 폴더: 환경 변수 STEWART_CLOUD_DIR → hub.toml [cloud] dir → $XDG_CACHE_HOME/stewart-cloud(없으면 ~/.cache/stewart-cloud).
    래퍼(stewart-cloud)가 쓰는 폴더와 같아야 한다 — 다르면 둘 중 하나로 맞춘다."""
    env = os.environ.get("STEWART_CLOUD_DIR")
    if env:
        return Path(os.path.expanduser(env))
    try:
        import tomllib
        from .forge import config_path
        p = config_path()
        if p.is_file():
            d = (tomllib.loads(p.read_text(encoding="utf-8")).get("cloud") or {}).get("dir")
            if isinstance(d, str) and d:
                return Path(os.path.expanduser(d))
    except (OSError, ValueError):
        pass
    return Path(os.path.expanduser(os.path.join(os.environ.get("XDG_CACHE_HOME") or "~/.cache", "stewart-cloud")))


def _now() -> datetime:
    return datetime.now().astimezone()


def read_current(d: Path | None = None) -> dict | None:
    p = Path(d or cloud_dir()) / "current.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def append_history(ev: dict, d: Path | None = None) -> None:
    d = Path(d or cloud_dir())
    d.mkdir(parents=True, exist_ok=True)
    with (d / "history.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _now().isoformat(timespec="seconds"), **ev}, ensure_ascii=False) + "\n")


def eval_args(cur: dict | None) -> str:
    """평가기에 넘길 인자(--instance·--machine)."""
    if not cur or cur.get("state") == "destroyed":
        return ""
    return f"--instance {cur['instance_id']} --machine {cur['machine_id']}"


def info(d: Path | None = None) -> dict:
    cur = read_current(d)
    return {"current": cur, "eval_args": eval_args(cur),
            "note": None if cur else "기록이 없다 — 래퍼로 빌린 적이 없거나 기록 폴더가 다르다"}


def _created(cur: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(cur["created"])
    except (KeyError, ValueError, TypeError):
        return None


TAILNET_SUFFIX = ".ts.net"
IPV4_RX = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def tailnet_ip(host: str, run=subprocess.run, which=shutil.which) -> tuple[str | None, str]:
    """사설망 이름의 주소를 tailscale 명령으로(운영체제 이름 풀이를 거치지 않는다) → (주소 또는 None, 'ok'|'no-cli'|'error')."""
    exe = os.environ.get("STEWART_TAILSCALE") or which("tailscale")
    if not exe:
        return None, "no-cli"
    try:
        r = run([exe, "ip", "-4", host], capture_output=True, encoding="utf-8", errors="replace", timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None, "error"
    ip = ((r.stdout or "").strip().splitlines() or [""])[0].strip()
    return (ip if r.returncode == 0 and IPV4_RX.match(ip) else None), "ok"


def _host(backend) -> str:
    from urllib.parse import urlsplit
    try:
        return urlsplit(getattr(backend, "base_url", "") or "").hostname or ""
    except ValueError:
        return ""


def _with_host(backend, ip: str):
    """같은 백엔드를 이름 대신 주소로(포트·경로 그대로)."""
    import dataclasses
    from urllib.parse import urlsplit, urlunsplit
    u = urlsplit(backend.base_url)
    netloc = ip + (f":{u.port}" if u.port else "")
    return dataclasses.replace(backend, base_url=urlunsplit((u.scheme, netloc, u.path, u.query, u.fragment)))


def wait(backend, port: int, runs_dir: Path, timeout_s: float = 2700, interval_s: float = 20, d: Path | None = None,
         status_fn=None, models_fn=None, sleep=time.sleep, say=print, ip_fn=tailnet_ip) -> dict:
    """/status → /v1/models 가 될 때까지 기다린다. 결과를 돌려주고, 됐으면 history 에 ready 를 남긴다.
    판 135: 사설망 이름이면 tailscale 이 주소를 줄 때까지 이름으로 묻지 않고, 그 뒤로는 주소로 묻는다."""
    from . import probe
    from .backend import BackendError, models
    cur = read_current(d)
    if not cur or cur.get("state") == "destroyed":
        return {"ok": False, "error": "기다릴 인스턴스가 없다(current.json 이 없거나 destroyed)"}
    created = _created(cur)
    t0 = time.monotonic()
    res = {"instance_id": cur["instance_id"], "machine_id": cur["machine_id"], "used_before": cur.get("used_before"),
           "status_at": None, "container_start": None, "ready_at": None, "ok": False}
    host = _host(backend)
    if host.endswith(TAILNET_SUFFIX):
        said = False
        while time.monotonic() - t0 < timeout_s:
            ip, how = ip_fn(host)
            if how == "no-cli":
                say(f"stewart: tailscale 명령이 없어 {host} 가 사설망에 붙었는지 미리 볼 수 없다 — 이름으로 묻는다"
                    "(붙기 전에 이름을 물으면 '없는 이름' 답이 한동안 남을 수 있다. STEWART_TAILSCALE 로 명령 경로를 준다)")
                break
            if ip:
                res["addr"] = ip
                res["name_wait_s"] = round(time.monotonic() - t0)
                say(f"stewart: {host} 가 사설망에 붙었다({ip}) — 이제 그 주소로 묻는다")
                backend = _with_host(backend, ip)
                break
            if not said:
                say(f"stewart: {host} 가 아직 사설망에 없다 — 붙을 때까지 이름으로 묻지 않는다(tailscale ip)")
                said = True
            sleep(interval_s)
    status_fn = status_fn or (lambda: probe.podstatus(backend, port, runs_dir))
    models_fn = models_fn or (lambda: models(backend))
    last = ""
    while time.monotonic() - t0 < timeout_s:
        if res["status_at"] is None:
            st = status_fn()
            if st.get("ok"):
                res["status_at"] = _now().isoformat(timespec="seconds")
                up = (st.get("status") or {}).get("uptime_s")
                if isinstance(up, (int, float)):
                    res["container_start"] = datetime.fromtimestamp(time.time() - up).astimezone().isoformat(timespec="seconds")
                say(f"stewart: 파드 감시기가 응답한다({res['status_at']}, 컨테이너 시작 {res['container_start']})")
        try:
            names, _ = models_fn()
            res["ready_at"] = _now().isoformat(timespec="seconds")
            res["models"] = names
            res["ok"] = True
            break
        except (BackendError, OSError) as e:
            msg = str(e).split("\n")[0][:120]
            if msg != last:
                say(f"stewart: 아직 — {msg}")
                last = msg
        sleep(interval_s)
    if created:
        def secs(x):
            return round((datetime.fromisoformat(x) - created).total_seconds()) if x else None
        res.update(to_container_s=secs(res["container_start"]), to_status_s=secs(res["status_at"]),
                   to_ready_s=secs(res["ready_at"]))
    if res["ok"]:
        append_history({"event": "ready", **{k: res.get(k) for k in ("instance_id", "machine_id", "used_before",
                                                                      "to_container_s", "to_status_s", "to_ready_s")}}, d)
    else:
        res["error"] = f"{round(timeout_s / 60)}분 안에 /v1/models 가 되지 않았다 — 래퍼 status·Vast 로그(HALT 줄)를 본다"
        if host.endswith(TAILNET_SUFFIX) and not res.get("addr") and res["status_at"] is None and status_fn is not None:
            res["error"] += f" (그 사이 {host} 가 사설망에 붙지 않았다 — tailscale ip 가 주소를 주지 않음)"
    return res
