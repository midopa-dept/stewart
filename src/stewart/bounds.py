"""작업 폴더 경계: 파일 도구·셸이 작업 폴더 밖에 쓰는 것을 가른다(읽기는 프로필 read_outside 대로).

판정
- 경로는 작업 폴더(셸은 그 셸의 지금 위치) 기준으로 붙이고 ~ 를 펼친 뒤 realpath 로 푼다. 심볼릭 링크·.. 로 돌려도 실제 자리로 본다.
  작업 폴더 안의 링크가 밖을 가리키면 밖이다.
- 안·밖은 글자가 아니라 파일 시스템으로 가른다: 실제 경로의 위쪽 폴더를 차례로 올라가며, 있는 폴더가 쓰기 허용 폴더와 같은 것
  (같은 장치·같은 inode)이면 안이다. 대소문자를 가리지 않는 볼륨에서 /Work 와 /work 는 같은 폴더로 잡히고, 가리는 볼륨에서는 다른 폴더다.
- 쓰기 허용 폴더: 작업 폴더 + 프로필 extra_write_dirs + 임시 폴더(tempfile.gettempdir()·/tmp·/var/tmp) + /dev/null 같은 장치.
- chat 은 밖 쓰기를 모드와 관계없이 물어 본다(y 는 그 실제 경로 하나만 이 세션에서 연다). run 은 거부하고 까닭을 도구 결과로 돌려준다.

셸(완전한 격리는 아니다 — 흔한 꼴만 본다)
- 명령을 줄·; && || | & 로 나눠 마디마다 본다. cd 는 다음 마디의 기준 폴더를 바꾼다.
- 쓰기 대상으로 보는 것: > >> &> >| 리다이렉트 대상, rm·rmdir·unlink·shred·touch·mkdir·truncate·chmod·chown·chgrp 의 인자,
  mv 의 인자 모두, cp·ln·install·rsync 의 마지막 인자, tee 의 인자, sed -i 의 파일, dd 의 of=.
- 못 보는 꼴(한계): 변수·명령 치환·글롭이 든 경로, 다른 프로그램이 안에서 쓰는 것(python -c, git -C, make, 스크립트 파일),
  eval·bash -c 안의 명령, here-doc, xargs 로 넘긴 인자, 셸 함수·별칭. 이런 것은 모드대로 다룬다.
"""

from __future__ import annotations

import os
import re
import shlex
import tempfile
from pathlib import Path

READ_TOOLS = frozenset({"read_file", "list_dir", "search", "glob"})
WRITE_TOOLS = frozenset({"write_file", "edit_file", "multi_edit", "notebook_edit"})
READ_OUTSIDE = ("allow", "ask", "deny")
DEVICES = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/zero")

RUN_DENY = ("작업 폴더 밖({path})에는 무인 실행에서 쓰지 않는다. 작업 폴더 안에서 하거나, 밖을 꼭 고쳐야 하면 무엇을 어떻게 "
            "바꿀지 최종 보고에 적어 사람에게 맡겨라.")
READ_DENY = "작업 폴더 밖({path})은 이 프로필에서 읽지 않는다(read_outside = deny). 작업 폴더 안의 자료로 하라."
ASK_WRITE = "    ⚠ 작업 폴더 밖에 쓴다: {paths}. 이 경로만 이번 세션에서 열까? [y/n/모델에 할 말] "
ASK_READ = "    작업 폴더 밖을 읽는다: {path}. 읽을까? [y/n/모델에 할 말] "

_SEG = {";", "&&", "||", "|", "&", "|&"}
_REDIR = re.compile(r"^(?:\d*|&)>{1,2}\|?$")
_ALL_ARGS = {"rm", "rmdir", "unlink", "shred", "touch", "mkdir", "truncate", "tee", "mv"}
_SKIP_FIRST = {"chmod", "chown", "chgrp"}
_LAST_ARG = {"cp", "ln", "install", "rsync"}
_PREFIX = {"sudo", "env", "command", "nice", "nohup", "time", "exec"}


def temp_dirs() -> list[str]:
    """쓰기를 늘 허용하는 임시 폴더(시험은 이 함수를 바꿔 끼워 끈다)."""
    return sorted({tempfile.gettempdir(), "/tmp", "/var/tmp"})


def _real(path: str, base: str) -> str:
    return os.path.realpath(os.path.join(base, os.path.expanduser(path)))


def _stat_of(p: str):
    try:
        return os.stat(p)
    except OSError:
        return None


class Bounds:
    def __init__(self, workdir, extra_write_dirs=(), read_outside: str = "allow"):
        self.workdir = str(Path(workdir).resolve())
        roots = [self.workdir] + [_real(str(d), self.workdir) for d in extra_write_dirs or ()]
        roots += temp_dirs()
        self.write_roots = [st for st in (_stat_of(os.path.realpath(r)) for r in roots) if st is not None]
        self.read_roots = [_stat_of(self.workdir)]
        self.read_outside = read_outside if read_outside in READ_OUTSIDE else "allow"
        self.approved: set[str] = set()      # chat 이 이 세션에서 연 실제 경로(쓰기)
        self.approved_read: set[str] = set()

    @staticmethod
    def _inside(real: str, roots) -> bool:
        p = real
        while True:
            st = _stat_of(p)
            if st is not None and any(os.path.samestat(st, r) for r in roots if r is not None):
                return True
            parent = os.path.dirname(p)
            if parent == p:
                return False
            p = parent

    def real(self, path: str, base: str | None = None) -> str:
        return _real(path, base or self.workdir)

    def write_outside(self, path: str, base: str | None = None) -> str | None:
        """쓰기 대상이 허용 밖이면 그 실제 경로, 안(또는 이미 승인)이면 None."""
        if path in DEVICES or path.startswith("/dev/fd/"):
            return None
        r = self.real(path, base)
        if r in self.approved or self._inside(r, self.write_roots):
            return None
        return r

    def read_outside_path(self, path: str) -> str | None:
        r = self.real(path or ".")
        if r in self.approved_read or self._inside(r, self.read_roots):
            return None
        return r

    def tool_write(self, name: str, args: dict) -> str | None:
        if name in WRITE_TOOLS and args.get("path"):
            return self.write_outside(str(args["path"]))
        return None

    def tool_read(self, name: str, args: dict) -> str | None:
        if name in READ_TOOLS and self.read_outside != "allow":
            return self.read_outside_path(str(args.get("path") or "."))
        return None

    def shell_outside(self, command: str, cwd: str | None = None) -> list[str]:
        """셸 명령에서 찾은 밖 쓰기 대상(실제 경로, 중복 없이). 못 읽는 꼴은 건너뛴다(한계는 모듈 설명)."""
        return self._scan(command, cwd, only_outside=True)

    def shell_writes(self, command: str, cwd: str | None = None) -> list[str]:
        """셸 명령에서 찾은 쓰기 대상 모두(안·밖 가리지 않음, 실제 경로)."""
        return self._scan(command, cwd, only_outside=False)

    def _scan(self, command: str, cwd: str | None, only_outside: bool) -> list[str]:
        out: list[str] = []
        base = cwd or self.workdir
        for line in (command or "").splitlines():
            try:
                lex = shlex.shlex(line, posix=True, punctuation_chars=";&|<>")
                lex.whitespace_split = True
                toks = list(lex)
            except ValueError:
                continue
            seg: list[str] = []
            for t in toks + [";"]:
                if t in _SEG:
                    base = self._segment(seg, base, out, only_outside)
                    seg = []
                else:
                    seg.append(t)
        return out

    def _segment(self, seg: list[str], base: str, out: list[str], only_outside: bool = True) -> str:
        words, targets, i = [], [], 0
        while i < len(seg):
            t = seg[i]
            if _REDIR.match(t) or t in (">", ">>"):
                if i + 1 < len(seg):
                    targets.append(seg[i + 1])
                i += 2
                continue
            if t in (">&", "<&", ">&-", "<&-"):      # 파일 기술자 복제(2>&1 등)
                i += 2 if t in (">&", "<&") else 1
                continue
            if t.startswith("<"):
                i += 2 if t in ("<", "<<", "<<<") else 1
                continue
            words.append(t)
            i += 1
        while words and (re.match(r"^[A-Za-z_]\w*=", words[0]) or words[0] in _PREFIX):
            words.pop(0)
        if words:
            cmd = os.path.basename(words[0])
            args = words[1:]
            plain = [a for a in args if not a.startswith("-")]
            if cmd == "cd":
                if plain:
                    base = self.real(plain[0], base)
                elif not args:
                    base = os.path.expanduser("~")
            elif cmd in _ALL_ARGS:
                targets += plain
            elif cmd in _SKIP_FIRST:
                targets += plain[1:]
            elif cmd in _LAST_ARG:
                targets += plain[-1:] if len(plain) >= 2 else []
            elif cmd == "sed" and any(a == "--in-place" or re.match(r"^-[a-zA-Z]*i", a) for a in args):
                has_script_opt = any(a in ("-e", "-f") or a.startswith(("--expression", "--file")) for a in args)
                files, skip = [], False
                for a in args:
                    if skip:
                        skip = False
                        continue
                    if a in ("-e", "-f"):
                        skip = True
                        continue
                    if not a.startswith("-"):
                        files.append(a)
                targets += files if has_script_opt else files[1:]
            elif cmd == "dd":
                targets += [a[3:] for a in args if a.startswith("of=")]
        for t in targets:
            if not t or "$" in t or "`" in t or any(c in t for c in "*?[") or t == "-":
                continue
            if only_outside:
                r = self.write_outside(t, base)
            elif t in DEVICES or t.startswith("/dev/fd/"):
                r = None
            else:
                r = self.real(t, base)
            if r and r not in out:
                out.append(r)
        return base


def from_profile(profile) -> Bounds:
    return Bounds(profile.workdir, getattr(profile, "extra_write_dirs", None) or (),
                  getattr(profile, "read_outside", None) or "allow")
