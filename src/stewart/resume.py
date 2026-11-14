"""세션 이어 하기(--continue, --resume <run>).

- 실행이 끝날 때마다(chat 은 차례마다) 기록 폴더에 messages.json(모델에 보낸 대화 전체)을 남긴다.
- 이어 하기는 그 대화를 불러와 새 기록 폴더에서 계속한다(옛 기록은 건드리지 않는다).
  messages.json 이 없는 옛 기록은 events.jsonl 에서 다시 짠다(run_start·user·step·interrupt·reset).
- 시스템 지시는 지금 프로필로 새로 만든다(프로필·기억 파일이 바뀌었으면 바뀐 것을 쓴다). 첫 메시지 하나만 바뀌므로
  prefix cache 는 그 뒤 첫 요청에서 한 번만 깨진다.
- 결과 없이 남은 툴콜(중단 등)은 "결과 없음"으로 채운다(다음 요청이 형식 오류가 나지 않게).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

MESSAGES_FILE = "messages.json"
_RUN_DIR = re.compile(r"^\d{8}-\d{6}-")


class ResumeError(Exception):
    pass


def clean(messages: list[dict]) -> list[dict]:
    return [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]


def save_messages(run_dir: Path, messages: list[dict]) -> None:
    tmp = Path(run_dir) / (MESSAGES_FILE + ".tmp")
    from .nostore import scrub   # Brave 결과는 이어 하기용 대화에도 남기지 않는다
    tmp.write_text(json.dumps(scrub(clean(messages)), ensure_ascii=False), encoding="utf-8")
    tmp.replace(Path(run_dir) / MESSAGES_FILE)


def _events(run_dir: Path) -> list[dict]:
    f = Path(run_dir) / "events.jsonl"
    if not f.is_file():
        raise ResumeError(f"실행 기록이 아니다(events.jsonl 없음): {run_dir}")
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def run_meta(run_dir: Path) -> dict:
    for e in _events(run_dir):
        if e.get("kind") == "run_start":
            return e.get("meta") or {}
    raise ResumeError(f"run_start 가 없다: {run_dir}")


def rebuild_from_events(run_dir: Path) -> list[dict]:
    msgs: list[dict] = []
    for e in _events(run_dir):
        k = e.get("kind")
        if k == "run_start":
            msgs = list(e.get("messages") or [])
        elif k == "user":
            msgs.append({"role": "user", "content": e.get("content", "")})
        elif k == "step":
            msgs.extend(e.get("new_messages") or [])
        elif k == "partial":   # 스트리밍 도중 끊은 답(chat.turn 이 interrupt 앞에 남김)
            msgs.append({"role": "assistant", "content": (e.get("content") or "") + "\n[사람이 중간에 끊음]"})
        elif k == "interrupt":
            msgs.append({"role": "user", "content": "[사용자가 작업을 중단했다. 다음 입력을 기다려라.]"})
            msgs.append({"role": "assistant", "content": "알겠다. 중단했다."})
        elif k == "reset":
            msgs = msgs[:1]
    return msgs


def load_messages(run_dir: Path) -> list[dict]:
    f = Path(run_dir) / MESSAGES_FILE
    if f.is_file():
        return json.loads(f.read_text(encoding="utf-8"))
    return rebuild_from_events(run_dir)


def _text(c) -> str:
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(str(p.get("text") or "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return ""


def cut_turn_text(run_dir: Path, messages: list[dict]) -> str | None:
    """판 148: 옛 판(143 전 — meta 에 saves_turn_start 가 없는 기록)은 대화를 차례 끝에만 저장해, 차례 도중 허브가 꺼지면(승인 대기 등)
    그 차례의 사람 말이 messages.json 에 없다. 그 말을 돌려준다 — 대화에 넣지 않는다(옛 지시가 다시 실행되지 않게).
    허브는 화면에 "끊긴 말 — 다시 보내기" 칸으로만 보인다(사람이 눌러야 보낸다).
    조건(모두): 옛 기록 · 마지막 사람 말 뒤에 차례 끝(turn_end)이 없다 · /clear(reset) 뒤의 말이다 · messages 의 사람 말 가운데 그 글이 없다."""
    from .record import head_meta
    if head_meta(run_dir).get("saves_turn_start"):
        return None
    last, ended = None, True
    try:
        with open(Path(run_dir) / "events.jsonl", encoding="utf-8") as fh:
            for line in fh:
                if '"user"' not in line and '"reset"' not in line and '"turn_end"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                k = e.get("kind")
                if k == "reset":
                    last, ended = None, True
                elif k == "turn_end":
                    ended = True
                elif k == "user" and isinstance(e.get("content"), str) and e["content"].strip():
                    last, ended = (e.get("raw") if isinstance(e.get("raw"), str) and e["raw"].strip() else e["content"]), False
    except OSError:
        return None
    if last is None or ended:
        return None
    for m in messages:
        if m.get("role") == "user" and last.strip() in _text(m.get("content")):
            return None
    return last


def close_dangling(messages: list[dict], note: str = "(이전 세션이 끝나 결과가 없다)") -> list[dict]:
    """모든 assistant tool_calls 뒤에 그 id 의 tool 결과가 있게 채운다."""
    out: list[dict] = []
    i = 0
    while i < len(messages):
        m = messages[i]
        out.append(m)
        i += 1
        if m.get("role") == "assistant" and m.get("tool_calls"):
            j = i
            have = set()
            while j < len(messages) and messages[j].get("role") == "tool":
                have.add(messages[j].get("tool_call_id"))
                out.append(messages[j])
                j += 1
            for rc in m["tool_calls"]:
                if rc.get("id") not in have:
                    out.append({"role": "tool", "tool_call_id": rc.get("id"),
                                "name": (rc.get("function") or {}).get("name", ""), "content": note})
            i = j
    return out


def prepare(history: list[dict], system: str) -> list[dict]:
    """불러온 대화의 시스템 지시를 지금 것으로 바꾸고, 빈 툴콜을 채운다."""
    body = [m for m in history if m.get("role") != "system"]
    return [{"role": "system", "content": system}] + close_dangling(body)


def resolve_run(runs_dir: Path, ref: str) -> Path:
    """--resume 의 값: 기록 폴더 경로, 또는 runs 폴더 안의 폴더 이름."""
    for cand in (Path(ref).expanduser(), Path(runs_dir) / ref):
        if cand.is_dir() and (cand / "events.jsonl").is_file():
            return cand.resolve()
    raise ResumeError(f"이어 할 기록을 찾지 못했다: {ref} (runs 폴더 {runs_dir})")


def find_latest(runs_dir: Path, workdir: Path, chat: bool) -> Path:
    """--continue: 같은 작업 폴더의 가장 최근 기록(chat 이면 chat 기록만, run 이면 run 기록만)."""
    wd = str(Path(workdir).resolve())
    root = Path(runs_dir)
    if root.is_dir():
        for d in sorted((p for p in root.iterdir() if p.is_dir() and _RUN_DIR.match(p.name)), key=lambda p: p.name,
                        reverse=True):
            try:
                meta = run_meta(d)
            except ResumeError:
                continue
            if (meta.get("mode") == "chat") == chat and str(Path(meta.get("workdir", "")).resolve()) == wd:
                return d
    raise ResumeError(f"이 작업 폴더({wd})의 {'chat' if chat else 'run'} 기록이 없다(runs 폴더 {runs_dir})")
