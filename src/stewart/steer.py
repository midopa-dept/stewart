"""되짚기 결론을 행동으로(U2-8 실사용, tag 47): 금지 호출 · 무해한 종료 코드 1 · 비밀 파일 읽기 막기.

사용자 사례: `.env` 를 cat 하는 for 고리가 마지막 `[ -f … ] && cat` 거짓으로 exit 1 → 모델이 실패로 보고 4번 되풀이.
되짚기 글은 맞게 나왔는데(첫 호출이 이미 내용을 줬다, read_file 로 바꾸겠다) 행동이 바뀌지 않았다. 사람이 ".env cat 하지 마라"
라고 하니 풀렸다. 그 "하지 마라" 를 stewart 가 스스로 건다:
- 금지(Ban): 되짚기 뒤 되풀이한 호출의 꼴(셸은 명령 낱말 묶음 — 글자가 조금 달라도 같은 프로그램·같은 대상이면 같다)과
  되짚기 글의 "금지: …" 줄. 세션 동안 남고(차례가 바뀌어도), 사람이 화면에서 푼다.
- 무해한 종료 코드 1: 출력이 있고 마지막 조각이 조건식(`[ … ]`·`test`·`[[ … ]]`)으로 끝나는 &&, 또는 grep 무매치 —
  "출력은 받았다 · 종료 코드 1" 로 적고 실패로 세지 않는다.
- 비밀 꼴 파일(.env·키·토큰): 셸로 내용을 읽으려 하면 기본은 거절하고 이름·크기·키 이름 길을 준다. 사람이 이번 말에서
  그 파일을 직접 말했으면 읽는다, 묻는 모드는 물어서 허락하면 읽는다.
"""

from __future__ import annotations

import hashlib
import re
import time

from . import fswalk

SIMILAR = 0.6                  # 셸 명령 낱말 묶음이 이만큼 겹치면 같은 꼴
STOP = frozenset("""for in do done if then else elif fi while until case esac echo printf true false && || ; | cd test
[ ] [[ ]] -f -d -e -n -z 2>/dev/null >/dev/null 2>&1 /dev/null -- == = != wc -l xargs sort uniq head tail""".split())
READERS = frozenset("""cat head tail less more bat sed awk grep rg strings xxd od hexdump base64 nl tac cut source . python3
python node perl ruby jq yq cp scp tar zip openssl""".split())
SAFE_SECRET = frozenset({"ls", "stat", "wc", "test", "[", "[[", "file", "du"})   # 이름·크기만 보는 프로그램
NAMES_ONLY_HINT = ("비밀 꼴 파일 내용은 기본으로 읽지 않는다. 이름·크기는 `ls -l <파일>`, 키 이름만은 "
                   "`sed -E 's/=.*$/=<가림>/' <파일>` 대신 사람에게 물어라. 사람이 읽으라고 분명히 말하면 읽는다.")


def words(cmd: str) -> list[str]:
    return re.findall(r"[^\s;&|()<>\"'`]+", cmd or "")


def shape(cmd: str) -> frozenset:
    """셸 명령의 꼴: 뜻이 있는 낱말(프로그램·대상·옵션) 묶음. $변수·셸 낱말은 뺀다."""
    out = set()
    for w in words(cmd):
        if w in STOP or w.startswith("$") or len(w) < 2:
            continue
        out.add(w.strip("/").lower())
    return frozenset(x for x in out if x)


def similar(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class Ban:
    def __init__(self, tool: str, text: str, shape_: frozenset | None = None, tokens: tuple = (), source: str = "reflect",
                 sig: str | None = None):
        self.tool, self.text, self.shape, self.tokens, self.source, self.sig = tool, text, shape_, tokens, source, sig
        self.id = hashlib.sha1(f"{tool}\0{text}\0{sorted(shape_ or ())}\0{tokens}".encode()).hexdigest()[:10]
        self.created = time.time()
        self.hits = 0

    def matches(self, name: str, raw_args: str, cmd: str | None) -> bool:
        if self.sig is not None and name + "\0" + raw_args == self.sig:
            return True
        if self.tool not in ("*", name):
            return False
        if self.shape and cmd is not None and similar(self.shape, shape(cmd)) >= SIMILAR:
            return True
        if self.tokens:
            hay = (cmd if cmd is not None else raw_args).lower()
            return all(t.lower() in hay for t in self.tokens)
        return False

    def view(self) -> dict:
        return {"id": self.id, "tool": self.tool, "text": self.text, "source": self.source, "hits": self.hits}


def ban_from_call(tc: dict) -> Ban | None:
    """되풀이한 호출에서 금지 하나. 셸은 꼴(낱말 묶음), 다른 도구는 같은 인자."""
    if not tc:
        return None
    name, raw = str(tc.get("name", "")), str(tc.get("raw_arguments", ""))
    cmd = None
    if name == "run_shell":
        try:
            import json
            cmd = str((json.loads(raw) or {}).get("command") or "")
        except (ValueError, AttributeError):
            cmd = None
    if cmd:
        return Ban(name, " ".join(cmd.split())[:160], shape_=shape(cmd), source="repeat", sig=name + "\0" + raw)
    return Ban(name, raw[:160], source="repeat", sig=name + "\0" + raw)


_BAN_LINE = re.compile(r"^\s*(?:[-*]\s*)?(?:금지|다시 하지 않을 호출)\s*[:：]\s*(.+)$", re.M)
_NEXT_LINE = re.compile(r"^\s*(?:[-*]\s*)?(?:다음|다음 첫 호출)\s*[:：]\s*(.+)$", re.M)


def parse_reflect(text: str, tool_names) -> dict:
    """되짚기 글의 끝 꼴 "금지: <도구> <꼴>" · "다음: <도구> <인자 요지>" 를 뽑는다."""
    bans, nxt = [], None
    for m in _BAN_LINE.finditer(text or ""):
        body = m.group(1).strip().strip("`")
        if not body or body in ("없음", "-", "(없음)"):
            continue
        parts = body.split(None, 1)
        tool = parts[0] if parts[0] in set(tool_names or ()) else "*"
        rest = parts[1] if tool != "*" and len(parts) > 1 else body
        toks = tuple(t for t in words(rest.strip("`")) if t not in STOP and len(t) >= 2)[:6]
        if tool == "run_shell" and rest.strip():
            bans.append(Ban(tool, body[:160], shape_=shape(rest.strip("`")) or None, tokens=toks, source="reflect"))
        elif toks:
            bans.append(Ban(tool, body[:160], tokens=toks, source="reflect"))
    m = _NEXT_LINE.search(text or "")
    if m:
        nxt = m.group(1).strip()[:200]
    return {"bans": bans, "next": nxt}


def soft_exit(cmd: str, result: str) -> bool:
    """exit 1 이지만 출력이 있고, 끝이 조건식 && … 또는 grep 무매치·diff 다름 같은 '거짓이라 1' 꼴인가."""
    head, _, body = (result or "").partition("\n")
    if not head.startswith("[exit 1]") or not body.strip():
        return False
    c = (cmd or "").strip()
    last = re.split(r"\s*(?:;|\n)\s*", c.rstrip("; \n"))[-1]
    if re.search(r"(\[\s[^\]]*\]|\[\[[^\]]*\]\]|\btest\s)\s*&&", last):           # [ -f x ] && cat x 로 끝남
        return True
    if re.search(r"\bdone\s*$", c) and re.search(r"(\[\s[^\]]*\]|\[\[[^\]]*\]\]|\btest\s)\s*&&", c):   # 고리 안 마지막이 그 꼴
        return True
    first = (words(last) or [""])[0]
    return first in ("grep", "rg", "diff", "cmp") or bool(re.search(r"\|\s*(grep|rg)\b[^|]*$", last))


SOFT_NOTE = ("\n\n[엔진] 출력은 받았다 · 종료 코드 1 — 마지막 조건식이 거짓이었거나(… && 로 끝나는 [ ] 검사) grep 무매치라 1 이 나왔다. "
             "실패가 아닐 수 있다. 위 출력을 읽고 다음으로 가라(같은 명령을 다시 부를 까닭이 없다).")


def secret_reads(cmd: str, is_secret) -> list[str]:
    """셸 명령이 비밀 꼴 파일의 내용을 읽으려 하는가: 읽는 프로그램이 있는 조각에서 비밀 꼴 이름."""
    out = []
    for piece in re.split(r"\s*(?:;|&&|\|\||\||\n|\bdo\b|\bthen\b)\s*", cmd or ""):
        ws = words(piece)
        if not ws:
            continue
        prog = ws[0].rsplit("/", 1)[-1]
        if prog in SAFE_SECRET or prog not in READERS:
            continue
        for w in ws[1:]:
            name = w.rstrip("/").rsplit("/", 1)[-1]
            if name and not name.startswith("-") and is_secret(name):
                out.append(w)
    # for f in .env .env.example; do cat "$f" — 고리 목록에 비밀 꼴 이름 + 고리 안 읽는 프로그램
    m = re.search(r"\bfor\s+\w+\s+in\s+([^;]*?)\s*;\s*do\b(.*?)\bdone\b", cmd or "", re.S)
    if m and any((words(p) or [""])[0].rsplit("/", 1)[-1] in READERS for p in re.split(r"\s*(?:;|&&|\|\||\|)\s*", m.group(2))):
        for w in words(m.group(1)):
            name = w.rstrip("/").rsplit("/", 1)[-1]
            if name and is_secret(name) and w not in out:
                out.append(w)
    return out


_FORBID = re.compile(r"(내용\S*\s*(은|는|을|를)?\s*(출력|보이|읽|보여|찍)\S*\s*(하지|지)\s*(말|마)|출력\s*(하지\s*)?(말|마|금지)|"
                     r"읽지\s*(말|마)|보지\s*(말|마)|cat\s*(은|는|을)?\s*하지\s*(말|마)|경로\S*\s*(와|과)?\s*크기만|이름만)")
_READ_VERB = re.compile(r"(읽어|읽고|보여|열어|출력해|cat\b|내용\s*(을|를)?\s*(봐|보|알려))")


def user_named(text: str, files: list[str]) -> bool:
    """사람이 이번 말에서 그 파일을 읽으라고 분명히 했는가: 파일 이름 + 읽는 말이 있고, "내용은 출력하지 말고"·"경로와 크기만"
    같은 금지 조건이 없을 때만(U2-8 사례: 지시에 금지가 있었는데 cat 했다)."""
    t = text or ""
    if not files or not all(f.rsplit("/", 1)[-1] in t for f in files):
        return False
    return not _FORBID.search(t) and bool(_READ_VERB.search(t))


def is_secret_name(name: str, globs=fswalk.SECRET_GLOBS, allow=fswalk.SECRET_ALLOW) -> bool:
    return fswalk.is_secret(name, globs, allow)
