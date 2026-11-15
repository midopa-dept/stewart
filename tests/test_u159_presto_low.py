"""판 159: presto 기본 생각 깊이 low(대화 틀 자리) · 생각 되풀이 끊개(기본 끔).

- presto 를 표에 생각 칸 없이 적으면, 잰 꼴(등급 칸 reasoning_effort = "low", reasoning_effort_at = "template")과 같은 요청이 나간다.
  그 꼴은 effort_values 없는 백엔드에서 쟀으므로 목록이 없어도 싣는다. 목록에 low 가 없거나 바깥 API 면 싣지 않고 까닭을 남긴다.
- 표의 reasoning_effort·reasoning_effort_at, 환경 STEWART_REASONING_EFFORT 가 이긴다. moderato 규칙(판 132)은 그대로다.
- 끊개: STEWART_THINK_REPEAT_CUT=N 또는 등급 칸 think_repeat_cut = N. 생각 스트림에서 같은 문단이 N번 나오면 그 걸음을 끊고
  생각 끔으로 한 번 다시 묻는다. 사건 think_repeat_cut 과 구성 요소 think_repeat 가 남는다. 안 켜면 아무 일도 없다.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from stewart import spawn as SP
from stewart import think as TH
from stewart import tierview as TV
from stewart.chat import REPEAT_THINK_NOTE
from stewart.config import Backend, Profile
from tests.test_realuse_shape import make_user_shape

PARAMS = {"temperature": 0.2, "chat_template_kwargs": {"enable_thinking": True}, "max_tokens": 16384}


def B(effort_values=None, url="http://127.0.0.1:9/v1", **kw):
    return Backend("cloud", url, "m", None, 150000, params=dict(PARAMS), effort_values=effort_values, **kw)


def prof(tier_raw):
    return Profile(name="x", path=Path(tempfile.gettempdir()) / "p.toml", system="s", tools=["read_file"],
                   workdir=Path(tempfile.gettempdir()), raw={"tier": tier_raw})


PRESTO = '''default = "cloud"
[backends.cloud]
base_url = "http://127.0.0.1:9/v1"
model = "m"
[tiers.presto]
backend = "cloud"
budget = 24000
max = 2
slim = true
use = "곧. 찾기·읽기·요약, 검산할 것."
default_for = ["read"]
'''
SEALED = PRESTO + 'reasoning_effort = "low"\nreasoning_effort_at = "template"\n'   # 잰 꼴(wlow)


def tier_from(text):
    p = Path(tempfile.mkdtemp(prefix="u159t-")) / "backends.toml"
    p.write_text(text, encoding="utf-8")
    return SP.load_tiers(p)["presto"]


def kwargs(t, backend, env=None):
    s = TH.settings(prof(SP.tier_raw(t, backend)), env=env or {})
    TH.check_effort(s, backend)
    return s, TH.call_kwargs(backend, s, None, 1000), TH.call_kwargs(backend, s, None, 1000, first=True)


class TPrestoDefault(unittest.TestCase):
    def test_same_request_as_sealed_form(self):
        b = B()
        s0, k0, f0 = kwargs(tier_from(SEALED), b)
        s1, k1, f1 = kwargs(tier_from(PRESTO), b)
        self.assertEqual((k1, f1), (k0, f0))                                                    # 요청에 얹는 칸이 같다
        self.assertEqual(k1["overrides"], {"chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "low"}})
        self.assertEqual({k: v for k, v in s1.items() if k != "source"}, {k: v for k, v in s0.items() if k != "source"})
        self.assertEqual((s0["source"]["effort"], s1["source"]["effort"]), ("tier", "default"))
        on_list = B(effort_values=["xhigh", "medium", "low"])
        self.assertEqual(kwargs(tier_from(PRESTO), on_list)[1], kwargs(tier_from(SEALED), on_list)[1])

    def test_not_sent(self):
        t = tier_from(PRESTO)
        s, k, _ = kwargs(t, B(effort_values=["medium", "high"]))
        self.assertNotIn("effort", s)
        self.assertIn("low 이 없다", s["effort_note"])
        for ext in (B(url="https://api.example.com/v1"), B(provider="example")):
            s, k, _ = kwargs(t, ext)
            self.assertNotIn("effort", s)
            self.assertIn("바깥 API", s["effort_note"])
            self.assertNotIn("reasoning_effort", json.dumps(k))
        s, k, _ = kwargs(tier_from(PRESTO + 'reasoning_effort = "none"\n'), B())
        self.assertEqual((s.get("effort"), s["source"].get("effort")), (None, "tier:off"))
        s, k, _ = kwargs(t, B(), {"STEWART_REASONING_EFFORT": "none"})
        self.assertNotIn("effort", s)
        s, k, _ = kwargs(t, B(), {"STEWART_REASONING_EFFORT": "medium"})
        self.assertEqual((s["effort"], s["effort_at"]), ("medium", "template"))                  # 환경 값도 presto 자리(template)로

    def test_table_wins(self):
        s, k, _ = kwargs(tier_from(PRESTO + 'reasoning_effort_at = "body"\n'), B())
        self.assertEqual((s["effort"], s["effort_at"]), ("low", "body"))
        self.assertEqual(k["overrides"], {"reasoning_effort": "low"})
        s, k, _ = kwargs(tier_from(PRESTO + 'reasoning_effort = "medium"\n'), B())
        self.assertEqual((s["effort"], s["effort_at"], s["source"]["effort"]), ("medium", "body", "tier"))

    def test_moderato_unchanged(self):
        self.assertEqual(SP.tier_raw({"name": "moderato"}, B(effort_values=["low", "medium"]))["think"],
                         {"reasoning_effort": "medium", "effort_default": True})
        self.assertIn("effort_values", SP.tier_raw({"name": "moderato"}, B())["effort_default_skipped"])
        for t in ("largo", "rubato"):
            self.assertNotIn("think", SP.tier_raw({"name": t}, B()))

    def test_tiers_view(self):
        d = Path(tempfile.mkdtemp(prefix="u159v-"))
        (d / "backends.toml").write_text(PRESTO, encoding="utf-8")
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("STEWART_REASONING_EFFORT", None)
            os.environ.pop("STEWART_INSTANCE", None)
            rows, _ = TV.load(d / "backends.toml")
        r = {x["tier"]: x for x in rows}
        self.assertEqual(r["presto"]["effort"], {"value": "low", "source": "default", "note": ""})
        self.assertEqual(TV.effort_label(r["presto"]["effort"]), "low (기본)")


class TRepeatSettings(unittest.TestCase):
    def test_off_by_default_and_knobs(self):
        self.assertNotIn("repeat_cut", TH.settings(prof({"name": "presto"}), env={}))
        self.assertNotIn("repeat_cut", kwargs(tier_from(PRESTO), B())[0])
        self.assertEqual(TH.settings(prof({}), env={"STEWART_THINK_REPEAT_CUT": "3"})["repeat_cut"], 3)
        t = tier_from(PRESTO + "think_repeat_cut = 4\n")
        self.assertEqual(t["think"]["think_repeat_cut"], 4)
        self.assertEqual(t["unknown_keys"], [])
        s = TH.settings(prof(SP.tier_raw(t, B())), env={})
        self.assertEqual((s["repeat_cut"], s["source"]["repeat_cut"]), (4, "tier"))
        s = TH.settings(prof(SP.tier_raw(t, B())), env={"STEWART_THINK_REPEAT_CUT": "0"})
        self.assertNotIn("repeat_cut", s)                                                       # 환경이 이긴다(끔)
        for bad in ("1", "x", "-3"):
            s = TH.settings(prof({}), env={"STEWART_THINK_REPEAT_CUT": bad})
            self.assertNotIn("repeat_cut", s)
            self.assertIn("STEWART_THINK_REPEAT_CUT", s["ignored"])

    def test_watch(self):
        para = "The user wants the README first line. Let me read the file README.md now."
        w = TH.RepeatWatch(3)
        self.assertIsNone(w.feed(para[:10]))
        self.assertIsNone(w.feed(para[10:] + "\n\n"))
        self.assertIsNone(w.feed("짧다.\n\n짧다.\n\n짧다.\n\n"))                                    # 짧은 문단은 세지 않는다
        self.assertIsNone(w.feed("다른 문단: 이것은 한 번만 나오는 긴 생각 문단이다, 셀 만큼 길다.\n\n"))
        self.assertIsNone(w.feed("  " + para.upper().replace(" ", "  ") + "\n \n"))                # 공백·대소문자는 접는다
        hit = w.feed(para + "\n")
        self.assertIsNone(hit)                                                                  # 아직 닫히지 않은 문단
        hit = w.feed("\nnext")
        self.assertEqual((hit["count"], hit["at"], hit["distinct"]), (3, 3, 2))
        self.assertEqual(hit["paragraph"], TH.norm_para(para))
        self.assertEqual(len(hit["para_hash"]), 12)


LOOP = "I need to check the README file again before answering, so let me think about what it says once more."


class Server:
    """생각 켬 요청에는 같은 문단을 되풀이하는 생각을 흘리고(끝에 답), 생각 끔 요청에는 바로 답한다."""

    def __init__(self):
        self.requests = []
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
                think = (body.get("chat_template_kwargs") or {}).get("enable_thinking") is not False
                if not body.get("stream"):   # 스트리밍이 아닌 요청: 생각을 한 덩이로 싣고 답
                    m = {"role": "assistant", "content": "답이다", **({"reasoning_content": (LOOP + "\n\n") * 8} if think else {})}
                    data = json.dumps({"choices": [{"message": m, "finish_reason": "stop"}],
                                       "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                evs = [{"choices": [{"index": 0, "delta": {"reasoning_content": LOOP + "\n\n"}}]} for _ in range(8)] if think else []
                evs += [{"choices": [{"index": 0, "delta": {"content": "답이다"}}]},
                        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    for e in evs:
                        self.wfile.write(b"data: " + json.dumps(e, ensure_ascii=False).encode() + b"\n\n")
                        self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n")
                except OSError:
                    pass

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Hd)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class TRepeatCut(unittest.TestCase):
    def run_worker(self, extra_env, worker=True):
        root = Path(tempfile.mkdtemp(prefix="u159r-"))
        srv = Server()
        self.addCleanup(srv.close)
        env = make_user_shape(root, srv.url)
        env.update(extra_env)
        wd = root / "w"
        wd.mkdir()
        r = subprocess.run([sys.executable, "-m", "stewart", "chat", "--once", "--backend", "cloud", "-p", "chat", *(["--worker", "read"] if worker else []),
                            "--workdir", str(wd), "README 첫 줄을 알려 줘"], env=env, cwd=str(root), capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        evs = [json.loads(x) for p in (root / "home").rglob("events.jsonl") for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        return srv.requests, evs, r

    def test_cut_then_retry_without_thinking(self):
        reqs, evs, r = self.run_worker({"STEWART_THINK_REPEAT_CUT": "3"})
        self.assertEqual(len(reqs), 2, [q.get("chat_template_kwargs") for q in reqs])
        self.assertTrue(reqs[0]["stream"])                                                     # 끊개를 켜면 스트리밍으로 받아 도중에 끊는다
        self.assertNotEqual((reqs[0].get("chat_template_kwargs") or {}).get("enable_thinking"), False)
        self.assertIs(reqs[1]["chat_template_kwargs"]["enable_thinking"], False)               # 생각 끔으로 한 번 다시
        self.assertEqual(reqs[1]["messages"][-1], {"role": "user", "content": REPEAT_THINK_NOTE})
        self.assertNotIn(LOOP, json.dumps(reqs[1]["messages"], ensure_ascii=False))             # 끊은 생각은 대화에 넣지 않는다
        cut = [e for e in evs if e.get("kind") == "think_repeat_cut"]
        self.assertEqual(len(cut), 1)
        self.assertEqual((cut[0]["count"], cut[0]["at"]), (3, 3))
        self.assertIn(TH.norm_para(LOOP), cut[0]["paragraph"])
        self.assertIn(LOOP, cut[0]["reasoning_content"])                                         # 과정은 기록에 닿는다
        comp = [e for e in evs if e.get("kind") == "component" and e.get("name") == "think_repeat"]
        self.assertEqual(len(comp), 1)
        self.assertEqual((comp[0]["trigger"], comp[0]["action"], comp[0]["detail"]["count"]), ("same_paragraph", "retry", 3))
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["stop_reason"], "completed")

    def test_main_stream_path(self):   # 허브 메인과 같은 길(ChatSession 스트리밍): 화면에 보이면서 세고, 끊으면 생각 끔으로 다시
        reqs, evs, r = self.run_worker({"STEWART_THINK_REPEAT_CUT": "3"}, worker=False)
        self.assertEqual(len(reqs), 2)
        self.assertTrue(reqs[0]["stream"] and reqs[1]["stream"])
        self.assertIs(reqs[1]["chat_template_kwargs"]["enable_thinking"], False)
        self.assertEqual(reqs[1]["messages"][-1], {"role": "user", "content": REPEAT_THINK_NOTE})
        self.assertNotIn(LOOP, json.dumps(reqs[1]["messages"], ensure_ascii=False))
        self.assertEqual(len([e for e in evs if e.get("kind") == "think_repeat_cut"]), 1)
        self.assertEqual(len([e for e in evs if e.get("kind") == "component" and e.get("name") == "think_repeat"]), 1)
        self.assertIn("답이다", r.stdout)
        reqs, evs, r = self.run_worker({}, worker=False)
        self.assertEqual(len(reqs), 1)                                                          # 끔이면 되풀이해도 그대로
        self.assertFalse([e for e in evs if e.get("kind") == "think_repeat_cut"])

    def test_off_by_default(self):
        reqs, evs, r = self.run_worker({})
        self.assertEqual(len(reqs), 1)
        self.assertFalse(reqs[0].get("stream"))                                                 # 작업자 길은 예전처럼 스트리밍 아님
        self.assertFalse([e for e in evs if e.get("kind") in ("think_repeat_cut",) or e.get("name") == "think_repeat"])
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["stop_reason"], "completed")


if __name__ == "__main__":
    unittest.main()
