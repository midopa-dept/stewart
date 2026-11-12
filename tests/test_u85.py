"""판 107: 도구가 체크포인트 밖에서 만든 파일(env.turn_extra_files)도 차례 끝 "만든 파일" 카드(ui.turn_files)에 실린다."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_u60 import resp


class UI(FakeUI):
    def __init__(self):
        super().__init__()
        self.files = []

    def turn_files(self, paths):
        self.files.append(paths)


class TExtra(unittest.TestCase):
    def test_extra_files_reach_card(self):
        tmp = Path(tempfile.mkdtemp())
        p = Profile(name="chat", path=tmp / "p.toml", system="s", tools=["read_file"], workdir=tmp, max_steps=3)
        ui = UI()
        s = C.ChatSession(p, Backend("f", "http://x/v1", "m", "q", 100000), Recorder(tmp / "runs", "c"), ui, stream=False, mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        s.env.turn_extra_files.append("out/abc.pdf")
        with mock.patch.object(C, "chat", side_effect=lambda *a, **k: resp("끝")):
            s.turn("x")
        self.assertEqual(ui.files, [["out/abc.pdf"]])
        self.assertEqual(s.env.turn_extra_files, [])
        with mock.patch.object(C, "chat", side_effect=lambda *a, **k: resp("끝")):
            s.turn("y")
        self.assertEqual(len(ui.files), 1)                                         # 다음 차례에는 없다


if __name__ == "__main__":
    unittest.main()
