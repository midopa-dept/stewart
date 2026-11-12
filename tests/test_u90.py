"""판 112(SW-45 ⑦): 지난 대화 본문 찾기(사람용) — 사람 말·모델 답에서 낱말(대소문자 무시, 두 글자 이상), 대화마다 첫 맞음, 로그인."""

import json

from tests import test_hub_ui as HU
from tests.test_hub import msg


class TWeb(HU.TWebSessions):
    locals().update({k: None for k in dir(HU.TWebSessions) if k.startswith("test")})

    def test_search(self):
        self.r.scripts["MAIN"] = [msg("Zebra 프로젝트 설정을 봤다")]
        self.main.send("얼룩말 설정 봐 줘")
        self.assertTrue(self.main.wait_idle())
        self.main.new_main()                                                       # 지금 대화는 지난 대화로
        r, body = self.req("GET", "/api/past_search?q=zebra", headers={"Cookie": self.ck})
        hits = json.loads(body)["hits"]
        self.assertEqual(r.status, 200)
        self.assertEqual(len(hits), 1)
        self.assertEqual((hits[0]["role"], hits[0]["first"]), ("assistant", "얼룩말 설정 봐 줘"))
        self.assertIn("Zebra 프로젝트", hits[0]["snippet"])
        self.assertEqual(json.loads(self.req("GET", "/api/past_search?q=%EC%96%BC%EB%A3%A9%EB%A7%90", headers={"Cookie": self.ck})[1])["hits"][0]["role"], "user")
        self.assertEqual(json.loads(self.req("GET", "/api/past_search?q=z", headers={"Cookie": self.ck})[1])["hits"], [])
        self.assertNotEqual(self.req("GET", "/api/past_search?q=zebra")[0].status, 200)
