"""플랫폼 층(osdep, WIN-2): 셸 찾기·설정, 확인 명령의 셸, 프로세스 묶음, 파이프 읽기, envinfo 셸 줄.

macOS·Linux 에서 돌 수 있는 것만 시험한다. Windows 분기는 win=True·repo_root 를 넘겨 판정 길만 보고,
Job Object·taskkill·CREATE_NEW_PROCESS_GROUP 는 실제 Windows 에서 확인한다(보고서 목록).
"""

import ctypes
import os
import re
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from stewart import envinfo, osdep, shell as SH, tools as T

SRC = Path(osdep.__file__).parent


def _exe(p: Path, body: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


FAKE_BUSYBOX = ('#!/bin/sh\n'
                'if [ $# -eq 0 ]; then echo "BusyBox v1.37.0-FRP-5236 (2025-01-01 00:00:00 UTC) multi-call binary."; exit 0; fi\n'
                '[ "$1" = sh ] || { echo "applet $1 없음" >&2; exit 127; }\n'
                'shift\nexec /bin/sh "$@"\n')


class TSpec(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = {"STEWART_USER_DIR": str(self.tmp / "user")}

    def test_posix_default_is_bin_bash(self):
        s = osdep.shell_spec(self.env, win=False)
        self.assertEqual(s.argv, ("/bin/bash",))
        self.assertEqual(s.login_args, ("--noprofile", "--norc"))
        self.assertEqual((s.kind, s.source), ("bash", "default"))

    def test_env_busybox_runs_sh_applet(self):
        bb = _exe(self.tmp / "busybox64.exe", FAKE_BUSYBOX)
        s = osdep.shell_spec({**self.env, "STEWART_SHELL": str(bb)}, win=False)
        self.assertEqual((s.argv, s.kind, s.login_args), ((str(bb), "sh"), "busybox", ()))
        self.assertEqual(s.run_argv("echo hi"), [str(bb), "sh", "-c", "echo hi"])

    def test_env_missing_file(self):
        with self.assertRaises(osdep.ShellNotFound) as c:
            osdep.shell_spec({**self.env, "STEWART_SHELL": str(self.tmp / "없음")}, win=False)
        self.assertIn("STEWART_SHELL", str(c.exception))

    def test_user_config(self):
        sh = _exe(self.tmp / "bin" / "mysh", "#!/bin/sh\nexec /bin/sh \"$@\"\n")
        (self.tmp / "user").mkdir()
        (self.tmp / "user" / "config.toml").write_text(f'[shell]\npath = "{sh}"\nargs = ["-e"]\n', encoding="utf-8")
        s = osdep.shell_spec(self.env, win=False)
        self.assertEqual((s.argv, s.kind), ((str(sh), "-e"), "sh"))
        self.assertIn("config.toml", s.source)
        # 환경 변수가 설정 파일보다 먼저
        bb = _exe(self.tmp / "busybox", FAKE_BUSYBOX)
        self.assertEqual(osdep.shell_spec({**self.env, "STEWART_SHELL": str(bb)}, win=False).kind, "busybox")

    def test_user_config_bad(self):
        (self.tmp / "user").mkdir()
        (self.tmp / "user" / "config.toml").write_text('[shell]\npath = 3\n', encoding="utf-8")
        with self.assertRaises(osdep.ShellNotFound):
            osdep.shell_spec(self.env, win=False)
        (self.tmp / "user" / "config.toml").write_text('[shell\n', encoding="utf-8")
        with self.assertRaises(osdep.ShellNotFound):
            osdep.shell_spec(self.env, win=False)

    def test_windows_bundled_order_and_missing(self):
        root = self.tmp / "stewart"
        with self.assertRaises(osdep.ShellNotFound) as c:
            osdep.shell_spec(self.env, win=True, repo_root=root)
        self.assertIn("busybox64u.exe", str(c.exception))
        home = self.tmp / "home"                         # 런처의 설치 뿌리(STEWART_HOME\bin)
        _exe(home / "bin" / "busybox64u.exe", FAKE_BUSYBOX)
        s = osdep.shell_spec({**self.env, "STEWART_HOME": str(home)}, win=True, repo_root=root)
        self.assertEqual((Path(s.path).parent.parent, s.source), (home, "bundled"))
        _exe(root / "bin" / "busybox.exe", FAKE_BUSYBOX)
        self.assertTrue(osdep.shell_spec(self.env, win=True, repo_root=root).path.endswith("busybox.exe"))
        _exe(root / "bin" / "busybox64.exe", FAKE_BUSYBOX)
        s = osdep.shell_spec(self.env, win=True, repo_root=root)
        self.assertTrue(s.path.endswith("busybox64.exe"))
        self.assertEqual((s.kind, s.source), ("busybox", "bundled"))

    def test_windows_does_not_pick_bin_bash(self):
        with self.assertRaises(osdep.ShellNotFound):     # Windows 는 /bin/bash 로 가지 않는다
            osdep.shell_spec(self.env, win=True, repo_root=self.tmp / "none")

    def test_msys_bash_is_flagged(self):
        git = self.tmp / "Git"
        bash = _exe(git / "usr" / "bin" / "bash.exe", "#!/bin/sh\n")
        (git / "usr" / "bin" / "msys-2.0.dll").write_bytes(b"")
        s = osdep.shell_spec({**self.env, "STEWART_SHELL": str(bash)}, win=True)
        self.assertEqual(s.kind, "msys")
        self.assertIn("WIN-6", s.warning)
        self.assertEqual(s.login_args, ("--noprofile", "--norc"))
        self.assertEqual(osdep.shell_spec({**self.env, "STEWART_SHELL": str(bash)}, win=False).kind, "bash")


class TCommand(unittest.TestCase):
    def test_posix_keeps_shell_true(self):
        self.assertEqual(osdep.command_args("echo hi", win=False), {"args": "echo hi", "shell": True})

    def test_windows_uses_found_shell(self):
        spec = osdep.ShellSpec(("C:/stewart/bin/busybox64.exe", "sh"), "busybox", "bundled")
        self.assertEqual(osdep.command_args("make test && echo ok", win=True, spec=spec),
                         {"args": ["C:/stewart/bin/busybox64.exe", "sh", "-c", "make test && echo ok"], "shell": False})

    def test_run_command_utf8(self):
        r = osdep.run_command("printf '한글\\n'; printf '깨짐\\377\\n' >&2", capture_output=True)
        self.assertEqual(r.stdout, "한글\n")
        self.assertIn("깨짐", r.stderr)               # 잘못된 바이트는 바꿔 넣고 죽지 않는다

    def test_source_no_shell_true_or_text_true(self):
        for f in ("hooks.py", "engine.py", "cli.py"):
            self.assertNotIn("shell=True", (SRC / f).read_text(encoding="utf-8"), f)
        for f in SRC.glob("*.py"):
            if f.name == "batch.py":                 # 출력이 파일로만 간다(글 풀기 없음)
                continue
            self.assertNotIn("text=True", f.read_text(encoding="utf-8"), f.name)

    def test_tools_write_keeps_newlines(self):
        src = (SRC / "tools.py").read_text(encoding="utf-8")
        writes = re.findall(r"\.write_text\([^\n]*\)", src)
        self.assertTrue(writes)
        for w in writes:
            self.assertIn('newline=""', w)              # Windows 에서 \n 이 \r\n 으로 바뀌지 않게

    def test_write_file_bytes(self):
        d = Path(tempfile.mkdtemp())
        env = T.ToolEnv(workdir=d)
        ok, _ = T.write_file(env, "a.txt", "x\ny\r\nz\n")
        self.assertTrue(ok)
        self.assertEqual((d / "a.txt").read_bytes(), b"x\ny\r\nz\n")


class TGroupAndPipe(unittest.TestCase):
    def test_pipe_reader(self):
        r, w = os.pipe()
        f = os.fdopen(r, "rb", buffering=0)
        pr = osdep.PipeReader(f)
        self.assertIsNone(pr.read(0.05))
        os.write(w, b"abc")
        self.assertEqual(pr.read(2), b"abc")
        os.close(w)
        self.assertEqual(pr.read(2), b"")
        self.assertEqual(pr.read(0), b"")
        pr.t.join(2)
        self.assertTrue(f.closed)                       # 끝에 닿으면 읽던 쪽이 닫는다

    def test_kill_group_takes_grandchild(self):
        d = Path(tempfile.mkdtemp())
        p = SH.spawn(["/bin/bash", "-c", f"sleep 60 & echo $! > {d}/gc; wait"], d)
        for _ in range(100):
            if (d / "gc").exists() and (d / "gc").read_text().strip():
                break
            time.sleep(0.05)
        gc = int((d / "gc").read_text())
        osdep.kill_group(p)
        p.wait(5)
        p.stdout.close()
        for _ in range(100):
            try:
                os.kill(gc, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("손자 프로세스가 남았다")

    def test_job_struct_size(self):
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            self.skipTest("64비트 아님")
        self.assertEqual(ctypes.sizeof(osdep._JOBOBJECT_EXTENDED_LIMIT_INFORMATION), 144)   # Windows x64 의 크기
        self.assertEqual(ctypes.sizeof(osdep._JOBOBJECT_BASIC_LIMIT_INFORMATION), 64)

    def test_posix_popen_kwargs(self):
        self.assertEqual(osdep.group_popen_kwargs(), {"start_new_session": True})
        self.assertEqual(osdep.sh_path("/a/b"), "/a/b")


class TShellWithOtherSpec(unittest.TestCase):
    """bash 가 아닌 셸 꼴(busybox 처럼 '경로 sh' 로 띄움)로도 상태 유지 셸이 도는지."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bb = _exe(self.tmp / "tools" / "busybox64.exe", FAKE_BUSYBOX)
        (self.tmp / "wd" / "sub").mkdir(parents=True)

    def test_state_cd_export_syntax(self):
        with mock.patch.dict(os.environ, {"STEWART_SHELL": str(self.bb)}):
            sh = SH.Shell(self.tmp / "wd")
        self.addCleanup(sh.close)
        self.assertEqual(sh.spec.kind, "busybox")
        self.assertEqual(sh.run("cd sub && export A=1", 10)[0], 0)
        rc, out, _ = sh.run('pwd; echo "A=$A"', 10)
        self.assertEqual(rc, 0)
        self.assertIn("A=1", out)
        self.assertTrue(sh.cwd.endswith("sub"))
        rc, out, note = sh.run("echo 'unterminated", 10)
        self.assertEqual(rc, 2)
        self.assertIn("문법 오류", note)
        rc, _, note = sh.run("sleep 5", 0.5)
        self.assertIsNone(rc)
        self.assertIn("시간 초과", note)
        rc, out, _ = sh.run('pwd; echo "A=$A"', 10)       # 새 셸: cd·export 는 직전 상태로
        self.assertIn("A=1", out)
        self.assertTrue(sh.cwd.endswith("sub"))
        bid, _ = sh.bg_start("echo bg; sleep 30")
        self.assertTrue(sh.bg_kill(bid))

    def test_run_shell_without_shell(self):
        env = T.ToolEnv(workdir=self.tmp / "wd")
        with mock.patch.dict(os.environ, {"STEWART_SHELL": str(self.tmp / "없음")}):
            ok, out = T.run_shell(env, "echo hi")
        self.assertFalse(ok)
        self.assertIn("셸을 띄우지 못했다", out)

    def test_envinfo_busybox_label(self):
        with mock.patch.dict(os.environ, {"STEWART_SHELL": str(self.bb)}):
            info = envinfo.measure(self.tmp / "wd")
        self.assertIn(f"{self.bb} sh (BusyBox v1.37.0-FRP-5236", info["shell"])
        self.assertIn("POSIX sh(ash)", info["shell"])

    def test_envinfo_no_shell(self):
        with mock.patch.dict(os.environ, {"STEWART_SHELL": str(self.tmp / "없음")}):
            self.assertTrue(envinfo.measure(self.tmp / "wd")["shell"].startswith("없음("))

    def test_windows_python_stub_counts_as_missing(self):
        stub = _exe(self.tmp / "WindowsApps" / "python3", "#!/bin/sh\nexit 9009\n")
        with mock.patch.object(envinfo.shutil, "which", return_value=str(stub)):
            self.assertIsNone(envinfo._python("python3", win=True)["path"])
            self.assertEqual(envinfo._python("python3", win=False)["path"], str(stub))   # POSIX 는 지금처럼


if __name__ == "__main__":
    unittest.main()
