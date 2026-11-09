"""폴더 정리 ③ 되돌리기."""

import json
import os
import unittest
from pathlib import Path

from stewart.organize import apply as A, plan as PL
from tests.test_organize_plan import NFC, NFD, Base


class Undo(Base):
    def applied(self):
        self.add({"op": "move", "src": f"{NFC}/a.jpg", "dst": f"{NFC}/2021/a.jpg", "reason": ""},
                 {"op": "to_delete", "src": "junk.tmp", "reason": ""},
                 {"op": "move", "src": "docs/old", "dst": "archive/old", "reason": ""})
        sha, _ = A.review(self.plan_path)
        res = A.apply(self.plan_path, sha[:12])
        self.assertEqual(res["moved"], 3)
        j = self.runs / "moves.jsonl"
        return j, PL.sha256_file(j)

    def test_undo_all(self):
        j, sha = self.applied()
        with self.assertRaises(PermissionError):
            A.undo(j, "0" * 12)
        dry = A.undo(j, sha[:12], dry_run=True)
        self.assertEqual(dry["restored"], 3)
        self.assertTrue((self.root / "archive/old/x.txt").exists())          # dry-run 은 그대로
        res = A.undo(j, sha[:12])
        self.assertEqual((res["restored"], res["skipped"]), (3, {}))
        self.assertTrue((self.root / NFD / "a.jpg").exists())
        self.assertTrue((self.root / "junk.tmp").exists())
        self.assertTrue((self.root / "docs/old/y.txt").exists())
        self.assertEqual(sorted(res["left_dirs"]), sorted([f"{NFD}/2021", "archive"]))   # 빈 폴더는 남긴다(목록만)
        self.assertTrue((self.root / "archive").is_dir())
        u = A.read_journal(self.runs / "undo-moves.jsonl")
        self.assertEqual([r["state"] for r in u if r.get("state")], ["start", "done"] * 3)
        self.assertEqual([r["of"] for r in u if r.get("state") == "start"], [3, 2, 1])     # 역순
        again = A.undo(j, sha[:12])
        self.assertEqual(again["restored"], 0)
        self.assertEqual(again["skipped"], {"이미 되돌림": 3})

    def test_conflicts(self):
        j, sha = self.applied()
        (self.root / NFD / "2021" / "a.jpg").write_text("edited after")     # 옮긴 뒤 바뀜
        (self.root / "junk.tmp").write_text("new one")                        # 원래 자리에 다른 것
        res = A.undo(j, sha[:12])
        self.assertEqual(res["restored"], 1)                                  # 폴더만 되돌림
        whys = " ".join(s["why"] for s in res["skipped_items"])
        self.assertIn("옮긴 뒤 바뀜", whys)
        self.assertIn("원래 자리에 다른 것", whys)
        self.assertEqual((self.root / "junk.tmp").read_text(), "new one")
        self.assertTrue((self.root / "_to_delete/junk.tmp").exists())

    def test_missing_parent_recreated(self):
        j, sha = self.applied()
        os.rename(self.root / "docs/r.pdf", self.root / "r.pdf")               # docs 를 비우고 지운다(사람이 한 일)
        (self.root / "docs").rmdir()
        (self.root / NFD / "2021" / "a.jpg").rename(self.root / "a.jpg")      # a.jpg 는 다른 데로 옮겨 감
        res = A.undo(j, sha[:12])
        self.assertIn("지금 자리에 없다", " ".join(s["why"] for s in res["skipped_items"]))
        self.assertEqual(res["restored"], 2)
        self.assertTrue((self.root / "docs/old/x.txt").exists())               # 원래 상위 폴더를 다시 만듦
        self.assertIn("docs", res["made_dirs"])

    def test_reconcile_undo(self):
        j, sha = self.applied()
        u = self.runs / "undo-moves.jsonl"
        os.rename(self.root / "_to_delete/junk.tmp", self.root / "junk.tmp")  # 되돌리기 rename 뒤 done 을 못 씀
        u.write_text(json.dumps({"op": "undo", "seq": 1, "of": 2, "src": "_to_delete/junk.tmp", "dst": "junk.tmp",
                                 "state": "start"}) + "\n")
        res = A.undo(j, sha[:12])
        self.assertEqual(res["recovered"][0]["state"], "done")
        self.assertEqual(res["restored"], 2)
        self.assertIn("이미 되돌림", " ".join(s["why"] for s in res["skipped_items"]))

    def test_cli(self):
        from stewart.cli import main
        j, sha = self.applied()
        self.assertEqual(main(["organize", "undo", str(j), "--approve", sha[:12]]), 0)
        self.assertTrue((self.root / "junk.tmp").exists())


if __name__ == "__main__":
    unittest.main()
