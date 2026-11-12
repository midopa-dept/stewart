"""기능 지문(판 102, 측정 준비 — R1 기능 사다리): 실행 시작 사건 run_start 의 meta["features"] 에 켜진 손잡이·값, 도구 목록,
고정 앞부분(시스템 지시 + 도구 정의) sha·토큰 어림을 한 덩어리로 남긴다. 같은 커밋에서 손잡이만 바꾼 칸들을 기록만으로 가를 수 있게."""

from __future__ import annotations

import hashlib
import json
import os

# 모델 문맥에 닿는 손잡이(판 80 이후 포함). 값이 없으면 각 기능의 기본(실사용·평가가 다름 — README "손잡이 모아 보기")
KNOBS = ("STEWART_MEMORY", "STEWART_MEMORY_EXTRACT", "STEWART_BUILTIN_SKILLS", "STEWART_HUB_ROLE", "STEWART_GOAL_NOTES",
         "STEWART_ENV_BLOCK", "STEWART_WEB_NUDGE", "STEWART_TOOLCALL_REPAIR", "STEWART_EDIT_FEEDBACK", "STEWART_REASONING_PASSBACK",
         "STEWART_IMAGE_OCR", "STEWART_TODO_NUDGE", "STEWART_INTENT_NUDGE", "STEWART_REFLECT", "STEWART_CHECK_ON_STOP",
         "STEWART_REVIEW_ON_STOP", "STEWART_ASK_CHECK", "STEWART_BASELINE_DIFF", "STEWART_TODO_CHECK", "STEWART_DOC_SEARCH",
         "STEWART_DELEGATE_NUDGE", "STEWART_EXAMPLES", "STEWART_COMPACT_MODE", "STEWART_COMPACT_RATIO", "STEWART_COMPACT_KEEP",
         "STEWART_COMPACT_FOLD", "STEWART_COMPACT_WINDOW", "STEWART_CONTEXT_BUDGET", "STEWART_BACKEND_RETRIES", "STEWART_CLAUDE_COMPAT", "STEWART_ADAPTER", "STEWART_SPAWN", "STEWART_SPAWN_MAX", "STEWART_AUTO_WAKE",
         "STEWART_THINK_MAX_TOKENS_FIRST", "STEWART_REASONING_EFFORT", "STEWART_REASONING_EFFORT_AT", "STEWART_SKILL_TOOLS")


def fingerprint(system: str, tool_defs: list, path: str, extra: dict | None = None) -> dict:
    """path = "run"(엔진) | "chat"(대화·허브). knobs 는 환경에 값이 있는 것만(없으면 그 길의 기본)."""
    from .compact import est_tokens
    tools_json = json.dumps(tool_defs or [], ensure_ascii=False)
    blob = (system or "") + "\n" + tools_json
    return {"path": path, "knobs": {k: os.environ[k] for k in KNOBS if k in os.environ},
            "tools": [d["function"]["name"] for d in tool_defs or []],
            "prefix_sha": hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16],
            "prefix_tokens": est_tokens(system or "") + est_tokens(tools_json), **(extra or {})}
