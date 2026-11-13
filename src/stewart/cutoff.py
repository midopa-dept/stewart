"""생성 상한(max_tokens)에서 잘린 응답 다루기(U2-1).

- 잘린 툴콜: 서버는 인자 JSON 이 중간에서 끊긴 툴콜을 finish_reason=length 로 돌려준다(llama-server 실측). 실행하지 않고
  "잘렸다, 나눠 써라" 를 도구 결과로 돌려준다(전에는 "인자 JSON 파싱 실패" 만 알려 모델이 같은 긴 내용을 다시 쓰다 또 잘렸다).
  기록에 남은 깨진 인자는 보낼 때 backend.safe_messages 가 "{}" 로 바꾼다(안 바꾸면 다음 요청마다 서버가 HTTP 500).
- 잘린 글 답(chat): 툴콜 없이 상한에서 끊긴 답은 최종 답으로 받지 않고 "끊긴 데부터 이어 써라" 로 잇는다. 상한 CONTINUE_MAX 는
  연달아 이은 수다(도구를 부른 걸음에서 다시 0). 연달아 이어도 또 끊기면 끝 이유는 cut_answer 다(answered·completed 아님, 판 131).
- 깨진 글(판 131): 서버가 바이트를 글자로 못 맞춘 자리(U+FFFD)가 가득한 출력. 문맥에도 답에도 넣지 않는다(garbled).
"""

from __future__ import annotations

import re

CONTINUE_MAX = 2
GARBLED_MAX = 2     # 연달아 깨진 글 — 이만큼까지는 버리고 다음 걸음으로, 넘으면 차례를 끝낸다(끝 이유 garbled_output)
GARBLED_MIN = 20    # 바꿈 글자(U+FFFD) 수 하한
GARBLED_RATIO = 0.005   # 글자 수에 대한 바꿈 글자 비율 하한
GARBLED_RUN = 1000  # 공백 아닌 같은 글자가 이만큼 이어지면 깨진 글
_RUN_RE = re.compile(r"(\S)\1{%d,}" % (GARBLED_RUN - 1))

CUT_NOTE = ("[엔진] 응답이 생성 상한(max_tokens {mt})에 닿아 이 도구 호출({name})의 인자가 {n}자에서 끊겼다. 실행하지 않았다. "
            "한 번에 이만큼 쓰지 마라: 긴 파일은 앞부분을 write_file 로 쓰고 나머지를 write_file(append=true) 로 몇 번에 나눠 이어 붙이거나, "
            "작은 파일 여럿으로 나눠 써라. 긴 코드를 run_shell 명령(python3 -c, heredoc)에 넣지 말고 파일로 쓴 뒤 실행하라.")
GARBLED_NOTE = ("[엔진] 앞 응답이 깨진 글자(뜻 없는 여러 문자가 뒤섞인 글)로 나와 버렸다. 그 글은 문맥에 넣지 않았다. "
                "지금 아는 것으로 바로 도구를 부르거나 최종 답을 써라.")
CONTINUE_NOTE = ("[엔진] 답이 생성 상한(max_tokens {mt})에 닿아 중간에서 끊겼다. 앞 글을 되풀이하지 말고 끊긴 자리부터 이어 써라. "
                 "도구를 쓰려던 것이면 지금 도구를 불러라.")


def limit_of(profile, backend) -> str:
    v = getattr(profile, "max_tokens", None) or (backend.params or {}).get("max_tokens")
    return str(v) if v else "서버 기본값"


def cut_note(profile, backend, name: str, raw_args: str) -> str:
    return CUT_NOTE.format(mt=limit_of(profile, backend), name=name, n=len(raw_args or ""))


def garbled(text: str | None) -> bool:
    """깨진 출력인가. 서버가 생성 토큰의 바이트를 글자로 맞추지 못하면 U+FFFD(바꿈 글자)가 섞인다.
    실측(판 131): 한 서버 차선이 생각 끔 긴 생성에서 낸 깨진 걸음 17개는 모두 바꿈 글자가 글의 1.5% 이상(수백 개)이었고,
    다른 차선의 정상 걸음 3,790개(본문·생각)는 모두 0개, 한국어·코드가 섞인 문서 조각 596개는 많아야 1개였다.
    같은 차선의 다른 깨진 꼴은 느낌표 하나만 상한까지 되풀이한 글이었다(8,191개). 정상 걸음·문서에는 같은 글자가 200번 넘게
    이어진 곳이 없었다.
    여러 문자 체계가 섞인 비율은 한국어·영어가 섞인 정상 글과 겹쳐 쓰지 않는다."""
    if not text:
        return False
    n = text.count("\ufffd")
    return (n >= GARBLED_MIN and n >= GARBLED_RATIO * len(text)) or (len(text) >= GARBLED_RUN and _RUN_RE.search(text) is not None)
