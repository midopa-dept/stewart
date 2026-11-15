"""판 155: 작업자 묶음 다듬기.

- 작업자 지시문에 허브 역할 글·현황판 지시를 넘기지 않는다(작업자는 session_*·board 를 못 쓴다). 메인 지시문은 그대로.
- 자리 안내(place_hints) 기본 끔. STEWART_PLACE_HINTS=1 이나 등급 prompt.place_hints = true 로 켜고, =0 이 늘 이긴다.
- run_shell 이 없는 묶음(읽기 작업자, 셸을 뺀 등급 등)에서는 shell_output·shell_kill 도 뺀다(볼 출력·끌 작업이 생기지 않는다).
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from stewart import delegate as DG, tools as T
from stewart.board import NOTE as BOARD_NOTE
from stewart.config import load_profile
from stewart.delegate import Budget
from stewart.hubmain import HUB_MAIN_NOTE, with_role
from tests.test_hub import msg
from tests.test_realuse_shape import FakeModel, Hub, is_worker, make_user_shape
from tests.test_realuse_tier_bodies import TASK

PTOOLS = ["read_file", "list_dir", "search", "glob", "write_file", "edit_file", "run_shell", "shell_output", "shell_kill"]


class TUnit(unittest.TestCase):
    def test_shell_pair(self):
        self.assertEqual(T.shell_pair(["read_file", "shell_output", "shell_kill"]), ["read_file"])
        self.assertEqual(T.shell_pair(["run_shell", "shell_output", "shell_kill"]), ["run_shell", "shell_output", "shell_kill"])
        names = [d["function"]["name"] for d in T.definitions(["read_file", "shell_output"])]
        self.assertEqual(names, ["read_file"])
        star = [d["function"]["name"] for d in T.definitions(["*"])]
        self.assertTrue({"run_shell", "shell_output", "shell_kill"} <= set(star))               # 셸이 있으면 그대로

    def test_place_hints_default_off(self):
        from stewart.chat import place_hints_on
        self.assertFalse(place_hints_on({}))
        self.assertTrue(place_hints_on({"STEWART_PLACE_HINTS": "1"}))
        self.assertFalse(place_hints_on({"STEWART_PLACE_HINTS": "0"}))
        self.assertTrue(place_hints_on({}, tier=True))
        self.assertFalse(place_hints_on({"STEWART_PLACE_HINTS": "1"}, tier=False))
        self.assertFalse(place_hints_on({"STEWART_PLACE_HINTS": "0"}, tier=True))

    def test_child_tools(self):
        parent, b = load_profile("chat"), Budget(100)
        prof, _ = DG.child_profile(parent, PTOOLS, "auto", {}, b, read_default=True)
        self.assertEqual(prof.tools, ["read_file", "list_dir", "search", "glob"])                 # 읽기 작업자: 셸 출력·끄기 없음
        prof, _ = DG.child_profile(parent, PTOOLS, "auto", {"tools": ["read_file", "run_shell", "shell_output", "shell_kill"]}, b)
        self.assertEqual(prof.tools, ["read_file", "run_shell", "shell_output", "shell_kill"])   # 셸 작업자는 그대로
        prof, _ = DG.child_profile(parent, PTOOLS, "auto", {"tools": ["read_file", "run_shell", "shell_output"],
                                                           "_tier": {"tools_deny": ["run_shell"]}}, b)
        self.assertEqual(prof.tools, ["read_file"])                                                # 등급이 셸을 빼면 따라 빠진다

    def test_child_system_without_role(self):
        parent = load_profile("chat")
        hub = with_role(parent)
        self.assertIn(HUB_MAIN_NOTE, hub.system)
        b = Budget(100)
        prof, _ = DG.child_profile(hub, PTOOLS, "auto", {}, b, read_default=True)
        self.assertNotIn(HUB_MAIN_NOTE, prof.system)
        self.assertNotIn(BOARD_NOTE, prof.system)
        plain, _ = DG.child_profile(parent, PTOOLS, "auto", {}, b, read_default=True)
        self.assertEqual(prof.system, plain.system)                                                # 역할 글을 뺀 나머지는 한 글자도 같다
        self.assertEqual(DG.without_role(parent.system), parent.system)                            # 역할 글이 없으면 그대로


class TOnce(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="u155-"))
        self.model = FakeModel(route=lambda b: msg("했다" if is_worker(b) else "네"))
        self.addCleanup(self.model.close)
        self.env = make_user_shape(self.root, self.model.url)
        (self.root / "project").mkdir(exist_ok=True)
        (self.root / "project" / "README.md").write_text("첫 줄\n", encoding="utf-8")

    def run_worker(self, *args):
        n0 = len(self.model.requests)
        r = subprocess.run([sys.executable, "-m", "stewart", "chat", "--once", "--backend", "cloud", "-p", "chat", *args,
                            "--workdir", str(self.root / "project"), TASK], env=self.env, cwd=str(self.root),
                           capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        return [b for b in self.model.requests[n0:] if b.get("tools")]

    def test_read_worker(self):
        wb = [b for b in self.run_worker("--worker", "read") if is_worker(b)][0]
        names = {t["function"]["name"] for t in wb["tools"]}
        self.assertFalse(names & {"run_shell", "shell_output", "shell_kill"}, names)
        wsys = str(wb["messages"][0]["content"])
        self.assertNotIn("허브 메인으로서", wsys)
        self.assertNotIn(BOARD_NOTE.splitlines()[0], wsys)
        self.assertIn("너는 부모 에이전트가 맡긴 일 하나만 하는 작업자다", wsys)
        mb = [b for b in self.run_worker("--hub-main") if not is_worker(b)][0]
        self.assertIn("허브 메인으로서", str(mb["messages"][0]["content"]))                     # 메인 지시문은 그대로
        self.assertIn("shell_output", {t["function"]["name"] for t in mb["tools"]})

    def test_hub_main_place_hints_off_by_default(self):
        mb = [b for b in self.run_worker("--hub-main") if not is_worker(b)][0]
        self.assertNotIn("이 컴퓨터의 앱·브라우저", str(mb["messages"][0]["content"]))
        self.env["STEWART_PLACE_HINTS"] = "1"
        mb = [b for b in self.run_worker("--hub-main") if not is_worker(b)][0]
        self.assertIn("이 컴퓨터의 앱·브라우저", str(mb["messages"][0]["content"]))

    def test_write_worker_keeps_shell(self):
        wb = [b for b in self.run_worker("--worker", "write", "--worker-tools", "read_file,list_dir,search,write_file,run_shell,shell_output,shell_kill") if is_worker(b)][0]
        names = {t["function"]["name"] for t in wb["tools"]}
        self.assertTrue({"run_shell", "shell_output", "shell_kill"} <= names, names)
        self.assertNotIn("허브 메인으로서", str(wb["messages"][0]["content"]))


if __name__ == "__main__":
    unittest.main()
