"""판 95(SW-41 ②): teacher 돌리기 — 과제 풀 TOML 검사, 회차마다 시작 폴더 복사·engine.run(verify_command = 과제 verify)·test_result 사건,
비용·회차 상한, 통과 궤적만 export --select passed --track teacher 로(엔진은 각본 모델)."""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import engine, teacher as TC, train as TRAIN
from stewart.config import Backend, Profile
from tests.test_u60 import resp


def pool_at(tmp: Path) -> Path:
    for d in ("a", "b"):
        (tmp / "tasks" / d).mkdir(parents=True)
        (tmp / "tasks" / d / "README").write_text("시작", encoding="utf-8")
    p = tmp / "pool.toml"
    p.write_text('name = "공개 묶음"\n'
                 '[[task]]\nid = "make-done"\nprompt = "done.txt 를 만들어라"\nstart = "tasks/a"\nverify = "test -f done.txt"\n'
                 '[[task]]\nid = "never"\nprompt = "아무것도 하지 마라"\nstart = "tasks/b"\nverify = "test -f nope.txt"\nmax_steps = 3\n',
                 encoding="utf-8")
    return p


def scripted(backend, messages, tools=None, *a, **k):
    n = sum(1 for m in messages if m["role"] == "assistant")
    task = next(m["content"] for m in messages if m["role"] == "user")
    if "done.txt" in str(task) and n == 0:
        r, dt = resp(calls=[("write_file", {"path": "done.txt", "content": "ok"})])
    else:
        r, dt = resp("끝냈다")
    r["usage"] = {"prompt_tokens": 10, "completion_tokens": 5, "cost_usd": 0.01}
    return r, dt


class TPool(unittest.TestCase):
    def test_checks(self):
        tmp = Path(tempfile.mkdtemp())
        self.assertEqual([t["id"] for t in TC.load_pool(pool_at(tmp))["tasks"]], ["make-done", "never"])
        bad = tmp / "bad.toml"
        for body in ('[[task]]\nid = "x y"\nprompt = "p"\nstart = "tasks/a"\nverify = "true"\n',
                     '[[task]]\nid = "x"\nprompt = "p"\nstart = "nowhere"\nverify = "true"\n',
                     '[[task]]\nid = "x"\nprompt = "p"\nstart = "tasks/a"\nverify = "true"\n[[task]]\nid = "x"\nprompt = "q"\nstart = "tasks/a"\nverify = "true"\n',
                     'name = "빈 것"\n'):
            bad.write_text(body, encoding="utf-8")
            with self.assertRaises(TC.PoolError):
                TC.load_pool(bad)


class TRun(unittest.TestCase):
    def test_run_export_and_caps(self):
        tmp = Path(tempfile.mkdtemp())
        pool = TC.load_pool(pool_at(tmp))
        prof = Profile(name="code", path=tmp / "p.toml", system="s", tools=["write_file", "read_file"], workdir=tmp, max_steps=5)
        bk = Backend("ds", "https://api.deepseek.com/v1", "deepseek-chat", "-", 100000, provider="api.deepseek.com")
        out = tmp / "runs" / "teacher-1"
        with mock.patch.object(engine, "chat", side_effect=scripted):
            s = TC.run_pool(pool, prof, bk, out, repeat=2, say=lambda *_: None)
        self.assertEqual((s["runs"], s["passed"]), (4, 2))
        self.assertGreater(s["cost_usd"], 0)
        self.assertTrue((out / "work" / "make-done-1" / "done.txt").is_file())
        self.assertFalse((tmp / "tasks" / "a" / "done.txt").exists())                # 시작 폴더는 그대로(복사본에서)
        ev = [json.loads(x) for d in out.iterdir() if (d / "events.jsonl").is_file()
              for x in (d / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sorted(e["passed"] for e in ev if e["kind"] == "test_result"), [False, False, True, True])
        st = TRAIN.export(out, tmp / "ex", "passed", track="teacher")
        self.assertEqual(st["written"], 2)
        rec = json.loads((tmp / "ex" / "train.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual((rec["meta"]["track"], rec["meta"]["test_pass"], rec["meta"]["license_tier"]), ("teacher", True, "train-ok"))
        self.assertIn("write_file", [d["function"]["name"] for d in rec["tools"]])
        with mock.patch.object(engine, "chat", side_effect=scripted):
            s = TC.run_pool(pool, prof, bk, tmp / "runs" / "teacher-2", repeat=5, max_cost=0.025, say=lambda *_: None)
        self.assertIn("비용 상한", s["stopped"])
        self.assertLess(s["runs"], 10)
        with mock.patch.object(engine, "chat", side_effect=scripted):
            s = TC.run_pool(pool, prof, bk, tmp / "runs" / "teacher-3", repeat=5, max_runs=3, say=lambda *_: None)
        self.assertEqual(s["runs"], 3)

    def test_cli_dry_run_says_where(self):
        tmp = Path(tempfile.mkdtemp())
        p = pool_at(tmp)
        (tmp / "backends.toml").write_text('default = "ds"\n[backends.ds]\nbase_url = "https://api.deepseek.com/v1"\nmodel = "deepseek-chat"\n'
                                          'provider = "api.deepseek.com"\nmax_context = 100000\n', encoding="utf-8")
        from stewart import cli
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"STEWART_BACKENDS": str(tmp / "backends.toml")}), mock.patch("sys.stdout", buf):
            self.assertEqual(cli.main(["train", "teacher", "--pool", str(p), "-b", "ds", "--repeat", "3", "--dry-run",
                                       "--runs-dir", str(tmp / "runs")]), 0)
        self.assertIn("회차 6개", buf.getvalue())
        self.assertIn("api.deepseek.com 로 나간다", buf.getvalue())
        self.assertFalse((tmp / "runs").exists())


if __name__ == "__main__":
    unittest.main()
