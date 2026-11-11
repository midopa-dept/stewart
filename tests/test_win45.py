"""WIN-4·5: 받는 중인 응답 끊기, 살아 있는지(pid_alive), 승인 통로(child_pipe), 바꿔 쓰기 다시 시도, TUI 의 Windows 분기.

실제 Windows API(OpenProcess·handle_list·SetConsoleCtrlHandler·VT)는 실제 Windows 연기 시험이 본다.
"""

import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from stewart import backend as BK, hub as H, osdep
from stewart.config import Backend


class _Slow(http.server.BaseHTTPRequestHandler):
    """조각 하나를 보내고 오래 멈춘다(생성이 느린 서버 흉내)."""

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        ev = {"choices": [{"delta": {"content": "반쯤"}}]}
        self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
        self.wfile.flush()
        time.sleep(8)

    def log_message(self, *a):
        pass


class TAbort(unittest.TestCase):
    def setUp(self):
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Slow)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.be = Backend("t", f"http://127.0.0.1:{self.srv.server_port}/v1", "m", "q", 4096, timeout_s=30,
                          configured_context=4096)

    def test_abort_turns_blocked_read_into_keyboardinterrupt_with_partial(self):
        got = []
        threading.Timer(0.6, lambda: got.append(BK.abort_streams())).start()
        t0 = time.monotonic()
        with self.assertRaises(KeyboardInterrupt) as cm:
            BK.chat_stream(self.be, [{"role": "user", "content": "x"}])
        self.assertLess(time.monotonic() - t0, 4)             # 서버가 8초 멈춰도 바로 깬다
        self.assertEqual(got, [1])
        self.assertEqual(cm.exception.partial.get("content"), "반쯤")
        self.assertEqual(BK.abort_streams(), 0)                # 끝난 연결은 등록에서 빠진다

    def test_abort_non_stream(self):
        threading.Timer(0.6, BK.abort_streams).start()
        with self.assertRaises(KeyboardInterrupt):
            BK.chat(self.be, [{"role": "user", "content": "x"}])


class TAbortThreaded(TAbort):
    """Windows 꼴: shutdown 이 막힌 읽기를 깨우지 못해도(흉내: shutdown 을 아무 일 안 하게) 읽는 스레드 덕에 바로 끊긴다.
    (11-10 VM 20번: 스레드 없이는 서버가 닫을 때까지 15초 늦었다)"""

    def setUp(self):
        super().setUp()
        for k, v in (("_THREADED", True), ("_sock_of", lambda r: mock.Mock())):
            p = mock.patch.object(BK, k, v)
            p.start()
            self.addCleanup(p.stop)

    def test_streaming_while_bytes_flow(self):
        """흐르는 중(조각이 계속 올 때)에도 끊긴다 — 눈 확인과 같은 꼴."""
        import http.server

        class Flow(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    for _ in range(200):
                        self.wfile.write(b'data: {"choices": [{"delta": {"content": "x"}}]}\n\n')
                        self.wfile.flush()
                        time.sleep(0.05)
                except OSError:
                    pass

            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Flow)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        be = Backend("f", f"http://127.0.0.1:{srv.server_port}/v1", "m", "q", 4096, timeout_s=30, configured_context=4096)
        threading.Timer(0.5, BK.abort_streams).start()
        t0 = time.monotonic()
        with self.assertRaises(KeyboardInterrupt) as cm:
            BK.chat_stream(be, [{"role": "user", "content": "x"}])
        self.assertLess(time.monotonic() - t0, 2)
        self.assertTrue(cm.exception.partial.get("content", "").startswith("x"))

    def test_normal_stream_unchanged(self):
        import http.server

        class Done(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"choices": [{"delta": {"content": "ok"}}]}\n\ndata: [DONE]\n\n')

            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Done)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        be = Backend("d", f"http://127.0.0.1:{srv.server_port}/v1", "m", "q", 4096, timeout_s=30, configured_context=4096)
        resp, _ = BK.chat_stream(be, [{"role": "user", "content": "x"}])
        self.assertEqual(resp["choices"][0]["message"]["content"], "ok")
        self.assertEqual(BK.abort_streams(), 0)


class TAlive(unittest.TestCase):
    def test_pid_alive(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(p.kill)
        self.assertTrue(osdep.pid_alive(p.pid))
        self.assertTrue(H._alive(p.pid))
        p.kill()
        p.wait()
        self.assertFalse(osdep.pid_alive(p.pid))
        self.assertFalse(osdep.pid_alive(None))

    def test_no_os_kill_on_windows(self):
        """Windows 분기는 os.kill 을 부르지 않는다(0 = CTRL_C_EVENT)."""
        src = Path(osdep.__file__).read_text(encoding="utf-8")
        body = src[src.index("def pid_alive"):src.index("def child_pipe")]
        win = body[body.index("if IS_WIN:"):body.index("    try:\n        os.kill")]
        self.assertNotIn("os.kill", win)
        self.assertNotIn("os.kill", Path(H.__file__).read_text(encoding="utf-8"))


class TChildPipe(unittest.TestCase):
    def test_posix_pipe_reaches_only_that_child(self):
        r, w, args, kw = osdep.child_pipe()
        self.assertEqual(args, ["--approval-fd", str(r)])
        code = ("import os,sys; from stewart import osdep; fd = osdep.open_child_pipe(int(sys.argv[2])); "
                "assert not os.get_inheritable(fd); print(os.read(fd, 100).decode())")
        env = {**os.environ, "PYTHONPATH": str(Path(osdep.__file__).parents[1])}
        p = subprocess.Popen([sys.executable, "-c", code, *args], stdout=subprocess.PIPE, env=env, **kw)
        os.close(r)
        os.write(w, b"hello\n")
        os.close(w)
        out, _ = p.communicate(timeout=20)
        self.assertEqual(out.decode().strip(), "hello")

    def test_windows_branch_args(self):
        """Windows 분기: handle_list 로 그 핸들만, close_fds. (msvcrt 는 흉내)"""
        fake = mock.MagicMock()
        fake.get_osfhandle.return_value = 1234
        si = mock.MagicMock()
        with mock.patch.object(osdep, "IS_WIN", True), mock.patch.dict(sys.modules, {"msvcrt": fake}), \
                mock.patch.object(os, "set_handle_inheritable", create=True) as sh, \
                mock.patch.object(subprocess, "STARTUPINFO", create=True, return_value=si):
            r, w, args, kw = osdep.child_pipe()
        os.close(r)
        os.close(w)
        self.assertEqual(args, ["--approval-handle", "1234"])
        sh.assert_called_once_with(1234, True)
        self.assertEqual(si.lpAttributeList, {"handle_list": [1234]})
        self.assertTrue(kw["close_fds"])
        self.assertNotIn("pass_fds", kw)


class TReplace(unittest.TestCase):
    def test_retry_on_windows(self):
        d = Path(tempfile.mkdtemp())
        a, b = d / "a", d / "b"
        a.write_text("x", encoding="utf-8")
        real = os.replace
        calls = []

        def flaky(s, t):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError(5, "액세스가 거부되었습니다")
            real(s, t)
        with mock.patch.object(osdep, "IS_WIN", True), mock.patch.object(os, "replace", flaky):
            osdep.replace_retry(a, b, wait=0.001)
        self.assertEqual(len(calls), 3)
        self.assertEqual(b.read_text(encoding="utf-8"), "x")

    def test_posix_once_and_write_atomic_cleans_tmp(self):
        d = Path(tempfile.mkdtemp())
        with mock.patch.object(os, "replace", side_effect=PermissionError(13, "no")):
            with self.assertRaises(PermissionError):
                H.write_atomic(d / "s.json", "{}")
        self.assertEqual(list(d.iterdir()), [])                 # 임시 파일이 남지 않는다


class TTuiWindows(unittest.TestCase):
    class _TTY:
        def isatty(self):
            return True

    def test_plain_reason_ignores_empty_term_on_windows(self):
        from stewart import tui
        t = self._TTY()
        self.assertIn("TERM", tui.plain_reason("auto", env={}, stdin=t, stdout=t, cols=120))     # POSIX 그대로
        with mock.patch.object(osdep, "IS_WIN", True), mock.patch.object(osdep, "win_console_vt", return_value=None):
            self.assertIsNone(tui.plain_reason("auto", env={}, stdin=t, stdout=t, cols=120))
            self.assertIn("dumb", tui.plain_reason("auto", env={"TERM": "dumb"}, stdin=t, stdout=t, cols=120))
        with mock.patch.object(osdep, "IS_WIN", True), mock.patch.object(osdep, "win_console_vt", return_value="콘솔이 아님"):
            self.assertEqual(tui.plain_reason("auto", env={}, stdin=t, stdout=t, cols=120), "콘솔이 아님")

    def test_interrupt_windows_path(self):
        from stewart import tui
        ui = tui.TuiUI.__new__(tui.TuiUI)
        ui._armed, ui._interrupted, ui.phase = True, False, ""
        with mock.patch.object(osdep, "IS_WIN", True), mock.patch("stewart.backend.abort_streams") as ab, \
                mock.patch("_thread.interrupt_main") as im:
            self.assertTrue(ui.interrupt())
        ab.assert_called_once()
        im.assert_called_once()
        ui._armed = False
        self.assertFalse(ui.interrupt())

    def test_guard_raw_noop_on_windows(self):
        from stewart import tui
        ui = tui.TuiUI.__new__(tui.TuiUI)
        ui._raw_attrs = [0, 0, 0, 0]
        with mock.patch.object(osdep, "IS_WIN", True):
            self.assertFalse(ui.guard_raw())

    def test_console_helpers_are_noop_on_posix(self):
        self.assertIsNone(osdep.win_console_vt())
        self.assertFalse(osdep.win_on_ctrl_c(lambda: None))


if __name__ == "__main__":
    unittest.main()
