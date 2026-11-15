"""판 156: 맡기기 장면 측정 도구를 평가 대행에 넣기 좋게.

- 회차가 어디서 깨지든(세션 만들기 포함) 오류 결과로 남는다. 결과가 모자라면 rc 2, 오류 회차가 있으면 rc 1.
- --out 머리에 expected·got·errors·scenes_sha. 회차 기록은 --work(기본 --out 옆 work/<칸>-<장면>-<회차>).
- 사용자 자리는 빈 임시 폴더, 기억은 끔으로 못박는다(--memory 로만 켬).
- hub 칸 첫 요청의 지시문·도구 정의가 `chat --once --hub-main` 과 같다.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, engine as E
from tests.test_hub import msg
from tests.test_realuse_shape import FakeModel, make_user_shape
from tests.test_u149_delegate_measure import tool
from tests.test_u60 import resp

ROOT = Path(__file__).resolve().parents[1]


class TMain(unittest.TestCase):
    def setUp(self):
        self.M = tool()
        self.out = Path(tempfile.mkdtemp(prefix="u156-")) / "d.json"
        e = mock.patch.dict(os.environ, {"STEWART_MEMORY": "1"})   # 바깥 환경이 기억을 켜도 도구가 끈다
        e.start()
        self.addCleanup(e.stop)
        self.sid = self.M.load()[0]["id"]

    def run_main(self, fake):
        argv = ["dm", "--lanes", "http://127.0.0.1:9/v1", "--model", "m", "-n", "2", "--only", self.sid,
                "--cells", "chat-nonudge", "--out", str(self.out), "--per-lane", "1"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(C, "chat", side_effect=fake), \
                mock.patch.object(E, "chat", side_effect=fake):
            rc = self.M.main()
        return rc, json.loads(self.out.read_text(encoding="utf-8"))

    def test_ok_meta_work_and_user_dir(self):
        seen = []

        def fake(backend, messages, tools=None, *a, **k):
            seen.append(str(messages[0]["content"]))
            return resp("끝")
        rc, d = self.run_main(fake)
        self.assertEqual(rc, 0)
        m = d["meta"]
        self.assertEqual((m["expected"], m["got"], m["errors"], m["memory"]), (2, 2, 0, False))
        self.assertEqual(len(m["scenes_sha"]), 16)
        rs = d["results"][f"chat-nonudge|{self.sid}"]
        self.assertEqual(sorted(r["work"] for r in rs), [f"work/chat-nonudge-{self.sid}-1", f"work/chat-nonudge-{self.sid}-2"])
        for r in rs:
            self.assertTrue((self.out.parent / r["work"] / "repo").is_dir())
            self.assertTrue(any((self.out.parent / r["work"] / "runs").rglob("events.jsonl")))
            self.assertEqual(r["bg_cleaned"], 0)
        self.assertEqual(os.environ["STEWART_MEMORY"], "0")
        ud = Path(os.environ["STEWART_USER_DIR"])
        self.assertFalse((ud / "STEWART.md").exists())                                              # 빈 사용자 자리
        self.assertNotIn("[기억]", seen[0])

    def test_error_rounds_rc1(self):
        with mock.patch.object(self.M.C, "ChatSession", side_effect=RuntimeError("생성 실패")):
            rc, d = self.run_main(lambda *a, **k: resp("끝"))
        self.assertEqual(rc, 1)
        self.assertEqual((d["meta"]["got"], d["meta"]["errors"]), (2, 2))                            # 스레드가 죽지 않고 결과로 남는다
        self.assertIn("생성 실패", d["results"][f"chat-nonudge|{self.sid}"][0]["error"])

    def test_backend_error_counts(self):
        def fake(*a, **k):
            raise RuntimeError("서버 없음")
        rc, d = self.run_main(fake)
        self.assertEqual(rc, 1)
        self.assertEqual(d["meta"]["errors"], 2)


class THubBytes(unittest.TestCase):
    """⑪: hub 칸 첫 요청의 지시문·도구 정의 = `chat --once --hub-main` 첫 요청(빈 사용자 자리·같은 웹 주소)."""

    def test_same_system_and_tools(self):
        M = tool()
        root = Path(tempfile.mkdtemp(prefix="u156h-"))
        fm = FakeModel(route=lambda b: msg("끝"))
        self.addCleanup(fm.close)
        env = make_user_shape(root, fm.url)
        env.update(M.empty_user_env(), STEWART_MEMORY="0", STEWART_SEARXNG_URL="http://127.0.0.1:9/search", STEWART_SEARXNG_MIN_INTERVAL="0")
        repo = M.make_repo(root / "cli-repo")
        said = "README 첫 줄을 알려 줘"
        r = subprocess.run([sys.executable, "-m", "stewart", "chat", "--once", "--backend", "cloud", "-p", "chat", "--hub-main",
                            "--workdir", str(repo), said], env=env, cwd=str(root), capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        cli = [b for b in fm.requests if b.get("tools")][0]
        got = {}

        def fake(backend, messages, tools=None, *a, **k):
            got.setdefault("b", {"messages": json.loads(json.dumps(messages)), "tools": tools})
            return resp("끝")
        same = {k: v for k, v in os.environ.items() if not k.startswith(("STEWART_", "XDG_"))}   # 시험 묶음이 켜 둔 STEWART_* 는 빼고
        same.update({k: v for k, v in env.items() if k.startswith(("STEWART_", "HOME", "XDG_"))})   # 하위 프로세스와 같은 환경
        with mock.patch.dict(os.environ, same, clear=True), mock.patch.object(C, "chat", side_effect=fake), mock.patch.object(E, "chat", side_effect=fake):
            from stewart.config import load_backends
            M.one({"id": "h", "kind": "should_not", "said": said}, load_backends()[0]["cloud"], "hub-nonudge", 12, work=root / "tool")

        def norm(s, rp):
            s = s.replace(str(rp), "<REPO>").replace(os.path.realpath(rp), "<REPO>")
            return re.sub(r"\d{4}-\d{2}-\d{2}", "<DATE>", s)
        a = norm(str(cli["messages"][0]["content"]), repo)
        b = norm(str(got["b"]["messages"][0]["content"]), root / "tool" / "repo")
        self.assertEqual(a, b)
        self.assertEqual(json.dumps(cli["tools"], ensure_ascii=False, sort_keys=True),
                         json.dumps(got["b"]["tools"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
