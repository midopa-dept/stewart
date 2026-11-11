"""판 89(SW-39 첫 조각) 실사용 기록 표시·내보내기: 교정 꼴 말 → 앞 차례 "교정됨", 허브 👍👎·떼기(train_label 사건),
/good·/bad 명령, export(good·after_fix·all, 엔진 넛지·실패 호출 빼기, 큰 결과 줄임, 비밀·홈 경로 가림), show, CLI 는 통계만."""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import train as TRAIN
from stewart.chat import classify
from tests.test_hub import msg, wait
from tests.test_hub3 import MainBase
from tests import test_hub_ui as HU


class TRule(unittest.TestCase):
    def test_correction_forms(self):
        for t in ("아니, b.txt 를 읽어라", "아니야 그거 말고", "그게 아니라 위쪽 파일", "다시 해", "틀렸어", "잘못했네 다시",
                  "No, the other one", "that's not what I asked", "wrong file"):
            self.assertTrue(TRAIN.is_correction(t), t)
        for t in ("고마워", "다음은 b.txt", "아니면 둘 다 해도 된다고 생각해?", "notes 폴더를 봐", "", "아니" * 400):
            self.assertFalse(TRAIN.is_correction(t), t)

    def test_commands_registered(self):
        self.assertEqual(classify("/good 잘했다")[:2], ("cmd", "/good"))
        self.assertEqual(classify("/bad")[:2], ("cmd", "/bad"))


def write_events(d: Path, events: list) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "events.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")
    return d


def call(cid, name, args):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


class TExport(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        big = "가" * 9000
        write_events(self.tmp / "runs" / "20261112-090000-hub-main", [
            {"kind": "run_start", "meta": {"tools": ["read_file"]}, "messages": [{"role": "system", "content": "지시"}]},
            {"kind": "user", "turn": 1, "content": "a.txt 를 읽어라"},
            {"kind": "step", "new_messages": [
                {"role": "assistant", "content": None, "tool_calls": [call("c1", "read_fil", {}), call("c2", "read_file", {"path": "a.txt"})]},
                {"role": "tool", "tool_call_id": "c1", "content": "모르는 도구: read_fil"},
                {"role": "tool", "tool_call_id": "c2", "name": "read_file", "content": big}]},
            {"kind": "step", "new_messages": [{"role": "user", "content": "[엔진] 같은 결과를 또 받았다"},
                                              {"role": "assistant", "content": "a.txt 는 '가'로 가득하다"}]},
            {"kind": "turn_end", "turn": 1, "reason": "answered"},
            {"kind": "user", "turn": 2, "content": "아니, b.txt 다", "raw": "아니, b.txt 다"},
            {"kind": "train_label", "turn": 1, "label": "corrected", "by": "rule"},
            {"kind": "step", "new_messages": [{"role": "assistant", "content": "b.txt 는 비었다"}]},
            {"kind": "turn_end", "turn": 2, "reason": "answered"},
            {"kind": "train_label", "turn": 2, "label": "bad", "by": "human"},
            {"kind": "train_label", "turn": 2, "label": "good", "by": "human"},
        ])
        write_events(self.tmp / "runs" / "20261101-090000-chat", [
            {"kind": "user", "turn": 1, "content": "옛 기록"}, {"kind": "turn_end", "turn": 1, "reason": "answered"},
            {"kind": "train_label", "turn": 1, "label": "good", "by": "human"}])

    def test_turns_labels_and_select(self):
        ts = TRAIN.turns_of(self.tmp / "runs" / "20261112-090000-hub-main")
        self.assertEqual([t["labels"] for t in ts], [["corrected"], ["good"]])           # 나쁨 → 좋음으로 바꿈
        self.assertEqual([t["turn"] for t in TRAIN.select(ts, "good")], [2])
        self.assertEqual([t["turn"] for t in TRAIN.select(ts, "after_fix")], [2])
        self.assertEqual(len(TRAIN.select(ts, "all")), 2)

    def test_clean_drops_failed_and_engine_clips_big(self):
        t = TRAIN.turns_of(self.tmp / "runs" / "20261112-090000-hub-main")[0]
        msgs, cnt = TRAIN.clean(t)
        self.assertEqual(cnt["failed_calls"], 1)
        self.assertEqual(cnt["engine_notes"], 1)
        self.assertEqual(cnt["clipped"], 1)
        self.assertEqual([m["role"] for m in msgs], ["system", "user", "assistant", "tool", "assistant"])
        self.assertEqual([tc["id"] for tc in msgs[2]["tool_calls"]], ["c2"])
        self.assertLess(len(msgs[3]["content"]), 4200)
        self.assertIn("자 줄임", msgs[3]["content"])

    def test_export_since_hashes_and_cli_prints_stats_only(self):
        out = self.tmp / "out"
        st = TRAIN.export(self.tmp / "runs", out, "good")
        self.assertEqual((st["runs"], st["written"]), (2, 2))
        st = TRAIN.export(self.tmp / "runs", out, "good", since=__import__("datetime").date(2026, 11, 10))
        self.assertEqual((st["runs"], st["written"]), (1, 1))
        st = TRAIN.export(self.tmp / "runs", out, "good", exclude_hashes={TRAIN.norm_hash("옛  기록")})
        self.assertEqual((st["written"], st["excluded_eval"]), (1, 1))
        rec = json.loads((out / "train.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(rec["meta"]["labels"], ["good"])
        self.assertTrue(json.loads((out / "stats.json").read_text(encoding="utf-8"))["select"] == "good")
        from stewart import cli
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = cli.main(["train", "export", "--runs-dir", str(self.tmp / "runs"), "--out", str(out), "--select", "all"])
        self.assertEqual(rc, 0)
        self.assertIn("씀 3개", buf.getvalue())
        self.assertNotIn("b.txt", buf.getvalue())                       # 내용은 찍지 않는다
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            cli.main(["train", "show", str(out), "-n", "3"])
        self.assertIn("3건 가운데 3건", buf.getvalue())
        self.assertIn("[모델 → 도구] read_file", buf.getvalue())

    def test_scrub_home_user_and_secret(self):
        d = write_events(self.tmp / "r2" / "20261112-100000-chat", [
            {"kind": "user", "turn": 1, "content": "/home/tester-q/notes.txt 를 tester-q 계정으로 열고 토큰 ghp_" + "a" * 36 + " 를 써라"},
            {"kind": "step", "new_messages": [{"role": "assistant", "content": "열었다"}]},
            {"kind": "turn_end", "turn": 1, "reason": "answered"}])
        with mock.patch.dict(os.environ, {"HOME": "/home/tester-q", "USER": "tester-q"}):
            TRAIN.export(d.parent, self.tmp / "o2", "all")
        body = (self.tmp / "o2" / "train.jsonl").read_text(encoding="utf-8")
        self.assertIn("~/notes.txt", body)
        self.assertNotIn("tester-q", body)
        self.assertNotIn("a" * 36, body)


class THub(MainBase):
    def test_rule_label_buttons_and_export(self):
        self.r.scripts["MAIN"] = [msg("a.txt 를 읽었다"), msg("b.txt 를 읽었다")]
        self.main.send("a.txt 를 읽어라")
        self.assertTrue(self.main.wait_idle())
        self.main.send("아니, b.txt 를 읽어라")
        self.assertTrue(self.main.wait_idle())
        log = [m for m in self.main.state()["main_log"] if m["kind"] == "assistant"]
        self.assertEqual([m.get("turn") for m in log], [1, 2])
        self.assertEqual(log[0].get("corrected"), "rule")                  # 다음 말이 고치는 꼴 → 앞 차례에 꼬리표
        self.assertNotIn("corrected", log[1])
        self.assertEqual(self.main.train_label(2, "good"), {"ok": True})
        self.assertFalse(self.main.train_label(9, "good")["ok"])            # 없는 차례
        self.assertFalse(self.main.train_label(1, "corrected")["ok"])       # 사람 단추로는 good·bad·clear·uncorrect 만
        runs = Path(self.main.sess.rec.dir).parent
        st = TRAIN.export(runs, self.tmp / "ex", "after_fix")
        self.assertEqual(st["written"], 1)
        self.main.train_label(1, "uncorrect")
        self.main.train_label(2, "clear")
        log = [m for m in self.main.state()["main_log"] if m["kind"] == "assistant"]
        self.assertNotIn("corrected", log[0])
        self.assertNotIn("label", log[1])
        self.assertEqual(TRAIN.export(runs, self.tmp / "ex2", "after_fix")["written"], 0)
        evs = [json.loads(x) for x in (Path(self.main.sess.rec.dir) / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(e["label"], e["by"]) for e in evs if e["kind"] == "train_label"],
                         [("corrected", "rule"), ("good", "human"), ("uncorrect", "human"), ("clear", "human")])
        sent = json.dumps(self.r.requests["MAIN"][-1]["messages"], ensure_ascii=False)
        self.assertNotIn("교정", sent)                                     # 표시는 모델 문맥에 들어가지 않는다


class TWeb(HU.TWebSessions):
    locals().update({k: None for k in dir(HU.TWebSessions) if k.startswith("test")})   # 바탕 시험은 거기서 돈다

    def test_label_endpoint(self):
        self.r.scripts["MAIN"] = [msg("했다")]
        self.main.send("해 줘")
        self.assertTrue(self.main.wait_idle())
        self.assertEqual(self.post("/api/train/label", turn=1, label="bad"), (200, {"ok": True}))
        self.assertEqual(self.post("/api/train/label", turn=1, label="corrected")[0], 400)
        self.assertEqual(self.post("/api/train/label", turn="1", label="bad")[0], 400)
        r, _ = self.req("POST", "/api/train/label", json.dumps({"turn": 1, "label": "good"}),
                        {k: v for k, v in self.good.items() if k != "X-Stewart-Token"})
        self.assertEqual(r.status, 403)                                     # CSRF 표 없이는 안 됨
        self.assertEqual([m.get("label") for m in self.main.state()["main_log"] if m["kind"] == "assistant"], ["bad"])


if __name__ == "__main__":
    unittest.main()
