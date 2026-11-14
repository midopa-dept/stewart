"""판 139: 단계 묶음 제목 요약(ui/src/stepsum.js) — 작업자 도구 줄 머리를 떼고 센다(node 가 있으면)."""

import os
import subprocess
import unittest
from pathlib import Path

from tests.test_u134_newchat_memory import _node_ok

ROOT = Path(__file__).resolve().parents[1]


class TStepSum(unittest.TestCase):
    @unittest.skipUnless(_node_ok(), "node 없음")
    def test_node_stepsum(self):
        r = subprocess.run(["node", "--test", "test/stepsum.test.mjs"], cwd=ROOT / "ui", capture_output=True, encoding="utf-8",
                           errors="replace", timeout=60, env={**os.environ, "MISE_OFFLINE": "1"})
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])

    def test_bundle_has_worker_prefix_strip(self):
        js = (ROOT / "src/stewart/web_ui/app.js").read_text(encoding="utf-8")
        self.assertIn("작업자 일", js)


if __name__ == "__main__":
    unittest.main()
