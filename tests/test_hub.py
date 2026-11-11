"""허브 H1: 세션 등록부·파일 편지함·작업자 프로세스·pause/resume·승인 통로(답은 파이프로만, answer/ 는 기록)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from stewart import hub as H, tools as T
from stewart.worker import Halt, HubUI

PY = sys.executable


def msg(content=None, calls=None):
    m = {"role": "assistant", "content": content}
    if calls:
        m["tool_calls"] = [{"id": f"t{time.monotonic_ns()}-{i}", "type": "function",
                            "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": m, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def sse(resp: dict) -> bytes:
    """완성된 응답 하나를 OpenAI 스트리밍(SSE) 조각으로 바꾼다: 본문 → 툴콜 → 끝 → usage → [DONE]."""
    ch = resp["choices"][0]
    m = ch["message"]
    evs = []
    if m.get("content"):
        text = m["content"]
        evs += [{"choices": [{"index": 0, "delta": {"role": "assistant", "content": text[:1]}}]}]
        if len(text) > 1:
            evs += [{"choices": [{"index": 0, "delta": {"content": text[1:]}}]}]
    for i, tc in enumerate(m.get("tool_calls") or []):
        evs.append({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": i, "id": tc["id"], "type": "function",
                                                                        "function": tc["function"]}]}}]})
    evs.append({"choices": [{"index": 0, "delta": {}, "finish_reason": ch.get("finish_reason") or "stop"}]})
    evs.append({"choices": [], "usage": resp.get("usage") or {}})
    return b"".join(b"data: " + json.dumps(e, ensure_ascii=False).encode() + b"\n\n" for e in evs) + b"data: [DONE]\n\n"


def reply(handler, body: dict, item: dict) -> None:
    """요청이 stream 이면 SSE 로, 아니면 JSON 하나로 답한다."""
    if body.get("stream"):
        data = sse(item)
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.end_headers()
        handler.wfile.write(data)
        return
    data = json.dumps(item).encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class FakeServer:
    """/v1/chat/completions 에 대본을 차례로 돌려준다. 대본 항목이 함수면 그때 불러 만든다."""

    def __init__(self):
        self.script: list = []
        self.requests: list = []
        me = self

        class Hd(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                me.requests.append(body)
                item = me.script.pop(0) if me.script else msg("(대본 끝)")
                reply(self, body, item() if callable(item) else item)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Hd)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def wait(cond, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("기다린 조건이 오지 않았다")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.fake = FakeServer()
        self.addCleanup(self.fake.close)
        (self.tmp / "backends.toml").write_text(
            f'default = "fake"\n[backends.fake]\nbase_url = "{self.fake.url}"\nmodel = "m"\nmax_context = 32768\n'
            'enabled = true\ntimeout_s = 30\n', encoding="utf-8")
        (self.tmp / "w.toml").write_text(
            'name = "w"\ntools = ["read_file", "write_file", "run_shell"]\nmax_steps = 10\nlanguage = "ko"\n'
            'system = "s"\n', encoding="utf-8")
        self.wd = self.tmp / "wd"
        self.wd.mkdir()
        e = mock.patch.dict(os.environ, {"STEWART_BACKENDS": str(self.tmp / "backends.toml"), "STEWART_SERVED_CONTEXT": "0",
                                         "STEWART_HUB_TOKEN": "hub-secret", "PYTHONPATH": str(Path(H.__file__).parents[1])})
        e.start()
        self.addCleanup(e.stop)
        self.hub = H.Hub(self.tmp / "hub", self.tmp / "runs")
        self.addCleanup(self.hub.close)

    def spawn(self, task, mode="auto", name="w1"):
        return self.hub.spawn(name, task, self.wd, str(self.tmp / "w.toml"), "fake", mode=mode)


class TMailbox(Base):
    def test_two_instructions_two_reports(self):
        self.fake.script = [msg(calls=[("write_file", {"path": "a.txt", "content": "A"})]), msg("첫 보고: a.txt 를 만들었다"),
                            msg("둘째 보고: 할 일 없음")]
        e = self.spawn("a.txt 를 만들어라")
        self.assertEqual((e["status"], e["inbox_seq"], e["executor"]), ("starting", 1, {"kind": "local"}))
        h1, r1 = self.hub.wait_report("w1", 1)
        self.assertEqual(r1.strip(), "첫 보고: a.txt 를 만들었다")
        self.assertEqual((h1["answers"], h1["reason"], h1["files_changed"], h1["report_kind"]), (1, "answered", ["a.txt"], "보고"))
        n = self.hub.send("w1", "둘째 지시")
        self.assertEqual(n, 2)
        h2, r2 = self.hub.wait_report("w1", 2)
        self.assertEqual((r2.strip(), h2["files_changed"]), ("둘째 보고: 할 일 없음", []))
        self.assertEqual((self.wd / "a.txt").read_text(), "A")
        wait(lambda: self.hub.sdir("w1").status().get("status") == "idle")
        reg = {x["name"]: x for x in self.hub.list()}
        self.assertEqual((reg["w1"]["status"], reg["w1"]["outbox_seq"]), ("idle", 2))
        self.assertTrue(Path(reg["w1"]["run_dir"]).is_dir())
        self.assertIn("둘째 지시", json.dumps(self.fake.requests[-1]["messages"], ensure_ascii=False))
        self.assertIn("a.txt 를 만들어라", json.dumps(self.fake.requests[-1]["messages"], ensure_ascii=False))  # 한 세션 대화
        self.assertTrue(all(r.get("stream") for r in self.fake.requests))       # 작업자는 스트리밍으로 받는다
        steps = [json.loads(l) for l in (Path(reg["w1"]["run_dir"]) / "events.jsonl").read_text().splitlines()
                 if '"kind": "step"' in l]
        self.assertTrue(steps and all(isinstance(e["response"].get("ttft_s"), float) for e in steps))

    def test_worker_env_has_no_hub_secret(self):
        self.fake.script = [msg(calls=[("run_shell", {"command": "env > env.txt"})]), msg("끝")]
        self.spawn("env 를 적어라")
        self.hub.wait_report("w1", 1)
        env_txt = (self.wd / "env.txt").read_text()
        self.assertNotIn("STEWART_HUB_TOKEN", env_txt)
        self.assertNotIn("hub-secret", env_txt)
        self.assertNotIn("STEWART_BACKENDS", env_txt)     # 셸에는 tool_env 규칙 그대로
        self.assertNotIn("hub-secret", json.dumps(H.worker_env({"STEWART_HUB_TOKEN": "hub-secret", "A": "1"})))

    def test_failed_when_worker_dies(self):
        self.fake.script = [msg("끝")]
        self.spawn("끝내라")
        self.hub.wait_report("w1", 1)
        os.killpg(self.hub.procs["w1"].pid, 9)
        self.hub.procs["w1"].wait()
        wait(lambda: {x["name"]: x for x in self.hub.list()}["w1"]["status"] == "failed")


class TApproval(Base):
    def test_answer_file_does_not_approve_pipe_does(self):
        self.fake.script = [msg(calls=[("run_shell", {"command": "touch made.txt"})]), msg("셸을 돌렸다")]
        self.spawn("셸을 돌려라", mode="edits")          # edits: 셸은 묻는다
        sd = self.hub.sdir("w1")
        wait(lambda: self.hub.pending_asks("w1"))
        ask = self.hub.pending_asks("w1")[0]
        self.assertEqual((ask["seq"], ask["kind"]), (1, "confirm"))
        self.assertNotIn("nonce", ask)
        # 같은 계정이 answer/ 를 써도(셸 도구가 할 수 있는 일) 승인되지 않는다
        H.write_json(sd.answer_dir / "0001.json", {"seq": 1, "answer": "y", "by": "human"})
        time.sleep(1.0)
        self.assertFalse((self.wd / "made.txt").exists())
        self.assertEqual(sd.status()["status"], "waiting_approval")
        self.assertIsNone(sd.read_report(1))
        # 승인 통로로 온 답만 받는다(번호가 다른 답은 버린다)
        os.write(self.hub.pipes["w1"], b'{"seq": 9, "answer": "y"}\n')
        time.sleep(0.5)
        self.assertFalse((self.wd / "made.txt").exists())
        self.hub.answer("w1", 1, "y")
        h, r = self.hub.wait_report("w1", 1)
        self.assertEqual(r.strip(), "셸을 돌렸다")
        self.assertTrue((self.wd / "made.txt").exists())
        rec = H.read_json(sd.answer_dir / "0001.json")
        self.assertEqual((rec["by"], rec["via"]), ("human", "api"))
        self.assertNotIn("answer", rec)                    # 기록에는 답 글을 다시 적지 않는다

    def test_shell_child_cannot_see_approval_fd(self):
        def probe():
            fd = int(self.hub.procs["w1"].args[self.hub.procs["w1"].args.index("--approval-fd") + 1])
            return msg(calls=[("run_shell", {"command": f"{PY} -c 'import os; os.fstat({fd})' && echo VISIBLE > fd.txt"
                                                        f" || echo HIDDEN > fd.txt"})])
        self.fake.script = [probe, msg("확인했다")]
        self.spawn("fd 를 봐라")
        self.hub.wait_report("w1", 1)
        self.assertEqual((self.wd / "fd.txt").read_text().strip(), "HIDDEN")

    def test_noninheritable_fd_hidden_from_shell_tool(self):
        r, w = os.pipe()
        self.addCleanup(lambda: (os.close(r), os.close(w)))
        env = T.ToolEnv(self.wd)
        self.addCleanup(lambda: T.close_env(env))
        check = f"{PY} -c 'import os; os.fstat({r})' && echo VISIBLE || echo HIDDEN"
        os.set_inheritable(r, False)
        self.assertIn("HIDDEN", T.run_shell(env, check)[1])
        p = subprocess.run(check, shell=True, capture_output=True, text=True, pass_fds=(r,))   # 대조: 넘기면 보인다
        self.assertIn("VISIBLE", p.stdout)


class TLifecycle(Base):
    def test_pause_resume_keeps_history(self):
        self.fake.script = [msg("첫 일 끝")]
        self.spawn("첫 일: 기억할 낱말은 사과")
        self.hub.wait_report("w1", 1)
        e = self.hub.pause("w1")
        self.assertEqual(e["status"], "paused")
        self.assertTrue(Path(e["run_dir"]).is_dir())
        self.assertNotIn("w1", self.hub.procs)
        self.hub.send("w1", "둘째 일")                      # 멈춘 동안 온 지시는 이어 가면 받는다
        time.sleep(0.3)
        self.assertIsNone(self.hub.read_report("w1", 2))
        n_req = len(self.fake.requests)
        self.fake.script = [msg("둘째 일 끝")]
        e2 = self.hub.resume("w1")
        self.assertEqual(e2["status"], "starting")
        h, r = self.hub.wait_report("w1", 2)
        self.assertEqual(r.strip(), "둘째 일 끝")
        sent = json.dumps(self.fake.requests[n_req]["messages"], ensure_ascii=False)
        self.assertIn("기억할 낱말은 사과", sent)            # 앞 대화를 이어 간다
        self.assertIn("첫 일 끝", sent)
        with self.assertRaises(ValueError):
            self.hub.resume("w1")                          # 멈춘 세션만 이어 간다

    def test_reopen_stopped_keeps_history(self):
        """끝낸(stopped) 작업자도 기록이 있으면 앞 대화 그대로 다시 연다(U2-2 2b)."""
        self.fake.script = [msg("첫 일 끝")]
        self.spawn("첫 일: 기억할 낱말은 배")
        self.hub.wait_report("w1", 1)
        self.assertEqual(self.hub.stop("w1")["status"], "stopped")
        n_req = len(self.fake.requests)
        self.fake.script = [msg("다시 연 뒤 끝")]
        self.hub.send("w1", "둘째 일")
        self.assertEqual(self.hub.resume("w1")["status"], "starting")
        h, r = self.hub.wait_report("w1", 2)
        self.assertEqual(r.strip(), "다시 연 뒤 끝")
        self.assertIn("기억할 낱말은 배", json.dumps(self.fake.requests[n_req]["messages"], ensure_ascii=False))

    def test_worker_now_in_list(self):
        def slow():
            time.sleep(1.2)
            return msg("느린 끝")
        self.fake.script = [msg(calls=[("write_file", {"path": "n.txt", "content": "N"})]), slow]
        self.spawn("n.txt 를 써라")
        seen = None
        for _ in range(60):
            e = [x for x in self.hub.list() if x["name"] == "w1"][0]
            if e.get("now"):
                seen = e["now"]
                break
            time.sleep(0.05)
        self.assertIsNotNone(seen)
        self.assertIn("since", seen)
        self.hub.wait_report("w1", 1)
        time.sleep(0.2)
        self.assertIsNone([x for x in self.hub.list() if x["name"] == "w1"][0].get("now"))   # 끝나면 비운다

    def test_pause_mid_turn_at_step_boundary(self):
        def slow():
            self.hub.sdir("w1").set_control("pause")         # 이 응답을 받은 뒤 다음 단계 앞에서 끊긴다
            return msg(calls=[("write_file", {"path": "b.txt", "content": "B"})])
        self.fake.script = [slow, msg("닿지 않음")]
        self.spawn("b.txt")
        h, r = self.hub.wait_report("w1", 1)
        self.assertEqual(h["reason"], "paused")
        self.assertTrue((self.wd / "b.txt").exists())        # 그 단계의 도구 호출은 끝냈다
        self.assertEqual(len(self.fake.requests), 1)
        self.hub.procs["w1"].wait(timeout=10)
        self.assertEqual(self.hub.sdir("w1").status()["status"], "paused")

    def test_names_and_dup(self):
        for bad in ("", "a b", "-x", "x" * 33, "../x"):
            with self.assertRaises(ValueError):
                self.hub.spawn(bad, "t", self.wd, str(self.tmp / "w.toml"), "fake")
        self.fake.script = [msg("끝")]
        self.spawn("t")
        with self.assertRaises(ValueError):
            self.spawn("t")


class TSessionDir(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.sd = H.SessionDir(self.tmp / "s")
        self.sd.make()

    def test_replace_and_urgent(self):
        a = self.sd.send("하나")
        b = self.sd.send("둘")
        u = self.sd.send("급함", kind="긴급")
        c = self.sd.send("둘 대신", kind="대체", replaces=b)
        self.assertEqual(self.sd.next_instruction()[0], a)
        self.sd.take(a)
        n, head, body = self.sd.next_instruction()
        self.assertEqual((n, head["kind"], body.strip()), (c, "대체", "둘 대신"))   # 바뀐 '둘' 은 건너뛴다
        self.sd.take(c)
        self.assertIsNone(self.sd.next_instruction())
        self.assertIn(b, self.sd.state()["dropped"])
        self.assertEqual([x[0] for x in self.sd.urgent_pending()], [u])
        self.sd.take(u, urgent=True)
        self.assertEqual(self.sd.urgent_pending(), [])
        with self.assertRaises(ValueError):
            self.sd.send("x", kind="대체")
        with self.assertRaises(ValueError):
            self.sd.send("x", kind="이상")

    def test_replace_after_taken_runs_next(self):
        a = self.sd.send("하나")
        self.sd.take(a)
        c = self.sd.send("하나 대신", kind="대체", replaces=a)
        self.assertEqual(self.sd.next_instruction()[0], c)       # 이미 넘어간 지시면 끝난 뒤 다음 지시로

    def test_ui_urgent_and_halt(self):
        ui = HubUI(self.sd, "s", None)
        self.sd.send("일")
        self.sd.send("급함", kind="긴급")
        self.assertEqual(ui.take_interjections(), ["[긴급 지시 2]\n급함"])
        self.assertEqual(ui.take_interjections(), [])
        self.sd.set_control("pause")
        with self.assertRaises(Halt):
            ui.take_interjections()
        with self.assertRaises(EOFError):                    # 통로가 없으면 묻기는 거부로
            HubUI(self.sd, "s", None).ask("실행할까? ")

    def test_pack_roundtrip(self):
        head, body = H.unpack(H.pack({"seq": 3, "kind": "지시"}, "본문\n둘째 줄"))
        self.assertEqual((head, body), ({"seq": 3, "kind": "지시"}, "본문\n둘째 줄\n"))
        self.assertEqual(H.unpack("머리 없음"), ({}, "머리 없음"))


if __name__ == "__main__":
    unittest.main()
