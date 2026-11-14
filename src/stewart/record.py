"""실행 기록: runs/<시각>-<프로필>/events.jsonl, transcript.md, summary.json, output.*"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

from .nostore import scrub


def _short(s: str | None, n: int = 1500) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + f"\n…(잘림, 전체 {len(s)}자 — events.jsonl에 전문)"


class Recorder:
    def __init__(self, runs_dir: Path, profile: str):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in profile)
        base = Path(runs_dir) / f"{stamp}-{safe}"
        Path(runs_dir).mkdir(parents=True, exist_ok=True)
        for n in range(1, 100):   # 같은 초에 같은 프로필로 또 열면(이어 하기 등) -2, -3 … 을 붙인다
            self.dir = base if n == 1 else base.with_name(f"{base.name}-{n}")
            try:
                self.dir.mkdir(exist_ok=False)
                break
            except FileExistsError:
                continue
        else:
            raise FileExistsError(f"기록 폴더를 만들 수 없다: {base}")
        self._ev = (self.dir / "events.jsonl").open("a", encoding="utf-8")
        self._ev_lock = threading.Lock()   # 허브 요청 실(학습 표시 등)과 차례 실이 같이 쓴다
        self._md: list[str] = []
        self.t0 = time.monotonic()

    def event(self, kind: str, **data) -> None:
        rec = {"t": round(time.monotonic() - self.t0, 3), "ts": datetime.now().isoformat(timespec="seconds"),
               "kind": kind, **scrub(data)}   # Brave 결과 구간은 기록에 남기지 않는다
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with self._ev_lock:
            self._ev.write(line)
            self._ev.flush()

    def md(self, text: str) -> None:
        self._md.append(scrub(text))

    # 사람이 읽을 요약 조각
    def md_header(self, meta: dict, task: str) -> None:
        b = meta["backend"]
        self.md(f"# stewart 실행 기록 — {meta['profile']}\n")
        self.md(f"- 시작: {meta['started']}\n- 엔진: stewart {meta['engine_version']}\n"
                f"- 백엔드: `{b['name']}` {b['model']} ({b['precision']}, ctx {b['max_context']}) {b['base_url']}\n"
                f"- 작업 폴더: `{meta['workdir']}`\n- 도구: {', '.join(meta['tools'])}\n"
                f"- 최대 단계: {meta['max_steps']}, 연속 오류 한도: {meta['max_consecutive_errors']}\n")
        if meta.get("memory_files"):
            self.md("- 프로젝트 기억 파일: " + ", ".join(
                f"`{m['path']}` {m['used']}/{m['chars']}자" + (" (잘림)" if m["truncated"] else "") for m in meta["memory_files"] if "used" in m) + "\n")
        self.md(f"## 과제\n\n```\n{_short(task, 4000)}\n```\n")

    def md_step(self, step: int, s: dict) -> None:
        tim = s.get("timings") or {}
        tps = tim.get("predicted_per_second")
        line = (f"## 단계 {step} — {s['elapsed_s']:.1f}초"
                + (f", 생성 {tim.get('predicted_n')} tok @ {tps:.1f} tok/s" if tps else "")
                + (f", 프롬프트 {tim.get('prompt_n')} tok" if tim.get("prompt_n") is not None else "")
                + (f" [{s['mode']}]" if s.get("mode") else ""))
        self.md(line + "\n")
        if s.get("content"):
            self.md(f"**모델:**\n\n```\n{_short(s['content'])}\n```\n")
        for tc in s.get("tool_calls") or []:
            self.md(f"**툴콜** `{tc['name']}` {_short(tc.get('raw_arguments', ''), 800)}\n")
            if tc.get("parse_error"):
                self.md(f"> 인자 파싱 실패: {tc['parse_error']}\n")
            if tc.get("denied"):
                self.md(f"> 행동 검사 거부: {tc['denied']}\n")
            if "result" in tc:
                ok = "성공" if tc["ok"] else "실패"
                self.md(f"→ {ok} ({tc['elapsed_s']:.2f}초)\n\n```\n{_short(tc['result'], 1200)}\n```\n")
        if s.get("note"):
            self.md(f"> {s['note']}\n")

    def close(self, summary: dict, final_output: str | None) -> None:
        summary, final_output = scrub(summary), scrub(final_output)
        if final_output is not None:
            ext = "json" if summary.get("output_format") == "json" else "md"
            (self.dir / f"output.{ext}").write_text(final_output, encoding="utf-8")
        (self.dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        s = summary
        self.md("## 요약\n")
        rows = [
            ("종료 사유", s["stop_reason"]), ("단계 수", s["steps"]), ("총 시간", f"{s['elapsed_s']:.1f}초"),
            ("모델 호출 시간 합", f"{s['model_time_s']:.1f}초"), ("툴콜 수", s["tool_calls"]),
            ("툴콜 실패(도구 오류)", s["tool_errors"]), ("툴콜 파싱 실패", s["parse_failures"]),
            ("출력 형식 실패", s["format_failures"]), ("반복 실패 경고", s.get("repeat_nudges", 0)),
            ("한국어 재요청", s.get("language_retries", 0)),
("생성 토큰 합", s["completion_tokens"]),
            ("프롬프트 토큰(마지막 단계)", s["last_prompt_tokens"]),
            ("평균 생성 속도", f"{s['gen_tok_per_s']:.1f} tok/s" if s["gen_tok_per_s"] else "-"),
            ("평균 프롬프트 처리 속도", f"{s['prompt_tok_per_s']:.1f} tok/s" if s["prompt_tok_per_s"] else "-"),
        ]
        if s.get("verify"):
            rows.append(("검증 명령", f"`{s['verify']['command']}` → exit {s['verify']['exit']}"))
        self.md("| 항목 | 값 |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows) + "\n")
        if s.get("verify"):
            self.md(f"검증 출력:\n\n```\n{_short(s['verify']['output'], 3000)}\n```\n")
        if final_output is not None:
            self.md(f"## 최종 출력\n\n```\n{_short(final_output, 6000)}\n```\n")
        (self.dir / "transcript.md").write_text("\n".join(self._md), encoding="utf-8")
        self._ev.close()


def head_meta(run_dir, lines: int = 20) -> dict:
    """판 139: 기록 앞쪽에서 run_start 의 meta(없으면 {}). 첫 줄이 다른 사건이어도 찾는다(판 132–138 의 대화 기록은
    생각 깊이 사건이 run_start 앞에 있다). 파일을 끝까지 읽지 않는다."""
    import json as _json
    from pathlib import Path as _Path
    try:
        with open(_Path(run_dir) / "events.jsonl", encoding="utf-8") as f:
            for _ in range(lines):
                line = f.readline()
                if not line:
                    break
                try:
                    e = _json.loads(line)
                except ValueError:
                    continue
                if e.get("kind") == "run_start" or ("kind" not in e and isinstance(e.get("meta"), dict)):   # kind 없는 옛 꼴도
                    return e.get("meta") or {}
    except OSError:
        pass
    return {}
