"""EXAONE 분리(판 77): stewart instance — 따로 자리·샌드박스 규칙·키는 환경 변수로만·허브 이름표·호출 누계."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import backend as B, instance as I, notify
from stewart.config import Backend

ROOT = Path(__file__).resolve().parents[1]


def _sandbox_works() -> bool:
    if not Path(I.SANDBOX).exists():
        return False
    r = subprocess.run([I.SANDBOX, "-p", "(version 1)(allow default)", "/usr/bin/true"], capture_output=True)
    return r.returncode == 0


class TInstance(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.home, ignore_errors=True))
        d = I.dirs("ex", self.home)
        d["config"].mkdir(parents=True)
        self.proj = self.home / "proj"
        (self.home / "secret-work").mkdir()
        (self.home / "secret-work" / "s.txt").write_text("비밀", encoding="utf-8")
        (self.home / "code").mkdir()
        (d["config"] / "instance.toml").write_text(
            f'project = "{self.proj}"\nport = 8766\nbackend = "exaone"\nkey_env = "EXAONE_API_KEY"\n'
            f'keychain_service = "stewart-ex"\nkeychain_account = "api"\n'
            f'stewart_src = ["{self.home}/code"]\ndeny = ["{self.home}/secret-work", "{self.home}/code-root"]\n', encoding="utf-8")
        (d["config"] / "sandbox-allow.txt").write_text(f"# 주석\nr {self.home}/secret-work/open\nrw {self.home}/shared\n", encoding="utf-8")
        self.d = d

    def cfg(self):
        return I.load("ex", self.home)

    def test_dirs_and_name(self):
        self.assertEqual(self.d["state"], self.home / ".local/state/stewart-ex")
        with self.assertRaises(I.InstanceError):
            I.dirs(I.check_name("../x"))

    def test_profile_order(self):
        sb = I.profile(self.cfg())
        lines = sb.splitlines()
        self.assertEqual(lines[0], "(version 1)")
        i_allow = next(i for i, l in enumerate(lines) if l == "(allow default)")
        i_wdeny = next(i for i, l in enumerate(lines) if l.startswith('(deny file-write* (subpath "/"))'))
        i_deny = next(i for i, l in enumerate(lines) if l.startswith("(deny file-read* file-write*"))
        i_src = next(i for i, l in enumerate(lines) if "/code\")" in l and l.startswith("(allow file-read*"))
        i_list = next(i for i, l in enumerate(lines) if "sandbox-allow.txt" in l and "secret-work/open" in l)
        self.assertTrue(i_allow < i_wdeny < i_deny < i_src < i_list)          # 뒤 규칙이 앞을 이긴다
        self.assertIn(str(os.path.realpath(self.proj)), sb)                   # 자기 폴더는 쓰기 됨
        self.assertIn("(allow file-read* file-write* (subpath", next(l for l in lines if "/shared" in l))
        self.assertIn('(global-name "com.apple.SecurityServer")', sb)         # 키체인 조회 막음

    def test_allow_file_format(self):
        (self.d["config"] / "sandbox-allow.txt").write_text("x /tmp\n", encoding="utf-8")
        with self.assertRaises(I.InstanceError):
            self.cfg()

    def test_env_has_key_only_in_key_env(self):
        base = {"PATH": "/usr/bin", "STEWART_BACKENDS": "/old/backends.toml", "STEWART_MEMORY_DIR": "/old", "EXAONE_API_KEY": "stale"}
        e = I.env(self.cfg(), "k-123", base=base)
        self.assertEqual(e["EXAONE_API_KEY"], "k-123")
        self.assertEqual(sum(1 for v in e.values() if "k-123" in v), 1)
        self.assertEqual(e["STEWART_BACKENDS"], str(self.d["config"] / "backends.toml"))   # 옛 자리를 물려받지 않는다
        self.assertEqual(e["STEWART_MEMORY_DIR"], str(self.d["share"] / "memory"))
        self.assertTrue(e["XDG_STATE_HOME"].startswith(str(self.d["state"])))
        self.assertNotIn("EXAONE_API_KEY", I.env(self.cfg(), None, base=base))           # 키 없이(check)는 아예 없음

    def test_hub_argv(self):
        a = I.hub_argv(self.cfg())
        self.assertEqual(a[1:4], ["-m", "stewart", "hub"])
        self.assertIn("--state-dir", a)
        self.assertEqual(a[a.index("--port") + 1], "8766")
        self.assertEqual(a[a.index("--config") + 1], str(self.d["config"] / "hub.toml"))

    def test_keychain_lookup_shape(self):
        run = mock.Mock(return_value=mock.Mock(returncode=0, stdout=b"secret\n"))
        with mock.patch.object(I.sys, "platform", "darwin"), mock.patch.object(I.shutil, "which", return_value="/usr/bin/security"):
            self.assertEqual(I.keychain("stewart-ex", "api", run=run), "secret")
        self.assertEqual(run.call_args[0][0], ["security", "find-generic-password", "-s", "stewart-ex", "-a", "api", "-w"])

    def test_cli_dispatch(self):
        from stewart import cli
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            with mock.patch("sys.stdout") as out:
                self.assertEqual(cli.main(["instance", "ex", "profile"]), 0)
        self.assertIn("(allow default)", "".join(c.args[0] for c in out.write.call_args_list))

    @unittest.skipUnless(_sandbox_works(), "sandbox-exec 를 여기서 걸 수 없다(이미 샌드박스 안) — 사람 셸에서 stewart instance exaone check")
    def test_real_sandbox_blocks(self):
        cfg = self.cfg()
        sb = I.write_profile(cfg)
        r = subprocess.run([I.SANDBOX, "-f", str(sb), "/bin/cat", str(self.home / "secret-work" / "s.txt")], capture_output=True)
        self.assertNotEqual(r.returncode, 0)
        self.proj.mkdir(exist_ok=True)
        r = subprocess.run([I.SANDBOX, "-f", str(sb), "/usr/bin/touch", str(self.proj / "ok")], capture_output=True)
        self.assertEqual(r.returncode, 0)


HUB_TOML = """# EXAONE 허브 설정(사용자 자리 예시와 같은 꼴)
[hub]
port = 8766
label = "EXAONE"
color = "#d97706"
# ntfy_url = "…"
"""


class TStartAndConfig(unittest.TestCase):
    def test_example_hub_toml_loads(self):
        from stewart.hubmain import hub_config
        d = Path(tempfile.mkdtemp())
        (d / "hub.toml").write_text(HUB_TOML, encoding="utf-8")
        cfg = hub_config(str(d / "hub.toml"))
        self.assertEqual((cfg["label"], cfg["color"], cfg["port"]), ("EXAONE", "#d97706", 8766))
        (d / "hub2.toml").write_text('[hub]\nnotify_tag = "EX"\n', encoding="utf-8")
        self.assertEqual(hub_config(str(d / "hub2.toml"))["notify_tag"], "EX")

    def test_user_example_if_given(self):
        p = Path(os.environ.get("STEWART_TEST_INSTANCE_HUB_TOML", ""))   # 사용자 자리 설정 본으로도 시험할 때만(없으면 건너뜀)
        if not os.environ.get("STEWART_TEST_INSTANCE_HUB_TOML") or not p.is_file():
            self.skipTest("STEWART_TEST_INSTANCE_HUB_TOML 없음")
        from stewart.hubmain import hub_config
        self.assertEqual(hub_config(str(p))["label"], "EXAONE")

    def test_watch_start(self):
        log = Path(tempfile.mkdtemp()) / "hub-run.log"
        log.write_text("앞 회차\n", encoding="utf-8")
        off = log.stat().st_size
        with open(log, "a", encoding="utf-8") as f:
            f.write("ConfigError: 모르는 키\n" + I.DEAD_MARK + "(exit 2) — 기록 x\n")
        st, text = I.watch_start(log, off, wait_s=2, sleep=lambda s: None)
        self.assertEqual(st, "dead")
        self.assertIn("ConfigError", text)
        self.assertNotIn("앞 회차", text)
        with open(log, "a", encoding="utf-8") as f:
            f.write("접속: http://127.0.0.1:8766/\n")
        self.assertEqual(I.watch_start(log, log.stat().st_size - 40, wait_s=1, sleep=lambda s: None)[0], "ready")
        self.assertEqual(I.watch_start(log, log.stat().st_size, wait_s=0.2, sleep=lambda s: None)[0], "slow")

    def test_tmux_cmd_shape(self):
        c = I.tmux_cmd("ex", Path("/x/hub-run.log"))
        self.assertTrue(c.startswith("bash -c "))
        self.assertIn("pipefail", c)
        self.assertIn("tee -a", c)
        self.assertIn("instance ex run", c)
        self.assertIn("허브가 끝났다", c)


class TLabelAndUse(unittest.TestCase):
    def test_ntfy_tag(self):
        n = notify.from_config({"ntfy_url": "https://ntfy.example/topic", "label": "EXAONE"}, "proj")
        self.assertTrue(n.message("승인 대기")["title"].startswith("[EXAONE] stewart proj"))

    def test_count_use(self):
        bk = Backend("x", "https://a.example/v1", "m", "api", 1000)
        B.count_use(bk, {"usage": {"prompt_tokens": 100, "completion_tokens": 7}})
        B.count_use(bk, {"usage": {"prompt_tokens": 10, "completion_tokens": 3, "cost_usd": 0.5}})
        self.assertEqual(bk.used, {"calls": 2, "prompt": 110, "completion": 10, "cost_usd": 0.5})
        self.assertNotIn("used", json.dumps(bk.public()))

    def test_ui_built(self):
        js = (ROOT / "src/stewart/web_ui/app.js").read_text(encoding="utf-8")
        for s in ("hub_label", "hub_color", "호출 "):
            self.assertIn(s, js)


if __name__ == "__main__":
    unittest.main()
