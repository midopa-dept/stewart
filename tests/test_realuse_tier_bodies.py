"""판 150: 허브 네 꼴·측정 길(chat --once)의 첫 요청 본문 대조 — 실사용 꼴 표(등급 표 없음 / [tiers.moderato] 한 칸·max 3).

- 허브: ① 새 대화 largo = ② 새 대화 등급 없음 = ③ 허브 메인(--tier 없이) — 지시문·도구·model·max_tokens·생각 칸이 같다.
  ④ 새 대화 moderato 는 reasoning_effort = medium 만 더해진다(백엔드 effort_values 에 medium 이 있을 때).
- 측정 길 `chat --once -p chat`(등급 없음)은 허브 메인과 **다르다**: 허브 역할 글(지시문 줄)과 허브 도구(board·session_*)가 없다.
  max_tokens·생각 칸은 같다. 그 차이를 못 박는다(측정 칸 정의가 이 차이를 알고 쓰도록).
- `chat --once --tier largo` 는 표에 [tiers.largo] 칸이 있어야 켜진다. 칸의 생각 손잡이만 본문에 더해진다(thinking_token_budget·max_tokens).
- 판 151: `chat --once --hub-main` 은 허브 메인(③)과 본문이 같다(역할 글 + 허브 도구 정의). 허브 도구를 부르면 거절 글 하나·사건 hub_tool_refused.
- 허브 메인이 띄우는 작업자: 표에 moderato(default_for write)가 있으면 쓰기 작업자만 medium, 읽기 작업자는 생각 칸 없음.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_hub import msg
from tests.test_realuse_shape import FakeModel, Hub, is_worker, make_user_shape

TASK = "README 첫 줄을 알려 줘"
MODERATO = '\n[tiers.moderato]\nuse = "코딩·대화의 기본(쓰기 작업자)"\ndefault_for = ["write"]\nmax = 3\n'
KEYS = ("model", "max_tokens", "temperature", "chat_template_kwargs", "reasoning_effort", "thinking_token_budget", "tool_choice")
HUB_TOOLS = {"board", "session_spawn", "session_send", "session_read_report", "session_list", "session_stop", "session_resume"}


def view(b: dict, root: Path) -> dict:
    ms = b.get("messages") or []
    sysm = str(ms[0].get("content")) if ms else ""
    for r in (str(root), os.path.realpath(root)):
        sysm = sysm.replace(r, "<ROOT>")
    return {"keys": {k: b.get(k) for k in KEYS if k in b}, "system": re.sub(r"\d{4}-\d{2}-\d{2}", "<DATE>", sysm),
            "tools": [t["function"]["name"] for t in b.get("tools") or []]}


class Base(unittest.TestCase):
    table = ""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="tierbody-"))
        self.model = FakeModel(route=lambda b: self.route(b))
        self.addCleanup(self.model.close)
        self.env = make_user_shape(self.root, self.model.url)
        bt = self.root / "home" / ".config" / "stewart" / "backends.toml"
        bt.write_text(bt.read_text(encoding="utf-8") + self.table, encoding="utf-8")
        (self.root / "project").mkdir(exist_ok=True)
        (self.root / "project" / "README.md").write_text("첫 줄\n", encoding="utf-8")

    def route(self, b):
        return msg("네")

    def hub_first(self, h, tier=None, main=False):
        n0 = len(self.model.requests)
        if main:
            h.post("/api/main", {"text": TASK})
            self.assertTrue(h.wait(lambda: not h.get("/api/state")["busy"]))
        else:
            c = h.post("/api/chat/new", {"how": "fresh", "kind": "code", "title": "새 대화", "from": "main", **({"tier": tier} if tier else {})})["chat"]
            h.post("/api/main", {"text": TASK, "chat": c})
            self.assertTrue(h.wait(lambda: not h.get(f"/api/state?chat={c}")["busy"]))
        return view([b for b in self.model.requests[n0:] if b.get("tools")][0], self.root)

    def cli_first(self, *extra):
        n0 = len(self.model.requests)
        cmd = [sys.executable, "-m", "stewart", "chat", "--once", "-p", "chat", "--backend", "cloud", "--workdir", str(self.root / "project"), *extra, TASK]
        r = subprocess.run(cmd, env=self.env, cwd=str(self.root), capture_output=True, text=True, timeout=120)
        bs = [b for b in self.model.requests[n0:] if b.get("tools")]
        return (view(bs[0], self.root) if bs else None), r


class THubForms(Base):
    table = MODERATO

    def test_hub_forms_and_cli(self):
        h = Hub(self.root, self.env)
        self.addCleanup(h.close)
        main = self.hub_first(h, main=True)
        largo = self.hub_first(h, "largo")
        none = self.hub_first(h)
        mod = self.hub_first(h, "moderato")
        self.assertEqual(largo, none)                                                               # ① = ②
        self.assertEqual(largo, main)                                                               # ① = ③
        self.assertEqual({**largo["keys"], "reasoning_effort": "medium"}, mod["keys"])              # ④ = ① + medium
        self.assertEqual((mod["system"], mod["tools"]), (largo["system"], largo["tools"]))
        self.assertEqual(largo["keys"]["max_tokens"], 8192)                                        # 허브가 실제로 보내는 값(백엔드 params)
        h.close()
        l0, r = self.cli_first()
        self.assertIsNotNone(l0, r.stderr[-400:])
        self.assertEqual(l0["keys"], largo["keys"])                                                 # ⑤: 생각 칸·max_tokens·모델은 같다
        self.assertEqual(set(largo["tools"]) - set(l0["tools"]), HUB_TOOLS)                         # ⑤: 허브 도구만 없다
        self.assertNotIn("허브 메인으로서", l0["system"])                                            # ⑤: 허브 역할 글이 없다
        self.assertIn("허브 메인으로서", largo["system"])
        m1, r = self.cli_first("--tier", "moderato")
        self.assertEqual(m1["keys"], mod["keys"], r.stderr[-400:])                                  # ⑥ = ④ 의 칸(지시문·도구는 ⑤ 와 같은 차이)
        hm, r = self.cli_first("--hub-main")
        self.assertEqual(hm, main, r.stderr[-400:])                                                 # 판 151: --hub-main = ③ — 지시문·도구 33·생각 칸·max_tokens 까지
        self.assertEqual(len(hm["tools"]), 33)
        hmm, r = self.cli_first("--hub-main", "--tier", "moderato")
        self.assertEqual(hmm, mod, r.stderr[-400:])                                                 # --hub-main --tier moderato = ④


class TCliTierWithoutRow(Base):
    def test_unlisted_tier_refused(self):
        v, r = self.cli_first("--tier", "largo")
        self.assertIsNone(v)                                                                        # 표에 칸이 없으면 --tier largo 는 거절 — L0 은 등급 없이 잰다
        self.assertIn("등급이 없다", r.stderr)
        v, r = self.cli_first("--hub-main", "--tier", "largo")
        self.assertIsNone(v)                                                                        # 판 151: --hub-main 이어도 같다
        self.assertIn("등급이 없다", r.stderr)


class THubMainRefusal(Base):
    """판 151: --hub-main 에서 허브 도구를 부르면 늘 같은 거절 글, 기록에 hub_tool_refused."""

    def route(self, b):
        if not b.get("tools"):
            return msg("")
        if not any(x.get("role") == "tool" for x in b["messages"]):
            return msg(calls=[("session_spawn", {"name": "w1", "task": "README 읽어라"}), ("board", {"action": "list"})])
        return msg("허브 없이 끝냈다")

    def test_refusal(self):
        from stewart.hubmain import HUB_ABSENT
        runs = self.root / "runs-hm"
        v, r = self.cli_first("--hub-main", "--runs-dir", str(runs))
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        last = [b for b in self.model.requests if b.get("tools")][-1]
        tool_msgs = [str(x.get("content")) for x in last["messages"] if x.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 2)
        self.assertTrue(all(HUB_ABSENT in t for t in tool_msgs), tool_msgs)                         # 한 가지 글
        evs = [json.loads(x) for d in runs.iterdir() if d.is_dir() for x in (d / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sorted(e["name"] for e in evs if e.get("kind") == "hub_tool_refused"), ["board", "session_spawn"])

    def test_needs_once(self):
        r = subprocess.run([sys.executable, "-m", "stewart", "chat", "--hub-main", "--worker", "read", "--once", "-p", "chat", TASK],
                           env=self.env, cwd=str(self.root), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--hub-main", r.stderr)


class TCliLargoKnobs(Base):
    table = MODERATO + '\n[tiers.largo]\nthinking_token_budget = 8192\nthink_max_tokens = 16384\n'

    def test_largo_knobs(self):
        l0, _ = self.cli_first()
        lt, r = self.cli_first("--tier", "largo")
        self.assertIsNotNone(lt, r.stderr[-400:])
        self.assertEqual({k: v for k, v in lt["keys"].items() if k not in ("max_tokens", "thinking_token_budget")},
                         {k: v for k, v in l0["keys"].items() if k not in ("max_tokens", "thinking_token_budget")})
        self.assertEqual((lt["keys"]["max_tokens"], lt["keys"].get("thinking_token_budget")), (16384, 8192))
        self.assertLess(lt["keys"]["thinking_token_budget"], lt["keys"]["max_tokens"])               # 예산 < 상한
        self.assertEqual((lt["system"], lt["tools"]), (l0["system"], l0["tools"]))


class TEmptyAsLargo(Base):
    """판 152: '기본' 칸 없음 — 등급 없음('')은 화면에 largo 로 보이고, 표에 [tiers.largo] 칸이 있으면 그 칸을 따른다(허브 메인도)."""
    table = MODERATO + '\n[tiers.largo]\nthinking_token_budget = 8192\nthink_max_tokens = 16384\n'

    def test_empty_follows_largo_row(self):
        h = Hub(self.root, self.env)
        self.addCleanup(h.close)
        t = h.get("/api/tiers")
        self.assertEqual((t.get("new_default"), t.get("empty_as")), ("largo", "largo"))
        self.assertNotIn("", [r["tier"] for r in t["rows"]])
        main = self.hub_first(h, main=True)
        largo = self.hub_first(h, "largo")
        none = self.hub_first(h)
        self.assertEqual(none, largo)                                                               # '' = largo 칸(예산·상한까지)
        self.assertEqual(main, largo)                                                               # 허브 메인(--tier 없이)도
        self.assertEqual((largo["keys"]["max_tokens"], largo["keys"].get("thinking_token_budget")), (16384, 8192))
        rows = h.get("/api/state")["chats"]
        self.assertEqual({c["tier"] for c in rows}, {"largo"})                                      # 화면 표시: 등급 없음 = largo
        l0, r = self.cli_first()
        self.assertEqual(l0["keys"]["max_tokens"], 8192, r.stderr[-400:])                           # `chat --once`(--tier 없이)는 표 칸을 따르지 않는다(측정 L0)


class TWorkers(Base):
    table = MODERATO

    def route(self, b):
        if not b.get("tools"):
            return msg("")
        if is_worker(b):
            return msg("했다")
        n = sum(1 for x in b["messages"] if x.get("role") == "tool")
        if n == 0:
            return msg(calls=[("workers", {"action": "spawn", "task": "w.txt 에 써라", "tools": ["write_file"]}),
                              ("workers", {"action": "spawn", "task": "README 읽어라"})])
        return msg(calls=[("workers", {"action": "wait"})]) if n == 2 else msg("받았다")

    def test_worker_effort(self):
        h = Hub(self.root, self.env)
        self.addCleanup(h.close)
        h.post("/api/main", {"text": "맡겨라"})
        self.assertTrue(h.wait(lambda: not h.get("/api/state")["busy"], 60))
        ws = {("write" if "write_file" in [t["function"]["name"] for t in b["tools"]] else "read"): b.get("reasoning_effort")
              for b in self.model.requests if is_worker(b)}
        self.assertEqual(ws, {"write": "medium", "read": None})


class TCliWorkerWrite(Base):
    """판 151: 실사용 꼴(hub.toml [forge])에서 `chat --once --worker write -p chat` — 작업자가 못 만드는 도구(forge_*·workers)를
    넘기다 '모르는 도구: forge_remote' 로 첫 걸음 전에 끝나던 것."""
    table = MODERATO

    def route(self, b):
        return msg("했다") if is_worker(b) else msg("")

    def test_write_worker_runs(self):
        r = subprocess.run([sys.executable, "-m", "stewart", "chat", "--once", "--mode", "edits", "--worker", "write", "-p", "chat",
                            "--backend", "cloud", "--workdir", str(self.root / "project"), TASK],
                           env=self.env, cwd=str(self.root), capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        ws = [b for b in self.model.requests if is_worker(b)]
        self.assertTrue(ws)
        names = {t["function"]["name"] for t in ws[0]["tools"]}
        self.assertIn("write_file", names)
        self.assertFalse(names & {"forge_remote", "forge_share", "workers", "delegate"}, names)


if __name__ == "__main__":
    unittest.main()
