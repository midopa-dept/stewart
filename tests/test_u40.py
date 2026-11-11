"""판 54 기억 셋째 조각: 여러 프로젝트를 품은 작업 뿌리 아래 폴더별 프로젝트 기억(대상 가르기), 실사용 차례마다 엔진 자동 메모,
꿈꾸기 자동(한가할 때·닫을 때·새 메모 N개, 모델 서버가 준비되지 않았으면 미룸 — 깨우지 않음). 평가 길에는 섞이지 않는다."""

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from stewart import dream as DR, memory as MEM
from stewart.config import Profile
from tests.test_hub import msg
from tests.test_hub3 import MainBase
from tests.test_memory_m2 import Base as DreamBase, KEY, page


def prof(wd):
    return Profile(name="chat", path=wd / "p.toml", system="s", tools=["read_file"], workdir=wd)


class TTarget(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.root = self.tmp / "work"
        for n in ("alpha", "beta"):
            (self.root / n / ".git").mkdir(parents=True)
            (self.root / n / "a.py").write_text("x")
        (self.root / "plain").mkdir()
        p = mock.patch.dict(os.environ, {"STEWART_MEMORY_DIR": str(self.tmp / "mem"), "STEWART_HUB_CONFIG": str(self.tmp / "none.toml")})
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("STEWART_PROJECTS_ROOT", None)
        self.m = MEM.Memory(prof(self.root), self.tmp / "runs" / "r1")

    def test_pick_and_notes(self):
        self.assertEqual(self.m.here()["kind"], "dir")                                   # 처음은 뿌리 자리
        t = self.m.retarget([str(self.root / "alpha" / "a.py"), "beta/a.py", "alpha"])
        self.assertEqual((t["kind"], t["name"]), ("repo", "alpha"))                      # 가장 많이 만진 첫 단계 하위 저장소
        self.assertIsNone(self.m.retarget(["alpha/a.py"]))                                 # 같으면 바뀜 없음
        note = self.m.target_note()
        self.assertIn("이번 대상 프로젝트: alpha", note)
        self.assertEqual(self.m.target_note(), "")                                         # 한 번만
        self.assertTrue(self.m.note_add("시험은 make test", "make test exit 0", "confirmed")[0])
        self.assertEqual(self.m.turn_notes([{"command": "make lint", "exit": 2}, {"command": "make lint", "exit": 2},
                                            {"command": "ls", "exit": 0}]), 1)
        self.assertEqual(self.m.turn_notes([{"command": "make lint", "exit": 2}]), 0)       # 세션에서 같은 명령은 한 번
        notes = (self.tmp / "mem" / t["key"] / "notes" / "r1.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(x)["by"] for x in notes], ["model", "engine"])
        self.assertIsNone(MEM.project_of(self.root, "plain/x"))                           # git 아닌 하위 폴더는 설정이 있어야
        self.assertIsNone(MEM.project_of(self.root, "/elsewhere/x"))
        self.assertIsNone(MEM.project_of(self.root / "alpha", "a.py"))                    # 저장소 안이면 가르지 않는다
        with mock.patch.dict(os.environ, {"STEWART_PROJECTS_ROOT": str(self.root)}):
            self.assertEqual(MEM.project_of(self.root, "plain/x"), (self.root / "plain").resolve())

    def test_project_memory_shown_with_target(self):
        t = MEM.place_of(self.root / "beta")
        (self.tmp / "mem" / t["key"]).mkdir(parents=True)
        (self.tmp / "mem" / t["key"] / "MEMORY.md").write_text("- [함정] beta 는 python3.13 만\n", encoding="utf-8")
        self.m.retarget(["beta/a.py"])
        self.m.remember("feedback", "beta 에서는 커밋하지 마라", scope="project")
        note = self.m.target_note()
        self.assertIn("beta 는 python3.13 만", note)
        self.assertIn("beta 에서는 커밋하지 마라", note)


class TChatTurn(MainBase):
    """허브 메인이 뿌리에서 하위 저장소를 만지면 그 프로젝트 색인이 도구 결과에 한 번 붙고, 실패한 셸이 그 대상 메모로 쌓인다."""
    def setUp(self):
        self.memdir = tempfile.mkdtemp(prefix="mem-")
        p = mock.patch.dict(os.environ, {"STEWART_MEMORY": "1", "STEWART_MEMORY_DIR": self.memdir, "STEWART_MEMORY_EXTRACT": "0",
                                         "STEWART_DREAM_AUTO": "0"})
        p.start()
        self.addCleanup(p.stop)
        super().setUp()

    def test_turn(self):
        (self.wd / "proj" / ".git").mkdir(parents=True)
        (self.wd / "proj" / "x.txt").write_text("hello")
        self.r.scripts["MAIN"] = [msg(calls=[("read_file", {"path": "proj/x.txt"})]),
                                  msg(calls=[("run_shell", {"command": "ls proj/nothere"})]), msg("다 봤다")]
        self.main.send("proj 를 봐")
        self.assertTrue(self.main.wait_idle())
        tools = [m for m in self.main.state()["main_log"] if m["kind"] == "tool"]
        self.assertIn("[기억] 이번 대상 프로젝트: proj", tools[0].get("result") or "")
        key = MEM.place_of(self.wd / "proj")["key"]
        nd = Path(self.memdir) / key / "notes"
        got = [json.loads(x) for f in nd.glob("*.jsonl") for x in f.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(x["by"] == "engine" and "ls proj/nothere" in x["claim"] for x in got), (tools[1], got))


class TAuto(DreamBase):
    def test_due_ready_defer(self):
        self.assertEqual(DR.due(self.root, 3), [(KEY, 3)])
        self.assertEqual(DR.due(self.root, 4), [])
        r = DR.auto(self.root, self.ask(page("- 설정은 calc/conf.py (run r1)")), self.runs, ready=lambda: False, min_notes=3)
        self.assertTrue(r["status"].startswith("미룸"))
        self.assertEqual(self.calls, [])                                                   # 서버가 안 됐으면 모델을 부르지 않는다
        r = DR.auto(self.root, self.ask(page("- 설정은 calc/conf.py (run r1)")), self.runs, ready=lambda: True, min_notes=3)
        self.assertEqual(r["results"][0]["status"], "쪽·색인 바꿈")                         # 자동은 바로 들인다(diff 는 dream-log)
        self.assertTrue((self.sdir / "MEMORY.md").is_file())
        self.assertTrue(list((self.sdir / "dream-log").glob("*-diff.diff")))
        self.assertEqual(DR.due(self.root, 1), [])                                         # 새 메모 없음
        self.assertEqual(DR.auto(self.root, self.ask("x"), self.runs, ready=lambda: True)["status"], "할 것 없음")

    def test_chat_close(self):
        from stewart.chat import dream_at_close

        class S:
            pass
        s = S()
        s.mem = mock.Mock(dir=self.root, runs_dir=self.runs)
        s.backend = None
        for i in range(DR.AUTO_MIN_NOTES):
            self.note("r2", by="model", claim=f"더 {i}", status="confirmed")
        r = dream_at_close(s, None, ask_model=self.ask(page("- 설정은 calc/conf.py (run r1)")),
                           probe_fn=lambda b, timeout, pod: {"state": "down"})
        self.assertTrue(r["status"].startswith("미룸"))
        self.assertEqual(self.calls, [])


class THubDream(MainBase):
    def setUp(self):
        self.memdir = tempfile.mkdtemp(prefix="mem-")
        p = mock.patch.dict(os.environ, {"STEWART_MEMORY": "1", "STEWART_MEMORY_DIR": self.memdir})
        p.start()
        self.addCleanup(p.stop)
        super().setUp()

    def test_idle_and_close(self):
        sd = Path(self.memdir) / KEY / "notes"
        sd.mkdir(parents=True)
        (sd / "r1.jsonl").write_text("".join(json.dumps({"run": "r1", "by": "model", "claim": f"c{i}", "evidence": "e",
                                                         "status": "confirmed"}) + "\n" for i in range(6)), encoding="utf-8")
        calls = []
        ask = lambda s, u: calls.append(u) or page("- c0 (run r1)")
        self.assertIsNone(self.main.maybe_dream(ask_model=ask))                           # 막 켜졌다 — 한가하지 않음
        self.main._idle_since = time.monotonic() - DR.AUTO_IDLE_S - 1
        self.main.watch.status = dict(self.main.watch.status, state="down")
        r = self.main.maybe_dream(ask_model=ask)
        self.assertTrue(r["status"].startswith("미룸"))
        self.assertEqual(calls, [])
        self.assertIsNone(self.main.maybe_dream(ask_model=ask))                           # 다시 보기 간격
        self.main.watch.status = dict(self.main.watch.status, state="ok")
        r = self.main.maybe_dream(closing=True, ask_model=ask)                             # 닫을 때는 간격 없이
        self.assertEqual(r["results"][0]["status"], "쪽·색인 바꿈")
        self.assertTrue(any(m["kind"] == "memory" and "꿈꾸기" in m["text"] for m in self.main.state()["main_log"]))


if __name__ == "__main__":
    unittest.main()
