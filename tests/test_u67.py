"""판 88(SW-40) 기억 정리: 모델 제안(갈래 옮김·합치기·지움 후보·짧게 다시 쓰기)을 코드가 다시 보고, 받은 것만 적용(사본·되돌리기),
허브 /memory·"기억 정리해 줘" → 제안 카드 → 받기, CLI --dry-run 은 아무것도 안 바꿈."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import memconsolidate as MC, memory as MEM
from stewart.config import Profile
from tests.test_hub import msg
from tests.test_hub3 import MainBase


def mem_at(tmp: Path) -> MEM.Memory:
    p = Profile(name="chat", path=tmp / "p.toml", system="s", tools=["read_file"], workdir=tmp)
    with mock.patch.dict(os.environ, {"STEWART_MEMORY_DIR": str(tmp / "mem")}):
        return MEM.Memory(p, tmp / "runs" / "r1")


def seed(m):
    a = m.remember("policy", "앞으로는 보고를 파일로 남겨라")
    b = m.remember("knowledge", "사용자는 짧은 답을 좋아한다")              # 갈래가 틀림 → profile
    c = m.remember("policy", "커밋하기 전에 반드시 전체 시험을 돌린다")
    d = m.remember("knowledge", "허브 포트는 8765 이다")
    e = m.remember("knowledge", "옛 서버 주소는 더는 쓰지 않는 자리다")
    return a, b, c, d, e


def reply(*lines):
    return {"choices": [{"message": {"role": "assistant", "content": "\n".join(json.dumps(x, ensure_ascii=False) for x in lines)}}]}, 0.1


class TPlanApply(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.m = mem_at(self.tmp)
        self.a, self.b, self.c, self.d, self.e = seed(self.m)

    def ask(self, *lines):
        return MC.propose(self.m, None, chat_fn=lambda *a, **k: reply(*lines))

    def test_parse_checks(self):
        plan = self.ask({"op": "move", "id": self.b["id"], "to": "profile", "why": "사용자 선호"},
                        {"op": "move", "id": "nope", "to": "profile", "why": "없는 id"},
                        {"op": "move", "id": self.a["id"], "to": "policy", "why": "같은 갈래"},
                        {"op": "rewrite", "id": self.c["id"], "text": "커밋 전 전체 시험", "why": "짧게"},
                        {"op": "drop", "id": self.c["id"], "why": "같은 id 두 번"},
                        {"op": "rewrite", "id": self.d["id"], "text": "토큰 ghp_" + "a" * 36, "why": "비밀"},
                        {"op": "merge", "ids": [self.a["id"]], "text": "x", "why": "하나만"},
                        {"op": "drop", "id": self.e["id"], "why": "낡음"})
        self.assertEqual([p["op"] for p in plan], ["move", "rewrite", "drop"])
        self.assertEqual([p["n"] for p in plan], [1, 2, 3])
        self.assertIn("knowledge → profile", MC.describe(plan[0]))

    def test_apply_only_accepted_backup_and_revert(self):
        plan = self.ask({"op": "move", "id": self.b["id"], "to": "profile", "why": "선호"},
                        {"op": "merge", "ids": [self.a["id"], self.c["id"]], "text": "앞으로는 보고를 파일로, 커밋 전에는 전체 시험", "why": "겹침"},
                        {"op": "drop", "id": self.e["id"], "why": "낡음"})
        before = sorted(x["text"] for x in self.m.remembered())
        r = MC.apply(self.m, plan, [1, 2])
        self.assertEqual(r["applied"], [1, 2])
        kinds = {x["text"]: x["kind"] for x in self.m.remembered()}
        self.assertEqual(kinds["사용자는 짧은 답을 좋아한다"], "profile")
        self.assertIn("앞으로는 보고를 파일로, 커밋 전에는 전체 시험", kinds)
        self.assertNotIn("커밋하기 전에 반드시 전체 시험을 돌린다", kinds)
        self.assertIn("옛 서버 주소는 더는 쓰지 않는 자리다", kinds)            # 3번은 두었다
        self.assertTrue(Path(r["log"]).is_file())
        MC.revert(self.m, Path(r["backup"]).name)
        self.assertEqual(sorted(x["text"] for x in self.m.remembered()), before)
        self.assertEqual(MC.apply(self.m, plan, [])["applied"], [])

    def test_not_three_way_or_empty(self):
        self.assertEqual(MC.propose(mem_at(Path(tempfile.mkdtemp())), None, chat_fn=lambda *a, **k: reply()), [])


class THub(MainBase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"STEWART_MEMORY": "1", "STEWART_MEMORY_DIR": tempfile.mkdtemp(prefix="mem-")})
        p.start()
        self.addCleanup(p.stop)
        super().setUp()

    def test_command_card_and_apply(self):
        mem = self.main.sess.mem
        a, b, c, d, e = seed(mem)
        fake = [{"op": "move", "ids": [b["id"]], "to": "profile", "why": "선호", "before": [b["text"]], "kinds": ["knowledge"], "n": 1},
                {"op": "drop", "ids": [e["id"]], "why": "낡음", "before": [e["text"]], "kinds": ["knowledge"], "n": 2}]
        from tests.test_hub import wait
        with mock.patch.object(MC, "propose", return_value=fake):
            self.assertEqual(self.main.send("기억 정리해 줘"), "memory_plan")
            card = wait(lambda: [m for m in self.main.state()["main_log"] if m["kind"] == "memory_plan"], timeout=10)
        self.assertEqual(len(card[0]["plan"]), 2)
        self.assertIn("갈래 옮김", card[0]["plan"][0]["desc"])
        self.assertEqual(self.r.requests.get("MAIN", []), [])              # 모델 차례로 보내지 않았다
        r = self.main.memory_apply([2])
        self.assertEqual(r["applied"], [2])
        self.assertNotIn(e["text"], [x["text"] for x in mem.remembered()])
        self.assertIn("되돌리기", self.main.state()["main_log"][-1]["text"])
        with mock.patch.object(MC, "propose", return_value=[]):
            self.assertEqual(self.main.send("/memory"), "memory_plan")
            wait(lambda: any("고칠 것이 없다" in m["text"] for m in self.main.state()["main_log"]), timeout=10)


class TCli(unittest.TestCase):
    def test_dry_run_changes_nothing(self):
        tmp = Path(tempfile.mkdtemp())
        m = mem_at(tmp)
        a, *_ = seed(m)
        files = sorted(str(p.relative_to(tmp)) + str(p.stat().st_mtime_ns) for p in (tmp / "mem").rglob("*") if p.is_file())
        from stewart import cli
        plan = [{"op": "drop", "ids": [a["id"]], "why": "x", "before": [a["text"]], "kinds": ["policy"], "n": 1}]
        with mock.patch.dict(os.environ, {"STEWART_MEMORY_DIR": str(tmp / "mem")}), \
                mock.patch.object(MC, "propose", return_value=plan), mock.patch("sys.stdout"):
            rc = cli.main(["memory", "consolidate", "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(str(p.relative_to(tmp)) + str(p.stat().st_mtime_ns) for p in (tmp / "mem").rglob("*") if p.is_file()), files)


if __name__ == "__main__":
    unittest.main()
