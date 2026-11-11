"""웹 넛지(판 80): 모델이 웹을 스스로 잘 안 쓸 때, 문구 대신 실행기가 알맞은 때에 "웹으로 확인할 때다"를 끼워 넣는다.

- 같은 오류(오류 꼴 err_sig)가 다른 명령에서 두 번째로 나면 — 되짚기(세 번째) 앞에서 오류 문구 검색을 권한다.
- 처음 보는 모듈·명령·속성·옵션 오류(ModuleNotFoundError·command not found·has no attribute·unrecognized arguments …)는 첫 번에.
- 사람 말이 학습 뒤에 바뀌었을 사실(최신·요즘·가격 — 또는 현재·버전·출시를 묻는 물음, 폴더·커밋 같은 작업 말은 빼고)을 물으면 차례 시작에 "답하기 전에 웹으로 확인".
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


def recent_hit(text: str) -> str:
    """학습 뒤에 바뀌었을 사실을 묻는 말이면 걸린 낱말, 아니면 ""."""
    t = text or ""
    m = STRONG_RX.search(t)
    if m:
        return m.group(0)
    m = WEAK_RX.search(t)
    if m and ASK_RX.search(t) and not LOCAL_RX.search(t):
        return m.group(0)
    return ""


UNKNOWN_RX = re.compile(
    r"(ModuleNotFoundError|No module named|ImportError|command not found|not found: [\w.-]+|has no attribute|"
    r"unrecognized arguments?|unknown (option|argument|flag|command)|invalid choice|is not recognized|"
    r"DeprecationWarning|deprecated|no matching distribution|Could not find a version)", re.I)

NOTE_SEARCH = ("\n\n[엔진] {why} — 이제 웹으로 찾아볼 때다. 오류 문구(또는 이름·판)를 그대로 web_search 로 찾고, "
               "공식 문서·이슈의 원문을 web_fetch 로 열어 확인한 뒤 고쳐라. 찾은 주소를 근거로 붙인다.")
NOTE_FETCH = ("\n\n[엔진] {why} — 짐작으로 되풀이하지 말고, 그 라이브러리·도구의 공식 문서 주소를 web_fetch 로 열어 확인한 뒤 고쳐라.")
NOTE_RECENT = ("[엔진] 이 물음은 학습 뒤에 바뀌었을 수 있는 사실(최신·버전·가격·출시 등)을 묻는다. 기억으로 답하지 말고 "
               "먼저 {tool} 로 확인한 뒤, 확인한 주소를 붙여 답하라.")


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
