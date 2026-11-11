"""판 62 도구를 적극적으로(SW-14): chat 지시문에 '확인하고 말한다'(바깥 사실은 web_search·web_fetch, 근거·"(확인 못 함)"),
되짚기 3) 에 '어느 도구로 알아낼지'. 실제 모델로 차이를 재는 것은 평가 세션 몫(보고에 과제·잴 거리)."""

import unittest

from stewart import reflect
from stewart.config import load_profile


class TProactive(unittest.TestCase):
    def test_chat_prompt(self):
        s = load_profile("chat").system
        self.assertIn("확인하고 말한다", s)
        self.assertIn("(확인 못 함)", s)
        self.assertIn("web_fetch 로 원문", s)

    def test_reflect(self):
        self.assertIn("어느 도구로 알아낼지", reflect.CHAT_PROMPT)



from tests.test_hub import msg  # noqa: E402
from tests.test_hub3 import MainBase  # noqa: E402
from stewart import steer as ST  # noqa: E402

KC = "printf 'security: SecKeychainSearchCopyNext: User interaction is not allowed. (-25308)\\n' >&2; exit 44"


class TSteer(unittest.TestCase):
    def test_sig_and_boundary(self):
        r = "[exit 44]\nsecurity: SecKeychainSearchCopyNext: User interaction is not allowed. (-25308)"
        self.assertEqual(ST.err_sig(r), "-25308")
        self.assertIn("키체인", ST.human_boundary(r))
        self.assertEqual(ST.err_sig("[exit 1]\nerror: unknown option '--foo'"), "error: unknown option <값>")
        self.assertEqual(ST.err_sig("[exit 2]\nok"), "")
        self.assertIn("sudo", ST.human_boundary("sudo: a terminal is required to read the password"))
        self.assertEqual(ST.human_boundary("[exit 1]\nls: x: No such file or directory"), "")


class TSameError(MainBase):
    """실사용 사례 재현: 키체인 -25308 을 내는 서로 다른 명령 셋 → 사람 손 카드 알림(한 번) + 같은 오류 셋째에 되짚기."""
    def test_flow(self):
        self.main.sess.mode = "auto"
        cmds = [KC + " # credential fill", KC + " # find -w", KC + " # find -g"]
        self.r.scripts["MAIN"] = [msg(calls=[("run_shell", {"command": c})]) for c in cmds] + \
            [msg("되짚음: -25308 은 화면 없는 자리라 허용 창을 못 띄운 것.\n금지: 없음\n다음: board 사람 손 카드"), msg("사람 손 카드를 올렸다")]
        self.main.send("git push 해 줘")
        self.assertTrue(self.main.wait_idle())
        tools = [m for m in self.main.state()["main_log"] if m["kind"] == "tool"]
        res = [t.get("result") or "" for t in tools]
        self.assertIn("사람만 풀 수 있는 경계", res[0])
        self.assertIn("board add_todo", res[0])                        # 허브: 현황판 사용자 할 일 꼴로
        self.assertNotIn("사람만 풀 수 있는 경계", res[1])              # 차례에 한 번
        self.assertNotIn("같은 오류", res[1])
        self.assertIn("같은 오류(-25308)가 3번째", res[2])
        self.assertIn("security error <코드>", res[2])
        self.assertGreaterEqual(self.main.sess.st.get("reflects", 0), 1)


if __name__ == "__main__":
    unittest.main()
