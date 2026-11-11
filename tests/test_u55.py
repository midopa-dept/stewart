"""판 74: 사진 붙이기가 폰·데스크톱 모두 막히던 일 — 화면 CSP 에 img-src(data:·blob:)가 없어 전처리 디코드가 막혔다.
type 빈 파일·붙여넣기 items·건너뛸 때 토스트·서버 거절 문구. 화면 쪽 판정 시험은 ui/test/imgprep.test.mjs(node 가 있으면 여기서도 돈다)."""

import os
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.test_hub3 import TWeb

ROOT = Path(__file__).resolve().parents[1]


def _node_ok() -> bool:
    """진짜 node 가 바로 도는가(mise 덧대기가 내려받으려 하면 건너뛴다)."""
    if not shutil.which("node"):
        return False
    try:
        r = subprocess.run(["node", "--version"], cwd=ROOT / "ui", capture_output=True, text=True, timeout=10,
                           env={**os.environ, "MISE_OFFLINE": "1"})
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and r.stdout.startswith("v")


class TCsp(TWeb):
    def test_img_src_allows_data_and_blob(self):
        ck, _ = self.login()
        r, _ = self.req("GET", "/", headers={"Cookie": ck})
        csp = r.getheader("Content-Security-Policy")
        self.assertIn("img-src 'self' data: blob:", csp)
        self.assertIn("default-src 'self'", csp)
        self.assertNotIn("script-src 'self' data:", csp)


for _n in [n for n in dir(TWeb) if n.startswith("test_")]:   # 물려받은 TWeb 시험은 여기서 다시 돌리지 않는다
    setattr(TCsp, _n, None)


class TBundle(unittest.TestCase):
    def test_built_bundle_has_new_paths(self):
        js = (ROOT / "src/stewart/web_ui/app.js").read_text(encoding="utf-8")
        for s in ("그림 파일이 아니라", "원본으로 보낸다", "getAsFile", "이 백엔드는 이미지를 받지 않는다", "사진과 함께 보내지 못했다"):
            self.assertIn(s, js)

    @unittest.skipUnless(_node_ok(), "node 없음")
    def test_node_imgprep(self):   # 꾸러미 없이 도는 시험만(깨끗한 CI 러너에는 node 는 있어도 ui/node_modules 가 없다)
        r = subprocess.run(["node", "--test", "test/imgprep.test.mjs"], cwd=ROOT / "ui", capture_output=True, text=True, timeout=60,
                           env={**os.environ, "MISE_OFFLINE": "1"})
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])

    @unittest.skipUnless(_node_ok() and (ROOT / "ui" / "node_modules" / "marked").is_dir(), "node 또는 ui/node_modules(marked) 없음")
    def test_node_mdtilde(self):   # marked 를 불러오므로 npm 꾸러미가 깔린 개발 자리에서만
        r = subprocess.run(["node", "--test", "test/mdtilde.test.mjs"], cwd=ROOT / "ui", capture_output=True, text=True, timeout=60,
                           env={**os.environ, "MISE_OFFLINE": "1"})
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
