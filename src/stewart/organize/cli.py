"""stewart organize review|apply|undo — 사람이 돌리는 명령(모델 도구가 아니다)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import apply as A


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="stewart organize", description="폴더 정리 계획 검토·실행·되돌리기")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("review", help="계획을 plan.md 로 펼치고 sha256 을 보여 준다")
    r.add_argument("plan")
    a = sub.add_parser("apply", help="검토한 계획 그대로 옮긴다(덮어쓰기·지우기 없음)")
    a.add_argument("plan")
    a.add_argument("--approve", required=True, help=f"review 가 보여 준 sha256 앞 {A.APPROVE_MIN}자 이상")
    a.add_argument("--dry-run", action="store_true", help="검사만 하고 옮기지 않는다")
    if hasattr(A, "undo"):
        u = sub.add_parser("undo", help="이동 기록을 역순으로 되돌린다")
        u.add_argument("journal", help="moves.jsonl")
        u.add_argument("--approve", required=True, help=f"기록 파일 sha256 앞 {A.APPROVE_MIN}자 이상")
        u.add_argument("--dry-run", action="store_true")
    x = ap.parse_args(argv)
    try:
        if x.cmd == "review":
            sha, md = A.review(Path(x.plan))
            print(f"검토 파일: {md}\nsha256: {sha}\n실행: stewart organize apply {x.plan} --approve {sha[:A.APPROVE_MIN]}")
            return 0
        if x.cmd == "apply":
            res = A.apply(Path(x.plan), x.approve, dry_run=x.dry_run)
        else:
            res = A.undo(Path(x.journal), x.approve, dry_run=x.dry_run)
    except PermissionError as e:
        print(f"stewart organize: {e}", file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in res.items() if k != "skipped_items"}, ensure_ascii=False, indent=1))
    for s in res.get("skipped_items", [])[:50]:
        print(f"  건너뜀 #{s.get('id', '')} {s['src']}: {s['why']}")
    return 0
