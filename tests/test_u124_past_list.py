"""판 124.2: 지난 대화 목록 — 작업 폴더로 거르지 않고(parlando·다른 코드 폴더 대화도) 자리(place)·폴더·마지막 시각을 달고,
열린 칸은 빼고, 다시 열어 이어 받은 원래 기록은 숨기며(지울 때 함께 옮김), 다시 연 대화는 앞 내용을 화면 줄로 다시 그린다."""

import json
import os
import time
from pathlib import Path

from stewart.trash import past_mains, past_search, superseded
from tests.test_hub import msg
from tests.test_hub3 import MainBase


def fake_run(runs: Path, name: str, workdir: Path, profile: str, users: list[str], resumed_from: str = "", started: str = "2026-11-01T09:00:00",
             mtime: float | None = None) -> Path:
    d = runs / name
    d.mkdir(parents=True)
    meta = {"workdir": str(workdir), "started": started, "profile": profile, "resumed_from": resumed_from or None}
    (d / "events.jsonl").write_text(json.dumps({"kind": "run_start", "meta": meta}) + "\n", encoding="utf-8")
    msgs = [{"role": "system", "content": "s"}]
    for u in users:
        msgs += [{"role": "user", "content": u}, {"role": "assistant", "content": "답: " + u}]
    (d / "messages.json").write_text(json.dumps(msgs, ensure_ascii=False), encoding="utf-8")
    if mtime is not None:
        for f in d.iterdir():
            os.utime(f, (mtime, mtime))
    return d


class TPastList(MainBase):
    def test_all_folders_and_places(self):
        runs = self.tmp / "runs"
        now = time.time()
        talk = fake_run(runs, "20261101-090000-hub-main-t", self.tmp / "talkdir", "talk", ["바다 이야기"], mtime=now - 100)
        other = fake_run(runs, "20261101-090001-hub-main-o", self.tmp / "다른폴더", "chat", ["다른 폴더 일"], mtime=now - 50)
        here = fake_run(runs, "20261101-090002-hub-main-h", self.wd, "chat", ["여기 일"], mtime=now - 10)
        past = self.main.past_mains(100)
        got = {p["run_dir"]: p for p in past}
        self.assertTrue({str(talk), str(other), str(here)} <= set(got))
        self.assertEqual(got[str(talk)]["place"], "talk")
        self.assertEqual((got[str(other)]["place"], got[str(other)]["folder"], got[str(other)]["here"]), ("code", "다른폴더", False))
        self.assertTrue(got[str(here)]["here"])
        order = [p["run_dir"] for p in past if p["run_dir"] in (str(talk), str(other), str(here))]
        self.assertEqual(order, [str(here), str(other), str(talk)])                    # 마지막으로 손댄 것부터
        mg = self.main.manager()
        self.assertIn(str(talk), [m["key"] for m in mg.mains()])                       # 관리 목록에도(kind 는 main 그대로)
        self.assertEqual({m["kind"] for m in mg.mains()}, {"main"})

    def test_open_chats_not_in_past(self):
        self.r.scripts["MAIN"] = [msg("답")]
        c = self.main.new_chat("fresh")
        c.send("열린 칸의 말")
        self.assertTrue(c.wait_idle())
        run = str(c.sess.rec.dir)
        self.assertNotIn(run, [m["key"] for m in self.main.manager().mains()])        # 열린 칸은 지난 목록에 없다
        with self.assertRaises(RuntimeError):
            self.main.manager().delete("main", run)
        self.main.close_chat(c.name)
        self.assertIn(run, [m["key"] for m in self.main.manager().mains()])

    def test_reopen_hides_original_and_replays(self):
        self.r.scripts["MAIN"] = [msg("배라고 기억했다")]
        c = self.main.new_chat("fresh")
        c.send("기억할 낱말은 배")
        self.assertTrue(c.wait_idle())
        run = self.main.close_chat(c.name)
        time.sleep(1.1)
        c2 = self.main.new_chat("reopen", run_dir=run)
        log = [(e["kind"], e["text"]) for e in c2.state()["main_log"]]
        self.assertIn(("user", "기억할 낱말은 배"), log)                              # 앞 내용이 화면 줄로
        self.assertIn(("assistant", "배라고 기억했다"), log)
        self.r.scripts["MAIN"] = [msg("배")]
        c2.send("낱말?")
        self.assertTrue(c2.wait_idle())
        run2 = self.main.close_chat(c2.name)
        keys = [m["key"] for m in self.main.manager().mains()]
        self.assertIn(run2, keys)
        self.assertNotIn(run, keys)                                                    # 이어 받은 원래 기록은 숨긴다
        self.assertEqual(superseded(self.tmp / "runs", run2), [run])
        tid = self.main.manager().delete("main", run2)                                 # 지우면 숨은 원래 기록도 함께
        self.assertFalse(Path(run).exists() or Path(run2).exists())
        self.main.manager().restore(tid)
        self.assertTrue(Path(run).exists() and Path(run2).exists())

    def test_fork_keeps_both(self):
        runs = self.tmp / "pr"
        now = time.time()
        a = fake_run(runs, "20261101-090000-hub-main", self.wd, "chat", ["원래"], mtime=now)            # 갈래 뒤에도 원래를 이어 씀
        b = fake_run(runs, "20261101-090100-hub-main", self.wd, "chat", ["원래", "갈래"], resumed_from=str(a),
                     started="2026-11-01T09:01:00", mtime=now - 5)
        got = [p["run_dir"] for p in past_mains(runs)]
        self.assertEqual(set(got), {str(a), str(b)})
        hits = past_search(runs, None, "원래")
        self.assertEqual(len(hits), 2)

    def test_fork_of_untouched_original_keeps_both(self):
        self.r.scripts["MAIN"] = [msg("답")]
        c = self.main.new_chat("fresh")
        c.send("원래 대화")
        self.assertTrue(c.wait_idle())
        time.sleep(1.1)
        f = self.main.new_chat("fork", source=c.name)                                 # 원래는 그 뒤로 손대지 않아도
        run = self.main.close_chat(c.name)
        frun = self.main.close_chat(f.name)
        keys = [m["key"] for m in self.main.manager().mains()]
        self.assertIn(run, keys)                                                       # 갈래는 원래 대화를 숨기지 않는다
        self.assertIn(frun, keys)                                                      # 갈래도 따로 보인다(앞 내용을 복사해 가졌다)
        self.assertEqual(superseded(self.tmp / "runs", frun), [])

    def test_chat_list_first_and_last(self):
        self.r.scripts["MAIN"] = [msg("답")]
        c = self.main.new_chat("fresh")
        c.send("첫 말은 이것")
        self.assertTrue(c.wait_idle())
        row = next(x for x in self.main.state()["chats"] if x["name"] == c.name)
        self.assertEqual(row["first"], "첫 말은 이것")
        self.assertGreater(row["last"], time.time() - 120)


if __name__ == "__main__":
    import unittest
    unittest.main()
