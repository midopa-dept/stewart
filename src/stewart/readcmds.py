"""읽기 셸 명령 기본 통과: 작업 폴더 안만 읽는 명령은 셸 확인 자리에서 묻지 않는다.

조건(하나라도 어기면 지금처럼 묻는다)
- 목록(READ)에 있는 명령이고, 그 명령에서 쓰거나 다른 것을 실행하는 옵션(BAD)을 쓰지 않는다.
- 경로로 보이는 인자(옵션 값 포함)·글로브가 펼쳐진 결과·cd 대상이 모두 작업 폴더 안이다
  (절대 경로·../·~·심볼릭 링크로 밖이면 묻는다). $ 가 든 인자는 펼쳐질 값을 모르니 묻는다.
- 비밀 파일 꼴(.env·키 등)을 건드리지 않는다(이름으로 본다 — 빌린 장비의 모델에게 내용이 가지 않게).
- 판정 차례: deny → ask 규칙 → 이 기본 통과 → allow 규칙. rules.toml 의 ask 로 다시 묻게 돌릴 수 있다.
- Windows(WIN-3): 이름이 동봉 busybox 의 애플릿(또는 sh 내장)으로 돌 때만 통과한다(osdep.win_read_cmd_ok — tree 는 빼고,
  System32 의 find.exe·sort.exe 같은 같은 이름 프로그램이 돌 수 있는 셸이면 통과 없음). 경로 글은 osdep.win_path_kind 로 먼저 가른다
  (UNC·C:x·스트림·끝 점/공백·CON 등은 묻는다).

목록에 넣지 않은 것과 까닭은 NOT_IN 에 적어 둔다(시험·보고가 이 표를 쓴다).
"""

from __future__ import annotations

import glob
import os
import shlex
from pathlib import Path

from . import osdep

# 명령 → 쓰지 못하는 옵션(정확히 같거나 "옵션=" 으로 시작). 짧은 옵션 묶음(-rn)도 글자로 본다
READ: dict[str, dict] = {
    "ls": {"bad": set(), "why": "목록만"},
    "pwd": {"bad": set(), "why": "현재 폴더"},
    "echo": {"bad": set(), "why": "글 내보내기(파일로 가는 > 는 따로 본다)"},
    "printf": {"bad": {"-v"}, "why": "-v 는 셸 변수에 쓴다"},
    "true": {"bad": set(), "why": "아무것도 안 함"},
    "cat": {"bad": set(), "why": "파일 읽기"},
    "head": {"bad": set(), "why": "파일 앞부분"},
    "tail": {"bad": {"-f", "-F", "--follow", "--retry"}, "why": "-f 는 끝나지 않는다"},
    "wc": {"bad": {"--files0-from"}, "why": "--files0-from 은 다른 파일의 목록을 따른다"},
    "nl": {"bad": set(), "why": "줄 번호"},
    "grep": {"bad": {"-r", "-R", "--recursive", "--dereference-recursive", "-d", "--directories"},
             "short": "rR", "why": "재귀는 .env 같은 비밀 파일 내용까지 읽어 뺀다(파일을 적어 읽는다)"},
    "egrep": {"bad": {"-r", "-R", "--recursive", "--dereference-recursive", "-d", "--directories"}, "short": "rR",
              "why": "grep 과 같음"},
    "fgrep": {"bad": {"-r", "-R", "--recursive", "--dereference-recursive", "-d", "--directories"}, "short": "rR",
              "why": "grep 과 같음"},
    "find": {"bad": {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls", "-L",
                     "-follow", "-H"},
             "why": "-exec·-ok·-delete 는 실행·지우기, -fprint·-fls 는 파일 쓰기, -L·-follow·-H 는 링크를 따라 밖으로"},
    "tree": {"bad": {"-o", "-l", "--fromfile", "-R", "-H"}, "short": "olR",
             "why": "-o 는 파일 쓰기, -l 은 링크를 따라 밖으로, -R·-H 는 html 출력 묶음"},
    "stat": {"bad": set(), "why": "파일 정보"},
    "file": {"bad": {"-C", "--compile", "-m", "--magic-file"}, "short": "C",
             "why": "-C 는 magic 파일을 쓴다, -m 은 다른 magic 파일을 읽는다"},
    "du": {"bad": {"-L", "--dereference", "--files0-from"}, "short": "L", "why": "-L 은 링크를 따라 밖으로"},
    "sort": {"bad": {"-o", "--output", "--compress-program", "-T", "--temporary-directory", "--files0-from"},
             "short": "oT", "why": "-o 는 파일 쓰기, --compress-program 은 다른 프로그램 실행, -T 는 임시 파일 쓰기"},
    "uniq": {"bad": set(), "max_files": 1, "why": "둘째 파일 인자는 출력 파일이라 파일은 하나까지"},
    "cut": {"bad": set(), "why": "열 자르기"},
    "tr": {"bad": set(), "why": "표준 입력만 읽는다(인자는 글자 묶음)"},
    "diff": {"bad": {"--to-file", "--from-file"}, "why": "읽기(--from/to-file 은 경로 검사를 피한다)"},
    "cmp": {"bad": set(), "why": "읽기"},
    "basename": {"bad": set(), "why": "글만"},
    "dirname": {"bad": set(), "why": "글만"},
    "realpath": {"bad": set(), "why": "경로 풀기"},
    "readlink": {"bad": set(), "why": "링크 대상 보기"},
    "which": {"bad": set(), "why": "명령 자리 찾기"},
}
# 읽기처럼 보이지만 쓰거나 실행하는 길이 있어 목록에 넣지 않은 것(규칙으로는 사람이 늘 허용할 수 있다)
NOT_IN: dict[str, str] = {
    "sed": "-i 로 파일을 고치고, 스크립트의 w 명령은 파일을 쓰며 e 명령은 셸을 실행한다",
    "awk": "system()·print > 파일·\"cmd\" | getline 으로 쓰기·실행",
    "gawk": "awk 와 같음",
    "perl": "코드 실행",
    "xargs": "받은 글을 명령으로 실행",
    "tee": "파일 쓰기",
    "env": "뒤의 명령을 실행",
    "less": "!명령 으로 셸 실행, 대화형",
    "more": "less 와 같음",
    "vi": "편집기(셸 실행·쓰기)",
    "vim": "vi 와 같음",
    "git": "읽기 하위 명령도 저장소 설정(core.pager·diff.external·textconv·hooks·alias)으로 프로그램을 실행한다",
    "rg": "기본이 재귀라 비밀 파일 내용을 읽고, --pre 는 다른 프로그램을 실행한다",
    "ag": "기본이 재귀",
    "jq": "--rawfile·--slurpfile 등 옵션 값으로 경로 검사를 피하기 쉽다(규칙으로 허용 가능)",
    "date": "-s 로 시각을 바꾼다",
    "cd": "기본 통과(대상이 작업 폴더 안일 때만)라 목록 밖에서 따로 본다",
    "open": "다른 프로그램을 연다",
    "curl": "망",
    "wget": "망",
    "python": "코드 실행",
    "node": "코드 실행",
}
SAFE_ENV = frozenset({"CI", "NO_COLOR", "FORCE_COLOR", "CLICOLOR", "CLICOLOR_FORCE", "TERM", "COLUMNS", "LINES", "TZ",
                      "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "PYTHONDONTWRITEBYTECODE",
                      "PYTHONUNBUFFERED", "PYTHONIOENCODING", "PYTHONHASHSEED", "DEBUG", "VERBOSE"})
_GLOB = set("*?[")


def _inside(p: Path, root: Path) -> bool:
    try:
        rp = p.resolve()
    except OSError:
        return False
    return rp == root or root in rp.parents


def path_ok(token: str, cwd: Path, root: Path, is_secret) -> bool:
    """경로로 쓰일 수 있는 글이 작업 폴더 안이고 비밀 파일이 아닌지. 글로브는 펼쳐 하나하나 본다."""
    if "$" in token or "`" in token:
        return False
    if token.startswith("~"):
        return False
    if osdep.IS_WIN:
        k = osdep.win_path_kind(token)
        if k == "device":
            return True
        if k == "ask":
            return False
    cands = [token]
    if _GLOB & set(token):
        found = glob.glob(token if os.path.isabs(token) else str(cwd / token))
        cands = found or [token]
    for c in cands:
        p = Path(c)
        p = p if p.is_absolute() else cwd / p
        if not _inside(p, root):
            return False
        if is_secret(Path(os.path.realpath(p))) or is_secret(p):
            return False
    return True


def _opt_bad(tok: str, spec: dict) -> bool:
    """못 쓰는 옵션인가. 긴 옵션은 같거나 "옵션=…", 짧은 옵션은 묶음(-rn)·값 붙음(-ofile)까지 글자로 본다(넉넉히 막는 쪽)."""
    bad = spec["bad"]
    if tok in bad or any(tok.startswith(b + "=") for b in bad if b.startswith("--")):
        return True
    if tok.startswith("--"):
        return False
    shorts = {b[1] for b in bad if len(b) == 2 and b[0] == "-"} | set(spec.get("short", ""))
    return any(ch in shorts for ch in tok[1:])


def read_ok(piece: str, cwd: Path, root: Path, is_secret) -> bool:
    """이 조각이 목록의 읽기 명령이고 작업 폴더 안만 읽는가."""
    try:
        toks = shlex.split(piece)
    except ValueError:
        return False
    if not toks or toks[0] not in READ:
        return False
    if osdep.IS_WIN and not osdep.win_read_cmd_ok(toks[0]):
        return False
    spec = READ[toks[0]]
    files = 0
    end_opts = False
    for t in toks[1:]:
        if not end_opts and t == "--":
            end_opts = True
            continue
        if not end_opts and t.startswith("-") and t != "-":
            if _opt_bad(t, spec):
                return False
            if "=" in t:   # --opt=값 의 값도 경로로 본다
                if not path_ok(t.split("=", 1)[1], cwd, root, is_secret):
                    return False
            elif "/" in t or "~" in t:   # -f/etc/x 꼴
                i = min([t.index(c) for c in "/~" if c in t])
                if not path_ok(t[i:], cwd, root, is_secret):
                    return False
            continue
        files += 1
        if not path_ok(t, cwd, root, is_secret):
            return False
    if "max_files" in spec and files > spec["max_files"]:
        return False
    return True


def cd_target(piece: str, cwd: Path, root: Path) -> Path | None:
    """cd 조각이면 옮겨 갈 폴더(작업 폴더 안일 때만), 아니면 None. 인자 없는 cd·cd - 는 None."""
    try:
        toks = shlex.split(piece)
    except ValueError:
        return None
    if len(toks) != 2 or toks[0] != "cd" or toks[1] == "-" or "$" in toks[1] or toks[1].startswith("~"):
        return None
    if osdep.IS_WIN and osdep.win_path_kind(toks[1]) != "plain":
        return None
    p = Path(toks[1])
    p = p if p.is_absolute() else cwd / p
    return p.resolve() if _inside(p, root) and p.is_dir() else None
