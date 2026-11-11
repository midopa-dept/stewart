"""판 87: 닫은 셸에 run 이 와도 새 bash 를 띄우지 않는다 · 허브(메인·대화)를 닫을 때 도는 차례를 멈추고 기다린다."""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from stewart import shell as SH


class TShellClosed(unittest.TestCase):
    def test_run_after_close(self):
        sh = SH.Shell(Path(tempfile.mkdtemp()))
        code, out, note = sh.run("echo hi", 10)
        self.assertEqual((code, out.strip()), (0, "hi"))
        sh.close()
        with mock.patch.object(sh, "_start", side_effect=AssertionError("닫은 뒤 새 셸을 띄우면 안 된다")):
            code, out, note = sh.run("echo again", 10)
        self.assertIsNone(code)
        self.assertIn("이미 닫혔다", note)


class TCloseWaitsTurn(unittest.TestCase):
    def test_stop_and_join(self):
        from stewart.hubmain import HubMain
        h = HubMain.__new__(HubMain)
        done = threading.Event()
        stop = threading.Event()

        def turn():
            stop.wait(5)
            time.sleep(0.2)          # 멈춤을 받고 정리하는 데 걸리는 시간
            done.set()
        h._turn = threading.Thread(target=turn, daemon=True)
        h._turn.start()
        with mock.patch.object(HubMain, "stop_turn", lambda self: stop.set() or "stopping"):
            h._stop_running_turn(wait_s=5)
        self.assertTrue(done.is_set())     # 셸을 닫기 전에 차례가 끝났다
        h2 = HubMain.__new__(HubMain)
        h2._stop_running_turn()            # 도는 차례가 없으면 아무 일 없음


class TAbortWhileWaitingSlot(unittest.TestCase):
    def test_abort_before_send_is_kept(self):
        from stewart import backend as B
        from stewart.config import Backend
        mark = B.abort_mark()
        B.abort_streams()                                   # 자리를 기다리는 사이에 온 [멈춤]
        with mock.patch.object(B, "_send", side_effect=AssertionError("보내면 안 된다")):
            with self.assertRaises(KeyboardInterrupt):
                B._send_abortable(Backend("t", "http://x/v1", "m", "q", 100), {}, None, gen=mark)



class TSweepStale(unittest.TestCase):
    def test_only_dead_marked_old(self):
        import os
        import tests as TT
        root = Path(tempfile.mkdtemp())
        dead, alive, nomark, young = (root / f"stewart-tests-{k}" for k in ("dead", "alive", "nomark", "young"))
        for d in (dead, alive, nomark, young):
            d.mkdir()
        (dead / ".pid").write_text("999999")
        (alive / ".pid").write_text(str(os.getpid()))
        (young / ".pid").write_text("999999")
        old = time.time() - 7 * 3600
        for d in (dead, alive):
            os.utime(d / ".pid", (old, old))
        self.assertEqual(TT._sweep_stale(str(root), str(root / "me")), 1)
        self.assertEqual(sorted(p.name for p in root.iterdir()), ["stewart-tests-alive", "stewart-tests-nomark", "stewart-tests-young"])


if __name__ == "__main__":
    unittest.main()
