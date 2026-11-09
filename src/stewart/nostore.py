"""저장하지 않는 바깥 자료: Brave Search 결과는 실행 중 메모리에서만 쓰고 디스크 기록에 남기지 않는다.

Brave Search API 약관은 결과 저장·캐시를 금지한다(동작에 필요한 일시 저장만 허용). SearXNG 가 엔진 이름에 brave 가 든
엔진(braveapi·brave·brave.news 등)의 결과를 하나라도 섞어 돌려주면, web_search 도구 결과 글을 BEGIN·END 표지로 감싼다.
모델은 그 글을 그대로 받는다(메모리). 기록기(events.jsonl·transcript.md·summary.json·messages.json)는 scrub() 으로 표지
구간을 자리 글로 바꿔 쓴다. 압축이 끝 표지를 잘라 먹었으면 시작 표지부터 글 끝까지 지운다.
"""

from __future__ import annotations

import re

BEGIN = "〔저장 안 함: Brave 검색 결과 — 이 실행 안에서만 쓴다〕"
END = "〔저장 안 함 끝〕"
_RE = re.compile(re.escape(BEGIN) + r"(.*?)(?:" + re.escape(END) + r"|\Z)", re.S)


def is_brave(engines) -> bool:
    return any("brave" in str(e).lower() for e in (engines or []))


def wrap(text: str) -> str:
    return f"{BEGIN}\n{text}\n{END}"


def _sub(m: re.Match) -> str:
    return f"〔Brave 검색 결과 {len(m.group(1))}자 — 약관상 기록에 남기지 않음〕"


def scrub(obj):
    """글·목록·사전 안의 표지 구간을 자리 글로 바꾼 사본(표지가 없으면 같은 것을 돌려준다)."""
    if isinstance(obj, str):
        return _RE.sub(_sub, obj) if BEGIN in obj else obj
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(scrub(x) for x in obj)
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()}
    return obj
