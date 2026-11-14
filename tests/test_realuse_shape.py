"""실사용 꼴 통시험(판 136): 실사용 설정과 같은 꼴(값이 아니라 항목의 꼴)로 허브를 실제로 띄워, 사람이 화면에서 하는 길을 한 번씩 누른다.

- 왜: 시험 설정이 실사용과 달라서 실사용에서만 깨진 일이 하루에 세 번 있었다.
  - 사용자 기억 파일이 있으면 새 대화 첫 화면이 안 그려졌다(판 134).
  - 백엔드에 effort_values 가 없어 기본 생각 깊이가 안 실렸다(판 132).
  - 안쪽 백엔드의 키 자리가 비어 있다고 등급이 막혔다(판 136).
- 꼴(이 파일의 USER_SHAPE·make_user_shape)
  - backends = mac·cloud·box, default = cloud(사용자 화면의 "키 없음" 셋이 cloud 를 가리켰다), [tiers.*] 표 없음.
  - cloud 에 effort_values·api_key_env(빈 환경 변수)·vision·concurrency·max_context·timeout_s·params(temperature·chat_template_kwargs.enable_thinking·max_tokens).
  - 사용자 프로필 ops 하나.
  - hub.toml 에 [hub](ntfy_url·ntfy_token_file·hub_url)·[forge]·[web](backend·url·fetch).
  - 사용자 수준 STEWART.md(707자)·사용자 스킬 19개.
  - 켜는 인자 `hub --project <폴더> -b cloud -p ops`(포트만 시험용으로 비운 자리).
- 하는 일: 허브를 하위 프로세스로 띄운다(사용자 셸처럼) → 로그인 → 등급 표 → 새 대화(moderato) → 첫 화면 기록 →
  한 마디 → 모델이 받은 요청 본문(reasoning_effort=medium 등) → 답이 옴 → parlando 새 대화.
- 다음 판부터 올리기 전에 이 시험을 돌린다(전체 시험에 들어 있다). 사용자 설정의 꼴이 바뀌면 USER_SHAPE 를 고친다.
"""

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.test_hub import msg, reply

ROOT = Path(__file__).resolve().parents[1]
USER_SHAPE = {
    "backends": ("mac", "cloud", "box"), "default": "cloud", "main_backend": "cloud", "profile": "ops",
    "memory_chars": 707, "skills": 19,
}


class FakeModel:
    """/v1/models 와 /v1/chat/completions(스트리밍 포함). 받은 요청 본문을 모두 남긴다."""

    def __init__(self, answer="네, 안녕하세요."):
        self.requests: list[dict] = []
        me = self

        class Hd(BaseHTTPRequestHandler):
            def do_GET(self):
                data = json.dumps({"data": [{"id": "m-cloud", "max_model_len": 65536}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                me.requests.append(body)
                reply(self, body, msg(answer))

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Hd)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def make_user_shape(root: Path, model_url: str) -> dict:
    """실사용 꼴 설정을 root 아래에 만든다 → 허브를 띄울 환경 변수. 값은 시험용(주소는 가짜 모델)."""
    cfg, state, cache, data = root / "config", root / "state", root / "cache", root / "share"
    ud = cfg / "stewart"
    for d in (ud / "profiles", ud / "skills", state, cache, data):
        d.mkdir(parents=True, exist_ok=True)
    (ud / "backends.toml").write_text(f'''default = "{USER_SHAPE['default']}"

[backends.mac]
base_url = "{model_url}/mac/v1"
model = "m-mac"
precision = "Q4"
max_context = 32768
concurrency = 1
timeout_s = 120

[backends.cloud]
description = "원격 GPU(사설망)"
base_url = "{model_url}/v1"
model = "m-cloud"
precision = "W4A16"
max_context = 65536
concurrency = 8
enabled = true
api_key_env = "STEWART_CLOUD_API_KEY"
timeout_s = 600
vision = true
effort_values = ["low", "medium"]

[backends.cloud.params]
temperature = 0.2
max_tokens = 8192
chat_template_kwargs = {{ enable_thinking = true }}

[backends.box]
base_url = "{model_url}/box/v1"
model = "m-box"
precision = "Q8"
max_context = 32768
concurrency = 2
timeout_s = 300
''', encoding="utf-8")
    (ud / "profiles" / "ops.toml").write_text('name = "ops"\nextends = "chat"\n', encoding="utf-8")
    mem = "# 사용자 기억\n\n" + "".join(f"- 규칙 {i:02d}: 답은 한국어로 짧게, 코드·명령은 원문 그대로 둔다.\n" for i in range(40))
    (ud / "STEWART.md").write_text(mem[:USER_SHAPE["memory_chars"]], encoding="utf-8")
    for i in range(USER_SHAPE["skills"]):
        sd = ud / "skills" / f"skill-{i:02d}"
        sd.mkdir(exist_ok=True)
        (sd / "SKILL.md").write_text(f"---\nname: skill-{i:02d}\ndescription: 시험용 스킬 {i:02d}\n---\n\n시험용 스킬 본문.\n", encoding="utf-8")
    (ud / "ntfy-token").write_text("test-only\n", encoding="utf-8")
    (ud / "hub.toml").write_text(f'''[hub]
ntfy_url = "http://127.0.0.1:9/stewart-test"
ntfy_token_file = "{ud / 'ntfy-token'}"
hub_url = "http://127.0.0.1:9/"

[forge]
url = "http://127.0.0.1:9"
owner = "tester"
credential = "keychain"

[web]
backend = "searxng"
url = "http://127.0.0.1:9/search"
fetch = true
''', encoding="utf-8")
    keep = {k: v for k, v in os.environ.items() if not k.startswith("STEWART_") and not k.startswith("XDG_")}
    return {**keep, "XDG_CONFIG_HOME": str(cfg), "XDG_STATE_HOME": str(state), "XDG_CACHE_HOME": str(cache),
            "XDG_DATA_HOME": str(data), "STEWART_CLOUD_API_KEY": "", "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1",
            # 기억 폴더·대화 자리 폴더는 XDG 를 따르지 않는 기본값(~/.local/share/stewart/…)이라 시험 자리로 돌린다
            "STEWART_MEMORY_DIR": str(data / "stewart" / "memory"), "STEWART_TALK_DIR": str(data / "stewart" / "talk")}


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Hub:
    """하위 프로세스 허브와 화면이 쓰는 API(쿠키·CSRF·Origin)."""

    def __init__(self, root: Path, env: dict, extra_args=()):
        self.port = free_port()
        self.project = root / "project"
        self.project.mkdir(exist_ok=True)
        self.log = open(root / "hub.log", "w", encoding="utf-8")
        self.p = subprocess.Popen([sys.executable, "-m", "stewart", "hub", "--project", str(self.project), "-b", "cloud", "-p", "ops",
                                   "--port", str(self.port), *extra_args], env=env, cwd=root, stdin=subprocess.DEVNULL,
                                  stdout=self.log, stderr=subprocess.STDOUT)
        self.root, self.origin = root, f"http://127.0.0.1:{self.port}"
        t0 = time.time()
        while time.time() - t0 < 60:
            if self.p.poll() is not None:
                raise RuntimeError("허브가 끝났다:\n" + (root / "hub.log").read_text(encoding="utf-8")[-3000:])
            try:
                if self.req("GET", "/login")[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.3)
        else:
            raise RuntimeError("허브가 60초 안에 뜨지 않았다")
        tok = next((root / "state").rglob("hub-token")).read_text().strip()
        st, _, hd = self.req("POST", "/login", urllib.parse.urlencode({"token": tok}),
                             {"Content-Type": "application/x-www-form-urlencoded", "Origin": self.origin})
        self.cookie = (hd.get("Set-Cookie") or "").split(";")[0]
        _, page, _ = self.req("GET", "/", headers={"Cookie": self.cookie})
        self.csrf = re.search(rb'name="csrf" content="([^"]+)"', page).group(1).decode()

    def req(self, method, path, body=None, headers=None):
        c = HTTPConnection("127.0.0.1", self.port, timeout=30)
        c.request(method, path, body=body, headers={"Host": f"127.0.0.1:{self.port}", **(headers or {})})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data, {k: v for k, v in r.getheaders()}

    def get(self, path):
        st, data, _ = self.req("GET", path, headers={"Cookie": self.cookie})
        if st != 200:
            raise AssertionError(f"GET {path} → {st} {data[:300]!r}")
        return json.loads(data)

    def post(self, path, body):
        st, data, _ = self.req("POST", path, json.dumps(body), {"Cookie": self.cookie, "Content-Type": "application/json",
                                                               "X-Stewart-Token": self.csrf, "Origin": self.origin})
        if st != 200:
            raise AssertionError(f"POST {path} → {st} {data[:300]!r}")
        return json.loads(data or b"{}")

    def wait(self, fn, s=30):
        t0 = time.time()
        while time.time() - t0 < s:
            v = fn()
            if v:
                return v
            time.sleep(0.2)
        return None

    def close(self):
        self.p.terminate()
        try:
            self.p.wait(10)
        except subprocess.TimeoutExpired:
            self.p.kill()
        self.log.close()


class TRealUseShape(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="realuse-"))
        self.model = FakeModel()
        self.addCleanup(self.model.close)
        self.env = make_user_shape(self.root, self.model.url)
        self.hub = Hub(self.root, self.env)
        self.addCleanup(self.hub.close)

    def test_newchat_moderato_to_answer(self):
        h = self.hub
        tiers = {r["tier"]: r for r in h.get("/api/tiers")["rows"]}
        for t in ("presto", "moderato", "largo"):   # 표 없는 등급 = 기본 모델(cloud). 빈 키 자리로 막지 않는다
            r = tiers[t]
            self.assertTrue(r["connected"], r)
            self.assertNotEqual(r["key"], "없음", r)
            self.assertNotEqual(r.get("status"), "auth", r)
        self.assertEqual({t: tiers[t]["backend"] for t in ("presto", "moderato", "largo")},
                         {t: "cloud" for t in ("presto", "moderato", "largo")})   # 표 없는 등급의 "기본 모델" = 허브가 켜진 모델(-b cloud)
        self.assertEqual(tiers["moderato"]["effort"]["value"], "medium")
        self.assertEqual(tiers["rubato"]["status"], "unset")
        name = h.post("/api/chat/new", {"how": "fresh", "kind": "code", "tier": "moderato", "title": "새 대화", "from": "main"})["chat"]
        st = h.get(f"/api/state?chat={urllib.parse.quote(name)}")
        log = st["main_log"]
        self.assertTrue(any("기억 파일" in (m.get("text") or "") for m in log), log)            # 시작 안내 줄
        self.assertFalse([m for m in log if m.get("kind") in ("user", "assistant")])           # 빈 대화(첫 화면이 그려질 조건)
        row = next(c for c in st["chats"] if c["name"] == name)
        self.assertEqual((row["tier"], row.get("first") or ""), ("moderato", ""))
        self.assertEqual(st["effort"]["value"], "medium")
        n0 = len(self.model.requests)
        h.post("/api/main", {"text": "안녕", "chat": name})
        got = h.wait(lambda: (lambda s: not s["busy"] and [m for m in s["main_log"] if m.get("kind") == "assistant"])(
            h.get(f"/api/state?chat={urllib.parse.quote(name)}")))
        self.assertTrue(got, (self.root / "hub.log").read_text(encoding="utf-8")[-2000:])
        self.assertIn("안녕하세요", got[-1]["text"])
        body = self.model.requests[n0]
        self.assertEqual(body.get("reasoning_effort"), "medium")
        self.assertEqual(body.get("model"), "m-cloud")
        self.assertIs((body.get("chat_template_kwargs") or {}).get("enable_thinking"), True)
        talk = h.post("/api/chat/new", {"how": "fresh", "kind": "talk", "title": "새 대화", "from": name})["chat"]   # 자리 바꾸기
        self.assertEqual(next(c for c in h.get("/api/state")["chats"] if c["name"] == talk)["kind"], "talk")

    def test_default_chat_unchanged(self):
        h = self.hub
        name = h.post("/api/chat/new", {"how": "fresh", "kind": "code", "title": "새 대화", "from": "main"})["chat"]
        n0 = len(self.model.requests)
        h.post("/api/main", {"text": "안녕", "chat": name})
        self.assertTrue(h.wait(lambda: len(self.model.requests) > n0 and not h.get(f"/api/state?chat={urllib.parse.quote(name)}")["busy"]))
        self.assertNotIn("reasoning_effort", self.model.requests[n0])                          # 등급 없는 대화는 그대로


if __name__ == "__main__":
    unittest.main()
