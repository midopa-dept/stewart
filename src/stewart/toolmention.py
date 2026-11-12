"""판 117: 사람이 도구를 지목했을 때 실행기가 먼저 챙긴다(모델이 조용히 다른 길로 가거나 침묵하지 않게).

- 지목한 도구가 이 요청 목록에 있으면: "[엔진] 사람이 X 를 쓰라고 했다 — 그 도구부터" 를 사람 말 끝에.
- 꺼져 있으면(켬·끔 칩, 기본 끔): 모델에게 "꺼져 있다 — 우회하지 말고 사람에게 말하라" + 사람 화면에 "도구 칩에서 켜면 쓴다".
- 아예 없으면(설정 없음 등): 모델에게 "이 대화에는 없다 — 사람에게 말하라".
- 지목 꼴은 MENTIONS(도구 이름 → (사람이 읽는 이름, 정규식)).
- 끄기: STEWART_TOOL_MENTION=0. 실사용(chat·허브)만 — 평가 run 에는 붙이지 않는다.
"""

from __future__ import annotations

import os
import re

MENTIONS: dict[str, tuple[str, re.Pattern]] = {
    "web_search": ("웹 검색", re.compile(r"웹\s*(으로\s*)?검색|인터넷(에서|으로)?\s*(찾|검색)|구글링|검색\s*엔진|\bweb_search\b|\bweb\s*search\b", re.I)),
    "web_fetch": ("웹 페이지 읽기", re.compile(r"\bweb_fetch\b|(주소|링크|URL|페이지)를?\s*(열어|읽어|가져와)", re.I)),
}

ON = "[엔진] 사람이 {label} 을(를) 쓰라고 했다. 다른 길(작업 폴더 찾기·셸)보다 그 도구({tool})부터 쓴다."
OFF = ("[엔진] 사람이 {label} 을(를) 말했는데 그 도구({tool})는 지금 꺼져 있다(허브 머리의 도구 칩으로 켠다). "
       "셸이나 다른 도구로 우회하지 말고, 사람에게 '{label} 이(가) 꺼져 있어 못 한다 — 칩을 켜 달라'고 먼저 말하라.")
ABSENT = ("[엔진] 사람이 {label} 을(를) 말했는데 이 대화에는 그 도구({tool})가 없다{why}. "
          "셸이나 다른 도구로 우회하지 말고, 사람에게 그렇게 먼저 말하라.")
SCREEN = "  ({label} 도구가 꺼져 있다 — 허브 머리의 도구 칩에서 켜면 쓴다)"


def enabled(env=None) -> bool:
    return (os.environ if env is None else env).get("STEWART_TOOL_MENTION", "1") != "0"


def notes(text: str, visible: set, toggled_off: set, why: dict | None = None) -> tuple[str, list[str]]:
    """(모델에게 붙일 글, 사람 화면 줄들). 지목이 없으면 ("", [])."""
    out, screen = [], []
    for tool, (label, rx) in MENTIONS.items():
        if not rx.search(text or ""):
            continue
        if tool in visible:
            out.append(ON.format(label=label, tool=tool))
        elif tool in toggled_off:
            out.append(OFF.format(label=label, tool=tool))
            screen.append(SCREEN.format(label=label))
        else:
            w = (why or {}).get(tool)
            out.append(ABSENT.format(label=label, tool=tool, why=f"({w})" if w else ""))
    return ("\n\n" + "\n".join(out)) if out else "", screen
