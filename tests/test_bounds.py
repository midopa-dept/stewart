"""작업 폴더 경계: 밖 쓰기는 run 거부·chat 물음(그 경로만 승인), 읽기는 read_outside, 셸의 흔한 쓰기 꼴, 우회(링크·..·~·대소문자)."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import bounds as BND, chat as C, engine
from stewart.config import Backend, ConfigError, Profile, load_profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI

_N = [0]


def r(content=None, calls=None):
    msg = {"role": "assistant", "content": content}
    if calls:
        _N[0] += 1
        msg["tool_calls"] = [{"id": f"b{_N[0]}-{i}", "type": "function",
                              "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}} for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": msg, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, 0.1


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.wd = self.tmp / "work"
        self.out = self.tmp / "outside"
        self.wd.mkdir()
        self.out.mkdir()
        (self.out / "f.txt").write_text("밖 파일")
        (self.wd / "a.txt").write_text("안 파일")
        os.symlink(self.out, self.wd / "link")                  # 작업 폴더 안 링크가 밖을 가리킨다
        p = mock.patch.object(BND, "temp_dirs", lambda: [])     # 시험 폴더가 임시 폴더라 임시 허용을 끈다
        p.start()
        self.addCleanup(p.stop)

    def b(self, **k):
        return BND.Bounds(self.wd, **k)


class TJudge(Base):
    def test_write_paths(self):
        b = self.b()
        self.assertIsNone(b.write_outside("a.txt"))
        self.assertIsNone(b.write_outside("new/dir/x.txt"))                     # 아직 없는 안쪽 경로
        self.assertIsNone(b.write_outside(str(self.wd / "b.txt")))
        self.assertIsNone(b.write_outside("/dev/null"))
        out = str(self.out / "x.txt")
        for p in ("../outside/x.txt", str(self.out / "x.txt"), "link/x.txt", "sub/../../outside/x.txt"):
            self.assertEqual(b.write_outside(p), out, p)
        # 링크를 지난 뒤의 .. 는 링크 대상의 위로 간다(운영체제와 같게) → 작업 폴더 밖
        self.assertEqual(b.write_outside("./link/../work-twin/x.txt"), str(self.tmp / "work-twin" / "x.txt"))
        with mock.patch.dict(os.environ, {"HOME": str(self.out)}):
            self.assertEqual(b.write_outside("~/x.txt"), out)                   # ~ 를 펼쳐 본다

    def test_case_variant(self):
        up = Path(str(self.wd).replace("/work", "/WORK"))
        b = self.b()
        if up.exists():   # 대소문자를 가리지 않는 볼륨(macOS 기본): 같은 폴더 → 안
            self.assertIsNone(b.write_outside(str(up / "x.txt")))
        else:             # 가리는 볼륨: 다른 폴더(없음) → 밖
            self.assertIsNotNone(b.write_outside(str(up / "x.txt")))
        (self.tmp / "Work2").mkdir()
        self.assertIsNotNone(b.write_outside(str(self.tmp / "Work2" / "x")))   # 이름이 비슷한 다른 폴더는 밖

    def test_extra_write_dirs_and_approval(self):
        b = self.b(extra_write_dirs=[str(self.out)])
        self.assertIsNone(b.write_outside("../outside/x.txt"))
        b = self.b(extra_write_dirs=["../outside"])                              # 작업 폴더 기준 상대 경로
        self.assertIsNone(b.write_outside(str(self.out / "deep" / "y")))
        b = self.b()
        b.approved.add(str(self.out / "x.txt"))
        self.assertIsNone(b.write_outside("../outside/x.txt"))                  # 승인한 그 경로만
        self.assertIsNotNone(b.write_outside("../outside/y.txt"))

    def test_shell_forms(self):
        b = self.b()
        o = lambda name: str(self.out / name)
        cases = {
            "echo hi > ../outside/f1": [o("f1")],
            "echo hi >> " + o("f2"): [o("f2")],
            "rm -rf ../outside/f.txt": [o("f.txt")],
            "cp a.txt ../outside/c": [o("c")],
            "mv ../outside/f.txt b.txt": [o("f.txt")],                          # 옮기면 원래 자리도 바뀐다
            "echo x | tee -a ../outside/log": [o("log")],
            "sed -i 's/a/b/' ../outside/f.txt": [o("f.txt")],
            "sed -i.bak -e s/a/b/ link/f.txt": [o("f.txt")],
            "dd if=a.txt of=../outside/d": [o("d")],
            "cd ../outside && touch x": [o("x")],
            "chmod 600 ../outside/f.txt": [o("f.txt")],
            "echo a 2>&1 >/dev/null": [],
            "cat ../outside/f.txt": [],                                          # 읽기는 셸 쓰기 꼴이 아니다
            "ls; touch ok.txt; mkdir -p d/e": [],
            "cd .. && rm work/a.txt": [],                                        # 다시 안으로
            "rm $HOME/x": [],                                                    # 변수는 못 본다(한계)
            "printf 'a\\n' > ok.txt\ntouch ../outside/n2": [o("n2")],          # 여러 줄
        }
        for cmd, want in cases.items():
            self.assertEqual(b.shell_outside(cmd), want, cmd)
        self.assertEqual(b.shell_outside("touch x", cwd=str(self.out)), [o("x")])   # 셸의 지금 위치 기준

    def test_config(self):
        tmp = Path(tempfile.mkdtemp())
        for bad in ('extra_write_dirs = "x"', 'extra_write_dirs = [""]', 'read_outside = "maybe"'):
            (tmp / "x.toml").write_text(f'system = "s"\n{bad}\n', encoding="utf-8")
            with self.assertRaises(ConfigError, msg=bad):
                load_profile(str(tmp / "x.toml"))
        (tmp / "x.toml").write_text('system = "s"\nextra_write_dirs = ["~/notes"]\nread_outside = "ask"\n', encoding="utf-8")
        p = load_profile(str(tmp / "x.toml"))
        self.assertEqual((p.extra_write_dirs, p.read_outside), (["~/notes"], "ask"))
        self.assertEqual(load_profile("code").read_outside, "allow")


class TRun(Base):
    def go(self, seq, **pk):
        p = Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["read_file", "write_file", "run_shell"],
                    workdir=self.wd, max_steps=6, **pk)
        sent = []

        def fake(backend, messages, *a, **k):
            sent.append(json.loads(json.dumps(messages)))
            return seq[len(sent) - 1]

        with mock.patch.object(engine, "chat", side_effect=fake):
            s = engine.run(p, Backend("fake", "http://x/v1", "m", "q", 100000), "일해", Recorder(self.tmp / "runs", "t"))
        return s, [m["content"] for m in sent[-1] if m["role"] == "tool"]

    def test_run_denies_outside_write_and_shell(self):
        s, tools = self.go([r(calls=[("write_file", {"path": "../outside/new.txt", "content": "x"})]),
                            r(calls=[("run_shell", {"command": "touch link/sh.txt"})]),
                            r(calls=[("write_file", {"path": "in.txt", "content": "x"})]), r("끝")])
        self.assertFalse((self.out / "new.txt").exists())
        self.assertFalse((self.out / "sh.txt").exists())                        # 셸도 돌지 않았다
        self.assertTrue((self.wd / "in.txt").exists())
        self.assertIn("작업 폴더 밖", tools[0])
        self.assertIn(str(self.out / "sh.txt"), tools[1])
        self.assertEqual(s["tool_error_kinds"].get("denied"), 2)

    def test_run_read_outside(self):
        s, tools = self.go([r(calls=[("read_file", {"path": "../outside/f.txt"})]), r("끝")])
        self.assertIn("밖 파일", tools[0])                                       # 기본 allow
        s, tools = self.go([r(calls=[("read_file", {"path": "link/f.txt"})]), r("끝")], read_outside="ask")
        self.assertIn("읽지 않는다", tools[0])                                    # run 에서 ask 는 거부
        s, tools = self.go([r(calls=[("read_file", {"path": "a.txt"})]), r("끝")], read_outside="deny")
        self.assertIn("안 파일", tools[0])                                       # 안은 그대로


class TChat(Base):
    def sess(self, answers, **pk):
        ui = FakeUI(answers)
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["read_file", "write_file", "run_shell"],
                    workdir=self.wd, max_steps=6, **pk)
        s = C.ChatSession(p, Backend("fake", "http://x/v1", "m", "q", 100000), Recorder(self.tmp / "runs", "chat-t"), ui,
                          stream=False, mode="auto")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        return s, ui

    def test_ask_even_in_auto_and_scope(self):
        s, ui = self.sess(["y", ""])
        seq = [r(calls=[("write_file", {"path": "../outside/o.txt", "content": "1"})]),
               r(calls=[("write_file", {"path": "../outside/o.txt", "content": "2"})]),   # 같은 경로: 다시 묻지 않음
               r(calls=[("write_file", {"path": "../outside/p.txt", "content": "3"})]),   # 다른 경로: 묻고 빈 입력 = 거부
               r("끝")]
        with mock.patch.object(C, "chat", side_effect=seq):
            s.turn("써")
        self.assertEqual((self.out / "o.txt").read_text(), "2")
        self.assertFalse((self.out / "p.txt").exists())
        self.assertEqual(ui.answers, [])                                        # 질문은 두 번뿐
        s.rec._ev.flush()
        ev = [json.loads(l) for l in (s.rec.dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual([e["paths"] for e in ev if e["kind"] == "outside_approved"], [[str(self.out / "o.txt")]])

    def test_chat_shell_and_read(self):
        s, ui = self.sess(["n", "y"], read_outside="ask")
        seq = [r(calls=[("run_shell", {"command": "echo x > ../outside/s.txt"})]),
               r(calls=[("read_file", {"path": "../outside/f.txt"})]), r("끝")]
        with mock.patch.object(C, "chat", side_effect=seq):
            s.turn("해")
        self.assertFalse((self.out / "s.txt").exists())
        tool = [m["content"] for m in s.messages if m.get("role") == "tool"]
        self.assertIn("거부", tool[0])
        self.assertIn("밖 파일", tool[1])                                        # 읽기 물음에 y


if __name__ == "__main__":
    unittest.main()
