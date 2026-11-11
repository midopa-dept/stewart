"""판 83(SW-35) 기억 세 갈래: 방침·프로필·자료 마크다운이 정본, 옛 remember.jsonl 자동 옮기기(사본·표·되돌리기),
이번 차례 지시는 남기지 않고 오래 지킬 지시는 [허용]·[이번만]으로 묻기, 자료는 지시문 밖(doc_search source=memory)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import memory as MEM, memstore as MS, tools as T
from stewart.config import Profile
from tests.test_hub import msg
from tests.test_hub3 import MainBase


def mem_at(tmp: Path, wd: Path | None = None) -> MEM.Memory:
    wd = wd or tmp
    p = Profile(name="chat", path=tmp / "p.toml", system="s", tools=["read_file"], workdir=wd)
    with mock.patch.dict(os.environ, {"STEWART_MEMORY_DIR": str(tmp / "mem")}):
        return MEM.Memory(p, tmp / "runs" / "r1")


def legacy(tmp: Path, rows: list[dict]) -> Path:
    d = tmp / "mem" / "user"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "remember.jsonl", "w", encoding="utf-8") as f:
        for i, (kind, text) in enumerate(rows):
            f.write(json.dumps({"id": f"old{i}", "ts": f"2026-11-0{i + 1}T10:00:00", "kind": kind, "text": text, "scope": "user",
                                "source": "said"}, ensure_ascii=False) + "\n")
        f.write(json.dumps({"forget": "old9"}) + "\n")
    return d


ROWS = [("feedback", "앞으로는 보고를 파일로 남겨라"), ("explicit", "배포는 금요일에 하지 않는다"), ("user", "나는 짧은 답을 좋아한다"),
        ("feedback", "이 저장소의 시험은 make check 로 돈다"), ("reference", "허브 기본 포트는 8765"),
        ("user", "사용자는 " + "아주 긴 설명 " * 20)]


class TMigrate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_dry_run_then_auto_then_revert(self):
        d = legacy(self.tmp, ROWS)
        r = MS.migrate(d, dry_run=True)
        self.assertEqual(r["counts"], {"policy": 2, "profile": 1, "knowledge": 3})
        self.assertIn("| old3 | feedback | 자료 |", r["table"])
        self.assertFalse((d / "policy.md").exists())                              # 미리보기는 쓰지 않는다
        m = mem_at(self.tmp)                                                       # 처음 뜰 때 자동으로 옮긴다
        self.assertEqual(m.migrated["counts"]["policy"], 2)
        self.assertTrue((d / "policy.md").is_file() and (d / "profile.md").is_file() and (d / "knowledge" / "general.md").is_file())
        self.assertTrue(list(d.glob("remember.jsonl.bak-*")) and list(d.glob("migrate-*.md")))
        self.assertTrue((d / "remember.jsonl").is_file())                          # 옛 파일은 그대로
        self.assertTrue(m.md())
        self.assertEqual({x["kind"] for x in m.remembered()}, {"policy", "profile", "knowledge"})
        self.assertIsNone(mem_at(self.tmp).migrated)                               # 두 번째는 옮기지 않는다
        rv = MS.revert(d)
        self.assertEqual(sorted(rv["moved"]), ["knowledge", "policy.md", "profile.md"])
        m2 = mem_at(self.tmp)
        self.assertIsNone(m2.migrated)                                             # 되돌린 뒤 다시 자동으로 옮기지 않는다
        self.assertFalse(m2.md())
        self.assertEqual(len(m2.remembered()), 6)                                  # 옛 파일로 돌아갔다

    def test_off_knob(self):
        legacy(self.tmp, ROWS)
        with mock.patch.dict(os.environ, {"STEWART_MEMORY_MIGRATE": "0"}):
            m = mem_at(self.tmp)
        self.assertIsNone(m.migrated)
        self.assertFalse(m.md())


class TStore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.m = mem_at(self.tmp)

    def test_new_install_is_three_way_and_dedup(self):
        self.assertTrue(self.m.md())
        a = self.m.remember("feedback", "앞으로는 커밋 전에 시험을 돌려라")
        self.assertEqual(a["kind"], "policy")
        b = self.m.remember("feedback", "앞으로는 커밋 전에 시험을 꼭 돌려라")       # 거의 같다 → 고치기
        self.assertEqual(b["id"], a["id"])
        self.assertEqual([x["text"] for x in self.m.remembered()], ["앞으로는 커밋 전에 시험을 꼭 돌려라"])
        self.assertEqual(self.m.remember("user", "나는 " + "아주 긴 설명을 붙인 선호 " * 8)["kind"], "knowledge")   # 긴 프로필은 자료로
        self.assertEqual(self.m.remember("project", "a 는 uv 로 돈다", scope="project")["kind"], "knowledge")

    def test_human_edits_file(self):
        self.m.remember("policy", "보고는 짧게")
        p = self.m.store.dir / "policy.md"
        p.write_text(p.read_text(encoding="utf-8").replace("보고는 짧게", "보고는 아주 짧게") + "- 손으로 쓴 방침 한 줄\n", encoding="utf-8")
        texts = [x["text"] for x in self.m.remembered()]
        self.assertEqual(set(texts), {"보고는 아주 짧게", "손으로 쓴 방침 한 줄"})
        hand = next(x for x in self.m.remembered() if x["text"] == "손으로 쓴 방침 한 줄")
        self.assertTrue(self.m.forget(hand["id"]))
        self.assertNotIn("손으로 쓴 방침 한 줄", p.read_text(encoding="utf-8"))

    def test_change_kind(self):
        x = self.m.remember("knowledge", "허브 포트는 8765")
        self.assertEqual(self.m.update(x["id"], "나는 허브를 8765 에 띄운다", kind="profile")["kind"], "profile")
        self.assertEqual([y["kind"] for y in self.m.remembered()], ["profile"])
        self.assertFalse((self.m.store.dir / "knowledge" / "general.md").read_text(encoding="utf-8").count("8765"))

    def test_inject_policy_profile_not_knowledge(self):
        self.m.remember("policy", "앞으로는 보고를 파일로 남겨라")
        self.m.remember("profile", "나는 한국어로 답을 받는다")
        self.m.remember("knowledge", "사내 저장소 주소 규칙은 org/이름 꼴이다")
        text, log = self.m.inject()
        self.assertIn("### 방침", text)
        self.assertIn("보고를 파일로", text)
        self.assertIn("한국어로 답", text)
        self.assertNotIn("org/이름", text)                                          # 자료는 싣지 않는다
        self.assertIn('doc_search(query, source="memory")', text)
        self.assertEqual(log[0]["knowledge"], 1)
        for i in range(12):
            self.m.remember("profile", " ".join(f"{w}{i}" for w in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta")))
        _, log = self.m.inject()
        self.assertTrue(log[0]["profile_over"])

    def test_doc_search_memory(self):
        self.m.remember("knowledge", "사내 저장소 주소 규칙은 org/이름 꼴이다 — 사람이 고쳐 줌")
        env = T.ToolEnv(workdir=self.tmp)
        env.memory_knowledge_dir = self.m.store.dir / "knowledge"
        ok, out = T.doc_search(env, "저장소 주소 규칙", source="memory")
        self.assertTrue(ok)
        self.assertIn("memory:general", out)
        self.assertNotIn("<!--", out)
        self.assertFalse(T.doc_search(env, "x", source="nope")[0])


class TRouteAndAsk(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.m = mem_at(self.tmp)

    def test_route(self):
        self.assertEqual(MEM.route("feedback", ".env cat 하지 마라."), (None, "skip"))          # 이번 차례 지시
        self.assertEqual(MEM.route("feedback", "앞으로는 보고를 파일로 남겨라"), ("policy", "ask"))
        self.assertEqual(MEM.route("explicit", "배포는 금요일에 하지 않는다"), ("policy", "save"))
        self.assertEqual(MEM.route("explicit", "허브 포트는 8765 다"), ("knowledge", "save"))
        self.assertEqual(MEM.route("user", "나는 짧은 보고를 좋아한다"), ("profile", "save"))

    def test_propose_confirm(self):
        p = self.m.propose("policy", "앞으로는 보고를 파일로 남겨라")
        self.assertEqual(self.m.remembered(), [])
        self.assertIsNone(self.m.propose("policy", "앞으로는 보고를 파일로 남겨라"))          # 같은 것을 두 번 묻지 않는다
        self.assertEqual(self.m.confirm(p["id"], False), {})                                  # 이번만
        self.assertEqual(self.m.remembered(), [])
        q = self.m.propose("policy", "앞으로는 보고를 파일로 남겨라")
        it = self.m.confirm(q["id"], True)
        self.assertEqual((it["kind"], it["text"]), ("policy", "앞으로는 보고를 파일로 남겨라"))
        self.assertEqual(self.m.pending(), [])
        self.assertIsNone(self.m.confirm("nope", True))

    def test_model_ops(self):
        done = MEM.apply_ops(self.m, [{"op": "add", "kind": "policy", "text": "보고는 표로 쓴다"}], said=["이번 보고는 표로 해 줘"])
        self.assertEqual((done[0]["op"], done[0]["item"]["kind"]), ("add", "knowledge"))      # "앞으로" 없으면 방침으로 안 남김
        done = MEM.apply_ops(self.m, [{"op": "add", "kind": "policy", "text": "보고는 늘 표로 쓴다"}], said=["앞으로 보고는 늘 표로 해 줘"])
        self.assertEqual(done[0]["op"], "ask")
        self.assertEqual(len(self.m.pending()), 1)
        done = MEM.apply_ops(self.m, [{"op": "add", "kind": "profile", "text": "사용자는 보안 일을 한다"}], said=["나는 보안 일을 해"])
        self.assertEqual(done[0]["item"]["kind"], "profile")
        self.assertIn("policy|profile|knowledge", MEM.extract_prompt(self.m, ["x"], "y"))


class THub(MainBase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"STEWART_MEMORY": "1", "STEWART_MEMORY_DIR": tempfile.mkdtemp(prefix="mem-")})
        p.start()
        self.addCleanup(p.stop)
        super().setUp()

    def test_one_off_skip_persist_ask_explicit_save(self):
        self.r.scripts["MAIN"] = [msg("알겠습니다.")] * 3
        self.main.send("이번에는 .env 보지 마. README 만 봐 줘")
        self.assertTrue(self.main.wait_idle())
        self.main.send("앞으로는 보고를 파일로 남겨라")
        self.assertTrue(self.main.wait_idle())
        self.main.send("기억해: 배포는 금요일에 하지 않는다")
        self.assertTrue(self.main.wait_idle())
        log = self.main.state()["main_log"]
        asks = [m for m in log if m["kind"] == "memory_ask"]
        saved = [m for m in log if m["kind"] == "memory"]
        self.assertEqual(len(asks), 1)
        self.assertIn("보고를 파일로", asks[0]["text"])
        self.assertEqual([m["mem_kind"] for m in saved], ["policy"])
        self.assertNotIn(".env", json.dumps([x["text"] for x in self.main.sess.mem.remembered()], ensure_ascii=False))
        it = self.main.sess.mem.confirm(asks[0]["mem_id"], True)
        self.assertEqual(it["kind"], "policy")
        self.assertEqual(len(self.main.sess.mem.remembered()), 2)


if __name__ == "__main__":
    unittest.main()
