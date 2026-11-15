"""판 158: 큰 폴더에서 search·glob·자르기.

- search: rg 를 먼저 여러 스레드로 돌린다(--sort 를 쓰지 않는다). 맞은 줄이 많으면 경로순 한 스레드로 다시 돌려 앞에서 멈춘다.
  어느 길이든 결과는 경로순 앞부분으로, 돌릴 때마다 같고 파이썬 방식과 같다(이름에 '-'·'.' 가 섞인 폴더 포함).
- search·glob: 시간 상한에 닿으면 "다 못 봤다(시간 초과 …)" 안내가 답 끝에 남는다.
- 글자 상한으로 자를 때도 "넘어서 멈춤·더 있다·이어 읽을 offset" 안내 줄은 끝에 그대로 붙는다.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import tools as T

HAS_RG = shutil.which("rg") is not None


def tree(root: Path, dirs=12, files=60, long=False) -> Path:
    for d in range(dirs):
        for s in ("a", "a-b", "a.c", "b"):
            p = root / f"d{d:02d}" / s
            p.mkdir(parents=True, exist_ok=True)
            for f in range(files):
                body = "".join(f"line {i} needle {'x' * (380 if long else 10)}\n" if i % 3 == 0 else f"line {i}\n" for i in range(9))
                (p / f"f{f:03d}.txt").write_text(body, encoding="utf-8")
    return root


class TSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tree(Path(tempfile.mkdtemp(prefix="u158-")), dirs=6, files=40)   # 960 파일, 맞은 줄 2,880
        cls.env = T.ToolEnv(workdir=cls.root)

    def run_search(self, rg=True, **kw):
        with mock.patch.dict(os.environ, {} if rg else {"STEWART_NO_RG": "1"}):
            ok, out = T.search(self.env, "needle", **kw)
        self.assertTrue(ok, out)
        return out

    @unittest.skipUnless(HAS_RG, "rg 없음")
    def test_rg_threads_and_same_as_python(self):
        seen = []
        real = subprocess.Popen

        def rec(cmd, *a, **k):
            seen.append(list(cmd))
            return real(cmd, *a, **k)
        with mock.patch.object(subprocess, "Popen", side_effect=rec):
            out1 = self.run_search()
        self.assertTrue(seen and "--sort" not in seen[0], seen)                          # 먼저 여러 스레드(정렬은 rg 밖에서)
        self.assertTrue(len(seen) == 2 and "--sort" in seen[1], seen)                    # 많으면 경로순 한 스레드로 다시
        for kw in ({}, {"output": "files"}, {"output": "count"}, {"context": 1}, {"glob": "**/a-b/*.txt"}):
            a, b = self.run_search(**kw), self.run_search(rg=False, **kw)
            with mock.patch.object(T, "SEARCH_SWITCH", 10 ** 9):
                c = self.run_search(**kw)                                               # 여러 스레드로 끝까지
            with mock.patch.object(T, "SEARCH_SWITCH", 0):
                d = self.run_search(**kw)                                               # 곧바로 경로순
            self.assertEqual(a, b, kw)                                                  # 경로순 앞부분 = 파이썬 방식
            self.assertEqual((c, d), (a, a), kw)                                        # 두 길이 같은 답
            self.assertEqual(a, self.run_search(**kw), kw)                              # 돌릴 때마다 같다
        self.assertEqual(out1, self.run_search())
        lines = out1.splitlines()
        self.assertEqual(len(lines), T.SEARCH_MAX + 1)
        self.assertTrue(lines[0].startswith("d00/a/f000.txt:1:"), lines[0])
        self.assertIn(f"{T.SEARCH_MAX}줄 넘어서 멈춤", lines[-1])

    def test_timeout_note(self):
        for rg in ((True, False) if HAS_RG else (False,)):
            with mock.patch.object(T, "SEARCH_TIMEOUT_S", 0):
                out = self.run_search(rg=rg)
            self.assertIn("시간 초과로 멈춤 — 다 못 봤다", out.splitlines()[-1], (rg, out[-300:]))


class TClipKeepsNotes(unittest.TestCase):
    def test_search_note_survives_clip(self):
        root = tree(Path(tempfile.mkdtemp(prefix="u158l-")), dirs=4, files=30, long=True)
        for rg in ((True, False) if HAS_RG else (False,)):
            with mock.patch.dict(os.environ, {} if rg else {"STEWART_NO_RG": "1"}):
                ok, out = T.search(T.ToolEnv(workdir=root), "needle")
            self.assertGreater(T.SEARCH_MAX * 300, T.MAX_OUT)                            # 글자 상한으로 잘리는 꼴
            self.assertIn("…(잘림: 본문", out)
            self.assertIn(f"{T.SEARCH_MAX}줄 넘어서 멈춤", out.splitlines()[-1])           # 안내 줄은 끝에 남는다
            self.assertLessEqual(len(out), T.MAX_OUT + 200)

    def test_read_file_offset_continues(self):
        root = Path(tempfile.mkdtemp(prefix="u158r-"))
        (root / "big.txt").write_text("".join(f"줄 {i:04d} " + "가" * 90 + "\n" for i in range(1, 1001)), encoding="utf-8")
        env = T.ToolEnv(workdir=root)
        ok, out = T.read_file(env, "big.txt")
        last = out.splitlines()[-1]
        self.assertIn("1000줄 중 1~", last)
        nxt = int(last.rsplit("offset=", 1)[1].rstrip(")"))
        self.assertLessEqual(len(out), T.MAX_OUT)
        self.assertIn(f"줄 {nxt - 1:04d}", out.splitlines()[-2])                         # 줄 가운데서 자르지 않는다
        ok, out2 = T.read_file(env, "big.txt", offset=nxt)
        self.assertTrue(out2.splitlines()[0].strip().startswith(f"{nxt}\t줄 {nxt:04d}"), out2[:80])   # 이어 읽기가 맞다

    def test_list_dir_note_survives(self):
        root = Path(tempfile.mkdtemp(prefix="u158d-"))
        for i in range(600):
            (root / (f"{i:04d}-" + "n" * 40 + ".txt")).write_text("x", encoding="utf-8")
        ok, out = T.list_dir(T.ToolEnv(workdir=root), ".", recursive=True)
        self.assertIn("500개 넘어서 멈춤", out.splitlines()[-1])

    def test_glob_notes_survive_and_timeout(self):
        root = Path(tempfile.mkdtemp(prefix="u158g-"))
        deep = root / ("긴폴더이름" * 6) / ("더긴폴더" * 6)
        deep.mkdir(parents=True)
        for i in range(1200):
            (deep / f"{i:05d}.txt").write_text("x", encoding="utf-8")
        env = T.ToolEnv(workdir=root)
        ok, out = T.glob_files(env, "**/*.txt", sort="name")
        self.assertIn("…(잘림: 본문", out)
        self.assertIn("1200개 중 1000개만 보임", out.splitlines()[-1])
        with mock.patch.object(T, "GLOB_TIMEOUT_S", 0):
            ok, out = T.glob_files(env, "**/*.txt")
        self.assertIn("시간 초과로 멈춤 — 다 못 봤다", out.splitlines()[-1])


if __name__ == "__main__":
    unittest.main()
