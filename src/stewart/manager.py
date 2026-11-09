"""관리자 모드: 장부(ledger) 도구와 검증자 판정.

- 장부 자리: 기록 폴더/ledger.json(작업 폴더 밖 — 작업자·평가 파일과 섞이지 않고 작업자가 못 고친다).
- ledger 도구는 관리자만 쓴다(작업자·검증자에게는 없다): op=show | set(items 통째로) | note(메모 한 줄).
- 엔진이 알아서 적는 것: delegate 가 끝날 때마다 runs[] 에 {n, kind, stop_reason, steps, files_written, verdict}.
  검증자(kind=verifier)의 판정은 마지막 "판정: 통과|실패 …" 줄에서 읽는다.
- 검증자 전후 git status --porcelain 이 다르면 그 판정은 무효("검증자가 파일을 바꿈")다. git 이 아니면 확인 불가로 적는다.
- 도구 결과 끝에 장부 요약 한 줄을 붙인다(관리자가 매번 show 하지 않아도 되게).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

NAME = "ledger"
MAX_ITEMS = 20
MAX_TEXT = 300
MAX_NOTES = 30
STATUSES = ("todo", "doing", "done", "failed")
VERIFIER = "verifier"
_VERDICT = re.compile(r"판정\s*[:：]\s*(통과|실패)(.*)")
_NOISE = re.compile(r"(__pycache__/|\.pyc$|\.pytest_cache/|\.mypy_cache/|\.ruff_cache/)")

DEFINITION = {"type": "function", "function": {
    "name": NAME,
    "description": ("관리자의 장부(할 일·진행·검증 결과). op=show 로 보고, op=set 으로 할 일 목록(items)을 통째로 바꾸고, "
                    "op=note 로 메모 한 줄을 더한다. 맡긴 일의 결과와 검증 판정은 엔진이 runs 에 적는다."),
    "parameters": {"type": "object", "properties": {
        "op": {"type": "string", "enum": ["show", "set", "note"]},
        "items": {"type": "array", "description": "op=set: 할 일 목록", "items": {"type": "object", "properties": {
            "id": {"type": "integer"}, "task": {"type": "string"},
            "status": {"type": "string", "enum": list(STATUSES)}, "note": {"type": "string"}},
            "required": ["id", "task", "status"]}},
        "text": {"type": "string", "description": "op=note: 메모"},
    }, "required": ["op"]},
}}


def _clip(s, n=MAX_TEXT) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


class Ledger:
    def __init__(self, path: Path, goal: str):
        self.path = Path(path)
        self.data = {"goal": _clip(goal), "items": [], "runs": [], "notes": []}
        self.save()

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")

    def op(self, args: dict) -> tuple[bool, str]:
        op = args.get("op")
        if op == "show":
            return True, self.show()
        if op == "set":
            items = args.get("items")
            if not isinstance(items, list):
                return False, "op=set 에는 items 목록이 필요하다."
            out = []
            for it in items[:MAX_ITEMS]:
                if not isinstance(it, dict):
                    continue
                st = it.get("status") if it.get("status") in STATUSES else "todo"
                out.append({"id": int(it.get("id") or len(out) + 1), "task": _clip(it.get("task")), "status": st,
                            **({"note": _clip(it["note"])} if it.get("note") else {})})
            self.data["items"] = out
            self.save()
            return True, self.show() + ("" if len(items) <= MAX_ITEMS else f"\n(할 일은 {MAX_ITEMS}개까지만 남겼다)")
        if op == "note":
            if not str(args.get("text") or "").strip():
                return False, "op=note 에는 text 가 필요하다."
            self.data["notes"] = (self.data["notes"] + [_clip(args["text"])])[-MAX_NOTES:]
            self.save()
            return True, "메모를 더했다."
        return False, "op 는 show·set·note 가운데 하나다."

    def add_run(self, entry: dict, verdict: str | None) -> None:
        self.data["runs"].append({k: entry.get(k) for k in ("n", "worker_kind", "stop_reason", "steps", "files_written")}
                                 | {"verdict": verdict, **({"git_check": entry["git_check"]} if entry.get("git_check") else {})})
        self.save()

    def summary_line(self) -> str:
        items = self.data["items"]
        cnt = {s: sum(1 for i in items if i["status"] == s) for s in STATUSES}
        last = next((r["verdict"] for r in reversed(self.data["runs"]) if r.get("verdict")), None)
        return (f"[장부: 할 일 {len(items)} · 끝 {cnt['done']} · 진행 {cnt['doing']} · 남음 {cnt['todo']} · 실패 {cnt['failed']}"
                f" · 맡긴 일 {len(self.data['runs'])} · 마지막 판정 {last or '없음'}]")

    def show(self) -> str:
        d = self.data
        lines = [f"목표: {d['goal']}", "할 일:"]
        lines += [f"  {i['id']}. [{i['status']}] {i['task']}" + (f" — {i['note']}" if i.get("note") else "") for i in d["items"]] or ["  (없음)"]
        lines.append("맡긴 일:")
        lines += [f"  {r['n']}. {r.get('worker_kind') or '-'} {r['stop_reason']} 단계 {r['steps']} 파일 {r.get('files_written') or []}"
                  + (f" 판정 {r['verdict']}" if r.get("verdict") else "") for r in d["runs"]] or ["  (없음)"]
        if d["notes"]:
            lines.append("메모:")
            lines += [f"  - {n}" for n in d["notes"]]
        return "\n".join(lines)

    def brief(self) -> dict:
        items = self.data["items"]
        return {"items": {s: sum(1 for i in items if i["status"] == s) for s in STATUSES},
                "runs": len(self.data["runs"]), "verdicts": [r["verdict"] for r in self.data["runs"] if r.get("verdict")],
                "path": str(self.path)}


def parse_verdict(text: str | None) -> str | None:
    for line in reversed((text or "").strip().splitlines()):
        m = _VERDICT.search(line)
        if m:
            return m.group(1) + (m.group(2).strip()[:200] and " " + m.group(2).strip()[:200])
    return None


def git_state(workdir: Path) -> str | None:
    """git status --porcelain(빌드 잡음 줄 빼고). git 저장소가 아니면 None."""
    try:
        p = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=str(workdir),
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    return "\n".join(l for l in p.stdout.splitlines() if not _NOISE.search(l))
