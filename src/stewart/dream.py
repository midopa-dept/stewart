"""꿈꾸기(기억 M2): 사람이 부르는 일. 메모(L1)를 기록(L0)과 대조해 대상마다 한 쪽(L2)을 고쳐 쓰고, 주입 색인(L3 MEMORY.md)을 만든다.

    stewart dream [--subject 키 …] [--backend 이름] [--dry-run] [--yes]      # 대상마다 한 번
    stewart dream --accept 키                                              # 제안된 MEMORY.md 를 들인다

- 쓰는 자는 이것 하나다. 실행 중의 모델은 메모(note_add)만 쓰고 쪽은 읽기만(memory_read) 한다. 대상마다 잠금 파일로 둘이 겹치지 않는다.
- 차례(대상마다)
  1. [코드] 지난 꿈 뒤 새 메모를 모은다(dream-state.json). 없으면 그 대상은 건너뛴다(모델을 부르지 않음).
  2. [코드] 근거 확인: 메모의 실행이 기록 색인(runs/index.jsonl)이나 기록 폴더에 있는지. 없으면 모델에게 "근거 없음" 으로 보인다.
  3. [모델] 쪽 하나를 다시 쓴다: 요약·확인된 사실·함정·결정·미확인. 사실 줄 끝에 (run <실행 id>), 중요한 원문은 > 로 그대로.
     입력은 그 대상의 기존 쪽 + 새 메모뿐이라 27B 문맥에 맞는다. 도구 없이 한 번 부른다(생각 끔).
  4. [코드] 다시 확인: 꼴(머리·절), 크기(쪽 8,192자), 확인된 사실·함정·결정의 줄은 아는 실행을 (run …) 으로 달아야 한다 —
     못 달면 미확인으로 내린다. 꼴이 틀리거나 너무 크면 쪽을 바꾸지 않고 기록만 남긴다.
  5. [코드] MEMORY.md 를 쪽에서 만든다(대상당 40줄·4,096 바이트): 쪽 갈고리 한 줄 → 함정 → 결정 → 확인된 사실(쪽 차례대로).
     기본은 MEMORY.md.proposed 로 두고 사람이 --accept 로 들인다. --yes 면 바로 바꾼다.
  6. 바뀐 것은 <대상>/dream-log/<시각>.diff 로 남긴다. --dry-run 은 아무것도 쓰지 않고 바뀔 diff 만 찍는다.
- 모르는 것: 쪽이 가리키는 파일이 바뀌었는지(오래됨)는 아직 보지 않는다. 쓰임(memory_read·search 횟수)으로 올리고 내리기도 아직 없다.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from .memory import INJECT_BYTES, INJECT_LINES, PAGE_MAX, memory_dir
from .nostore import scrub

SECTIONS = ("요약", "확인된 사실", "함정", "결정", "미확인")
CITED = ("확인된 사실", "함정", "결정")
LOCK_STALE_S = 2 * 3600
LINE_MAX = 200

SYSTEM = (
    "너는 기억 쪽을 고쳐 쓰는 편집자다. 한 대상(폴더·저장소, 또는 일의 종류)에 대해 앞 실행들이 남긴 메모를 받아, "
    "다음에 같은 일을 하는 에이전트가 다시 알아내지 않아도 되게 쪽 하나로 정리한다.\n"
    "꼴(이 순서, 이 머리 그대로):\n# <대상 이름>\n## 요약\n(한두 줄)\n## 확인된 사실\n- 사실 (run <실행 id>)\n## 함정\n- 함정 (run <실행 id>)\n"
    "## 결정\n- 결정 (run <실행 id>)\n## 미확인\n- 확인 못 한 주장\n"
    "규칙: 확인된 사실·함정·결정의 줄 끝에는 근거가 된 메모의 실행 id 를 (run <id>) 로 단다(여럿이면 (run a, run b)). "
    "근거 없음으로 표시된 메모나 unconfirmed 메모는 미확인에만 둔다. 같은 것은 하나로 합치고, 서로 어긋나면 더 새 실행 쪽을 남기고 "
    "옛 것은 지운다. 파일 내용·명령 출력처럼 바로 다시 읽을 수 있는 것은 남기지 않는다. 중요한 원문(오류 문구·설정 값·명령)은 "
    "> 로 그대로 인용한다. 쪽 전체는 6,000자 안. 머리말·맺음말 없이 쪽만 쓴다.")


def subjects_with_notes(root: Path) -> list[str]:
    return sorted(d.name for d in root.iterdir() if (d / "notes").is_dir()) if root.is_dir() else []


def known_runs(runs_dir: Path | None) -> set[str]:
    out: set[str] = set()
    if runs_dir is None or not Path(runs_dir).is_dir():
        return out
    idx = Path(runs_dir) / "index.jsonl"
    if idx.is_file():
        for ln in idx.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.add(json.loads(ln)["run"])
            except (ValueError, KeyError, TypeError):
                continue
    out |= {p.name for p in Path(runs_dir).iterdir() if p.is_dir() and (p / "events.jsonl").is_file()}
    return out


class Lock:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        try:
            if self.path.exists() and time.time() - self.path.stat().st_mtime > LOCK_STALE_S:
                self.path.unlink()
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise RuntimeError(f"다른 꿈꾸기가 이 대상을 고치고 있다({self.path})") from None
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        return self

    def __exit__(self, *a):
        try:
            self.path.unlink()
        except OSError:
            pass


def new_notes(sdir: Path, state: dict) -> tuple[list[dict], dict]:
    done = dict(state.get("done") or {})
    out = []
    nd = sdir / "notes"
    for f in sorted(nd.glob("*.jsonl")) if nd.is_dir() else []:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        for ln in lines[done.get(f.name, 0):]:
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
        done[f.name] = len(lines)
    return out, done


def prompt_notes(notes: list[dict], runs: set[str]) -> str:
    rows = []
    for n in notes:
        ok = n.get("run") in runs
        mark = "" if ok else " [근거 없음: 그 실행 기록이 없다]"
        rows.append(f"- (run {n.get('run')}) {n.get('status')} · {n.get('by')}: {n.get('claim')} — 근거: {n.get('evidence')}{mark}")
    return "\n".join(rows)


def _sections(page: str) -> dict[str, list[str]]:
    cur, out = None, {}
    for ln in page.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", ln)
        if m:
            cur = m.group(1)
            out.setdefault(cur, [])
        elif cur is not None:
            out[cur].append(ln)
    return out


def validate(raw: str, title: str, runs: set[str]) -> tuple[str | None, list[str]]:
    """(고친 쪽 | None(받지 않음), 알림)."""
    notes = []
    t = raw.strip()
    t = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", t).strip()
    if not t.startswith("# "):
        return None, ["쪽이 '# ' 머리로 시작하지 않는다"]
    sec = _sections(t)
    missing = [s for s in SECTIONS if s not in sec]
    if missing:
        return None, [f"빠진 절: {', '.join(missing)}"]
    demoted = []
    for s in CITED:
        keep = []
        for ln in sec[s]:
            if ln.strip().startswith("- "):
                ids = re.findall(r"run\s+([\w.-]+)", ln)
                if not ids or not all(i in runs for i in ids):
                    demoted.append(ln.strip() + " (근거 확인 못 함 — 꿈꾸기가 내림)")
                    continue
            keep.append(ln)
        sec[s] = keep
    if demoted:
        notes.append(f"근거 없는 줄 {len(demoted)}개를 미확인으로 내렸다")
        sec["미확인"] = [x for x in sec["미확인"] if x.strip()] + demoted
    head = t.splitlines()[0] if t.splitlines()[0].strip() != "#" else f"# {title}"
    body = [head, ""]
    for s in SECTIONS:
        lines = [x.rstrip() for x in sec[s]]
        while lines and not lines[-1].strip():
            lines.pop()
        while lines and not lines[0].strip():
            lines.pop(0)
        body += [f"## {s}"] + (lines or ["- 없음"]) + [""]
    page = scrub("\n".join(body).rstrip() + "\n")
    if len(page) > PAGE_MAX:
        return None, notes + [f"쪽이 상한({PAGE_MAX:,}자)을 넘는다({len(page):,}자)"]
    return page, notes


def build_index(page: str, key: str) -> str:
    sec = _sections(page)
    summary = next((x.strip() for x in sec.get("요약", []) if x.strip()), "")
    lines = [f"- 쪽: memory_read(subject={key}) — {summary}"[:LINE_MAX]]
    for s in ("함정", "결정", "확인된 사실"):
        for ln in sec.get(s, []):
            if ln.strip().startswith("- ") and ln.strip() != "- 없음":
                lines.append((f"- [{s}] " + ln.strip()[2:])[:LINE_MAX])
    out, size = [], 0
    for ln in lines:
        b = len((ln + "\n").encode("utf-8"))
        if len(out) >= INJECT_LINES or size + b > INJECT_BYTES:
            break
        out.append(ln)
        size += b
    return "\n".join(out) + "\n"


def _diff(old: str, new: str, name: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{name}", f"b/{name}"))


def dream_subject(root: Path, key: str, ask_model, runs_dir: Path | None, dry_run=False, yes=False) -> dict:
    """ask_model(system, user) → 글. 돌려주는 것: {subject, status, notes, demoted…, diff 파일}."""
    sdir = root / key
    res = {"subject": key, "status": "", "new_notes": 0, "notices": []}
    with Lock(sdir / ".dream.lock"):
        sf = sdir / "dream-state.json"
        state = json.loads(sf.read_text()) if sf.is_file() else {}
        notes, done = new_notes(sdir, state)
        res["new_notes"] = len(notes)
        if not notes:
            res["status"] = "새 메모 없음"
            return res
        runs = known_runs(runs_dir) | {n.get("run") for n in notes if n.get("by") == "engine"}
        old_page = (sdir / "page.md").read_text(encoding="utf-8") if (sdir / "page.md").is_file() else ""
        cited_before = set(re.findall(r"run\s+([\w.-]+)", old_page))
        runs |= cited_before                         # 앞 쪽이 이미 단 실행은 그대로 인정
        user = (f"대상: {key}\n\n기존 쪽:\n{old_page or '(아직 없음)'}\n\n새 메모({len(notes)}개):\n{prompt_notes(notes, runs)}\n\n"
                "위 규칙대로 쪽 전체를 다시 써라.")
        raw = ask_model(SYSTEM, user)
        page, notices = validate(raw or "", key, runs)
        res["notices"] = notices
        if page is None:
            res["status"] = "쪽을 받지 않음(그대로 둠)"
            if not dry_run:
                _log(sdir, "rejected", raw or "", notices)
            return res
        old_index = (sdir / "MEMORY.md").read_text(encoding="utf-8") if (sdir / "MEMORY.md").is_file() else ""
        index = build_index(page, key)
        diff = _diff(old_page, page, "page.md") + _diff(old_index, index, "MEMORY.md")
        res["diff"] = diff
        if dry_run:
            res["status"] = "보기만(쓰지 않음)"
            return res
        (sdir / "page.md").write_text(page, encoding="utf-8")
        if yes:
            (sdir / "MEMORY.md").write_text(index, encoding="utf-8")
            (sdir / "MEMORY.md.proposed").unlink(missing_ok=True)
            res["status"] = "쪽·색인 바꿈"
        else:
            (sdir / "MEMORY.md.proposed").write_text(index, encoding="utf-8")
            res["status"] = "쪽 바꿈, 색인은 제안(stewart dream --accept " + key + ")"
        res["log"] = str(_log(sdir, "diff", diff, notices))
        sf.write_text(json.dumps({"done": done, "last": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False))
        return res


def accept(root: Path, key: str) -> dict:
    sdir = root / key
    prop = sdir / "MEMORY.md.proposed"
    if not prop.is_file():
        return {"subject": key, "status": "제안된 색인이 없다"}
    with Lock(sdir / ".dream.lock"):
        old = (sdir / "MEMORY.md").read_text(encoding="utf-8") if (sdir / "MEMORY.md").is_file() else ""
        new = prop.read_text(encoding="utf-8")
        os.replace(prop, sdir / "MEMORY.md")
        log = _log(sdir, "accept", _diff(old, new, "MEMORY.md"), [])
    return {"subject": key, "status": "색인 들임", "log": str(log)}


def _log(sdir: Path, kind: str, text: str, notices: list[str]) -> Path:
    d = sdir / "dream-log"
    d.mkdir(exist_ok=True)
    f = d / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{kind}.diff"
    n = 2
    while f.exists():
        f = d / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{kind}-{n}.diff"
        n += 1
    f.write_text("".join(f"# {x}\n" for x in notices) + text, encoding="utf-8")
    return f


def model_caller(backend):
    from .backend import chat

    def ask(system: str, user: str) -> str:
        resp, _ = chat(backend, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                       max_tokens=4096, thinking=False)
        return ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return ask


def run(subjects: list[str] | None, backend, runs_dir: Path | None, dry_run=False, yes=False, root: Path | None = None,
        ask_model=None) -> list[dict]:
    root = root or memory_dir()
    keys = subjects or subjects_with_notes(root)
    ask = ask_model or model_caller(backend)
    out = []
    for k in keys:
        if not re.fullmatch(r"[\w.-]{1,80}", k) or not (root / k).is_dir():
            out.append({"subject": k, "status": "없는 대상"})
            continue
        try:
            out.append(dream_subject(root, k, ask, runs_dir, dry_run, yes))
        except RuntimeError as e:
            out.append({"subject": k, "status": str(e)})
    return out


# ── 꿈꾸기 자동(판 54): 한가할 때·닫을 때, 새 메모가 N개 넘은 대상만, 모델 서버가 준비됐을 때만 ──
AUTO_MIN_NOTES = int(os.environ.get("STEWART_DREAM_MIN_NOTES") or 5)
AUTO_MAX_SUBJECTS = 3
AUTO_IDLE_S = 600            # 마지막 차례 뒤 이만큼 한가하면
AUTO_RETRY_S = 1800          # 한 번 해 본 뒤(했든 미뤘든) 다시 보기까지


def auto_enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return env.get("STEWART_DREAM_AUTO") != "0"


def due(root: Path, min_notes: int = AUTO_MIN_NOTES) -> list[tuple[str, int]]:
    """새 메모가 min_notes 개 이상인 대상 [(키, 새 메모 수)] — 많은 것부터. 모델은 부르지 않는다."""
    out = []
    for k in subjects_with_notes(root):
        sdir = root / k
        try:
            state = json.loads((sdir / "dream-state.json").read_text()) if (sdir / "dream-state.json").is_file() else {}
            n = len(new_notes(sdir, state)[0])
        except (OSError, ValueError):
            continue
        if n >= min_notes:
            out.append((k, n))
    return sorted(out, key=lambda x: -x[1])


def auto(root: Path, ask_model, runs_dir: Path | None, ready, min_notes: int = AUTO_MIN_NOTES,
         max_subjects: int = AUTO_MAX_SUBJECTS) -> dict:
    """ready() 가 참일 때만(모델 서버가 이미 켜져 준비됨 — 깨우려고 부르지 않는다) 대상마다 쪽·색인을 바로 고친다(--yes 와 같음,
    바뀐 것은 dream-log 의 diff 로 남아 되돌릴 수 있다). 돌려줌: {status, results}."""
    keys = [k for k, _ in due(root, min_notes)][:max_subjects]
    if not keys:
        return {"status": "할 것 없음", "results": []}
    if not ready():
        return {"status": "미룸(모델 서버가 준비되지 않음)", "results": [], "subjects": keys}
    res = []
    for k in keys:
        if not ready():
            res.append({"subject": k, "status": "미룸(모델 서버가 준비되지 않음)"})
            continue
        try:
            res.append(dream_subject(root, k, ask_model, runs_dir, dry_run=False, yes=True))
        except Exception as e:   # 모델 오류·잠금 — 다음 기회에
            res.append({"subject": k, "status": f"못 함: {type(e).__name__}: {str(e)[:120]}"})
    return {"status": "함", "results": res}
