"""실제로 한 일 기록: 모델 보고와 실제를 사람이 바로 대조하게 한다.

- run 이 끝나면(어떤 종료 사유든) 기록 폴더에 facts.json + facts.md 를 남긴다. output.md(모델 최종 출력)에는 붙이지 않는다.
  summary.json 에는 facts 파일 경로만 더한다.
- 담는 것: 종료 사유, 바꾼 파일(체크포인트 기준: 새로 만듦·고침, 지금 없으면 그렇게 표시), 셸 명령과 종료 코드,
  실패하거나 거부된 도구 호출(이름·오류 첫 줄), 작업자(delegate)가 있으면 작업자별 요약.
- chat 은 /facts 로 이번 차례(또는 방금 끝난 차례)의 같은 내용을 보인다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_EXIT = re.compile(r"^\[exit (-?\d+)\]")


def _first(s: str | None, n: int = 160) -> str:
    line = (s or "").strip().splitlines()[0] if (s or "").strip() else ""
    return line if len(line) <= n else line[:n] + "…"


def _args(tc: dict) -> dict:
    try:
        a = json.loads(tc.get("raw_arguments") or "{}")
        return a if isinstance(a, dict) else {}
    except ValueError:
        return {}


def collect(stop_reason: str, calls: list[dict], cp_files: list[dict], workdir: Path,
            delegations: list[dict] | None = None, rolled_back: dict | None = None,
            not_rolled_back: str | None = None) -> dict:
    """calls: 도구 호출 기록(engine·chat 의 tc), cp_files: 체크포인트의 그 실행(차례) 파일 목록."""
    from .checkpoint import _sha
    files = []
    for e in cp_files:
        p = Path(e["path"])
        # 체크포인트에는 거부된 쓰기도 '건드리기 직전' 으로 남는다. 실제로 바뀐 것만 적는다.
        reverted = False
        if e.get("existed"):
            if p.is_file() and e.get("sha") and _sha(p) == e["sha"]:
                if not (rolled_back and e.get("after_sha") and e["after_sha"] != e["sha"]):
                    continue
                reverted = True   # 도구가 고친 뒤 엔진이 되돌렸다(rollback_unfinished)
        elif not p.exists() and not e.get("after_sha"):
            continue
        elif not p.exists() and rolled_back:
            reverted = True
        try:
            rel = str(p.resolve().relative_to(Path(workdir).resolve()))
        except ValueError:
            rel = str(p)
        files.append({"path": rel, "change": ("고침" if e.get("existed") else "새로 만듦") + (" → 되돌림" if reverted else ""),
                      "tool": e.get("tool"), **({} if p.exists() or reverted else {"now": "없음"})})
    shell, failed = [], []
    for tc in calls:
        name, res = tc.get("name"), tc.get("result") or ""
        if name == "run_shell":
            a = _args(tc)
            m = _EXIT.match(res)
            code = (int(m.group(1)) if m else "거부" if tc.get("denied") else
                    "시간 초과" if res.startswith("[시간 초과]") else "백그라운드" if a.get("background") else "모름")
            shell.append({"command": _first(str(a.get("command", "")), 200), "exit": code})
        if not tc.get("ok"):
            failed.append({"tool": name, "error": _first(tc.get("denied") or tc.get("parse_error") or res)})
    out = {"stop_reason": stop_reason, "files": files, "shell": shell, "failed_calls": failed,
           "tool_calls": len(calls)}
    if rolled_back:
        out["rolled_back"] = rolled_back
    if not_rolled_back:
        out["not_rolled_back"] = not_rolled_back
    if delegations:
        out["delegations"] = [{k: d.get(k) for k in ("n", "worker_kind", "stop_reason", "steps", "files_written", "verdict")}
                              for d in delegations]
    return out


def to_md(f: dict) -> str:
    L = [f"# 실제로 한 일(엔진 기록)", "", f"- 종료 사유: {f['stop_reason']}", f"- 도구 호출: {f['tool_calls']}번", "",
         "## 바꾼 파일(도구로 쓴 것, 체크포인트 기준)"]
    L += [f"- {x['change']}: {x['path']}" + (f" (지금 없음)" if x.get("now") else "") for x in f["files"]] or ["- 없음"]
    L += ["", "## 셸 명령"]
    L += [f"- `{x['command']}` → {('exit ' + str(x['exit'])) if isinstance(x['exit'], int) else x['exit']}" for x in f["shell"]] or ["- 없음"]
    L += ["", "## 실패·거부된 도구 호출"]
    L += [f"- {x['tool']}: {x['error']}" for x in f["failed_calls"]] or ["- 없음"]
    if f.get("rolled_back"):
        rb = f["rolled_back"]
        L += ["", "## 되돌림(끝내지 못해 엔진이 되돌림)",
              f"- 되씌움 {rb.get('restored', 0)} · 새 파일 옮김 {rb.get('moved_aside', 0)} · 보관 {rb.get('aside_dir', '-')}"
              + (f" · 셸 명령 {rb['shell_calls']}번이 바꾼 것은 못 되돌렸을 수 있다" if rb.get("shell_calls") else "")]
    if f.get("not_rolled_back"):
        L += ["", "## 되돌리지 않음(끝내지 못했지만 엔진이 되돌리지 않음)", f"- 이유: {f['not_rolled_back']}"]
    if f.get("delegations"):
        L += ["", "## 작업자"]
        L += [f"- {d['n']}. {d.get('worker_kind') or '-'} {d['stop_reason']} 단계 {d['steps']} 파일 {d.get('files_written') or []}"
              + (f" 판정 {d['verdict']}" if d.get("verdict") else "") for d in f["delegations"]]
    L += ["", "셸 명령이 바꾼 파일은 '바꾼 파일' 에 없다(셸 명령 목록을 본다)."]
    return "\n".join(L) + "\n"


def write(rec_dir: Path, f: dict) -> Path:
    p = Path(rec_dir) / "facts.json"
    p.write_text(json.dumps(f, ensure_ascii=False, indent=1), encoding="utf-8")
    (Path(rec_dir) / "facts.md").write_text(to_md(f), encoding="utf-8")
    return p
