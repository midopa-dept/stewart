"""판 132: moderato 등급의 기본 생각 깊이(reasoning_effort) = medium.

- 표·환경에 값이 없을 때만, 그 등급 백엔드의 대화 틀이 medium 을 받을 때만(effort_values 에 medium) 싣는다.
  목록이 없거나 medium 이 없으면 지금처럼 보내지 않고 그 까닭을 보인다.
- 우선: 환경 STEWART_REASONING_EFFORT > 등급 칸 reasoning_effort > 등급 기본값. "none" = 보내지 않음(가장 깊게 — 서버 기본).
- presto·largo·rubato 와 등급 없는 실행은 그대로다.
- 보이기: stewart tiers 표·설정 시트(등급 줄 effort)·"이 대화" 판(허브 상태 effort) — 값과 출처.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import spawn as SP
from stewart import think as TH
from stewart import tierview as TV
from stewart.config import Backend, Profile


def B(name="b", effort_values=None, **kw):
    return Backend(name, "http://127.0.0.1:9/v1", "m", None, 32768, effort_values=effort_values, **kw)


OK = ["low", "medium"]


def prof(tmp, tier_raw=None):
    return Profile(name="x", path=Path(tmp) / "p.toml", system="s", tools=["read_file"], workdir=Path(tmp),
                   raw={"tier": tier_raw} if tier_raw is not None else None)


class TDefault(unittest.TestCase):
    def test_default_effort(self):
        self.assertEqual(SP.default_effort("moderato", {}, B(effort_values=OK)), ("medium", ""))
        v, why = SP.default_effort("moderato", {}, B(name="cloud"))
        self.assertIsNone(v)
        self.assertIn("effort_values", why)
        self.assertIn("cloud", why)
        v, why = SP.default_effort("moderato", None, B(effort_values=["low"]))
        self.assertIsNone(v)
        self.assertIn("medium 이 없다", why)
        for t in ("presto", "largo", "rubato", "", None):
            self.assertEqual(SP.default_effort(t, {}, B(effort_values=OK)), (None, ""))
        self.assertEqual(SP.default_effort("moderato", {"reasoning_effort": "low"}, B(effort_values=OK)), (None, ""))   # 표가 이긴다
        self.assertEqual(SP.default_effort("moderato", {"reasoning_effort": "none"}, B(effort_values=OK)), (None, ""))

    def test_tier_raw(self):
        r = SP.tier_raw({"name": "moderato", "budget": 32000}, B(effort_values=OK))
        self.assertEqual(r["think"], {"reasoning_effort": "medium", "effort_default": True})
        r2 = SP.tier_raw({"name": "moderato", "think": {"think_max_tokens": 4096}}, B())
        self.assertEqual(r2["think"], {"think_max_tokens": 4096})
        self.assertIn("effort_values", r2["effort_default_skipped"])
        self.assertNotIn("think", SP.tier_raw({"name": "largo"}, B(effort_values=OK)))
        self.assertNotIn("think", SP.tier_raw({"name": "moderato"}))                            # 백엔드를 모르면 싣지 않는다


class TSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.b = B(effort_values=OK, params={"chat_template_kwargs": {"enable_thinking": True}})

    def s(self, raw, env=None):
        return TH.settings(prof(self.tmp, raw), env=env or {})

    def test_default_is_sent(self):
        s = self.s(SP.tier_raw({"name": "moderato"}, self.b))
        self.assertEqual((s["effort"], s["source"]["effort"]), ("medium", "default"))
        self.assertEqual(TH.call_kwargs(self.b, s, None, 100)["overrides"]["reasoning_effort"], "medium")
        self.assertEqual(TH.call_kwargs(self.b, s, False, 100), {})                              # 생각 끈 요청에는 안 싣는다

    def test_env_and_table_win(self):
        raw = SP.tier_raw({"name": "moderato"}, self.b)
        s = self.s(raw, {"STEWART_REASONING_EFFORT": "none"})                                   # 되돌리기 한 줄(환경)
        self.assertNotIn("effort", s)
        self.assertEqual(s["source"]["effort"], "env:off")
        self.assertEqual(TH.call_kwargs(self.b, s, None, 100).get("overrides", {}).get("reasoning_effort"), None)
        s = self.s(raw, {"STEWART_REASONING_EFFORT": "low"})
        self.assertEqual((s["effort"], s["source"]["effort"]), ("low", "env"))
        s = self.s(SP.tier_raw({"name": "moderato", "think": {"reasoning_effort": "none"}}, self.b))   # 되돌리기 한 줄(표)
        self.assertNotIn("effort", s)
        self.assertEqual(s["source"]["effort"], "tier:off")
        s = self.s(SP.tier_raw({"name": "moderato", "think": {"reasoning_effort": "low"}}, self.b))
        self.assertEqual((s["effort"], s["source"]["effort"]), ("low", "tier"))

    def test_skipped_note_and_others_unchanged(self):
        s = self.s(SP.tier_raw({"name": "moderato"}, B(name="cloud")))
        self.assertNotIn("effort", s)
        self.assertIn("effort_values", s["effort_note"])
        self.assertNotIn("effort_note", self.s(SP.tier_raw({"name": "moderato"}, B()), {"STEWART_REASONING_EFFORT": "low"}))
        for t in ("presto", "largo", "rubato"):
            self.assertNotIn("effort", self.s(SP.tier_raw({"name": t}, self.b)))
        none = TH.settings(prof(self.tmp), env={})                                              # 등급 없는 실행
        self.assertEqual(none, {"max_tokens": None, "sampling": None, "params": {}, "source": {"max_tokens": None, "sampling": None},
                                "ignored": []})


TABLE = '''default = "big"
[backends.big]
base_url = "http://127.0.0.1:9/v1"
model = "big-model"
effort_values = ["low", "medium"]
[backends.bare]
base_url = "http://127.0.0.1:9/bare/v1"
model = "bare-model"
[tiers.largo]
max = 1
[tiers.presto]
backend = "bare"
'''


class TView(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = self.tmp / "backends.toml"
        os.environ.pop("STEWART_INSTANCE", None)

    def rows(self, text, env=None):
        self.p.write_text(text, encoding="utf-8")
        with mock.patch.dict(os.environ, env or {}):
            os.environ.pop("STEWART_REASONING_EFFORT", None) if not env else None
            rows, _ = TV.load(self.p)
        return {r["tier"]: r for r in rows}, rows

    def test_table_shows_effort_and_source(self):
        r, rows = self.rows(TABLE)
        self.assertEqual(r["moderato"]["effort"], {"value": "medium", "source": "default", "note": ""})   # 표에 없어도 기본 모델 + 기본값
        self.assertEqual(r["largo"]["effort"]["value"], None)
        self.assertEqual(TV.effort_label(r["moderato"]["effort"]), "medium (기본)")
        self.assertEqual(TV.effort_label(r["largo"]["effort"]), "값 없음")
        out = TV.text(rows)
        self.assertIn("생각 깊이", out)
        self.assertIn("medium (기본)", out)

    def test_table_value_and_env(self):
        r, _ = self.rows(TABLE + '[tiers.moderato]\nreasoning_effort = "none"\n')
        self.assertEqual(TV.effort_label(r["moderato"]["effort"]), "값 없음 (표)")
        r, _ = self.rows(TABLE, {"STEWART_REASONING_EFFORT": "low"})
        self.assertEqual(TV.effort_label(r["moderato"]["effort"]), "low (환경)")
        self.assertEqual(TV.effort_label(r["largo"]["effort"]), "low (환경)")

    def test_backend_without_effort_values_is_visible(self):
        r, rows = self.rows(TABLE + '[tiers.moderato]\nbackend = "bare"\n')
        self.assertIsNone(r["moderato"]["effort"]["value"])
        self.assertIn("effort_values", r["moderato"]["effort"]["note"])
        self.assertIn("moderato 생각 깊이: 기본 medium 을 싣지 않았다", TV.text(rows))


class THubChat(__import__("tests.test_hub3", fromlist=["MainBase"]).MainBase):
    """허브 「＋ 새 대화」에서 moderato 를 고르면 그 대화의 요청에 medium 이 실리고, "이 대화" 판 상태에 값·출처가 보인다."""

    def setUp(self):
        super().setUp()
        p = self.tmp / "backends.toml"
        p.write_text(p.read_text(encoding="utf-8").replace('model = "m"\n', 'model = "m"\neffort_values = ["low", "medium"]\n'),
                     encoding="utf-8")
        e = mock.patch.dict(os.environ, {"STEWART_BACKENDS": str(p)})
        e.start()
        self.addCleanup(e.stop)
        os.environ.pop("STEWART_REASONING_EFFORT", None)

    def test_moderato_chat_sends_medium(self):
        from tests.test_hub import msg
        c = self.main.new_chat("fresh", tier="moderato")
        self.assertEqual(c.state()["effort"], {"value": "medium", "source": "default", "note": ""})
        self.r.scripts["MAIN"] = [msg("답")]
        c.send("안녕")
        self.assertTrue(c.wait_idle())
        self.assertEqual(self.r.requests["MAIN"][-1].get("reasoning_effort"), "medium")
        d = self.main.new_chat("fresh")                                                        # 등급을 안 고르면 그대로
        self.assertEqual(d.state()["effort"], {"value": None, "source": "", "note": ""})
        g = self.main.new_chat("fresh", tier="largo")
        self.assertIsNone(g.state()["effort"]["value"])


if __name__ == "__main__":
    unittest.main()
