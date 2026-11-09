"""끝내기 앞뒤의 자기 점검 손잡이(모두 기본 끔). 프로필 칸이 켜고, 같은 이름의 환경 변수(0|1)가 이긴다.

| 손잡이 | 프로필 칸 | 환경 변수 | 하는 일 |
|---|---|---|---|
| 끝내기 전 요구 대조 | review_on_stop | STEWART_REVIEW_ON_STOP | 파일을 바꾼 실행이 끝내기 전 확인을 통과(또는 확인 없음)하면, 한 번만 요구를 하나씩 대조하게 돌려준다 |
| 되묻기 판단 받침 | ask_check | STEWART_ASK_CHECK | 첫 쓰기·셸 바꿈 호출 직전 한 번, 모순·빠진 값·되돌리기 어려운 선택이 있으면 묻게 한다 |
| 회귀 기준선 | baseline_diff | STEWART_BASELINE_DIFF | 시작 때 확인 명령을 한 번 돌려 실패하던 시험을 적고, 끝내기 전 확인을 새로 깬 것·처음부터 실패하던 것으로 나눈다 |
| 할 일 목록 확인 | todo_check | STEWART_TODO_CHECK | todo_write 를 쓴 실행에서 (가) 남은 할 일이 있는데 최종 답을 내면 한 번 돌려주고, (나) todo_write 없이 todo_remind_every 단계가 지나면 남은 할 일을 상기 |
"""

from __future__ import annotations

import os
import re

ENVS = {"review_on_stop": "STEWART_REVIEW_ON_STOP", "ask_check": "STEWART_ASK_CHECK", "baseline_diff": "STEWART_BASELINE_DIFF",
        "todo_check": "STEWART_TODO_CHECK"}

REVIEW = ("[엔진] 끝내기 전 한 번 점검: 과제 글을 요구 하나씩으로 나눠 적고, 각 요구를 무엇(실행한 명령·시험 출력)으로 "
          "확인했는지 한 줄씩 적어라. 확인 못 한 요구는 지금 작은 실행이나 임시 시험으로 확인하고, 틀리면 고쳐라. "
          "임시 시험 파일은 끝나면 지우거나 임시 폴더에 둔다. 다 확인됐으면 최종 보고를 다시 써라.")


def flags(profile, env=None) -> dict:
    """{이름: bool} 와 출처. 잘못된 env 값은 무시하고 ignored 에 남긴다."""
    env = os.environ if env is None else env
    out, src, bad = {}, {}, []
    for name, var in ENVS.items():
        on = getattr(profile, name, None) is True
        s = "profile" if on else "off"
        v = env.get(var)
        if v in ("0", "1"):
            on, s = v == "1", "env"
        elif v is not None:
            bad.append(var)
        out[name], src[name] = on, s
    return {**out, "source": src, "ignored": bad}

ASK_CHECK = ("[엔진] 이 호출은 아직 실행하지 않았다. 바꾸기 전에 한 번 점검: 과제에 서로 어긋나는 요구, 꼭 필요한데 빠진 값, "
             "되돌리기 어려운 선택(지우기·작업 폴더 밖 변경·두 해석 중 하나를 골라야 함)이 있을 때만 ask_user 로 하나만 물어라. "
             "그런 것이 없으면 묻지 말고 같은 호출을 다시 해서 진행하라.")
ASK_HOLD = "[엔진] 점검 뒤에 다시 부른다 — 이 호출도 실행하지 않았다."
WRITE_TOOLS = frozenset({"write_file", "edit_file", "multi_edit", "notebook_edit"})


def changes(name: str, args: dict, bd, cwd: str | None) -> bool:
    """쓰기 도구이거나, 셸 명령에서 쓰기 꼴(bounds 의 감지)이 보이면 True."""
    if name in WRITE_TOOLS:
        return True
    if name == "run_shell":
        return bool(bd.shell_writes(str(args.get("command") or ""), cwd))
    return False

_UNITTEST = re.compile(r"^(?:FAIL|ERROR): (\S+) \(([\w.]+)\)", re.M)
_PYTEST = re.compile(r"^(?:FAILED|ERROR) (\S+::\S+)", re.M)


def failing_tests(output: str, exit_code) -> list[str] | None:
    """실패한 시험 이름(unittest·pytest 꼴). 통과면 []. 실패했는데 이름을 못 읽으면 None(모름 — 수집 오류 등)."""
    if exit_code == 0:
        return []
    ids = set()
    for name, where in _UNITTEST.findall(output or ""):
        ids.add(where if where.endswith("." + name) else f"{where}.{name}")
    ids |= set(_PYTEST.findall(output or ""))
    return sorted(ids) if ids else None


def baseline_split(chk: dict, base: list[str] | None) -> dict | None:
    """끝내기 전 확인 결과를 기준선과 나눈다. 둘 중 하나라도 이름을 못 읽었으면 None(전처럼)."""
    cur = chk.get("failing")
    if base is None or cur is None:
        return None
    b, c = set(base), set(cur)
    return {"new": sorted(c - b), "still": sorted(c & b), "fixed": sorted(b - c)}


def baseline_note(split: dict) -> str:
    def lst(xs):
        return ", ".join(xs[:20]) + (f" 외 {len(xs) - 20}개" if len(xs) > 20 else "") if xs else "없음"
    return (f"\n\n[엔진] 시작 때 기준선과 견주면 — 새로 깨진 시험: {lst(split['new'])} / "
            f"처음부터 실패하던 시험(과제와 상관없으면 그대로 둔다): {lst(split['still'])}")


def pending_todos(todos: list) -> list[str]:
    return [t.get("content", "") for t in todos or [] if t.get("status") != "done"]


def _short_list(xs: list[str], n: int = 5) -> str:
    return "; ".join(x[:80] for x in xs[:n]) + (f" 외 {len(xs) - n}개" if len(xs) > n else "")


def todo_final(pending: list[str]) -> str:
    return (f"[엔진] 할 일 목록에 끝나지 않은 항목이 {len(pending)}개 있다: {_short_list(pending)}. 남은 일을 하라. "
            "이미 끝냈으면 todo_write 로 done 으로 바꾸고, 하지 않기로 했으면 그 까닭을 최종 보고에 적어라.")


def todo_remind(pending: list[str]) -> str:
    return (f"[엔진] 할 일 목록 상기: 남은 것 {len(pending)}개 — {_short_list(pending, 3)}. "
            "진행에 맞게 todo_write 로 상태를 고쳐라.")
