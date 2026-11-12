"""판 113: 작은 모델용 얇은 허브 메인. backends 표의 등급에 `slim = true` 를 적고 `stewart hub --tier <그 등급>` 으로 켠다.

얇게 하는 것(메인·사람이 연 대화만 — 작업자는 그대로):
- 지시문: 프로필의 긴 지시 + 허브 역할 지시 + 현황판 지시 대신 짧은 지시 하나(등급의 slim_system 으로 바꿀 수 있다).
- 도구: 핵심 몇 개(KEEP, 등급의 slim_keep 으로 바꿈)만 모델에게 보이고, 나머지는 허브 머리의 켬·끔 칩(기본 끔)으로 남는다.
- 스킬 색인: 이름만(설명 없이).
기본(등급 없이 켬)은 바뀌지 않는다.
"""

SYSTEM = """너는 사람과 대화하며 작업 폴더에서 일하는 도우미다.
- 필요한 만큼만 읽고(list_dir·search·read_file) 고친다(edit_file — old 에는 파일 내용을 공백까지 그대로). 고친 뒤 run_shell 로 확인한다.
- 꼭 필요한 값이 요청과 폴더 어디에도 없으면 추측하지 말고 ask_user 로 묻는다(무엇이 모자란지 짚어서).
- 바깥 사실·최신·처음 보는 이름은 작업 폴더보다 web_search 로 먼저 찾고 web_fetch 로 원문을 연다.
- 큰 일(파일 여럿 고침·오래 걸리는 구현·시험)이나 파일을 넓게 읽는 조사는 workers 로 작업자에게 맡긴다. 작업자는 이 대화를 못 보니 task 에 목표·범위·끝의 기준을 다 적는다. 끝 알림이 오면 확인할 것만 보고 짧게 알린다.
- 꺼져 있는 도구가 필요하면 사람에게 허브 머리의 도구 칩을 켜 달라고 말한다.
- 사람 말을 받으면 먼저 한 문장으로 할 일을 말하고, 끝낼 때 한 일과 남은 일을 짧게 말한다.
- 비밀(키·토큰·.env)은 열거나 출력하지 않는다."""

KEEP = ("read_file", "list_dir", "search", "edit_file", "write_file", "run_shell", "ask_user", "workers", "view_image",
        "load_skill", "web_search", "web_fetch")   # 판 117.1: 웹 둘 — 얇은 허브에서도 바깥 사실은 웹으로


def conf(profile) -> dict | None:
    """프로필 raw 의 slim(허브가 등급에서 넣는다). 없으면 None."""
    v = (getattr(profile, "raw", None) or {}).get("slim")
    if v is True:
        return {}
    return dict(v) if isinstance(v, dict) else None


def keep(c: dict) -> set:
    k = c.get("keep")
    return set(k) if isinstance(k, (list, tuple)) and k else set(KEEP)


def apply(profile, tier: dict):
    """등급 → 메인 프로필 사본(지시문 바꿈 + raw.slim). 프로필 파일·작업자 프로필은 그대로."""
    import dataclasses
    c = {}
    if isinstance(tier.get("slim_keep"), list):
        c["keep"] = [str(x) for x in tier["slim_keep"]]
    raw = dict(profile.raw or {})
    raw["slim"] = c
    system = str(tier.get("slim_system") or SYSTEM)
    return dataclasses.replace(profile, system=system, raw=raw)


def skills_text(skills) -> str:
    if not skills:
        return ""
    return "스킬(맞는 것이 있을 때만 load_skill 로 본문을 읽고 따른다): " + ", ".join(s.name for s in skills)
