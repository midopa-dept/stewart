"""지난 실행 기록에서 web_search 결과를 지운다(Brave 결과를 저장하지 않게 되기 전에 남은 것 손질).

    python -m stewart.scrub_records --runs-dir <실행 기록 폴더> --since YYYY-MM-DD [--list-out 목록.tsv]      # 보기만(기본)
    python -m stewart.scrub_records --runs-dir <…> --since YYYY-MM-DD --apply [--backup-dir <폴더>]          # 바꾸기

- 대상: --runs-dir 바로 아래(그리고 한 단계 아래 묶음 폴더)의 실행 폴더 가운데 이름 앞 YYYYMMDD 가 --since 이후인 것.
- 바꾸는 파일: events.jsonl · transcript.md · messages.json · summary.json. 그 밖(plan.json·facts·output·scan 등)은 그대로.
  · web_search 도구 결과 글(도구 메시지 content·tool_calls 의 result·대화록의 결과 칸) → "〔웹 검색 결과 n자 — 약관상 기록에 남기지 않음(지난 기록 손질)〕"
  · web_search 기록의 urls·web_result_urls → 비움(지운 수만 urls_removed 로)
  · 이미 지운 자리 글이나 nostore 자리 글은 다시 세지 않는다(여러 번 돌려도 같다).
- 보기만(기본): 실행 폴더마다 바뀔 수만 찍는다(검색어·결과·주소 같은 내용은 찍지 않는다). --list-out 이면 같은 표를 파일로.
- --apply: 파일마다 임시 파일에 쓰고 바꿔 끼운다(권한 비트는 원본 그대로). 원본 보관은 기본 안 함.
  --backup-dir 를 주면 원본을 거기 복사한다 — 그 보관본도 지워야 손질한 뜻이 있다(경고를 찍는다).
- 소유자는 바뀌지 않게 기록 폴더 주인 계정으로 돌린다(예: sudo -u <그 계정> …). 경로·계정은 짐작하지 말고 그 서버에서 확인한다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import date
from pathlib import Path

TOOL = "web_search"
PLACE = "〔웹 검색 결과 {n}자 — 약관상 기록에 남기지 않음(지난 기록 손질)〕"
_DONE = re.compile(r"^〔(웹 검색 결과|Brave 검색 결과) \d+자 — 약관상 기록에 남기지 않음")
FILES = ("events.jsonl", "transcript.md", "messages.json", "summary.json")
_RUN = re.compile(r"^(\d{8})-\d{6}")
_MD = re.compile(r"(\*\*툴콜\*\* `web_search`[^\n]*\n(?:\n|> [^\n]*\n)*→ [^\n]*\n\n```\n)(.*?)(\n```\n)", re.S)


def _place(text: str) -> str:
    return text if (not text or _DONE.match(text)) else PLACE.format(n=len(text))


class Counter:
    def __init__(self):
        self.results = 0
        self.urls = 0

    def result(self, old: str, new: str) -> str:
        if new != old:
            self.results += 1
        return new


def _walk(obj, c: Counter, ids: dict):
    """사전·목록을 돌며 web_search 결과·주소를 지운 사본."""
    if isinstance(obj, list):
        for m in obj:   # 대화 목록이면 tool_call_id → 도구 이름
            if isinstance(m, dict):
                for tc in m.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("id"):
                        ids[tc["id"]] = (tc.get("function") or {}).get("name") or tc.get("name")
        return [_walk(x, c, ids) for x in obj]
    if not isinstance(obj, dict):
        return obj
    out = {}
    is_tool_msg = obj.get("role") == "tool" and (obj.get("name") == TOOL or ids.get(obj.get("tool_call_id")) == TOOL)
    is_call_rec = obj.get("name") == TOOL and "result" in obj
    for k, v in obj.items():
        if is_tool_msg and k == "content" and isinstance(v, str):
            out[k] = c.result(v, _place(v))
        elif is_call_rec and k == "result" and isinstance(v, str):
            out[k] = c.result(v, _place(v))
        elif k in ("urls", "web_result_urls") and isinstance(v, list) and v and all(isinstance(u, str) for u in v):
            c.urls += len(v)
            out[k] = []
            out["urls_removed"] = out.get("urls_removed", 0) + len(v)
        else:
            out[k] = _walk(v, c, ids)
    return out


def scrub_text_md(text: str, c: Counter) -> str:
    def sub(m):
        new = _place(m.group(2))
        return m.group(1) + c.result(m.group(2), new) + m.group(3)
    return _MD.sub(sub, text)


def scrub_file(path: Path) -> tuple[str | None, Counter]:
    """(새 내용 | 바뀐 것 없음이면 None, 셈)"""
    c = Counter()
    raw = path.read_text(encoding="utf-8", errors="surrogateescape")
    if path.name == "events.jsonl":
        lines = []
        for ln in raw.splitlines():
            try:
                obj = json.loads(ln)
            except ValueError:
                lines.append(ln)
                continue
            lines.append(json.dumps(_walk(obj, c, {}), ensure_ascii=False))
        new = "\n".join(lines) + ("\n" if raw.endswith("\n") else "")
    elif path.suffix == ".json":
        obj = json.loads(raw)
        new = json.dumps(_walk(obj, c, {}), ensure_ascii=False, indent=2 if path.name == "summary.json" else None)
    else:
        new = scrub_text_md(raw, c)
    return (None if (c.results == 0 and c.urls == 0) else new), c


def run_dirs(root: Path, since: date) -> list[Path]:
    out = []
    for d in sorted(root.iterdir()):
        cands = [d] + ([x for x in sorted(d.iterdir()) if x.is_dir()] if d.is_dir() and not (d / "events.jsonl").exists() else [])
        for x in cands:
            m = _RUN.match(x.name)
            if x.is_dir() and m and (x / "events.jsonl").exists():
                y = m.group(1)
                if date(int(y[:4]), int(y[4:6]), int(y[6:])) >= since:
                    out.append(x)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m stewart.scrub_records", description=__doc__.split("\n")[0])
    ap.add_argument("--runs-dir", required=True)
    ap.add_argument("--since", required=True, help="YYYY-MM-DD(이날 포함)")
    ap.add_argument("--apply", action="store_true", help="실제로 바꾼다(없으면 보기만)")
    ap.add_argument("--backup-dir", help="--apply 때 원본을 여기 복사(기본 보관 안 함)")
    ap.add_argument("--list-out", help="실행 폴더별 셈을 TSV 로")
    a = ap.parse_args(argv)
    root = Path(a.runs_dir)
    if not root.is_dir():
        print(f"없는 폴더: {root}", file=sys.stderr)
        return 2
    try:
        since = date.fromisoformat(a.since)
    except ValueError:
        print("--since 는 YYYY-MM-DD", file=sys.stderr)
        return 2
    if a.backup_dir and not a.apply:
        print("--backup-dir 는 --apply 와 함께만", file=sys.stderr)
        return 2
    rows, total = [], {"runs": 0, "files": 0, "results": 0, "urls": 0}
    for d in run_dirs(root, since):
        per = {"results": 0, "urls": 0, "files": []}
        for name in FILES:
            f = d / name
            if not f.is_file():
                continue
            try:
                new, c = scrub_file(f)
            except (OSError, ValueError) as e:
                print(f"{d.name}\t{name}\t읽기 실패: {type(e).__name__}", file=sys.stderr)
                continue
            if new is None:
                continue
            per["results"] += c.results
            per["urls"] += c.urls
            per["files"].append(name)
            if a.apply:
                if a.backup_dir:
                    b = Path(a.backup_dir) / d.relative_to(root) / name
                    b.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, b)
                tmp = f.with_name(f.name + ".scrub-tmp")
                tmp.write_text(new, encoding="utf-8", errors="surrogateescape")
                shutil.copymode(f, tmp)
                os.replace(tmp, f)
        if per["files"]:
            total["runs"] += 1
            total["files"] += len(per["files"])
            total["results"] += per["results"]
            total["urls"] += per["urls"]
            rows.append(f"{d.relative_to(root)}\t{per['results']}\t{per['urls']}\t{','.join(per['files'])}")
    head = "실행 폴더\t지울 결과 수\t지울 주소 수\t파일"
    print(head)
    for r in rows:
        print(r)
    mode = "바꿈" if a.apply else "보기만(바꾸지 않음, --apply 로 바꾼다)"
    print(f"# {mode}: 실행 {total['runs']}개 · 파일 {total['files']}개 · 결과 {total['results']}개 · 주소 {total['urls']}개 "
          f"(--since {since.isoformat()}, {root})")
    if a.apply and a.backup_dir:
        print(f"# 경고: 원본을 {a.backup_dir} 에 보관했다. 보관본도 지워야 손질한 뜻이 있다.", file=sys.stderr)
    if a.list_out:
        Path(a.list_out).write_text(head + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
