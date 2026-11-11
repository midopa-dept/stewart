"""읽기 셸 명령 기본 통과 + 막히는 정당한 일 대안(cd·파이프 걸러내기·작업 폴더 안 리다이렉트·안전 변수)."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stewart import allowrules as AR, chat as C, fswalk, projcfg as PC, readcmds as RC
from stewart.config import Backend, Profile
from stewart.record import Recorder
from tests.test_engine import _resp


def secret(p: Path) -> bool:
    return fswalk.is_secret(p.name)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.wd = self.tmp / "wd"
        (self.wd / "sub").mkdir(parents=True)
        (self.wd / "a.py").write_text("print(1)\n")
        (self.wd / "b.py").write_text("print(2)\n")
        (self.wd / "sub" / "c.txt").write_text("c\n")
        (self.wd / ".env").write_text("TOKEN=zz\n")
        (self.tmp / "outside.txt").write_text("밖\n")
        os.symlink(self.tmp / "outside.txt", self.wd / "link.txt")
        os.symlink(self.tmp, self.wd / "linkdir")

    def ok(self, piece, cwd=None):
        return RC.read_ok(piece, cwd or self.wd, self.wd, secret)

    def ctx(self, **k):
        return AR.Ctx(self.wd, k.pop("cwd", self.wd), secret, **k)


class TReadOk(Base):
    def test_inside_reads_pass(self):
        for p in ("ls", "ls -la sub", "pwd", "cat a.py", "head -n 5 a.py", "tail -n 3 sub/c.txt", "wc -l a.py b.py",
                  "grep -n print a.py", "grep -in print *.py", "find . -name '*.py'", "find sub -type f", "stat a.py",
                  "du -sh sub", "tree -L 2", "sort a.py", "uniq a.py", "cut -c1-3 a.py", "tr a b", "diff a.py b.py",
                  "cmp a.py b.py", "file a.py", "echo hi", "printf '%s\\n' x", "basename sub/c.txt", "realpath sub",
                  "which python3", "nl a.py", "cat -- a.py"):
            self.assertTrue(self.ok(p), p)
        self.assertTrue(self.ok("cat c.txt", self.wd / "sub"))                 # cd 뒤의 상대 경로

    def test_outside_and_secret_ask(self):
        for p in ("ls ..", "ls ../", "cat ../outside.txt", "cat /etc/hosts", "cat ~/x", "cat $HOME/x", "ls ~",
                  "cat link.txt",                                              # 심볼릭 링크로 밖
                  "ls linkdir/", "cat linkdir/outside.txt", "cat .env", "grep TOKEN .env", "head .env*",
                  "grep -f/etc/passwd a.py", "grep --file=/etc/passwd a.py", "cat sub/../../outside.txt",
                  "find / -name x", "find .. -name x", "du -sh /"):
            self.assertFalse(self.ok(p), p)

    def test_glob_expanded_and_checked(self):
        self.assertTrue(self.ok("cat *.py"))
        self.assertFalse(self.ok("cat *.txt"))                                 # link.txt 가 밖을 가리킨다
        self.assertFalse(self.ok("cat .e*"))                                   # .env

    def test_writing_or_running_options_ask(self):
        for p in ("find . -exec rm {} ;", "find . -execdir ls ;", "find . -ok ls ;", "find . -delete", "find . -fprint x",
                  "find . -fprintf x %p", "find . -fls x", "find -L .", "find . -follow", "sort -o out a.py",
                  "sort -oout a.py", "sort --output=out a.py", "sort --compress-program=sh a.py", "sort -T /tmp a.py",
                  "uniq a.py out.txt", "grep -r x .", "grep -R x .", "grep -rn x .", "grep --recursive x .",
                  "tail -f a.py", "tail -fn 5 a.py", "tree -o out", "tree -l", "file -C", "du -L sub", "printf -v x y",
                  "wc --files0-from=list"):
            self.assertFalse(self.ok(p), p)

    def test_not_in_list(self):
        for cmd in ("sed -n p a.py", "awk '{print}' a.py", "xargs ls", "tee x", "env", "less a.py", "git status",
                    "rg x", "jq . a.json", "python3 a.py", "date", "open a.py", "touch a.py", "rm a.py", "mv a b"):
            self.assertFalse(self.ok(cmd), cmd)
        for name in ("sed", "awk", "xargs", "tee", "git", "rg"):
            self.assertIn(name, RC.NOT_IN)

    def test_cd_target(self):
        self.assertEqual(RC.cd_target("cd sub", self.wd, self.wd), (self.wd / "sub").resolve())
        for p in ("cd ..", "cd /", "cd ~", "cd", "cd -", "cd $HOME", "cd linkdir", "cd nope"):
            self.assertIsNone(RC.cd_target(p, self.wd, self.wd), p)


class TDecideCtx(Base):
    def test_chains_cd_and_filters(self):
        d = lambda cmd, rules=(), **k: AR.decide(cmd, list(rules), self.ctx(**k))
        self.assertEqual(d("cd sub && ls").effect, "allow")
        self.assertEqual(d("cd sub && cat c.txt").effect, "allow")
        self.assertIsNone(d("cd .. && ls").effect)                               # 밖으로 cd 는 묻기
        self.assertIsNone(d("cd sub && cat ../../outside.txt").effect)           # cd 뒤 상대 경로로 밖
        self.assertEqual(d("ls | grep py | wc -l").effect, "allow")             # 파이프 뒤 걸러내기
        npm = [AR.Rule("npm test *", "allow", "user")]
        self.assertEqual(d("npm test 2>&1 | tail -20", npm).effect, "allow")
        self.assertEqual(d("cd sub && npm test", npm).effect, "allow")
        self.assertEqual(d("CI=1 NO_COLOR=1 npm test", npm).effect, "allow")    # 안전 목록 변수는 떼고 본다
        for bad in ("PATH=/x npm test", "LD_PRELOAD=x npm test", "PYTHONPATH=. npm test", "NODE_OPTIONS=x npm test",
                    "CI=$X npm test", "FOO=1"):
            self.assertEqual(d(bad, npm).effect, "ask", bad)
        r = d("ls && touch x")
        self.assertIsNone(r.effect)                                              # 한 조각이라도 통과 못 하면 묻기

    def test_redirects(self):
        d = lambda cmd, **k: AR.decide(cmd, [AR.Rule("npm test *", "allow", "user")], self.ctx(**k))
        self.assertEqual(d("npm test > out.txt", allow_redirect=True).effect, "allow")      # edits: 작업 폴더 안
        self.assertEqual(d("npm test >> sub/log.txt", allow_redirect=True).effect, "allow")
        self.assertEqual(d("npm test > out.txt").effect, "ask")                   # edits 가 아니면 묻기
        for bad in ("npm test > ../x", "npm test > /tmp/x", "npm test > .env", "npm test > ~/x", "npm test > link.txt",
                    "npm test > $HOME/x"):
            self.assertEqual(d(bad, allow_redirect=True).effect, "ask", bad)
        with mock.patch.dict(os.environ, {"STEWART_USER_DIR": str(self.wd / "sub")}):
            self.assertEqual(d("npm test > sub/x.txt", allow_redirect=True).effect, "ask")   # 설정 폴더로 가는 쓰기
        self.assertEqual(d("sort < a.py", allow_redirect=False).effect, "allow")  # 안쪽 파일 읽기
        self.assertEqual(d("sort < ../outside.txt").effect, "ask")

    def test_rules_come_first(self):
        deny = [AR.Rule("ls *", "deny", "user")]
        self.assertEqual(AR.decide("ls sub", deny, self.ctx()).effect, "deny")
        ask = [AR.Rule("cat *", "ask", "user")]
        self.assertEqual(AR.decide("cat a.py", ask, self.ctx()).effect, "ask")   # rules.toml 로 다시 묻게
        r = AR.decide("ls", [], self.ctx())
        self.assertEqual((r.effect, r.sources, r.rule_ids), ("allow", ["builtin"], ["read"]))
        self.assertIsNone(AR.decide("ls", [], None).effect)                      # 문맥 없으면(옛 꼴) 기본 통과 없음


class TChatModes(Base):
    def sess(self, mode):
        prof = Profile(name="t", path=self.wd / "p.toml", system="s", tools=["run_shell"], workdir=self.wd, max_steps=4)
        ui = C.UI(out=open(os.devnull, "w"))
        self.addCleanup(ui.out.close)
        with mock.patch.dict(os.environ, {PC.USER_DIR_ENV: str(self.tmp / "no-user")}):
            s = C.ChatSession(prof, Backend("fake", "http://x/v1", "m", "q", 100000), Recorder(self.tmp / "runs", "c"), ui=ui,
                              mode=mode)
        self.addCleanup(s.close)
        asked = []
        ui.ask = lambda p: asked.append(p) or "n"
        return s, asked

    def run_cmds(self, s, cmds):
        seq = [_resp(calls=[("run_shell", json.dumps({"command": c}))]) for c in cmds] + [_resp("끝")]
        with mock.patch.object(C, "chat", side_effect=seq):
            s.turn("셸")
        return [m["content"] for m in s.messages if m["role"] == "tool"]

    def test_edits_and_ask_modes(self):
        s, asked = self.sess("edits")
        out = self.run_cmds(s, ["ls", "cat a.py", "echo x > made.txt", "cat ../outside.txt", "touch t.txt"])
        self.assertEqual(len(asked), 2)                                         # 밖 읽기·touch 만 물음
        self.assertTrue((self.wd / "made.txt").exists())                         # edits: 작업 폴더 안 > 는 통과
        self.assertIn("a.py", out[0])
        s2, asked2 = self.sess("ask")
        self.run_cmds(s2, ["ls", "echo y > made2.txt"])
        self.assertEqual(len(asked2), 1)                                         # ask: 읽기는 통과, 쓰는 > 는 물음
        self.assertFalse((self.wd / "made2.txt").exists())

    def test_component_source_builtin(self):
        s, _ = self.sess("edits")
        self.run_cmds(s, ["ls sub"])
        ev = [json.loads(l) for l in (s.rec.dir / "events.jsonl").read_text().splitlines()]
        comp = [e for e in ev if e["kind"] == "component" and e["name"] == "allow_rule"]
        self.assertEqual((comp[0]["action"], comp[0]["detail"]["source"], comp[0]["detail"]["rule"]), ("allow", "builtin", "read"))


if __name__ == "__main__":
    unittest.main()
