"""실사용 꼴 통시험(판 136): 실사용에서 흔한 설정의 꼴로 허브를 실제로 띄워, 사람이 화면에서 하는 길을 한 번씩 누른다.

- 왜: 시험 설정이 실사용과 달라서, 시험은 통과하는데 실사용에서만 깨지는 일이 있었다.
  - 사용자 기억 파일이 있으면 새 대화 첫 화면이 안 그려졌다(판 134).
  - 백엔드에 effort_values 가 없으면 기본 생각 깊이가 안 실렸다(판 132).
  - 안쪽 백엔드의 키 자리가 비어 있으면 등급이 막혔다(판 136).
- 꼴(이 파일의 SHAPE·make_user_shape) — 값은 둥근 시험 값이고, 판정은 꼴에만 기댄다.
  - 백엔드 여럿(local·cloud·spare). default 가 허브를 켜는 백엔드와 같다. [tiers.*] 표는 없다(표 없는 등급 = 기본 모델).
  - 허브를 켜는 백엔드(cloud): effort_values · 키 자리(api_key_env, 빈 환경 변수 = 키 없이 보냄) · vision · concurrency ·
    max_context · timeout_s · params(temperature · chat_template_kwargs.enable_thinking · max_tokens).
  - 사용자 프로필 하나(work). hub.toml 에 [hub](ntfy_url · ntfy_token_file · hub_url) · [forge] · [web](backend · url · fetch).
  - 사용자 기억 파일(STEWART.md) · 사용자 스킬 여럿.
  - 켜는 인자 `hub --project <폴더> -b cloud -p work`(포트만 시험용).
- 홈: 허브 하위 프로세스의 HOME 은 시험 자리이고 XDG_* · STEWART_* 는 비운다(판 136.1).
  기본 자리(~/.config/stewart 등)를 그대로 쓰되 그 ~ 가 시험 자리라, 이 시험은 실행하는 사람의 홈 설정을 읽지 않는다.
- 하는 일: 허브를 하위 프로세스로 띄운다(셸에서 켜듯) → 로그인 → 등급 표 → 새 대화(moderato) → 첫 화면 기록 →
  한 마디 → 모델이 받은 요청 본문(reasoning_effort=medium 등) → 답이 옴 → parlando 새 대화.
- 올리기 전 전체 시험에 들어 있다. 실사용에서 새 꼴이 깨지면 그 꼴을 make_user_shape 에 먼저 넣고 고친다.
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
SHAPE = {
    "backends": ("local", "cloud", "spare"), "default": "cloud", "main_backend": "cloud", "profile": "work",
    "memory_chars": 600, "skills": 12,
}


class FakeModel:
    """/v1/models 와 /v1/chat/completions(스트리밍 포함). 받은 요청 본문을 모두 남긴다."""

    def __init__(self, answer="네, 안녕하세요.", route=None):
        self.requests: list[dict] = []
        self.route = route   # 판 138: 요청 본문 → 응답(없으면 늘 answer)
        self.down = False    # 판 143: True 면 모델 서버가 안 닿는 것처럼(503)
        me = self

        class Hd(BaseHTTPRequestHandler):
            def _down(self):
                if not me.down:
                    return False
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return True

            def do_GET(self):
                if self._down():
                    return
                data = json.dumps({"data": [{"id": "m-cloud", "max_model_len": 65536}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self._down():
                    return
                me.requests.append(body)
                reply(self, body, me.route(body) if me.route else msg(answer))

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Hd)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def make_user_shape(root: Path, model_url: str) -> dict:
    """실사용 꼴 설정을 root 아래에 만든다 → 허브를 띄울 환경 변수. 값은 시험용(주소는 가짜 모델).
    판 136.1: HOME 을 시험 자리(root/home)로 바꾸고 XDG_*·STEWART_* 는 비운다 — 사용자처럼 기본 자리(~/.config/stewart 등)를 쓰되,
    그 ~ 가 시험 자리다. 그래서 XDG 를 따르지 않는 기본값(프로필·스킬·기억·대화 자리)도 실제 사용자 폴더로 새지 않는다."""
    home = root / "home"
    cfg, state, cache, data = home / ".config", home / ".local" / "state", home / ".cache", home / ".local" / "share"
    ud = cfg / "stewart"
    for d in (ud / "profiles", ud / "skills", state, cache, data):
        d.mkdir(parents=True, exist_ok=True)
    (ud / "backends.toml").write_text(f'''default = "{SHAPE['default']}"

[backends.local]
base_url = "{model_url}/local/v1"
model = "m-local"
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

[backends.spare]
base_url = "{model_url}/spare/v1"
model = "m-spare"
precision = "Q8"
max_context = 32768
concurrency = 2
timeout_s = 300
''', encoding="utf-8")
    (ud / "profiles" / f"{SHAPE['profile']}.toml").write_text(f'name = "{SHAPE["profile"]}"\nextends = "chat"\n', encoding="utf-8")
    mem = "# 사용자 기억\n\n" + "".join(f"- 규칙 {i:02d}: 답은 한국어로 짧게, 코드·명령은 원문 그대로 둔다.\n" for i in range(40))
    (ud / "STEWART.md").write_text(mem[:SHAPE["memory_chars"]], encoding="utf-8")
    for i in range(SHAPE["skills"]):
        sd = ud / "skills" / f"skill-{i:02d}"
        sd.mkdir(exist_ok=True)
        (sd / "SKILL.md").write_text(f"---\nname: skill-{i:02d}\ndescription: 시험용 스킬 {i:02d}\n---\n\n시험용 스킬 본문.\n", encoding="utf-8")
    (ud / "ntfy-token").write_text("test-only\n", encoding="utf-8")
    (ud / "hub.toml").write_text(f'''[hub]
ntfy_url = "http://127.0.0.1:9/stewart-test"
ntfy_token_file = "{(ud / 'ntfy-token').as_posix()}"
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
    # 설정 글(TOML)에 경로를 쓸 때는 as_posix — Windows 경로의 역슬래시(C:\\Users…)는 TOML 기본 문자열에서 이스케이프로 읽힌다.
    # Windows: ~ 는 USERPROFILE 로 풀리므로 그것도 시험 자리로. 하위 허브는 런처처럼 UTF-8 모드(PYTHONUTF8=1)로 — 아니면 기록이 지역 인코딩으로 쓰인다.
    keep = {k: v for k, v in os.environ.items() if not k.startswith("STEWART_") and not k.startswith("XDG_") and k not in ("HOME", "USERPROFILE")}
    return {**keep, "HOME": str(home), "USERPROFILE": str(home), "STEWART_CLOUD_API_KEY": "", "PYTHONPATH": str(ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}


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
        self.p = subprocess.Popen([sys.executable, "-m", "stewart", "hub", "--project", str(self.project), "-b", SHAPE["main_backend"], "-p", SHAPE["profile"],
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
        tok = next(root.rglob("hub-token")).read_text(encoding="utf-8").strip()
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


WORKER_HEAD = "너는 부모 에이전트가 맡긴 일 하나만 하는 작업자다"


def is_worker(body: dict) -> bool:
    return str((body.get("messages") or [{}])[0].get("content") or "").startswith(WORKER_HEAD)


class TWorkerPath(unittest.TestCase):
    """판 138: `chat --once --worker` 가 허브가 띄운 작업자와 같은 설정(지시문·도구·문맥 창·생각)으로 도는지 — 요청 본문 대조."""

    TASK = "README.md 를 읽고 첫 줄을 그대로 적어라"
    TOOLS = ["read_file", "write_file"]

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="realuse-worker-"))
        self.spawned = False

        def route(body):
            if is_worker(body):
                return msg("작업자 끝")
            if not self.spawned and any(t["function"]["name"] == "workers" for t in body.get("tools") or []):
                self.spawned = True
                return msg(calls=[("workers", {"action": "spawn", "task": self.TASK, "tools": self.TOOLS})])
            return msg("끝")
        self.model = FakeModel(route=route)
        self.addCleanup(self.model.close)
        self.env = make_user_shape(self.root, self.model.url)
        bt = self.root / "home" / ".config" / "stewart" / "backends.toml"   # moderato 한 칸(budget 없이) — 쓰기 작업자의 등급
        bt.write_text(bt.read_text(encoding="utf-8") + '\n[tiers.moderato]\nuse = "코딩·대화의 기본"\ndefault_for = ["write"]\n', encoding="utf-8")
        (self.root / "project").mkdir(exist_ok=True)
        (self.root / "project" / "README.md").write_text("첫 줄\n", encoding="utf-8")

    def first_worker(self, n0):
        return next((b for b in self.model.requests[n0:] if is_worker(b)), None)

    def test_cli_worker_matches_hub_worker(self):
        hub = Hub(self.root, self.env)
        try:
            name = hub.post("/api/chat/new", {"how": "fresh", "kind": "code", "title": "새 대화", "from": "main"})["chat"]
            n0 = len(self.model.requests)
            hub.post("/api/main", {"text": "작업자에게 맡겨라", "chat": name})
            hw = hub.wait(lambda: self.first_worker(n0))
        finally:
            hub.close()
        self.assertIsNotNone(hw, "허브 작업자가 뜨지 않았다:\n" + (self.root / "hub.log").read_text(encoding="utf-8")[-2000:])
        n1 = len(self.model.requests)
        r = subprocess.run([sys.executable, "-m", "stewart", "chat", "-p", SHAPE["profile"], "--backend", SHAPE["main_backend"],
                            "--workdir", str(self.root / "project"), "--once", "--mode", "edits", "--worker", "write",
                            "--worker-tools", ",".join(self.TOOLS), self.TASK],
                           env=self.env, cwd=self.root, capture_output=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual((out["tier"], out["stop_reason"]), ("moderato", "completed"))
        cw = self.first_worker(n1)
        self.assertIsNotNone(cw)
        tools = lambda b: sorted(t["function"]["name"] for t in b.get("tools") or [])
        self.assertEqual(tools(cw), tools(hw))
        self.assertEqual(cw["messages"][0]["content"], hw["messages"][0]["content"])                 # 지시문 그대로
        for k in ("model", "reasoning_effort", "max_tokens", "chat_template_kwargs", "temperature"):
            self.assertEqual(cw.get(k), hw.get(k), k)
        self.assertEqual(cw.get("reasoning_effort"), "medium")
        self.assertIn(self.TASK, json.dumps(cw["messages"][1:], ensure_ascii=False))
        meta = json.loads((Path(out["run_dir"]) / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])["meta"]
        self.assertEqual(meta["worker"], {"depth": 1, "tier": "moderato"})                           # 작업자 표시
        self.assertEqual(meta["think"]["source"].get("effort"), "default")
        self.assertEqual(meta["features"]["tier"]["name"], "moderato")


class TNoLeak(unittest.TestCase):
    """시험 설정이 실제 사용자 폴더에 기대지 않는다(판 136.1: 프로필이 실제 ~/.config 에서 읽혀 이 맥에서만 통과했다)."""

    def test_defaults_resolve_inside_test_home(self):
        root = Path(tempfile.mkdtemp(prefix="realuse-leak-"))
        env = make_user_shape(root, "http://127.0.0.1:9")
        home = str(root / "home")
        code = ("import json; from stewart import config as C, skills as S, memory as M, projcfg as P, forge as F; "
                "print(json.dumps([str(C.user_profiles_dir()), str(S.user_dir()), str(M.memory_dir({})) if hasattr(M, 'memory_dir') else '', "
                "str(P.user_dir()), str(F.config_path()), str(C.backends_path())]))")
        r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        for p in json.loads(r.stdout):
            if p:
                self.assertTrue(p.startswith(home), p)

    def test_windows_shape(self):
        """Windows 에서만 드러나던 시험 쪽 꼴: 설정 글의 경로는 / 로(TOML 이스케이프), ~ 자리 USERPROFILE·UTF-8 모드도 시험 쪽."""
        import tomllib
        root = Path(tempfile.mkdtemp(prefix="realuse-win-"))
        env = make_user_shape(root, "http://127.0.0.1:9")
        self.assertEqual((env["USERPROFILE"], env["PYTHONUTF8"]), (env["HOME"], "1"))
        for f in (root / "home").rglob("*.toml"):
            text = f.read_text(encoding="utf-8")
            tomllib.loads(text)
            for line in text.splitlines():
                if str(root.as_posix()) in line:
                    self.assertNotIn("\\", line, f)


if __name__ == "__main__":
    unittest.main()
