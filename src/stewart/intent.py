"""말만 하고 멈춤 되돌리기(U2-3): 사람이 일을 시켰는데 모델이 도구를 한 번도 안 부르고 "…하겠습니다" 로 앞으로 할 일만 말한 채 끝낸 답.

- 좁은 조건(모두 맞아야): ① 이번 차례에 도구 호출 0번 ② 사람 말이 일을 시킨 꼴(시키는 말끝이 있고, 묻는 말이 아님)
  ③ 답의 마지막 문장이 앞으로 할 일(…하겠습니다·…할게요·…하겠다) ④ 답이 지난 일을 했다고 말하지 않음(…했습니다·고쳤습니다 등이 없음).
- 차례당 한 번. 끄기: STEWART_INTENT_NUDGE=0.
- 되돌리는 글은 사람 메시지로 붙인다(앞 메시지를 고치지 않음).
"""

from __future__ import annotations

import os
import re

ENV = "STEWART_INTENT_NUDGE"

NOTE = ("[엔진] 할 일을 말로만 적고 도구를 부르지 않은 채 끝냈다. 말한 그 첫 단계를 지금 도구로 실행하라. "
        "정말 사람에게 물어야 할 것이 있으면 ask_user 로 물어라.")

# 일을 시키는 말끝(…해라·…하라·…해 줘·…해주세요·…하세요·…할 것·…고쳐 등)
_WORK = re.compile(r"(해라|하라|해\s*줘|해\s*주세요|해주세요|하세요|해\s*봐|할\s*것|고쳐|만들어|써\s*줘|써라|돌려|확인해|정리해|조사해|"
                   r"찾아|바꿔|지워|옮겨|추가해|넣어|빼|띄워|올려|실행해|설치해)[^가-힣]*$|"
                   r"(고치고|만들고|돌리고|확인하고|쓰고|조사해|정리하고)")
# 묻는 말(설명·의견을 바람) — 일 시킴으로 보지 않는다
_ASK = re.compile(r"(\?|？|뭐야|뭔가요|무엇인가|무엇이야|알려\s*줘|알려주세요|설명해|어떻게\s*(하|되|생각)|왜\s|인가요|일까|할까|될까|맞아|맞나요)")
_FUTURE_END = re.compile(r"(겠습니다|겠어요|겠다|할게요|할게|해\s*볼게요|하려고\s*합니다|하도록\s*하겠습니다|진행합니다|시작합니다)[.!…\s]*$")
_PAST = re.compile(r"(했습니다|했어요|했다|고쳤|만들었|바꿨|썼습니다|끝냈|마쳤|완료했|통과했|확인했|돌렸)")


def on(env=None) -> bool:
    return (os.environ if env is None else env).get(ENV) != "0"


def _last_sentence(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    parts = [p.strip() for p in re.split(r"(?<=[.!?。])\s+|\n+", t) if p.strip()]
    return parts[-1] if parts else ""


def _sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[.!?。])\s+|\n+", text or "") if p.strip() and not p.strip().startswith("[")]


def is_work_request(user_text: str) -> bool:
    ss = _sentences(user_text)
    if not ss or _ASK.search(ss[-1]):
        return False
    return any(_WORK.search(x) or re.search(r"(마라|말\s*것)[^가-힣]*$", x) for x in ss)


def intent_only(user_text: str, answer: str, tool_calls_this_turn: int) -> bool:
    if tool_calls_this_turn or not (answer or "").strip():
        return False
    if not is_work_request(user_text):
        return False
    last = _last_sentence(answer)
    if not _FUTURE_END.search(last):
        return False
    return not _PAST.search(answer)
