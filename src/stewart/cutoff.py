"""생성 상한(max_tokens)에서 잘린 응답 다루기(U2-1).

- 잘린 툴콜: 서버는 인자 JSON 이 중간에서 끊긴 툴콜을 finish_reason=length 로 돌려준다(llama-server 실측). 실행하지 않고
  "잘렸다, 나눠 써라" 를 도구 결과로 돌려준다(전에는 "인자 JSON 파싱 실패" 만 알려 모델이 같은 긴 내용을 다시 쓰다 또 잘렸다).
  기록에 남은 깨진 인자는 보낼 때 backend.safe_messages 가 "{}" 로 바꾼다(안 바꾸면 다음 요청마다 서버가 HTTP 500).
- 잘린 글 답(chat): 툴콜 없이 상한에서 끊긴 답은 최종 답으로 받지 않고 "끊긴 데부터 이어 써라" 로 차례당 CONTINUE_MAX 번까지 잇는다.
"""

from __future__ import annotations

CONTINUE_MAX = 2

CUT_NOTE = ("[엔진] 응답이 생성 상한(max_tokens {mt})에 닿아 이 도구 호출({name})의 인자가 {n}자에서 끊겼다. 실행하지 않았다. "
            "한 번에 이만큼 쓰지 마라: 긴 파일은 앞부분을 write_file 로 쓰고 나머지를 write_file(append=true) 로 몇 번에 나눠 이어 붙이거나, "
            "작은 파일 여럿으로 나눠 써라. 긴 코드를 run_shell 명령(python3 -c, heredoc)에 넣지 말고 파일로 쓴 뒤 실행하라.")
CONTINUE_NOTE = ("[엔진] 답이 생성 상한(max_tokens {mt})에 닿아 중간에서 끊겼다. 앞 글을 되풀이하지 말고 끊긴 자리부터 이어 써라. "
                 "도구를 쓰려던 것이면 지금 도구를 불러라.")


def limit_of(profile, backend) -> str:
    v = getattr(profile, "max_tokens", None) or (backend.params or {}).get("max_tokens")
    return str(v) if v else "서버 기본값"


def cut_note(profile, backend, name: str, raw_args: str) -> str:
    return CUT_NOTE.format(mt=limit_of(profile, backend), name=name, n=len(raw_args or ""))
