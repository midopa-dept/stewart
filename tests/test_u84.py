"""판 106(SW-45 ⑤·⑫): 그림 밖 파일 올리기(작업 폴더 inbox/, 덮지 않음·이름 다듬기·비밀 꼴 거부·상한), ask_user 선택지가 질문 줄의
options 로 달림(화면 단추)."""

import base64
from pathlib import Path
from unittest import mock

from tests import test_hub_ui as HU
from tests.test_hub import msg


class TWeb(HU.TWebSessions):
    locals().update({k: None for k in dir(HU.TWebSessions) if k.startswith("test")})

    def up(self, name, data=b"%PDF-1.4 x"):
        return self.post("/api/main/upload", name=name, data=base64.b64encode(data).decode())

    def test_upload(self):
        wd = Path(self.main.sess.env.workdir)
        code, r = self.up("회의록 11월.pdf")
        self.assertEqual((code, r["path"]), (200, "inbox/회의록 11월.pdf"))
        self.assertEqual((wd / "inbox" / "회의록 11월.pdf").read_bytes(), b"%PDF-1.4 x")
        self.assertEqual(self.up("회의록 11월.pdf")[1]["path"], "inbox/회의록 11월-2.pdf")   # 덮지 않음
        self.assertEqual(self.up("../../etc/passwd")[1]["path"], "inbox/passwd")              # 경로는 이름만
        self.assertEqual(self.up("server.pem")[0], 400)                                            # 비밀 꼴
        self.assertEqual(self.post("/api/main/upload", name="a.txt", data="!!")[0], 400)
        with mock.patch("stewart.web.UPLOAD_MAX", 3):
            self.assertEqual(self.up("big.bin")[0], 413)

    def test_ask_options(self):
        self.main.sess.tool_names.append("ask_user")                               # 시험 프로필에는 없다
        self.r.scripts["MAIN"] = [msg(calls=[("ask_user", {"question": "어느 쪽으로 할까요?", "options": ["A 안", "B 안"]})])]
        self.main.send("정해서 해 줘")
        self.assertTrue(self.main.wait_idle())
        a = [m for m in self.main.state()["main_log"] if m["kind"] == "assistant"][-1]
        self.assertIn("어느 쪽으로", a["text"])
        self.assertEqual(a["options"], ["A 안", "B 안"])


if __name__ == "__main__":
    import unittest
    unittest.main()
