"""U2-8 실사용(tag 45): 같은 호출을 결과가 같은 채로 되풀이하면 묻기 전에 스스로 되짚고(도구 없이), 같은 호출은 막고,
다른 길로 이어 간다. 되짚기를 다 쓰고도 같은 고리면 그때 사람에게 — 무엇을 해 봤고 왜 막혔는지 + 고를 길."""

import json
import os
from unittest import mock

from tests.test_hub import msg, wait
from tests.test_hub3 import MainBase

LOOP = 'for d in a b c; do echo "== $d"; ls "$d" 2>/dev/null | wc -l; done'
REFLECTION = ("1) 지금까지 한 것: for 로 a b c 를 돌며 파일 수를 셌다. 결과는 세 번 다 같았다.\n2) 왜 막혔나: 결과를 이미 받았는데 다시 불렀다.\n"
              "3) 무엇을 모르나: 없음.\n4) 다르게 할 길: 결과를 정리해 보고 / 폴더별로 따로 / find 로 세기\n5) 고른 길: 결과를 정리해 보고한다.")


class TSelfReflect(MainBase):
    def setUp(self):
        super().setUp()
        for d in "abc":
            (self.wd / d).mkdir()
            (self.wd / d / "f.txt").write_text("x")
        self.main.sess.rf = {**self.main.sess.rf, "on": True}   # 세션은 setUp 에서 만들어졌다(STEWART_REFLECT=0 판에서도 켬)

    def test_repeat_then_reflect_then_other_way(self):
        self.main.sess.mode = "auto"             # 셸 승인 없이(되짚기 흐름만 본다)
        shell = ("run_shell", {"command": LOOP})
        self.r.scripts["MAIN"] = [msg(calls=[shell]) for _ in range(4)] + [
            msg(REFLECTION),                       # 되짚기(도구 없음)
            msg(calls=[shell]),                    # 같은 호출 → 막힘(돌리지 않음)
            msg("a·b·c 에 파일이 하나씩 있다.")]
        self.assertEqual(self.main.send("폴더마다 파일 수를 세 알려 줘"), "started")
        self.assertTrue(self.main.wait_idle(20))
        log = self.main.state()["main_log"]
        refl = [m for m in log if m["kind"] == "reflect"]
        self.assertEqual(len(refl), 1)
        self.assertEqual((refl[0]["state"], refl[0]["label"]), ("done", "같은 호출 4번"))
        self.assertIn("고른 길", refl[0]["body"])
        self.assertEqual(log[[m["kind"] for m in log].index("turn_end")]["reason"], "answered")
        tools = [m for m in log if m["kind"] == "tool"]
        self.assertEqual(len(tools), 5)
        self.assertFalse(tools[-1]["ok"])                                    # 되짚기 뒤 같은 호출은 막혔다
        self.assertFalse([m for m in log if m["kind"] == "warn" and "되풀이해 멈췄다" in m["text"]])
        reqs = self.r.requests["MAIN"]
        self.assertNotIn("tools", reqs[4])                                   # 되짚기 호출은 도구 없이
        self.assertIn("다르게 할 길 2~3개", reqs[4]["messages"][-1]["content"])
        self.assertIn("결과 같음(성공", reqs[4]["messages"][-1]["content"])   # 같은 호출·같은 (성공) 결과를 사실로 보인다
        blocked = [m for m in reqs[6]["messages"] if m["role"] == "tool"][-1]["content"]
        self.assertIn("되짚기 뒤라", blocked)

    def test_used_up_then_ask_with_summary(self):
        os.environ["STEWART_REFLECT_MAX"] = "1"
        self.addCleanup(os.environ.pop, "STEWART_REFLECT_MAX", None)
        self.main.sess.rf = {**self.main.sess.rf, "max": 1, "on": True}
        self.main.sess.max_steps = 30                                          # 단계 상한 묻기가 끼지 않게
        rd = ("read_file", {"path": "a/f.txt"})
        rd2 = ("read_file", {"path": "b/f.txt"})
        self.r.scripts["MAIN"] = ([msg(calls=[rd]) for _ in range(4)] + [msg(REFLECTION)]
                                  + [msg(calls=[rd2]) for _ in range(6)]       # 되짚고도 다른 호출로 또 같은 고리
                                  + [msg("알겠다, 정리해 보고한다.")])
        self.main.send("읽어")
        q = wait(lambda: self.hub.queue(), timeout=20)
        self.assertIn("스스로 되짚기 1번으로도 풀리지 않았다", q[0]["prompt"])
        self.assertIn("마지막 되짚기", q[0]["prompt"])
        self.assertIn("고를 길", q[0]["prompt"])
        self.hub.answer("main", q[0]["seq"], "읽기는 그만하고 지금까지 본 것으로 보고해라", by="human", via="test")
        self.assertTrue(self.main.wait_idle(20))
        last = self.r.requests["MAIN"][-1]["messages"]
        self.assertTrue(any(m["role"] == "user" and "[사람] 읽기는 그만하고" in str(m.get("content")) for m in last))
        self.assertEqual([m for m in self.main.state()["main_log"] if m["kind"] == "turn_end"][-1]["reason"], "answered")


class TTodoCard(MainBase):
    def test_todo_snapshot_and_note(self):
        """할 일 목록: todo_write 줄에 그때의 목록(앞 카드는 접힌 한 줄로 이것을 쓴다), 허브 메인 지시에 목록 쓰기."""
        from stewart.hubmain import HUB_MAIN_NOTE
        self.assertIn("todo_write", HUB_MAIN_NOTE)
        (self.tmp / "main.toml").write_text('name = "main"\ntools = ["read_file", "todo_write"]\nmax_steps = 10\n'
                                            'system = "MAIN 지시"\n', encoding="utf-8")
        from stewart.cli import resolve
        from stewart.hubmain import HubMain
        from stewart.record import Recorder
        self.main.close()
        profile, backend = resolve(str(self.tmp / "main.toml"), "fake", str(self.wd))
        self.main = HubMain(self.hub, profile, backend, Recorder(self.tmp / "runs", "hub-main"), mode="edits")
        self.addCleanup(self.main.close)
        tw = lambda n: ("todo_write", {"items": [{"content": c, "status": "done" if i < n else "in_progress" if i == n else "pending"}  # noqa: E731
                                                 for i, c in enumerate(["읽기", "고치기", "보고"])]})
        self.r.scripts["MAIN"] = [msg(calls=[tw(0)]), msg(calls=[tw(1)]), msg("했다")]
        self.main.send("해")
        self.assertTrue(self.main.wait_idle())
        snaps = [m["todos"] for m in self.main.state()["main_log"] if m["kind"] == "tool" and m["text"].startswith("todo_write")]
        self.assertEqual([[t["status"] for t in s] for s in snaps], [["in_progress", "pending", "pending"], ["done", "in_progress", "pending"]])
        self.assertEqual([t["status"] for t in self.main.state()["todos"]], ["done", "in_progress", "pending"])


class TResultFull(MainBase):
    def test_full_result_on_recent_tools_only(self):
        """작업 카드 펼치기 = 모델이 받은 결과 그대로(끝 15줄이 아니라). 상태에는 최근 도구 20개만 싣는다."""
        from stewart import hubmain as HM
        self.main.sess.mode = "auto"
        cmd = "for i in $(seq 1 40); do echo line$i; done"
        self.r.scripts["MAIN"] = [msg(calls=[("run_shell", {"command": cmd})])] + [
            msg(calls=[("read_file", {"path": f"x{i}.txt"})]) for i in range(HM.RESULT_KEEP + 2)] + [msg("끝")]
        self.main.sess.max_steps = 40
        for i in range(HM.RESULT_KEEP + 2):
            (self.wd / f"x{i}.txt").write_text(f"내용 {i}")                    # 실패가 이어져 되짚기가 끼지 않게
        self.main.send("돌려")
        self.assertTrue(self.main.wait_idle(30))
        tools = [m for m in self.main.state()["main_log"] if m["kind"] == "tool"]
        self.assertNotIn("result", tools[0])                                  # 오래된 것은 덜어냄
        self.assertEqual(len([t for t in tools if "result" in t]), HM.RESULT_KEEP)
        full = [m for m in self.main.ui.log if m["kind"] == "tool"][0]["result"]
        self.assertIn("line1\n", full)                                        # 끝 15줄이 아니라 앞부터
        self.assertIn("line40", full)
