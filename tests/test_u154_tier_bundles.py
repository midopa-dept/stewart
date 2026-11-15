"""판 154: 등급 칸이 「＋ 새 대화」와 작업자에도 먹는다 — 등급마다 도구 묶음·지시문 꼴을 다르게 둘 수 있게.

- 새 대화(코드 자리)에 등급의 profile·slim(·slim_keep·slim_system)이 먹는다(hub --tier 의 메인과 같게). 대화 자리(parlando)는 자기 지시문.
- 등급의 tools·tools_deny 가 작업자에도 먹는다. 도구는 줄이기만(그 자리 도구의 부분집합, 작업자가 만들 수 있는 도구만).
- 메인이 등급으로 앉았어도 그 꼴(얇은 꼴·등급 프로필)이 다른 등급의 새 대화로 새지 않는다.
- 쓰이지 않는 칸은 stewart tiers·설정 시트에 한 줄로 알린다(조용히 무시하지 않게).
- 표가 없거나 그 칸이 없으면 지금 동작 그대로.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from stewart import spawn as SP, tierview as TV
from stewart.delegate import Budget
from tests.test_hub import msg
from tests.test_realuse_shape import FakeModel, Hub, is_worker, make_user_shape
from tests.test_realuse_tier_bodies import TASK, view

PROFILE = '''name = "lean"
description = "시험용 — 등급 프로필"
tools = ["read_file", "list_dir", "search", "edit_file", "run_shell", "write_file"]
system = "LEAN-지시문: 짧게 일한다."
max_steps = 20
'''
TIERS = '''
[tiers.presto]
slim = true
slim_keep = ["read_file", "search", "workers"]
use = "찾기·읽기"
default_for = ["read"]
tools = ["read_file", "list_dir", "search", "glob"]

[tiers.moderato]
use = "코딩"
default_for = ["write"]
tools_deny = ["run_shell", "web_fetch"]

[tiers.lean]
profile = "lean"
use = "등급 프로필"
'''


class TUnit(unittest.TestCase):
    def test_narrow_only_reduces(self):
        names = ["read_file", "search", "run_shell", "write_file"]
        self.assertEqual(SP.narrow_tools(names, None), names)
        self.assertEqual(SP.narrow_tools(names, {}), names)
        self.assertEqual(SP.narrow_tools(names, {"tools": ["search", "read_file", "view_image"]}), ["read_file", "search"])   # 없는 이름은 생기지 않는다
        self.assertEqual(SP.narrow_tools(names, {"tools_deny": ["run_shell"]}), ["read_file", "search", "write_file"])
        self.assertEqual(SP.narrow_tools(names, {"tools": ["search", "run_shell"], "tools_deny": ["run_shell"]}), ["search"])   # 빼는 쪽이 이긴다

    def test_load_and_notes(self):
        p = Path(tempfile.mkdtemp()) / "backends.toml"
        p.write_text('[backends.m]\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "m"\nmax_context = 32768\n'
                     '[tiers.a]\ntools_deny = ["run_shell"]\nslim_keep = ["read_file"]\nfoo = 1\n'
                     '[tiers.b]\ntools = "read_file"\nprofile = "없는프로필-154"\n'
                     '[tiers.c]\nslim = true\ntools = ["x"]\ntools_deny = ["x"]\n', encoding="utf-8")
        ts = SP.load_tiers(p)
        self.assertEqual(ts["a"]["tools_deny"], ["run_shell"])
        self.assertEqual(SP.tier_raw(ts["a"])["tools_deny"], ["run_shell"])
        na = " ".join(SP.tier_notes(ts["a"]))
        self.assertIn("모르는 칸 foo", na)
        self.assertIn("slim_keep", na)
        nb = " ".join(SP.tier_notes(ts["b"]))
        self.assertIn("tools 는 이름 목록", nb)
        self.assertIn("없는프로필-154", nb)
        nc = " ".join(SP.tier_notes(ts["c"]))
        self.assertIn("작업자로 띄울 때는 얇은 꼴을 쓰지 않는다", nc)
        self.assertIn("parlando", nc)
        self.assertIn("빼는 쪽이 이긴다", nc)
        self.assertEqual(SP.tier_notes({"name": "m", "manual": True, "default_for": ["read"]}, manual=True),
                         ["사람만 등급이라 작업자로 띄우지 않는다 — default_for·max 는 쓰지 않는다"])
        self.assertEqual(SP.tier_notes({"name": "plain", "budget": 32000, "use": "x"}), [])              # 쓰이는 칸만이면 알림 없음
        from stewart.config import Backend
        rows = TV.rows({"m": Backend(name="m", base_url="http://127.0.0.1:9/v1", model="m", precision="Q4", max_context=32768)}, "m", ts)
        out = TV.text(rows)
        self.assertIn("· a: 모르는 칸 foo", out)
        self.assertIn("· c: x 가 tools 와 tools_deny 에 다 있다", out)
        self.assertEqual(next(r for r in rows if r["tier"] == "a")["tools_deny"], ["run_shell"])

    def test_child_profile_reduces(self):
        from stewart import delegate as DG
        from stewart.config import load_profile
        parent = load_profile("chat")
        ptools = ["read_file", "list_dir", "search", "glob", "write_file", "run_shell", "forge_remote"]
        b = Budget(100)
        prof, _ = DG.child_profile(parent, ptools, "auto", {"tools": ["read_file", "write_file", "run_shell"],
                                                              "_tier": {"tools": ["read_file", "write_file", "view_image"]}}, b)
        self.assertEqual(prof.tools, ["read_file", "write_file"])                                    # 등급 tools 로 줄고, 부모에 없는 것은 안 생김
        prof, _ = DG.child_profile(parent, ptools, "auto", {"tools": ["read_file", "write_file", "run_shell"],
                                                              "_tier": {"tools_deny": ["run_shell"]}}, b)
        self.assertEqual(prof.tools, ["read_file", "write_file"])
        prof, _ = DG.child_profile(parent, ptools, "auto", {"tools": ["read_file", "forge_remote"], "_tier": {"tools": ["forge_remote", "read_file"]}}, b)
        self.assertEqual(prof.tools, ["read_file"])                                                  # 판 151 그대로(작업자가 만들 수 있는 도구만)
        prof, _ = DG.child_profile(parent, ptools, "auto", {"kind": "code", "_tier_kind": True}, b, read_default=True)
        self.assertEqual(prof.tools, ["read_file", "list_dir", "search", "glob"])                    # 읽기 작업자: 등급 profile 이 무엇이든 읽기 도구만
        prof, _ = DG.child_profile(parent, ptools, "auto", {"kind": "code"}, b, read_default=True)
        self.assertIn("write_file", prof.tools)                                                      # 모델이 고른 종류(에이전트 정의)는 예전 그대로
        prof0, _ = DG.child_profile(parent, ptools, "auto", {"tools": ["read_file", "write_file"]}, b)
        prof1, _ = DG.child_profile(parent, ptools, "auto", {"tools": ["read_file", "write_file"], "_tier": {"name": "x", "budget": 9000}}, b)
        self.assertEqual(prof0.tools, prof1.tools)                                                   # 칸이 없으면 그대로


class THub(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="u154-"))
        self.model = FakeModel(route=lambda b: msg("했다" if is_worker(b) else "네"))
        self.addCleanup(self.model.close)
        self.env = make_user_shape(self.root, self.model.url)
        ud = self.root / "home/.config/stewart"
        (ud / "profiles" / "lean.toml").write_text(PROFILE, encoding="utf-8")
        bt = ud / "backends.toml"
        bt.write_text(bt.read_text(encoding="utf-8") + TIERS, encoding="utf-8")
        (self.root / "project").mkdir(exist_ok=True)
        (self.root / "project" / "README.md").write_text("첫 줄\n", encoding="utf-8")

    def hub(self, extra=()):
        h = Hub(self.root, self.env, extra_args=extra)
        self.addCleanup(h.close)
        return h

    def first(self, h, kind, tier):
        c = h.post("/api/chat/new", {"how": "fresh", "kind": kind, "title": "새 대화", "from": "main", "tier": tier})["chat"]
        n0 = len(self.model.requests)
        h.post("/api/main", {"text": TASK, "chat": c})
        self.assertTrue(h.wait(lambda: not h.get(f"/api/state?chat={c}")["busy"]))
        return view([b for b in self.model.requests[n0:] if b.get("tools")][0], self.root)

    def main_first(self, h):
        n0 = len(self.model.requests)
        h.post("/api/main", {"text": TASK})
        self.assertTrue(h.wait(lambda: not h.get("/api/state")["busy"]))
        return view([b for b in self.model.requests[n0:] if b.get("tools")][0], self.root)

    def test_new_chat_profile_slim_and_talk(self):
        h = self.hub()
        main = self.main_first(h)
        slim = self.first(h, "code", "presto")
        self.assertEqual(slim["tools"], ["read_file", "search"])                                     # slim_keep ∩ 등급 tools(workers 는 tools 에 없어 빠짐)
        self.assertNotIn("허브 메인으로서", slim["system"])                                           # 짧은 지시(역할 글 없음)
        self.assertIn("꺼져 있는 도구", slim["system"])
        lean = self.first(h, "code", "lean")
        self.assertTrue(lean["system"].startswith("LEAN-지시문"), lean["system"][:80])
        self.assertTrue({"read_file", "list_dir", "search", "edit_file", "run_shell", "write_file"} <= set(lean["tools"]))   # 그 프로필 도구(+ 자리 공통 도구·허브 도구)
        self.assertFalse({"multi_edit", "web_fetch", "glob", "notebook_edit"} & set(lean["tools"]))
        mod = self.first(h, "code", "moderato")
        self.assertNotIn("run_shell", mod["tools"])
        self.assertNotIn("web_fetch", mod["tools"])
        self.assertEqual(set(main["tools"]) - set(mod["tools"]), {"run_shell", "web_fetch", "shell_output", "shell_kill"})   # 그 밖은 메인 자리와 같다(셸이 빠지면 셸 출력·끄기도)
        self.assertIn("허브 메인으로서", mod["system"])                                               # 지시문 꼴은 그 자리 그대로(자리 안내는 남은 도구를 따른다)
        talk = self.first(h, "talk", "lean")
        self.assertFalse(talk["system"].startswith("LEAN-지시문"))                                   # 대화 자리는 자기 지시문
        rows = {r["tier"]: r for r in h.get("/api/tiers")["rows"]}
        self.assertTrue(any("parlando" in n for n in rows["lean"]["notes"]))

    def test_main_tier_does_not_leak(self):
        h = self.hub(("--tier", "presto"))
        main = self.main_first(h)
        self.assertEqual(main["tools"], ["read_file", "search"])                                     # 메인은 얇은 꼴
        mod = self.first(h, "code", "moderato")
        self.assertIn("허브 메인으로서", mod["system"])                                               # 새 대화 moderato 는 얇은 꼴이 아니다
        self.assertIn("write_file", mod["tools"])
        self.assertNotIn("run_shell", mod["tools"])

    def test_read_worker_tier_profile_stays_read_only(self):
        """판 154(메인 지시): 쓰기·셸이 든 profile 을 등급에 적어도 읽기 작업자(도구를 적지 않음)는 읽기 도구만."""
        n0 = len(self.model.requests)
        r = subprocess.run([sys.executable, "-m", "stewart", "chat", "--once", "--backend", "cloud", "-p", "chat", "--worker", "read",
                            "--tier", "lean", "--workdir", str(self.root / "project"), TASK], env=self.env, cwd=str(self.root),
                           capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        wb = [b for b in self.model.requests[n0:] if is_worker(b) and b.get("tools")][0]
        names = {t["function"]["name"] for t in wb["tools"]}
        self.assertFalse(names & {"write_file", "edit_file", "multi_edit", "notebook_edit", "run_shell"}, names)
        self.assertTrue({"read_file", "list_dir", "search"} <= names)
        self.assertIn("LEAN-지시문", str(wb["messages"][0]["content"]))                               # 지시문은 그 프로필

    def test_worker_tier_tools(self):
        py = sys.executable
        for tier, want in (("presto", ["glob", "list_dir", "read_file", "search"]), ("moderato", None)):
            n0 = len(self.model.requests)
            r = subprocess.run([py, "-m", "stewart", "chat", "--once", "--backend", "cloud", "-p", "chat", "--worker", "write",
                                "--worker-tools", "read_file,list_dir,search,glob,write_file,run_shell", "--tier", tier,
                                "--workdir", str(self.root / "project"), TASK], env=self.env, cwd=str(self.root),
                               capture_output=True, text=True, timeout=180)
            self.assertEqual(r.returncode, 0, r.stderr[-500:])
            wb = [b for b in self.model.requests[n0:] if is_worker(b) and b.get("tools")]
            names = sorted(t["function"]["name"] for t in wb[0]["tools"])
            if want:
                self.assertEqual(names, want)
            else:
                self.assertNotIn("run_shell", names)
                self.assertIn("write_file", names)



class TPromptParts(unittest.TestCase):
    """판 154: 지시문 절 켬끔(등급 prompt 칸) — 빼기만, 남긴 글은 그대로, 칸이 없으면 지금 그대로."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="u154p-"))
        self.model = FakeModel(route=lambda b: msg("했다" if is_worker(b) else "네"))
        self.addCleanup(self.model.close)
        self.env = make_user_shape(self.root, self.model.url)
        ud = self.root / "home/.config/stewart"
        bt = ud / "backends.toml"
        bt.write_text(bt.read_text(encoding="utf-8")
                      + '\n[tiers.thin]\nuse = "얇게"\nprompt = { role = "line", board = false, engine_core = false, place_hints = false, '
                        'nudges = false, examples = false, memory_guide = false, user_memory = false, claude_md = false, skills_index = "names" }\n'
                      + '\n[tiers.noskill]\nuse = "스킬 색인 없음"\nprompt = { skills_index = "off", role = "off" }\n'
                      + '\n[tiers.same]\nuse = "칸만 있음"\nprompt = {}\n'
                      + '\n[tiers.mid]\nuse = "중간"\nprompt = { place_hints = false, nudges = false, examples = false, skills_index = "names" }\n'
                      + '\n[tiers.midl]\nuse = "중간 + 길이 맞춤"\nprompt = { place_hints = false, nudges = false, examples = false, '
                        'skills_index = "names", filler_file = "filler.txt" }\n', encoding="utf-8")
        self.filler = ud / "filler.txt"   # 상대 경로는 백엔드 표 폴더 기준
        (self.root / "project").mkdir(exist_ok=True)
        (self.root / "project" / "README.md").write_text("첫 줄\n", encoding="utf-8")
        (self.root / "project" / "CLAUDE.md").write_text("CLAUDE-호환-글\n", encoding="utf-8")
        (self.root / "project" / "STEWART.md").write_text("프로젝트-규칙-글\n", encoding="utf-8")

    def once(self, *args, worker=False):
        n0 = len(self.model.requests)
        r = subprocess.run([sys.executable, "-m", "stewart", "chat", "--once", "--backend", "cloud", "-p", "chat", *args,
                            "--workdir", str(self.root / "project"), TASK], env=self.env, cwd=str(self.root),
                           capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        b = [x for x in self.model.requests[n0:] if x.get("tools") and is_worker(x) == worker][0]
        return str(b["messages"][0]["content"]), [t["function"]["name"] for t in b["tools"]], b

    def test_unit(self):
        from stewart import promptparts as PP
        self.assertEqual(PP.check(None), ({}, []))
        v, n = PP.check({"role": "line", "nudges": "no", "zz": 1})
        self.assertEqual(v, {"role": "line"})
        self.assertEqual(len(n), 2)
        src = "머리\n- 요청을 받으면 필요한 만큼만 읽고 x\n- 명령은 run_shell로 y\n- 맡기기(판 97·108): z\n  이어진 줄\n- 답은 한국어로"
        self.assertEqual(PP.strip_core(src), "머리\n- 명령은 run_shell로 y\n- 답은 한국어로")
        self.assertEqual(PP.apply_base(src, {}), src)

    def test_hub_main_parts(self):
        self.env["STEWART_PLACE_HINTS"] = "1"   # 자리 안내는 기본 끔 — 켠 꼴에서 칸이 빼는지 본다
        full, tools0, b0 = self.once("--hub-main")
        same, tools_s, _ = self.once("--hub-main", "--tier", "same")
        self.assertEqual((same, tools_s), (full, tools0))                                          # 빈 prompt 칸 = 지금 그대로
        thin, tools1, b1 = self.once("--hub-main", "--tier", "thin")
        self.assertEqual(tools1, tools0)                                                             # 도구는 그대로(도구는 tools 칸 몫)
        self.assertIn("허브 메인으로서 일을 나눠 맡긴다:\n- 서로 독립인 큰 일", thin)                # role line: 머리 + 첫 줄
        self.assertNotIn("- 넓게 읽어야 하는 조사", thin)
        self.assertNotIn("현황판(board 도구)", thin)
        self.assertNotIn("- 확인하고 말한다(판 62)", thin)                                            # engine_core
        self.assertNotIn("- 요청을 받으면 필요한 만큼만", thin)
        self.assertIn("- 명령은 run_shell로 돌린다", thin)                                             # 안전·승인 줄은 남음
        self.assertIn("너는 사람과 대화하며 일하는 코딩 에이전트다", thin)                               # 정체
        self.assertNotIn("이 컴퓨터의 앱·브라우저", thin)                                              # place_hints
        self.assertNotIn("규칙 00", thin)                                                              # user_memory
        self.assertNotIn("CLAUDE-호환-글", thin)                                                       # claude_md
        self.assertIn("프로젝트-규칙-글", thin)                                                        # 프로젝트 STEWART.md 는 늘 남음
        self.assertIn("CLAUDE-호환-글", full)
        self.assertNotIn("[기억] 아래는", thin)                                                        # memory_guide
        self.assertIn("[기억] 아래는", full)
        self.assertIn("스킬(맞는 것이 있을 때만 load_skill", thin)                                     # skills_index names
        self.assertNotIn("쓸 수 있는 스킬(사용자가 넣어 둔 설명서)", thin)
        self.assertLess(len(thin), len(full) * 0.6)
        for ln in thin.splitlines():                                                                   # 남긴 줄은 한 글자도 고치지 않음
            if ln.strip() and "load_skill 로 본문을 읽고 따른다):" not in ln:
                self.assertIn(ln, full)
        d0 = {t["function"]["name"]: t["function"]["description"] for t in b0["tools"]}
        d1 = {t["function"]["name"]: t["function"]["description"] for t in b1["tools"]}
        self.assertTrue(any(d1[k] != d0[k] for k in d0))                                              # place_hints 는 도구 설명 덧붙임도 뺀다
        no, _, _ = self.once("--hub-main", "--tier", "noskill")
        self.assertNotIn("load_skill 로 본문을 읽고", no)
        self.assertNotIn("허브 메인으로서", no)
        self.assertIn("현황판(board 도구)", no)                                                        # board 는 따로

    def test_filler_same_length(self):
        """M-I−L 칸: 뺀 만큼의 글을 프로필 지시 끝에 그대로 붙여 지시문 길이를 C 와 ±3% 안으로. 본문은 그 글 한 덩이만 다르다."""
        import json
        c, tc, _ = self.once("--hub-main")
        mid, tm, _ = self.once("--hub-main", "--tier", "mid")
        gap = len(c) - len(mid) - 2
        self.assertGreater(gap, 500)
        text = ("봄비가 그친 뒤 골목의 담장 위로 젖은 흙냄새가 천천히 번진다. " * 200)[:gap].rstrip()   # 붙일 때 끝 공백은 뺀다
        self.filler.write_text(text + "\n", encoding="utf-8")
        n0 = len(self.model.requests)
        r = subprocess.run([sys.executable, "-m", "stewart", "chat", "--once", "--backend", "cloud", "-p", "chat", "--hub-main", "--tier", "midl",
                            "--workdir", str(self.root / "project"), TASK], env=self.env, cwd=str(self.root), capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        b = [x for x in self.model.requests[n0:] if x.get("tools")][0]
        midl = str(b["messages"][0]["content"])
        self.assertLessEqual(abs(len(midl) - len(c)) / len(c), 0.03)
        self.assertEqual(midl.replace("\n\n" + text, "", 1), mid)                                     # 그 글 한 덩이만 다르다
        self.assertEqual([t["function"]["name"] for t in b["tools"]], tm)
        self.assertLess(midl.index(text), midl.index("규칙 00"))                                       # 기억 글 앞(프로필 지시 끝)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        meta = json.loads((Path(out["run_dir"]) / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])["meta"]
        self.assertEqual(meta["features"]["prompt_filler"]["chars"], len(text))
        self.assertEqual(len(meta["features"]["prompt_filler"]["sha"]), 16)
        self.filler.unlink()
        r = subprocess.run([sys.executable, "-m", "stewart", "chat", "--once", "--backend", "cloud", "-p", "chat", "--hub-main", "--tier", "midl",
                            "--workdir", str(self.root / "project"), TASK], env=self.env, cwd=str(self.root), capture_output=True, text=True, timeout=180)
        self.assertNotEqual(r.returncode, 0)                                                           # 파일이 없으면 그 칸 없이 돌지 않는다
        self.assertIn("filler_file", r.stderr + r.stdout)

    def test_worker_parts(self):
        full, _, _ = self.once("--worker", "read", worker=True)
        thin, _, _ = self.once("--worker", "read", "--tier", "noskill", worker=True)
        self.assertIn("쓸 수 있는 스킬", full)
        self.assertNotIn("쓸 수 있는 스킬", thin)
        self.assertNotIn("허브 메인으로서", thin)
        self.assertIn("너는 부모 에이전트가 맡긴 일 하나만 하는 작업자다", thin)


if __name__ == "__main__":
    unittest.main()
