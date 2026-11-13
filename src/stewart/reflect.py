"""되짚기(E-2): 실패가 이어져 차례(chat)·실행(run)을 끝내기 직전에, 도구 없이 한 번 원인과 다음 방법을 쓰게 한다.

- 걸리는 자리: consecutive_errors(모두 실패한 단계가 max_consecutive_errors 번) 또는 repeated_failure(같은 호출 6번 실패) 문턱.
- 한 번 쓰면(reflect_max): 엔진이 센 실패를 붙인 지시 → 도구 없는 모델 호출 한 번 → 그 글을 대화에 두고 이어 가기 지시.
  연속 실패 수는 0 으로, 이미 3번 넘게 실패한 같은 호출은 한 번만 더 실패해도 끝나게 낮춘다. 또 문턱이면 지금처럼 끝낸다.
- 건너뛰는 때: 꺼짐, 횟수를 다 씀, 문턱에 닿은 실패가 모두 거부(사람·규칙·경계), 반복 감지 마무리·형식 강제 중.
- 켜고 끄기: 프로필 reflect_on_fail(없으면 chat·허브 켬, run 끔) < 환경 변수 STEWART_REFLECT=0|1.
  reflect_max(기본 1, run 은 실행당·chat 은 차례당), reflect_max_tokens(기본 1200, 되짚기는 생각 끔으로 보낸다 — 판 122).
- 메시지는 뒤에 붙이기만 한다(앞 메시지를 고치지 않아 prefix cache 유지). 되짚기는 단계로 세지 않고 모델 호출 1번으로 따로 센다.
"""

from __future__ import annotations

import hashlib
import os

from .backend import BackendError
from .cutoff import garbled

ENV = "STEWART_REFLECT"
ENV_MAX = "STEWART_REFLECT_MAX"
DEFAULT_MAX = 1            # run(평가) 실행당
CHAT_MAX = 2               # chat·허브 차례당(U2-8 실사용: 스스로 되짚는 횟수 상한, 기본 2)
DEFAULT_MAX_TOKENS = 1200
FACTS_N = 5
ARGS_CHARS = 120
LINE_CHARS = 160
RECENT_KEEP = 30

WHY = {"consecutive_errors": "도구 호출이 모두 실패한 단계가 이어짐",
       "repeated_failure": "똑같은 호출이 거듭 실패함",
       "repeated_call": "같은 호출을 결과가 같은 채로 되풀이함(결과는 이미 받았다 — 새로 알게 된 것이 없다)",
       "intent_only": "할 일을 말로만 하고 도구를 부르지 않은 채 두 번 멈춤",
       "same_error": "다른 명령인데 같은 오류가 거듭 나옴(같은 계열을 또 돌려도 새로 알 것이 없다)",
       "length_streak": "생각이 단계 생성 상한에 연달아 닿아 답(도구 호출·글)을 내지 못함"}
LABEL = {"consecutive_errors": "실패가 이어짐", "repeated_failure": "같은 실패 되풀이",
         "repeated_call": "같은 호출 되풀이", "intent_only": "말만 하고 멈춤", "same_error": "같은 오류 되풀이",
         "length_streak": "생각이 연달아 잘림"}

CHAT_PROMPT = ("[엔진] 이대로면 이 차례가 막힌다(사유: {why}). 이번 답은 도구를 부르지 말고 글로만 되짚어라.\n"
          "엔진이 본 것(최근 것부터 아래로):\n{facts}\n"
          "첫 줄은 사람에게 하는 말 한두 문장으로(예: \"같은 명령을 세 번 불렀습니다. 종료 코드 문제였고 내용은 이미 받았으니 이 걸음은 건너뛰고 …부터 하겠습니다.\"). 그다음:\n"
          "1) 지금까지 한 것: 무엇을 했고 무엇이 나왔나(위 결과를 실제로 읽고 그 내용을 한두 줄로).\n"
          "2) 왜 막혔나: 왜 같은 결과·같은 실패가 나오는지 원인을 하나로 좁혀라. 가설마다 그것을 가르는 싼 확인 하나(오류 코드 풀이 명령·설정 한 줄 읽기 등)를 적고, 돌려 확인하기 전에는 \"추측\"이라고 적어라. 오류 코드·메시지의 뜻을 모르면 그것부터 찾는다.\n"
          "3) 무엇을 모르나: 다음으로 가려면 알아야 하는데 아직 모르는 것과, 그것을 짐작 말고 어느 도구로 알아낼지(읽기·검색·실행·웹).\n"
          "4) 다르게 할 길 2~3개: 앞과 다른 방법을 각각 한 줄로.\n"
          "5) 이 걸음이 목표에 꼭 필요한가: 건너뛰면 무엇이 안 되나. 꼭 필요하지 않으면 건너뛰고 본 목표로 간다(예: 읽을 까닭이 없는 파일을 읽으려던 걸음).\n"
          "6) 고른 길과 그 첫 호출. 앞에서 한 똑같은 호출을 다시 하는 안은 쓰지 마라(엔진이 막는다).\n"
          "   작업 폴더를 읽어도 알 수 없는 값이 모자라 막혔다면 6) 대신 사람에게 물을 것을 한 줄로 적어라.\n"
          "맨 끝 두 줄은 이 꼴 그대로(엔진이 읽어 지킨다):\n"
          "금지: <도구 이름> <다시 하지 않을 호출의 꼴, 예: run_shell cat .env>   (여럿이면 줄마다, 없으면 금지: 없음)\n"
          "다음: <도구 이름> <첫 호출의 인자 요지>\n"
          "30줄 안으로, 한국어로.")
CHAT_GO = ("[엔진] 되짚기를 마쳤다. 6)의 길로(\"다음:\" 줄) 이어 가라(물을 것을 적었으면 ask_user 로 물어라). "
      "앞에서 한 똑같은 호출과 \"금지:\" 에 적은 꼴은 엔진이 막는다(사람이 풀 수 있다).")
# run(평가) 은 옛 글 그대로(평가 동작을 바꾸지 않게)
PROMPT = ("[엔진] 실패가 이어져 이 차례를 끝내기 직전이다(사유: {why}). 이번 답은 도구를 부르지 말고 글로만 되짚어라.\n"
          "엔진이 센 실패(최근 것부터 아래로):\n{facts}\n"
          "1) 무엇이 실패했나: 위에서 같은 원인으로 묶이는 것끼리 한 줄씩.\n"
          "2) 왜: 원인을 하나로 좁혀라. 결과로 확인되지 않은 것은 \"추측\"이라고 적어라.\n"
          "3) 지금까지 해 본 방법과 왜 안 됐나.\n"
          "4) 다음에 다르게 할 것: 앞과 다른 방법 하나와 그 첫 호출. 같은 호출을 다시 하는 안은 쓰지 마라.\n"
          "   작업 폴더를 읽어도 알 수 없는 값이 모자라 막혔다면 4) 대신 사람에게 물을 것을 한 줄로 적어라.\n"
          "20줄 안으로, 한국어로.")
GO = ("[엔진] 되짚기를 마쳤다. 4)의 방법으로 이어 가라(물을 것을 적었으면 ask_user 로 물어라). "
      "앞에서 실패한 똑같은 호출을 다시 하면 엔진이 이 차례를 끝낸다.")
BLOCKED_NOTE = ("[엔진] 되짚기 뒤라 앞에서 되풀이한 똑같은 호출은 이 차례에 돌리지 않았다(결과는 앞과 같다). "
                "되짚기에 적은 다른 길로 이어 가라.")
FAILED_NOTE = "(되짚기 답을 받지 못했다: {why})"


def settings(profile, interactive: bool, env=None) -> dict:
    """{on, max, max_tokens, source, ignored}. interactive = chat·허브(기본 켬), 아니면 run(기본 끔)."""
    env = os.environ if env is None else env
    pv = getattr(profile, "reflect_on_fail", None)
    on, src = (pv, "profile") if pv is not None else (interactive, "default")
    ignored = None
    v = env.get(ENV)
    if v in ("0", "1"):
        on, src = v == "1", "env"
    elif v is not None:
        ignored = ENV
    mx = getattr(profile, "reflect_max", None) or (CHAT_MAX if interactive else DEFAULT_MAX)
    vm = env.get(ENV_MAX)
    if vm and vm.isdigit():
        mx = int(vm)
    return {"on": bool(on), "max": mx,
            "max_tokens": getattr(profile, "reflect_max_tokens", None) or DEFAULT_MAX_TOKENS,
            "source": src, "ignored": ignored}


def sig_of(tc: dict) -> str:
    return str(tc.get("name", "")) + "\0" + str(tc.get("raw_arguments", ""))


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()[:12]


def _one(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n - 1] + "…"


def _tail(result: str) -> str:
    body = str(result or "").split("\n\n[엔진]")[0]   # 엔진이 덧붙인 알림은 뺀다
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    return " / ".join(_one(l, LINE_CHARS) for l in lines[-2:]) or "(빈 결과)"


def facts(fail_count: dict, recent: list[dict], n: int = FACTS_N) -> str:
    """최근 실패 호출을 서명마다 하나씩(최근 것부터) n 개: 도구·인자 앞부분·실패 횟수·결과 끝 2줄."""
    out, seen = [], set()
    for tc in reversed(recent):
        s = sig_of(tc)
        if s in seen:
            continue
        seen.add(s)
        mark = " [거부됨]" if tc.get("denied") else ""
        out.append(f"- {tc.get('name')} {_one(tc.get('raw_arguments', ''), ARGS_CHARS)} — 실패 {fail_count.get(s, 1)}번{mark}: "
                   f"{_tail(tc.get('result', ''))}")
        if len(out) >= n:
            break
    return "\n".join(out) or "- (실패한 도구 호출 기록이 없다: 도구 호출 형식이 깨져 돌려보낸 단계일 수 있다)"


def facts_same(tc: dict, times: int) -> str:
    """같은 호출 되풀이: 그 호출과 (성공했던) 결과 끝 몇 줄."""
    body = str(tc.get("result") or "").split("\n\n[엔진]")[0]
    lines = [l for l in body.splitlines() if l.strip()]
    tail = "\n".join("    " + _one(l, LINE_CHARS) for l in lines[-8:]) or "    (빈 결과)"
    return (f"- {tc.get('name')} {_one(tc.get('raw_arguments', ''), ARGS_CHARS * 2)} — {times}번, 결과 같음"
            f"({'성공' if tc.get('ok') else '실패'}, {len(lines)}줄). 결과 끝:\n{tail}")


def all_denied(calls: list[dict]) -> bool:
    """문턱에 닿은 실패가 모두 거부(사람·규칙·경계)인가. 비었으면 아니다."""
    return bool(calls) and all(tc.get("denied") for tc in calls)


def skip_reason(cfg: dict, used: int, calls: list[dict], busy: str | None = None) -> str | None:
    """되짚기를 건너뛸 까닭(없으면 None)."""
    if not cfg["on"]:
        return "off"
    if used >= cfg["max"]:
        return "used_up"
    if busy:
        return busy
    if all_denied(calls):
        return "all_denied"
    return None


def lower_hot(fail_count: dict, nudge: int, stop: int) -> list[str]:
    """이미 nudge 번 넘게 실패한 호출은 한 번만 더 실패해도 stop 에 닿게 낮춘다. 낮춘 서명의 해시 목록."""
    hot = []
    for s, c in fail_count.items():
        if c >= nudge:
            fail_count[s] = stop - 1
            hot.append(_hash(s))
    return hot


def append_user(messages: list[dict], text: str) -> None:
    """사람 메시지로 붙인다. 마지막이 이번에 붙인(아직 보내지 않은) 사람 메시지면 그 뒤에 잇는다."""
    if messages and messages[-1].get("role") == "user" and isinstance(messages[-1].get("content"), str):
        messages[-1]["content"] += "\n\n" + text
    else:
        messages.append({"role": "user", "content": text})


def notool_kw(cfg: dict, kw: dict | None = None, backend=None, think_max: int | None = None) -> tuple[dict, int]:
    """도구 없는 한 번(되짚기·마지막 답)의 (키워드, 상한): 생각을 끄고 reflect_max_tokens 로 보낸다.
    생각을 켜 두면 상한이 생각에 다 먹혀 빈 답이 된다. 실물 검색 과제 문맥으로 재 보니 상한 1200 은 거의 늘 비었고,
    단계 생성 상한만큼 더해도 절반쯤 비면서 몇 배 느렸다. 생각 끔은 모두 답했다(판 122).
    생각을 끌 수 없는 백엔드(thinking_control = none)만 생각 몫(생각 상한, 없으면 백엔드 max_tokens)을 더한다."""
    cap = cfg["max_tokens"]
    if backend is not None and getattr(backend, "thinking_control", "chat_template") == "none":
        cap += think_max or int((getattr(backend, "params", None) or {}).get("max_tokens") or 0)
    return {**(kw or {}), "thinking": False}, cap


def call(chat_fn, backend, messages: list[dict], kw: dict, max_tokens: int) -> dict:
    """도구 없이 한 번 부른다. {ok, content, usage, elapsed_s, finish_reason, dropped_tool_calls, error}."""
    kw = {**kw, "max_tokens": max_tokens}
    try:
        resp, dt = chat_fn(backend, messages, None, **kw)
    except BackendError as e:
        return {"ok": False, "content": "", "usage": {}, "elapsed_s": 0.0, "finish_reason": None,
                "dropped_tool_calls": 0, "error": f"{e} {(e.body or '')[:200]}".strip()}
    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = (msg.get("content") or "").strip()
    bad = garbled(content)   # 판 131: 깨진 글은 답으로 받지 않는다(문맥·답 파일에 넣지 않게 내용은 비운다)
    err = "깨진 글" if bad else None if content else "빈 답"
    return {"ok": err is None, "content": "" if bad else content, "usage": resp.get("usage") or {}, "elapsed_s": dt,
            "finish_reason": choice.get("finish_reason"), "dropped_tool_calls": len(msg.get("tool_calls") or []),
            "error": err, **({"garbled_chars": len(content)} if bad else {})}


class State:
    """한 실행(run) 또는 한 차례(chat)의 되짚기 기록."""

    def __init__(self):
        self.used = 0
        self.log: list[dict] = []     # {trigger, at_step, ok, chars, completion_tokens, hot}
        self.skipped: list[dict] = []

    def summary(self, final_reason: str, steps_now: int) -> dict | None:
        if not self.log and not self.skipped:
            return None
        last = self.log[-1] if self.log else None
        return {"used": self.used, "log": self.log, "skipped": self.skipped,
                "steps_after": (steps_now - last["at_step"]) if last else None,
                "recovered": bool(last and last["ok"] and final_reason in ("completed", "answered", "asked")),
                "final_reason": final_reason}
