"""판 123: 생각 손잡이(서버 생각 예산·등급에서 고르는 생각 되돌려 보내기), template 자리 노력의 400 재시도."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from stewart import backend as B
from stewart import spawn
from stewart import think as TH
from stewart import toolrepair as TR
from stewart.backend import BackendError
from stewart.config import Backend

def prof(think: dict | None = None):
    return SimpleNamespace(raw={"tier": {"think": think}} if think else {})


def be(thinking=True):
    return Backend("fake", "http://x/v1", "m", "q", 100000,
                   params={"chat_template_kwargs": {"enable_thinking": thinking}, "max_tokens": 8192})


class TBudget(unittest.TestCase):
    def test_default_none(self):
        s = TH.settings(prof(), env={})
        self.assertNotIn("budget", s)
        self.assertEqual(TH.call_kwargs(be(), s, None, 1000), {})

    def test_tier_and_env(self):
        s = TH.settings(prof({"thinking_token_budget": 4096}), env={})
        self.assertEqual(s["budget"], 4096)
        self.assertEqual(TH.call_kwargs(be(), s, None, 1000)["overrides"]["thinking_token_budget"], 4096)
        self.assertEqual(TH.call_kwargs(be(), s, False, 1000), {})               # 생각 끔 요청에는 싣지 않는다
        self.assertEqual(TH.settings(prof({"thinking_token_budget": 4096}), env={TH.ENV_BUDGET: "2048"})["budget"], 2048)
        self.assertNotIn("budget", TH.settings(prof({"thinking_token_budget": 4096}), env={TH.ENV_BUDGET: "0"}))
        self.assertIn(TH.ENV_BUDGET, TH.settings(prof(), env={TH.ENV_BUDGET: "x"})["ignored"])

    def test_tier_keys_loaded(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "backends.toml"
            p.write_text('[tiers.t]\nthinking_token_budget = 4096\nreasoning_passback = "current_turn"\n', encoding="utf-8")
            t = spawn.load_tiers(p)["t"]
        self.assertEqual(t["think"], {"thinking_token_budget": 4096, "reasoning_passback": "current_turn"})


class TPassback(unittest.TestCase):
    def test_tier_default_and_env(self):
        self.assertEqual(TR.passback(env={}), "off")
        self.assertEqual(TR.passback(env={}, default="current_turn"), "current_turn")
        self.assertEqual(TR.passback(env={"STEWART_REASONING_PASSBACK": "off"}, default="current_turn"), "off")   # 환경이 이긴다
        self.assertEqual(TR.passback(env={}, default="weird"), "off")


class TDropOn400(unittest.TestCase):
    def call(self, body, overrides, err_body):
        b = be()
        seen = []

        def fake_open(backend, path, body_, timeout):
            seen.append({**body_, "chat_template_kwargs": dict(body_.get("chat_template_kwargs") or {})})
            if len(seen) == 1:
                raise BackendError("HTTP 400", 400, err_body)
            return {"ok": True}
        with mock.patch.object(B, "_open", side_effect=fake_open):
            B._send_once(b, body, overrides)
        return b, seen

    def test_template_effort_dropped(self):
        ctk = {"enable_thinking": True, "reasoning_effort": "medium"}
        body = {"messages": [], "chat_template_kwargs": ctk}
        b, seen = self.call(body, {"chat_template_kwargs": ctk}, "template error: unknown reasoning_effort 'medium'")
        self.assertIn("reasoning_effort", seen[0]["chat_template_kwargs"])
        self.assertNotIn("reasoning_effort", seen[1]["chat_template_kwargs"])
        self.assertTrue(seen[1]["chat_template_kwargs"]["enable_thinking"])
        self.assertIn(B.TPL_EFFORT, b.dropped_params)
        self.assertEqual(ctk["reasoning_effort"], "medium")                       # 넘겨준 dict 는 고치지 않는다
        later = {"chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "low"}}
        for k in b.dropped_params:                                                 # 그 백엔드에서는 이후에도 뺀다
            B._drop(later, k)
        self.assertEqual(later["chat_template_kwargs"], {"enable_thinking": True})

    def test_budget_dropped(self):
        body = {"messages": [], "thinking_token_budget": 4096}
        b, seen = self.call(body, {"thinking_token_budget": 4096}, "Extra inputs are not permitted: thinking_token_budget")
        self.assertNotIn("thinking_token_budget", seen[1])
        self.assertIn("thinking_token_budget", b.dropped_params)

    def test_other_400_raises(self):
        b = be()
        with mock.patch.object(B, "_open", side_effect=BackendError("HTTP 400", 400, "bad messages")):
            with self.assertRaises(BackendError):
                B._send_once(b, {"messages": [], "chat_template_kwargs": {"reasoning_effort": "x"}},
                             {"chat_template_kwargs": {"reasoning_effort": "x"}})


class TEffortValues(unittest.TestCase):
    """서버 틀이 모르는 노력 낱말은 조용히 다른 수준이 된다 — 백엔드가 받는 낱말 목록이 있으면 밖의 값은 알리고 보내지 않는다."""

    def be(self, values=None):
        b = be()
        b.effort_values = values
        return b

    def test_outside_list_is_dropped_and_noted(self):
        s = TH.settings(prof({"reasoning_effort": "high"}), env={})
        note = TH.check_effort(s, self.be(["xhigh", "medium", "low"]))
        self.assertIn("'high'", note)
        self.assertIn("xhigh·medium·low", note)
        self.assertNotIn("effort", s)
        self.assertNotIn("reasoning_effort", json_of(TH.call_kwargs(self.be(["xhigh", "medium", "low"]), s, None, 1000)))
        self.assertEqual(s["effort_rejected"]["value"], "high")

    def test_inside_list_and_no_list_unchanged(self):
        s = TH.settings(prof({"reasoning_effort": "xhigh"}), env={})
        self.assertIsNone(TH.check_effort(s, self.be(["xhigh", "medium", "low"])))
        self.assertEqual(TH.call_kwargs(be(), s, None, 1000)["overrides"]["reasoning_effort"], "xhigh")
        s2 = TH.settings(prof({"reasoning_effort": "high"}), env={})
        self.assertIsNone(TH.check_effort(s2, self.be(None)))                  # 목록이 없으면 그대로 보낸다
        self.assertEqual(s2["effort"], "high")
        s3 = TH.settings(prof(), env={})
        self.assertIsNone(TH.check_effort(s3, self.be(["low"])))                # 노력을 안 적었으면 그대로(기본 동작)
        self.assertEqual(TH.call_kwargs(be(), s3, None, 1000), {})

    def test_config_loads_list(self):
        from stewart.config import ConfigError, load_backends
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "backends.toml"
            p.write_text('default = "a"\n[backends.a]\nbase_url = "http://x/v1"\nmodel = "m"\nprecision = "q"\nmax_context = 1000\n'
                         'effort_values = ["XHigh", "medium"]\n', encoding="utf-8")
            bes, _ = load_backends(p)
            self.assertEqual(bes["a"].effort_values, ["xhigh", "medium"])
            p.write_text('default = "a"\n[backends.a]\nbase_url = "http://x/v1"\nmodel = "m"\nprecision = "q"\nmax_context = 1000\n'
                         'effort_values = "high"\n', encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_backends(p)


class TEffortChatStart(unittest.TestCase):
    def test_chat_warns_at_start_and_does_not_send(self):
        import json
        import shutil
        from stewart import chat as C
        from stewart.config import Profile
        from stewart.record import Recorder
        from tests.test_cloud import FakeUI
        tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        (tmp / "w").mkdir()
        p = Profile(name="t", path=tmp / "p.toml", system="s", tools=["read_file"], workdir=tmp / "w", max_steps=3,
                    raw={"tier": {"think": {"reasoning_effort": "high"}}})
        b = be()
        b.effort_values = ["xhigh", "medium", "low"]
        ui = FakeUI()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(TH.ENV_EFFORT, None)
            s = C.ChatSession(p, b, Recorder(tmp / "runs", "t"), ui, stream=False, mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        self.assertIn("보내지 않는다", ui.out.getvalue())
        sent = []

        def f(backend, messages, tools=None, *a, **k):
            sent.append(k)
            return {"choices": [{"message": {"role": "assistant", "content": "답"}, "finish_reason": "stop"}], "usage": {}}, 0.1
        with mock.patch.object(C, "chat", side_effect=f):
            self.assertEqual(s.turn("물음"), "answered")
        self.assertNotIn("reasoning_effort", json.dumps(sent))


def json_of(d) -> str:
    import json
    return json.dumps(d)


if __name__ == "__main__":
    unittest.main()
