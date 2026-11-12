"""판 98: 허브에 보낸 사진이 대화에 보임 — 보낸 말 줄에 그림 이름(imgs), /api/img 로 원본, 허브를 다시 켜 화면 표가 비어도
그 허브 기록 폴더에서 찾아 줌, 기록 사건 user 에 img_names, 지난 대화 다시 그리기에도 imgs."""

import json
import unittest
from pathlib import Path

from tests import test_hub_ui as HU
from tests.test_hub import msg
from tests.test_u70 import png


class TWeb(HU.TWebSessions):
    locals().update({k: None for k in dir(HU.TWebSessions) if k.startswith("test")})

    def test_sent_photo_shows_and_survives(self):
        self.main.sess.backend.vision = True
        self.r.scripts["MAIN"] = [msg("빨간 네모다")]
        data = png(12, 8)
        self.assertEqual(self.main.send("이거 봐", images=[("a.png", data)]), "started")
        self.assertTrue(self.main.wait_idle())
        u = [m for m in self.main.state()["main_log"] if m["kind"] == "user"][-1]
        self.assertEqual(u["text"], "이거 봐")
        (name,) = u["imgs"]
        r, body = self.req("GET", "/api/img?n=" + name, headers={"Cookie": self.ck})
        self.assertEqual((r.status, r.getheader("Content-Type")), (200, "image/png"))
        self.main.ui.imgs.clear()                                                  # 허브를 다시 켠 꼴
        r, body2 = self.req("GET", "/api/img?n=" + name, headers={"Cookie": self.ck})
        self.assertEqual((r.status, body2), (200, body))
        evs = [json.loads(x) for x in (Path(self.main.sess.rec.dir) / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([e.get("img_names") for e in evs if e["kind"] == "user"], [[name]])
        items = [x for e in evs for x in self.web._conv_items(e) if x["kind"] == "user"]
        self.assertEqual(items[-1].get("imgs"), [name])
        self.assertEqual(self.req("GET", "/api/img?n=" + name)[0].status in (200,), False)   # 로그인 없이는 안 됨


if __name__ == "__main__":
    unittest.main()
