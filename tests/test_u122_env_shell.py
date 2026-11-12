"""환경 정보의 파이썬은 run_shell 이 실제로 쓰는 PATH 로 찾고, 셸에 없는 명령에는 웹 넛지 대신 짧은 안내 한 줄.

- stewart 가 가상 환경에서 돌고 그 bin 이 PATH 에 있을 때(`uv run`), 셸 PATH 에서는 그 bin 이 빠진다.
  환경 정보도 같은 PATH 로 찾아야 셸에서 부를 수 있는 것만 "있음"이 된다.
- 작업 폴더에 .venv 파이썬이 있으면 부르는 법을 한 줄 적는다(stewart 자신의 가상 환경이면 적지 않는다).
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C
from stewart import envinfo
from stewart import shell
from stewart.config import Backend, Profile
from stewart.osdep import IS_WIN
from stewart.record import Recorder
from tests.test_cloud import FakeUI


def fake_python(bindir: Path, ver: str) -> None:
    bindir.mkdir(parents=True, exist_ok=True)
    for n in ("python", "python3"):
        f = bindir / n
        f.write_text(f"#!/bin/sh\necho 'Python {ver}'\n")
        f.chmod(0o755)


@unittest.skipIf(IS_WIN, "POSIX 가상 환경 꼴")
class TEnvMatchesShell(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.own = self.tmp / "own-venv"
        fake_python(self.own / "bin", "3.99.1")        # stewart 자신의 가상 환경(셸에서는 빠진다)
        self.other = self.tmp / "other-bin"
        self.other.mkdir()
        (self.other / "python3").write_text("#!/bin/sh\necho 'Python 3.98.0'\n")
        (self.other / "python3").chmod(0o755)
        path = os.pathsep.join([str(self.own / "bin"), str(self.other), "/usr/bin", "/bin"])
        for p in (mock.patch.dict(os.environ, {"PATH": path}), mock.patch.object(shell, "own_venv", return_value=str(self.own))):
            p.start()
            self.addCleanup(p.stop)
        self.wd = self.tmp / "work"
        self.wd.mkdir()

    def test_same_answer_as_shell_path(self):
        info = envinfo.measure(self.wd)
        sp = shell.tool_env()["PATH"]
        self.assertNotIn(str(self.own / "bin"), sp.split(os.pathsep))
        for p in info["python"]:
            self.assertEqual(p["path"], shutil.which(p["name"], path=sp))     # 셸이 찾는 것과 같다
            self.assertFalse((p["path"] or "").startswith(str(self.own)))
        py3 = next(p for p in info["python"] if p["name"] == "python3")
        self.assertEqual(py3["path"], str(self.other / "python3"))
        self.assertEqual(py3["version"], "3.98.0")                          # 판도 셸 쪽 것
        self.assertNotIn("3.99.1", envinfo.block(info))

    def test_project_venv_line(self):
        fake_python(self.wd / ".venv" / "bin", "3.97.0")
        info = envinfo.measure(self.wd)
        self.assertEqual(info["project_venv"], ".venv/bin/python")
        self.assertIn("작업 폴더 .venv 파이썬: `.venv/bin/python`", envinfo.block(info))

    def test_own_venv_in_workdir_not_named(self):
        fake_python(self.wd / ".venv" / "bin", "3.97.0")
        with mock.patch.object(shell, "own_venv", return_value=os.path.realpath(self.wd / ".venv")):
            info = envinfo.measure(self.wd)
        self.assertIsNone(info["project_venv"])
        self.assertNotIn(".venv", envinfo.block(info))


class TMissingCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.wd = self.tmp / "work"
        self.wd.mkdir()
        self.sent = []

    def test_patterns(self):
        from stewart import webnudge as WN
        self.assertEqual(WN.missing_command("bash: line 1: python: command not found"), "python")
        self.assertEqual(WN.missing_command("zsh: command not found: pip"), "pip")
        self.assertEqual(WN.missing_command("'foo' is not recognized as an internal or external command,"), "foo")
        self.assertIsNone(WN.missing_command("ModuleNotFoundError: No module named 'foo'"))
        self.assertIn("(파이썬은 `python3`)", WN.missing_note("python", ["python3"]))
        self.assertNotIn("파이썬은", WN.missing_note("jq", ["python3"]))

    def test_chat_note_once_and_no_web_nudge(self):
        def r(calls=None, content=None):
            m = {"role": "assistant", "content": content}
            if calls:
                m["tool_calls"] = [{"id": f"c{len(self.sent)}-{i}", "type": "function",
                                    "function": {"name": "run_shell", "arguments": json.dumps({"command": c})}}
                                   for i, c in enumerate(calls)]
            return {"choices": [{"message": m, "finish_reason": "stop"}], "usage": {}}, 0.1
        seq = [r(["stewart-no-such-cmd-xyz --v"]), r(["stewart-no-such-cmd-xyz -h"]), r(content="없다")]

        def f(backend, messages, tools=None, *a, **k):
            self.sent.append(1)
            return seq[len(self.sent) - 1]
        p = Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["run_shell", "web_fetch"], workdir=self.wd, max_steps=10)
        with mock.patch.dict(os.environ, {"STEWART_WEB_NUDGE": "1"}):
            s = C.ChatSession(p, Backend("fake", "http://x/v1", "m", "q", 100000), Recorder(self.tmp / "runs", "t"),
                              FakeUI(), stream=False, mode="auto")
            self.addCleanup(lambda: C.T.close_env(s.env))
            self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
            with mock.patch.object(C, "chat", side_effect=f):
                self.assertEqual(s.turn("돌려"), "answered")
        tools = [m["content"] for m in s.messages if m.get("role") == "tool"]
        self.assertIn("`stewart-no-such-cmd-xyz` 명령이 이 셸에 없다", tools[0])
        self.assertNotIn("이 셸에 없다", tools[1])                              # 같은 명령은 한 번만
        self.assertTrue(all("web_fetch" not in t and "웹으로" not in t for t in tools))   # 웹 넛지는 붙지 않는다


if __name__ == "__main__":
    unittest.main()
