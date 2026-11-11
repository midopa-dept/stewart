"""U2-8 지난 세션 정리: 지우기(휴지통)·되돌리기·비우기·이름·보관, 도는 세션은 막기, 작업 폴더는 그대로, CLI."""

import io
import json
import os
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from stewart import cli
from stewart.trash import Manager
from tests.test_hub3 import MainBase


class Base(MainBase):
    def setUp(self):
        super().setUp()
        self.runs = Path(self.main._runs_dir)

    def fake_worker(self, name, status="stopped", last="2026-11-01T10:00:00"):
        run = self.runs / f"20261101-1000-{name}"
        run.mkdir(parents=True)
        (run / "messages.json").write_text("[]")
        sd = Path(self.hub.dir) / "sessions" / name
        (sd / "inbox").mkdir(parents=True)
        (sd / "inbox" / "0001.md").write_text("첫 지시\n")
        reg = self.hub.registry()
        reg["sessions"][name] = {"name": name, "role": "worker", "workdir": str(self.wd), "status": status, "run_dir": str(run),
                                 "last_event": last}
        self.hub._save(reg)
        return run, sd

    def fake_main(self, tag="old"):
        d = self.runs / f"20261101-0900-hub-main-{tag}"
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text(json.dumps({"meta": {"workdir": str(self.wd), "started": "2026-11-01T09:00:00"}}) + "\n")
        (d / "messages.json").write_text(json.dumps([{"role": "system", "content": "s"}, {"role": "user", "content": "예전 일"}]))
        return d


class TManager(Base):
    def test_delete_restore_empty_worker(self):
        (self.wd / "결과.txt").write_text("작업 폴더 파일")
        run, sd = self.fake_worker("w-old")
        mg = self.main.manager()
        self.assertEqual([w["name"] for w in mg.workers()], ["w-old"])
        tid = mg.delete("worker", "w-old")
        self.assertFalse(run.exists() or sd.exists())
        self.assertNotIn("w-old", self.hub.registry()["sessions"])
        self.assertTrue((self.wd / "결과.txt").is_file())                    # 작업 폴더는 그대로
        self.assertEqual([t["key"] for t in mg.trash()], ["w-old"])
        r = mg.restore(tid)
        self.assertEqual(r["key"], "w-old")
        self.assertTrue(run.is_dir() and (sd / "inbox" / "0001.md").is_file())
        self.assertEqual(self.hub.registry()["sessions"]["w-old"]["status"], "stopped")
        self.assertEqual(mg.trash(), [])
        mg.delete("worker", "w-old")
        self.assertEqual(mg.empty(), 1)
        self.assertEqual(mg.trash(), [])
        self.assertFalse(run.exists())

    def test_live_refused_and_current_main(self):
        self.fake_worker("w-live", status="working")
        mg = self.main.manager()
        with self.assertRaises(RuntimeError):
            mg.delete("worker", "w-live")
        with self.assertRaises(RuntimeError):
            mg.delete("main", str(self.main.sess.rec.dir))
        with self.assertRaises(ValueError):
            mg.delete("main", "/어딘가/남의/기록")

    def test_main_rename_archive_older(self):
        d = self.fake_main()
        self.fake_worker("w-new", last=time.strftime("%Y-%m-%dT%H:%M:%S"))
        mg = self.main.manager()
        self.assertEqual([m["run_dir"] for m in mg.mains()], [str(d)])
        mg.rename("main", str(d), "  지난주   일  ")
        mg.archive("main", str(d))
        m = mg.mains()[0]
        self.assertEqual((m["title"], m["archived"]), ("지난주 일", True))
        self.assertEqual(mg.older_than(3), [("main", str(d))])              # w-new 는 오늘 것
        tid = mg.delete("main", str(d))
        self.assertFalse(d.exists())
        self.assertEqual(mg.mains(), [])
        mg.restore(tid)
        m = mg.mains()[0]
        self.assertEqual((m["title"], m["archived"]), ("지난주 일", True))   # 이름·보관도 함께 돌아온다

    def test_restore_conflict(self):
        self.fake_worker("w1")
        mg = self.main.manager()
        tid = mg.delete("worker", "w1")
        self.fake_worker("w1")
        with self.assertRaises(RuntimeError):
            mg.restore(tid)


class TWeb(Base):
    def test_manage_endpoint(self):
        from stewart.web import WebApp
        self.fake_worker("w-a")
        self.fake_worker("w-b")
        d = self.fake_main()
        web = WebApp(self.main, self.hub, "tok")
        self.addCleanup(web.httpd.server_close)
        p = web.past_sessions()
        self.assertEqual(sorted(w["name"] for w in p["workers"]), ["w-a", "w-b"])
        self.assertEqual(p["workers"][0]["first"], "첫 지시")
        mg = self.main.manager()
        mg.archive("worker", "w-b")
        p = web.past_sessions()
        self.assertEqual(([w["name"] for w in p["workers"]], p["archived_count"]), (["w-a"], 1))
        self.assertEqual([w["name"] for w in web.past_sessions(archived=True)["workers"]], ["w-b"])
        mg.delete("main", str(d))
        self.assertEqual(len(web.past_sessions()["trash"]), 1)


class TCli(Base):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["sessions", *args, "--project", str(self.wd), "--state-dir", str(self.hub.dir), "--runs-dir", str(self.runs)])
        return rc, out.getvalue() + err.getvalue()

    def test_cli(self):
        self.fake_worker("w-cli")
        d = self.fake_main()
        rc, o = self.run_cli("list")
        self.assertEqual(rc, 0)
        self.assertIn("w-cli", o)
        self.assertIn(d.name, o)
        rc, o = self.run_cli("delete", "w-cli", d.name)
        self.assertEqual(rc, 0, o)
        self.assertIn("휴지통으로", o)
        rc, o = self.run_cli("trash")
        tid = next(line.split()[0] for line in o.splitlines() if "w-cli" in line)
        self.assertEqual(self.run_cli("restore", tid)[0], 0)
        self.assertIn("w-cli", self.hub.registry()["sessions"])
        self.assertEqual(self.run_cli("empty")[0], 2)                         # --yes 없이는 안 비운다
        rc, o = self.run_cli("empty", "--yes")
        self.assertIn("1개", o)
        self.assertEqual(self.run_cli("delete", "없는것")[0], 1)
        self.assertEqual(self.run_cli("rename", "w-cli", "새", "이름")[0], 0)
        self.assertEqual(Manager(self.hub, self.runs, self.wd).workers()[0]["title"], "새 이름")


class TStaleMainAsk(Base):
    def test_restart_cancels_old_main_ask(self):
        from stewart import hub as H
        from stewart.cli import resolve
        from stewart.hubmain import HubMain
        from stewart.record import Recorder
        H.write_json(self.hub.sdir("main").ask_dir / H.seq_name(1, "json"),
                     {"seq": 1, "session": "main", "kind": "confirm", "prompt": "p", "args_summary": {"tool": "run_shell", "command": "ls"}})
        self.assertEqual(len(self.hub.queue()), 1)
        self.main.close()
        hub2 = H.Hub(self.hub.dir, self.tmp / "runs")
        profile, backend = resolve(str(self.tmp / "main.toml"), "fake", str(self.wd))
        m2 = HubMain(hub2, profile, backend, Recorder(self.tmp / "runs", "hub-main"), mode="edits")
        self.addCleanup(m2.close)
        self.assertEqual(hub2.queue(), [])                                     # 다시 켠 허브에는 앞 메인의 승인 건이 없다
