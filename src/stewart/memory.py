"""기억 M1: 모든 프로필에 같은 층. 쓰는 법은 모델에게, 받침은 규칙으로.

층
- L0 기록 색인: 실행이 끝나면 기록 폴더의 부모(runs)/index.jsonl 에 한 줄(대상 키·과제 첫 줄·종료 사유·바꾼 파일).
  도구 memory_search(읽기 전용) 가 그 색인으로 같은 대상의 실행을 좁혀 transcript.md·facts.md·메모에서 맞는 줄을 찾는다.
- L1 메모: <기억 폴더>/<대상>/notes/<실행 id>.jsonl — 실행마다 따로라 동시 쓰기 충돌이 없다.
  · 엔진 자동 메모(모델 판단 없음): 판정 명령 결과, 끝내기 전 확인 실패, 되돌림, 빈 잘림, 실패한 셸 명령 첫 줄 — status "confirmed", by "engine"
  · 모델 메모: 도구 note_add(claim, evidence, status) — 실행당 NOTE_MAX(10)개, evidence 필수(없으면 "미확인" 으로 쓰게)
- L3 주입 색인: <기억 폴더>/<대상>/MEMORY.md 를 지시문 끝에 넣는다(대상당 40줄·4 KB, 합 6 KB, 넘으면 자르고 표시).
  M1 에서는 사람이 쓰거나 비워 둔다. 쪽(L2)·꿈꾸기는 M2(쓰는 자는 꿈꾸기 하나).
대상(코드가 정한다): 자리 = git 저장소 뿌리(있으면) 또는 작업 폴더 realpath / 종류 = 프로필 이름.
켜기: 프로필 memory = true, 또는 STEWART_MEMORY=1. STEWART_MEMORY=0 이 이긴다. 기본은 끔(내장 프로필 모두 — 평가 기본 끔).
기억 폴더: STEWART_MEMORY_DIR, 없으면 ~/.local/share/stewart/memory. 작업자(delegate)는 기억을 쓰지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

from .nostore import scrub

ENV_ON, ENV_DIR = "STEWART_MEMORY", "STEWART_MEMORY_DIR"
INJECT_LINES, INJECT_BYTES, INJECT_TOTAL = 40, 4096, 6144
NOTE_MAX = 10
STATUSES = ("confirmed", "unconfirmed", "trap")
SEARCH_MAX = 20
PAGE_MAX = 8192            # L2 쪽 하나의 상한(바이트에 가깝게 글자로)

PURPOSE = ("[기억] 아래는 같은 대상(이 폴더·저장소, 이 종류의 일)에서 앞서 알아낸 것의 색인이다. 기억은 다음에 같은 일을 할 때 "
           "다시 알아내지 않게 하려고 있다. 일을 시작하기 전에 관련 기억을 보고(대상 쪽은 memory_read, 앞 실행의 기록은 memory_search), "
           "기억의 주장은 지금 환경에서 확인한 뒤 쓴다. 확인한 사실·함정·결정은 note_add 로 남긴다(근거와 함께, 확인 못 했으면 unconfirmed).")


def enabled(profile, env=None) -> bool:
    env = os.environ if env is None else env
    v = env.get(ENV_ON)
    if v in ("0", "1"):
        return v == "1"
    return getattr(profile, "memory", None) is True


def memory_dir(env=None) -> Path:
    env = os.environ if env is None else env
    return Path(os.path.expanduser(env.get(ENV_DIR) or "~/.local/share/stewart/memory"))


def _slug(s: str) -> str:
    s = re.sub(r"[^\w.-]+", "-", s, flags=re.U).strip("-")
    return (s or "x")[:40]


def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def subjects(profile) -> list[dict]:
    """[{key, kind, name, path}] — 자리 하나 + 종류 하나."""
    from .memfile import git_root
    wd = Path(profile.workdir).resolve()
    root = git_root(wd)
    out = []
    if root is not None:
        out.append({"key": f"repo-{_slug(root.name)}-{_h(str(root))}", "kind": "repo", "name": root.name, "path": str(root)})
    else:
        out.append({"key": f"dir-{_slug(wd.name)}-{_h(str(wd))}", "kind": "dir", "name": wd.name, "path": str(wd)})
    out.append({"key": f"kind-{_slug(profile.name)}", "kind": "kind", "name": profile.name, "path": ""})
    return out


class Memory:
    """한 실행(또는 chat 세션)의 기억 손잡이. 도구(memory_search·note_add)와 엔진이 쓴다."""

    def __init__(self, profile, run_dir: Path, env=None):
        self.dir = memory_dir(env)
        self.subjects = subjects(profile)
        self.place = self.subjects[0]
        self.run_dir = Path(run_dir)
        self.runs_dir = self.run_dir.parent
        self.run_id = self.run_dir.name
        self.notes_written = 0

    # ---- L3 주입 ----
    def inject(self) -> tuple[str, list[dict]]:
        parts, log, total = [], [], 0
        for s in self.subjects:
            f = self.dir / s["key"] / "MEMORY.md"
            if not f.is_file():
                continue
            raw = f.read_text(encoding="utf-8", errors="replace")
            lines = raw.splitlines()
            keep, size, cut = [], 0, False
            for ln in lines:
                b = len((ln + "\n").encode("utf-8"))
                if len(keep) >= INJECT_LINES or size + b > INJECT_BYTES or total + size + b > INJECT_TOTAL:
                    cut = True
                    break
                keep.append(ln)
                size += b
            total += size
            log.append({"subject": s["key"], "lines": len(keep), "bytes": size, "of_lines": len(lines), "truncated": cut})
            if keep:
                head = f"## {s['kind']}: {s['name']}" + (f" ({s['path']})" if s["path"] else "")
                parts.append(head + "\n" + "\n".join(keep) + ("\n…(색인이 상한에서 잘림 — 나머지는 memory_search)" if cut else ""))
        text = PURPOSE + ("\n\n" + "\n\n".join(parts) if parts else "\n\n(아직 색인이 없다.)")
        return text, log

    # ---- L1 메모 ----
    def _note_file(self, subject: dict) -> Path:
        d = self.dir / subject["key"] / "notes"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{self.run_id}.jsonl"

    def _append(self, subject: dict, rec: dict) -> None:
        rec = scrub({"ts": datetime.now().isoformat(timespec="seconds"), "run": self.run_id, "subject": subject["key"], **rec})
        with open(self._note_file(subject), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def note_add(self, claim: str, evidence: str, status: str, scope: str = "place") -> tuple[bool, str]:
        claim, evidence = (claim or "").strip(), (evidence or "").strip()
        if self.notes_written >= NOTE_MAX:
            return False, f"이 실행의 메모 상한({NOTE_MAX}개)에 닿았다. 가장 중요한 것만 남겼는지 보고 일로 돌아가라."
        if not claim or len(claim) > 500:
            return False, "claim 은 1~500자 한 사실(문장 하나 또는 두셋)"
        if not evidence:
            return False, "evidence 가 비었다 — 이번 실행에서 확인한 근거(도구 호출·파일·명령 결과)를 적거나, 확인 못 했으면 '미확인' 이라 쓰고 status 를 unconfirmed 로"
        if status not in STATUSES:
            return False, f"status 는 {'|'.join(STATUSES)}"
        if scope not in ("place", "kind"):
            return False, "scope 는 place(이 폴더·저장소) 또는 kind(이 종류의 일)"
        subj = self.place if scope == "place" else self.subjects[1]
        self._append(subj, {"by": "model", "claim": claim, "evidence": evidence[:500], "status": status})
        self.notes_written += 1
        return True, f"메모 남김({subj['key']}, {status}, 이 실행 {self.notes_written}/{NOTE_MAX})"

    def auto_notes(self, summary: dict, facts: dict | None) -> int:
        """엔진 자동 메모(모델 판단 없음). 남긴 수."""
        n = 0
        v = summary.get("verify")
        if isinstance(v, dict) and v.get("command"):
            self._append(self.place, {"by": "engine", "status": "confirmed",
                                      "claim": f"판정 명령 `{v['command']}` → exit {v.get('exit')} ({'통과' if v.get('passed') else '실패'})",
                                      "evidence": f"run {self.run_id} 끝 판정"})
            n += 1
        cs = summary.get("check_on_stop") or {}
        if cs.get("fails"):
            self._append(self.place, {"by": "engine", "status": "confirmed",
                                      "claim": f"끝내기 전 확인 `{cs.get('command')}` 이 {cs['fails']}번 실패했다",
                                      "evidence": f"run {self.run_id} check_on_stop"})
            n += 1
        rb = summary.get("rolled_back")
        if isinstance(rb, dict) and not rb.get("error"):
            self._append(self.place, {"by": "engine", "status": "confirmed",
                                      "claim": f"끝내지 못해({summary.get('stop_reason')}) 바꾼 파일을 되돌렸다",
                                      "evidence": f"run {self.run_id} rollback"})
            n += 1
        if summary.get("stop_reason") == "length_empty":
            self._append(self.subjects[1], {"by": "engine", "status": "confirmed",
                                            "claim": "생각만 하다 생성 상한에 닿아 답하지 못하고 끝났다(length_empty)",
                                            "evidence": f"run {self.run_id}"})
            n += 1
        for c in ((facts or {}).get("shell") or [])[:50]:
            if isinstance(c, dict) and isinstance(c.get("exit"), int) and c["exit"] != 0 and n < 12:
                self._append(self.place, {"by": "engine", "status": "confirmed",
                                          "claim": f"셸 `{str(c.get('command', ''))[:160]}` → exit {c.get('exit')}",
                                          "evidence": f"run {self.run_id} 셸 기록"})
                n += 1
        return n

    # ---- L0 색인 ----
    def index_run(self, task: str, summary: dict, files: list[str]) -> None:
        rec = scrub({"run": self.run_id, "ts": datetime.now().isoformat(timespec="seconds"),
                     "subjects": [s["key"] for s in self.subjects], "profile": summary.get("profile"),
                     "task": (task or "").strip().splitlines()[0][:200] if (task or "").strip() else "",
                     "stop_reason": summary.get("stop_reason"), "files": files[:50]})
        with open(self.runs_dir / "index.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def read_page(self, subject: str = "") -> tuple[bool, str]:
        """L2 쪽 읽기(쓰는 것은 stewart dream 하나). 대상 키를 안 주면 이 폴더·저장소 쪽."""
        want = subject or self.place["key"]
        if not re.fullmatch(r"[\w.-]{1,80}", want):
            return False, f"subject 꼴이 아니다: {want}"
        f = self.dir / want / "page.md"
        if not f.is_file():
            known = [s["key"] for s in self.subjects if (self.dir / s["key"] / "page.md").is_file()]
            return False, f"{want} 의 쪽이 아직 없다(stewart dream 이 만든다). 있는 쪽: {', '.join(known) or '없음'}"
        return True, f.read_text(encoding="utf-8", errors="replace")[:PAGE_MAX]

    def search(self, query: str, subject: str = "", limit: int = SEARCH_MAX) -> tuple[bool, str]:
        words = [w.lower() for w in (query or "").split() if w.strip()]
        if not words:
            return False, "query 가 비었다"
        want = subject or self.place["key"]
        if want not in [s["key"] for s in self.subjects] and not re.fullmatch(r"[\w.-]{1,80}", want):
            return False, f"subject 꼴이 아니다: {want}"
        limit = max(1, min(int(limit or SEARCH_MAX), SEARCH_MAX))
        runs = []
        idx = self.runs_dir / "index.jsonl"
        if idx.is_file():
            for ln in idx.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if want in (r.get("subjects") or []) and r.get("run") != self.run_id:
                    runs.append(r)
        hits = []

        def scan(label: str, path: Path):
            if not path.is_file():
                return
            for i, ln in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                low = ln.lower()
                if all(w in low for w in words):
                    hits.append(f"{label}:{i}: {ln.strip()[:240]}")
                    if len(hits) >= limit:
                        return

        for r in reversed(runs):                    # 최근 실행부터
            d = self.runs_dir / r["run"]
            for name in ("facts.md", "transcript.md"):
                if len(hits) < limit:
                    scan(f"{r['run']}/{name}", d / name)
        nd = self.dir / want / "notes"
        if nd.is_dir():
            for f in sorted(nd.glob("*.jsonl"), reverse=True):
                if len(hits) < limit:
                    scan(f"메모 {f.stem}", f)
        head = f"memory_search '{query}' — 대상 {want}, 앞 실행 {len(runs)}개"
        return True, head + "\n" + ("\n".join(hits) if hits else "(맞는 줄 없음)")


def tool_defs() -> list[dict]:
    return [
        {"type": "function", "function": {"name": "memory_search", "description": (
            "같은 대상(이 폴더·저장소)의 앞 실행 기록(대화록·한 일)과 메모에서 낱말이 모두 든 줄을 찾는다. 읽기만 한다."),
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "찾을 낱말들(빈칸으로 나눔, 모두 든 줄)"},
                "subject": {"type": "string", "description": "대상 키(없으면 이 폴더·저장소)"},
                "limit": {"type": "integer", "description": f"최대 줄 수(기본·상한 {SEARCH_MAX})"}},
                "required": ["query"]}}},
        {"type": "function", "function": {"name": "memory_read", "description": (
            "대상(이 폴더·저장소, 또는 이 종류의 일)의 기억 쪽을 읽는다. 확인된 사실·함정·결정이 원문 인용과 함께 있다. 읽기만 한다."),
            "parameters": {"type": "object", "properties": {
                "subject": {"type": "string", "description": "대상 키(없으면 이 폴더·저장소)"}}, "required": []}}},
        {"type": "function", "function": {"name": "note_add", "description": (
            f"다음에 같은 일을 할 때 다시 알아내지 않아도 되게 사실·함정·결정 하나를 기억에 남긴다(실행당 {NOTE_MAX}개). "
            "화면에서 바로 읽을 수 있는 것(지금 파일 내용 등)은 남기지 않는다."),
            "parameters": {"type": "object", "properties": {
                "claim": {"type": "string", "description": "남길 사실 하나(짧게)"},
                "evidence": {"type": "string", "description": "이번 실행에서 확인한 근거(도구·명령·파일), 없으면 '미확인'"},
                "status": {"type": "string", "enum": list(STATUSES), "description": "confirmed(확인함)·unconfirmed(미확인)·trap(함정)"},
                "scope": {"type": "string", "enum": ["place", "kind"], "description": "place = 이 폴더·저장소(기본), kind = 이 종류의 일"}},
                "required": ["claim", "evidence", "status"]}}},
    ]


NAMES = ("memory_search", "note_add", "memory_read")


def execute(mem: Memory, name: str, args: dict) -> tuple[bool, str]:
    from .tools import _outside
    if name == "memory_search":
        ok, out = mem.search(str(args.get("query", "")), str(args.get("subject") or ""), args.get("limit") or SEARCH_MAX)
        return ok, _outside("앞 실행 기록·메모", out) if ok else out
    if name == "memory_read":
        ok, out = mem.read_page(str(args.get("subject") or ""))
        return ok, _outside("기억 쪽", out) if ok else out
    if name == "note_add":
        return mem.note_add(str(args.get("claim", "")), str(args.get("evidence", "")), str(args.get("status", "")),
                            str(args.get("scope") or "place"))
    return False, f"없는 기억 도구: {name}"
