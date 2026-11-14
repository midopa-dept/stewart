"""실사용 길 훑기(판 139–): 실사용 꼴 설정(test_realuse_shape.make_user_shape)으로 허브를 실제로 띄워, 사람이 화면에서 하는 길을 한 번씩 누른다.

- 길 목록과 결함 장부는 개발 기록에 따로 둔다. 이 파일은 그 길들의 시험이다.
- 모델은 시험 안 가짜 서버다(요청 본문을 보고 대본으로 답한다). 화면은 허브 HTTP API(화면이 쓰는 것과 같은 길)로 누른다.
- 묶음 1: 메인 한 마디 · 승인 셸(허락·거절) · 파일 편집과 diff · 작업자 둘(읽기·쓰기 동시)과 결과 모으기 · 위임(delegate)의 셸 승인.
- 묶음 2: 새 대화(자리 둘 × 등급 넷) · 갈래 · 닫기 → 다시 열기 · 사진 붙이기.
"""

import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from tests.test_hub import msg
from tests.test_realuse_shape import FakeModel, Hub, is_worker, make_user_shape


def text_of(body) -> str:
    return json.dumps(body.get("messages") or [], ensure_ascii=False)


def tool_results(body) -> list[str]:
    return [str(m.get("content")) for m in body.get("messages") or [] if m.get("role") == "tool"]


class PathBase(unittest.TestCase):
    """길 하나 = 허브 하나. route(body) 가 모델의 답을 정한다."""

    def route(self, body):
        return msg("끝")

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="realuse-path-"))
        self.model = FakeModel(route=lambda b: self.route(b))
        self.addCleanup(self.model.close)
        self.env = make_user_shape(self.root, self.model.url)
        self.proj = self.root / "project"
        self.proj.mkdir(exist_ok=True)
        (self.proj / "README.md").write_text("첫 줄\n둘째 줄\n", encoding="utf-8")
        self.hub = Hub(self.root, self.env)
        self.addCleanup(self.hub.close)

    def state(self, chat=None):
        return self.hub.get("/api/state" + (f"?chat={urllib.parse.quote(chat)}" if chat else ""))

    def say(self, text, chat=None):
        self.hub.post("/api/main", {"text": text, **({"chat": chat} if chat else {})})

    def idle(self, chat=None, s=30):
        return self.hub.wait(lambda: (lambda st: st if not st["busy"] else None)(self.state(chat)), s)

    def log_tail(self):
        return (self.root / "hub.log").read_text(encoding="utf-8")[-2000:]

    def main_reqs(self):
        return [b for b in self.model.requests if not is_worker(b) and b.get("tools")]


class T01MainSay(PathBase):
    def route(self, body):
        return msg("안녕하세요, 무엇을 할까요?")

    def test_main_one_message(self):
        self.say("안녕")
        st = self.idle()
        self.assertTrue(st, self.log_tail())
        self.assertIn("무엇을 할까요", [m for m in st["main_log"] if m.get("kind") == "assistant"][-1]["text"])
        self.assertEqual(st["mode"], "edits")


class T02ShellApproval(PathBase):
    def route(self, body):
        if not tool_results(body):
            return msg(calls=[("run_shell", {"command": "touch made.txt"})])
        return msg("끝")

    def _ask(self):
        return self.hub.wait(lambda: next((q for q in self.state()["queue"] if q.get("kind") == "confirm"), None))

    def test_allow(self):
        self.say("파일 하나 만들어")
        q = self._ask()
        self.assertTrue(q, self.log_tail())                                                    # 승인 카드가 뜬다(edits 의 셸)
        self.hub.post("/api/answer", {"session": q["session"], "seq": q["seq"], "answer": "y"})
        self.assertTrue(self.idle(), self.log_tail())
        self.assertTrue((self.proj / "made.txt").exists())

    def test_deny(self):
        self.say("파일 하나 만들어")
        q = self._ask()
        self.assertTrue(q, self.log_tail())
        self.hub.post("/api/answer", {"session": q["session"], "seq": q["seq"], "answer": "n"})
        self.assertTrue(self.idle(), self.log_tail())
        self.assertFalse((self.proj / "made.txt").exists())
        self.assertTrue(any("거부" in r or "거절" in r for r in tool_results(self.main_reqs()[-1])), tool_results(self.main_reqs()[-1]))


class T04EditDiff(PathBase):
    def route(self, body):
        n = len(tool_results(body))
        if n == 0:
            return msg(calls=[("write_file", {"path": "a.py", "content": "x = 1\n"})])
        if n == 1:
            return msg(calls=[("edit_file", {"path": "a.py", "old": "x = 1", "new": "x = 2"})])
        return msg("고쳤다")

    def test_edit_and_diff_card(self):
        self.say("a.py 를 만들고 고쳐")
        st = self.idle()
        self.assertTrue(st, self.log_tail())
        self.assertEqual((self.proj / "a.py").read_text(encoding="utf-8"), "x = 2\n")             # edits: 편집은 묻지 않고 바로
        self.assertFalse(st["queue"])
        diffs = [m for m in st["main_log"] if m.get("kind") == "diff"]
        self.assertTrue(diffs, [m.get("kind") for m in st["main_log"]])
        self.assertIn("x = 2", diffs[-1]["text"])


class T05Workers(PathBase):
    def route(self, body):
        if is_worker(body):
            t = text_of(body)
            done = len(tool_results(body))
            if "w.txt 에 써라" in t:
                return msg(calls=[("write_file", {"path": "w.txt", "content": "W"})]) if done == 0 else msg("w.txt 를 썼다")
            return msg(calls=[("read_file", {"path": "README.md"})]) if done == 0 else msg("README 첫 줄: 첫 줄")
        n = len(tool_results(body))
        if n == 0:
            return msg(calls=[("workers", {"action": "spawn", "task": "README.md 첫 줄을 알려라"}),
                              ("workers", {"action": "spawn", "task": "w.txt 에 써라", "tools": ["write_file"]})])
        if n == 2:
            return msg(calls=[("workers", {"action": "wait"})])
        return msg("둘 다 받았다")

    def test_two_workers_and_collect(self):
        self.say("둘로 나눠 맡겨라")
        self.assertTrue(self.idle(s=60), self.log_tail())
        self.assertEqual((self.proj / "w.txt").read_text(encoding="utf-8"), "W")                  # 쓰기 작업자(edits 메인)
        waited = [r for b in self.main_reqs() for r in tool_results(b) if "README 첫 줄" in r and "w.txt 를 썼다" in r]
        self.assertTrue(waited, [tool_results(b) for b in self.main_reqs()])                       # wait 로 둘 다 모음
        self.assertFalse([r for b in self.main_reqs() for r in tool_results(b) if "작업자가 실패했다" in r])


class T06Delegate(PathBase):
    def route(self, body):
        if is_worker(body):
            return msg(calls=[("run_shell", {"command": "touch d.txt"})]) if not tool_results(body) else msg("d.txt 를 만들었다")
        if not tool_results(body):
            return msg(calls=[("delegate", {"task": "셸로 d.txt 를 만들어라", "tools": ["run_shell"]})])
        return msg("위임 끝")

    def test_delegate_shell_approval(self):
        self.say("위임해서 만들어")
        q = self.hub.wait(lambda: next((q for q in self.state()["queue"] if q.get("kind") == "confirm"), None))
        self.assertTrue(q, self.log_tail())                                                    # 위임 작업자의 셸도 사람에게 묻는다
        self.hub.post("/api/answer", {"session": q["session"], "seq": q["seq"], "answer": "y"})
        self.assertTrue(self.idle(s=60), self.log_tail())
        self.assertTrue((self.proj / "d.txt").exists())
        self.assertTrue(any("d.txt 를 만들었다" in r for b in self.main_reqs() for r in tool_results(b)))


PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class T07NewChats(PathBase):
    def route(self, body):
        return msg("네")

    def test_places_by_tiers(self):
        for kind in ("code", "talk"):
            for tier in ("", "presto", "moderato", "largo"):
                with self.subTest(kind=kind, tier=tier):
                    name = self.hub.post("/api/chat/new", {"how": "fresh", "kind": kind, "title": "새 대화", "from": "main",
                                                           **({"tier": tier} if tier else {})})["chat"]
                    row = next(c for c in self.state()["chats"] if c["name"] == name)
                    self.assertEqual((row["kind"], row.get("tier") or ""), (kind, tier))
                    n0 = len(self.model.requests)
                    self.say("안녕", name)
                    st = self.idle(name)
                    self.assertTrue(st, self.log_tail())
                    self.assertTrue([m for m in st["main_log"] if m.get("kind") == "assistant"], st["main_log"][-5:])
                    body = next(b for b in self.model.requests[n0:] if b.get("tools"))
                    self.assertEqual(body.get("model"), "m-cloud")                                   # 표 없는 등급 = 허브 모델
                    self.assertEqual(body.get("reasoning_effort"), "medium" if tier == "moderato" else None)
                    self.hub.post("/api/chat/close", {"chat": name})


class T08ForkCloseReopen(PathBase):
    def route(self, body):
        return msg("네")

    def test_fork_then_close_and_reopen(self):
        name = self.hub.post("/api/chat/new", {"how": "fresh", "kind": "code", "tier": "moderato", "title": "새 대화", "from": "main"})["chat"]
        self.say("첫 말 하나", name)
        self.assertTrue(self.idle(name), self.log_tail())
        fork = self.hub.post("/api/chat/new", {"how": "fork", "from": name})["chat"]               # 갈래: 같은 자리·등급, 앞 말을 이어 받음
        row = next(c for c in self.state()["chats"] if c["name"] == fork)
        self.assertEqual((row["kind"], row.get("tier")), ("code", "moderato"))
        n0 = len(self.model.requests)
        self.say("갈래에서 둘째 말", fork)
        self.assertTrue(self.idle(fork), self.log_tail())
        body = next(b for b in self.model.requests[n0:] if b.get("tools"))
        self.assertIn("첫 말 하나", text_of(body))
        self.assertEqual(body.get("reasoning_effort"), "medium")
        run = self.hub.post("/api/chat/close", {"chat": name})["run_dir"]                           # 닫기 → 다시 열기
        self.assertTrue(run)
        again = self.hub.post("/api/chat/new", {"how": "reopen", "run_dir": run, "title": "첫 말 하나"})["chat"]
        n1 = len(self.model.requests)
        self.say("다시 열어 셋째 말", again)
        self.assertTrue(self.idle(again), self.log_tail())
        body = next(b for b in self.model.requests[n1:] if b.get("tools"))
        self.assertIn("첫 말 하나", text_of(body))
        row = next(c for c in self.state()["chats"] if c["name"] == again)
        self.assertEqual(row.get("tier") or "", "moderato")                                      # 다시 연 대화도 그 등급


class T10Image(PathBase):
    def route(self, body):
        return msg("사진을 봤다")

    def test_image_attach(self):
        name = self.hub.post("/api/chat/new", {"how": "fresh", "kind": "talk", "title": "새 대화", "from": "main"})["chat"]
        n0 = len(self.model.requests)
        self.hub.post("/api/main", {"text": "이 사진 뭐야", "chat": name, "images": [{"name": "a.png", "data": PNG}]})
        self.assertTrue(self.idle(name), self.log_tail())
        body = next(b for b in self.model.requests[n0:] if b.get("tools"))
        self.assertIn("image_url", text_of(body))                                                # vision 백엔드에 그림이 실린다


class T09PastPlaceTier(PathBase):
    """결함 R2(판 132–138): moderato 대화 기록은 첫 줄이 생각 깊이 사건이라, 첫 줄로 meta 를 읽는 지난 대화 목록·다시 열기가
    자리(parlando)·등급을 놓쳤다. 장면: parlando × moderato 대화에서 한 마디 → 닫기 → 지난 대화 목록 → 다시 열기."""

    def route(self, body):
        return msg("네")

    def test_parlando_moderato_closed_and_reopened(self):
        name = self.hub.post("/api/chat/new", {"how": "fresh", "kind": "talk", "tier": "moderato", "title": "새 대화", "from": "main"})["chat"]
        self.say("파를란도에서 한 마디", name)
        self.assertTrue(self.idle(name), self.log_tail())
        run = self.hub.post("/api/chat/close", {"chat": name})["run_dir"]
        first = json.loads((Path(run) / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(first["kind"], "run_start")                                             # 기록 첫 줄은 run_start
        past = self.hub.get("/api/past_sessions")
        rows = [r for r in (past.get("mains") or past.get("past") or []) if r.get("run_dir") == run] or \
               [r for v in past.values() if isinstance(v, list) for r in v if isinstance(r, dict) and r.get("run_dir") == run]
        self.assertTrue(rows, past)
        self.assertEqual(rows[0].get("place"), "talk")                                           # 지난 대화의 parlando 묶음
        again = self.hub.post("/api/chat/new", {"how": "reopen", "run_dir": run, "title": "파를란도에서 한 마디"})["chat"]
        row = next(c for c in self.state()["chats"] if c["name"] == again)
        self.assertEqual((row["kind"], row.get("tier") or ""), ("talk", "moderato"))


if __name__ == "__main__":
    unittest.main()
