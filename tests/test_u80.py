"""판 102(측정 준비): 실행 시작 사건에 기능 지문(켜진 손잡이·도구 목록·고정 앞부분 sha·토큰 어림·자기 점검·복구 켬끔),
편집 불일치는 tool_error_kinds 의 edit_mismatch 로 따로."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import engine
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_u60 import resp


def run(env):
    tmp = Path(tempfile.mkdtemp())
    (tmp / "a.txt").write_text("x\n", encoding="utf-8")
    p = Profile(name="code", path=tmp / "p.toml", system="지시", tools=["read_file", "edit_file"], workdir=tmp, max_steps=4)
    seq = [resp(calls=[("read_file", {"path": "a.txt"})]), resp(calls=[("edit_file", {"path": "a.txt", "old": "없는 글", "new": "y"})]), resp("끝")]
    with mock.patch.dict(os.environ, env), mock.patch.object(engine, "chat", side_effect=lambda *a, **k: seq.pop(0)):
        rec = Recorder(tmp / "runs", "code")
        s = engine.run(p, Backend("f", "http://x/v1", "m", "q", 100000), "a.txt 를 고쳐라", rec)
    ev = [json.loads(x) for x in (rec.dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    return s, next(e for e in ev if e["kind"] == "run_start")["meta"]["features"]


class TFingerprint(unittest.TestCase):
    def test_fingerprint_and_edit_kind(self):
        s, f = run({"STEWART_TOOLCALL_REPAIR": "1", "STEWART_REVIEW_ON_STOP": "0"})
        self.assertEqual(f["path"], "run")
        self.assertEqual(f["tools"], ["read_file", "edit_file"])
        self.assertEqual(f["knobs"]["STEWART_TOOLCALL_REPAIR"], "1")
        self.assertTrue(f["repair"])
        self.assertFalse(f["selfcheck"]["review_on_stop"])
        self.assertEqual(len(f["prefix_sha"]), 16)
        self.assertGreater(f["prefix_tokens"], 50)
        self.assertEqual(s["tool_error_kinds"].get("edit_mismatch"), 1)
        _, f2 = run({"STEWART_TOOLCALL_REPAIR": "0"})
        self.assertEqual(f2["prefix_sha"], f["prefix_sha"])                    # 손잡이만 다르면 고정 앞부분은 같다
        self.assertFalse(f2["repair"])


if __name__ == "__main__":
    unittest.main()
