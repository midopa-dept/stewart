"""작업자: 허브가 띄우는 stewart chat 세션 하나. 편지함의 지시 하나를 받아 차례 하나를 돌고, 같은 번호로 보고를 쓴다.

python -m stewart.worker --session-dir <세션 폴더> --approval-fd <fd>|--approval-handle <핸들> [--resume <기록 폴더>]

- 확인 질문(실행 확인·작업 폴더 밖·설정 폴더·단계 더)은 ask/NNNN.json 에 적고, 승인 통로에서 그 번호의 답이 올 때까지 기다린다.
- [긴급] 지시는 지금 차례의 다음 단계 앞에 끼워 넣는다. 보고는 따로 쓰지 않는다(그 차례의 보고에 들어간다).
- control 파일이 pause·stop 이면 단계 경계에서 그 차례를 끊고(보고 reason=paused), 기록을 닫고 끝낸다.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path

from . import bounds as BND, config_guard as CG, tools as T
from .chat import UI, ChatSession, make_diff
from .hub import SessionDir, now, read_json, seq_name, write_json
from .record import Recorder

POLL_S = 0.1
LIVE_EVERY_S = 0.7     # 받는 중인 글을 상태 파일에 적는 간격
LIVE_KEEP = 1500       # 상태 파일에 남기는 받는 중인 글 끝부분
REPORT_MAX = 8000
COMMAND_MAX = 2000
DIFF_LINES = 400   # 승인 칸에 보내는 diff 줄 상한(화면은 앞 40줄을 보이고 나머지는 펼친다)
_PREFIX = lambda s: s.split("{")[0].strip()   # noqa: E731 — 질문 문구 상수의 앞부분(경로 자리 앞)


def ask_kind(prompt: str) -> str:
    """chat.py 가 묻는 자리: 작업 폴더 밖 쓰기·읽기, 설정 폴더, 단계 더, 그 밖은 실행 확인."""
    if _PREFIX(BND.ASK_WRITE) in prompt:
        return "outside_write"
    if _PREFIX(BND.ASK_READ) in prompt:
        return "outside_read"
    if _PREFIX(CG.ASK_PROMPT) in prompt:
        return "config"
    if "계속할까?" in prompt:
        return "more_steps"
    return "confirm"


def args_summary(env: T.ToolEnv | None, name: str, args: dict) -> dict:
    """사람이 판단할 거리: 셸 명령, 경로, 쓰기면 diff 앞 40줄(비밀 파일은 내용을 읽지 않는다)."""
    out: dict = {"tool": name}
    if name == "run_shell":
        cmd = str(args.get("command") or "")
        out["command"] = cmd if len(cmd) <= COMMAND_MAX else cmd[:COMMAND_MAX] + f"\n…(명령 {len(cmd)}자 중 앞 {COMMAND_MAX}자만 보임 — 실행은 전체)"
    path = args.get("path")
    if path:
        out["paths"] = [str(path)]
    if env is None or not path or name not in ("write_file", "edit_file", "multi_edit"):
        return out
    p = Path(os.path.expanduser(str(path)))
    p = p if p.is_absolute() else env.workdir / p
    if env.is_secret(p):
        out["diff"] = None
        return out
    try:
        before = p.read_text(encoding="utf-8") if p.is_file() else None
    except (OSError, UnicodeDecodeError):
        before = None
    after = None
    if name == "write_file":
        add = args.get("append") is True or str(args.get("append")).lower() == "true"
        after = ((before or "") + str(args.get("content") or "")) if add else str(args.get("content") or "")
    elif name == "edit_file":
        ok, txt, _ = T._apply_edit(before or "", str(args.get("old") or ""), str(args.get("new") or ""),
                                   bool(args.get("replace_all", False)))
        after = txt if ok else None
    else:
        txt = before or ""
        for e in args.get("edits") or []:
            if not isinstance(e, dict):
                txt = None
                break
            ok, txt, _ = T._apply_edit(txt, str(e.get("old") or ""), str(e.get("new") or ""), bool(e.get("replace_all", False)))
            if not ok:
                txt = None
                break
        after = txt
    out["diff"] = make_diff(before, after, str(path), max_lines=DIFF_LINES)[:DIFF_LINES] if after is not None else None
    return out


class Halt(KeyboardInterrupt):
    """허브가 pause·stop 을 요청함(단계 경계에서 차례를 끊는다)."""


class HubUI(UI):
    memory_writes = False   # 작업자는 기억을 읽기만 한다(쓰기는 메인·꿈꾸기)

    def __init__(self, sd: SessionDir, name: str, fd: int | None):
        super().__init__(out=io.StringIO(), color=False)
        self.sd, self.name, self.fd = sd, name, fd
        self._buf = b""
        self._reader = None
        self.halt: str | None = None
        self.env: T.ToolEnv | None = None     # 세션을 만든 뒤 붙인다(diff 를 만들 때 작업 폴더·비밀 파일 규칙)
        self._call: dict | None = None
        self._answers: dict[int, str] = {}
        self._hub_rules: list = []            # 허브 "늘 허용" 규칙(승인 통로로만 받는다)

    def _drain(self, timeout: float) -> bool:
        """승인 통로에서 온 줄을 읽어 둔다(답은 번호별로, 규칙은 통째로 바꿈). 통로가 닫혔으면 False."""
        if self.fd is None:
            return False
        if self._reader is None:      # select 는 Windows 파이프를 받지 않아 스레드로 읽는다(두 플랫폼 공용, WIN-5)
            from .osdep import PipeReader
            self._reader = PipeReader(os.fdopen(self.fd, "rb", buffering=0, closefd=True))
        chunk = self._reader.read(timeout)
        if chunk == b"":
            return False
        if chunk:
            self._buf += chunk
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "rules":
                from .allowrules import rules_from_payload
                self._hub_rules = rules_from_payload(msg.get("rules"))
            elif isinstance(msg.get("seq"), int) and isinstance(msg.get("answer"), str):
                self._answers[msg["seq"]] = msg["answer"]
        return True

    def hub_rules(self) -> list:
        if self.fd is not None:
            self._drain(0)
        return list(self._hub_rules)

    def call_context(self, name: str, args: dict) -> None:
        self._call = args_summary(self.env, name, args)

    # 화면 대신 아무 데도 쓰지 않는다(기록은 Recorder 가 남긴다)
    def say(self, s: str = "") -> None:
        pass

    # 진행 표시(U2-2): 지금 하는 일(now)과 받는 중인 글(live)을 상태 파일에 LIVE_EVERY_S 간격으로 적는다(허브 화면이 읽는다).
    # False 를 돌려 답 글은 지금처럼 assistant() 로 받게 한다
    def _now(self, what: str) -> None:
        self._live_txt = getattr(self, "_live_txt", "")
        self.sd.set_status(now={"what": what[:200], "since": time.time()})

    def tool(self, name: str, summary: str) -> None:
        self._tool_what = f"{name} {summary}"
        self._now(f"도구 실행 중: {self._tool_what}")

    def stream_start(self) -> None:
        self._live_txt, self._live_t = "", 0.0
        self._now("모델이 답을 쓰는 중")

    def stream_piece(self, text: str, think: str) -> None:
        if text:
            self._live_txt = getattr(self, "_live_txt", "") + text
        if time.monotonic() - getattr(self, "_live_t", 0.0) >= LIVE_EVERY_S and self._live_txt:
            self._live_t = time.monotonic()
            self.sd.set_status(live=self._live_txt[-LIVE_KEEP:])

    def stream_end(self) -> bool:
        if getattr(self, "_live_txt", ""):
            self.sd.set_status(live=None)
        self._live_txt = ""
        return False

    def ask(self, prompt: str) -> str:
        n = (sorted(int(p.stem) for p in self.sd.ask_dir.glob("[0-9][0-9][0-9][0-9].json")) or [0])[-1] + 1
        kind = ask_kind(prompt)
        q = {"seq": n, "session": self.name, "kind": kind, "prompt": prompt.strip(), "created": now(),
             "args_summary": self._call if kind != "more_steps" else None}
        write_json(self.sd.ask_dir / seq_name(n, "json"), q)
        self.sd.set_status(status="waiting_approval", ask_seq=n,
                           now={"what": "사람 승인 기다리는 중", "since": time.time()})
        try:
            ans = self._wait_answer(n)
        except EOFError:   # 멈춤·통로 닫힘: 이 질문은 답 없이 끝났다(대기열에서 뺀다)
            write_json(self.sd.ask_dir / seq_name(n, "json"), {**q, "cancelled": True})
            raise
        finally:   # 답을 받았으면(또는 끊겼으면) "승인 기다리는 중" 을 곧바로 지운다 — 그대로 두면 도구가 도는 내내 남았다(U2-8)
            self.sd.set_status(status="working", ask_seq=None,
                               now={"what": f"도구 실행 중: {getattr(self, '_tool_what', '')}"[:200], "since": time.time()})
        return ans

    def _wait_answer(self, n: int) -> str:
        """승인 통로에서 seq == n 인 답 하나. 통로가 없거나 닫히면 EOFError(chat 은 거부로 다룬다)."""
        if self.fd is None:
            raise EOFError
        while True:
            if n in self._answers:
                return self._answers.pop(n)
            if not self._drain(0.5):
                raise EOFError
            if n not in self._answers and self.sd.control() in ("pause", "stop"):
                raise EOFError

    def take_interjections(self) -> list[str]:
        ctl = self.sd.control()
        if ctl in ("pause", "stop"):
            self.halt = ctl
            raise Halt(ctl)
        out = []
        for n, body in self.sd.urgent_pending():
            self.sd.take(n, urgent=True)
            out.append(f"[긴급 지시 {n}]\n{body.strip()}")
        return out


def _last_assistant(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("content") and not m.get("tool_calls"):
            return str(m["content"])
    return ""


def turn_report(messages: list[dict], start: int, reason: str, reflect: dict | None = None) -> str:
    """이번 차례(messages[start:])의 최종 답. 답 없이 끝났으면 그렇다고 적고, 이번 차례의 마지막 모델 글(되짚기 글 등)을 붙인다.
    앞 차례의 답을 다시 내지 않는다(전에는 대화 전체에서 찾아 오류로 끝난 지시에 앞 지시의 보고가 다시 나갔다)."""
    text = _last_assistant(messages[start:])
    if reason in ("answered", "asked") and text:
        return text
    note = f"(이번 지시에 최종 답이 없다: 종료 {reason})"
    if text:
        what = "되짚기 글" if reflect and reflect.get("log") else "이번 차례의 마지막 모델 글"
        note += f"\n\n[{what}]\n{text}"
    return note


def _rel(paths, workdir: Path) -> list[str]:
    """보고에는 작업 폴더 기준 경로로 적는다(밖이면 그대로)."""
    out = []
    for p in sorted(paths):
        try:
            out.append(str(Path(p).resolve().relative_to(workdir.resolve())))
        except ValueError:
            out.append(str(p))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="stewart worker")
    ap.add_argument("--session-dir", required=True)
    ap.add_argument("--approval-fd", type=int)
    ap.add_argument("--approval-handle", type=int, help="Windows: 물려받은 승인 통로 핸들(osdep.child_pipe)")
    ap.add_argument("--resume")
    a = ap.parse_args(argv)
    from . import osdep
    fd = osdep.open_child_pipe(a.approval_fd, a.approval_handle)   # 받자마자 상속 불가(셸 도구의 자식에게 물려주지 않는다)
    sd = SessionDir(Path(a.session_dir))
    cfg = read_json(sd.config_file)
    from .cli import resolve
    from .resume import load_messages
    from .config import ConfigError
    try:
        profile, backend = resolve(cfg["profile"], cfg.get("backend"), cfg["workdir"])
    except ConfigError:   # stewart 프로필이 없으면 .stewart/agents(호환 .claude/agents)의 정의를 chat 프로필 위에 얹는다
        from . import projcfg as PC
        base, backend = resolve("chat", cfg.get("backend"), cfg["workdir"])
        agent = PC.find_agent(Path(cfg["workdir"]), cfg["profile"], PC.flags(base, interactive=True))
        if agent is None:
            raise
        profile = PC.agent_profile(agent, base)
    if os.environ.get("STEWART_HUB_ROLE") != "0":   # 보고 끝 '사용자 할 일:' 절 꼴(허브가 현황판에 올린다, 판 53)
        import dataclasses
        from .board import WORKER_NOTE
        profile = dataclasses.replace(profile, system=(profile.system or "").rstrip() + "\n\n" + WORKER_NOTE)
    history = load_messages(Path(a.resume)) if a.resume else None
    rec = Recorder(Path(cfg["runs_dir"]), "worker-" + cfg["name"])
    ui = HubUI(sd, cfg["name"], fd)
    sess = ChatSession(profile, backend, rec, ui=ui, history=history, resumed_from=a.resume, stream=True,
                       mode=cfg.get("mode") or "edits", default_mode="edits")
    ui.env = sess.env
    sd.set_status(status="idle", run_dir=str(rec.dir), pid=os.getpid())
    halted = None
    try:
        while halted is None:
            ctl = sd.control()
            if ctl in ("pause", "stop"):
                halted = ctl
                break
            nxt = sd.next_instruction()
            if nxt is None:
                time.sleep(POLL_S)
                continue
            n, head, body = nxt
            sd.take(n)
            sd.set_status(status="working", seq=n, last_event=now())
            steps0, files0, m0 = sess.st["steps"], set(sess.st["files_changed"]), len(sess.messages)
            r0 = len(sess._reflect_log)
            # 사람이 허브 화면에서 직접 보낸 지시는 그렇다고 밝혀 둔다(메인이 보낸 지시는 그대로)
            reason = sess.turn(("[사람이 허브 화면에서 직접 보낸 지시]\n" + body) if head.get("from") == "human" else body)
            if ui.halt:
                halted, reason = ui.halt, "paused"
            rsum = sess._reflect_log[-1] if len(sess._reflect_log) > r0 else None
            text = (turn_report(sess.messages, m0, reason, rsum) if reason != "paused"
                    else "(허브가 멈춰 이 지시를 끝내지 못했다)")
            sd.report(n, text[:REPORT_MAX], reason=reason, steps=sess.st["steps"] - steps0,
                      reflected=bool(rsum and rsum.get("used")),
                      files_changed=_rel(set(sess.st["files_changed"]) - files0, profile.workdir),
                      report_kind="질문" if reason == "asked" else "보고")
            sd.set_status(status="idle", outbox_seq=n, last_event=now(), now=None, live=None)
    finally:
        sess.close()
        sd.set_status(status="paused" if halted == "pause" else "stopped" if halted == "stop" else "idle",
                      run_dir=str(rec.dir), pid=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
