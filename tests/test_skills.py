"""스킬: 찾기(사용자·프로젝트), 앞머리, 목록 상한, load_skill(폴더 밖·비밀·큰 파일 거부), run·chat 연결, 끄기."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, engine, modes as M, skills as SK
from stewart.config import Backend, Profile, load_profile
from stewart.record import Recorder
from tests.test_cloud import FakeUI
from tests.test_engine import _resp


def mk(root: Path, name: str, desc="설명", body="본문", extra="", files=None):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n{extra}---\n{body}\n")
    for k, v in (files or {}).items():
        (d / k).parent.mkdir(parents=True, exist_ok=True)
        (d / k).write_text(v)
    return d


class TSkillsUnit(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.user = self.tmp / "user-skills"
        self.user.mkdir()
        p = mock.patch.dict(os.environ, {"STEWART_SKILLS_DIR": str(self.user)})
        p.start()
        self.addCleanup(p.stop)
        self.repo = self.tmp / "repo"
        (self.repo / "sub").mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)

    def test_parse_and_ignored_keys(self):
        mk(self.user, "a", extra="allowed-tools: run_shell, write_file\nmodel: big\n")
        (self.user / "nodesc").mkdir()
        (self.user / "nodesc" / "SKILL.md").write_text("---\nname: nodesc\n---\n본문\n")
        (self.user / "nofront").mkdir()
        (self.user / "nofront" / "SKILL.md").write_text("그냥 글\n")
        ss = SK.discover(self.repo)
        self.assertEqual([s.name for s in ss], ["a"])          # 설명 없는 것·앞머리 없는 것은 뺀다
        self.assertEqual(ss[0].ignored_keys, ["allowed-tools", "model"])
        self.assertEqual(ss[0].body, "본문")
        self.assertEqual(SK.meta_log(ss)[0]["ignored_keys"], ["allowed-tools", "model"])

    def test_user_and_project_override(self):
        mk(self.user, "fmt", desc="사용자 것")
        mk(self.user, "only-user")
        mk(self.repo / ".stewart" / "skills", "fmt", desc="뿌리 것")
        mk(self.repo / "sub" / ".stewart" / "skills", "fmt", desc="작업 폴더 것")
        ss = {s.name: s for s in SK.discover(self.repo / "sub")}
        self.assertEqual((ss["fmt"].description, ss["fmt"].scope), ("작업 폴더 것", "project"))
        self.assertEqual(ss["only-user"].scope, "user")
        outside = self.tmp / "plain"
        outside.mkdir()
        mk(self.tmp / ".stewart" / "skills", "up")               # git 밖이면 작업 폴더 위로 올라가지 않는다
        self.assertNotIn("up", {s.name for s in SK.discover(outside)})

    def test_list_caps(self):
        for i in range(40):
            mk(self.user, f"s{i:02d}", desc="가" * 500)
        ss = SK.discover(self.repo)
        self.assertEqual(len(ss), SK.MAX_SKILLS)
        t = SK.list_text(ss)
        self.assertLessEqual(len(t), SK.MAX_LIST_CHARS + 40)
        self.assertIn("생략", t)
        self.assertIn("가" * SK.MAX_DESC + "…", t)
        self.assertEqual(SK.list_text([]), "")

    def test_load_and_refusals(self):
        d = mk(self.user, "doc", files={"ref/guide.md": "안내", ".env": "KEY=1", "big.txt": "x" * (SK.MAX_FILE_BYTES + 1)})
        secret_out = self.tmp / "outside.txt"
        secret_out.write_text("밖")
        os.symlink(secret_out, d / "link.txt")
        os.symlink(self.tmp, d / "updir")
        ss = SK.discover(self.repo)
        ok, body = SK.load(ss, "doc")
        self.assertTrue(ok)
        self.assertIn("본문", body)
        self.assertIn("ref/guide.md", body)
        self.assertNotIn(str(d), body)                           # 스킬 폴더 경로를 보이지 않는다
        self.assertIn("작업 폴더에 둔다", body)
        self.assertNotIn(".env", body)                           # 목록에도 비밀 파일은 안 보인다
        self.assertNotIn("link.txt", body)
        self.assertEqual(SK.load(ss, "doc", "ref/guide.md"), (True, "[스킬 doc 의 ref/guide.md]\n안내"))
        for f, word in (("../outside.txt", ".."), (str(secret_out), "절대"), ("link.txt", "밖을"), ("updir/outside.txt", "밖을"),
                        (".env", "비밀"), ("big.txt", "너무 크다"), ("없음.md", "없는")):
            ok, msg = SK.load(ss, "doc", f)
            self.assertFalse(ok, f)
            self.assertIn(word, msg, f)
        self.assertFalse(SK.load(ss, "없는스킬")[0])

    def test_profile_key_and_mode(self):
        p = self.tmp / "p.toml"
        p.write_text('name = "x"\nsystem = "s"\ntools = ["read_file"]\nskills = false\n')
        self.assertIs(load_profile(str(p)).skills, False)
        self.assertEqual(M.decide("plan", "load_skill"), "run")


class TSkillsRun(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.user = self.tmp / "us"
        self.user.mkdir()
        p = mock.patch.dict(os.environ, {"STEWART_SKILLS_DIR": str(self.user)})
        p.start()
        self.addCleanup(p.stop)
        self.wd = self.tmp / "wd"
        self.wd.mkdir()
        self.bk = Backend("fake", "http://x/v1", "m", "q", 4096)

    def prof(self, **kw):
        return Profile(name="code", path=self.tmp / "p.toml", system="s", tools=["read_file"], workdir=self.wd, max_steps=5, **kw)

    def go(self, seq, **kw):
        rec = Recorder(self.tmp / "runs", "t")
        with mock.patch.object(engine, "chat", side_effect=seq) as m:
            s = engine.run(self.prof(**kw), self.bk, "해라", rec)
        return s, m, rec

    def test_run_lists_and_loads(self):
        mk(self.user, "release-notes", desc="변경 기록을 쓰는 방법", body="머리에 날짜를 쓴다")
        s, m, rec = self.go([_resp(calls=[("load_skill", '{"name": "release-notes"}')]), _resp("다 했다")])
        sysm = m.call_args_list[0][0][1][0]["content"]
        self.assertIn("- release-notes: 변경 기록을 쓰는 방법", sysm)
        self.assertIn("load_skill", [d["function"]["name"] for d in m.call_args_list[0][0][2]])
        tool_msg = [x for x in m.call_args_list[-1][0][1] if x["role"] == "tool"][0]["content"]
        self.assertIn("머리에 날짜를 쓴다", tool_msg)
        self.assertEqual(s["skills_loaded"], ["release-notes"])
        ev = [json.loads(l) for l in (rec.dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual([e["name"] for e in ev if e["kind"] == "skill_load"], ["release-notes"])
        self.assertEqual(ev[0]["meta"]["skills"][0]["name"], "release-notes")

    def test_off_and_empty(self):
        mk(self.user, "a")
        s, m, _ = self.go([_resp("끝")], skills=False)
        self.assertNotIn("load_skill", [d["function"]["name"] for d in m.call_args_list[0][0][2]])
        self.assertNotIn("쓸 수 있는 스킬", m.call_args_list[0][0][1][0]["content"])
        (self.user / "a" / "SKILL.md").unlink()
        s, m, _ = self.go([_resp("끝")])                          # 스킬이 없으면 도구도 목록도 없다
        self.assertNotIn("load_skill", [d["function"]["name"] for d in m.call_args_list[0][0][2]])

    def test_chat_ignored_keys_and_pick(self):
        mk(self.user, "fmt", desc="표 모양", body="표는 세 칸", extra="allowed-tools: run_shell\n")
        ui = FakeUI()
        p = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["read_file"], workdir=self.wd, max_steps=5)
        s = C.ChatSession(p, self.bk, Recorder(self.tmp / "runs", "chat-s"), ui, mode="edits")
        self.addCleanup(lambda: s.rec._ev.closed or s.rec._ev.close())
        out = ui.out.getvalue()
        self.assertIn("스킬 1개: fmt(사용자)", out)
        self.assertIn("무시한 키(스킬은 도구·모드·확인을 바꾸지 못한다): fmt: allowed-tools", out)
        self.assertEqual(s.mode, "edits")
        s.pick_skill("fmt")
        with self.assertRaises(ValueError):
            s.pick_skill("없음")
        with mock.patch.object(C, "chat", side_effect=[_resp(calls=[("load_skill", '{"name": "fmt"}')]), _resp("표")]) as m:
            s.turn("표로 정리해")
            first_user = [x for x in m.call_args_list[0][0][1] if x["role"] == "user"][-1]["content"]
        from stewart.envinfo import strip_user_note
        self.assertTrue(strip_user_note(first_user).startswith("[사람이 스킬 fmt 을 골랐다"))
        self.assertIn("표는 세 칸", first_user)
        self.assertEqual(s.summary()["skills_loaded"], ["fmt", "fmt"])


if __name__ == "__main__":
    unittest.main()
