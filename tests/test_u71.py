"""판 92(SW-41 ④ 판 A): 예시 은행 — examples.md 꼴(더하기·읽기·지우기), 사람 말에 가까운 하나만 BM25 로(문턱), chat 차례 시작에
사람 말 끝으로 붙이고 example_used 를 남김(STEWART_EXAMPLES=0 이면 안 붙음), 학습 내보내기는 그 블록을 뺌, CLI 목록·지우기."""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, examples as EX, train as TRAIN
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_u60 import resp

PYTEST = ("pytest 실패를 고칠 때", "상황: 시험이 실패한다\n한 일: run_shell \"pytest -q -x\" 로 첫 실패만 보고, 그 파일을 read_file 로 읽고 edit_file\n결과: 시험 통과")
CSV = ("csv 열 합계를 낼 때", "상황: csv 파일의 열 합계\n한 일: run_shell 로 python3 -c 의 csv 모듈\n결과: 합계를 답에 적음")


class TBank(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_add_load_remove_and_pick(self):
        a = EX.add(self.d, *PYTEST, src="abc#3")
        EX.add(self.d, *CSV)
        self.assertEqual(EX.add(self.d, *PYTEST)["id"], a["id"])                    # 같은 것은 두 번 안 붙음
        (self.d / "examples.md").write_text((self.d / "examples.md").read_text() + "\n## 꼴이 틀린 머리\n본문\n", encoding="utf-8")
        xs = EX.load(self.d)
        self.assertEqual([x["title"] for x in xs], [PYTEST[0], CSV[0]])
        self.assertEqual(xs[0]["meta"]["src"], "abc#3")
        hit = EX.pick(xs, "pytest 가 실패하는데 고쳐 줘")
        self.assertEqual(hit[0]["id"], a["id"])
        self.assertIsNone(EX.pick(xs, "오늘 날씨 어때"))
        self.assertIn("[예시]", EX.block(hit[0]))
        self.assertTrue(EX.remove(self.d, a["id"]))
        self.assertEqual([x["title"] for x in EX.load(self.d)], [CSV[0]])
        self.assertFalse(EX.remove(self.d, a["id"]))


class TChat(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        e = mock.patch.dict(os.environ, {"STEWART_MEMORY": "1", "STEWART_MEMORY_DIR": str(self.tmp / "mem")})
        e.start()
        self.addCleanup(e.stop)

    def run_turn(self, text, **env):
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["run_shell"], workdir=self.tmp, max_steps=3)
        with mock.patch.dict(os.environ, env):
            s = C.ChatSession(p, Backend("f", "http://x/v1", "m", "q", 100000), Recorder(self.tmp / "runs", "c"), FakeUI(),
                              stream=False, mode="auto")
            self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
            EX.add(s.mem.store.dir, *PYTEST)
            sent = []
            with mock.patch.object(C, "chat", side_effect=lambda b, m, *a, **k: (sent.append(json.loads(json.dumps(m))), resp("했다"))[1]):
                s.turn(text)
        return s, sent

    def test_attach_one_and_off(self):
        s, sent = self.run_turn("pytest 실패 고쳐 줘")
        self.assertIn("[예시]", sent[0][-1]["content"])
        self.assertIn("pytest -q -x", sent[0][-1]["content"])
        self.assertNotIn("[예시]", sent[0][0]["content"])                          # 지시문은 그대로(접두 캐시)
        self.assertIn('"kind": "example_used"', (s.rec.dir / "events.jsonl").read_text())
        _, sent = self.run_turn("pytest 실패 고쳐 줘", STEWART_EXAMPLES="0")
        self.assertNotIn("[예시]", sent[0][-1]["content"])
        _, sent = self.run_turn("오늘 할 일 정리해 줘")
        self.assertNotIn("[예시]", sent[0][-1]["content"])

    def test_export_strips_block(self):
        s, _ = self.run_turn("pytest 실패 고쳐 줘")
        s.label(1, "good")
        s.rec._ev.close()
        out = self.tmp / "o"
        TRAIN.export(self.tmp / "runs", out, "good", include_tiers={"train-ok", "unknown"})   # 시험 백엔드 주소(x)는 바깥으로 침
        line = (out / "train.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("[예시]", line)
        self.assertIn("pytest 실패 고쳐 줘", line)


class TCli(unittest.TestCase):
    def test_list_and_rm(self):
        d = Path(tempfile.mkdtemp())
        from stewart import cli
        from stewart.memory import USER
        with mock.patch.dict(os.environ, {"STEWART_MEMORY_DIR": str(d)}):
            a = EX.add(d / USER["key"], *CSV)
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                self.assertEqual(cli.main(["memory", "examples"]), 0)
                self.assertEqual(cli.main(["memory", "examples", "--rm", a["id"]]), 0)
            self.assertIn("예시 1개", buf.getvalue())
            self.assertEqual(EX.load(d / USER["key"]), [])


if __name__ == "__main__":
    unittest.main()
