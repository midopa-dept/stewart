"""판 111(SW-45 ⑩): [상태] 탭 — /api/status_info 가 켜진 손잡이(환경에 값 있는 것만)·도구·고정 앞부분 토큰·모드·문맥 예산을 준다(로그인)."""

import json
import os
from unittest import mock

from tests import test_hub_ui as HU


class TWeb(HU.TWebSessions):
    locals().update({k: None for k in dir(HU.TWebSessions) if k.startswith("test")})

    def test_status(self):
        with mock.patch.dict(os.environ, {"STEWART_WEB_NUDGE": "0"}):
            r, body = self.req("GET", "/api/status_info", headers={"Cookie": self.ck})
        d = json.loads(body)
        self.assertEqual(r.status, 200)
        self.assertEqual(d["knobs"].get("STEWART_WEB_NUDGE"), "0")
        self.assertIn("read_file", d["tools"])
        self.assertGreater(d["prefix_tokens"], 100)
        self.assertEqual(d["mode"], "edits")
        self.assertIn("STEWART_SPAWN", d["all_knobs"])
        self.assertNotEqual(self.req("GET", "/api/status_info")[0].status, 200)
