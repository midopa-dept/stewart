"""U2-8 실사용 사례(사용자가 붙여 준 것): .env·.env.example 를 cat 하는 for 고리가 마지막 `[ -f … ] && cat` 거짓으로 exit 1 →
모델이 실패로 보고 같은 고리를 되풀이. 되짚기 글은 맞았는데 행동이 안 바뀜. 고친 것: 무해한 exit 1 은 실패로 안 셈,
되짚기 결론(금지)을 도구 층에서 지킴(글자가 조금 달라도 같은 꼴), 비밀 파일 내용 읽기는 기본 거절, 새 사람 말이 목표를 덮음."""

import json
import os
import unittest

from stewart import steer as ST
from tests.test_hub import msg
from tests.test_hub3 import MainBase

LOOP = 'for d in a b c; do echo "== $d"; [ -f "$d/.env.example" ] && cat "$d/.env.example"; done'
LOOP2 = 'for d in a b c d; do [ -f $d/.env.example ] && cat $d/.env.example; done'     # 글자는 다르지만 같은 꼴
REFLECTION = ("같은 고리를 네 번 불렀습니다. 종료 코드 문제였고 내용은 이미 받았으니 이 걸음은 건너뛰고 보고하겠습니다.\n"
              "1) …\n5) .env.example 을 다시 읽는 일은 목표에 필요 없다.\n6) 보고한다.\n"
              "금지: run_shell cat .env.example\n다음: 없음")


class TUnits(unittest.TestCase):
    def test_soft_exit(self):
        self.assertTrue(ST.soft_exit(LOOP, "[exit 1]\n== a\nKEY=1\n== c\n"))
        self.assertTrue(ST.soft_exit("grep -n foo a.txt", "[exit 1]\n(출력)\n"))
        self.assertFalse(ST.soft_exit(LOOP, "[exit 1]\n"))                  # 출력이 없으면 실패
        self.assertFalse(ST.soft_exit("python3 x.py", "[exit 1]\nTraceback …\n"))
        self.assertFalse(ST.soft_exit(LOOP, "[exit 2]\n…\n"))

    def test_shape_similar(self):
        self.assertGreaterEqual(ST.similar(ST.shape(LOOP), ST.shape(LOOP2)), ST.SIMILAR)
        self.assertLess(ST.similar(ST.shape(LOOP), ST.shape("ls -la a b c")), ST.SIMILAR)

    def test_secret_reads(self):
        sec = lambda n: ST.is_secret_name(n)   # noqa: E731
        self.assertEqual(ST.secret_reads("cat a/.env", sec), ["a/.env"])
        self.assertEqual(ST.secret_reads("for f in .env .env.local; do cat $f; done", sec), [".env", ".env.local"])
        self.assertEqual(ST.secret_reads("ls -l .env && wc -c .env", sec), [])          # 이름·크기만
        self.assertEqual(ST.secret_reads("cat .env.example", sec), [])                   # 견본은 비밀이 아니다
        self.assertTrue(ST.user_named(".env 를 읽어서 키를 알려 줘", [".env"]))
        self.assertFalse(ST.user_named(".env 가 있는지 보고 내용은 출력하지 말고 경로만 알려 줘", [".env"]))   # 금지 조건
        self.assertFalse(ST.user_named(".env 경로와 크기만 알려 줘", [".env"]))
        self.assertFalse(ST.user_named(".env 가 있나?", [".env"]))                                  # 읽으라는 말 없음

    def test_parse_reflect(self):
        p = ST.parse_reflect(REFLECTION, ["run_shell", "read_file"])
        self.assertEqual(len(p["bans"]), 1)
        b = p["bans"][0]
        self.assertEqual(b.tool, "run_shell")
        self.assertTrue(b.matches("run_shell", "{}", LOOP2))
        self.assertFalse(b.matches("run_shell", "{}", "git init"))
        self.assertEqual(ST.parse_reflect("금지: 없음", ["run_shell"])["bans"], [])


class TCase(MainBase):
    def setUp(self):
        super().setUp()
        for d in "ab":
            (self.wd / d).mkdir()
            (self.wd / d / ".env.example").write_text("KEY=\n")
        (self.wd / "c").mkdir()
        (self.wd / "a" / ".env").write_text("SECRET=xyz\n")
        self.main.sess.mode = "auto"
        self.main.sess.rf = {**self.main.sess.rf, "on": True}

    def test_case(self):
        sh = lambda c: ("run_shell", {"command": c})   # noqa: E731
        self.r.scripts["MAIN"] = [msg(calls=[sh(LOOP)]) for _ in range(4)] + [
            msg(REFLECTION),                         # 되짚기
            msg(calls=[sh(LOOP2)]),                  # 글자만 바꾼 같은 꼴 → 금지로 거절
            msg(calls=[sh("cat a/.env")]),           # 비밀 파일 내용 → 거절(auto)
            msg("a·b 에 .env.example 이 있고 c 에는 없습니다. 비밀 파일 내용은 읽지 않았습니다.")]
        self.main.send("폴더마다 .env 와 .env.example 이 있는지 보고 내용은 출력하지 말고 경로만 알려 줘")
        self.assertTrue(self.main.wait_idle(20))
        log = self.main.state()["main_log"]
        tools = [m for m in log if m["kind"] == "tool"]
        self.assertTrue(all(t["ok"] for t in tools[:4]))                        # exit 1 이지만 출력이 있는 [ ] && cat → 실패 아님
        self.assertIn("출력은 받았다 · 종료 코드 1", tools[0]["result"])
        self.assertIn("이미 받은 결과", tools[1]["result"])                         # 두 번째부터 알림
        self.assertEqual([m["state"] for m in log if m["kind"] == "reflect"], ["done"])
        self.assertIn("다시 하지 않기로 한 호출", tools[4]["result"])                # 같은 꼴(LOOP2)을 도구 층에서 거절
        self.assertIn("비밀 꼴 파일", tools[5]["result"])
        self.assertNotIn("SECRET=xyz", json.dumps(self.r.requests["MAIN"], ensure_ascii=False))   # 비밀 내용이 모델에 가지 않았다
        bans = self.main.state()["bans"]
        self.assertTrue(any("cat .env.example" in b["text"] for b in bans))
        self.assertTrue(any(b["hits"] for b in bans))
        self.assertEqual([m for m in log if m["kind"] == "turn_end"][-1]["reason"], "answered")

        # 사람이 "다 집어치우고 …" — 새 말이 앞 목표를 덮는다는 엔진 알림이 그 말에 붙는다(금지가 남아 있으므로)
        self.r.scripts["MAIN"] = [msg(".gitignore 를 만들고 git init 하겠습니다.")]
        self.main.send("다 집어치우고 .gitignore 작성하고 git init 해라")
        self.assertTrue(self.main.wait_idle(20))
        said = [m for m in self.r.requests["MAIN"][-1]["messages"] if m["role"] == "user" and "다 집어치우고" in str(m["content"])]
        self.assertIn("이 사람 말이 지금 목표다", str(said[-1]["content"]))

        # 사람이 금지를 푼다
        bid = bans[0]["id"]
        self.assertTrue(self.main.sess.unban(bid))
        self.assertNotIn(bid, [b["id"] for b in self.main.state()["bans"]])

    def test_secret_ok_when_user_names_it(self):
        self.r.scripts["MAIN"] = [msg(calls=[("run_shell", {"command": "cat a/.env"})]), msg("읽었습니다.")]
        self.main.send("a/.env 를 cat 해서 보여 줘")                                  # 사람이 그 파일을 직접 말함
        self.assertTrue(self.main.wait_idle(20))
        tool = [m for m in self.main.state()["main_log"] if m["kind"] == "tool"][0]
        self.assertIn("SECRET=xyz", tool["result"])

    def test_narration_full_text_with_calls(self):
        """도구 호출과 함께 온 글이 한 줄로 잘리지 않고 대화에 차례대로 남는다(작업 카드 밖 본문)."""
        long = "알겠습니다. 우선 폴더 구조부터 보겠습니다. " + "그다음 시험을 돌려 깨진 곳을 찾겠습니다. " * 12
        self.r.scripts["MAIN"] = [msg(long, calls=[("run_shell", {"command": "ls"})]), msg("다 봤습니다.")]
        self.main.send("구조 봐 줘")
        self.assertTrue(self.main.wait_idle(20))
        kinds = [(m["kind"], m["text"][:12]) for m in self.main.state()["main_log"] if m["kind"] in ("assistant", "tool", "user")]
        self.assertEqual([k for k, _ in kinds], ["user", "assistant", "tool", "assistant"])
        said = [m["text"] for m in self.main.state()["main_log"] if m["kind"] == "assistant"][0]
        self.assertGreater(len(said), 300)


if __name__ == "__main__":
    unittest.main()


class TNowAfterApproval(MainBase):
    def test_now_after_answer_is_tool_running(self):
        """tag 45 실사용: 승인 대기가 없는데 "사람 승인 기다리는 중". 원인 = 답을 받은 뒤 지금 하는 일 글을 되돌리지 않음."""
        import time
        from tests.test_hub import wait
        self.r.scripts["MAIN"] = [msg(calls=[("run_shell", {"command": "sleep 2; echo done"})]), msg("끝")]
        self.main.send("돌려")
        q = wait(lambda: self.hub.queue())
        self.assertTrue(self.main.state()["now"]["what"].startswith("사람 승인 기다리는 중"))
        self.hub.answer("main", q[0]["seq"], "y", by="human", via="test")
        time.sleep(0.6)                                                        # 셸이 도는 중
        st = self.main.state()
        self.assertEqual(st["queue"], [])
        self.assertTrue(st["now"]["what"].startswith("도구 실행 중: run_shell"), st["now"])
        self.assertTrue(self.main.wait_idle(20))


class TNarrateNudge(MainBase):
    def test_silent_tools_get_one_nudge(self):
        from stewart.chat import NARRATE_NOTE, SILENT_STEPS
        for i in range(SILENT_STEPS + 2):
            (self.wd / f"n{i}.txt").write_text(str(i))
        self.r.scripts["MAIN"] = [msg(calls=[("read_file", {"path": f"n{i}.txt"})]) for i in range(SILENT_STEPS + 2)] + [msg("다 읽었습니다.")]
        self.main.sess.max_steps = 30
        self.main.send("읽어")
        self.assertTrue(self.main.wait_idle(20))
        last = self.r.requests["MAIN"][-1]["messages"]
        self.assertEqual(sum(NARRATE_NOTE in str(m.get("content")) for m in last if m["role"] == "user"), 1)   # 차례에 한 번만
