"""웹 넛지(판 80): 모델이 웹을 스스로 잘 안 쓸 때, 문구 대신 실행기가 알맞은 때에 "웹으로 확인할 때다"를 끼워 넣는다.

- 같은 오류(오류 꼴 err_sig)가 다른 명령에서 두 번째로 나면 — 되짚기(세 번째) 앞에서 오류 문구 검색을 권한다.
- 처음 보는 모듈·속성·옵션 오류(ModuleNotFoundError·has no attribute·unrecognized arguments …)는 첫 번에.
  셸에 명령이 없는 것(command not found)은 빼고 짧은 셸 안내 한 줄(missing_note)로 — 문서로 풀 일이 아니다.
- 사람 말이 학습 뒤에 바뀌었을 사실(최신·요즘·가격 — 또는 현재·버전·출시를 묻는 물음, 폴더·커밋 같은 작업 말은 빼고)을 물으면 차례 시작에 "답하기 전에 웹으로 확인".
- 끝맺기(판 90): 웹을 부른 차례에서 남은 걸음이 WRAP_AT 이하가 되면 한 번 "더 찾지 말고 지금까지 찾은 것으로 답하라"(작은 모델이 검색만 되풀이하다 상한에 걸림).
- 웹 도구가 있을 때만(검색이 없으면 web_fetch 로 공식 문서를). 상한: 차례당 PER_TURN, 세션당 PER_SESSION.
- 켜기: STEWART_WEB_NUDGE=0|1 이 이긴다, 없으면 실사용(chat·허브) 켬 — 평가 run 에는 붙이지 않는다(엔진은 이 모듈을 쓰지 않음).
"""

from __future__ import annotations

import os
import re

PER_TURN = 2
PER_SESSION = 10

STRONG_RX = re.compile(   # 이것만으로 "학습 뒤 사실" — 최신·요즘·가격처럼 시간이 지나면 바뀌는 것
    r"(최신|요즘|요새|올해|릴리스\s*노트|출시일|가격|요금|시세|환율|"
    r"\b(latest|newest|this\s+year|release\s+notes|pricing|exchange\s+rate)\b)", re.I)
WEAK_RX = re.compile(     # 물음일 때만 — "현재 폴더"·"버전을 바꿔 줘" 같은 작업 지시는 빼려고
    r"(최근|현재|지금|버전|출시|나왔|나온|새로\s*(들어온|생긴|바뀐|추가된)|바뀐\s*점|달라진|릴리스|발표|20[2-3]\d\s*년|\b(current|recent|version|release[sd]?|today)\b)", re.I)
ASK_RX = re.compile(r"(\?|뭐|무엇|언제|몇|어떤|어느|알려|나왔|있어|있나|인가|일까|\b(what|when|which|is there)\b)", re.I)
LOCAL_RX = re.compile(r"(폴더|파일|디렉터리|경로|커밋|브랜치|로그|기록|함수|코드|저장소|대화|세션|\b(repo|git|folder|file)\b)", re.I)


LOOKUP_RX = re.compile(   # 판 117: "알아봐·찾아봐" + 처음 보는 이름(영문·숫자 섞인 제품·판 이름) — 작업 폴더에 있을 까닭이 없으면 웹부터
    r"(알아\s*봐|알아봐\s*줘|알아\s*봐\s*줘|찾아\s*봐|조사해|뭔지\s*(알려|찾아)|\b(look\s+up|find\s+out)\b)", re.I)
NAME_RX = re.compile(r"\b[A-Za-z][A-Za-z0-9.+-]*\s+\d+(?:\.\d+)+\b|\b[A-Z][a-z]+[A-Z]\w*\b|\b[a-z]+-?\d+(?:\.\d+)+\b")


def recent_hit(text: str) -> str:
    """학습 뒤에 바뀌었을 사실을 묻는 말이면 걸린 낱말, 아니면 ""."""
    t = text or ""
    m = STRONG_RX.search(t)
    if m:
        return m.group(0)
    m = LOOKUP_RX.search(t)
    if m and not LOCAL_RX.search(t):
        return m.group(0)
    m = NAME_RX.search(t)
    if m and ASK_RX.search(t) and not LOCAL_RX.search(t):
        return m.group(0)
    m = WEAK_RX.search(t)
    if m and ASK_RX.search(t) and not LOCAL_RX.search(t):
        return m.group(0)
    return ""


# 셸에 그 명령이 없는 것(판 122): 문서로 풀 일이 아니다 — 웹 넛지에서 빼고 chat 이 짧은 셸 안내 한 줄을 붙인다(missing_note).
MISSING_CMD_RX = re.compile(
    r"(?:^|\n|: )([\w.+-]+): command not found(?!: )|command not found: ([\w.+-]+)"
    r"|'([\w.+-]+)' is not recognized as an internal or external command", re.I)
NOTE_MISSING = "\n\n[엔진] `{name}` 명령이 이 셸에 없다. 환경 정보에 있다고 적힌 것{alt}을 써라."


def missing_command(result: str) -> str | None:
    """셸이 '명령 없음'을 냈으면 그 명령 이름."""
    m = MISSING_CMD_RX.search(result or "")
    return next((g for g in m.groups() if g), None) if m else None


def missing_note(name: str, pythons: list[str]) -> str:
    """명령 없음 안내 한 줄. 파이썬 계열이면 환경 정보에서 찾은 파이썬 명령을 적는다."""
    alt = ""
    if pythons and re.match(r"^(python|pip)[\d.]*$", name):
        alt = f"(파이썬은 `{pythons[0]}`)"
    return NOTE_MISSING.format(name=name, alt=alt)


UNKNOWN_RX = re.compile(
    r"(ModuleNotFoundError|No module named|ImportError|command not found|not found: [\w.-]+|has no attribute|"
    r"unrecognized arguments?|unknown (option|argument|flag|command)|invalid choice|is not recognized|"
    r"DeprecationWarning|deprecated|no matching distribution|Could not find a version)", re.I)

NOTE_SEARCH = ("\n\n[엔진] {why} — 이제 웹으로 찾아볼 때다. 오류 문구(또는 이름·판)를 그대로 web_search 로 찾고, "
               "공식 문서·이슈의 원문을 web_fetch 로 열어 확인한 뒤 고쳐라. 찾은 주소를 근거로 붙인다.")
NOTE_FETCH = ("\n\n[엔진] {why} — 짐작으로 되풀이하지 말고, 그 라이브러리·도구의 공식 문서 주소를 web_fetch 로 열어 확인한 뒤 고쳐라.")
NOTE_RECENT = ("[엔진] 이 물음은 학습 뒤에 바뀌었을 수 있는 사실(최신·버전·가격·출시·처음 보는 이름 등)을 묻는다. 기억으로 답하지 말고, "
               "작업 폴더에 있을 까닭이 없으면 폴더를 뒤지기 전에 먼저 {tool} 로 확인한 뒤, 확인한 주소를 붙여 답하라.")


WRAP_AT = 2
NOTE_WRAP = ("\n\n[엔진] 남은 걸음이 {left}개다. 더 찾지 말고 지금까지 찾은 것으로 답하라 — 확인한 것은 주소를 붙이고, "
             "확인하지 못한 것은 확인하지 못했다고 적는다.")


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return env.get("STEWART_WEB_NUDGE", "1") != "0"


class Nudger:
    def __init__(self, search: bool, fetch: bool, on: bool = True):
        self.search, self.fetch = bool(search), bool(fetch)
        self.on = on and (self.search or self.fetch)
        self.turn_n = 0
        self.session_n = 0
        self.seen_sigs: set = set()
        self.log: list = []

    def reset_turn(self) -> None:
        self.turn_n = 0
        self.wrapped = False

    def on_budget(self, left: int, web_calls: int) -> str:
        """이 차례에 웹을 불렀고 남은 걸음이 WRAP_AT 이하면 끝맺기 글(차례에 한 번, 차례·세션 상한과 따로)."""
        if not self.on or getattr(self, "wrapped", False) or web_calls <= 0 or left > WRAP_AT or left < 1:
            return ""
        self.wrapped = True
        self.log.append({"kind": "wrap", "why": f"left {left}"})
        return NOTE_WRAP.format(left=left)

    def _room(self) -> bool:
        return self.on and self.turn_n < PER_TURN and self.session_n < PER_SESSION

    def _take(self, kind: str, why: str) -> None:
        self.turn_n += 1
        self.session_n += 1
        self.log.append({"kind": kind, "why": why[:200]})

    def on_turn(self, text: str) -> str:
        """사람 말이 최신 사실을 물으면 그 말 끝에 붙일 글(없으면 "")."""
        hit = recent_hit(text) if self._room() else ""
        if not hit:
            return ""
        tool = "web_search(그리고 원문은 web_fetch)" if self.search else "공식 문서·발표 주소를 web_fetch"
        self._take("recent", hit)
        return "\n\n" + NOTE_RECENT.format(tool=tool)

    def on_error(self, sig: str, n: int, result: str) -> str:
        """실패한 명령 하나: 넣을 글(없으면 "")."""
        if not self._room():
            return ""
        if missing_command(result):   # 셸에 명령이 없다 — 웹으로 찾을 일이 아니다(판 122)
            return ""
        m = UNKNOWN_RX.search(result or "")
        if m and (sig or m.group(0)) not in self.seen_sigs:
            self.seen_sigs.add(sig or m.group(0))
            why = f"처음 보는 이름·옵션 오류({m.group(0)})"
        elif sig and n == 2 and sig not in self.seen_sigs:
            self.seen_sigs.add(sig)
            why = f"같은 오류({sig})가 두 번째"
        else:
            return ""
        self._take("error", why)
        return (NOTE_SEARCH if self.search else NOTE_FETCH).format(why=why)
