"""판 94(SW-41 ④ 판 C): 배워 두기의 스킬 초안(이름 꼴·단계 수·겹침·초안 자리·되돌리면 지움)과 효과 보기(예시가 붙은 차례 대 안 붙은 차례,
예시별 쓰임·지움 후보·안 쓰인 예시) — 모델 없이."""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import examples as EX, improve as IM
from tests.test_u67 import mem_at
from tests.test_u68 import write_events
from tests.test_u72 import runs_with_labels

SK = {"op": "skill", "name": "fix-failing-tests", "when": "시험이 깨졌을 때", "steps": ["pytest -q -x 로 첫 실패만 본다", "그 파일을 읽고 고친다", "다시 돌린다"],
      "src": "T1"}


class TSkill(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.runs = runs_with_labels(self.tmp / "runs")
        self.m = mem_at(self.tmp)
        e = mock.patch.dict(os.environ, {"STEWART_SKILLS_DIR": str(self.tmp / "skills")})
        e.start()
        self.addCleanup(e.stop)

    def test_parse_apply_revert(self):
        picked = IM.collect(self.runs, self.m.store.dir)
        bad = [{**SK, "name": "Fix Tests"}, {**SK, "steps": ["하나"]}, {**SK, "when": ""}, {**SK, "name": "taken-one"}]
        plan = IM.parse("\n".join(json.dumps(x, ensure_ascii=False) for x in bad + [SK]), picked, [], [], taken={"taken-one"})
        self.assertEqual([(p["op"], p["title"]) for p in plan], [("skill", "fix-failing-tests")])
        self.assertIn("description: 시험이 깨졌을 때", plan[0]["body"])
        self.assertIn("스킬 초안", IM.describe(plan[0]))
        r = IM.apply(self.m, plan, None, picked)
        f = self.m.store.dir / IM.DRAFTS / "fix-failing-tests" / "SKILL.md"
        self.assertTrue(f.is_file())
        from stewart import skills
        meta, body = skills.parse(f.read_text(encoding="utf-8"))
        self.assertEqual(meta["name"], "fix-failing-tests")
        self.assertIn("1. pytest -q -x", body)
        self.assertFalse((self.tmp / "skills").exists())                         # 켜는 것은 사람(초안 자리에만)
        self.assertEqual(r["drafts"], [str(f.parent)])
        self.assertIn("fix-failing-tests", IM.skill_names(self.m.store.dir))      # 다음 제안과 겹침 검사
        IM.revert(self.m.store.dir, Path(r["backup"]).name)
        self.assertFalse(f.parent.exists())


class TStats(unittest.TestCase):
    def test_with_without_and_candidates(self):
        tmp = Path(tempfile.mkdtemp())
        mem = tmp / "mem"
        a = EX.add(mem, "pytest 실패", "상황: a\n한 일: b\n결과: c")
        b = EX.add(mem, "안 쓰인 예시", "상황: a\n한 일: b\n결과: c")
        ev = [{"kind": "run_start", "meta": {}, "messages": []}]
        for t in range(1, 5):   # 예시가 붙은 차례 넷: 셋 교정, 하나 👍
            ev += [{"kind": "example_used", "id": a["id"], "score": 0.5}, {"kind": "user", "turn": t, "content": f"pytest {t}"},
                   {"kind": "turn_end", "turn": t, "reason": "answered"}]
        ev += [{"kind": "user", "turn": 5, "content": "다른 일"}, {"kind": "turn_end", "turn": 5, "reason": "answered"},
               {"kind": "train_label", "turn": 5, "label": "good", "by": "human"}]
        ev += [{"kind": "train_label", "turn": t, "label": "corrected", "by": "rule"} for t in (1, 2, 3)]
        ev += [{"kind": "train_label", "turn": 4, "label": "good", "by": "human"}]
        write_events(tmp / "runs" / "20261112-120000-hub-main", ev)
        st = IM.stats(tmp / "runs", mem)
        self.assertEqual(st["groups"]["with"], {"turns": 4, "good": 1, "corrected": 3})
        self.assertEqual(st["groups"]["without"], {"turns": 1, "good": 1, "corrected": 0})
        self.assertEqual(st["drop_candidates"], [a["id"]])
        self.assertEqual(st["unused"], [b["id"]])
        txt = IM.stats_text(st)
        self.assertIn("지움 후보", txt)
        self.assertIn("아직 안 쓰임", txt)
        from stewart import cli
        from stewart.memory import USER
        mem2 = tmp / "m2" / USER["key"]
        mem2.mkdir(parents=True)
        (mem2 / "examples.md").write_text((mem / "examples.md").read_text(encoding="utf-8"), encoding="utf-8")
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"STEWART_MEMORY_DIR": str(tmp / "m2")}), mock.patch("sys.stdout", buf):
            self.assertEqual(cli.main(["improve", "--stats", "--runs-dir", str(tmp / "runs")]), 0)
        self.assertIn("예시가 붙은 차례 4개", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
