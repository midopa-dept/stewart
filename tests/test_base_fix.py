import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import engine
from stewart.backend import BackendError
from stewart.config import Backend, Profile, load_backends
from stewart.record import Recorder
from tests.test_engine import _resp


class TestBaseFix(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.backend = Backend("fake", "http://x/v1", "m", "fp16", 4096)
        self.prof = Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["*"], workdir=self.tmp, max_steps=5)

    def test_cloud_limits(self):
        b = load_backends()[0]["cloud"]
        self.assertEqual((b.params.get("max_tokens"), b.timeout_s), (8192, 240))

    def test_length_counted(self):
        r, dt = _resp("잘린 답")
        r["choices"][0]["finish_reason"] = "length"
        with mock.patch.object(engine, "chat", side_effect=[(r, dt)]):
            s = engine.run(self.prof, self.backend, "do", Recorder(self.tmp / "runs", "t"))
        self.assertEqual((s["length_stops"], s["backend_timeouts"]), (1, 0))

    def test_timeout_counted(self):
        with mock.patch.object(engine, "chat", side_effect=BackendError("연결 실패: timed out")):
            s = engine.run(self.prof, self.backend, "do", Recorder(self.tmp / "runs", "t"))
        self.assertEqual((s["stop_reason"], s["backend_timeouts"]), ("backend_error", 1))


if __name__ == "__main__":
    unittest.main()
