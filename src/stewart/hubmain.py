"""허브 안의 메인 세션과 허브 고리.

- 메인 = ChatSession + 세션 도구 6개(hubtools). 사람은 웹 화면에서 메인에게만 말한다.
- 메인의 확인 질문도 작업자와 같은 대기열(session=main)에 들어간다. 답은 허브 프로세스 안 큐로만 받는다(파일을 읽지 않는다).
- 허브 고리(스레드): Hub.tick() 을 돌려 승인 대기 알림·보고 도착 알림을 만든다. 보고 도착 알림은 메인의 다음 단계 앞에 끼워 넣는다.
"""

from __future__ import annotations

import json
import re
import queue
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from . import hubtools as HT
from .chat import ChatSession
from .hub import Hub, now
from .worker import HubUI

LOG_MAX = 500
# 허브 메인에만 붙는 역할 지시(U2-2 3a). 사람과의 대화를 가볍게 두고, 큰 일은 쪼개 맡긴다. STEWART_HUB_ROLE=0 이면 붙이지 않는다
HUB_MAIN_NOTE = """허브 메인으로서 일을 나눠 맡긴다:
- 서로 독립인 큰 일(바꿀 파일 묶음이 다른 둘 이상, 또는 오래 걸리는 구현·시험)은 session_spawn 으로 작업자를 띄워 동시에 돌린다. 하나씩 띄우고 기다리지 말고, 나눌 수 있으면 한 번에 여럿 띄운다. 작업자는 이 대화를 못 보니 task 에 목표·범위·바꿀 파일·끝의 기준·보고할 것을 다 적는다. 같은 파일을 두 작업자에게 주지 않는다.
- 넓게 읽어야 하는 조사(코드 찾기·구조 파악·여러 파일 대조·원인 찾기)는 delegate 로 맡기고 결과 글만 받는다. 이 대화에 긴 파일 내용을 쌓지 않는다.
- 맡기지 않는 것: 파일 한두 개 고침, 명령 한두 번으로 끝나는 확인, 이 대화의 앞 내용을 알아야 하는 일, 사람에게 바로 답할 수 있는 물음. 이런 것은 직접 한다.
- 작업자 보고가 오면 확인할 것만 확인하고 사람에게 짧게 알린다. 사람의 말에는 일을 하는 사이사이에도 먼저 짧게 답한다.
- 세 걸음 넘는 일은 시작할 때 todo_write 로 할 일 목록을 만든다(사람 화면에 목록이 뜬다). 하나를 시작할 때 in_progress, 끝내면 곧바로 done 으로 고친다. 작업자에게 맡긴 항목은 내용 앞에 작업자 이름을 적는다(예: "w-docs: README 고치기"). 계획을 글로만 쓰지 않는다.
- 일하며 말한다(사람이 대화하는 느낌을 받게, 걸음 단위로 — 도구마다 말하지는 않는다):
  ① 사람 말을 받으면 첫 도구 호출 전에 한두 문장으로 알아들은 것과 먼저 할 일("알겠습니다. 우선 …부터 보겠습니다").
  ② 걸음이 바뀔 때(조사 → 고침 → 확인), 뜻밖의 것을 찾았을 때, 방향을 바꿀 때 한 문장. 그 글은 도구 호출과 같은 답에 같이 쓴다.
  ③ 끝낼 때 무엇을 했고 무엇이 남았는지 짧게. 사람이 쓰는 말(한국어)로."""
TICK_S = 0.5
RESULT_KEEP = 20   # 화면 상태에 결과 전문을 싣는 최근 도구 수(그 앞은 끝 15줄 화면 줄만 — SSE 크기)


THINK_KEEP = 4000   # 생각 블록에 남기는 끝 글자 수(판 56)


class HubMainUI(HubUI):
    """메인 세션 화면: 글은 웹 화면용 기록에 쌓고, 묻기는 대기열 + 메모리 안 큐."""

    def __init__(self, hub: Hub):
        super().__init__(hub.sdir("main"), "main", None)
        self.log: deque = deque(maxlen=LOG_MAX)
        self._answers: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self.pending: list[str] = []          # 메인이 일하는 동안 사람이 보낸 글
        self.notices: list[str] = []          # 허브 알림(보고 도착)
        self.live = {"text": "", "think": 0}  # 지금 받는 중인 답(스트리밍 조각). 단계가 끝나면 비운다(답은 log 로 간다)
        self.now = None                       # 지금 하는 일 한 줄 {"what", "since"(epoch)} — 화면 맨 아래 "몇 초째"
        self.halt_req = threading.Event()     # 화면 [멈춤]: 단계 경계·생성 도중·승인 대기에서 지금 차례를 끊는다
        self.last_user = ""                   # 마지막 사람 말(오류 칸의 [다시 보내기])
        self.on_backend_error = None          # 허브가 모델 서버 감시를 깨우는 고리
        self.err_count: dict = {}
        self.full_narration = True
        self.goal_notes = True                # 새 사람 말이 앞 목표를 덮는다는 엔진 알림, 같은 결과 두 번째 알림(허브만)            # 도구와 함께 온 모델 글을 한 줄로 자르지 않는다(걸음 사이 말)             # 같은 오류(도구 + 오류 첫 줄) 몇 번째인지 — 화면 "같은 오류 N번째"(U2-8)

    def _add(self, kind: str, text: str) -> None:
        self.log.append({"ts": datetime.now().strftime("%H:%M:%S"), "kind": kind, "text": text})

    def say(self, s: str = "") -> None:
        if s.strip():
            self._add("info", s.strip())

    def assistant(self, s: str) -> None:
        self._add("assistant", s)

    def tool(self, name: str, summary: str) -> None:
        self._add("tool", f"{name} {summary}")
        self._tool_what = f"{name} {summary}"
        self.now = {"what": f"도구 실행 중: {self._tool_what}"[:200], "since": time.time()}

    def warn(self, s: str) -> None:
        t = s.strip()
        # chat 의 "차례 끝: X" 는 허브가 따로 turn_end 줄을 남기므로 meta 로(화면 대화에서 같은 뜻이 두 번 나오지 않게)
        self._add("meta" if t.startswith("차례 끝: ") else "warn", t)

    def dim(self, s: str) -> None:
        t = s.strip()
        if t.startswith("이어 감: "):   # 허브가 "앞 메인 대화를 이어 받았다" 를 따로 남긴다(같은 뜻 두 줄 막기)
            self._add("meta", t)
            return
        # 차례 요약 줄("[answered · 단계 2 · 프롬프트 …]")은 화면 대화에서 숨기고 작업 내역에만(토큰 줄이 같은 것을 보인다)
        self._add("meta" if re.match(r"^\[[\w-]+ · 단계 ", t) else "info", t)

    def reflect_card(self, phase: str, trigger: str, label: str, text: str) -> None:
        """되짚기(chat._reflect): 작업 카드에 "되짚는 중 — 같은 호출 4번" 줄, 끝나면 되짚기 글(접힘)."""
        if phase == "start":
            self._add("reflect", f"되짚는 중 — {label}")
            self.log[-1].update(trigger=trigger, label=label, state="running", body="")
            self.now = {"what": f"되짚는 중 — {label}(도구 없이 원인·다른 길 쓰기)", "since": time.time()}
            return
        for m in reversed(self.log):
            if m["kind"] == "reflect" and m.get("state") == "running":
                m.update(state="done", body=text, text=f"되짚음 — {label}")
                return
        self._add("reflect", f"되짚음 — {label}")
        self.log[-1].update(trigger=trigger, label=label, state="done", body=text)

    memory_writes = True    # HubUI(작업자)는 False — 메인은 쓴다

    def remembered(self, item: dict) -> None:
        """스스로 기억한 것: 대화에 "기억함: …" 한 줄([되돌리기])."""
        self.memory_op("add", item)

    def memory_op(self, op: str, item: dict) -> None:
        word = {"add": "기억함", "update": "기억 고침", "drop": "기억 버림"}[op]
        self._add("memory", f"{word}: {item['text']}")
        self.log[-1].update(mem_id=item["id"], mem_kind=item["kind"], mem_op=op, mem_source=item.get("source"),
                            mem_scope=item.get("scope", "user"))

    def backend_error(self, e) -> None:
        """모델 서버 오류: 한 줄(까닭 + 할 일)과 [다시 보내기] 칸. 옛 글(urlopen …)은 자세히로."""
        from .modelstat import explain
        kind, text = explain(e)
        self._add("error", text)
        self.log[-1].update(cls=kind, detail=(f"{e} {(e.body or '')[:300]}").strip(), retry=self.last_user)
        if self.on_backend_error is not None:
            try:
                self.on_backend_error(kind, text)
            except Exception:
                pass

    def info(self, s: str) -> None:
        self._add("info", s.strip())

    def diff(self, lines: list[str]) -> None:
        """파일을 고칠 때마다: 대화 칸에 "± 파일 +N −M" 접힌 한 줄(펼치면 diff 전체, U2-5)."""
        from .chat import diff_stat
        st = diff_stat(lines)
        self._add("diff", "\n".join(lines))
        self.log[-1]["stat"] = st

    def compacted(self, kind: str, info: dict) -> None:
        """chat 이 문맥을 줄였을 때 화면 한 줄(판 65)."""
        if kind == "nothing":
            self._add("compact", f"문맥 {info.get('pct')}% — 줄일 오래된 도구 결과도, 아직 접을 앞 대화도 없다(최근 몫은 원문 유지). "
                                 "계속 차면 [토큰] 탭의 [요약해 접기] 또는 새 메인")
            return
        if kind == "summary_fail":
            self._add("warn", f"문맥이 차서 앞 대화를 요약해 접기를 못 했다({info.get('error')}) — 다음 차례에 다시 해 본다. "
                              "급하면 작업 내역 [토큰] 탭의 [요약해 접기]")
            return
        if kind == "clip":
            self._add("compact", f"문맥 줄임: 오래된 도구 결과 {info.get('clipped', 0)}개를 줄였다(원문은 기록에, 필요하면 다시 읽는다)")
        else:
            self._add("compact", f"앞 대화를 요약으로 접음: 메시지 {info.get('folded_messages', 0)}개 → 요약 한 쪽"
                                 f"({'자동 — 문맥이 문턱을 넘음' if info.get('reason') == 'auto' else '사람이 누름'}, 원문은 기록에)")

    def tool_done(self, tc: dict) -> None:
        """도구 하나가 끝났다(chat 이 모든 길에서 부른다): 화면 작업 카드의 그 단계 줄에 ✓/✕·걸린 시간·같은 오류 몇 번째를 붙인다."""
        for m in reversed(self.log):
            if m["kind"] == "tool":
                if "ok" in m:
                    return
                ok = bool(tc.get("ok", True))
                res = str(tc.get("result") or "")
                m["result"] = res[:8000] + (f"\n…(모델은 {len(res)}자를 받았다 — 여기는 앞 8000자)" if len(res) > 8000 else "")   # 모델이 받은 그대로(카드 펼치기)
                if tc.get("name") == "todo_write" and ok and getattr(self, "env", None) is not None:
                    m["todos"] = [dict(t) for t in (getattr(self.env, "todos", None) or [])]   # 그때의 목록(앞 카드는 이것으로)
                m["ok"], m["elapsed"] = ok, round(float(tc.get("elapsed_s") or 0), 2)
                if tc.get("name") == "run_shell":   # [명령 복사] 는 줄인 한 줄이 아니라 명령 전문(판 65)
                    try:
                        m["command"] = str(json.loads(tc.get("raw_arguments") or "{}").get("command") or "")[:8000]
                    except (ValueError, AttributeError):
                        pass
                if not ok:
                    first = next((x.strip() for x in str(tc.get("result") or "").splitlines() if x.strip()), "")
                    sig = str(tc.get("name")) + "\0" + first[:160]
                    self.err_count[sig] = self.err_count.get(sig, 0) + 1
                    m["rep"] = max(self.err_count[sig], int(tc.get("repeat") or 0))
                return

    def turn_files(self, paths: list[str]) -> None:
        self._add("files", f"이번 차례에 바뀐 파일 {len(paths)}개: " + ", ".join(paths[:20]) + (" …" if len(paths) > 20 else ""))

    def ask(self, prompt: str) -> str:
        self.now = {"what": "사람 승인 기다리는 중 — 승인 칸", "since": time.time(), "ask": True}
        try:
            return super().ask(prompt)
        finally:   # 답을 받으면 곧바로 되돌린다(전에는 승인 뒤 도구가 도는 내내 "승인 기다리는 중" 이 남았다 — tag 45 실사용)
            self.now = {"what": f"도구 실행 중: {getattr(self, '_tool_what', '')}"[:200], "since": time.time()}

    def put_answer(self, seq: int, text: str) -> None:
        self._answers.put((seq, text))

    def _wait_answer(self, n: int) -> str:
        while True:
            if self.halt_req.is_set():
                raise EOFError          # HubUI.ask 가 이 질문을 취소로 적고, chat 은 거부로 받는다(다음 단계 앞에서 끊긴다)
            try:
                seq, text = self._answers.get(timeout=0.3)
            except queue.Empty:
                continue
            if seq == n:
                return text

    # 스트리밍 조각을 화면 상태(live)로 모은다. False 를 돌려 답 글은 지금처럼 assistant() 로 log 에 남긴다
    def stream_start(self) -> None:
        self.live = {"text": "", "think": 0, "since": time.time()}
        self.now = {"what": "모델 응답 기다리는 중(긴 문맥은 읽는 데 오래 걸린다)", "since": time.time()}

    def stream_piece(self, text: str, think: str) -> None:
        if self.halt_req.is_set():
            raise KeyboardInterrupt     # 생성 도중 끊기(chat_stream 이 받은 데까지를 partial 로 남긴다)
        if text:
            if not self.live["text"] and self.now and self.now.get("what", "").startswith("모델 응답 기다리는"):
                self.now = {"what": "모델이 답을 쓰는 중", "since": time.time()}
            self.live["text"] += text
        if think:
            if not self.live["think"] and self.now and self.now.get("what", "").startswith("모델 응답 기다리는"):
                self.now = {"what": "모델이 생각하는 중", "since": time.time()}
            self.live["think"] += len(think)
            if not self.live.get("think_since"):
                self.live["think_since"] = time.time()
            self.live["think_text"] = (self.live.get("think_text", "") + think)[-THINK_KEEP:]   # 생각 블록(판 56)

    def stream_end(self) -> bool:
        lv = self.live
        if lv.get("think"):   # 끝난 생각은 접힌 블록으로 남긴다: "생각 · 12초"(끝 THINK_KEEP 자만)
            self._add("think", lv.get("think_text", ""))
            self.log[-1].update(chars=lv["think"], secs=round(time.time() - (lv.get("think_since") or time.time()), 1),
                                cut=lv["think"] > THINK_KEEP)
        self.live = {"text": "", "think": 0}
        return False

    def take_interjections(self) -> list[str]:
        if self.halt_req.is_set():
            raise KeyboardInterrupt     # 단계 경계에서 끊기(chat.turn 이 interrupted 로 닫는다)
        with self._lock:
            out, self.pending = self.notices + self.pending, []
            self.notices = []
        return out


class HubMain:
    def __init__(self, hub: Hub, profile, backend, rec, mode: str = "edits", worker_profile: str = "chat",
                 worker_backend: str | None = None, history: list | None = None, resumed_from: str | None = None):
        self.hub = hub
        import dataclasses
        import os
        if os.environ.get("STEWART_HUB_ROLE") != "0":
            from .board import NOTE as BOARD_NOTE
            profile = dataclasses.replace(profile, system=(profile.system or "").rstrip() + "\n\n" + HUB_MAIN_NOTE + "\n" + BOARD_NOTE)
        hub.register_main(profile.workdir, mode)
        from .board import Board
        hub.board = Board(hub.dir, notify=hub._notify if hub._notify_on else None)   # 현황판(판 49): 새 사용자 할 일은 폰으로
        # 앞 허브의 메인이 묻다가 끝났으면 그 승인 건은 답할 데가 없다(새 메인은 그 차례를 잇지 않는다) — 취소로 닫는다
        from .hub import seq_name, write_json
        for q in hub.pending_asks("main"):
            if q and not q.get("cancelled") and isinstance(q.get("seq"), int):
                write_json(hub.sdir("main").ask_dir / seq_name(q["seq"], "json"),
                           {**q, "cancelled": True, "cancel_reason": "허브를 다시 켜 앞 메인 차례가 끝났다"})
                hub.event("main_ask_stale", seq=q["seq"])
        self.ui = HubMainUI(hub)
        self.ui.hub_rules = hub.allow_rules      # 메인은 허브 메모리의 "늘 허용" 규칙을 바로 쓴다
        hub.main_answer = self.ui.put_answer
        self.tools = HT.MainTools(hub, main_mode=mode, default_profile=worker_profile, default_backend=worker_backend)
        self._profile, self._backend, self._mode, self._runs_dir = profile, backend, mode, Path(rec.dir).parent
        self.sess = ChatSession(profile, backend, rec, ui=self.ui, stream=True, mode=mode, default_mode="edits",
                                extra_tools=(HT.DEFINITIONS, self.tools.execute), history=history, resumed_from=resumed_from)
        if history:
            self.ui._add("info", f"앞 메인 대화를 이어 받았다({resumed_from}). 새로 시작하려면 허브를 --new-main 으로 띄운다.")
        self.ui.env = self.sess.env
        self.sess.env.on_quota = self._quota_alert   # brave 이달 80%·한도 도달 → 폰·화면(판 61)
        from .modelstat import ModelWatch
        self._idle_since = time.monotonic()  # 꿈꾸기 자동의 한가함 잣대(판 54)
        self.watch = ModelWatch(backend)      # 모델 서버 점(허브가 start — 시험에서는 띄우지 않는다)
        self.ui.on_backend_error = lambda kind, text: self.watch.poke(kind if kind in ("down", "loading", "auth") else None, text)
        self.ui.sd.set_status(status="idle", run_dir=str(rec.dir))
        self._busy = threading.Lock()
        self._stop = threading.Event()
        self._loop: threading.Thread | None = None
        self._turn: threading.Thread | None = None
        self._turn0: tuple | None = None                 # 차례 시작 때 누계(이번 차례 토큰 몫을 빼서 낸다)
        self._hist_est: tuple = (None, 0)
        self._resumed = bool(history)

    # ── 사람 → 메인 ──
    def send(self, text: str, images: list | None = None) -> str:
        """사람의 글: 메인이 쉬면 새 차례를 시작하고, 일하는 중이면 다음 단계 앞에 끼워 넣는다.
        images: 화면에서 붙인 이미지 [(이름, 바이트)] — 메인이 쉴 때만(이미지는 끼워 넣지 않는다), vision 백엔드만."""
        text = text.strip()
        if not text and not images:
            return "empty"
        parts = []
        if images:
            from . import images as IMG
            if not self.sess.backend.vision:
                return "no_vision"
            if self.busy:
                return "busy_images"
            for name, data in images:
                part, desc = IMG.from_bytes(data, name, self.sess.rec.dir)   # ImageError 는 화면으로(까닭)
                parts.append(part)
                self.ui._add("info", f"이미지 붙임: {desc}")
        text = text or "(붙인 이미지를 봐라)"
        self.ui._add("user", text + (f"  [이미지 {len(parts)}장]" if parts else ""))
        if not parts:
            self.ui.last_user = text
        if text.startswith("/"):   # 프로젝트 명령(.stewart/commands, 호환 .claude/commands)은 펼쳐 보낸다. stewart 내장 명령은 허브 메인 칸에서 쓰지 않는다
            from .chat import COMMANDS
            exp = self.sess.expand_command(text)
            if exp is not None:
                self.ui._add("info", f"({text.split()[0]} → 프로젝트 명령 본문을 보낸다)")
                text = exp
            elif text.split()[0] == "/compact":   # 허브 칸에서도 줄이기(판 65): "/compact" = 도구 결과 줄이기, "/compact 요약" = 앞 대화 접기
                self.ui._add("user", text)
                try:
                    r = self.compact_now("summary" if "요약" in text or "summary" in text else "clip")
                    if r.get("ok") and r.get("clipped"):
                        pass   # compacted 고리가 한 줄을 남겼다
                except RuntimeError as e:
                    self.ui._add("warn", f"/compact: {e}")
                return "compacted"
            elif text.split()[0] in COMMANDS:
                self.ui._add("warn", f"{text.split()[0]} 는 stewart chat 의 내장 명령이다. 허브 메인 칸에서는 쓰지 않는다.")
                return "builtin_ignored"
        if not self._busy.acquire(blocking=False):
            with self.ui._lock:
                self.ui.pending.append(text)
            return "queued"
        self._turn = threading.Thread(target=self._run_turn, args=(text, parts or None), daemon=True)
        self._turn.start()
        return "started"

    def _run_turn(self, text: str, images: list | None = None) -> None:
        try:
            self.ui.sd.set_status(status="working")
            st = self.sess.st
            self._turn0 = (st.get("prompt_tokens_total") or 0, st.get("completion_tokens") or 0, st.get("steps") or 0,
                           round(st.get("model_time_s") or 0, 1))
            n0, f0 = len(self.sess.messages), self.sess.st.get("summaries", 0)
            reason = self.sess.turn(text, images=images)
            if self.sess.st.get("summaries", 0) == f0:   # 차례 안에서 앞 대화를 접었으면 자리(n0)가 바뀌어 이번에는 건너뛴다
                self._extract_later(n0)
            try:   # 차례 끝 꼬리표: 열린 사용자 할 일 + 승인 대기(판과 대기열이 출처)
                tail = self.hub.board.tail(self.hub.queue())
                if tail:
                    self.ui._add("tail", tail)
            except Exception:
                pass
            self.ui._add("turn_end", f"[차례 끝: {reason}]" + (" — 화면에서 멈춤" if self.ui.halt_req.is_set() else ""))
            self.ui.log[-1]["reason"] = "stopped" if self.ui.halt_req.is_set() else reason
        except Exception as e:   # 메인이 죽으면 화면에 남기고 다음 입력을 받는다
            self.ui._add("warn", f"메인 오류: {type(e).__name__}: {e}")
        finally:
            self.ui.halt_req.clear()
            self.ui.live = {"text": "", "think": 0}
            self.ui.now = None
            self.ui.sd.set_status(status="idle")
            self._idle_since = time.monotonic()
            self._busy.release()

    # ── 꿈꾸기 자동(판 54): 한가할 때·닫을 때, 모델 서버가 준비됐을 때만(깨우려고 부르지 않는다) ──
    def _quota_alert(self, text: str) -> None:
        self.ui._add("warn", "웹 검색: " + text)
        if self.hub._notify_on:
            try:
                self.hub._notify("stewart 웹 검색: " + text)
            except Exception:
                pass

    def _dream_due(self) -> bool:
        """고리에서 쓰레드를 띄울지 가볍게(모델·파일 안 봄): 한가한 시간·다시 보기 간격만."""
        from . import dream as DR
        nowm = time.monotonic()
        return (getattr(self.sess, "mem", None) is not None and DR.auto_enabled()
                and nowm - getattr(self, "_idle_since", nowm) >= DR.AUTO_IDLE_S
                and nowm - getattr(self, "_dream_at", -1e9) >= DR.AUTO_RETRY_S)

    def _dream_ready(self) -> bool:
        return self.watch.status.get("state") == "ok"

    def maybe_dream(self, closing: bool = False, ask_model=None) -> dict | None:
        from . import dream as DR
        mem = getattr(self.sess, "mem", None)
        if mem is None or not DR.auto_enabled() or getattr(self, "_dreaming", False):
            return None
        nowm = time.monotonic()
        if not closing:
            if self.busy or nowm - getattr(self, "_idle_since", nowm) < DR.AUTO_IDLE_S \
                    or nowm - getattr(self, "_dream_at", -1e9) < DR.AUTO_RETRY_S:
                return None
        self._dreaming, self._dream_at = True, nowm
        try:
            r = DR.auto(mem.dir, ask_model or DR.model_caller(self.sess.backend), mem.runs_dir, self._dream_ready)
        finally:
            self._dreaming = False
        if r["status"] != "할 것 없음":
            done = [x["subject"] for x in r["results"] if str(x.get("status", "")).startswith("쪽")]
            self.hub.event("dream_auto", status=r["status"], subjects=done or r.get("subjects") or [])
            if done:
                self.ui._add("memory", f"기억 정리(꿈꾸기): {len(done)}개 대상의 쪽·색인을 고쳤다 — 되돌리기는 기억 폴더 dream-log")
        return r

    # ── 지난 세션(U2-3 2c) ──
    def past_mains(self, limit: int = 10) -> list[dict]:
        """이 프로젝트의 지난 허브 메인 대화(지금 것 빼고, 새것부터): {run_dir, started, first, turns}."""
        from .trash import past_mains
        return past_mains(self._runs_dir, Path(self._profile.workdir), str(self.sess.rec.dir), limit=limit)

    def manager(self):
        """지난 세션 정리(지우기·되돌리기·이름·보관, trash.py)."""
        from .trash import Manager
        return Manager(self.hub, self._runs_dir, Path(self._profile.workdir), current_main=str(self.sess.rec.dir))

    def reopen_main(self, run_dir: str) -> str:
        """지난 메인 대화 하나로 메인을 바꿔 연다(지금 메인 대화는 기록에 그대로 남는다). 일하는 중이면 거절."""
        from .record import Recorder
        from .resume import load_messages
        if run_dir not in {x["run_dir"] for x in self.past_mains(50)}:
            raise ValueError("지난 메인 대화 목록에 없는 기록")
        if not self._busy.acquire(blocking=False):
            raise RuntimeError("메인이 일하는 중이다. 멈추거나 끝난 뒤 다시 연다")
        try:
            history = load_messages(Path(run_dir))
            old = self.sess
            rec = Recorder(self._runs_dir, "hub-main")
            self.sess = ChatSession(self._profile, self._backend, rec, ui=self.ui, stream=True, mode=self._mode,
                                    default_mode="edits", extra_tools=(HT.DEFINITIONS, self.tools.execute),
                                    history=history, resumed_from=run_dir)
            self.ui.env = self.sess.env
            self.sess.env.on_quota = self._quota_alert
            self._resumed, self._turn0 = True, None
            self.ui.sd.set_status(run_dir=str(rec.dir))
            try:
                old.close()
            except Exception:
                pass
            self.ui._add("info", f"지난 메인 대화를 다시 열었다({run_dir}).")
            return str(rec.dir)
        finally:
            self._busy.release()

    def _extract_later(self, n0: int) -> None:
        """차례 끝 기억 고르기(판 52): 모델 서버가 준비됐을 때만, 이번 차례의 사람 말과 마지막 답만(문맥을 다 보내지 않게),
        도구 없는 짧은 한 번을 따로 돌린다(사람이 기다리지 않게). STEWART_MEMORY_EXTRACT=0 이면 끔."""
        import os
        mem = getattr(self.sess, "mem", None)
        if mem is None or os.environ.get("STEWART_MEMORY_EXTRACT") == "0" or self.watch.status.get("state") not in ("ok", "unknown"):
            return
        from .images import text_of
        said, answer = [], ""
        for m in self.sess.messages[n0:]:
            c = text_of(m.get("content")) if m.get("content") is not None else ""
            if m.get("role") == "user" and c and not c.startswith("[엔진]"):
                said.append(c.split("\n\n[엔진]")[0].strip())
            elif m.get("role") == "assistant" and c and not m.get("tool_calls"):
                answer = c
        if not said:
            return

        def run():
            from . import memory as MEM
            from .backend import BackendError, chat
            try:
                resp, _dt = chat(self.sess.backend, [{"role": "system", "content": "너는 기억을 고르는 도우미다. 지시한 꼴의 JSON 줄만 쓴다."},
                                                     {"role": "user", "content": MEM.extract_prompt(mem, said, answer)}],
                                 None, max_tokens=MEM.EXTRACT_MAX_TOKENS, thinking=False)
            except (BackendError, OSError, ValueError):
                return
            msg = ((resp.get("choices") or [{}])[0].get("message") or {})
            for d in MEM.apply_ops(mem, MEM.extract_ops(msg.get("content") or "")):
                self.ui.memory_op(d["op"], d["item"])
            self.sess.rec.event("memory_extract", ops=len(MEM.extract_ops(msg.get("content") or "")))
        t = threading.Thread(target=run, daemon=True)
        t.start()
        self._extract_t = t

    def compact_now(self, mode: str = "clip") -> dict:
        """사람이 누른 줄이기: clip = 오래된 도구 결과 줄이기, summary = 앞 대화를 요약으로 접기. 일하는 중이면 RuntimeError."""
        if mode not in ("clip", "summary"):
            raise ValueError("mode 는 clip 또는 summary")
        if not self._busy.acquire(blocking=False):
            raise RuntimeError("메인이 일하는 중이다 — 차례가 끝난 뒤에 누른다")
        try:
            if mode == "clip":
                n = self.sess.compact(force=True)
                if not n:
                    self.ui._add("compact", "문맥 줄이기: 더 줄일 오래된 도구 결과가 없다")
                return {"ok": True, "clipped": n}
            r = self.sess.summarize(reason="manual")
            if r is None:
                self.ui._add("compact", "요약해 접기: 접을 앞 대화가 없거나(최근 두 차례는 남긴다) 요약을 받지 못했다")
            return {"ok": r is not None, **(r or {})}
        finally:
            self._busy.release()

    def stop_turn(self) -> str:
        """화면 [멈춤]: 메인 차례를 끊는다. 도는 셸 명령은 끝날 때까지 기다린 뒤 다음 단계 앞에서 끊긴다."""
        if not self.busy:
            return "idle"
        with self.ui._lock:
            self.ui.pending = []        # 일하는 동안 보낸 글도 버린다(멈춘 뒤 새로 말한다)
        self.ui.halt_req.set()
        self.ui._add("warn", "멈추는 중…")
        # 받는 중인 모델 응답(긴 프롬프트 읽기·생성 포함)을 바로 끊는다 — 전에는 응답이 끝날 때까지 기다려 몇 분 늦었다(U2-8)
        from .backend import abort_streams
        try:
            abort_streams()
        except Exception:
            pass
        return "stopping"

    @property
    def busy(self) -> bool:
        return self._busy.locked()

    def wait_idle(self, timeout_s: float = 30) -> bool:
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            if not self.busy:
                return True
            time.sleep(0.05)
        return False

    # ── 허브 고리 ──
    def tick(self) -> dict:
        r = self.hub.tick()
        notes = self.hub.take_notices()
        if notes:
            with self.ui._lock:
                self.ui.notices += notes
            for n in notes:
                self.ui._add("hub", n)
        return r

    def start_loop(self) -> None:
        def loop():
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception as e:   # 고리는 멈추지 않는다
                    self.hub.event("loop_error", error=type(e).__name__)
                try:
                    if not getattr(self, "_dreaming", False) and not self.busy and self._dream_due():
                        threading.Thread(target=self.maybe_dream, daemon=True).start()
                except Exception as e:
                    self.hub.event("dream_error", error=type(e).__name__)
                self._stop.wait(TICK_S)
        self._loop = threading.Thread(target=loop, daemon=True)
        self._loop.start()

    def close(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        self._stop.set()
        try:   # 닫을 때 꿈꾸기(새 메모가 N개 넘고 모델 서버가 준비됐을 때만)
            self.maybe_dream(closing=True)
        except Exception as e:
            self.hub.event("dream_error", error=type(e).__name__)
        self.watch.close()
        if self._loop is not None:
            self._loop.join(timeout=5)
        try:
            self.sess.close()
        finally:
            self.hub.close()

    def state(self) -> dict:
        """웹 화면이 보는 상태(세션 목록·대기열·메인 기록). 대기열 항목의 명령·경로·diff 는 화면에만 간다."""
        live = self.ui.live
        log = list(self.ui.log)[-200:]
        keep = {id(m) for m in [x for x in log if x.get("kind") == "tool"][-RESULT_KEEP:]}
        log = [m if "result" not in m or id(m) in keep else {k: v for k, v in m.items() if k != "result"} for m in log]   # 상태 크기
        return {"ts": now(), "busy": self.busy, "sessions": self.hub.list(), "queue": self.hub.queue(),
                "main_log": log, "stopping": self.ui.halt_req.is_set(),
                "live": {"text": live["text"][-4000:], "think": live["think"], "think_text": live.get("think_text", "")[-4000:],
                         "think_since": live.get("think_since")} if self.busy else None,
                "now": self.ui.now if self.busy else None, "server_time": time.time(),
                "todos": list(getattr(self.sess.env, "todos", None) or []), "main_run": str(self.sess.rec.dir),
                "vision": bool(self.sess.backend.vision), "pending": list(self.ui.pending),
                "backend": {"name": self.sess.backend.name, "model": self.sess.backend.model},
                "tokens": self.tokens(), "resume_note": self.resume_note(), "model": self.watch.status,
                "web": getattr(self.sess, "web_status", None),
                "bans": [b.view() for b in getattr(self.sess, "bans", [])], "board": self.hub.board.view()}

    def tokens(self) -> dict:
        """화면 문맥 막대·토큰 줄: 지난 단계 입력(= 지금 문맥), 창, 줄이기 선, 누계, 이번(마지막) 차례 몫."""
        st, comp = self.sess.st, self.sess.comp
        at = getattr(comp, "next_at", None)
        out = {"last_prompt": st.get("last_prompt_tokens") or 0, "window": getattr(comp, "full_context", None) or self.sess.backend.max_context,
               "compact_at": round(at) if isinstance(at, (int, float)) and at != float("inf") else None,
               "prompt_total": st.get("prompt_tokens_total") or 0, "completion_total": st.get("completion_tokens") or 0,
               "steps": st.get("steps") or 0, "compactions": st.get("compactions") or 0, "summaries": st.get("summaries") or 0, "model_s": round(st.get("model_time_s") or 0, 1)}
        t0 = self._turn0
        if t0 is not None:
            out["turn"] = {"prompt": out["prompt_total"] - t0[0], "completion": out["completion_total"] - t0[1],
                           "steps": out["steps"] - t0[2], "model_s": round(out["model_s"] - t0[3], 1)}
        return out

    def resume_note(self) -> str | None:
        """이어 받은 긴 대화의 첫 차례: 서버가 앞 대화를 처음 읽어 오래 걸린다는 안내(첫 차례 답이 오면 사라진다)."""
        if not self._resumed or (self.sess.st.get("last_prompt_tokens") or 0) > 0:
            return None
        key = id(self.sess)
        if self._hist_est[0] != key:
            from .compact import est_tokens
            try:
                n = est_tokens(json.dumps(self.sess.messages, ensure_ascii=False))
            except (TypeError, ValueError):
                n = 0
            self._hist_est = (key, n)
        n = self._hist_est[1]
        if n < 8000:
            return None
        return (f"앞 대화가 길다(≈{n // 1000}k 토큰). 첫 차례는 모델 서버가 이것을 처음부터 읽어 몇 분 걸릴 수 있다 — "
                "멈춘 것이 아니다. 그다음 차례부터는 빨라진다.")


DEFAULT_PORT = 8765


def hub_config(path: str | None = None) -> dict:
    """[hub] 설정: notify_url · state_dir · worktree_root("{repo}" 자리 글) · port · hosts(앞단 이름, 예: tailscale serve)."""
    import os
    import tomllib
    from pathlib import Path
    p = Path(os.path.expanduser(path or os.environ.get("STEWART_HUB_CONFIG")
                                or os.path.join(os.environ.get("XDG_CONFIG_HOME") or "~/.config", "stewart", "hub.toml")))
    if not p.is_file():
        return {}
    d = tomllib.loads(p.read_text(encoding="utf-8")).get("hub") or {}
    known = {"notify_url", "state_dir", "worktree_root", "port", "hosts", "ntfy_url", "ntfy_token_file", "hub_url", "ntfy_priority", "ui",
             "accounts", "listen", "user_root"}
    bad = sorted(set(d) - known)
    if bad:
        from .config import ConfigError
        raise ConfigError(f"{p}: [hub] 에 모르는 키 {bad} (쓸 수 있는 것: {sorted(known)})")
    if "accounts" in d and not isinstance(d["accounts"], bool):
        from .config import ConfigError
        raise ConfigError(f"{p}: [hub] accounts 는 true 또는 false")
    return d


def _strip_env(text: str) -> str:
    """사람 입력 앞에 엔진·허브가 붙인 "[…]" 블록(첫 빈 줄까지)을 뺀 첫 줄."""
    t = (text or "").strip()
    while t.startswith("["):
        head, sep, rest = t.partition("\n\n")
        if not sep:
            first = t.splitlines()[0]
            return t.splitlines()[1].strip() if first.endswith("]") and len(t.splitlines()) > 1 else ""
        t = rest.strip()
    return t.splitlines()[0].strip() if t else ""


def last_main(hub: Hub) -> tuple[list | None, str | None]:
    """이 허브의 마지막 메인 기록(messages.json)을 이어 받을 대화로. 없거나 못 읽으면 (None, None)."""
    from pathlib import Path
    from .resume import load_messages
    run = (hub.sdir("main").status() or {}).get("run_dir")
    if not run or not (Path(run) / "messages.json").is_file():
        return None, None
    try:
        msgs = load_messages(Path(run))
    except Exception:   # 망가진 기록이면 새로 시작한다(허브는 뜬다)
        return None, None
    return (msgs, run) if msgs else (None, None)


def default_state_dir(project) -> "Path":
    import hashlib
    import os
    from pathlib import Path
    root = Path(os.path.expanduser(os.environ.get("XDG_STATE_HOME") or "~/.local/state")) / "stewart" / "hub"
    proj = Path(project).resolve()
    return root / f"{proj.name}-{hashlib.sha256(str(proj).encode()).hexdigest()[:8]}"


def hub_url(state_dir) -> int:
    """`stewart hub url`: 떠 있는(또는 마지막에 떴던) 허브의 접속 주소와 로그인 토큰을 다시 보인다."""
    import sys
    from pathlib import Path
    sd = Path(state_dir)
    u, t = sd / "hub-url", sd / "hub-token"
    if not u.is_file():
        print(f"허브 기록이 없다: {sd} (허브를 한 번 띄운 뒤 다시)", file=sys.stderr)
        return 1
    print(f"접속: {u.read_text(encoding='utf-8').strip()}")
    if t.is_file():
        print(f"로그인 토큰: {t.read_text(encoding='utf-8').strip()}  (파일 {t})")
    return 0


def run_sessions(a) -> int:
    """`stewart sessions …`: 허브의 지난 세션 정리(화면과 같은 것 — 목록·지우기(휴지통)·되돌리기·비우기·이름·보관).
    허브가 떠 있어도 된다(같은 파일을 쓴다). 대화 기록만 옮기고 작업 폴더는 건드리지 않는다."""
    import os
    import sys
    from pathlib import Path
    from .trash import KEEP_DAYS_NOTE, Manager
    cfg = hub_config(a.config)
    project = Path(a.project or os.getcwd()).resolve()
    state_dir = Path(os.path.expanduser(a.state_dir or cfg.get("state_dir") or str(default_state_dir(project))))
    if not (state_dir / "sessions.json").is_file():
        print(f"허브 기록이 없다: {state_dir}", file=sys.stderr)
        return 1
    hub = Hub(state_dir, Path(a.runs_dir))
    cur = (hub.registry()["sessions"].get("main") or {}).get("run_dir")
    mg = Manager(hub, Path(a.runs_dir), project, current_main=cur)

    def key_of(k: str) -> tuple[str, str]:
        if k in hub.registry()["sessions"] and k != "main":
            return "worker", k
        for m in mg.mains():
            if k in (m["run_dir"], Path(m["run_dir"]).name):
                return "main", m["run_dir"]
        raise ValueError(f"모르는 세션: {k} (stewart sessions list 의 이름 또는 메인 기록 폴더 이름)")

    act = a.sessions_action
    try:
        if act == "list":
            ws, ms = mg.workers(), mg.mains()
            show = lambda x: x["archived"] == a.archived   # noqa: E731
            print("작업자(쉬는·끝낸):")
            for w in filter(show, ws):
                print(f"  {w['name']:<20} {w['status']:<8} {(w['last_event'] or '')[:16]}  {w['title']}")
            print("지난 메인 대화:")
            for m in filter(show, ms):
                print(f"  {Path(m['run_dir']).name:<40} 사람 말 {m['turns']:>3}  {m['title'] or m['first'][:50]}")
            n_arch = sum(1 for x in ws + ms if x["archived"])
            if n_arch and not a.archived:
                print(f"(보관 {n_arch}개 — --archived 로 보기)")
            t = mg.trash()
            if t:
                print(f"휴지통 {len(t)}개 — stewart sessions trash")
            return 0
        if act == "trash":
            for t in mg.trash():
                when = datetime.fromtimestamp(t["deleted_at"]).strftime("%Y-%m-%d %H:%M") if t.get("deleted_at") else ""
                print(f"  {t['id']}  {t['kind']:<6} {when}  {t['label']}")
            print(f"({KEEP_DAYS_NOTE}: stewart sessions restore <id>)")
            return 0
        if act == "delete":
            items = mg.older_than(a.older_days) if a.older_days is not None else [key_of(k) for k in a.keys]
            if not items:
                print("지울 것이 없다")
                return 0
            for kind, key in items:
                tid = mg.delete(kind, key)
                print(f"휴지통으로: {key} → {tid}")
            print(f"(작업 폴더는 그대로. {KEEP_DAYS_NOTE})")
            return 0
        if act == "restore":
            r = mg.restore(a.keys[0])
            print(f"되돌림: {r['label']}")
            return 0
        if act == "empty":
            if not a.yes:
                print("휴지통 비우기는 되돌릴 수 없다. 정말이면 --yes 를 붙인다", file=sys.stderr)
                return 2
            print(f"휴지통에서 {mg.empty(a.older_days)}개를 없앴다")
            return 0
        if act == "rename":
            kind, key = key_of(a.keys[0])
            mg.rename(kind, key, " ".join(a.keys[1:]))
            print("이름 바꿈")
            return 0
        if act in ("archive", "unarchive"):
            for k in a.keys:
                kind, key = key_of(k)
                mg.archive(kind, key, act == "archive")
            print("보관함으로" if act == "archive" else "보관 풂")
            return 0
    except (ValueError, RuntimeError, OSError) as e:
        print(f"안 됨: {e}", file=sys.stderr)
        return 1
    return 2


class UserHubs:
    """계정 모드(U2-7): 사람마다 따로 도는 허브 하나씩 — 상태 <허브 폴더>/users/<이름>, 기록 <runs>/users/<이름>,
    작업 폴더 <user_root>/<이름>(처음 로그인 때 만든다). 세션 목록·대화·승인 대기열·작업 폴더가 그 사람 것만 보인다.
    메인의 파일 도구는 작업 폴더 밖 읽기를 거부한다(read_outside = deny — 남의 폴더를 도구로 읽지 않게)."""

    def __init__(self, state_dir, runs_dir, user_root, a):
        self.state_dir, self.runs_dir, self.user_root, self.a = Path(state_dir), Path(runs_dir), Path(user_root), a
        self._lock = threading.Lock()
        self._hubs: dict[str, tuple] = {}

    def folder(self, name: str) -> Path:
        from .accounts import check_name
        check_name(name)
        return self.user_root / name

    def get(self, name: str) -> tuple:
        with self._lock:
            if name in self._hubs:
                return self._hubs[name]
            from .cli import resolve
            from .record import Recorder
            wd = self.folder(name)
            wd.mkdir(parents=True, exist_ok=True)
            hub = Hub(self.state_dir / "users" / name, self.runs_dir / "users" / name)
            profile, backend = resolve(self.a.profile, self.a.backend, str(wd))
            profile.read_outside = "deny"
            profile.memory_root = str(self.state_dir / "users" / name / "memory")   # 기억도 사람마다(판 50 과 함께)
            history, prev = last_main(hub)
            rec = Recorder(self.runs_dir / "users" / name, "hub-main")
            main = HubMain(hub, profile, backend, rec, mode=self.a.mode, worker_profile=self.a.worker_profile or self.a.profile,
                           worker_backend=self.a.backend, history=history, resumed_from=prev)
            hub.recover()
            main.start_loop()
            self._hubs[name] = (main, hub)
            return self._hubs[name]

    def close_all(self) -> None:
        with self._lock:
            items = list(self._hubs.values())
            self._hubs.clear()
        for main, hub in items:
            for n in list(hub.procs):
                try:
                    hub.pause(n, timeout_s=30, resume_on_start=True)
                except Exception:
                    pass
            try:
                main.close()
            except Exception:
                pass


def _state_dir(a, cfg, project) -> "Path":
    import os
    return Path(os.path.expanduser(a.state_dir or cfg.get("state_dir") or str(default_state_dir(project))))


def run_hub_accounts(a, cfg, project, state_dir) -> int:
    """계정 모드 허브: 로그인한 사람마다 따로 도는 허브. 듣는 주소는 [hub] listen(정한 주소 하나), 없으면 127.0.0.1."""
    import os
    import sys
    from .accounts import Sessions, Store
    from .web import WebApp
    root = cfg.get("user_root")
    if not root:
        print("stewart hub: [hub] accounts = true 이면 user_root(사람별 작업 폴더의 뿌리)를 정해야 한다", file=sys.stderr)
        return 2
    store = Store(state_dir)
    if not store.users():
        print(f"stewart hub: 사용자가 없다. 먼저 더한다: stewart hub-user add <이름> --state-dir {state_dir}", file=sys.stderr)
        return 2
    users = UserHubs(state_dir, a.runs_dir, Path(os.path.expanduser(str(root))), a)
    listen = str(cfg.get("listen") or "127.0.0.1")
    try:
        web = WebApp(None, None, None, host=listen, port=a.port or int(cfg.get("port") or DEFAULT_PORT),
                     extra_hosts=cfg.get("hosts") or (), accounts=Sessions(store), users=users)
    except (ValueError, OSError) as e:
        print(f"stewart hub: 듣는 주소 {listen} 에 묶지 못했다: {e}", file=sys.stderr)
        return 2
    web.serve()
    print(f"stewart hub(계정 모드) — 사람별 작업 폴더 {root}/<이름>", file=sys.stderr)
    print(f"  화면: http://{listen if ':' not in listen else '[' + listen + ']'}:{web.port}/  (사용자 {len(store.users())}명)", file=sys.stderr)
    print("  TLS 없음 — 같은 사무실 망 안에서만 쓴다. 끝내기: Ctrl-C", file=sys.stderr)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        web.close()
        users.close_all()
    return 0


def run_hub_user(a) -> int:
    """stewart hub-user add|passwd|disable|enable|list [이름] — 계정 모드 허브의 사용자 관리(관리 화면은 없다)."""
    import getpass
    import os
    import sys
    from .accounts import AccountError, Store
    cfg = hub_config(a.config)
    project = Path(a.project or os.getcwd()).resolve()
    st = Store(_state_dir(a, cfg, project))
    try:
        if a.action == "list":
            for n, u in sorted(st.users().items()):
                print(f"{n}\t{'막힘' if u.get('disabled') else '쓰는 중'}\t만듦 {u.get('created', '?')}")
            return 0
        if not a.name:
            raise AccountError("사용자 이름을 준다")
        if a.action in ("add", "passwd"):
            if a.password_stdin:
                pw = sys.stdin.readline().rstrip("\r\n")
            else:
                pw = getpass.getpass(f"{a.name} 의 새 비밀번호: ")
                if getpass.getpass("한 번 더: ") != pw:
                    raise AccountError("두 번 넣은 비밀번호가 다르다")
            (st.add if a.action == "add" else st.set_password)(a.name, pw)
        else:
            st.set_disabled(a.name, a.action == "disable")
    except AccountError as e:
        print(f"stewart hub-user: {e}", file=sys.stderr)
        return 2
    print(f"stewart hub-user: {a.action} {a.name} 끝 ({st.path})", file=sys.stderr)
    return 0


def run_hub(a) -> int:
    import os
    import sys
    from pathlib import Path
    from .cli import resolve
    from .record import Recorder
    from .web import LOOPBACK, SESSION_DAYS, WebApp, make_token
    cfg = hub_config(a.config)
    project = Path(a.project or os.getcwd()).resolve()
    state_dir = _state_dir(a, cfg, project)
    if getattr(a, "action", None) == "url":
        return hub_url(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    if cfg.get("accounts") is True:
        return run_hub_accounts(a, cfg, project, state_dir)
    if cfg.get("listen") and str(cfg["listen"]) not in LOOPBACK:   # 계정 없이 밖 주소는 열지 않는다
        print(f"stewart hub: [hub] listen = {cfg['listen']} 은 계정 모드([hub] accounts = true)에서만 쓴다. "
              "계정 없이는 127.0.0.1 에만 묶는다 — 켜지 않았다", file=sys.stderr)
        return 2
    from .notify import from_config
    ntfy = from_config(cfg, project.name)   # [hub] ntfy_url 이 있으면 승인 대기를 폰(ntfy)으로(notify.py)
    hub = Hub(state_dir, Path(a.runs_dir), notify_url=cfg.get("notify_url"), worktree_root=cfg.get("worktree_root"),
              notify=ntfy)
    if ntfy is not None:
        ntfy.hub = hub
        ntfy.on_error = lambda why: hub.event("ntfy_fail", detail=why)
    profile, backend = resolve(a.profile, a.backend, str(project))
    history, prev = last_main(hub) if not getattr(a, "new_main", False) else (None, None)
    rec = Recorder(Path(a.runs_dir), "hub-main")
    main = HubMain(hub, profile, backend, rec, mode=a.mode, worker_profile=a.worker_profile or a.profile,
                   worker_backend=a.backend, history=history, resumed_from=prev)
    resumed = hub.recover()
    token = make_token(state_dir, reuse=not getattr(a, "new_token", False))   # 다시 켜도 같은 토큰·로그인 유지(--new-token 이면 새로)
    web = WebApp(main, hub, token, port=a.port or int(cfg.get("port") or DEFAULT_PORT), extra_hosts=cfg.get("hosts") or (),
                 state_dir=state_dir)
    web.ui_default = cfg.get("ui") if cfg.get("ui") in ("new", "old") else None
    web.serve()
    main.start_loop()
    main.watch.start()
    print(f"stewart hub — 프로젝트 {project}", file=sys.stderr)
    print(f"  화면: http://127.0.0.1:{web.port}/  (로그인 토큰은 {state_dir / 'hub-token'})", file=sys.stderr)
    print(f"  로그인 토큰: {token}", file=sys.stderr)
    if resumed:
        print(f"  이어 간 세션: {', '.join(resumed)}", file=sys.stderr)
    print("  끝내기: Ctrl-C(작업자는 멈춤 상태로 남고, 다음에 허브를 띄우면 이어 간다)", file=sys.stderr)
    url = f"http://127.0.0.1:{web.port}/"
    (state_dir / "hub-url").write_text(url + "\n", encoding="utf-8")
    again = f"stewart hub url --project {project}" + (f" --state-dir {state_dir}" if a.state_dir else "")
    print(f"  한 번 로그인하면 브라우저가 {SESSION_DAYS}일 기억한다(허브를 다시 켜도). 다시 보기: {again}", file=sys.stderr)
    print(f"접속: {url}", file=sys.stderr, flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        web.close()
        for name in list(hub.procs):
            try:
                hub.pause(name, timeout_s=30, resume_on_start=True)
            except Exception:
                pass
        main.close()
    return 0
