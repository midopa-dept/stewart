"""되묻기: ask_user 도구.

- run: 부르면 실행을 바로 끝낸다. stop_reason "asked", 최종 출력 = 질문 글 그대로(파일은 그 시점 그대로).
  v2 의 "되물었다" 판정은 최종 보고 글이 구체적 질문인지 + 파일을 하나도 바꾸지 않았는지다(종료 사유 이름은 보지 않는다).
- chat: 질문을 보여 주고 그 차례를 끝낸다(reason "asked"). 다음 사람 입력이 답이다.
- 도구 정의는 tools.py 가 아니라 여기 둔다. 프로필 tools 가 ["*"] 이거나 "ask_user" 를 적었을 때만 연다.
  media·organize 처럼 도구를 낱낱이 적은 프로필에는 생기지 않는다. 출력이 json 인 프로필에는 열지 않는다.
- 파일을 바꾸지 않으므로 modes 에서는 읽기 도구로 본다.
"""

from __future__ import annotations

import re

NAME = "ask_user"

DEFINITION = {"type": "function", "function": {
    "name": NAME,
    "description": "사람에게 묻고 멈춘다. 결과가 갈리는 모호함이나 꼭 필요한 값이 없을 때만, 파일을 바꾸기 전에 쓴다. 무엇이 모자라는지 짚어 묻는다.",
    "parameters": {"type": "object", "properties": {
        "question": {"type": "string", "description": "사람에게 할 질문(무엇이 모자라거나 부딪히는지, 무엇을 정해 주면 되는지)"},
        "options": {"type": "array", "items": {"type": "string"}, "description": "고를 수 있는 답(있으면)"},
    }, "required": ["question"]},
}}


def wanted(tools: list[str], output_format: str = "free") -> bool:
    return output_format != "json" and (tools == ["*"] or NAME in tools)


def other_tools(tools: list[str]) -> list[str]:
    """tools.definitions 에 넘길 이름(ask_user 를 뺀다)."""
    return [t for t in tools if t != NAME]


def question_text(args: dict) -> str:
    q = str(args.get("question") or "").strip()
    opts = [str(o).strip() for o in (args.get("options") or []) if str(o).strip()]
    if opts:
        q += "\n" + "\n".join(f"- {o}" for o in opts)
    return q


_ASK_ENDS = ("까요", "나요", "주세요", "주십시오", "주시겠어요", "주시면", "알려 달라", "정해 달라")
# 조건부 맺음말("…있으면 알려주세요", "필요하시면 말씀해 주세요")은 질문으로 보지 않는다
_CONDITIONAL = re.compile(r"(있으면|있으시면|필요하면|필요하시면|원하면|원하시면|궁금하면|궁금하시면|언제든)")
_SENT = re.compile(r"[^.!?？\n]+[.!?？]*")


def looks_like_question(text: str) -> bool:
    """최종 글의 끝 두 문장 가운데 조건부 맺음말이 아닌 질문·요청이 있는가(참고 신호 asked_text 용, 판정에 쓰지 않는다)."""
    sents = [x.strip() for x in _SENT.findall((text or "").strip()[-300:]) if x.strip()]
    for x in sents[-2:]:
        if _CONDITIONAL.search(x):
            continue
        core = x.rstrip("*`)\"'”’ .!")
        if x.rstrip("*`)\"'”’ ").endswith(("?", "？")) or core.endswith(_ASK_ENDS):
            return True
    return False


TOOL_RESULT = "사람에게 물었다. 작업을 멈추고 답을 기다린다."
AFTER_CHANGE = ("실행하지 않음: 이미 파일을 바꿨다. ask_user 는 파일을 바꾸기 전에만 쓴다. 묻지 말고 할 수 있는 데까지 마친 뒤, "
                "막힌 점(무엇이 모자라는지)을 최종 보고에 적어라.")
