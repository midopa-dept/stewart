"""따로 도는 허브(stewart instance) 실사용 꼴 — 시험 자리(HOME)에 인스턴스 설정을 만들고 instance.env·hub_argv 그대로 띄울 인자를 돌려준다.

- 바깥 API 는 시험 안 가짜 모델(provider 를 적어 바깥으로 보이게), 키는 환경 변수(key_env)로 — 진짜 키체인·인스턴스 폴더는 건드리지 않는다.
- OS 샌드박스(sandbox-exec)는 씌우지 않는다(시험 프로세스 안에서 겹쳐 못 씌운다). 샌드박스 규칙 글은 instance.profile 로 따로 본다.
"""

import os
import sys
from pathlib import Path

from stewart import instance as I
from tests.test_realuse_shape import free_port

NAME = "out"


def make_instance(root: Path, model_url: str, key: str | None = "test-key", price=(1.0, 2.0), extra_backends: str = "") -> tuple[dict, dict]:
    """(cfg, env). root/home 아래 ~/.config/stewart-<이름> 등을 만든다."""
    home = root / "home"
    d = I.dirs(NAME, home)
    d["config"].mkdir(parents=True, exist_ok=True)
    proj = root / "project"
    proj.mkdir(parents=True, exist_ok=True)
    port = free_port()
    (d["config"] / "instance.toml").write_text(
        f'project = "{proj.as_posix()}"\nport = {port}\nbackend = "out"\nkey_env = "OUT_TEST_KEY"\n'
        f'keychain_service = "stewart-out-test"\nmode = "edits"\n', encoding="utf-8")
    (d["config"] / "backends.toml").write_text(
        f'default = "out"\n\n[backends.out]\nbase_url = "{model_url}/v1"\nmodel = "out-chat"\nprovider = "Outside"\n'
        f'api_key_env = "OUT_TEST_KEY"\nmax_context = 65536\nconcurrency = 2\ntimeout_s = 60\n'
        + (f"price = {{ input = {price[0]}, output = {price[1]} }}\n" if price else "") + extra_backends, encoding="utf-8")
    (d["config"] / "hub.toml").write_text('[web]\nbackend = "searxng"\nurl = "http://127.0.0.1:9/search"\nfetch = true\n', encoding="utf-8")
    for sub in ("profiles", "skills"):
        (d["config"] / sub).mkdir(exist_ok=True)
    cfg = I.load(NAME, home)
    I.make_dirs(cfg)
    base = {k: v for k, v in os.environ.items() if not k.startswith(("STEWART_", "XDG_")) and k not in ("HOME", "USERPROFILE")}
    root_src = Path(__file__).resolve().parents[1] / "src"
    base.update(HOME=str(home), USERPROFILE=str(home), PYTHONPATH=str(root_src), PYTHONUTF8="1")
    env = I.env(cfg, key, base)
    return cfg, env


def argv(cfg) -> list[str]:
    a = I.hub_argv(cfg)
    a[0] = sys.executable
    return a
