"""허브 H2: 대기열(묻는 종류·인자 요약)·알림 훅·보고 도착 알림·메인 도구 6개·worktree·허브 재시작."""

import json
import os
import subprocess
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from stewart import bounds as BND, config_guard as CG, hub as H, hubtools as HT, tools as T
from stewart.worker import args_summary, ask_kind
from tests.test_hub import Base, msg, wait


class TKinds(unittest.TestCase):
    def test_kind_from_real_prompts(self):
        self.assertEqual(ask_kind(BND.ASK_WRITE.format(paths="/x/y")), "outside_write")
        self.assertEqual(ask_kind("[작업자 1] " + BND.ASK_WRITE.format(paths="/x").lstrip()), "outside_write")
        self.assertEqual(ask_kind(BND.ASK_READ.format(path="/x")), "outside_read")
        self.assertEqual(ask_kind(CG.ASK_PROMPT.format(path="~/.config/stewart/a.toml")), "config")
        self.assertEqual(ask_kind("  단계 200개를 썼다(상한). 계속할까? [y/n] "), "more_steps")
        self.assertEqual(ask_kind("    실행할까? [y/n/모델에 할 말] "), "confirm")

    def test_args_summary(self):
        import tempfile, shutil
        wd = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(wd, ignore_errors=True))
        (wd / "a.txt").write_text("one\ntwo\n")
        (wd / ".env").write_text("TOKEN=zz\n")
        env = T.ToolEnv(wd)
        s = args_summary(env, "write_file", {"path": "a.txt", "content": "one\nTWO\n"})
        self.assertEqual(s["paths"], ["a.txt"])
        self.assertIn("-two", s["diff"])
        self.assertIn("+TWO", s["diff"])
        s = args_summary(env, "edit_file", {"path": "a.txt", "old": "one", "new": "ONE"})
        self.assertIn("+ONE", s["diff"])
        s = args_summary(env, "multi_edit", {"path": "a.txt", "edits": [{"old": "one", "new": "1"}, {"old": "two", "new": "2"}]})
        self.assertIn("+1", s["diff"])
        self.assertIn("+2", s["diff"])
        big = "\n".join(f"l{i}" for i in range(200))
        self.assertLessEqual(len(args_summary(env, "write_file", {"path": "b.txt", "content": big})["diff"]), 400)   # 화면이 40줄 뒤를 접는다(U2-5)
        s = args_summary(env, "write_file", {"path": ".env", "content": "TOKEN=new\n"})
        self.assertIsNone(s["diff"])                                        # 비밀 파일은 내용을 읽지도 싣지도 않는다
        self.assertNotIn("zz", json.dumps(s))
        cmd = args_summary(env, "run_shell", {"command": "x" * 5000})["command"]
        self.assertTrue(cmd.startswith("x" * 2000) and "명령 5000자 중 앞 2000자만 보임" in cmd)   # 잘린 것을 표시(U2-1)
        self.assertEqual(args_summary(env, "edit_file", {"path": "a.txt", "old": "없음", "new": "y"})["diff"], None)


class TQueueNotify(Base):
    def setUp(self):
        super().setUp()
        self.sent = []
        self.hub = H.Hub(self.tmp / "hub2", self.tmp / "runs", notify=self.sent.append)
        self.addCleanup(self.hub.close)

    def test_queue_kind_summary_and_notify(self):
        self.fake.script = [msg(calls=[("run_shell", {"command": "touch secret-cmd-out.txt"})]), msg("돌렸다")]
        self.spawn("셸", mode="edits")
        q = wait(lambda: self.hub.queue())
        self.assertEqual((q[0]["session"], q[0]["kind"], q[0]["seq"]), ("w1", "confirm", 1))
        self.assertEqual(q[0]["args_summary"]["command"], "touch secret-cmd-out.txt")
        r = self.hub.tick()
        self.assertEqual(r["notified"], ["w1: run_shell 승인 대기"])          # 명령·경로 없음
        self.assertEqual(self.sent, ["w1: run_shell 승인 대기"])
        self.hub.tick()
        self.assertEqual(len(self.sent), 1)                                   # 같은 질문은 한 번만
        self.hub.answer("w1", 1, "y")
        self.hub.wait_report("w1", 1)
        self.assertEqual(self.hub.queue(), [])
        r = self.hub.tick()
        self.assertEqual(r["reports"], ["[허브] w1 보고 0001 도착 — session_read_report(name='w1', seq=1)"])
        self.assertEqual(self.hub.take_notices(), r["reports"])
        self.assertEqual(self.hub.take_notices(), [])
        self.assertEqual(self.hub.tick()["reports"], [])                      # 보고 알림도 한 번만

    def test_outside_read_kind_and_paths(self):
        # 시험 폴더는 임시 폴더 아래라 밖 쓰기는 늘 허용된다(bounds.temp_dirs). 묻는 자리는 밖 읽기(read_outside=ask)로 본다
        out = self.tmp / "outside.txt"
        out.write_text("밖")
        ro = self.tmp / "ro.toml"
        ro.write_text((self.tmp / "w.toml").read_text() + 'read_outside = "ask"\n')
        self.fake.script = [msg(calls=[("read_file", {"path": str(out)})]), msg("거절당했다")]
        self.hub.spawn("w1", "밖을 읽어라", self.wd, str(ro), "fake", mode="edits")
        q = wait(lambda: self.hub.queue())
        self.assertEqual(q[0]["kind"], "outside_read")
        self.assertEqual(q[0]["args_summary"]["paths"], [str(out)])
        self.hub.tick()
        self.assertEqual(self.sent, ["w1: 밖 읽기 승인 대기"])                  # 경로 없음
        self.hub.answer("w1", 1, "n")
        h, _ = self.hub.wait_report("w1", 1)
        req = json.dumps(self.fake.requests[-1]["messages"], ensure_ascii=False)
        self.assertNotIn('"밖"', req)                                           # 읽지 않았다

    def test_notify_failure_logged_once(self):
        def boom(text):
            raise OSError("down")
        hub = H.Hub(self.tmp / "hub3", self.tmp / "runs", notify=boom)
        self.addCleanup(hub.close)
        self.fake.script = [msg(calls=[("run_shell", {"command": "touch t.txt"})]), msg("끝")]
        hub.spawn("w1", "셸", self.wd, str(self.tmp / "w.toml"), "fake", mode="edits")
        wait(lambda: hub.queue())
        hub.tick()
        hub.tick()
        ev = [json.loads(l) for l in (hub.dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual([e["kind"] for e in ev].count("notify_fail"), 1)
        hub.answer("w1", 1, "y")
        hub.wait_report("w1", 1)

    def test_notify_url_posts_plain_text(self):
        got = []

        class Hd(BaseHTTPRequestHandler):
            def do_POST(self):
                got.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Hd)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        hub = H.Hub(self.tmp / "hub4", self.tmp / "runs", notify_url=f"http://127.0.0.1:{srv.server_address[1]}/t")
        self.addCleanup(hub.close)
        self.fake.script = [msg(calls=[("run_shell", {"command": "cat /etc/hosts"})]), msg("끝")]
        hub.spawn("w1", "셸", self.wd, str(self.tmp / "w.toml"), "fake", mode="edits")
        wait(lambda: hub.queue())
        hub.tick()
        self.assertEqual(got, ["w1: run_shell 승인 대기"])
        self.assertIsNone(H.Hub(self.tmp / "hub5", self.tmp / "runs", notify_url="  ").notify_url)   # 비우면 끔
        hub.answer("w1", 1, "n")
        hub.wait_report("w1", 1)


class TMainTools(Base):
    def setUp(self):
        super().setUp()
        self.mt = HT.MainTools(self.hub, main_mode="edits", default_profile=str(self.tmp / "w.toml"), default_backend="fake")

    def test_no_answer_tool(self):
        names = [d["function"]["name"] for d in HT.DEFINITIONS]
        self.assertEqual(tuple(names), HT.NAMES)
        self.assertFalse([n for n in names if "answer" in n or "approv" in n])
        self.assertFalse(hasattr(self.mt, "answer"))
        self.assertFalse(self.mt.execute("answer", {"name": "w1", "seq": 1, "text": "y"})[0])

    def test_spawn_send_read_list_stop_resume(self):
        self.fake.script = [msg("보고 하나"), msg("보고 둘")]
        ok, out = self.mt.execute("session_spawn", {"name": "w1", "task": "일 하나", "workdir": str(self.wd)})
        self.assertTrue(ok, out)
        self.hub.wait_report("w1", 1)
        ok, out = self.mt.execute("session_send", {"name": "w1", "text": "일 둘"})
        self.assertEqual((ok, out), (True, "w1 에 지시 0002(지시)를 보냈다."))
        self.hub.wait_report("w1", 2)
        ok, out = self.mt.execute("session_read_report", {"name": "w1"})
        self.assertTrue(out.startswith("보고 하나"), out)
        self.assertIn("[보고 0001 · 종료 answered", out)
        ok, out = self.mt.execute("session_read_report", {"name": "w1"})
        self.assertTrue(out.startswith("보고 둘"))
        self.assertFalse(self.mt.execute("session_read_report", {"name": "w1"})[0])     # 다 읽음
        self.assertTrue(self.mt.execute("session_read_report", {"name": "w1", "seq": 1})[1].startswith("보고 하나"))
        wait(lambda: self.hub.sdir("w1").status().get("status") == "idle")
        rows = json.loads(self.mt.execute("session_list", {})[1])
        self.assertEqual((rows[0]["name"], rows[0]["status"], rows[0]["last_report"], rows[0]["unread_reports"]),
                         ("w1", "idle", 2, []))
        self.assertEqual(self.mt.execute("session_stop", {"name": "w1"}), (True, "w1: paused"))
        self.fake.script = [msg("보고 셋")]
        self.mt.execute("session_send", {"name": "w1", "text": "일 셋"})
        self.assertTrue(self.mt.execute("session_resume", {"name": "w1"})[0])
        self.hub.wait_report("w1", 3)
        self.assertEqual(self.mt.execute("session_stop", {"name": "w1", "how": "stop"}), (True, "w1: stopped"))
        self.assertTrue(self.mt.execute("session_resume", {"name": "w1"})[0])           # 끝낸 세션도 앞 대화로 다시 연다(U2-2 2b)
        self.hub.stop("w1", 10)
        self.assertFalse(self.mt.execute("session_send", {"name": "nope", "text": "x"})[0])
        self.assertFalse(self.mt.execute("session_spawn", {"name": "w1", "task": "x", "workdir": str(self.wd)})[0])

    def test_mode_not_wider_than_main(self):
        ok, out = self.mt.execute("session_spawn", {"name": "w2", "task": "x", "workdir": str(self.wd), "mode": "auto"})
        self.assertFalse(ok)
        self.assertIn("넓을 수 없다", out)
        self.assertNotIn("w2", self.hub.registry()["sessions"])

    def test_worktree_is_the_boundary(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        ge = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        for c in (["git", "init", "-q", "-b", "main"], ["git", "commit", "-q", "--allow-empty", "-m", "i"]):
            subprocess.run(c, cwd=repo, check=True, env=ge)
        (repo / "keep.txt").write_text("main")
        ro = self.tmp / "ro.toml"
        ro.write_text((self.tmp / "w.toml").read_text() + 'read_outside = "ask"\n')
        self.fake.script = [msg(calls=[("write_file", {"path": "w.txt", "content": "in"})]),
                            msg(calls=[("read_file", {"path": str(repo / "keep.txt")})]), msg("끝")]
        ok, out = self.mt.execute("session_spawn", {"name": "wt", "task": "worktree 안에서", "mode": "edits",
                                                    "profile": str(ro),
                                                    "worktree": {"repo": str(repo), "branch": "work/wt"}})
        self.assertTrue(ok, out)
        e = self.hub.registry()["sessions"]["wt"]
        wt = Path(e["worktree"]["path"])
        self.assertEqual((e["workdir"], e["worktree"]["branch"]), (str(wt), "work/wt"))
        q = wait(lambda: self.hub.queue())
        self.assertEqual(q[0]["kind"], "outside_read")                         # 원래 체크아웃은 경계 밖
        self.hub.answer("wt", 1, "n")
        self.hub.wait_report("wt", 1)
        self.assertEqual((wt / "w.txt").read_text(), "in")
        self.assertEqual((repo / "keep.txt").read_text(), "main")
        br = subprocess.run(["git", "branch", "--list", "work/wt"], cwd=repo, capture_output=True, text=True).stdout
        self.assertIn("work/wt", br)
        for bad in ({"repo": str(self.wd), "branch": "x"}, {"repo": str(repo), "branch": "-x"},
                    {"repo": str(repo), "branch": "a..b"}, {"repo": str(repo), "branch": "y", "base": "--force"}):
            self.assertFalse(self.mt.execute("session_spawn", {"name": "bad", "task": "x", "worktree": bad})[0], bad)


class TRestart(Base):
    def test_restart_pauses_and_resumes_with_new_pipe(self):
        self.fake.script = [msg("첫 보고")]
        self.spawn("첫 일", mode="edits")
        self.hub.wait_report("w1", 1)
        old_pid = self.hub.procs["w1"].pid
        hub2 = H.Hub(self.hub.dir, self.tmp / "runs")                         # 허브가 다시 떴다(통로는 옛 허브 메모리에만)
        self.addCleanup(hub2.close)
        self.assertEqual(hub2.recover(), ["w1"])
        self.hub.procs["w1"].wait(timeout=10)
        self.assertNotEqual(hub2.procs["w1"].pid, old_pid)
        self.assertIn("w1", hub2.pipes)
        self.fake.script = [msg(calls=[("run_shell", {"command": "touch z.txt"})]), msg("둘째 보고")]
        hub2.send("w1", "셸")
        q = wait(lambda: hub2.queue())
        hub2.answer("w1", q[0]["seq"], "y")                                    # 새 통로로 승인
        h, r = hub2.wait_report("w1", 2)
        self.assertEqual(r.strip(), "둘째 보고")
        self.assertTrue((self.wd / "z.txt").exists())
        self.hub.procs.clear()
        self.hub.pipes.clear()


if __name__ == "__main__":
    unittest.main()
