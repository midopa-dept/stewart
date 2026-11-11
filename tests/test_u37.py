"""판 50 실사용 기억 첫 조각: chat·허브는 기본 켬·run(평가)은 끔, 사람 말에서 다음에도 지킬 지시를 스스로 남김(사용자 전역),
다음 세션 지시문 맨 앞에, 허브 화면 "기억함" 한 줄·되돌리기, 작업자는 읽기만."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import memory as MEM
from stewart.config import Profile
from tests.test_hub import msg
from tests.test_hub3 import MainBase


def prof(tmp, memory=None):
    return Profile(name="chat", path=tmp / "p.toml", system="s", tools=["read_file"], workdir=tmp, memory=memory)


class TEnabled(unittest.TestCase):
    def test_defaults(self):
        tmp = Path(tempfile.mkdtemp())
        self.assertTrue(MEM.enabled(prof(tmp), env={}, interactive=True))    # chat·허브(실사용)
        self.assertFalse(MEM.enabled(prof(tmp), env={}))                      # run·bench·평가
        self.assertFalse(MEM.enabled(prof(tmp, memory=False), env={}, interactive=True))
        self.assertFalse(MEM.enabled(prof(tmp), env={"STEWART_MEMORY": "0"}, interactive=True))
        self.assertTrue(MEM.enabled(prof(tmp), env={"STEWART_MEMORY": "1"}))

    def test_run_never_sees_memory(self):
        """평가 길(engine.run)은 기억이 남아 있어도 지시문·도구에 섞지 않는다."""
        from stewart import engine
        from stewart.config import Backend
        from stewart.record import Recorder
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.dict(os.environ, {"STEWART_MEMORY_DIR": str(tmp / "mem")}):
            os.environ.pop("STEWART_MEMORY", None)
            try:
                m = MEM.Memory(prof(tmp), tmp / "runs" / "r0")
                self.assertIsNotNone(m.remember("feedback", "앞으로는 .env 를 cat 하지 마라"))
                seen = []

                def fake(backend, messages, tools=None, **k):
                    seen.append((json.dumps(messages, ensure_ascii=False), [t["function"]["name"] for t in tools or []]))
                    return {"choices": [{"message": {"role": "assistant", "content": "끝"}, "finish_reason": "stop"}],
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}, 0.1
                with mock.patch.object(engine, "chat", side_effect=fake):
                    engine.run(prof(tmp), Backend("fake", "http://x/v1", "m", "q", 100000), "일해", Recorder(tmp / "runs", "t"))
            finally:
                os.environ["STEWART_MEMORY"] = "0"
        self.assertNotIn(".env 를 cat", seen[0][0])
        self.assertNotIn("note_add", seen[0][1])


class TDetect(unittest.TestCase):
    def test_detect(self):
        self.assertEqual(MEM.detect(".env cat 하지 마라."), [("feedback", ".env cat 하지 마라.")])
        self.assertEqual(MEM.detect("앞으로는 보고를 파일로 남겨라"), [("feedback", "앞으로는 보고를 파일로 남겨라")])
        self.assertEqual(MEM.detect("기억해: 배포는 금요일에 하지 않는다"), [("explicit", "배포는 금요일에 하지 않는다")])
        self.assertEqual(MEM.detect("나는 짧은 보고를 좋아한다"), [("user", "나는 짧은 보고를 좋아한다")])
        self.assertEqual(MEM.detect("이 파일 읽어 줘"), [])
        self.assertEqual(MEM.detect("앞으로 이걸 어떻게 할까?"), [])                       # 물음은 아님
        self.assertEqual(MEM.detect("기억해: 토큰 ghp_" + "a" * 36), [])                 # 비밀 꼴은 안 남김


class THub(MainBase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"STEWART_MEMORY": "1", "STEWART_MEMORY_DIR": tempfile.mkdtemp(prefix="mem-")})   # 시험마다 따로
        p.start()
        self.addCleanup(p.stop)
        super().setUp()

    def test_remember_show_undo_and_next_session(self):
        self.r.scripts["MAIN"] = [msg("알겠습니다.")]
        self.main.send("앞으로는 .env cat 하지 마라. 이번에는 README 만 봐 줘")   # 판 83: 오래 지킬 지시는 먼저 묻는다
        self.assertTrue(self.main.wait_idle())
        ask = [m for m in self.main.state()["main_log"] if m["kind"] == "memory_ask"]
        self.assertEqual(len(ask), 1)
        self.assertEqual([m for m in self.main.state()["main_log"] if m["kind"] == "memory"], [])
        it = self.main.sess.mem.confirm(ask[0]["mem_id"], True)                  # [허용]
        self.main.ui.memory_op("add", it)
        mem = [m for m in self.main.state()["main_log"] if m["kind"] == "memory"]
        self.assertEqual(len(mem), 1)
        self.assertIn(".env cat 하지 마라", mem[0]["text"])
        self.assertEqual(mem[0]["mem_kind"], "policy")
        # 다음 세션(새 메인): 지시문 맨 앞 기억에 들어 있다
        from stewart.cli import resolve
        from stewart.hubmain import HubMain
        from stewart.record import Recorder
        self.main.close()
        profile, backend = resolve(str(self.tmp / "main.toml"), "fake", str(self.wd))
        m2 = HubMain(self.hub, profile, backend, Recorder(self.tmp / "runs", "hub-main"), mode="edits")
        self.addCleanup(m2.close)
        self.assertIn(".env cat 하지 마라", m2.sess.system)
        self.assertIn("스스로 기억함", m2.sess.system)
        # 되돌리기(화면 [되돌리기] = /api/memory/forget)
        self.assertTrue(m2.sess.mem.forget(mem[0]["mem_id"]))
        self.assertEqual(m2.sess.mem.remembered(), [])
        self.assertFalse(m2.sess.mem.forget(mem[0]["mem_id"]))

    def test_worker_ui_reads_only(self):
        from stewart.hubmain import HubMainUI
        from stewart.worker import HubUI
        self.assertFalse(HubUI.memory_writes)
        self.assertTrue(HubMainUI.memory_writes)


if __name__ == "__main__":
    unittest.main()
