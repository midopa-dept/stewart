"""stewart 사용자 설정 폴더 쓰기 통제.

- 지키는 곳: ~/.config/stewart/ 아래 전부, STEWART_SKILLS_DIR·STEWART_PROFILES_DIR 가 가리키는 곳.
  (stewart 에는 아직 사용자 전역 기억 파일이 없다. 생기면 여기에 더한다.)
- 쓰기 도구(write_file·edit_file·multi_edit·notebook_edit)가 그 안에 쓰려 하면
  · chat: 모드와 관계없이(auto·edits 포함) 매번 y/n 확인. 작업자([작업자 N])도 같다.
  · run(무인): 거부하고 이유를 도구 결과로 돌려준다.
- 이유: 바깥 자료(web_fetch 등)에 속은 모델이 전역 스킬·프로필에 써 넣으면 다음 세션마다 따라붙는 지속 주입이 되고,
  프로필은 도구·확인 설정이라 권한 상승이 된다.
- 경로는 realpath 로 본다(심링크·.. 로 돌려도 잡힌다). 없는 파일은 있는 위쪽까지 풀어 본다.
- 프로젝트 안 .stewart/skills 는 일반 작업 폴더 파일처럼 둔다(git diff 로 보이고 그 저장소에서만 쓰인다).
- 한계: run_shell 로 쓰는 것은 이 장치로 막지 않는다(셸은 모드대로 묻는다).
"""

from __future__ import annotations

import os
from pathlib import Path

WRITE_TOOLS = frozenset({"write_file", "edit_file", "multi_edit", "notebook_edit"})
ASK_PROMPT = ("    ⚠ 다음 세션부터 영향을 주는 stewart 설정 파일이다({path}). "
              "스킬·프로필은 다음 실행의 지시문·도구·확인을 바꾼다. 쓸까? [y/n/모델에 할 말] ")
RUN_DENY = ("stewart 사용자 설정 폴더({path})는 무인 실행에서 쓰지 않는다. 그 안의 스킬·프로필은 다음 세션의 지시문·도구·확인을 "
            "바꾸기 때문이다. 바꿔야 하면 최종 보고에 무엇을 어떻게 바꿀지 적어 사람에게 맡겨라.")


def protected_dirs() -> list[Path]:
    raw = ["~/.config/stewart", os.environ.get("STEWART_SKILLS_DIR"), os.environ.get("STEWART_PROFILES_DIR")]
    out: list[Path] = []
    for r in raw:
        if r:
            p = Path(os.path.realpath(os.path.expanduser(r)))
            if p not in out:
                out.append(p)
    return out


def hit(workdir: Path, name: str, args: dict) -> Path | None:
    """쓰기 도구의 대상이 지키는 폴더 안이면 그 실제 경로, 아니면 None."""
    if name not in WRITE_TOOLS or not args.get("path"):
        return None
    q = Path(os.path.expanduser(str(args["path"])))
    q = q if q.is_absolute() else Path(workdir) / q
    real = Path(os.path.realpath(q))
    for d in protected_dirs():
        if real == d or d in real.parents:
            return real
    return None
