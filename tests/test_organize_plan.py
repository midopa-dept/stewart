"""폴더 정리 ② 계획·검토·실행기. 임시 폴더의 가짜 나무로만."""

import json
import os
import re
import shutil
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

from stewart import tools as T
from stewart.organize import apply as A, names, plan as PL

NFD = unicodedata.normalize("NFD", "사진")
NFC = unicodedata.normalize("NFC", "사진")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "root"
        self.runs = self.tmp / "runs"
        for rel, data in {f"{NFD}/a.jpg": "A", f"{NFD}/b.jpg": "BB", "docs/r.pdf": "PDF", "docs/old/x.txt": "x",
                          "docs/old/y.txt": "y", "Music/m.mp3": "M", "junk.tmp": "j", "_to_delete/z.txt": "z",
                          "keep/file": "f"}.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(data)
        os.symlink(self.tmp, self.root / "link")
        self.env = T.ToolEnv(self.root, artifacts_dir=self.runs)
        self.assertTrue(T.folder_scan(self.env)[0])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, *items):
        return T.plan_add(self.env, list(items))

    @property
    def plan_path(self):
        return self.runs / "plan.json"

    def items(self):
        return json.loads(self.plan_path.read_text())["items"]


class PlanChecks(Base):
    def test_accept_and_disk_names(self):
        ok, out = self.add({"op": "move", "src": f"{NFC}/a.jpg", "dst": f"{NFC}/2021/a.jpg", "reason": "연도", "confidence": 0.9},
                           {"op": "to_delete", "src": "junk.tmp", "reason": "임시"},
                           {"op": "move", "src": "docs/old", "dst": "archive/old", "reason": "폴더째"})
        self.assertTrue(ok, out)
        it = self.items()
        self.assertEqual(it[0]["src"], f"{NFD}/a.jpg")                         # src 는 디스크 이름
        self.assertEqual(it[0]["dst"], f"{NFD}/2021/a.jpg")                    # 있는 폴더 부분도 디스크 이름
        self.assertTrue(it[0]["new_folder"])
        self.assertEqual(it[1]["dst"], "_to_delete/junk.tmp")
        self.assertEqual((it[2]["type"], it[2]["size"]), ("dir", 2))

    def test_new_folder_spelling_unified(self):
        fold = names.case_insensitive(self.root)
        self.add({"op": "move", "src": "docs/r.pdf", "dst": "New/r.pdf", "reason": ""})
        ok, out = self.add({"op": "move", "src": "Music/m.mp3", "dst": "new/m.mp3", "reason": ""})
        self.assertTrue(ok, out)
        self.assertEqual(self.items()[1]["dst"], "New/m.mp3" if fold else "new/m.mp3")

    def test_refusals(self):
        self.add({"op": "move", "src": "docs/old", "dst": "archive/old", "reason": ""},
                 {"op": "move", "src": "docs/r.pdf", "dst": "D/r.pdf", "reason": ""})
        bad = {
            "없다": {"op": "move", "src": "nope.txt", "dst": "x/nope.txt"},
            "링크": {"op": "move", "src": "link", "dst": "x/link"},
            "이미 _to_delete": {"op": "move", "src": "_to_delete/z.txt", "dst": "z.txt"},
            "op=to_delete": {"op": "move", "src": "junk.tmp", "dst": "_TO_DELETE/junk.tmp"},
            "이미 있다": {"op": "move", "src": "junk.tmp", "dst": "keep/file"},
            "폴더가 아니다": {"op": "move", "src": "junk.tmp", "dst": "keep/file/junk.tmp"},
            "같은 src": {"op": "move", "src": "docs/r.pdf", "dst": "E/r.pdf"},
            "같은 dst": {"op": "move", "src": "junk.tmp", "dst": "D/r.pdf"},
            "옮기는 폴더": {"op": "move", "src": "docs/old/x.txt", "dst": "x.txt"},
            "안 항목": {"op": "move", "src": "docs", "dst": "docs2"},
            "옮겨 가는 폴더": {"op": "move", "src": "junk.tmp", "dst": "docs/old/junk.tmp"},
            "자기 안": {"op": "move", "src": "keep", "dst": "keep/inner"},
            "절대": {"op": "move", "src": "junk.tmp", "dst": "/tmp/junk.tmp"},
            "'..'": {"op": "move", "src": "junk.tmp", "dst": "../junk.tmp"},
            "같다": {"op": "move", "src": "junk.tmp", "dst": "JUNK.tmp" if names.case_insensitive(self.root) else "junk.tmp"},
            "op 은": {"op": "copy", "src": "junk.tmp", "dst": "a"},
            "겹친다": {"op": "move", "src": "junk.tmp", "dst": "D/r.pdf/junk"},
        }
        for word, it in bad.items():
            ok, out = self.add({**it, "reason": ""})
            self.assertFalse(ok, (word, out))
            self.assertIn(word, out, word)
        self.assertEqual(len(self.items()), 2)

    def test_show_drop(self):
        self.add({"op": "move", "src": "docs/r.pdf", "dst": "D/r.pdf", "reason": "문서"})
        self.assertIn("#1 move docs/r.pdf → D/r.pdf", T.plan_show(self.env)[1])
        self.assertIn("뺐다 1건", T.plan_drop(self.env, [1])[1])
        self.assertEqual(self.items(), [])
        self.assertFalse(T.plan_show(T.ToolEnv(self.root, artifacts_dir=self.tmp / "other"))[0])

    def test_from_media(self):
        p = PL.Plan.load(self.plan_path) if self.plan_path.exists() else None
        self.add({"op": "move", "src": "junk.tmp", "dst": "t/junk.tmp", "reason": ""})
        p = PL.Plan.load(self.plan_path)
        rows, skipped = PL.from_media({"items": [{"file": "m.mp3", "destination": "Music/Artist/", "reason": "r", "confidence": 0.8,
                                                  "source": "rule"},
                                                 {"file": "a.jpg", "destination": "_review/"},
                                                 {"file": "nope.mp3", "destination": "Music/"}]}, p)
        self.assertEqual(rows[0]["dst"], "Music/Artist/m.mp3")
        self.assertEqual(len(skipped), 2)


class Apply(Base):
    def plan3(self):
        self.add({"op": "move", "src": f"{NFC}/a.jpg", "dst": f"{NFC}/2021/a.jpg", "reason": "연도", "confidence": 0.3},
                 {"op": "to_delete", "src": "junk.tmp", "reason": "임시"},
                 {"op": "move", "src": "docs/old", "dst": "archive/old", "reason": "폴더째", "confidence": 0.9})
        sha, md = A.review(self.plan_path)
        return sha, md

    def test_review(self):
        sha, md = self.plan3()
        text = md.read_text()
        self.assertIn(sha, text)
        self.assertIn("계정 권한", text)                                   # 전제
        self.assertIn("확신 낮은 것", text)
        self.assertIn("_to_delete 로 옮길 것", text)
        self.assertIn("archive", text)

    def test_apply_and_rerun(self):
        sha, _ = self.plan3()
        with self.assertRaises(PermissionError):
            A.apply(self.plan_path, sha[:8])
        with self.assertRaises(PermissionError):
            A.apply(self.plan_path, "0" * 12)
        dry = A.apply(self.plan_path, sha[:12], dry_run=True)
        self.assertEqual(dry["moved"], 3)
        self.assertEqual(len(dry["made_dirs"]), len(set(dry["made_dirs"])))
        self.assertTrue((self.root / NFD / "a.jpg").exists())                  # dry-run 은 옮기지 않는다
        self.assertFalse((self.runs / "moves.jsonl").exists())
        res = A.apply(self.plan_path, sha[:12])
        self.assertEqual((res["moved"], res["skipped"]), (3, {}))
        self.assertTrue((self.root / NFD / "2021" / "a.jpg").exists())
        self.assertTrue((self.root / "_to_delete" / "junk.tmp").exists())
        self.assertTrue((self.root / "archive" / "old" / "x.txt").exists())
        self.assertFalse((self.root / "docs" / "old").exists())
        self.assertEqual(sorted(os.listdir(self.root / NFD)), ["2021", "b.jpg"])   # NFC 새 폴더가 따로 생기지 않음
        recs = A.read_journal(self.runs / "moves.jsonl")
        self.assertEqual([r["state"] for r in recs if r.get("state")], ["start", "done"] * 3)
        self.assertEqual(sum(r["op"] == "mkdir" for r in recs), 2)
        again = A.apply(self.plan_path, sha[:12])
        self.assertEqual(again["moved"], 0)
        self.assertIn("이미 옮김", again["skipped_items"][0]["why"])

    def test_plan_edited_after_review(self):
        sha, _ = self.plan3()
        d = json.loads(self.plan_path.read_text())
        d["items"][0]["dst"] = "elsewhere/a.jpg"
        self.plan_path.write_text(json.dumps(d))
        with self.assertRaises(PermissionError):
            A.apply(self.plan_path, sha[:12])

    def test_skips(self):
        sha, _ = self.plan3()
        (self.root / NFD / "a.jpg").write_text("changed!")                    # 계획 뒤 바뀜
        (self.root / "archive").mkdir()
        os.symlink(self.tmp, self.root / "archive" / "old")                    # dst 자리에 링크가 생김
        (self.root / "_to_delete" / "junk.tmp").write_text("someone")          # dst 가 생김
        res = A.apply(self.plan_path, sha[:12])
        self.assertEqual(res["moved"], 0)
        whys = " ".join(s["why"] for s in res["skipped_items"])
        self.assertIn("계획 뒤 바뀜", whys)
        self.assertIn("이미 있다", whys)
        self.assertTrue((self.root / "junk.tmp").exists())

    def test_symlink_in_dst_path(self):
        self.add({"op": "move", "src": "docs/r.pdf", "dst": "via/deep/r.pdf", "reason": ""})
        sha, _ = A.review(self.plan_path)
        os.symlink(self.tmp, self.root / "via")                                # 계획 뒤 경로 중간에 링크
        res = A.apply(self.plan_path, sha[:12])
        self.assertIn("심볼릭 링크", res["skipped_items"][0]["why"])
        self.assertFalse((self.tmp / "deep").exists())

    def test_exdev_and_case_taken(self):
        self.add({"op": "move", "src": "docs/r.pdf", "dst": "Z/r.pdf", "reason": ""})
        sha, _ = A.review(self.plan_path)
        with mock.patch.object(A.os, "rename", side_effect=OSError(18, "Cross-device link")):
            res = A.apply(self.plan_path, sha[:12])
        self.assertIn("다른 파일 시스템", res["skipped_items"][0]["why"])
        self.assertTrue((self.root / "docs" / "r.pdf").exists())
        par = self.tmp / "p"
        par.mkdir()
        (par / "Photo.JPG").write_text("x")
        self.assertEqual(A._name_taken(par, "photo.jpg", True), "Photo.JPG")
        self.assertIsNone(A._name_taken(par, "photo.jpg", False) if not names.case_insensitive(par) else None)

    def test_reconcile_interrupted(self):
        sha, _ = self.plan3()
        j = self.runs / "moves.jsonl"
        os.rename(self.root / "junk.tmp", self.root / "_to_delete" / "junk.tmp")   # rename 은 됐는데 done 을 못 씀
        j.write_text(json.dumps({"op": "to_delete", "seq": 2, "id": 2, "src": "junk.tmp", "dst": "_to_delete/junk.tmp",
                                 "type": "file", "size": 1, "mtime_ns": 0, "state": "start"}) + "\n{broken")
        res = A.apply(self.plan_path, sha[:12])
        self.assertEqual(res["recovered"][0]["state"], "done")
        self.assertEqual(res["moved"], 2)
        self.assertIn("이미 옮김", " ".join(s["why"] for s in res["skipped_items"]))

    def test_no_destructive_calls(self):
        for mod in (A, PL):
            src = Path(mod.__file__).read_text()
            code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
            code = re.sub(r'"""[\s\S]*?"""', "", code)
            for bad in ("unlink(", "rmdir(", "rmtree", "shutil", "remove(", "copy", "replace(src", "truncate"):
                self.assertNotIn(bad, code, (mod.__name__, bad))


class Cli(Base):
    def test_cli(self):
        from stewart.cli import main
        self.add({"op": "move", "src": "docs/r.pdf", "dst": "D/r.pdf", "reason": ""})
        self.assertEqual(main(["organize", "review", str(self.plan_path)]), 0)
        sha = PL.sha256_file(self.plan_path)
        self.assertEqual(main(["organize", "apply", str(self.plan_path), "--approve", "deadbeef0000"]), 2)
        self.assertEqual(main(["organize", "apply", str(self.plan_path), "--approve", sha[:12]]), 0)
        self.assertTrue((self.root / "D" / "r.pdf").exists())


if __name__ == "__main__":
    unittest.main()
