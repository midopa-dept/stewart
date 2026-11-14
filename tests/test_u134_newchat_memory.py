"""판 134: 사용자 기억 파일이 있으면 새 대화 첫 화면(자리·등급 고르기)이 통째로 안 그려지던 것.

- 까닭: 화면이 "기록 줄이 하나도 없음"을 빈 대화로 봤다. 사용자 기억 파일(<사용자 폴더>/STEWART.md)이 있으면 대화 시작 때
  "프로젝트 기억 파일: … (N자)" 안내 줄이 찍혀, 말이 없는 새 대화도 빈 대화가 아니게 됐다. 시험에는 기억 파일이 없어 늘 통과했다.
- 고친 꼴: 빈 대화 = 사람·모델 말이 아직 없음(ui/src/emptyconv.js). 안내 줄은 숨기지 않고 고르기 줄 아래에 둔다.
- 여기서 보는 것: 기억 파일이 있는 허브에서 새 대화를 열면 (1) 안내 줄이 기록에 있고 (2) 서버의 빈 새 대화 판정(first 없음)은 그대로이고
  (3) 화면 판정 함수가 그 기록을 빈 대화로 본다(node 가 있으면). 화면 그림은 시험대 허브로 따로 찍었다(보고 참고).
"""

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from stewart import scenes as SC
from tests.test_hub3 import MainBase

ROOT = Path(__file__).resolve().parents[1]


def _node_ok() -> bool:
    """진짜 node 가 바로 도는가(mise 덧대기가 내려받으려 하면 건너뛴다 — test_u55 와 같은 판정)."""
    if not shutil.which("node"):
        return False
    try:
        r = subprocess.run(["node", "--version"], cwd=ROOT / "ui", capture_output=True, text=True, timeout=10,
                           env={**os.environ, "MISE_OFFLINE": "1"})
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and r.stdout.startswith("v")


class TNewChatWithMemory(MainBase):
    def setUp(self):
        self.udir = SC.user_dir_with_memory()
        e = mock.patch.dict(os.environ, {"STEWART_USER_DIR": str(self.udir)})
        e.start()
        self.addCleanup(e.stop)
        super().setUp()

    def test_start_line_and_draft(self):
        c = self.main.new_chat("fresh", title="새 대화")
        log = c.state()["main_log"]
        mem = [m for m in log if "기억 파일" in (m.get("text") or "")]
        self.assertTrue(mem, log)                                                   # 안내 줄이 기록에 있다(숨기지 않는다)
        self.assertIn(str(self.udir / "STEWART.md"), mem[0]["text"])
        self.assertFalse([m for m in log if m.get("kind") in ("user", "assistant")])   # 말은 아직 없다
        row = next(x for x in self.main.state()["chats"] if x["name"] == c.name)
        self.assertEqual((row["title"], row.get("first") or ""), ("새 대화", ""))      # 서버도 빈 새 대화(화면 isDraft 와 같다)
        if _node_ok():   # 화면 판정 함수가 이 기록을 빈 대화로 본다
            src = ("import { talked } from './src/emptyconv.js';"
                   f"process.stdout.write(String(talked({json.dumps(log, ensure_ascii=False)})))")
            r = subprocess.run(["node", "--input-type=module", "-e", src], cwd=ROOT / "ui", capture_output=True, text=True, timeout=60,
                               env={**os.environ, "MISE_OFFLINE": "1"})
            self.assertEqual((r.returncode, r.stdout), (0, "false"), r.stderr[-1500:])


class TBundle(unittest.TestCase):
    def test_bundle_uses_talk_not_length(self):
        js = (ROOT / "src/stewart/web_ui/app.js").read_text(encoding="utf-8")
        self.assertIn("empty-hint", js)
        flat = js.replace("'", '"').replace("`", '"').replace(" ", "")
        self.assertTrue('["user","assistant"].includes' in flat, "묶음에 빈 대화 판정(말 기준)이 없다")

    @unittest.skipUnless(_node_ok(), "node 없음")
    def test_node_emptyconv(self):
        r = subprocess.run(["node", "--test", "test/emptyconv.test.mjs"], cwd=ROOT / "ui", capture_output=True, text=True, timeout=60,
                           env={**os.environ, "MISE_OFFLINE": "1"})
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
