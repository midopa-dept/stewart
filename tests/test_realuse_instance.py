"""실사용 길 훑기 2(판 147): 따로 도는 허브(stewart instance) 꼴 — 바깥 API 하나(rubato 만 연결)로 도는 허브.

- 설정은 tests/realuse_instance_shape.make_instance(시험 HOME 의 ~/.config/stewart-<이름>), 띄우기는 instance.hub_argv·env 그대로.
  바깥 API 는 시험 안 가짜 모델(provider 를 적어 바깥으로 보인다), 키는 key_env 환경 변수. OS 샌드박스는 씌우지 않는다(규칙 글만 따로 본다).
- 길: 등급 표 · 새 대화 자리 둘(rubato) · 승인 셸 · 편집 · 작업자·위임(어느 백엔드로) · 닫았다 다시 열기·지난 대화 · 스킬 · 사진 ·
  돈 상한 · 키 없음·인증 거부 · 다시 켜기 · 셸에 키가 새지 않음 · 샌드박스 규칙.
"""

import json
import tempfile
import time
import unittest
from pathlib import Path

from stewart import instance as I
from tests.realuse_instance_shape import NAME, argv, make_instance
from tests.test_hub import msg
from tests.test_realuse_paths import PathBase, text_of, tool_results
from tests.test_realuse_shape import FakeModel, Hub, is_worker


class InstBase(PathBase):
    key = "test-key"
    extra_backends = ""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="realuse-inst-"))
        self.model = FakeModel(route=lambda b: self.route(b))
        self.addCleanup(self.model.close)
        self.cfg, self.env = make_instance(self.root, self.model.url, key=self.key, extra_backends=self.extra_backends)
        self.proj = Path(self.cfg["project"])
        (self.proj / "README.md").write_text("첫 줄\n둘째 줄\n", encoding="utf-8")
        self.start()

    def start(self):
        self.hub = Hub(self.root, self.env, argv=argv(self.cfg), port=int(self.cfg["port"]))
        self.addCleanup(lambda: self.hub.close())

    def new(self, kind="code"):
        return self.hub.post("/api/chat/new", {"how": "fresh", "kind": kind, "tier": "rubato", "title": "새 대화", "from": "main"})["chat"]

    def turn(self, text, chat=None, s=30):
        self.say(text, chat)
        st = self.idle(chat, s)
        self.assertTrue(st, self.log_tail())
        return st


class I01TiersAndPlaces(InstBase):
    def route(self, body):
        return msg("네, 바깥 모델입니다.")

    def test_rubato_only_both_places(self):
        rows = {r["tier"]: r for r in self.hub.get("/api/tiers")["rows"]}
        self.assertTrue(self.hub.get("/api/tiers")["instance"])
        self.assertEqual([t for t, r in rows.items() if r.get("connected")], ["rubato"])          # rubato 만 연결
        self.assertEqual((rows["rubato"]["provider"], rows["rubato"]["key"]), ("Outside", "있음"))
        for kind in ("code", "talk"):
            c = self.new(kind)
            n0 = len(self.model.requests)
            self.turn("안녕", c)
            body = next(b for b in self.model.requests[n0:] if b.get("tools") is not None or b.get("messages"))
            self.assertEqual(body.get("model"), "out-chat")
            st = self.state(c)
            self.assertIn("바깥 모델", [m.get("text") for m in st["main_log"] if m.get("kind") == "assistant"][-1])
        self.assertEqual(self.state()["backend"]["external"], "Outside")                         # 화면의 "바깥 API" 표시


class I02ShellApprovalAndEdit(InstBase):
    def route(self, body):
        ms = body["messages"]
        li = max(i for i, m in enumerate(ms) if m.get("role") == "user")
        last, n = ms[li], len([m for m in ms[li:] if m.get("role") == "tool"])
        if "셸" in str(last.get("content")) and n == 0:
            return msg(calls=[("run_shell", {"command": "touch made.txt"})])
        if "고쳐" in str(last.get("content")) and n == 0:
            return msg(calls=[("write_file", {"path": "a.py", "content": "x = 1\n"})])
        return msg("했다")

    def test_shell_card_and_edit(self):
        c = self.new()
        self.say("셸로 파일 만들어", c)
        q = self.hub.wait(lambda: next((x for x in self.state(c)["queue"] if x.get("kind") == "confirm"), None))
        self.assertTrue(q, self.log_tail())                                                       # edits 의 셸은 묻는다
        self.hub.post("/api/answer", {"session": q["session"], "seq": q["seq"], "answer": "y"})
        self.assertTrue(self.idle(c), self.log_tail())
        self.assertTrue((self.proj / "made.txt").exists())
        self.turn("a.py 고쳐", c)
        self.assertEqual((self.proj / "a.py").read_text(encoding="utf-8"), "x = 1\n")             # 편집은 묻지 않고(edits)


class I03KeyNotInShell(InstBase):
    """모델의 셸에 키가 새지 않는다(key_env 는 셸·도구 자식 환경에서 지운다)."""

    def route(self, body):
        if not tool_results(body):
            return msg(calls=[("run_shell", {"command": "echo KEY=[$OUT_TEST_KEY] > out.txt"})])
        return msg("했다")

    def test_key_hidden(self):
        c = self.new()
        self.say("셸", c)
        q = self.hub.wait(lambda: next((x for x in self.state(c)["queue"] if x.get("kind") == "confirm"), None))
        self.assertTrue(q, self.log_tail())
        self.hub.post("/api/answer", {"session": q["session"], "seq": q["seq"], "answer": "y"})
        self.assertTrue(self.idle(c), self.log_tail())
        self.assertEqual((self.proj / "out.txt").read_text(encoding="utf-8").strip(), "KEY=[]")


class I04Workers(InstBase):
    """작업자·위임이 어느 백엔드로 가는지(이 허브에는 바깥 API 하나뿐)."""

    def route(self, body):
        if is_worker(body):
            return msg(calls=[("read_file", {"path": "README.md"})]) if not tool_results(body) else msg("첫 줄")
        n = len(tool_results(body))
        if n == 0:
            return msg(calls=[("workers", {"action": "spawn", "task": "README.md 첫 줄을 알려라"})])
        if n == 1:
            return msg(calls=[("workers", {"action": "wait"})])
        return msg("받았다")

    def test_worker_backend(self):
        c = self.new()
        self.turn("맡겨라", c, s=60)
        res = [r for b in self.model.requests if not is_worker(b) for r in tool_results(b)]
        workers = [b for b in self.model.requests if is_worker(b)]
        self.facts = {"worker_requests": len(workers), "worker_models": sorted({b.get("model") for b in workers}),
                      "spawn_result": [r[:200] for r in res if "작업자" in r or "w1" in r][:2]}
        if workers:
            self.assertEqual({b.get("model") for b in workers}, {"out-chat"})                      # 작업자도 같은 바깥 API(돈이 같이 센다)


class I05CloseReopen(InstBase):
    def route(self, body):
        return msg("알겠다")

    def test_close_reopen_and_past(self):
        c = self.new()
        self.turn("첫 말 하나", c)
        run = self.hub.post("/api/chat/close", {"chat": c})["run_dir"]
        past = self.hub.get("/api/past_sessions")
        self.assertIn(run, json.dumps(past, ensure_ascii=False))                                  # 지난 대화에 보인다
        again = self.hub.post("/api/chat/new", {"how": "reopen", "run_dir": run, "title": "첫 말 하나"})["chat"]
        row = next(x for x in self.state()["chats"] if x["name"] == again)
        self.assertEqual(row.get("tier") or "", "rubato")
        n0 = len(self.model.requests)
        self.turn("다시 연 뒤", again)
        self.assertIn("첫 말 하나", text_of(self.model.requests[n0]))
        self.assertTrue(str(run).startswith(str(I.dirs(NAME, self.root / "home")["state"])))        # 기록은 인스턴스 자리 안


class I06SkillAndImage(InstBase):
    def route(self, body):
        last = [m for m in body["messages"] if m.get("role") == "user"][-1]
        if "스킬" in str(last.get("content")) and not tool_results(body):
            return msg(calls=[("load_skill", {"name": "inst-skill"})])
        return msg("했다")

    def setUp(self):
        super().setUp()

    def test_skill_and_image(self):
        sd = I.dirs(NAME, self.root / "home")["config"] / "skills" / "inst-skill"
        sd.mkdir(parents=True)
        (sd / "SKILL.md").write_text("---\nname: inst-skill\ndescription: 인스턴스 시험 스킬\n---\n\n본문 표지 SKL-7\n", encoding="utf-8")
        c = self.new()
        n0 = len(self.model.requests)
        self.turn("스킬 써", c)
        self.assertIn("inst-skill", self.model.requests[n0]["messages"][0]["content"])           # 인스턴스 스킬 자리
        self.assertTrue([r for b in self.model.requests[n0:] for r in tool_results(b) if "SKL-7" in r])
        from tests.test_realuse_paths import PNG
        st, data, _ = self.hub.req("POST", "/api/main", json.dumps({"text": "이 사진 뭐야", "chat": c, "images": [{"name": "a.png", "data": PNG}]}),
                                   {"Cookie": self.hub.cookie, "Content-Type": "application/json", "X-Stewart-Token": self.hub.csrf, "Origin": self.hub.origin})
        r = json.loads(data or b"{}")
        self.facts = {"status": st, "reply": r}
        self.assertEqual((st, r.get("error")), (400, "images"))                                   # vision 이 아닌 바깥 API — 그림은 보내기 전에 막는다
        self.assertIn("이미지를 받지 않는다", r.get("detail", ""))


class I07SpendCap(InstBase):
    extra_backends = '\n[tiers.rubato]\nbackend = "out"\nmax_usd_chat = 0.0001\n'

    def route(self, body):
        r = msg("긴 답")
        r["usage"] = {"prompt_tokens": 50000, "completion_tokens": 2000}
        return r

    def test_cap_reached(self):
        c = self.new()
        self.turn("첫 말", c)
        n0 = len(self.model.requests)
        st = self.turn("둘째 말", c)
        texts = [str(m.get("text") or "") for m in st["main_log"][-6:]]
        self.facts = {"requests_after_cap": len(self.model.requests) - n0, "screen": texts}
        self.assertEqual(len(self.model.requests) - n0, 0, texts)                                # 상한을 넘으면 보내지 않는다
        self.assertTrue([t for t in texts if "상한" in t], texts)                                  # 까닭이 화면에


class I08NoKey(InstBase):
    key = None

    def route(self, body):
        return msg("닿으면 안 됨")

    def test_no_key_message(self):
        rows = {r["tier"]: r for r in self.hub.get("/api/tiers")["rows"]}
        self.facts = {"rubato_status": rows["rubato"].get("status"), "key": rows["rubato"].get("key")}
        st = self.turn("안녕")
        texts = [str(m.get("text") or "") for m in st["main_log"][-5:]]
        self.assertEqual([b for b in self.model.requests if b.get("messages")], [])               # 키 없이 바깥으로 보내지 않는다
        self.assertTrue([t for t in texts if "키" in t], texts)


class I09AuthRejected(InstBase):
    def route(self, body):
        return msg("닿으면 안 됨")

    def test_401_message(self):
        self.model.status = 401
        st = self.turn("안녕", s=60)
        texts = [str(m.get("text") or "") for m in st["main_log"][-5:]]
        self.facts = {"screen": texts}
        self.assertTrue([t for t in texts if "401" in t or "인증" in t or "키" in t], texts)


class I10Restart(InstBase):
    def route(self, body):
        return msg("네")

    def test_restart_continues(self):
        self.turn("다시 켜기 전 말")
        self.hub.close()
        self.start()
        n0 = len(self.model.requests)
        self.turn("다시 켠 뒤")
        self.assertIn("다시 켜기 전 말", text_of(self.model.requests[n0]))


class I11SandboxProfile(unittest.TestCase):
    """샌드박스 규칙 글(instance profile): 쓰기는 자기 자리·임시만, 설정은 읽기만, 키체인 조회 막음, deny·허용 목록."""

    def test_profile_text(self):
        root = Path(tempfile.mkdtemp(prefix="realuse-inst-sb-"))
        m = FakeModel()
        self.addCleanup(m.close)
        cfg, env = make_instance(root, m.url)
        secret = root / "secret"
        secret.mkdir()
        (I.dirs(NAME, root / "home")["config"] / "instance.toml").write_text(
            (I.dirs(NAME, root / "home")["config"] / "instance.toml").read_text(encoding="utf-8") + f'deny = ["{secret.as_posix()}"]\n', encoding="utf-8")
        cfg = I.load(NAME, root / "home")
        sb = I.profile(cfg)
        self.assertIn('(deny file-write* (subpath "/"))', sb)
        self.assertIn(I._q(I._abs(str(cfg["project"]))), sb)
        self.assertIn(f'(deny file-read* file-write* (subpath {I._q(I._abs(str(secret)))}))', sb)
        self.assertIn('(deny mach-lookup (global-name "com.apple.SecurityServer")', sb)
        self.assertNotIn("test-key", sb)
        self.assertEqual(env.get("OUT_TEST_KEY"), "test-key")                                     # 키는 key_env 하나에만
        self.assertFalse([k for k, v in env.items() if v == "test-key" and k != "OUT_TEST_KEY"])


if __name__ == "__main__":
    unittest.main()
