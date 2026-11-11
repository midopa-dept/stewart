"""실행 모드(Claude Code 의 권한 모드에 해당). chat·run 공통 판단.

  auto   : 도구를 모두 바로 실행(지금까지의 기본, 권한 넓음)
  edits  : 파일 쓰기·고치기는 바로, 셸·그 밖의 바꾸는 도구는 사람에게 묻는다(편집 자동 허락)
  ask    : 읽기 도구 말고는 모두 묻는다(--confirm 과 같다)
  plan   : 읽기 도구만 실행하고 나머지는 거부한다. 모델은 고칠 계획만 쓴다. 사람이 모드를 바꾸면 실행에 들어간다

- 읽기 도구는 허용 목록(READ_TOOLS)이다. 목록에 없는 새 도구는 "바꾸는 도구"로 본다(plan 에서 거부, ask·edits 에서 물음).
- 무인 실행(run)에는 물을 사람이 없다. run 에서 쓰는 모드는 auto 와 plan 뿐이다(edits·ask 는 오류).
"""

from __future__ import annotations

from .config import ConfigError

MODES = ("auto", "edits", "ask", "plan")
READ_TOOLS = frozenset({"read_file", "view_image", "list_dir", "search", "glob", "web_search", "web_fetch", "todo_read", "todo_write",
                        "shell_output", "ask_user", "load_skill", "delegate",
                        # 폴더 정리 도구: 사용자 파일을 바꾸지 않는다(계획 파일은 기록 폴더 안, 이동은 사람 검토 뒤 실행기가 한다)
                        "folder_scan", "scan_read", "scan_stats", "peek", "plan_add", "plan_show", "plan_drop",
                        # 기억 도구: 기억 폴더의 메모만 쓰고 사용자 파일은 바꾸지 않는다
                        "memory_search", "note_add", "memory_read"})
EDIT_TOOLS = frozenset({"write_file", "edit_file", "multi_edit", "notebook_edit"})

PLAN_NOTE = ("[계획 모드] 지금은 읽기 도구(파일 읽기·목록·검색)만 쓸 수 있다. 파일을 바꾸거나 명령을 돌리지 말고, "
             "무엇을 어떤 순서로 고칠지 단계별 계획(바꿀 파일, 바꿀 내용, 확인 방법)을 써서 끝내라.")
PLAN_DENY = "계획 모드라 실행하지 않았다(읽기 도구만 된다). 이 일은 계획에 단계로 적어라."
PLAN_EXIT = "[계획 모드 끝] 사람이 계획을 받아들였다. 이제 계획대로 실행하라."


def decide(mode: str, tool: str) -> str:
    """'run' | 'ask' | 'deny'."""
    if tool in READ_TOOLS or mode == "auto":
        return "run"
    if mode == "plan":
        return "deny"
    if mode == "edits" and tool in EDIT_TOOLS:
        return "run"
    return "ask"


def check_mode(mode: str, interactive: bool) -> str:
    if mode not in MODES:
        raise ConfigError(f"모르는 모드: {mode} (쓸 수 있는 것: {', '.join(MODES)})")
    if not interactive and mode in ("edits", "ask"):
        raise ConfigError(f"모드 {mode} 는 사람에게 묻는 모드라 run(무인)에는 쓸 수 없다(auto 또는 plan)")
    return mode
