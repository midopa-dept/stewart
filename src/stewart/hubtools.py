"""메인 에이전트의 세션 도구 6개: session_spawn · session_send · session_read_report · session_list · session_stop · session_resume.

- 승인 답을 쓰는 도구는 없다(답은 사람이 허브 화면에서만 준다).
- 작업자 모드는 메인 모드보다 넓을 수 없다(plan < ask < edits < auto).
- 보고는 8,000자까지 돌려준다(delegate 와 같다). 끝에 사실 한 줄(종료 사유·단계·바꾼 파일)을 붙인다.
"""

from __future__ import annotations

import json

from .hub import KINDS, MODE_RANK, Hub

REPORT_MAX = 8000
NAMES = ("session_spawn", "session_send", "session_read_report", "session_list", "session_stop", "session_resume", "board")


def _fn(name: str, desc: str, props: dict, req: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": props, "required": req}}}


DEFINITIONS = [
    _fn("session_spawn", "작업자 세션 하나를 띄우고 첫 지시를 준다. 오래 가는 일에 쓴다(짧은 조사는 delegate). "
        "작업자는 이 대화를 못 보니 task 에 목표·범위·보고할 것을 다 적는다.",
        {"name": {"type": "string", "description": "세션 이름(영숫자·'-')"},
         "task": {"type": "string", "description": "첫 지시"},
         "workdir": {"type": "string", "description": "작업 폴더(worktree 를 주지 않을 때)"},
         "worktree": {"type": "object", "description": "새 git worktree: {repo, branch, base}. 작업자 경계는 그 worktree",
                      "properties": {"repo": {"type": "string"}, "branch": {"type": "string"}, "base": {"type": "string"}},
                      "required": ["repo", "branch"]},
         "profile": {"type": "string", "description": "작업자 프로필(없으면 기본). .stewart/agents 의 이름도 된다"},
         "backend": {"type": "string"},
         "mode": {"type": "string", "enum": list(MODE_RANK), "description": "작업자 모드(메인보다 넓을 수 없다)"}},
        ["name", "task"]),
    _fn("session_send", "작업자에게 지시 하나를 보낸다. kind=긴급 은 지금 차례에 끼워 넣고, 대체는 replaces 번호의 지시를 통째로 바꾼다.",
        {"name": {"type": "string"}, "text": {"type": "string"},
         "kind": {"type": "string", "enum": list(KINDS)}, "replaces": {"type": "integer"}},
        ["name", "text"]),
    _fn("session_read_report", "작업자의 보고를 읽는다. seq 를 빼면 아직 안 읽은 것 가운데 가장 앞 번호.",
        {"name": {"type": "string"}, "seq": {"type": "integer"}}, ["name"]),
    _fn("session_list", "세션 목록: 이름·상태·대기 질문 수·마지막 보고 번호·안 읽은 보고.", {}, []),
    _fn("session_stop", "작업자를 멈춘다. how=pause 는 나중에 이어 가고, stop 은 끝낸다(worktree 는 남는다).",
        {"name": {"type": "string"}, "how": {"type": "string", "enum": ["pause", "stop"]}}, ["name"]),
    _fn("session_resume", "멈춘(paused) 작업자를 이어 가거나, 끝낸(stopped)·실패한 작업자를 앞 대화 그대로 다시 연다.",
        {"name": {"type": "string"}}, ["name"]),
]


from .board import DEFINITION as _BOARD_DEF   # noqa: E402

DEFINITIONS.append(_BOARD_DEF)   # 현황판(판 49)


class MainTools:
    def __init__(self, hub: Hub, main_mode: str = "edits", default_profile: str = "chat", default_backend: str | None = None):
        self.hub, self.main_mode = hub, main_mode
        self.default_profile, self.default_backend = default_profile, default_backend

    def _known(self, name) -> str | None:
        if not isinstance(name, str) or name not in self.hub.registry()["sessions"]:
            return f"없는 세션: {name}"
        return None

    def execute(self, name: str, args: dict) -> tuple[bool, str]:
        if name not in NAMES:
            return False, f"모르는 세션 도구: {name}"
        try:
            return getattr(self, name)(**args)
        except TypeError as e:
            return False, f"인자가 맞지 않다: {e}"
        except (ValueError, RuntimeError, OSError) as e:
            return False, str(e)

    def board(self, **args):
        b = getattr(self.hub, "board", None)
        if b is None:
            return False, "현황판이 붙어 있지 않다"
        from .board import execute
        return execute(b, args)

    def session_spawn(self, name, task, workdir=None, worktree=None, profile=None, backend=None, mode=None):
        mode = mode or self.main_mode
        if MODE_RANK.get(mode, 99) > MODE_RANK.get(self.main_mode, -1):
            return False, f"작업자 모드({mode})는 메인 모드({self.main_mode})보다 넓을 수 없다"
        e = self.hub.spawn(name, task, workdir, profile or self.default_profile, backend or self.default_backend,
                           mode=mode, worktree=worktree)
        return True, (f"세션 {name} 을 띄웠다: 상태 {e['status']}, 작업 폴더 {e['workdir']}, 모드 {e['mode']}, 지시 0001. "
                      "보고가 오면 알림이 온다.")

    def session_send(self, name, text, kind="지시", replaces=None):
        if (err := self._known(name)):
            return False, err
        n = self.hub.send(name, text, kind, replaces)
        return True, f"{name} 에 지시 {n:04d}({kind})를 보냈다."

    def session_read_report(self, name, seq=None):
        if (err := self._known(name)):
            return False, err
        e = self.hub.registry()["sessions"][name]
        if seq is None:
            done = set(e.get("read_reports") or [])
            from .hub import seqs
            todo = [n for n in seqs(self.hub.sdir(name).outbox, "md") if n not in done]
            if not todo:
                return False, f"{name} 에 안 읽은 보고가 없다(상태 {e['status']})"
            seq = todo[0]
        r = self.hub.read_report(name, int(seq))
        if r is None:
            return False, f"{name} 보고 {int(seq):04d} 가 아직 없다(상태 {e['status']})"
        head, body = r
        self.hub.mark_read(name, int(seq))
        body = body if len(body) <= REPORT_MAX else body[:REPORT_MAX] + f"\n…(보고 {len(body)}자 중 {REPORT_MAX}자)"
        files = head.get("files_changed") or []
        fact = (f"[{head.get('report_kind', '보고')} {int(seq):04d} · 종료 {head.get('reason')} · 단계 {head.get('steps')}"
                f"{' · 되짚기 함' if head.get('reflected') else ''}"
                f" · 바꾼 파일 {len(files)}개{': ' + ', '.join(files[:20]) if files else ''}]")
        return True, body.rstrip() + "\n\n" + fact

    def session_list(self):
        rows = []
        q = self.hub.queue()
        for e in self.hub.list():
            waiting = sum(1 for x in q if x["session"] == e["name"])
            unread = [n for n in range(1, (e.get("outbox_seq") or 0) + 1) if n not in (e.get("read_reports") or [])]
            rows.append({"name": e["name"], "status": e["status"], "mode": e["mode"], "waiting_approvals": waiting,
                         "last_report": e.get("outbox_seq") or 0, "unread_reports": unread})
        return True, json.dumps(rows, ensure_ascii=False) if rows else "세션이 없다"

    def session_stop(self, name, how="pause"):
        if (err := self._known(name)):
            return False, err
        if how not in ("pause", "stop"):
            return False, "how 는 pause·stop 중 하나"
        e = self.hub.pause(name) if how == "pause" else self.hub.stop(name)
        return True, f"{name}: {e['status']}"

    def session_resume(self, name):
        if (err := self._known(name)):
            return False, err
        e = self.hub.resume(name)
        return True, f"{name}: {e['status']}(이어 감)"
