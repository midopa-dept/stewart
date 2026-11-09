"""문맥 넘침을 종료 사유 context_overflow 로 따로 남기는지(engine.run·chat 두 경로)."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, engine
from stewart.backend import BackendError, is_context_overflow
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI

VLLM_BODY = ('{"object":"error","message":"This model\'s maximum context length is 150000 tokens. However, you '
             'requested 151234 tokens (143042 in the messages, 8192 in the completion). Please reduce the length of '
             'the messages or completion.","type":"BadRequestError","param":null,"code":400}')
LLAMA_BODY = '{"error":{"code":400,"message":"the request exceeds the available context size, try increasing it","type":"exceed_context_size_error"}}'


class TOverflow(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bk = Backend("fake", "http://x/v1", "m", "q", 4096)

    def prof(self, name="t"):
        return Profile(name=name, path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.tmp, max_steps=5)

    def test_detect(self):
        self.assertTrue(is_context_overflow(BackendError("HTTP 400", 400, VLLM_BODY)))
        self.assertTrue(is_context_overflow(BackendError("HTTP 400", 400, LLAMA_BODY)))
        # 다른 400(인자 오류)·연결 실패·5xx 는 넘침이 아니다
        self.assertFalse(is_context_overflow(BackendError("HTTP 400", 400, '{"message":"invalid tool_choice"}')))
        self.assertFalse(is_context_overflow(BackendError("연결 실패: timed out")))
        self.assertFalse(is_context_overflow(BackendError("HTTP 500", 500, "maximum context length")))

    def test_engine_run(self):
        with mock.patch.object(engine, "chat", side_effect=BackendError("HTTP 400", 400, VLLM_BODY)):
            s = engine.run(self.prof(), self.bk, "do", Recorder(self.tmp / "runs", "t"))
        self.assertEqual((s["stop_reason"], s["success"]), ("context_overflow", False))

    def test_engine_other_error_stays(self):
        with mock.patch.object(engine, "chat", side_effect=BackendError("HTTP 500", 500, "boom")):
            s = engine.run(self.prof(), self.bk, "do", Recorder(self.tmp / "runs", "t"))
        self.assertEqual(s["stop_reason"], "backend_error")

    def test_chat(self):
        ui = FakeUI()
        s = C.ChatSession(self.prof("chat"), self.bk, Recorder(self.tmp / "runs", "chat-t"), ui)
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        with mock.patch.object(C, "chat", side_effect=BackendError("HTTP 400", 400, LLAMA_BODY)):
            self.assertEqual(s.turn("길게"), "context_overflow")
        self.assertIn("/compact", ui.out.getvalue())


if __name__ == "__main__":
    unittest.main()
