"""기억 M1 — L0 색인·memory_search, L1 엔진 자동 메모·note_add(≤10), L3 MEMORY.md 주입(40줄·4 KB·합 6 KB), 기본 끔."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, engine, memory as MEM, nostore as NS
from stewart.config import Backend, Profile, load_profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI

_N = [0]


def r(content=None, calls=None):
    msg = {"role": "assistant", "content": content}
    if calls:
        _N[0] += 1
        msg["tool_calls"] = [{"id": f"m{_N[0]}-{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
                             for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": msg, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, 0.1


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.wd = self.tmp / "work"
        self.wd.mkdir()
        self.mdir = self.tmp / "mem"
        self.env = mock.patch.dict(os.environ, {MEM.ENV_DIR: str(self.mdir)}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop(MEM.ENV_ON, None)

    def prof(self, **k):
        d = dict(name="code", path=self.tmp / "p.toml", system="s", tools=["read_file", "write_file", "run_shell"],
                 workdir=self.wd, max_steps=8, memory=True)
        return Profile(**{**d, **k})

    def go(self, seq, prof=None, task="일해"):
        sent, tools = [], []

        def fake(backend, messages, tool_defs=None, *a, **k):
            sent.append(json.loads(json.dumps(messages)))
            tools.append([d["function"]["name"] for d in tool_defs or []])
            return seq[len(sent) - 1]

        rec = Recorder(self.tmp / "runs", "t")
        with mock.patch.object(engine, "chat", side_effect=fake):
            s = engine.run(prof or self.prof(), Backend("fake", "http://x/v1", "m", "q", 100000), task, rec)
        return s, sent, tools, rec


class TSwitch(Base):
    def test_enabled_rules(self):
        p = self.prof(memory=None)
        self.assertFalse(MEM.enabled(p, {}))
        self.assertTrue(MEM.enabled(self.prof(), {}))
        self.assertFalse(MEM.enabled(self.prof(), {MEM.ENV_ON: "0"}))       # env 0 이 이긴다
        self.assertTrue(MEM.enabled(p, {MEM.ENV_ON: "1"}))
        for name in ("code", "chat", "organize"):
            self.assertIsNone(load_profile(name).memory)                    # 내장 프로필은 끔(평가 기본 끔)

    def test_default_off_no_tools_no_inject(self):
        s, sent, tools, rec = self.go([r("끝")], prof=self.prof(memory=None))
        self.assertNotIn("[기억]", sent[0][0]["content"])
        self.assertFalse(set(MEM.NAMES) & set(tools[0]))
        self.assertNotIn("memory", s)
        self.assertFalse((self.tmp / "runs" / "index.jsonl").exists())
        self.assertFalse(self.mdir.exists())

    def test_subjects(self):
        a = MEM.subjects(self.prof())
        self.assertEqual([x["kind"] for x in a], ["dir", "kind"])
        self.assertTrue(a[0]["key"].startswith("dir-work-") and a[1]["key"] == "kind-code")
        subprocess.run(["git", "init", "-q", str(self.wd)], check=True)
        sub = self.wd / "pkg"
        sub.mkdir()
        b = MEM.subjects(self.prof(workdir=sub))
        self.assertTrue(b[0]["key"].startswith("repo-work-"))                # 저장소 안이면 뿌리가 대상
        self.assertEqual(b[0]["path"], str(self.wd.resolve()))


class TInject(Base):
    def test_caps(self):
        m = MEM.Memory(self.prof(), self.tmp / "runs" / "r1")
        place, kind = m.subjects
        (self.mdir / place["key"]).mkdir(parents=True)
        (self.mdir / place["key"] / "MEMORY.md").write_text("\n".join(f"- 줄 {i}" for i in range(60)), encoding="utf-8")
        (self.mdir / kind["key"]).mkdir(parents=True)
        (self.mdir / kind["key"] / "MEMORY.md").write_text("\n".join("가" * 120 for _ in range(30)), encoding="utf-8")
        text, log = m.inject()
        self.assertTrue(text.startswith(MEM.PURPOSE))
        self.assertEqual((log[0]["lines"], log[0]["truncated"]), (40, True))     # 40줄
        self.assertIn("- 줄 39", text)
        self.assertNotIn("- 줄 40", text)
        self.assertLessEqual(log[1]["bytes"], MEM.INJECT_BYTES)
        self.assertLessEqual(log[0]["bytes"] + log[1]["bytes"], MEM.INJECT_TOTAL)   # 합 6 KB
        self.assertTrue(log[1]["truncated"])
        self.assertIn("나머지는 memory_search", text)

    def test_empty(self):
        text, log = MEM.Memory(self.prof(), self.tmp / "runs" / "r1").inject()
        self.assertIn("(아직 색인이 없다.)", text)
        self.assertEqual(log, [])


class TRun(Base):
    def test_notes_index_search_across_runs(self):
        (self.wd / "test_a.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertTrue(False)\n")
        p = self.prof(verify_command=f"{sys.executable} -m unittest -q")
        s1, sent, tools, rec1 = self.go([
            r(calls=[("note_add", {"claim": "빌드는 make 가 아니라 python -m build 로 한다", "evidence": "README 7행 읽음",
                                   "status": "confirmed"})]),
            r(calls=[("note_add", {"claim": "x", "evidence": "", "status": "confirmed"})]),
            r(calls=[("run_shell", {"command": "false"})]), r("끝")], prof=p, task="첫 과제: 빌드 고치기")
        self.assertIn("memory_search", tools[0])
        self.assertIn("[기억]", sent[0][0]["content"])
        self.assertEqual((s1["memory"]["notes_model"], s1["memory"]["notes_engine"] >= 2), (1, True))
        tool_msgs = [m["content"] for m in sent[2] if m["role"] == "tool"]
        self.assertIn("evidence 가 비었다", tool_msgs[-1])                  # 근거 없는 메모는 거절
        key = MEM.subjects(p)[0]["key"]
        notes = [json.loads(l) for l in (self.mdir / key / "notes" / f"{rec1.dir.name}.jsonl").read_text().splitlines()]
        by = [(n["by"], n["status"]) for n in notes]
        self.assertIn(("model", "confirmed"), by)
        self.assertTrue(any(n["by"] == "engine" and "판정 명령" in n["claim"] for n in notes))
        self.assertTrue(any(n["by"] == "engine" and "`false` → exit 1" in n["claim"] for n in notes))
        idx = [json.loads(l) for l in (self.tmp / "runs" / "index.jsonl").read_text().splitlines()]
        self.assertEqual((idx[0]["run"], idx[0]["task"], idx[0]["subjects"][0]), (rec1.dir.name, "첫 과제: 빌드 고치기", key))
        # 두 번째 실행: 앞 실행의 메모·기록을 찾는다
        s2, sent2, _, _ = self.go([r(calls=[("memory_search", {"query": "python -m build"})]), r("끝")], prof=p)
        found = [m["content"] for m in sent2[1] if m["role"] == "tool"][-1]
        self.assertIn("빌드는 make 가 아니라 python -m build", found)
        self.assertIn("앞 실행 1개", found)

    def test_note_cap_and_scrub(self):
        calls = [r(calls=[("note_add", {"claim": f"사실 {i} " + (NS.wrap("브레이브글") if i == 0 else ""),
                                        "evidence": "e", "status": "unconfirmed"})]) for i in range(11)]
        s, sent, _, rec = self.go(calls + [r("끝")], prof=self.prof(max_steps=14))
        last = [m["content"] for m in sent[11] if m["role"] == "tool"][-1]
        self.assertIn("메모 상한(10개)", last)
        self.assertEqual(s["memory"]["notes_model"], 10)
        raw = (self.mdir / MEM.subjects(self.prof())[0]["key"] / "notes" / f"{rec.dir.name}.jsonl").read_text()
        self.assertNotIn("브레이브글", raw)                                    # Brave 표지 구간은 메모에도 안 남는다

    def test_env_off_wins(self):
        os.environ[MEM.ENV_ON] = "0"
        s, sent, tools, _ = self.go([r("끝")])
        self.assertFalse(set(MEM.NAMES) & set(tools[0]))
        self.assertNotIn("memory", s)


class TChat(Base):
    def test_chat_tools_and_index(self):
        ui = FakeUI()
        sess = C.ChatSession(self.prof(name="chat"), Backend("fake", "http://x/v1", "m", "q", 100000),
                             Recorder(self.tmp / "runs", "chat-t"), ui, stream=False, mode="auto")
        self.assertIn("note_add", sess.tool_names)
        self.assertIn("[기억]", sess.messages[0]["content"])
        with mock.patch.object(C, "chat", side_effect=[r(calls=[("note_add", {"claim": "c", "evidence": "e", "status": "trap"})]),
                                                       r("끝")]):
            sess.turn("안녕")
        s = sess.close()
        self.assertEqual(s["memory"]["notes_model"], 1)
        idx = [json.loads(l) for l in (self.tmp / "runs" / "index.jsonl").read_text().splitlines()]
        self.assertEqual(idx[-1]["task"].endswith("안녕"), True)


if __name__ == "__main__":
    unittest.main()
