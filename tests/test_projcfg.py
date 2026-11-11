"""프로젝트 구성: 정본(AGENTS.md·STEWART.md·.stewart/·~/.config/stewart) + Claude Code 꼴 호환(CLAUDE.md·.claude/)."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import chat as C, delegate as DG, engine, memfile, projcfg as PC, skills as SK
from stewart.config import Backend, ConfigError, Profile, load_profile
from stewart.record import Recorder
from tests.test_engine import _resp

ON = PC.Flags(compat=True)
ALL = PC.Flags(compat=True, user=True)
OFF = PC.Flags()


def w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.chome = self.tmp / "claude-home"
        self.udir = self.tmp / "user-stewart"
        self.repo = self.tmp / "repo"
        (self.repo / ".git").mkdir(parents=True)
        self.wd = self.repo / "sub"
        self.wd.mkdir()
        e = mock.patch.dict(os.environ, {PC.HOME_ENV: str(self.chome), PC.USER_DIR_ENV: str(self.udir),
                                         "STEWART_SKILLS_DIR": str(self.udir / "skills"), "HOME": str(self.tmp / "home")})
        e.start()
        self.addCleanup(e.stop)
        for k in (PC.ENV, PC.USER_ENV):
            os.environ.pop(k, None)

    def prof(self, **k):
        return Profile(**{"name": "t", "path": self.wd / "p.toml", "system": "s", "tools": ["*"], "workdir": self.wd,
                          "max_steps": 6, **k})


class TFlags(Base):
    def test_defaults_and_overrides(self):
        p = self.prof()
        self.assertEqual((PC.compat_on(p, True), PC.compat_on(p, False)), (True, False))     # chat·허브 켬, run 끔
        self.assertFalse(PC.compat_user_on(p, True))                                        # ~/.claude 는 기본 끔
        self.assertTrue(PC.compat_on(self.prof(claude_compat=True), False))
        self.assertFalse(PC.compat_on(self.prof(claude_compat=False), True))
        self.assertTrue(PC.compat_user_on(self.prof(claude_compat_user=True), True))
        self.assertFalse(PC.compat_user_on(self.prof(claude_compat_user=True, claude_compat=False), True))
        with mock.patch.dict(os.environ, {PC.ENV: "0"}):
            self.assertFalse(PC.compat_on(self.prof(claude_compat=True), True))           # 평가 대기열의 명시적 끔
        with mock.patch.dict(os.environ, {PC.ENV: "1", PC.USER_ENV: "1"}):
            self.assertEqual(PC.flags(p, False), ALL)

    def test_profile_keys(self):
        w(self.tmp / "p.toml", 'system = "s"\nclaude_compat = false\nclaude_compat_user = true\n')
        p = load_profile(str(self.tmp / "p.toml"))
        self.assertEqual((p.claude_compat, p.claude_compat_user), (False, True))
        w(self.tmp / "q.toml", 'system = "s"\nclaude_compat = "yes"\n')
        with self.assertRaises(ConfigError):
            load_profile(str(self.tmp / "q.toml"))


class TMemory(Base):
    def test_canonical_order_and_headers(self):
        w(self.udir / "STEWART.md", "사용자 stewart")
        w(self.repo / "AGENTS.md", "뿌리 AGENTS")
        w(self.repo / "STEWART.md", "뿌리 STEWART")
        w(self.wd / "AGENTS.md", "아래 AGENTS")
        text, log = memfile.collect(self.wd, fl=OFF)
        order = [text.index(s) for s in ("사용자 stewart", "뿌리 AGENTS", "뿌리 STEWART", "아래 AGENTS")]
        self.assertEqual(order, sorted(order))                              # 위 → 아래, 한 폴더 안은 AGENTS → STEWART
        for f in (self.udir / "STEWART.md", self.repo / "AGENTS.md", self.repo / "STEWART.md"):
            self.assertIn(f"### {f}\n", text)

    def test_claude_only_where_no_agents(self):
        w(self.repo / "AGENTS.md", "뿌리 AGENTS")
        w(self.repo / "CLAUDE.md", "뿌리 CLAUDE(읽지 않음)")                 # 같은 폴더에 AGENTS.md 가 있음
        w(self.wd / "CLAUDE.md", "아래 CLAUDE")                              # 이 폴더엔 AGENTS.md 가 없음
        w(self.wd / "STEWART.md", "아래 STEWART")
        text, _ = memfile.collect(self.wd, fl=ON)
        self.assertNotIn("뿌리 CLAUDE", text)
        self.assertLess(text.index("아래 CLAUDE"), text.index("아래 STEWART"))   # STEWART.md 가 뒤(이김)
        t0, _ = memfile.collect(self.wd, fl=OFF)                            # claude_compat=false
        self.assertNotIn("CLAUDE", t0)

    def test_claude_duplicate_skipped(self):
        w(self.repo / "AGENTS.md", "같은 규칙")
        w(self.wd / "CLAUDE.md", "같은 규칙")
        d2 = self.wd / "deeper"
        w(d2 / "CLAUDE.md", "@AGENTS.md")
        text, log = memfile.collect(d2, fl=ON)
        self.assertEqual(text.count("같은 규칙"), 1)
        self.assertEqual(sorted(x["path"] for x in log if x.get("skipped") == "same_as_agents"),
                         sorted([str(self.wd / "CLAUDE.md"), str(d2 / "CLAUDE.md")]))

    def test_user_claude_needs_user_flag(self):
        w(self.chome / "CLAUDE.md", "Claude Code 세션 규칙")
        self.assertNotIn("Claude Code 세션 규칙", memfile.collect(self.wd, fl=ON)[0])   # 호환을 켜도 기본은 안 읽음
        self.assertIn("Claude Code 세션 규칙", memfile.collect(self.wd, fl=ALL)[0])

    def test_imports_in_all_three(self):
        w(self.repo / "docs" / "style.md", "문체 규칙 @deeper.md")
        w(self.repo / "docs" / "deeper.md", "더 깊은 규칙")
        w(self.repo / "docs" / "s.md", "stewart 덧붙임")
        w(self.repo / ".env", "TOKEN=zz")
        w(self.tmp / "outside.md", "밖의 글")
        w(self.repo / "a.md", "@b.md")
        w(self.repo / "b.md", "@a.md")
        w(self.repo / "AGENTS.md", "\n".join([
            "문체: @docs/style.md", "밖: @../outside.md", "비밀: @.env", "없음: @nope.md",
            "되돌기: @a.md", "메일 x@y.com 과 @사람 은 가져오기가 아니다", "`@docs/style.md` 칸 안",
            "```", "@docs/style.md", "```"]))
        w(self.repo / "STEWART.md", "@docs/s.md")
        w(self.wd / "CLAUDE.md", "@../docs/deeper.md")
        w(self.udir / "STEWART.md", f"@rules.md\n@{self.repo / 'docs' / 's.md'}")
        w(self.udir / "rules.md", "사용자 규칙")
        text, log = memfile.collect(self.wd, fl=ON)
        self.assertIn("[가져옴: " + str(self.repo / "docs" / "style.md") + "]", text)
        self.assertIn("더 깊은 규칙", text)
        self.assertIn("stewart 덧붙임", text)                                # STEWART.md 의 가져오기
        self.assertEqual(text.count("더 깊은 규칙"), 2)                       # CLAUDE.md 의 가져오기도
        self.assertIn("사용자 규칙", text)                                   # 전역 파일은 그 폴더 안만
        self.assertIn("(가져오지 않음: 허용 폴더 밖)", text.split("### " + str(self.udir / "STEWART.md"))[1].split("###")[0])
        self.assertNotIn("밖의 글", text)
        self.assertIn("@../outside.md (가져오지 않음: 허용 폴더 밖)", text)
        self.assertNotIn("zz", text)
        self.assertIn("@.env (가져오지 않음: 비밀 파일)", text)
        self.assertIn("@nope.md (가져오지 않음: 없는 파일)", text)
        self.assertIn("되돌아오는 가져오기", text)
        self.assertIn("메일 x@y.com 과 @사람", text)
        self.assertIn("`@docs/style.md` 칸 안", text)
        self.assertIn("```\n@docs/style.md\n```", text)

    def test_import_depth_limit(self):
        for i in range(8):
            w(self.repo / f"d{i}.md", f"층{i} @d{i + 1}.md")
        w(self.repo / "AGENTS.md", "@d0.md")
        text, _ = memfile.collect(self.wd, fl=OFF)
        self.assertIn("층4", text)
        self.assertNotIn("층5", text)
        self.assertIn("가져오기 깊이 상한", text)

    def test_broken_utf8(self):
        (self.repo / "AGENTS.md").write_bytes(b"\xff\xfe\x00 \xe2\x82 bad")
        _, log = memfile.collect(self.wd, fl=OFF)
        self.assertEqual(len(log), 1)


class TSkills(Base):
    def test_canonical_and_compat(self):
        w(self.udir / "skills" / "u1" / "SKILL.md", "---\nname: u1\ndescription: 사용자 스킬\n---\n본문")
        w(self.repo / ".stewart" / "skills" / "same" / "SKILL.md", "---\nname: same\ndescription: 정본\n---\n정본 본문")
        w(self.wd / ".claude" / "skills" / "same" / "SKILL.md", "---\nname: same\ndescription: 호환\n---\n호환 본문")
        w(self.repo / ".claude" / "skills" / "c1" / "SKILL.md", "---\nname: c1\ndescription: 클로드\nallowed-tools: Read\n---\n본문")
        w(self.repo / ".agents" / "skills" / "a1" / "SKILL.md", "---\nname: a1\ndescription: 공통\n---\n본문")
        w(self.chome / "skills" / "g1" / "SKILL.md", "---\nname: g1\ndescription: 전역 클로드\n---\n본문")
        w(self.repo / ".stewart" / "skills" / "bad" / "SKILL.md", "---\nname: bad\n닫는 줄 없음")
        found = {s.name: s for s in SK.discover(self.wd, ON)}
        self.assertEqual(set(found), {"u1", "same", "c1", "a1"})              # ~/.claude 는 기본으로 안 읽음
        self.assertEqual((found["same"].origin, found["same"].description), ("stewart", "정본"))   # 정본이 이김
        self.assertEqual((found["c1"].origin, found["c1"].ignored_keys), ("claude", ["allowed-tools"]))
        listing = SK.list_text(list(found.values()))
        self.assertIn("- c1: 클로드 [.claude]", listing)
        self.assertIn("- a1: 공통 [.agents]", listing)
        self.assertIn("- same: 정본\n", listing + "\n")
        self.assertNotIn("정본 본문", listing)
        self.assertEqual({s.name for s in SK.discover(self.wd, OFF)}, {"u1", "same", "a1"})
        self.assertIn("g1", {s.name for s in SK.discover(self.wd, ALL)})


class TCommandsAgents(Base):
    def test_commands(self):
        w(self.udir / "commands" / "hello.md", "---\ndescription: 인사\n---\n안녕 $ARGUMENTS")
        w(self.repo / ".stewart" / "commands" / "review.md", "---\ndescription: 검토\n---\n$1 를 $2 기준으로 검토")
        w(self.repo / ".claude" / "commands" / "review.md", "호환 검토(지면 안 됨)")
        w(self.repo / ".claude" / "commands" / "git" / "log.md", "최근 기록을 요약")
        w(self.repo / ".stewart" / "commands" / "help.md", "내장과 겹친다")
        w(self.repo / ".stewart" / "commands" / "empty.md", "---\ndescription: 빈 것\n---\n")
        cmds = {c.name: c for c in PC.discover_commands(self.wd, ON, set(C.COMMANDS))}
        self.assertEqual(set(cmds), {"/hello", "/review", "/git:log", "/help"})
        self.assertEqual(cmds["/review"].origin, "stewart")                  # 정본이 이김
        self.assertEqual(cmds["/git:log"].origin, "claude")
        self.assertTrue(cmds["/help"].shadowed)                              # 내장이 이김
        ex = lambda t: (PC.expand_command(list(cmds.values()), t) or (None, None))[1]
        self.assertEqual(ex("/hello 세상"), "안녕 세상")
        self.assertEqual(ex("/review a.py 보안"), "a.py 를 보안 기준으로 검토")
        self.assertEqual(ex("/git:log 오늘"), "최근 기록을 요약\n\n오늘")
        self.assertIsNone(ex("/help"))
        self.assertIsNone(ex("/nope"))
        self.assertIn("내장 명령과 겹쳐 쓰지 않음", PC.commands_help(list(cmds.values())))
        self.assertEqual({c.name for c in PC.discover_commands(self.wd, OFF, set(C.COMMANDS))}, {"/hello", "/review", "/help"})

    def test_agents(self):
        w(self.repo / ".stewart" / "agents" / "reviewer.md",
          "---\nname: reviewer\ndescription: 검토\ntools: read_file, search, Bash\nmodel: big\ncolor: blue\n---\n너는 검토자다.")
        w(self.repo / ".claude" / "agents" / "reviewer.md", "---\nname: reviewer\ndescription: 호환\n---\n지면 안 됨")
        w(self.repo / ".claude" / "agents" / "cr.md", "---\nname: cr\ndescription: 클로드 검토\ntools: Read, Grep, Glob, Task\n---\n클로드 검토자")
        w(self.repo / ".stewart" / "agents" / "nodesc.md", "---\nname: nodesc\n---\n본문")
        w(self.repo / ".stewart" / "agents" / "broken.md", "---\nname: broken\n")
        ag = {a.name: a for a in PC.discover_agents(self.wd, ON)}
        self.assertEqual(set(ag), {"reviewer", "cr"})
        r = ag["reviewer"]
        self.assertEqual((r.origin, r.tools, r.model, r.ignored_keys), ("stewart", ["read_file", "search", "run_shell"], "big", ["color"]))
        self.assertEqual((ag["cr"].tools, ag["cr"].unknown_tools), (["read_file", "search", "list_dir"], ["Task"]))
        base = Profile(name="p", path=self.wd / "p.toml", system="부모", tools=["read_file", "write_file", "search"], workdir=self.wd)
        prof = PC.agent_profile(r, base)
        self.assertEqual((prof.name, prof.system, prof.tools), ("agent:reviewer", "너는 검토자다.", ["read_file", "search"]))
        self.assertEqual({a.name for a in PC.discover_agents(self.wd, OFF)}, {"reviewer"})
        self.assertIn("cr: 클로드 검토 [.claude]", PC.agents_text(list(ag.values())))


class TEngineAndChat(Base):
    def run_(self, seq, env=None, **pk):
        sent = []

        def fake(backend, messages, tools=None, *a, **k):
            sent.append(json.dumps({"m": messages, "t": tools}, ensure_ascii=False, sort_keys=True))
            return seq[len(sent) - 1]

        with mock.patch.dict(os.environ, env or {}), mock.patch.object(engine, "chat", side_effect=fake):
            s = engine.run(self.prof(**pk), Backend("fake", "http://x/v1", "m", "q", 100000), "일",
                           Recorder(self.tmp / f"runs-{len(os.listdir(self.tmp))}", "t"))
        return s, sent

    def test_no_config_same_bytes(self):
        w(self.repo / "AGENTS.md", "기존 규칙")                              # 원래 읽던 파일만(가져오기 없음)
        _, a = self.run_([_resp("끝")])
        _, b = self.run_([_resp("끝")], {PC.ENV: "1"})
        _, c = self.run_([_resp("끝")], {PC.ENV: "0"})
        self.assertEqual(a, b)
        self.assertEqual(a, c)
        self.assertIn("기존 규칙", a[0])

    def test_run_default_off_chat_default_on(self):
        w(self.repo / "CLAUDE.md", "클로드 규칙")
        w(self.repo / ".claude" / "agents" / "rv.md", "---\nname: rv\ndescription: 검토\n---\n검토하라")
        _, a = self.run_([_resp("끝")])
        self.assertNotIn("클로드 규칙", a[0])                                 # run 은 호환 기본 끔
        self.assertNotIn("rv: 검토", a[0])
        _, b = self.run_([_resp("끝")], claude_compat=True)
        self.assertIn("클로드 규칙", b[0])
        self.assertIn("rv: 검토 [.claude]", b[0])
        sess = C.ChatSession(self.prof(), Backend("fake", "http://x/v1", "m", "q", 100000), Recorder(self.tmp / "rc", "chat-t"))
        self.addCleanup(sess.close)
        self.assertIn("클로드 규칙", sess.messages[0]["content"])             # chat 은 호환 기본 켬
        w(self.chome / "CLAUDE.md", "전역 Claude Code 규칙")
        s2 = C.ChatSession(self.prof(), Backend("fake", "http://x/v1", "m", "q", 100000), Recorder(self.tmp / "rc2", "chat-t"))
        self.addCleanup(s2.close)
        self.assertNotIn("전역 Claude Code 규칙", s2.messages[0]["content"])  # ~/.claude 는 기본 안 읽음
        s3 = C.ChatSession(self.prof(claude_compat_user=True), Backend("fake", "http://x/v1", "m", "q", 100000),
                           Recorder(self.tmp / "rc3", "chat-t"))
        self.addCleanup(s3.close)
        self.assertIn("전역 Claude Code 규칙", s3.messages[0]["content"])

    def test_delegate_kind_picks_agent(self):
        w(self.repo / ".stewart" / "agents" / "rv.md", "---\nname: rv\ndescription: 검토\ntools: read_file\n---\n너는 검토 작업자다.")
        w(self.repo / ".stewart" / "agents" / "code.md", "---\nname: code\ndescription: 겹치는 이름\n---\n쓰이면 안 된다")
        sent = []

        def fake(backend, messages, tools=None, *a, **k):
            sent.append({"m": messages, "t": [d["function"]["name"] for d in tools or []]})
            seq = [_resp(calls=[("delegate", json.dumps({"task": "봐라", "kind": "rv"}))]), _resp("검토함"), _resp("끝")]
            return seq[len(sent) - 1]

        with mock.patch.object(engine, "chat", side_effect=fake):
            engine.run(self.prof(), Backend("fake", "http://x/v1", "m", "q", 100000), "일", Recorder(self.tmp / "r", "t"))
        self.assertIn("너는 검토 작업자다.", sent[1]["m"][0]["content"])
        self.assertEqual(sent[1]["t"], ["read_file"])
        prof, _ = DG.child_profile(self.prof(), ["read_file", "run_shell"], "auto", {"kind": "code"}, DG.Budget(6), OFF)
        self.assertNotIn("쓰이면 안 된다", prof.system)                       # 같은 이름이면 stewart 프로필이 이긴다
        w(self.repo / ".claude" / "agents" / "only.md", "---\nname: only\ndescription: 호환만\n---\n호환 작업자")
        with self.assertRaises(ConfigError):
            DG.child_profile(self.prof(), ["read_file"], "auto", {"kind": "only"}, DG.Budget(6), OFF)
        prof, _ = DG.child_profile(self.prof(), ["read_file"], "auto", {"kind": "only"}, DG.Budget(6), ON)
        self.assertIn("호환 작업자", prof.system)

    def test_chat_with_skipped_claude(self):
        w(self.repo / "AGENTS.md", "규칙")
        w(self.wd / "CLAUDE.md", "@AGENTS.md")
        sess = C.ChatSession(self.prof(), Backend("fake", "http://x/v1", "m", "q", 100000), Recorder(self.tmp / "rs", "chat-t"))
        self.addCleanup(sess.close)
        self.assertEqual([m.get("skipped") for m in sess.meta["memory_files"]], [None, "same_as_agents"])

    def test_chat_command_expansion(self):
        w(self.repo / ".stewart" / "commands" / "fix.md", "고쳐라: $ARGUMENTS")
        sess = C.ChatSession(self.prof(), Backend("fake", "http://x/v1", "m", "q", 100000), Recorder(self.tmp / "rc", "chat-t"))
        self.addCleanup(sess.close)
        self.assertEqual(sess.expand_command("/fix a.py"), "고쳐라: a.py")
        self.assertIsNone(sess.expand_command("/mode"))
        self.assertEqual(sess.meta["project_config"]["commands"][0]["name"], "/fix")


if __name__ == "__main__":
    unittest.main()
