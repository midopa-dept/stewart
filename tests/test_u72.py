"""판 93(SW-41 ④ 판 B): 지난 대화로 배워 두기 — 👍·교정된 차례 모으기(본 것 건너뜀), 모델 제안 확인 규칙, 받은 것만 적용·사본·되돌리기,
허브 /improve·"지난 대화로 배워 둬" → 카드(api·revert 칸) → 받기, 바깥 API 면 먼저 묻기, CLI --dry-run 무변경."""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import examples as EX, improve as IM
from tests.test_hub import msg, wait
from tests.test_hub3 import MainBase
from tests.test_u67 import mem_at
from tests.test_u68 import call, write_events


def runs_with_labels(root: Path) -> Path:
    write_events(root / "20261112-090000-hub-main", [
        {"kind": "run_start", "meta": {"backend": {"name": "q", "model": "m"}}, "messages": [{"role": "system", "content": "지시"}]},
        {"kind": "user", "turn": 1, "content": "시험이 깨졌다 고쳐 줘"},
        {"kind": "step", "new_messages": [{"role": "assistant", "content": None, "tool_calls": [call("c1", "run_shell", {"command": "pytest -q -x"})]},
                                          {"role": "tool", "tool_call_id": "c1", "name": "run_shell", "content": "1 failed"}]},
        {"kind": "step", "new_messages": [{"role": "assistant", "content": "고쳤다"}]},
        {"kind": "turn_end", "turn": 1, "reason": "answered"},
        {"kind": "train_label", "turn": 1, "label": "good", "by": "human"},
        {"kind": "user", "turn": 2, "content": "보고서 써 줘"},
        {"kind": "step", "new_messages": [{"role": "assistant", "content": "영어 보고서"}]},
        {"kind": "turn_end", "turn": 2, "reason": "answered"},
        {"kind": "user", "turn": 3, "content": "아니, 한국어로"},
        {"kind": "train_label", "turn": 2, "label": "corrected", "by": "rule"},
        {"kind": "step", "new_messages": [{"role": "assistant", "content": "한국어 보고서"}]},
        {"kind": "turn_end", "turn": 3, "reason": "answered"}])
    return root


def reply(*lines):
    return {"choices": [{"message": {"role": "assistant", "content": "\n".join(json.dumps(x, ensure_ascii=False) for x in lines)}}]}, 0.1


GOOD = [{"op": "example", "title": "pytest 실패 고치기", "situation": "시험 실패", "did": "run_shell pytest -q -x 로 첫 실패만", "outcome": "통과", "src": "T1"},
        {"op": "rule", "text": "보고서는 한국어로 쓴다", "src": "T2"}]


class TCollectParse(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.runs = runs_with_labels(self.tmp / "runs")
        self.m = mem_at(self.tmp)

    def test_collect_and_seen(self):
        got = IM.collect(self.runs, self.m.store.dir)
        self.assertEqual([g["label"] for g in got], ["좋음", "교정"])
        self.assertIn("pytest -q -x", got[0]["text"])
        self.assertIn("고친 차례", got[1]["text"])
        IM.mark_seen(self.m.store.dir, [got[0]["ref"]])
        self.assertEqual([g["label"] for g in IM.collect(self.runs, self.m.store.dir)], ["교정"])

    def test_parse_rules(self):
        picked = IM.collect(self.runs, self.m.store.dir)
        EX.add(self.m.store.dir, "csv 합계", "상황: x\n한 일: y\n결과: z")
        self.m.remember("policy", "앞으로는 커밋 전에 시험을 돌린다")
        bad = [{"op": "rule", "text": "x", "src": "T9"},                                         # 없는 근거
               {"op": "example", "title": "csv 합계", "did": "a", "src": "T1"},                  # 있는 예시 제목
               {"op": "rule", "text": "토큰 ghp_" + "a" * 36 + " 를 쓴다", "src": "T2"},        # 비밀 꼴
               {"op": "rule", "text": "앞으로는 커밋 전에 시험을 돌린다", "src": "T2"},          # 있는 방침과 같음
               {"op": "rule", "text": "가" * 400, "src": "T2"},
               {"op": "example", "title": "빈 일", "did": "", "src": "T1"},
               {"op": "nope", "src": "T1"}]
        plan = IM.parse("\n".join(json.dumps(x, ensure_ascii=False) for x in bad + GOOD + [GOOD[0]]), picked,
                        EX.load(self.m.store.dir), [x["text"] for x in self.m.remembered() if x["kind"] == "policy"])
        self.assertEqual([(p["op"], p["src"]) for p in plan], [("example", "T1"), ("rule", "T2")])   # 같은 근거 두 번은 하나만
        self.assertIn("한 일: run_shell", plan[0]["body"])

    def test_apply_revert(self):
        plan, got = IM.propose(self.m, None, self.runs, chat_fn=lambda *a, **k: reply(*GOOD))
        before_pol = sorted(x["text"] for x in self.m.remembered())
        r = IM.apply(self.m, plan, [1, 2], got)
        self.assertEqual(r["applied"], [1, 2])
        self.assertEqual([x["title"] for x in EX.load(self.m.store.dir)], ["pytest 실패 고치기"])
        self.assertIn("보고서는 한국어로 쓴다", [x["text"] for x in self.m.remembered()])
        self.assertEqual(IM.collect(self.runs, self.m.store.dir), [])                             # 본 차례는 다시 안 봄
        IM.revert(self.m.store.dir, Path(r["backup"]).name)
        self.assertEqual(EX.load(self.m.store.dir), [])
        self.assertEqual(sorted(x["text"] for x in self.m.remembered()), before_pol)


class THub(MainBase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"STEWART_MEMORY": "1", "STEWART_MEMORY_DIR": tempfile.mkdtemp(prefix="mem-")})
        p.start()
        self.addCleanup(p.stop)
        super().setUp()

    def test_command_card_apply_and_external(self):
        runs_with_labels(self.main._runs_dir)
        with mock.patch.object(IM, "propose", side_effect=lambda mem, b, runs, picked=None, chat_fn=None:
                               IM.parse("\n".join(json.dumps(x, ensure_ascii=False) for x in GOOD), picked, [], []) and
                               (IM.parse("\n".join(json.dumps(x, ensure_ascii=False) for x in GOOD), picked, [], []), picked)):
            self.assertEqual(self.main.send("지난 대화로 배워 둬"), "memory_plan")
            card = wait(lambda: [m for m in self.main.state()["main_log"] if m["kind"] == "memory_plan"], timeout=10)
        self.assertEqual(card[0]["api"], "/api/improve/apply")
        self.assertEqual(len(card[0]["plan"]), 2)
        self.assertEqual(self.r.requests.get("MAIN", []), [])                    # 모델 차례로 보내지 않았다
        r = self.main.improve_apply([1])
        self.assertEqual(r["applied"], [1])
        self.assertEqual(len(EX.load(self.main.sess.mem.store.dir)), 1)
        self.assertIn("stewart improve --revert", self.main.state()["main_log"][-1]["text"])
        self.assertEqual(self.main.send("/improve"), "nothing")                  # 본 차례는 다시 안 봄
        runs_with_labels(self.main._runs_dir / "x")                              # 다른 폴더 이름 → 새 차례
        for d in (self.main._runs_dir / "x").iterdir():
            d.rename(self.main._runs_dir / "20261112-100000-hub-main")
        with mock.patch.object(type(self.main.sess.backend), "external", return_value=True):
            self.assertEqual(self.main.send("/improve"), "confirm")             # 바깥 API 면 먼저 알림
        self.assertIn("/improve yes", self.main.state()["main_log"][-1]["text"])


class TCli(unittest.TestCase):
    def test_dry_run_changes_nothing(self):
        tmp = Path(tempfile.mkdtemp())
        runs = runs_with_labels(tmp / "runs")
        from stewart import cli
        with mock.patch.dict(os.environ, {"STEWART_MEMORY_DIR": str(tmp / "mem")}), \
                mock.patch.object(IM, "propose", side_effect=lambda mem, b, r, picked=None, chat_fn=None:
                                  (IM.parse("\n".join(json.dumps(x, ensure_ascii=False) for x in GOOD), picked, [], []), picked)), \
                mock.patch("stewart.cli.load_backends", return_value=({"f": mock.Mock(external=lambda: False)}, "f")):
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                rc = cli.main(["improve", "--dry-run", "--runs-dir", str(runs)])
        self.assertEqual(rc, 0)
        self.assertIn("[예시] pytest 실패 고치기", buf.getvalue())
        self.assertFalse(any(p.name in ("examples.md", IM.SEEN) for p in (tmp / "mem").rglob("*")))


if __name__ == "__main__":
    unittest.main()
