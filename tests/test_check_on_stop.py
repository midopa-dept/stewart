"""끝내기 전 확인(hooks Stop 첫 꼴). 실패하면 출력을 모델에 돌려주고 다시(최대 check_on_stop_max), 환경 변수로 켜고 끄기."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import engine, hooks as HK
from stewart.config import Backend, ConfigError, Profile, load_profile
from stewart.record import Recorder

PY = sys.executable
TEST = "import unittest\nfrom m import f\n\nclass T(unittest.TestCase):\n    def test_f(self):\n        self.assertEqual(f(), 2)\n"


_N = [0]


def r(content=None, calls=None):
    msg = {"role": "assistant", "content": content}
    if calls:
        _N[0] += 1
        msg["tool_calls"] = [{"id": f"c{_N[0]}-{i}", "type": "function", "function": {"name": n, "arguments": a}}
                             for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": msg, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, 0.1


def read():
    return r(calls=[("read_file", json.dumps({"path": "m.py"}))])


def write(v):
    return r(calls=[("write_file", json.dumps({"path": "m.py", "content": f"def f():\n    return {v}\n"}))])


class TConfig(unittest.TestCase):
    def p(self, **k):
        return Profile(name="t", path=Path("p"), system="s", tools=[], workdir=Path("."), verify_command="V", **k)

    def test_sources(self):
        self.assertEqual(HK.stop_command(self.p(check_on_stop=True), {})["command"], "V")
        self.assertEqual(HK.stop_command(self.p(check_on_stop="C"), {})["command"], "C")
        self.assertEqual(HK.stop_command(self.p(), {}), {"command": None, "source": "off", "ignored": None})
        self.assertEqual(HK.stop_command(self.p(check_on_stop=True), {HK.ENV_CHECK: "0"})["command"], None)
        on = HK.stop_command(self.p(), {HK.ENV_CHECK: "1"})
        self.assertEqual((on["command"], on["source"]), ("V", "env:on"))           # 프로필이 꺼도 env 1 이면 verify_command
        bad = HK.stop_command(self.p(check_on_stop=True), {HK.ENV_CHECK: "yes"})
        self.assertEqual((bad["command"], bad["ignored"]), ("V", "yes"))            # 잘못된 값은 무시하고 기록

    def test_profile_keys(self):
        code = load_profile("code")
        self.assertIs(code.check_on_stop, True)
        self.assertEqual((code.check_on_stop_max, code.check_on_stop_skip_exit), (2, [5]))
        self.assertIsNone(load_profile("chat").check_on_stop)
        tmp = Path(tempfile.mkdtemp())
        for bad in ("check_on_stop = 3", 'check_on_stop = ""', "check_on_stop_max = 0"):
            (tmp / "x.toml").write_text(f'system = "s"\n{bad}\n', encoding="utf-8")
            with self.assertRaises(ConfigError, msg=bad):
                load_profile(str(tmp / "x.toml"))


class TEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        (self.tmp / "test_m.py").write_text(TEST)
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop(HK.ENV_CHECK, None)
        # 같은 초·같은 크기로 m.py 를 다시 쓰면 __pycache__ 가 옛 판을 돌려준다(시험 꾸밈 탓) — 바이트코드 캐시를 끈다
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        self.addCleanup(self.env.stop)

    def prof(self, **k):
        d = dict(name="t", path=self.tmp / "p.toml", system="s", tools=["write_file", "read_file"], workdir=self.tmp,
                 max_steps=10, verify_command=f"{PY} -m unittest -q", check_on_stop=True)
        return Profile(**{**d, **k})

    def go(self, seq, prof=None):
        sent = []

        def fake(backend, messages, *a, **k):
            sent.append(json.loads(json.dumps(messages)))
            return seq[len(sent) - 1]

        rec = Recorder(self.tmp / "runs", "t")
        with mock.patch.object(engine, "chat", side_effect=fake):
            s = engine.run(prof or self.prof(), Backend("fake", "http://x/v1", "m", "q", 100000), "f 가 2 를 돌려주게", rec)
        ev = [json.loads(l) for l in (rec.dir / "events.jsonl").read_text().splitlines()]
        return s, sent, ev

    def test_fail_feedback_then_fix(self):
        s, sent, ev = self.go([write(1), r("고쳤다"), read(), write(2), r("이제 고쳤다")])
        self.assertEqual(s["stop_reason"], "completed")
        self.assertEqual(s["check_on_stop"], {"command": f"{PY} -m unittest -q", "source": "profile", "runs": 2, "fails": 1})
        fb = sent[2][-1]                                                         # 첫 최종 답 뒤의 요청
        self.assertEqual(fb["role"], "user")
        self.assertIn("끝내기 전에 확인 명령", fb["content"])
        self.assertIn("AssertionError", fb["content"])                         # 실패 출력이 모델에 간다
        self.assertEqual(sent[2][-2], {"role": "assistant", "content": "고쳤다"})
        checks = [e for e in ev if e["kind"] == "check_on_stop"]
        self.assertEqual([c["passed"] for c in checks], [False, True])
        self.assertTrue(s["verify"]["passed"] if isinstance(s.get("verify"), dict) else True)

    def test_max_two_then_accept(self):
        s, sent, _ = self.go([write(1), r("a"), r("b"), r("c")])
        self.assertEqual((s["stop_reason"], s["check_on_stop"]["runs"], s["check_on_stop"]["fails"]), ("completed", 2, 2))
        self.assertEqual(len(sent), 4)                                          # 세 번째 최종 답은 그대로 받는다

    def test_no_change_no_check(self):
        s, sent, ev = self.go([r("읽기만 했다")])
        self.assertEqual((s["stop_reason"], s["check_on_stop"]["runs"]), ("completed", 0))
        self.assertFalse([e for e in ev if e["kind"] == "check_on_stop"])

    def test_env_off_and_on(self):
        os.environ[HK.ENV_CHECK] = "0"
        s, sent, _ = self.go([write(1), r("끝")])
        self.assertEqual((s["stop_reason"], len(sent)), ("completed", 2))
        self.assertNotIn("check_on_stop", s)
        os.environ[HK.ENV_CHECK] = "1"
        (self.tmp / "m.py").unlink()
        s, sent, _ = self.go([write(1), r("끝"), r("그대로 둔다")],
                             prof=self.prof(check_on_stop=None, check_on_stop_max=1))   # 프로필은 끔, env 가 켬
        self.assertEqual((s["check_on_stop"]["source"], s["check_on_stop"]["fails"]), ("env:on", 1))

    def test_skip_exit_no_tests(self):
        (self.tmp / "test_m.py").unlink()
        s, sent, ev = self.go([write(1), r("끝")])
        self.assertEqual((s["stop_reason"], len(sent)), ("completed", 2))
        c = [e for e in ev if e["kind"] == "check_on_stop"][0]
        self.assertTrue(c["skipped"])                                           # 시험 없음(exit 5)은 확인할 것 없음

    def test_hook_stdin_and_env(self):
        cmd = f"{PY} -c \"import sys,os,json; d=json.load(sys.stdin); open('hook.json','w').write(json.dumps([d, os.environ.get('STEWART_HOOK_EVENT')]))\""
        s, _, _ = self.go([write(2), r("끝")], prof=self.prof(check_on_stop=cmd))
        d, ev = json.loads((self.tmp / "hook.json").read_text())
        self.assertEqual((d["event"], ev, d["final_output"], d["step"]), ("Stop", "Stop", "끝", 2))
        self.assertEqual(d["workdir"], str(self.tmp))


if __name__ == "__main__":
    unittest.main()
