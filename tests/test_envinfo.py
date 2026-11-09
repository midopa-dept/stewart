"""환경 블록: OS·셸·작업 폴더·날짜·git·파이썬 명령. 세션 시작 때 한 번, 이어 하기 때 다시. 환경 변수 값은 넣지 않는다."""

import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from stewart import chat as C, engine, envinfo
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_engine import _resp


class TEnvInfo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bk = Backend("fake", "http://x/v1", "m", "q", 4096)

    def prof(self):
        return Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["read_file"], workdir=self.tmp, max_steps=5)

    def test_block_fields(self):
        with mock.patch.dict(os.environ, {"STEWART_TEST_SECRET": "값-보이면-안됨"}):
            info = envinfo.measure(self.tmp, today=date(2026, 11, 9))
            b = envinfo.block(info)
        for part in ("OS: ", "셸: run_shell 은 /bin/bash", f"작업 폴더: {self.tmp}\n", "git 저장소 안: 아니오",
                     "오늘 날짜: 2026-11-09", "파이썬: python3 "):
            self.assertIn(part, b)
        self.assertNotIn("값-보이면-안됨", b)
        self.assertNotIn("STEWART_TEST_SECRET", b)

    def test_git_detected(self):
        if not shutil.which("git"):
            self.skipTest("git 없음")
        subprocess.run(["git", "init", "-q"], cwd=self.tmp, check=True)
        self.assertTrue(envinfo.measure(self.tmp)["git"])

    def test_python_hint(self):
        real = shutil.which
        with mock.patch.object(envinfo.shutil, "which", side_effect=lambda n: None if n == "python" else real(n)):
            b = envinfo.block(envinfo.measure(self.tmp))
        if real("python3"):
            self.assertIn("python 없음 → 파이썬은 `python3` 로 부른다", b)
        with mock.patch.object(envinfo.shutil, "which", side_effect=lambda n: None if n.startswith("python") else real(n)):
            self.assertIn("파이썬 명령이 없다", envinfo.block(envinfo.measure(self.tmp)))

    def test_chat_measures_once_and_resume_again(self):
        with mock.patch.object(envinfo, "measure", wraps=envinfo.measure) as ms:
            s = C.ChatSession(self.prof(), self.bk, Recorder(self.tmp / "runs", "chat-e"), FakeUI())
            self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
            with mock.patch.object(C, "chat", side_effect=[_resp("하나"), _resp("둘")]) as m:
                s.turn("1")
                s.turn("2")
            sys1 = [c[0][1][0]["content"] for c in m.call_args_list]
            self.assertEqual(ms.call_count, 1)
            self.assertEqual(sys1[0], sys1[1])                      # 차례마다 지시문이 그대로(cache)
            self.assertNotIn("환경(세션 시작 때 잰 값)", sys1[0])      # 다이어트 안 a
            users = [x["content"] for x in m.call_args_list[1][0][1] if x["role"] == "user"]
            self.assertTrue(users[0].startswith("[엔진이 붙인 환경 정보"))   # 첫 입력에만 붙는다
            self.assertEqual(users[1], "2")
            with mock.patch.object(envinfo, "date") as d:
                d.today.return_value = date(2030, 1, 2)
                s2 = C.ChatSession(self.prof(), self.bk, Recorder(self.tmp / "runs", "chat-f"), FakeUI(), history=s.messages,
                                   resumed_from="x")
                self.addCleanup(lambda: s2.rec._ev.closed or s2.rec._ev.close())
            self.assertEqual(ms.call_count, 2)                      # 이어 하기는 다시 잰다
        self.assertIn("오늘 날짜: 2030-01-02", s2._env_note)
        self.assertEqual(s2.meta["env_info"]["date"], "2030-01-02")

    def test_run_measures_once(self):
        (self.tmp / "a.txt").write_text("x")
        seq = [_resp(calls=[("read_file", '{"path": "a.txt"}')]), _resp(calls=[("read_file", '{"path": "a.txt"}')]), _resp("끝")]
        with mock.patch.object(envinfo, "measure", wraps=envinfo.measure) as ms, \
                mock.patch.object(engine, "chat", side_effect=seq) as m:
            s = engine.run(self.prof(), self.bk, "읽어", Recorder(self.tmp / "runs", "t"))
        self.assertEqual(ms.call_count, 1)
        self.assertEqual(len({c[0][1][0]["content"] for c in m.call_args_list}), 1)
        self.assertEqual(s["stop_reason"], "completed")


if __name__ == "__main__":
    unittest.main()
