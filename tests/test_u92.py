"""판 114: chat 무인 측정 꼴 — stewart chat --task-file·--once(사람 없이 한 차례, 끝에 verify_command 로 success)·--tier(허브와 같은 등급,
얇은 꼴 포함). summary 는 run 길과 같은 칸(stop_reason·verify·success·tool_error_kinds·features·asked), output.md. 대화형 chat 의 success 는
마지막 차례가 답으로 끝났을 때만 참(전에는 늘 참)."""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, cli
from tests.test_u60 import resp

TABLE = ('default = "big"\n[backends.big]\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "big-m"\nmax_context = 100000\n'
         '[backends.small]\nbase_url = "http://127.0.0.1:8/v1"\nmodel = "small-m"\nmax_context = 32768\n'
         '[tiers.thin]\nbackend = "small"\nslim = true\nbudget = 16000\n')


class TOnce(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "b.toml").write_text(TABLE, encoding="utf-8")
        self.wd = self.tmp / "wd"
        self.wd.mkdir()
        (self.wd / "a.txt").write_text("hello\n", encoding="utf-8")
        (self.tmp / "p.toml").write_text(
            'name = "pt"\ntools = ["read_file", "edit_file", "write_file", "run_shell"]\nmax_steps = 8\nsystem = "s"\n'
            'verify_command = "grep -q bye a.txt"\n', encoding="utf-8")
        (self.tmp / "task.md").write_text("a.txt 의 hello 를\nbye 로 바꿔라", encoding="utf-8")
        e = mock.patch.dict(os.environ, {"STEWART_BACKENDS": str(self.tmp / "b.toml"), "STEWART_MEMORY": "0"})
        e.start()
        self.addCleanup(e.stop)

    def cli(self, seq, *extra):
        seen = []

        def fake(backend, messages, *a, **k):
            seen.append((backend.name, [d["function"]["name"] for d in (k.get("tools") or (a[0] if a else []) or [])], messages))
            return seq.pop(0)
        buf = io.StringIO()
        with mock.patch.object(C, "chat", side_effect=fake), mock.patch("sys.stdout", buf), mock.patch("sys.stdin", io.StringIO("")):
            code = cli.main(["chat", "-p", str(self.tmp / "p.toml"), "--workdir", str(self.wd), "--runs-dir", str(self.tmp / "runs"),
                             "--no-stream", *extra])
        line = [x for x in buf.getvalue().splitlines() if x.startswith("{")][-1]
        return code, json.loads(line), seen

    def summary(self, run_dir):
        return json.loads((Path(run_dir) / "summary.json").read_text(encoding="utf-8"))

    def test_once_verify_pass(self):
        seq = [resp(calls=[("edit_file", {"path": "a.txt", "old": "hello", "new": "bye"})]), resp("바꿨다")]
        code, out, seen = self.cli(seq, "--once", "--task-file", str(self.tmp / "task.md"))
        self.assertEqual((code, out["stop_reason"], out["success"]), (0, "completed", True))
        s = self.summary(out["run_dir"])
        self.assertTrue(s["verify"]["passed"])
        self.assertEqual(s["exec_mode"], "auto")                                   # --mode 없으면 auto
        self.assertIn("features", s)
        self.assertEqual(s["tool_error_kinds"], {})
        self.assertIn("bye 로 바꿔라", json.dumps(seen[0][2], ensure_ascii=False))   # 과제 파일 여러 줄이 한 입력으로
        self.assertEqual((Path(out["run_dir"]) / "output.md").read_text(encoding="utf-8"), "바꿨다")

    def test_once_verify_fail_and_edit_mismatch(self):
        seq = [resp(calls=[("edit_file", {"path": "a.txt", "old": "없는 글", "new": "bye"})]), resp("못 했다")]
        code, out, _ = self.cli(seq, "--once", "--task-file", str(self.tmp / "task.md"))
        self.assertEqual((out["stop_reason"], out["success"]), ("completed", False))   # 답으로 끝났어도 판정 실패면 거짓
        self.assertEqual(self.summary(out["run_dir"])["tool_error_kinds"], {"edit_mismatch": 1})

    def test_once_asked(self):
        seq = [resp(calls=[("ask_user", {"question": "어느 파일인가?"})])]
        (self.tmp / "p.toml").write_text((self.tmp / "p.toml").read_text(encoding="utf-8").replace('"run_shell"]', '"run_shell", "ask_user"]'),
                                         encoding="utf-8")
        code, out, _ = self.cli(seq, "--once", "고쳐 줘")
        s = self.summary(out["run_dir"])
        self.assertEqual((code, s["stop_reason"]), (0, "asked"))
        self.assertIn("어느 파일", s["asked"])

    def test_once_needs_task(self):
        with mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(cli.main(["chat", "-p", str(self.tmp / "p.toml"), "--workdir", str(self.wd), "--once",
                                       "--runs-dir", str(self.tmp / "runs")]), 2)

    def test_tier_slim(self):
        seq = [resp("네")]
        code, out, seen = self.cli(seq, "--once", "--tier", "thin", "-p", "chat", "안녕")
        self.assertEqual(seen[0][0], "small")
        s = self.summary(out["run_dir"])
        self.assertEqual(s["backend"], "small")
        self.assertTrue(seen[0][1])
        self.assertLessEqual(set(seen[0][1]), {"read_file", "list_dir", "search", "edit_file", "write_file", "run_shell", "ask_user",
                                               "workers", "view_image", "load_skill"})   # 얇은 꼴: 핵심 도구만

    def test_interactive_success_not_always_true(self):
        from stewart.config import Backend, Profile
        from stewart.record import Recorder
        from tests.test_cloud import FakeUI
        p = Profile(name="pt", path=self.tmp / "p.toml", system="s", tools=["read_file"], workdir=self.wd, max_steps=8)
        s = C.ChatSession(p, Backend("big", "http://127.0.0.1:9/v1", "big-m", "q", 100000), Recorder(self.tmp / "r2", "c"), FakeUI(),
                          stream=False, mode="auto")
        self.assertFalse(s.summary()["success"])                                   # 차례가 없으면 거짓
        with mock.patch.object(C, "chat", side_effect=[resp("네")]):
            s.turn("안녕")
        sm = s.close()
        self.assertEqual((sm["stop_reason"], sm["success"], sm["last_turn_reason"]), ("chat_end", True, "answered"))


class TCost(unittest.TestCase):
    def test_chat_slim_cost(self):
        from stewart import ctxcost as CC
        r = CC.measure("chat", clean=True, slim=True)
        self.assertFalse(r["hub"])
        self.assertLess(r["total"], 5000)
        self.assertIn("얇은 chat", CC.table([r]))


class TKnob(unittest.TestCase):
    def test_compact_window_in_fingerprint(self):   # 판 114.1: 평가 창 1 W 칸을 지문으로 가른다
        from stewart import features as FT
        with mock.patch.dict(os.environ, {"STEWART_COMPACT_WINDOW": "24000"}):
            self.assertEqual(FT.fingerprint("s", [], "run")["knobs"].get("STEWART_COMPACT_WINDOW"), "24000")


if __name__ == "__main__":
    unittest.main()
