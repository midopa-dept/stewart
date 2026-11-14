"""판 136: 안쪽 백엔드의 키 자리가 비었다고 등급을 막지 않는다 · 허브 새 대화의 '기본 모델'은 허브가 켜진 모델.

- 안쪽(바깥 아닌) 백엔드에 api_key_env 를 적었는데 그 환경 변수가 비면 키 없이 보낸다(예시 표의 "비어 있으면 키 없이 보냄").
  등급 표는 그것을 "키 없음"(막음)으로 봐서 사용자 화면의 presto·moderato·largo 가 모두 흐리게 막혔다.
  막는 것은 바깥 백엔드의 키 없음과, 찔러 본 서버의 인증 거부(401·403)뿐이다.
- 허브의 등급 표가 표에 없는 등급의 "기본 모델"을 백엔드 표의 default 로 잡아, 허브를 -b 로 다른 모델로 켜면 새 대화가 다른 모델로 갔다.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import tierview as TV
from stewart.config import Backend


class TKeyState(unittest.TestCase):
    def test_local_empty_env_not_blocking(self):
        b = Backend("cloud", "http://127.0.0.1:9/v1", "m", "q", 1000, api_key_env="TV136_EMPTY")
        with mock.patch.dict(os.environ, {"TV136_EMPTY": ""}):
            ks, where = TV.key_state(b, False)
        self.assertEqual(ks, TV.KEY_EMPTY_LOCAL)
        self.assertIn("TV136_EMPTY", where)
        with mock.patch.dict(os.environ, {"TV136_EMPTY": "x"}):
            self.assertEqual(TV.key_state(b, False)[0], "있음")

    def test_outside_empty_still_blocks(self):
        b = Backend("out", "https://api.example.invalid/v1", "m", "q", 1000, api_key_env="TV136_EMPTY", provider="Example")
        with mock.patch.dict(os.environ, {"TV136_EMPTY": ""}):
            self.assertEqual(TV.key_state(b, True)[0], "없음")

    def test_rows_and_text(self):
        d = Path(tempfile.mkdtemp())
        p = d / "backends.toml"
        p.write_text('default = "cloud"\n[backends.cloud]\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "m"\napi_key_env = "TV136_EMPTY"\n',
                     encoding="utf-8")
        with mock.patch.dict(os.environ, {"TV136_EMPTY": ""}):
            os.environ.pop("STEWART_INSTANCE", None)
            rows, _ = TV.load(p)
        r = {x["tier"]: x for x in rows}
        for t in ("presto", "moderato", "largo"):
            self.assertEqual((r[t]["key"], r[t]["status"]), (TV.KEY_EMPTY_LOCAL, "unchecked"))   # nokey 가 아니다
        out = TV.text(rows)
        self.assertIn("키 없이 보낸다", out)
        self.assertNotIn("키 없음 —", out)


class THubDefaultModel(__import__("tests.test_hub3", fromlist=["MainBase"]).MainBase):
    def setUp(self):
        super().setUp()
        p = self.tmp / "backends.toml"
        p.write_text(p.read_text(encoding="utf-8").replace('default = "fake"', 'default = "other"')
                     + '[backends.other]\nbase_url = "http://127.0.0.1:9/other/v1"\nmodel = "o"\n', encoding="utf-8")
        e = mock.patch.dict(os.environ, {"STEWART_BACKENDS": str(p)})
        e.start()
        self.addCleanup(e.stop)

    def test_unlisted_tier_uses_hub_model(self):
        rows = {r["tier"]: r for r in self.main.tier_rows()["rows"]}
        self.assertEqual(rows["moderato"]["backend"], "fake")                                   # 허브 모델(표의 default 는 other)
        c = self.main.new_chat("fresh", tier="moderato")
        self.assertEqual(c.sess.backend.name, "fake")


if __name__ == "__main__":
    unittest.main()
