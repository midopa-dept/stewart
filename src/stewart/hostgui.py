"""판 117: 이 컴퓨터의 화면(앱·브라우저·창)을 여는·조작하는 셸 명령 — 기본 거절.

- 셸 명령이 이 컴퓨터의 앱·브라우저·창을 열거나 조작하면(macOS open·osascript·automator·shortcuts run·screencapture·cliclick,
  리눅스 xdg-open·gio open·xdotool·wmctrl, 윈도우 start·Start-Process·explorer, 파이썬 webbrowser 등) 실행하지 않고 그 까닭을 돌려준다.
- 이 컴퓨터를 조작하는 기능은 따로 켠다: 프로필 `host_gui = true` 또는 STEWART_HOST_GUI=1. 켜져 있어도 대화(chat·허브)에서
  명령마다 "이 컴퓨터의 …를 연다"를 밝혀 사람에게 묻고, 승인한 그 명령 한 번만 돈다. 사람에게 묻지 못하는 길(작업자·run)은 늘 거절.
- 승인 규칙(늘 허용)·auto 모드로도 건너뛰지 않는다.
"""

from __future__ import annotations

import os
import re

_HEAD = r"(?:^|[;&|(`\n]|\$\()\s*(?:(?:sudo|env|nohup|exec|command|time|nice)\s+(?:-\S+\s+)*)*"
RX = re.compile(
    _HEAD + r"(?:/usr/bin/)?(?P<w>open|osascript|automator|screencapture|cliclick|xdg-open|gnome-open|kde-open|xdotool|ydotool|wmctrl|"
    r"explorer(?:\.exe)?|nircmd(?:\.exe)?|start)(?=\s|$)"
    r"|" + _HEAD + r"(?P<w2>shortcuts\s+run|gio\s+open|cmd(?:\.exe)?\s+/c\s+start)\b"
    r"|(?P<w3>Start-Process|\[System\.Diagnostics\.Process\]::Start|Invoke-Item)\b"
    r"|(?P<w4>-m\s+webbrowser\b|import\s+webbrowser|webbrowser\.open)",
    re.I)
EXTRA_HINT = ""

REFUSE = ("[실행 안 함] 이 명령({w})은 이 컴퓨터의 앱·브라우저·창을 열거나 조작한다. 이 컴퓨터를 조작하는 기능은 꺼져 있어(기본) "
          "실행하지 않았다. 셸로 우회하지 말고, 사람에게 이 사실을 짧게 말하라.{hint}")
ASK = "이 명령은 **이 컴퓨터**의 앱·브라우저·창을 열거나 조작합니다({w}):\n  {cmd}\n이 컴퓨터에서 실행할까요? (y = 이 명령 한 번만)"


def hit(command: str) -> str:
    """걸린 낱말(없으면 ""). 따옴표 안 글(echo "open …")까지 완벽히 가르지는 않는다 — 넓게 잡고 거절한다."""
    m = RX.search(command or "")
    if not m:
        return ""
    return next(g for g in m.groups() if g).strip()


def enabled(profile=None, env=None) -> bool:
    env = os.environ if env is None else env
    v = env.get("STEWART_HOST_GUI")
    if v in ("0", "1"):
        return v == "1"
    return bool((getattr(profile, "raw", None) or {}).get("host_gui"))


def refuse(word: str) -> str:
    return REFUSE.format(w=word, hint=(" " + EXTRA_HINT) if EXTRA_HINT else "")
