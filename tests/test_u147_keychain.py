"""판 147.4: 키체인이 시간 안에 답하지 않으면 — 같은 항목을 부를 때마다 10초씩 다시 기다리지 않고, "없음"으로 굳히지도 않는다.

- 답하지 않은 항목은 KC_RETRY_S 동안 None(기다리지 않음), 그 뒤 다시 읽는다(키체인을 푼 뒤).
- 등급 표는 "키체인이 답하지 않았다"로 "키 없음"과 가른다.
- 시험 묶음은 PATH 맨 앞의 가짜 security 로 진짜 키체인에 묻지 않는다(tests/__init__).
"""

import shutil
import subprocess
import unittest
from unittest import mock

from stewart import config as C


class TKeychainStuck(unittest.TestCase):
    def setUp(self):
        C._KEYCHAIN.clear()
        C._KC_STUCK.clear()
        self.addCleanup(C._KEYCHAIN.clear)
        self.addCleanup(C._KC_STUCK.clear)

    def test_timeout_remembered_then_retried(self):
        calls = []

        def slow(*a, **k):
            calls.append(a[0])
            raise subprocess.TimeoutExpired(a[0], 10)
        now = [1000.0]
        with mock.patch("sys.platform", "darwin"), mock.patch("shutil.which", return_value="/usr/bin/security"), \
                mock.patch("subprocess.run", side_effect=slow), mock.patch("time.monotonic", side_effect=lambda: now[0]):
            self.assertIsNone(C.keychain_secret("svc"))
            self.assertTrue(C.keychain_stuck("svc"))
            self.assertIsNone(C.keychain_secret("svc"))
            self.assertEqual(len(calls), 1)                                           # 두 번째는 기다리지 않는다
            now[0] += C.KC_RETRY_S + 1
            self.assertIsNone(C.keychain_secret("svc"))
            self.assertEqual(len(calls), 2)                                           # 시간이 지나면 다시 읽는다

    def test_answer_after_unlock_clears(self):
        now = [1000.0]
        seq = [subprocess.TimeoutExpired("security", 10), subprocess.CompletedProcess([], 0, b"k-1\n", b"")]

        def run(*a, **k):
            x = seq.pop(0)
            if isinstance(x, Exception):
                raise x
            return x
        with mock.patch("sys.platform", "darwin"), mock.patch("shutil.which", return_value="/usr/bin/security"), \
                mock.patch("subprocess.run", side_effect=run), mock.patch("time.monotonic", side_effect=lambda: now[0]):
            self.assertIsNone(C.keychain_secret("svc"))
            now[0] += C.KC_RETRY_S + 1
            self.assertEqual(C.keychain_secret("svc"), "k-1")
            self.assertFalse(C.keychain_stuck("svc"))

    def test_tier_table_says_not_answered(self):
        from stewart import tierview as TV
        from stewart.config import Backend
        b = Backend("out", "https://api.example.invalid/v1", "m", "q", 1000, provider="Outside", api_key_keychain="svc")
        C._KC_STUCK["svc"] = 0.0
        with mock.patch.object(C, "keychain_secret", return_value=None), mock.patch("time.monotonic", return_value=1.0):
            ks, kw = TV.key_state(b, True)
        self.assertEqual(ks, "없음")
        self.assertIn("답하지 않았다", kw)

    def test_suite_has_fake_security(self):
        p = shutil.which("security")
        if p is None:
            self.skipTest("POSIX 가 아님")
        r = subprocess.run([p, "find-generic-password", "-s", "x", "-w"], capture_output=True, timeout=5)
        self.assertEqual(r.returncode, 44)                                            # 진짜 키체인에 묻지 않는다


if __name__ == "__main__":
    unittest.main()
