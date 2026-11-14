"""판 138: 메인 모드가 edits(허브 기본)일 때 동시 작업자(workers)가 시작하자마자 실패하던 것.

- 까닭: 동시 작업자에게 '묻는 함수'가 없어 엔진이 무인 실행으로 보고, edits·ask 는 무인에 쓸 수 없다며 ConfigError 로 끝났다.
- 고친 꼴: 동시 작업자의 묻기 자리는 사람에게 묻지 않고 늘 거절한다(chat._spawn_no_ask). 그래서 edits 에서 파일 편집은 바로 돌고,
  승인이 필요한 행동(허용 규칙 밖의 셸 등)은 하지 않고 그 사실을 도구 결과로 받는다.
"""

import time
import unittest

from tests.test_hub import msg
from tests.test_hub3 import MainBase


class TSpawnEdits(MainBase):
    def setUp(self):
        super().setUp()
        from stewart.cli import resolve
        from stewart.hubmain import HubMain
        from stewart.record import Recorder
        (self.tmp / "main2.toml").write_text('name = "main2"\ntools = ["read_file", "write_file", "run_shell", "delegate"]\n'
                                             'max_steps = 10\nsystem = "MAIN 지시"\n', encoding="utf-8")   # delegate 가 있어야 workers 도 실린다
        profile, backend = resolve(str(self.tmp / "main2.toml"), "fake", str(self.wd))
        self.main.close()
        self.main = HubMain(self.hub, profile, backend, Recorder(self.tmp / "runs", "hub-main2"), mode="edits",
                            worker_profile=str(self.tmp / "w.toml"), worker_backend="fake")
        self.addCleanup(self.main.close)

    def test_worker_runs_in_edits_mode(self):
        self.assertEqual(self.main.sess.mode, "edits")
        self.r.scripts["MAIN"] = [msg(calls=[("workers", {"action": "spawn", "task": "a.txt 를 쓰고 셸로 확인하라",
                                                         "tools": ["write_file", "run_shell"]})]),
                                  msg(calls=[("workers", {"action": "wait"})]), msg("끝")]
        self.r.scripts["WORKER"] = [msg(calls=[("write_file", {"path": "a.txt", "content": "A"})]),
                                    msg(calls=[("run_shell", {"command": "touch b.txt"})]), msg("a.txt 를 썼다")]
        self.assertEqual(self.main.send("맡겨라"), "started")
        self.assertTrue(self.main.wait_idle())
        t0 = time.time()
        while time.time() - t0 < 10 and len(self.r.requests["WORKER"]) < 3:
            time.sleep(0.1)
        self.assertEqual(len(self.r.requests["WORKER"]), 3)                                      # 작업자가 떴다(전에는 0)
        self.assertEqual((self.wd / "a.txt").read_text(), "A")                                   # edits: 파일 편집은 바로
        self.assertFalse((self.wd / "b.txt").exists())                                           # 셸은 묻지 않고 하지 않았다
        tool = [m for m in self.r.requests["WORKER"][2]["messages"] if m.get("role") == "tool"][-1]["content"]
        self.assertIn("사람에게 묻지 못해", tool)


if __name__ == "__main__":
    unittest.main()
