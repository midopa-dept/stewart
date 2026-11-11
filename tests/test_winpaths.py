"""WIN-3 경로 판정: Windows 꼴 경로 가르기(osdep.win_path_kind 등)와 그것을 쓰는 판정 층(bounds·readcmds·allowrules·config_guard·is_secret).

macOS·Linux 에서는 osdep.IS_WIN 을 바꿔 끼워 Windows 분기의 "글로 가르는" 길만 본다. realpath·samestat·정션·8.3 은 실제 Windows 몫이다
(실제 Windows 연기 시험).
"""

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import allowrules as AR, bounds as BND, config_guard as CG, fswalk, osdep, readcmds as RC, tools as T
from stewart import shell as SH


def win():
    return mock.patch.object(osdep, "IS_WIN", True)


class TKind(unittest.TestCase):
    def test_table(self):
        cases = {
            "a.txt": "plain", "sub/a.txt": "plain", "./a": "plain", "../a": "plain", "C:/x/y": "plain", "c:\\x\\y": "plain",
            "/x": "plain", "PROGRA~1/x": "plain", ".env": "plain",
            "/dev/null": "device", "/DEV/NULL": "device", "/dev/zero": "device", "/dev/tty": "device", "NUL": "device",
            "nul": "device", "sub/nul": "device",
            "/dev/stdout": "ask", "/dev/fd/1": "ask", "//srv/share/x": "ask", "\\\\srv\\share\\x": "ask",
            "\\\\?\\C:\\x": "ask", "//./PhysicalDrive0": "ask", "C:x": "ask", "C:": "ask", "c:..\\x": "ask",
            "a.txt:s": "ask", ".env::$DATA": "ask", "C:/x/a:s": "ask", "a.": "ask", "a. ": "ask", ".env ": "ask",
            "dir./x": "ask", "CON": "ask", "con.txt": "ask", "aux": "ask", "COM1": "ask", "lpt9.log": "ask",
            "nul.txt": "ask", "nul/x": "ask", "": "ask",
        }
        for t, want in cases.items():
            self.assertEqual(osdep.win_path_kind(t), want, t)

    def test_norm_name(self):
        for n, want in {".env ": ".env", ".env.": ".env", ".env::$DATA": ".env", "a/b\\.env. ": ".env", "x": "x",
                        "...": "...", "a.txt:s": "a.txt"}.items():
            self.assertEqual(osdep.win_norm_name(n), want, n)

    def test_strip_components(self):
        self.assertEqual(osdep.win_strip_components("C:\\Data\\u\\.config\\stewart.\\x. "), "C:/Data/u/.config/stewart/x")
        self.assertEqual(osdep.win_strip_components("/a/../b./c"), "/a/../b/c")


class TSecret(unittest.TestCase):
    def test_windows_names(self):
        for n in (".env ", ".env.", ".env::$DATA", "dir\\.env", ".ENV. ", "id_rsa.", "server.pem:x"):
            self.assertTrue(fswalk.is_secret(n, win=True), n)
        for n in (".env.example ", "a.txt", "env", "notes.md."):
            self.assertFalse(fswalk.is_secret(n, win=True), n)

    def test_posix_unchanged(self):
        self.assertFalse(fswalk.is_secret(".env ", win=False))
        self.assertFalse(fswalk.is_secret(".env::$DATA", win=False))
        self.assertTrue(fswalk.is_secret(".env", win=False))

    def test_toolenv_realpath_name_on_windows(self):
        d = Path(tempfile.mkdtemp())
        (d / ".env").write_text("K=v", encoding="utf-8")
        (d / "notes").symlink_to(d / ".env")       # Windows 의 8.3 이름·정션 대신(실제 이름이 비밀)
        env = T.ToolEnv(workdir=d)
        self.assertTrue(env.is_secret(d / "notes"))           # POSIX 도 실제 이름을 본다(WIN-4a)
        with win():
            self.assertTrue(env.is_secret(d / "notes"))

    def test_read_file_refuses_symlink_to_secret_posix(self):
        d = Path(tempfile.mkdtemp())
        (d / ".env").write_text("K=v", encoding="utf-8")
        (d / "notes").symlink_to(d / ".env")
        (d / "plain.txt").write_text("ok", encoding="utf-8")
        (d / "alias").symlink_to(d / "plain.txt")
        env = T.ToolEnv(workdir=d)
        ok, out = T.read_file(env, "notes")
        self.assertFalse(ok)
        self.assertNotIn("K=v", out)
        self.assertTrue(T.read_file(env, "alias")[0])           # 비밀 아닌 곳을 가리키는 링크는 그대로 읽는다


class TBounds(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_write_kinds(self):
        with win():
            b = BND.Bounds(self.d)
            for p in ("//srv/x", "C:x", "a.txt:s", "a.", "CON", "/dev/stdout", "nul.txt"):
                self.assertEqual(b.write_outside(p), p, p)
            for p in ("/dev/null", "NUL", "x.txt"):
                self.assertIsNone(b.write_outside(p), p)
            self.assertEqual(b.shell_outside("echo hi > //srv/x; echo b > NUL; echo c > /dev/null; echo d > in.txt"), ["//srv/x"])
            self.assertEqual(b.shell_outside("cp a.txt C:b.txt"), ["C:b.txt"])

    def test_read_kinds(self):
        with win():
            b = BND.Bounds(self.d, read_outside="ask")
            self.assertEqual(b.read_outside_path("//srv/x"), "//srv/x")
            self.assertIsNone(b.read_outside_path("/dev/null"))
            self.assertIsNone(b.read_outside_path("x.txt"))

    def test_temp_dirs(self):
        with win():
            self.assertEqual(BND.temp_dirs(), [tempfile.gettempdir()])
        self.assertIn("/tmp", BND.temp_dirs())                # POSIX 그대로

    def test_too_wide_temp_is_dropped(self):
        home = os.path.expanduser("~")
        for d in ("/", home, os.path.dirname(home)):
            self.assertTrue(BND._too_wide(d), d)
        self.assertFalse(BND._too_wide(str(self.d)))
        with mock.patch.object(tempfile, "gettempdir", return_value=home):
            self.assertNotIn(home, BND.temp_dirs())
            b = BND.Bounds(self.d)
            self.assertIsNotNone(b.write_outside(os.path.join(home, "x.txt")))   # 홈이 임시 폴더로 잡혀도 밖

    def test_posix_devices_unchanged(self):
        b = BND.Bounds(self.d)
        self.assertIsNone(b.write_outside("/dev/stdout"))
        self.assertIsNone(b.write_outside("/dev/fd/3"))


FAKE_BB = '#!/bin/sh\n[ "$1" = --list ] && { printf "cat\\nls\\nfind\\nsort\\nhead\\nwc\\n"; exit 0; }\nexit 1\n'


class TReadCmds(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp()).resolve()
        (self.d / "a.txt").write_text("x", encoding="utf-8")
        bb = self.d / "bin" / "busybox64u.exe"
        bb.parent.mkdir()
        bb.write_text(FAKE_BB, encoding="utf-8")
        bb.chmod(bb.stat().st_mode | stat.S_IXUSR)
        self.spec = osdep.ShellSpec((str(bb), "sh"), "busybox", "bundled")

    def test_win_read_cmd_ok(self):
        ok = lambda n, env=None, spec=None: osdep.win_read_cmd_ok(n, env or {}, spec or self.spec)   # noqa: E731
        self.assertTrue(ok("cat"))
        self.assertTrue(ok("echo"))                  # sh 내장
        self.assertFalse(ok("tree"))                 # System32\tree.com
        self.assertFalse(ok("file"))                 # 애플릿 아님 → 외부 프로그램이 돌 수 있다
        self.assertFalse(ok("cat", {"BB_OVERRIDE_APPLETS": "make cat"}))
        bash = osdep.ShellSpec(("C:/Git/bin/bash.exe",), "msys", "env")
        self.assertFalse(ok("cat", spec=bash))       # busybox 가 아니면 통과 없음
        self.assertTrue(ok("pwd", spec=bash))

    def test_path_ok_kinds(self):
        sec = lambda p: False   # noqa: E731
        with win():
            self.assertFalse(RC.path_ok("C:x", self.d, self.d, sec))
            self.assertFalse(RC.path_ok("//srv/x", self.d, self.d, sec))
            self.assertFalse(RC.path_ok("a.txt:s", self.d, self.d, sec))
            self.assertFalse(RC.path_ok("CON", self.d, self.d, sec))
            self.assertTrue(RC.path_ok("/dev/null", self.d, self.d, sec))
            self.assertTrue(RC.path_ok("a.txt", self.d, self.d, sec))
            self.assertIsNone(RC.cd_target("cd C:x", self.d, self.d))

    def test_read_ok_gated_on_windows(self):
        sec = lambda p: False   # noqa: E731
        with win(), mock.patch.object(osdep, "shell_spec", return_value=self.spec):
            self.assertTrue(RC.read_ok("cat a.txt", self.d, self.d, sec))
            self.assertFalse(RC.read_ok("tree", self.d, self.d, sec))
            self.assertFalse(RC.read_ok("stat a.txt", self.d, self.d, sec))     # 가짜 목록에 없음
            self.assertFalse(RC.read_ok("cat //srv/x", self.d, self.d, sec))
        self.assertTrue(RC.read_ok("stat a.txt", self.d, self.d, sec))          # POSIX 는 지금처럼

    def test_allowrules_decide(self):
        env = T.ToolEnv(workdir=self.d)
        ctx = AR.Ctx(workdir=self.d, cwd=self.d, is_secret=env.is_secret, allow_redirect=True)
        with win(), mock.patch.object(osdep, "shell_spec", return_value=self.spec):
            self.assertEqual(AR.decide("cat a.txt | head -n 1", [], ctx).effect, "allow")
            self.assertNotEqual(AR.decide("cat C:a.txt", [], ctx).effect, "allow")
            self.assertNotEqual(AR.decide("cat a.txt > a.txt:s", [], ctx).effect, "allow")
            self.assertNotEqual(AR.decide("tree", [], ctx).effect, "allow")
            self.assertEqual(AR.decide("cat a.txt > NUL", [], ctx).effect, "allow")
            self.assertNotEqual(AR.decide("cat .env.", [], ctx).effect, "allow")


class TConfigGuard(unittest.TestCase):
    def test_trailing_dot_into_protected(self):
        d = Path(tempfile.mkdtemp())
        user = d / "user"
        user.mkdir()
        with mock.patch.dict(os.environ, {"STEWART_USER_DIR": str(user)}):
            self.assertIsNone(CG.hit(d, "write_file", {"path": f"{d}/user./rules.toml"}))   # POSIX: 다른 폴더(지금 그대로)
            with win():
                self.assertIsNotNone(CG.hit(d, "write_file", {"path": f"{d}/user./rules.toml"}))


class TShellPwd(unittest.TestCase):
    def test_unexpected_pwd_form_is_unknown(self):
        d = Path(tempfile.mkdtemp())
        (d / "sub").mkdir()
        sh = SH.Shell(d)
        self.addCleanup(sh.close)
        sh.run("true", 10)                            # 셸은 POSIX 로 띄워 둔다
        with win():
            sh.run("cd sub", 10)                      # $PWD 가 /… 꼴 → Windows 에서는 모름
        self.assertEqual(sh.cwd, SH.UNKNOWN_CWD)
        b = BND.Bounds(d)
        self.assertTrue(b.shell_outside("echo x > a.txt", cwd=sh.cwd))   # 모르는 위치의 상대 경로 쓰기 → 묻는다
        sh.run("cd ..", 10)
        self.assertEqual(Path(sh.cwd).resolve(), d.resolve())          # POSIX 로 돌아오면 다시 따라간다


if __name__ == "__main__":
    unittest.main()


class THome(unittest.TestCase):
    def test_home_filled_on_windows_only(self):
        with win():
            e = SH.tool_env({"PATH": "x"})
        self.assertEqual(e["HOME"], osdep.sh_path(os.path.expanduser("~")))
        with win():
            self.assertEqual(SH.tool_env({"PATH": "x", "HOME": "C:/h"})["HOME"], "C:/h")
        self.assertNotIn("HOME", SH.tool_env({"PATH": "x"}))


class TSuspended(unittest.TestCase):
    """WIN-3: Windows 는 멈춘 채 띄우고 Job 에 넣은 뒤 푼다(분기만, 실제 API 는 Windows 몫)."""

    def test_flags(self):
        with win():
            self.assertEqual(osdep.group_popen_kwargs(), {"creationflags": osdep.CREATE_NEW_PROCESS_GROUP | osdep.CREATE_SUSPENDED})

    def test_resume_even_when_job_fails_and_kill_when_resume_fails(self):
        p = mock.Mock(_handle=123)
        with win(), mock.patch.object(osdep, "_win_job_for", side_effect=OSError("x")), \
                mock.patch.object(osdep, "_win_resume", return_value=True) as rs:
            osdep.attach_group(p)
        rs.assert_called_once_with(123)
        self.assertIsNone(p._stewart_job)
        with win(), mock.patch.object(osdep, "_win_job_for", return_value="job"), \
                mock.patch.object(osdep, "_win_resume", return_value=False):
            with self.assertRaises(OSError):
                osdep.attach_group(p)
        p.kill.assert_called_once()
