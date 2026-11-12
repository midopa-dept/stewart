"""반복 감지(run·chat 공통).

같은 호출(도구 이름 + 인자)이 같은 결과로 되풀이되면 단계별로 대응한다.
- 3번: 도구 결과 끝에 알림 / 5번: 강한 알림 / 6번: 멈춤 신호(run 은 도구 없이 최종 보고, chat 은 사람에게 묻기)
- 성공한 호출도 센다(실패 반복은 engine REPEAT_* 가 따로 센다).
- 결과 비교 전에 잡음만 지운다: 소요 시간("in 0.002s", "1.23초" 꼴)·타임스탬프·임시 경로·pid.
  개수·종료 코드·결과 글은 그대로 비교한다("failures=5" 와 "failures=4" 는 다르다).
- 두 반복 사이에 파일을 바꿀 수 있는 다른 호출(쓰기 도구, 다른 run_shell 등 읽기 도구 목록에 없는 것)이 있었으면
  그 앞의 것은 세지 않는다(고치며 시험을 다시 돌리는 정상 진행).
- 알림은 이번 도구 결과 끝에만 붙인다(앞 메시지를 고치지 않아 prefix cache 유지).
"""

from __future__ import annotations

import hashlib
import re

from .modes import READ_TOOLS

WINDOW = 20                 # 최근 이만큼의 호출 안에서 센다
NUDGE, STRONG, STOP = 3, 5, 6
REFLECT_AT = 4            # chat: 같은 호출·같은 결과가 이만큼이면 사람에게 묻기 전에 되짚기(U2-8)
EXEMPT = frozenset({"shell_output", "todo_read", "todo_write"})   # 기다리며 묻기·목록 갱신은 반복으로 치지 않는다
READ_STREAK_NUDGE = (6, 12)  # 읽기만 한 단계가 이만큼 이어지면 알림(run)
STEPS_LEFT_NUDGE = 5         # 남은 단계가 이만큼일 때 한 번 알림(run)

_NOISE = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<시각>"),
    (re.compile(r"\b\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<시각>"),
    (re.compile(r"\bin \d+(?:\.\d+)?\s?(?:s|ms|sec|seconds)\b"), "in <시간>"),
    (re.compile(r"\b\d+\.\d+\s?(?:s|ms|sec|secs|seconds)\b"), "<시간>"),
    (re.compile(r"\d+(?:\.\d+)?\s?초"), "<시간>"),
    (re.compile(r"/(?:private/)?(?:var/folders|tmp)/[^\s'\"),:]+"), "<임시경로>"),
    (re.compile(r"\btmp[A-Za-z0-9_-]{6,}"), "<임시>"),
    (re.compile(r"\bpid[ =:]?\s?\d+", re.I), "pid <n>"),
]


def normalize(result: str) -> str:
    for pat, rep in _NOISE:
        result = pat.sub(rep, result)
    return result


def _digest(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()


def changes_files(name: str, ok: bool) -> bool:
    """파일을 바꿀 수 있는 호출인가. 읽기 도구 목록 밖이면 그렇다고 본다. 셸은 실패해도 무언가 바꿨을 수 있다."""
    if name == "delegate":   # 작업자가 파일을 바꿨을 수 있다(imp6 합치기)
        return True
    return name not in READ_TOOLS and (ok or name == "run_shell")


EARLY_NOTE = "[엔진] 바로 앞 같은 호출과 결과가 같다(이미 받은 결과다). 다시 부를 까닭이 없으면 위 결과로 다음 걸음을 가라."
NOTE = ("[엔진] 같은 호출을 {n}번 했고(사이에 파일을 바꾼 다른 호출 없음) 결과도 같았다. 새로 알게 된 것이 없다. "
        "같은 호출을 되풀이하지 말고 다음 일(고치기, 다른 확인, 최종 보고)로 넘어가라.")
STRONG_NOTE = ("[엔진] 같은 호출·같은 결과가 {n}번이다. 한 번 더 되풀이하면 엔진이 도구를 빼고 최종 보고를 받는다. "
               "다음 응답은 도구 없이, 지금까지 한 일과 남은 일을 최종 보고로 써라.")
FINAL_PROMPT = ("[엔진] 같은 호출을 결과가 같은 채로 {n}번 되풀이해 도구를 뺐다. 더 호출하지 말고, "
                "지금까지 한 일·확인한 것·남은 일을 최종 보고로 써라. "
                "실제로 한 호출 횟수와 그 결과만 사실대로 적어라. 하지 않은 일을 했다고 쓰지 마라.")
# chat 이 사람 없이(auto·--once) 되풀이·연속 잘림으로 멈출 때 도구 없이 한 번 받는 마지막 답(판 122 — run 의 FINAL_PROMPT 와 같은 뜻)
# {why} 예: "같은 호출을 결과가 같은 채로 6번 되풀이해"
CHAT_FINAL_PROMPT = ("[엔진] {why} 이 차례를 여기서 마친다. 도구는 더 부를 수 없다. "
                     "지금까지 받은 결과만으로 처음 물음에 답하라. 확인하지 못한 것은 확인하지 못했다고, 답을 모르면 모른다고 써라. "
                     "하지 않은 호출이나 보지 않은 결과를 지어내지 마라.")


class LoopGuard:
    def __init__(self, window: int = WINDOW):
        self.window = window
        self.recent: list[tuple[str, str, str, bool]] = []   # (서명, 결과 요약, 이름, 파일을 바꿀 수 있음)
        self.notes = 0
        self.read_streak = 0

    def reset(self) -> None:
        self.recent.clear()
        self.read_streak = 0

    def observe(self, name: str, raw_args: str, ok: bool, result: str) -> int:
        """호출 하나를 기록하고, 세는 대상이면 '같은 호출·같은 결과' 횟수(이번 포함)를 돌려준다. 아니면 0."""
        sig = name + "\0" + raw_args
        res = _digest(normalize(result))
        same = 0
        if name not in EXEMPT:
            same = 1
            for s, r, n, mod in reversed(self.recent[-self.window:]):
                if s == sig:
                    if r == res:
                        same += 1
                    else:
                        break        # 같은 호출인데 결과가 바뀌었다 → 그 앞은 세지 않는다
                elif mod:
                    break            # 사이에 파일을 바꿀 수 있는 다른 호출 → 그 앞은 세지 않는다
        self.recent.append((sig, res, name, changes_files(name, ok)))
        del self.recent[:-self.window * 2]
        return same

    def note(self, same: int) -> str | None:
        """횟수에 맞는 알림 글(도구 결과 끝에 붙인다). 없으면 None."""
        if same >= STRONG:
            self.notes += 1
            return STRONG_NOTE.format(n=same)
        if same >= NUDGE:
            self.notes += 1
            return NOTE.format(n=same)
        return None

    def step_names(self, names: set[str]) -> str | None:
        """한 단계에서 부른 도구 이름들. 읽기만 하는 단계가 이어지면 알림 글."""
        self.read_streak = self.read_streak + 1 if names and names <= (READ_TOOLS - {"delegate"}) else 0
        if self.read_streak in READ_STREAK_NUDGE:
            self.notes += 1
            return (f"[엔진] 읽기·찾기만 {self.read_streak}단계 이어졌다. 필요한 것을 읽었으면 고치거나 시험을 돌려라. "
                    "찾는 것이 안 나오면 찾는 방법(낱말, 경로)을 바꿔라.")
        return None
