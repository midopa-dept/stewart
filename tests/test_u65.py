"""판 86(SW-38): 값싼 바깥 API 인스턴스 — 캐시 적중 토큰(DeepSeek 꼴·OpenAI 꼴) 세기·적중률, 혼잡 시간 배수 가격표, 생각 끔 인자 통과,
생각 켬 + 도구에서 앞 생각을 안 돌려 보내면 400(가짜 API) ↔ 되돌려 보내기 손잡이, 지시문·도구 접두가 단계마다 같은지,
인스턴스 설정 꼴 그대로 허브를 실제로 띄워 보기(판 77 의 "설정 키 모름으로 못 뜸" 되풀이 막기).
예시 설정 폴더로도 돌리려면 STEWART_TEST_INSTANCE_DIRS=<폴더>[,<폴더>…] (instance.toml·backends.toml·hub.toml 이 든 곳)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from stewart import backend as B, chat as C, instance as I
from stewart.config import Backend, Profile, load_backends
from stewart.record import Recorder
from tests.test_cloud import FakeUI

BACKENDS = """default = "deepseek"
[backends.deepseek]
kind = "openai"
provider = "DeepSeek"
base_url = "https://api.deepseek.com"
model = "deepseek-flash"
precision = "api"
max_context = 1000000
context_budget = 131072
concurrency = 2
api_key_env = "DEEPSEEK_API_KEY"
thinking_control = "none"
[backends.deepseek.params]
max_tokens = 8192
thinking = { type = "disabled" }
[backends.deepseek.price]
input = 0.15
cache_read = 0.003
output = 0.60
peak = { multiplier = 2.0, tz = "UTC", days = "mon-fri", hours = ["01-04", "06-10"] }
"""
HUB = """[hub]
port = 8767
label = "DeepSeek"
color = "#4f46e5"
[web]
url = "https://search.example"
fetch = true
"""
INSTANCE = """project = "{proj}"
port = 8767
backend = "deepseek"
mode = "edits"
key_env = "DEEPSEEK_API_KEY"
keychain_service = "stewart-deepseek"
keychain_account = "api"
deny = ["{deny}"]
"""


class TCacheAndPrice(unittest.TestCase):
    def test_cache_tokens_both_shapes(self):
        self.assertEqual(B.cache_hit_tokens({"prompt_cache_hit_tokens": 900, "prompt_cache_miss_tokens": 100}), 900)
        self.assertEqual(B.cache_hit_tokens({"prompt_tokens_details": {"cached_tokens": 40}}), 40)
        self.assertIsNone(B.cache_hit_tokens({"prompt_tokens": 5}))
        bk = Backend("d", "http://x", "m", "q", 1000)
        B.count_use(bk, {"usage": {"prompt_tokens": 1000, "completion_tokens": 10, "prompt_cache_hit_tokens": 900}})
        B.count_use(bk, {"usage": {"prompt_tokens": 500, "completion_tokens": 5}})          # 캐시 칸 없는 호출은 적중률에 안 든다
        self.assertEqual((bk.used["cache_hit"], bk.used["cache_seen"]), (900, 1000))

    def test_peak_factor(self):
        pr = tomllib.loads(BACKENDS)["backends"]["deepseek"]["price"]
        wed = datetime(2026, 11, 11, 2, 30, tzinfo=timezone.utc)      # 수요일 UTC 02:30 = 한국 11:30
        self.assertEqual(B.peak_factor(pr, wed), 2.0)
        self.assertEqual(B.peak_factor(pr, wed.replace(hour=5)), 1.0)
        self.assertEqual(B.peak_factor(pr, wed.replace(hour=9, minute=59)), 2.0)
        self.assertEqual(B.peak_factor(pr, datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc)), 1.0)   # 토요일
        self.assertEqual(B.peak_factor({"input": 1}), 1.0)

    def test_cost_uses_hit_tokens_and_peak(self):
        d = Path(tempfile.mkdtemp())
        (d / "b.toml").write_text(BACKENDS, encoding="utf-8")
        bk = load_backends(d / "b.toml")[0]["deepseek"]
        u = {"prompt_tokens": 1_000_000, "completion_tokens": 100_000, "prompt_cache_hit_tokens": 800_000}
        with mock.patch.object(B, "peak_factor", return_value=1.0):
            data = {"usage": dict(u)}
            B.add_cost(bk, data)
        self.assertAlmostEqual(data["usage"]["cost_usd"], 0.8 * 0.003 + 0.2 * 0.15 + 0.1 * 0.60, places=6)
        with mock.patch.object(B, "peak_factor", return_value=2.0):
            data = {"usage": dict(u)}
            B.add_cost(bk, data)
        self.assertAlmostEqual(data["usage"]["cost_usd"], 2 * (0.8 * 0.003 + 0.2 * 0.15 + 0.1 * 0.60), places=6)

    def test_thinking_param_goes_through(self):
        d = Path(tempfile.mkdtemp())
        (d / "b.toml").write_text(BACKENDS, encoding="utf-8")
        bk = load_backends(d / "b.toml")[0]["deepseek"]
        body = B._body(bk, [{"role": "user", "content": "x"}])
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["model"], "deepseek-flash")


def fake_deepseek(thinking_on: bool):
    """DeepSeek 꼴 가짜: 생각 켬 + tools 면 앞 assistant 메시지에 reasoning_content 가 없을 때 400. usage 에 캐시 칸."""
    seen = []

    def f(backend, messages, tools=None, *a, **k):
        seen.append(messages)
        if thinking_on and tools and any(m["role"] == "assistant" and "reasoning_content" not in m for m in messages):
            raise B.BackendError("HTTP 400", 400, '{"error":{"message":"The reasoning_content in the thinking mode must be passed back to the API."}}')
        n = sum(1 for m in messages if m["role"] == "assistant")
        msg = ({"role": "assistant", "content": None, "reasoning_content": "읽는다",
                "tool_calls": [{"id": f"c{n}", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}]}
               if n == 0 else {"role": "assistant", "content": "끝", "reasoning_content": "다 봤다"})
        return {"choices": [{"message": msg, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 5, "prompt_cache_hit_tokens": 800 if n else 0}}, 0.1
    return f, seen


class TThinkingPassback(unittest.TestCase):
    def sess(self, tmp, env):
        (tmp / "a.txt").write_text("x", encoding="utf-8")
        p = Profile(name="chat", path=tmp / "p.toml", system="s", tools=["read_file"], workdir=tmp, max_steps=4)
        with mock.patch.dict(os.environ, env):
            s = C.ChatSession(p, Backend("d", "http://x/v1", "m", "q", 100000), Recorder(tmp / "runs", "c"), FakeUI(),
                              stream=False, mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        self.addCleanup(lambda: C.T.close_env(s.env))
        return s

    def test_passback_needed_when_thinking_on(self):
        for mode, want in (("off", "backend_error"), ("tool_turns", "answered"), ("all", "answered")):
            tmp = Path(tempfile.mkdtemp())
            s = self.sess(tmp, {"STEWART_REASONING_PASSBACK": mode})
            f, seen = fake_deepseek(True)
            with mock.patch.dict(os.environ, {"STEWART_REASONING_PASSBACK": mode}), mock.patch.object(C, "chat", side_effect=f):
                self.assertEqual(s.turn("a.txt 봐"), want, mode)
        tmp = Path(tempfile.mkdtemp())
        s = self.sess(tmp, {})
        f, seen = fake_deepseek(False)      # 생각 끔(프리셋 기본): 되돌려 보내지 않아도 된다
        with mock.patch.object(C, "chat", side_effect=f):
            self.assertEqual(s.turn("a.txt 봐"), "answered")
        self.assertEqual(s.st.get("prefix_changes", 0), 0)    # 지시문·도구 접두가 단계마다 같다(캐시가 맞는 꼴)


def _launch(cfgdir: Path, tmp: Path) -> tuple[int | None, str]:
    """설정 폴더 그대로 허브를 띄워(샌드박스 없이, 포트 0 대신 빈 포트) 첫 "접속:" 줄이 나오는지. (끝 코드 또는 None, 출력)."""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proj = tmp / "proj"
    proj.mkdir(exist_ok=True)
    data = tomllib.loads((cfgdir / "backends.toml").read_text(encoding="utf-8"))
    bname = data.get("default") or next(iter(data["backends"]))
    env = {k: v for k, v in os.environ.items() if not k.startswith("STEWART_")}
    env.update(STEWART_BACKENDS=str(cfgdir / "backends.toml"), STEWART_HUB_CONFIG=str(cfgdir / "hub.toml"),
               STEWART_MEMORY_DIR=str(tmp / "mem"), STEWART_RUNS_DIR=str(tmp / "runs"), XDG_STATE_HOME=str(tmp / "st"),
               XDG_CONFIG_HOME=str(tmp / "cfg"), XDG_DATA_HOME=str(tmp / "data"), XDG_CACHE_HOME=str(tmp / "cache"),
               STEWART_SERVED_CONTEXT="0", PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
               **{str(data["backends"][bname].get("api_key_env") or "X_KEY"): "test-key-not-real"})
    p = subprocess.Popen([sys.executable, "-m", "stewart", "hub", "-b", bname, "--project", str(proj), "--state-dir", str(tmp / "hub"),
                          "--runs-dir", str(tmp / "runs"), "--port", str(port), "--config", str(cfgdir / "hub.toml")],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, cwd=str(proj))
    out, t0 = b"", time.monotonic()
    try:
        os.set_blocking(p.stdout.fileno(), False)
        while time.monotonic() - t0 < 40:
            try:
                x = p.stdout.read() or b""
            except BlockingIOError:
                x = b""
            out += x
            if b"\xec\xa0\x91\xec\x86\x8d: " in out or "화면: http".encode() in out:   # "접속: " 또는 허브의 "화면: http"
                return None, out.decode("utf-8", "replace")
            if p.poll() is not None:
                out += p.stdout.read() or b""
                return p.returncode, out.decode("utf-8", "replace")
            time.sleep(0.2)
        return -1, out.decode("utf-8", "replace")
    finally:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()


class TInstanceConfig(unittest.TestCase):
    def make(self, home: Path) -> Path:
        cfg = home / ".config" / "stewart-deepseek"
        cfg.mkdir(parents=True)
        (cfg / "backends.toml").write_text(BACKENDS, encoding="utf-8")
        (cfg / "hub.toml").write_text(HUB, encoding="utf-8")
        (cfg / "instance.toml").write_text(INSTANCE.format(proj=home / "proj", deny=home / "other"), encoding="utf-8")
        return cfg

    def test_inline_preset_loads_and_launches(self):
        tmp = Path(tempfile.mkdtemp())
        cfgdir = self.make(tmp)
        c = I.load("deepseek", home=tmp)
        self.assertEqual((c["port"], c["backend"], c["keychain_service"]), (8767, "deepseek", "stewart-deepseek"))
        from stewart.hubmain import hub_config
        self.assertEqual(hub_config(str(cfgdir / "hub.toml"))["label"], "DeepSeek")
        rc, out = _launch(cfgdir, tmp)
        self.assertIsNone(rc, out[-1500:])

    def test_example_dirs_if_given(self):   # 사람이 복사해 쓸 예시 설정 폴더 그대로(없으면 건너뜀)
        dirs = [Path(x) for x in (os.environ.get("STEWART_TEST_INSTANCE_DIRS") or "").split(",") if x.strip()]
        if not dirs:
            self.skipTest("STEWART_TEST_INSTANCE_DIRS 없음")
        for d in dirs:
            with self.subTest(d=d.name):
                tmp = Path(tempfile.mkdtemp())
                cfg = tmp / ".config" / f"stewart-{d.name}"
                shutil.copytree(d, cfg)
                inst = tomllib.loads((cfg / "instance.toml").read_text(encoding="utf-8"))
                (cfg / "instance.toml").write_text((cfg / "instance.toml").read_text(encoding="utf-8")
                                                   .replace(inst["project"], str(tmp / "proj")), encoding="utf-8")
                c = I.load(d.name, home=tmp)
                be = load_backends(cfg / "backends.toml")[0]
                self.assertIn(c["backend"], be)
                self.assertEqual(be[c["backend"]].api_key_env, c["key_env"])
                rc, out = _launch(cfg, tmp)
                self.assertIsNone(rc, out[-1500:])


if __name__ == "__main__":
    unittest.main()
