"""허브 안의 메인 세션과 허브 고리.

- 메인 = ChatSession + 세션 도구 6개(hubtools). 사람은 웹 화면에서 메인에게만 말한다.
- 메인의 확인 질문도 작업자와 같은 대기열(session=main)에 들어간다. 답은 허브 프로세스 안 큐로만 받는다(파일을 읽지 않는다).
- 허브 고리(스레드): Hub.tick() 을 돌려 승인 대기 알림·보고 도착 알림을 만든다. 보고 도착 알림은 메인의 다음 단계 앞에 끼워 넣는다.
"""

from __future__ import annotations

import json
import os
import re
import queue
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from . import hubtools as HT
from . import tools as T
from .chat import ChatSession
from .hub import Hub, now
from .worker import HubUI

LOG_MAX = 500
# 허브 메인에만 붙는 역할 지시(U2-2 3a). 사람과의 대화를 가볍게 두고, 큰 일은 쪼개 맡긴다. STEWART_HUB_ROLE=0 이면 붙이지 않는다
HUB_MAIN_NOTE = """허브 메인으로서 일을 나눠 맡긴다:
- 서로 독립인 큰 일(바꿀 파일 묶음이 다른 둘 이상, 또는 오래 걸리는 구현·시험)은 session_spawn 으로 작업자를 띄워 동시에 돌린다. 하나씩 띄우고 기다리지 말고, 나눌 수 있으면 한 번에 여럿 띄운다. 작업자는 이 대화를 못 보니 task 에 목표·범위·바꿀 파일·끝의 기준·보고할 것을 다 적는다. 같은 파일을 두 작업자에게 주지 않는다.
- 넓게 읽어야 하는 조사(코드 찾기·구조 파악·여러 파일 대조·원인 찾기 — 파일 셋 넘게 읽을 것 같으면)는 delegate 로 맡기고 결과 글만 받는다. 이 대화에 긴 파일 내용을 쌓지 않는다.
- 맡기지 않는 것: 파일 한두 개 고침, 명령 한두 번으로 끝나는 확인, 이 대화의 앞 내용을 알아야 하는 일, 사람에게 바로 답할 수 있는 물음. 이런 것은 직접 한다.
- 작업자 보고가 오면 확인할 것만 확인하고 사람에게 짧게 알린다. 사람의 말에는 일을 하는 사이사이에도 먼저 짧게 답한다.
- 세 걸음 넘는 일은 시작할 때 todo_write 로 할 일 목록을 만든다(사람 화면에 목록이 뜬다). 하나를 시작할 때 in_progress, 끝내면 곧바로 done 으로 고친다. 작업자에게 맡긴 항목은 내용 앞에 작업자 이름을 적는다(예: "w-docs: README 고치기"). 계획을 글로만 쓰지 않는다.
- 일하며 말한다(사람이 대화하는 느낌을 받게, 걸음 단위로 — 도구마다 말하지는 않는다):
  ① 사람 말을 받으면 첫 도구 호출 전에 한두 문장으로 알아들은 것과 먼저 할 일("알겠습니다. 우선 …부터 보겠습니다").
  ② 걸음이 바뀔 때(조사 → 고침 → 확인), 뜻밖의 것을 찾았을 때, 방향을 바꿀 때 한 문장. 그 글은 도구 호출과 같은 답에 같이 쓴다.
  ③ 끝낼 때 무엇을 했고 무엇이 남았는지 짧게. 사람이 쓰는 말(한국어)로.
- 단계 세우고 묻기(판 67): 큰 일(파일 셋 넘게 고침·걸음 다섯 넘음·되돌리기 어려움)은 먼저 todo_write 로 단계 계획을 내고, 단계 묶음(한두 항목)을 끝낼 때마다 무엇을 했고 무엇이 나왔는지 짧게 요약한 뒤 "다음은 …입니다. 진행할까요?"로 차례를 마친다. 사람이 "끝까지"·"쭉 해"·"멈추지 말고"라고 하면 그 차례는 묻지 않고 끝까지 간다. 작은 일·물음·한두 걸음 일은 계획 없이 바로 한다."""
TICK_S = 0.5
RESULT_KEEP = 20   # 화면 상태에 결과 전문을 싣는 최근 도구 수(그 앞은 끝 15줄 화면 줄만 — SSE 크기)


IMG_NAME_RX = re.compile(r"^[0-9a-f]{16}\.(png|jpg|webp|gif)$")   # images.store 가 짓는 이름
IMG_KEEP = 300
THINK_KEEP = 4000   # 생각 블록에 남기는 끝 글자 수(판 56)


class HubMainUI(HubUI):
    """메인 세션 화면: 글은 웹 화면용 기록에 쌓고, 묻기는 대기열 + 메모리 안 큐."""

    def __init__(self, hub: Hub, name: str = "main"):
        super().__init__(hub.sdir(name), name, None)
        self.hub = hub
        self.run_rec = None                   # 판 127: 승인 기록을 그 대화의 기록 폴더에도(다시 열 때 다시 그린다)
        self._step_u = None                   # 판 127: 막 받은 걸음의 숫자(토큰·시간) — 다음 도구 줄 또는 답 줄에 붙인다
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
        self.on_backend_ok = None             # 판 127.3: 답을 받으면 모델 점을 바로 "준비됨"으로
        self.err_count: dict = {}
        self.turn_no = 0                      # 지금 차례 번호(답 줄의 👍👎 가 어느 차례인지, 판 89)
        self.imgs: dict = {}                  # 작업 카드 그림 이름 → 파일(판 91, 이 화면이 받은 것만 내준다)
        self.full_narration = True
        self.goal_notes = os.environ.get("STEWART_GOAL_NOTES", "1") != "0"   # 판 100 손잡이 — 새 사람 말이 앞 목표를 덮는다는 엔진 알림, 같은 결과 두 번째 알림(허브만)            # 도구와 함께 온 모델 글을 한 줄로 자르지 않는다(걸음 사이 말)             # 같은 오류(도구 + 오류 첫 줄) 몇 번째인지 — 화면 "같은 오류 N번째"(U2-8)

    def _add(self, kind: str, text: str) -> None:
        self.log.append({"ts": datetime.now().strftime("%H:%M:%S"), "kind": kind, "text": text})

    def say(self, s: str = "") -> None:
        if s.strip():
            self._add("info", s.strip())

    def assistant(self, s: str) -> None:
        self._add("assistant", s)
        if self.turn_no:
            self.log[-1]["turn"] = self.turn_no
        if self._step_u is not None and not self._step_u.get("calls"):   # 판 127: 답으로 끝난 걸음의 숫자
            self.log[-1]["usage"], self._step_u = self._step_u, None

    def asked_options(self, opts: list[str]) -> None:
        """판 106: ask_user 의 선택지를 바로 앞 질문 줄에 단다(화면 단추 — 누르면 그 글이 다음 사람 말로 간다)."""
        for e in reversed(self.log):
            if e.get("kind") == "assistant":
                e["options"] = list(opts)
                return

    def register_imgs(self, paths: list) -> list[str]:
        """그림 파일들을 이 화면이 내줄 수 있게 적고 이름 목록을 돌려준다(/api/img, 판 91·98)."""
        names = []
        for p in paths:
            name = Path(p).name
            if not IMG_NAME_RX.match(name):
                continue
            with self._lock:
                self.imgs[name] = str(p)
                while len(self.imgs) > IMG_KEEP:
                    self.imgs.pop(next(iter(self.imgs)))
            names.append(name)
        return names

    def tool_images(self, paths: list) -> None:
        """도구가 모델에게 보인 그림(view_image 등)을 바로 앞 작업 줄에 단다(판 91). 화면은 이름만 받고 /api/img 로 가져간다."""
        names = self.register_imgs(paths)
        if not names:
            return
        for e in reversed(self.log):
            if e.get("kind") == "tool":
                e["imgs"] = (e.get("imgs") or []) + names
                return

    def labeled(self, turn: int, kind: str, by: str) -> None:
        """학습 표시(판 89)를 화면 기록의 그 차례 답 줄에: good·bad·clear → label, corrected·uncorrect → corrected."""
        for e in self.log:
            if e.get("kind") != "assistant" or e.get("turn") != turn:
                continue
            if kind in ("good", "bad"):
                e["label"] = kind
            elif kind == "clear":
                e.pop("label", None)
            elif kind == "corrected":
                e["corrected"] = by
            elif kind == "uncorrect":
                e.pop("corrected", None)

    def tool(self, name: str, summary: str) -> None:
        self._add("tool", f"{name} {summary}")
        if self._step_u is not None:   # 판 127: 이 도구를 부른 걸음의 숫자(한 걸음에 도구가 여럿이면 첫 줄에만)
            self.log[-1]["usage"], self._step_u = self._step_u, None
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

    def memory_ask(self, item: dict) -> None:
        """오래 지킬 지시(판 83): 남기기 전에 묻는 카드 — [허용](방침으로)·[이번만]."""
        self._add("memory_ask", f"방침으로 기억할까요: {item['text']}")
        self.log[-1].update(mem_id=item["id"], mem_kind=item["kind"], mem_source=item.get("source"), mem_scope=item.get("scope", "user"))

    def memory_op(self, op: str, item: dict) -> None:
        if op == "ask":
            return self.memory_ask(item)
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
            if self.now and str(self.now.get("what", "")).startswith("문맥 압축 중"):
                self.now = getattr(self, "_now_before_compact", None)
            self._add("warn", f"문맥이 차서 앞 대화를 요약해 접기를 못 했다({info.get('error')}) — 다음 차례에 다시 해 본다. "
                              "급하면 작업 내역 [토큰] 탭의 [요약해 접기]")
            return
        k = lambda n: f"{round(n / 1000, 1)}k" if isinstance(n, (int, float)) and n >= 1000 else str(n)   # noqa: E731
        if self.now and str(self.now.get("what", "")).startswith("문맥 압축 중"):
            self.now = self._now_before_compact
        if kind == "clip":
            self._add("compact", f"압축함(오래된 도구 결과 {info.get('clipped', 0)}개 줄임): {k(info.get('before_tokens'))} → 약 {k(info.get('tokens_after'))} 토큰"
                                 " — 원문은 기록에, 필요하면 다시 읽는다")
        else:
            self._add("compact", f"압축함(앞 대화 메시지 {info.get('folded_messages', 0)}개 → 요약 한 쪽, "
                                 f"{'자동' if info.get('reason') == 'auto' else '사람이 누름'}): 약 {k(info.get('tokens_before'))} → {k(info.get('tokens_after'))} 토큰 — 원문은 기록에")

    def compacting(self) -> None:
        """요약 접기를 시작할 때(모델 한 번이라 수십 초): 화면 맨 아래 돌기 줄 '문맥 압축 중…'."""
        self._now_before_compact = self.now
        self.now = {"what": "문맥 압축 중… (앞 대화를 요약해 접는다)", "since": time.time()}

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
        self.log[-1]["paths"] = list(paths[:50])   # 판 104: 화면 "만든 파일" 카드의 [열기]·[받기]
        if self.turn_no:
            self.log[-1]["turn"] = self.turn_no     # 판 105: [이 차례 되돌리기]
        self.last_files = len(paths)

    def ask(self, prompt: str) -> str:
        self.now = {"what": "사람 승인 기다리는 중 — 승인 칸", "since": time.time(), "ask": True}
        t0, what = time.time(), getattr(self, "_tool_what", "")
        n = (sorted(int(p.stem) for p in self.sd.ask_dir.glob("[0-9][0-9][0-9][0-9].json")) or [0])[-1] + 1   # HubUI.ask 가 쓸 번호
        try:
            ans = super().ask(prompt)
        except EOFError:
            self.approval(what, None, None, time.time() - t0, prompt)
            raise
        finally:   # 답을 받으면 곧바로 되돌린다(전에는 승인 뒤 도구가 도는 내내 "승인 기다리는 중" 이 남았다 — tag 45 실사용)
            self.now = {"what": f"도구 실행 중: {getattr(self, '_tool_what', '')}"[:200], "since": time.time()}
        self.approval(what, ans, self.hub.answer_via.pop((self.name, n), None), time.time() - t0, prompt)
        return ans

    def approval(self, what: str, ans: str | None, via: str | None, secs: float, prompt: str = "") -> None:
        """판 127: 승인 하나를 화면 기록·그 대화 기록 폴더에 남긴다 — 무엇을(도구·인자 요약)·어떻게 답했나·얼마나 기다렸나."""
        a = (ans or "").strip().lower()
        word = ("취소됨(멈춤)" if ans is None else "늘 허용함" if via == "web-always" else "허용함" if a in ("y", "yes", "ㅇ", "네")
                else "거부함" if a in ("n", "no", "ㄴ", "아니", "") else "말을 전함")
        s = round(secs)
        self._add("approval", f"{word} · {s // 60}분 {s % 60}초" if s >= 60 else f"{word} · {s}초")
        self.log[-1].update(what=what[:200], answer=word, secs=round(secs, 1), via=via or "", turn=self.turn_no or None,
                            note=(ans or "")[:200] if word == "말을 전함" else "")
        if self.run_rec is not None:
            try:
                self.run_rec.event("approval", what=what[:300], answer=word, secs=round(secs, 1), via=via or "",
                                   prompt=str(prompt or "").strip()[:300])
            except Exception:   # noqa: BLE001 — 기록 실패가 승인을 막지 않게
                pass

    def backend_ok(self, name: str) -> None:
        """판 127.3: 모델 서버에서 답을 받았다(chat 이 부른다)."""
        if self.on_backend_ok is not None:
            try:
                self.on_backend_ok(name)
            except Exception:   # noqa: BLE001
                pass

    def step_usage(self, d: dict) -> None:
        """판 127: 막 받은 걸음의 숫자 — 도구를 부른 걸음이면 다음 도구 줄에, 아니면 다음 답 줄에 붙인다."""
        self._step_u = dict(d)

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


HUB_COMMANDS = (("clear", "지금 대화를 기록에 남기고 빈 문맥으로 새 대화"), ("reset", "/clear 와 같다"),
                ("compact", "오래된 도구 결과 줄이기('/compact 요약' 은 앞 대화를 요약해 접기)"),
                ("memory", "기억 정리 제안 — 모델이 방침·프로필·자료를 읽고 고칠 것을 카드로(항목별 받기·두기)"),
                ("improve", "지난 대화로 배워 두기 — 👍·교정된 차례에서 예시·방침 제안을 카드로"), ("help", "이 목록"))


def _profile_workdir(name) -> str | None:
    """판 118: --project 가 없으면 프로필에 적힌 작업 폴더(예: 대화 자리의 대화 폴더). 프로필에 없으면 None(지금 폴더)."""
    if not name:
        return None
    from .config import ConfigError, load_profile
    try:
        p = load_profile(name)
    except (ConfigError, OSError):
        return None
    return str(p.workdir) if "workdir" in (p.raw or {}) else None


def main_tier(a, workdir: str):
    """판 113: --tier 가 있으면 그 등급(backends 표 [tiers.<이름>])의 백엔드·프로필·문맥 예산으로 메인을 켠다.
    등급에 slim = true 면 얇은 메인(slim.py). -b 는 작업자 기본 백엔드로 남는다. 없으면 -p·-b 그대로."""
    from .cli import resolve
    name = getattr(a, "tier", None)
    if not name:
        return resolve(a.profile, a.backend, workdir)
    from . import spawn as SP
    from .config import ConfigError
    tiers = SP.load_tiers(main=True)
    t = tiers.get(name)
    if t is None:   # 판 116: 없는 등급은 기본 모델로 대신하지 않는다(바깥 유료 등급이 몰래 물려받기로 떨어지지 않게)
        raise ConfigError(f"등급이 없다: {name}. 등급은 backends 표 [tiers.<이름>] 에 적어야 켜진다(있는 등급: {', '.join(tiers) or '없음'}). "
                          "바깥 유료 모델 등급은 기본 모델로 대신하지 않는다 — backends 표에 적는다(stewart tiers 로 확인)")
    import dataclasses
    profile, backend = resolve(t.get("profile") or a.profile, t.get("backend") or a.backend, workdir)
    if t.get("budget"):
        backend = dataclasses.replace(backend, context_budget=max(4096, min(int(t["budget"]), backend.max_context)))
    profile = dataclasses.replace(profile, raw={**(profile.raw or {}), "tier": SP.tier_raw(t, backend)})   # 판 116: 생각 칸 등(판 132: 기본 생각 깊이)
    if t.get("backend"):
        why = _reach(backend)
        if why:
            import sys
            print(f"stewart: 등급 {name} 의 백엔드 {backend.name} 에 닿지 못했다({why}) — 켜도 첫 답에서 오류가 난다. "
                  "기본 모델로 대신 돌리지 않는다. 서버를 켜거나, 등급의 backend 줄을 지우면 기본 모델로 돈다", file=sys.stderr)
    if t.get("slim"):
        from . import slim as SL
        profile = SL.apply(profile, t)
    return profile, backend


def _reach(backend) -> str:
    """판 116: 등급 백엔드에 연결만 해 본다(3초, 인증 오류는 닿은 것). 닿지 못하면 까닭, 닿으면 빈 글."""
    import urllib.error
    import urllib.request
    try:
        urllib.request.urlopen(backend.base_url.rstrip("/") + "/models", timeout=3).close()
    except urllib.error.HTTPError:
        return ""
    except (OSError, ValueError) as e:
        return type(e).__name__
    return ""


def _approvals(run_dir) -> list[dict]:
    """판 127: 이어 받은 기록(과 그 앞 기록들)의 승인 사건 — 오래된 것부터."""
    return _chain_events(run_dir, "approval")


def _step_calls(run_dir) -> list[dict]:
    """판 127.2: 이어 받은 기록(과 그 앞 기록들)의 도구 호출 결과(이름·성공·걸린 시간·결과 앞부분) — 오래된 것부터, 메시지의 도구 호출 차례와 같다."""
    out = []
    for e in _chain_events(run_dir, "step"):
        for tc in ((e.get("response") or {}).get("tool_calls") or []):
            if isinstance(tc, dict):
                out.append({"name": tc.get("name"), "ok": tc.get("ok"), "elapsed_s": tc.get("elapsed_s"), "result": str(tc.get("result") or "")[:8000]})
    return out


def _chain_events(run_dir, kind: str) -> list[dict]:
    out, seen, cur = [], set(), str(run_dir or "")
    while cur and cur not in seen and len(seen) < 20:
        seen.add(cur)
        evs, nxt = [], ""
        try:
            with open(Path(cur) / "events.jsonl", encoding="utf-8") as f:
                for i, line in enumerate(f):
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if i == 0:
                        nxt = str((e.get("meta") or {}).get("resumed_from") or "")
                    if e.get("kind") == kind:
                        evs.append(e)
        except OSError:
            break
        out = evs + out
        cur = nxt
    return out


def _replay(ui, messages: list | None, run_dir=None) -> None:
    """판 124.2: 이어 받은 대화(다시 열기·갈래·허브 다시 켜기)의 앞 내용을 화면 기록에 다시 그린다 — 사람 말·도구 한 줄·답.
    모델 문맥은 그대로(sess.messages), 화면 줄만 만든다. 도구 결과 본문은 다시 그리지 않는다(기록 폴더에 있다).
    판 127: 그 기록의 승인 사건을 해당 도구 줄 뒤에 다시 붙인다(허용함 · 12초)."""
    from .chat import _arg_summary
    from .images import text_of
    aps = _approvals(run_dir) if run_dir else []
    calls = _step_calls(run_dir) if run_dir else []   # 판 127.2: 다시 그린 도구 줄에도 성공 표시·결과·걸린 시간
    for m in messages or []:
        role = m.get("role")
        if role == "user":
            t = _strip_env(text_of(m.get("content"))).strip()
            if isinstance(m.get("content"), list) and text_of(m.get("content")).lstrip().startswith("[엔진]"):   # 판 127.2: 도구가 본 그림(view_image 등)을 그 도구 줄에 다시
                from .images import path_of
                paths = [str(x) for x in (path_of(q) for q in m["content"] if isinstance(q, dict) and q.get("type") == "image_url") if x and Path(x).is_file()]
                if paths and hasattr(ui, "tool_images"):
                    ui.tool_images(paths)
                continue
            if not t or t.startswith("[엔진]"):
                continue
            ui._add("hub" if t.startswith("[허브]") else "user", t)
        elif role == "assistant":
            t = str(m.get("content") or "").strip() if isinstance(m.get("content"), (str, type(None))) else text_of(m.get("content")).strip()
            if t:   # 실제 차례처럼 답 글이 먼저, 그 뒤 도구 줄
                ui._add("assistant", t)
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                tl = f"{fn.get('name') or '?'} {_arg_summary(fn.get('name') or '', args if isinstance(args, dict) else {})}"
                ui._add("tool", tl)
                if calls and calls[0].get("name") == fn.get("name"):
                    c = calls.pop(0)
                    ui.log[-1].update(ok=bool(c.get("ok", True)), elapsed=round(float(c.get("elapsed_s") or 0), 2), result=c.get("result") or "")
                if aps and str(aps[0].get("what") or "")[:80] == tl[:80]:
                    a = aps.pop(0)
                    sec = int(round(float(a.get("secs") or 0)))
                    ui._add("approval", f"{a.get('answer')} · {sec // 60}분 {sec % 60}초" if sec >= 60 else f"{a.get('answer')} · {sec}초")
                    ui.log[-1].update(what=tl[:200], answer=a.get("answer"), secs=a.get("secs"), via=a.get("via") or "", replayed=True)


def _conv_brief(sess) -> dict:
    """판 124.2: 목록 줄에 쓸 첫 사람 말(한 줄)과 마지막으로 손댄 때(기록 파일 시각, 없으면 지금)."""
    from .images import text_of
    first = ""
    for m in getattr(sess, "messages", None) or []:
        if m.get("role") == "user":
            t = _strip_env(text_of(m.get("content")))
            if t and not t.startswith("[엔진]"):
                first = " ".join(t.split())[:120]
                break
    try:
        last = (Path(sess.rec.dir) / "messages.json").stat().st_mtime
    except (OSError, AttributeError):
        last = time.time()
    return {"first": first, "last": last}


def _run_kind(run_dir: Path, default: str) -> str:
    """지난 기록의 종류: 그 기록을 연 프로필의 자리(대화 프로필이면 talk)."""
    from . import surfaces as SF
    try:
        with open(Path(run_dir) / "events.jsonl", encoding="utf-8") as f:
            meta = json.loads(f.readline()).get("meta") or {}
    except (OSError, ValueError):
        return default
    return "talk" if meta.get("profile") == SF.TALK_PROFILE else "code" if meta.get("profile") else default


def with_role(profile):
    """허브 역할 지시(일 나눠 맡기기) + 현황판 지시를 붙인 프로필. 얇은 꼴(판 113)·hub_role = false(판 118)·STEWART_HUB_ROLE=0 이면 그대로."""
    import dataclasses
    import os
    from . import slim as SL
    if (SL.conf(profile) is None and os.environ.get("STEWART_HUB_ROLE") != "0"
            and (profile.raw or {}).get("hub_role", True) is not False and HUB_MAIN_NOTE not in (profile.system or "")):
        from .board import NOTE as BOARD_NOTE
        profile = dataclasses.replace(profile, system=(profile.system or "").rstrip() + "\n\n" + HUB_MAIN_NOTE + "\n" + BOARD_NOTE)
    return profile


def kind_of(profile) -> str:
    """판 120: 대화 칸의 종류 — 프로필 surface 가 talk 면 talk(대화), 아니면 code(코드)."""
    return "talk" if str((getattr(profile, "raw", None) or {}).get("surface") or "") == "talk" else "code"


def kind_resolver(a, project: Path, backend_name):
    """판 120: run_hub 이 쓰는 종류 → (프로필, 백엔드). 대화는 대화 프로필·대화 폴더, 코드는 -p(대화 프로필이면 chat)·허브 프로젝트."""
    from . import surfaces as SF
    from .cli import resolve
    from .config import load_profile

    def r(kind: str):
        if kind == "talk":
            wd = Path(load_profile(SF.TALK_PROFILE).workdir)
            wd.mkdir(parents=True, exist_ok=True)
            return resolve(SF.TALK_PROFILE, backend_name, str(wd))
        name = a.profile if a.profile and a.profile != SF.TALK_PROFILE else "chat"
        p, b = resolve(name, backend_name, str(project))
        return (p, b) if kind_of(p) == "code" else resolve("chat", backend_name, str(project))
    return r


class HubMain:
    def __init__(self, hub: Hub, profile, backend, rec, mode: str = "edits", worker_profile: str = "chat",
                 worker_backend: str | None = None, history: list | None = None, resumed_from: str | None = None):
        self.hub = hub
        import dataclasses
        import os
        profile = with_role(profile)
        self.kind = kind_of(profile)   # 판 120: 메인 칸의 종류(code·talk) — 새 대화의 기본
        self.kind_resolver = None      # run_hub 이 넣는다: 종류 → (프로필, 백엔드). 없으면 메인과 같은 종류만
        hub.register_main(profile.workdir, mode)
        self.name = "main"
        self.chats: dict = {}                 # 사람이 연 대화(메인과 같은 채팅): 이름 → HubChat
        self._chat_n = 0
        for n, e in list(hub.registry()["sessions"].items()):   # 앞 허브의 대화는 이어 붙지 않는다(기록은 지난 세션에서 다시 연다)
            if e.get("role") == "chat":
                hub.drop_chat(n)
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
        self._worker_profile, self._worker_backend = worker_profile, worker_backend
        self.sess = ChatSession(profile, backend, rec, ui=self.ui, stream=True, mode=mode, default_mode="edits",
                                extra_tools=(HT.DEFINITIONS, self.tools.execute), history=history, resumed_from=resumed_from)
        if history:
            _replay(self.ui, history, resumed_from)
            self.ui._add("meta", f"앞 메인 대화를 이어 받았다({resumed_from}). 새로 시작하려면 허브를 --new-main 으로 띄운다.")
        self.ui.env = self.sess.env
        self.ui.run_rec = self.sess.rec   # 판 127: 승인 기록을 이 대화 기록 폴더에도
        self.sess.env.on_quota = self._quota_alert   # brave 이달 80%·한도 도달 → 폰·화면(판 61)
        from .modelstat import ModelWatch
        self._idle_since = time.monotonic()  # 꿈꾸기 자동의 한가함 잣대(판 54)
        self.watch = ModelWatch(backend)      # 모델 서버 점(허브가 start — 시험에서는 띄우지 않는다)
        mig = getattr(getattr(self.sess, "mem", None), "migrated", None)
        if mig:   # 판 83: 옛 기억을 세 갈래로 옮겼으면 한 번 알린다(옛 파일·옮긴 표는 남김)
            c = mig["counts"]
            self.ui._add("info", f"기억을 세 갈래로 옮겼다 — 방침 {c['policy']} · 프로필 {c['profile']} · 자료 {c['knowledge']}. "
                                 f"옮긴 표: {mig.get('table_path')} · 옛 파일은 그대로 · 되돌리기: stewart memory migrate --revert"
                                 " · 모델로 한 번 정리하려면 /memory")
            hub.event("memory_migrated", **c)
        self.ui.on_backend_error = lambda kind, text: self.watch.poke(kind if kind in ("down", "loading", "auth") else None, text)
        self.ui.on_backend_ok = self.watch.reply_ok
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
        parts, img_note = [], ""
        if images:
            from . import images as IMG
            if not self.sess.backend.vision:
                return "no_vision"
            if self.busy:
                return "busy_images"
            from . import ocr as OCR
            ocr_on = OCR.enabled(getattr(self._root(), "image_ocr", False))
            notes = []
            for i, item in enumerate(images, 1):
                name, data = item[0], item[1]
                orig = item[2] if len(item) > 2 else None
                part, desc = IMG.from_bytes(data, name, self.sess.rec.dir)   # ImageError 는 화면으로(까닭)
                parts.append(part)
                kept = IMG.keep_original(part, orig) if orig else None   # 줄여 받은 사진의 원본(부분 확대용, 판 82)
                self.ui._add("info", f"이미지 붙임: {desc}" + (" · 원본도 둠(부분 확대용)" if kept else ""))
                notes.append(f"{i}) {IMG.path_of(part)}")
                if ocr_on:
                    notes[-1] += OCR.note(i, OCR.text_of(orig or data))
            img_note = ("[붙인 그림 — 작은 글자·세부는 view_image(path, region=[x0,y0,x1,y1] 0~1 비율)로 잘라 크게 본다: "
                        + " ".join(n for n in notes) + "]")
        text = text or "(붙인 이미지를 봐라)"
        self.ui._add("user", text + (f"  [이미지 {len(parts)}장]" if parts else ""))
        if not self.busy:
            self.ui.log[-1]["turn"] = self.sess.st["turns"] + 1   # 판 105: 이 말이 여는 차례(되감기 단추)
        if parts:   # 보낸 말 줄 아래 작은 그림(판 98) — 전에는 "[이미지 n장]" 글만 남아 화면에 안 보였다
            from . import images as IMG
            names = self.ui.register_imgs([str(x) for x in (IMG.path_of(q) for q in parts) if x])
            if names:
                self.ui.log[-1]["imgs"] = names
                self.ui.log[-1]["text"] = text
        if not parts:
            self.ui.last_user = text
        if not parts and re.match(r"^\s*(기억\s*(을|를)?\s*(좀\s*)?정리|memory\s+consolidate)", text, re.I) and len(text) <= 40:
            self.ui._add("user", text)
            return self.memory_consolidate()   # "기억 정리해 줘" 도 /memory 와 같이(판 88)
        if not parts and re.match(r"^\s*지난\s*(대화|일)\s*(로|에서|으로)\s*배워", text) and len(text) <= 40:
            self.ui._add("user", text)
            return self.improve()
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
            elif text.split()[0] in ("/clear", "/reset"):   # 새 대화(Claude Code 와 같은 이름) — 지금 대화는 기록에 남는다
                try:
                    self.new_main()
                except RuntimeError as e:
                    self.ui._add("warn", f"{text.split()[0]}: {e}")
                    return "busy"
                return "cleared"
            elif text.split()[0] == "/memory":   # 기억 정리(판 88, SW-40)
                return self.memory_consolidate()
            elif text.split()[0] == "/improve":   # 지난 대화로 배워 두기(판 93)
                return self.improve(confirm=text.split()[1:2] == ["yes"])
            elif text.split()[0] == "/help":
                self.ui._add("info", "허브 메인 칸 명령: " + " · ".join(f"/{n} — {d}" for n, d in HUB_COMMANDS)
                             + " · 그 밖 /이름 은 프로젝트·사용자 명령(.stewart/commands)")
                return "help"
            elif text.split()[0] in COMMANDS:
                self.ui._add("warn", f"{text.split()[0]} 는 stewart chat 의 내장 명령이다. 허브 메인 칸에서는 쓰지 않는다.")
                return "builtin_ignored"
        self._auto_wakes = 0   # 사람이 말했으면 자동 이어 가기 셈을 새로
        if not self._busy.acquire(blocking=False):
            with self.ui._lock:
                self.ui.pending.append(text)
            return "queued"
        if img_note:   # 모델에게만: 붙인 그림의 경로(부분 확대용)·OCR 글 — 화면 말 줄은 그대로
            text = text + "\n\n" + img_note
        self._turn = threading.Thread(target=self._run_turn, args=(text, parts or None), daemon=True)
        self._turn.start()
        return "started"

    def _run_turn(self, text: str, images: list | None = None) -> None:
        from .backend import set_abort_scope
        set_abort_scope(self.name)   # [멈춤]은 이 대화의 응답만 끊는다(다른 대화는 그대로)
        try:
            self.ui.sd.set_status(status="working")
            st = self.sess.st
            self._turn0 = (st.get("prompt_tokens_total") or 0, st.get("completion_tokens") or 0, st.get("steps") or 0,
                           round(st.get("model_time_s") or 0, 1))
            n0, f0 = len(self.sess.messages), self.sess.st.get("summaries", 0)
            t_turn = time.monotonic()
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
            try:
                self._notify_turn_end(reason, time.monotonic() - t_turn, stopped=self.ui.halt_req.is_set())
            except Exception:
                pass
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
    def _notify_turn_end(self, reason: str, secs: float, stopped: bool = False) -> str | None:
        """차례가 끝나면 폰(ntfy): 끝·질문·막힘. 짧은 차례(NOTIFY_DONE_MIN_S, 기본 60초)·사람이 멈춘 것은 알리지 않는다.
        23:00~07:30 은 알리지 않는다(현황판 밤 조용과 같은 규칙, STEWART_BOARD_QUIET=0 이면 늘). 내용(답 글)은 싣지 않는다."""
        import os
        from datetime import datetime
        from .board import _quiet
        lim = float(os.environ.get("STEWART_NOTIFY_DONE_MIN_S") or 60)
        if stopped or not self.hub._notify_on or lim <= 0 or secs < lim:
            return None
        if os.environ.get("STEWART_BOARD_QUIET") != "0" and _quiet(datetime.now()):
            return None
        last = next((m.get("text") or "" for m in reversed(self.ui.log) if m.get("kind") == "assistant"), "")
        kind = ("막힘" if reason not in ("answered", "asked") else
                "질문" if reason == "asked" or last.rstrip().endswith(("?", "？", "까요", "까요.", "니까")) else "끝")
        mins = f"{int(secs // 60)}분 {int(secs % 60)}초" if secs >= 60 else f"{int(secs)}초"
        text = {"끝": f"stewart: 차례 끝({mins})", "질문": f"stewart: 묻는 것이 있다({mins}) — 화면에서 답", 
                "막힘": f"stewart: 막혀서 멈춤({mins}, {reason}) — 화면에서 확인"}[kind]
        nf = getattr(self.ui, "last_files", 0)
        if nf:
            text += f" · 파일 {nf}개"   # 판 104: 화면 "만든 파일" 카드에서 열기·받기
        self.ui.last_files = 0
        lab = getattr(self._root(), "label", "")
        self.hub._notify((f"[{lab}] " if lab else "") + text)
        return kind

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
        """이 허브의 지난 대화(열린 칸 빼고, 마지막으로 손댄 것부터 — trash.past_mains)."""
        from .trash import past_mains
        return past_mains(self._runs_dir, Path(self._profile.workdir), str(self.sess.rec.dir), limit=limit, skip=self.open_runs())

    def open_runs(self) -> set[str]:
        """지금 열린 대화 칸(메인·사람이 연 대화)의 기록 폴더."""
        root = self._root()
        return {str(root.sess.rec.dir)} | {str(c.sess.rec.dir) for c in root.chats.values()}

    def manager(self):
        """지난 세션 정리(지우기·되돌리기·이름·보관, trash.py)."""
        from .trash import Manager
        return Manager(self.hub, self._runs_dir, Path(self._profile.workdir), current_main=str(self.sess.rec.dir), open_runs=self.open_runs())

    def _swap_session(self, history: list | None, resumed_from: str | None) -> str:
        """메인 대화를 새 ChatSession 으로 바꾼다(지금 대화는 기록에 그대로). 부르는 쪽이 _busy 를 잡고 있어야 한다."""
        from .record import Recorder
        old = self.sess
        rec = Recorder(self._runs_dir, "hub-main")
        self.sess = ChatSession(self._profile, self._backend, rec, ui=self.ui, stream=True, mode=self._mode,
                                default_mode="edits", extra_tools=(HT.DEFINITIONS, self.tools.execute),
                                history=history, resumed_from=resumed_from)
        self.ui.env = self.sess.env
        self.ui.run_rec = self.sess.rec   # 판 127: 승인 기록을 이 대화 기록 폴더에도
        self.sess.env.on_quota = self._quota_alert
        self._resumed, self._turn0 = bool(history), None
        self.ui.sd.set_status(run_dir=str(rec.dir))
        try:
            old.close()
        except Exception:
            pass
        return str(rec.dir)

    def reopen_main(self, run_dir: str) -> str:
        """지난 메인 대화 하나로 메인을 바꿔 연다(지금 메인 대화는 기록에 그대로 남는다). 일하는 중이면 거절."""
        from .resume import load_messages
        if run_dir not in {x["run_dir"] for x in self.past_mains(100000)}:
            raise ValueError("지난 메인 대화 목록에 없는 기록")
        if not self._busy.acquire(blocking=False):
            raise RuntimeError("메인이 일하는 중이다. 멈추거나 끝난 뒤 다시 연다")
        try:
            hist = load_messages(Path(run_dir))
            new = self._swap_session(hist, run_dir)
            with self.ui._lock:
                self.ui.log.clear()
            _replay(self.ui, hist, run_dir)
            self.ui._add("info", f"지난 메인 대화를 다시 열었다({run_dir}).")
            return new
        finally:
            self._busy.release()

    def new_main(self) -> str:
        """/clear·/reset·화면 [새 대화]: 지금 대화는 기록에 남기고(지난 세션 목록에서 다시 열 수 있다) 빈 문맥으로 새로 시작.
        기억·현황판·허용 규칙은 그대로, 압축 상태·문맥 % 는 새 대화 것(0). 일하는 중이면 거절."""
        if not self._busy.acquire(blocking=False):
            raise RuntimeError("메인이 일하는 중이다. 멈추거나 끝난 뒤 새로 시작한다")
        try:
            old = str(self.sess.rec.dir)
            new = self._swap_session(None, None)
            with self.ui._lock:
                self.ui.log.clear()
                self.ui.pending = []
            self.ui.err_count = {}
            self.ui.last_user = ""
            self.ui._add("info", f"새 대화를 시작했다. 앞 대화는 기록에 남았다 — 지난 세션 목록에서 다시 열 수 있다({old}).")
            self.hub.event("main_new", previous=old, run_dir=new)
            return new
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
            for d in MEM.apply_ops(mem, MEM.extract_ops(msg.get("content") or ""), said=said):
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

    # ---- 기억 정리(판 88, SW-40): 모델이 세 갈래를 읽고 제안 → 카드에서 항목별 받기 ----
    def memory_consolidate(self) -> str:
        mem = getattr(self.sess, "mem", None)
        if mem is None or not mem.md():
            self.ui._add("warn", "기억 정리: 기억이 꺼져 있거나 아직 세 갈래로 옮기지 않았다")
            return "memory_off"
        if self.busy:
            self.ui._add("warn", "기억 정리: 차례가 끝난 뒤에 부른다")
            return "busy"
        self.ui._add("info", "기억 정리 제안을 만드는 중(모델 한 번)…")

        def run():
            from . import memconsolidate as MC
            from .backend import BackendError
            try:
                plan = MC.propose(mem, self.sess.backend)
            except (BackendError, OSError, ValueError) as e:
                self.ui._add("warn", f"기억 정리: 모델을 부르지 못했다({str(e)[:160]})")
                return
            self._mem_plan = plan
            if not plan:
                self.ui._add("info", "기억 정리: 고칠 것이 없다고 한다(바뀐 것 없음)")
                return
            self.ui._add("memory_plan", f"기억 정리 제안 {len(plan)}개 — 항목마다 받기·두기, 받은 것만 바뀐다(적용 전 사본)")
            self.ui.log[-1].update(plan=[{**p, "desc": MC.describe(p)} for p in plan])
            self.hub.event("memory_plan", n=len(plan))
        threading.Thread(target=run, daemon=True).start()
        return "memory_plan"

    # ---- 지난 대화로 배워 두기(판 93, SW-41 ④ 판 B): 표시된 차례 → 예시·방침 제안 카드 ----
    def improve(self, confirm: bool = False) -> str:
        mem = getattr(self.sess, "mem", None)
        if mem is None:
            self.ui._add("warn", "배워 두기: 기억이 꺼져 있다(STEWART_MEMORY)")
            return "memory_off"
        if self.busy:
            self.ui._add("warn", "배워 두기: 차례가 끝난 뒤에 부른다")
            return "busy"
        from . import improve as IM
        picked = IM.collect(self._runs_dir, mem.store.dir)
        if not picked:
            self.ui._add("info", "배워 두기: 새로 볼 차례가 없다 — 답 줄의 👍 나 교정됨 표시가 붙은 차례만 본다")
            return "nothing"
        b = self.sess.backend
        if b.external() and not confirm:
            self.ui._add("warn", f"배워 두기: 표시된 차례 {len(picked)}개의 글(비밀·홈 경로는 가림)이 {b.provider_label()} 로 나간다. "
                                 "그래도 하려면 /improve yes")
            return "confirm"
        self.ui._add("info", f"배워 두기: 차례 {len(picked)}개로 제안을 만드는 중(모델 한 번)…")

        def run():
            from .backend import BackendError
            try:
                plan, got = IM.propose(mem, b, self._runs_dir, picked=picked)
            except (BackendError, OSError, ValueError) as e:
                self.ui._add("warn", f"배워 두기: 모델을 부르지 못했다({str(e)[:160]})")
                return
            self._imp_plan = (plan, got)
            if not plan:
                IM.mark_seen(mem.store.dir, [x["ref"] for x in got])
                self.ui._add("info", "배워 두기: 남길 만한 것이 없다고 한다(본 차례는 다음에 다시 보지 않는다)")
                return
            self.ui._add("memory_plan", f"배워 두기 제안 {len(plan)}개 — 예시는 비슷한 일에 하나씩 붙고, 방침은 늘 지킨다. 받은 것만 쓴다(적용 전 사본)")
            self.ui.log[-1].update(plan=[{**p, "desc": IM.describe(p)} for p in plan], api="/api/improve/apply",
                                   revert="stewart improve --revert")
            self.hub.event("improve_plan", n=len(plan))
        threading.Thread(target=run, daemon=True).start()
        return "memory_plan"

    def improve_apply(self, accept: list[int] | None) -> dict:
        from . import improve as IM
        plan, got = getattr(self, "_imp_plan", None) or ([], [])
        mem = getattr(self.sess, "mem", None)
        if not plan or mem is None:
            return {"applied": [], "error": "배워 두기 제안이 없다(/improve 로 다시)"}
        r = IM.apply(mem, plan, accept, got)
        self._imp_plan = None
        if r["applied"]:
            self.ui._add("info", f"배워 두기: {len(r['applied'])}개 받음 — 되돌리기: stewart improve --revert {Path(r['backup']).name}"
                                 + "".join(f" · 스킬 초안 {Path(x).name}(켜려면 사용자 스킬 폴더로 옮김)" for x in r.get("drafts") or []))
        else:
            self.ui._add("info", "배워 두기: 받은 것이 없어 바꾸지 않았다")
        return r

    def memory_apply(self, accept: list[int] | None) -> dict:
        from . import memconsolidate as MC
        plan = getattr(self, "_mem_plan", None) or []
        mem = getattr(self.sess, "mem", None)
        if not plan or mem is None:
            return {"applied": [], "error": "정리 제안이 없다(/memory 로 다시)"}
        r = MC.apply(mem, plan, accept)
        self._mem_plan = None
        if r["applied"]:
            self.ui._add("info", f"기억 정리: {len(r['applied'])}개 받음 — 적용 전 사본 {Path(r['backup']).name}/ · "
                                 f"되돌리기: stewart memory consolidate --revert {Path(r['backup']).name}")
        else:
            self.ui._add("info", "기억 정리: 받은 것이 없어 바꾸지 않았다")
        self.hub.event("memory_apply", applied=len(r["applied"]))
        return r

    # ---- 학습 표시(판 89, SW-39): 답 줄 👍👎·교정됨 떼기 → 기록 사건 train_label ----
    def train_label(self, turn: int, label: str) -> dict:
        if label not in ("good", "bad", "clear", "uncorrect"):
            return {"ok": False, "error": "label"}
        ok = self.sess.label(int(turn), label, by="human")
        return {"ok": ok, **({} if ok else {"error": "turn"})}

    def _stop_running_turn(self, wait_s: float = 10.0) -> None:
        """닫기 전에 도는 차례를 멈추고 기다린다(판 87) — 셸을 닫은 뒤 그 차례가 run_shell 로 새 셸을 띄우지 않게."""
        t = getattr(self, "_turn", None)
        if t is None or not t.is_alive():
            return
        try:
            self.stop_turn()
        except Exception:
            pass
        t.join(timeout=wait_s)

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
            abort_streams(self.name)
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

    # ── 대화 여럿(SW-29): 메인과 같은 채팅을 사람이 더 연다 ──
    def _root(self) -> "HubMain":
        return getattr(self, "main", None) or self

    def _chat_list(self) -> list[dict]:
        root = self._root()
        tier = (((root._profile.raw or {}).get("tier") or {}).get("name")) or ""
        out = [{"name": "main", "title": "메인", "busy": root.busy, "run_dir": str(root.sess.rec.dir), "kind": root.kind, "tier": tier,
                **_conv_brief(root.sess)}]
        for c in root.chats.values():
            out.append({"name": c.name, "title": c.title, "origin": c.origin, "busy": c.busy, "run_dir": str(c.sess.rec.dir),
                        "kind": getattr(c, "kind", root.kind),
                        "tier": getattr(c, "tier", "") or (tier if getattr(c, "kind", root.kind) == root.kind else ""),
                        **_conv_brief(c.sess)})
        return out

    def conv(self, name: str | None) -> "HubMain":
        """대화 하나: 'main'(또는 없음)이면 메인, 아니면 사람이 연 대화. 없으면 KeyError."""
        root = self._root()
        if not name or name == "main":
            return root
        return root.chats[name]

    def search_dirs(self) -> list:
        """판 120: 지난 대화 찾기가 볼 폴더 — 메인 칸 폴더와 다른 종류 칸의 폴더(종류 꼬리표)."""
        root = self._root()
        out = [(Path(root._profile.workdir), root.kind)]
        if root.kind_resolver is not None:
            try:
                k2 = "code" if root.kind == "talk" else "talk"
                out.append((Path(root.kind_profile(k2)[0].workdir), k2))
            except Exception:   # noqa: BLE001 — 풀이 실패는 찾기를 막지 않는다
                pass
        return out

    def tier_rows(self, probe: bool = False) -> dict:
        """판 125: 등급 표(tierview) — 새 대화 고르기·설정 시트. 따로 도는 허브는 rubato 만 연결된 꼴. 상태 찌르기는 60초 묶음."""
        from . import tierview as TV
        root = self._root()
        inst = bool(os.environ.get("STEWART_INSTANCE"))
        # 판 136: 표에 없는 등급의 "기본 모델" = 이 허브가 켜진 모델(-b). 백엔드 표의 default 가 아니다(화면 글 "허브의 기본 모델" 그대로)
        rows, bks = TV.load(instance_backend=root._backend.name if inst else None,
                            default_backend=None if inst else root._backend.name)
        if probe:
            cache = root.__dict__.setdefault("_tier_probe", {"t": 0.0, "st": {}})
            if time.monotonic() - cache["t"] > 60:
                TV.probe(rows, bks, timeout=2.0)
                cache.update(t=time.monotonic(), st={r["tier"]: r["status"] for r in rows})
            else:
                for r in rows:
                    if r.get("status") == "unchecked" and r["tier"] in cache["st"]:
                        r["status"] = cache["st"][r["tier"]]
        return {"instance": inst, "rows": rows}

    def tier_profile(self, tier: str, kind: str):
        """판 125: 사람이 고른 등급의 (프로필, 백엔드) — 그 자리(kind)의 프로필에 등급의 백엔드·문맥 예산·생각 칸.
        연결 없음·키 없음이면 거절한다(다른 모델로 대신 돌리지 않는다)."""
        import dataclasses
        from . import spawn as SP
        from .cli import resolve
        rows = {r["tier"]: r for r in self.tier_rows()["rows"]}
        r = rows.get(tier)
        if r is None:
            raise ValueError(f"모르는 등급: {tier} (있는 등급: {', '.join(rows)})")
        if not r.get("connected"):
            raise ValueError(f"등급 {tier} 는 이 허브에 연결이 없다")
        if r.get("key") == "없음":
            raise ValueError(f"등급 {tier} 의 키가 없다 — `stewart tiers` 의 안내대로 넣는다")
        prof, _ = self.kind_profile(kind)
        _, back = resolve(str(prof.path), r["backend"], str(prof.workdir), check_workdir=False)
        t = {**(SP.load_tiers().get(tier) or {}), "name": tier}
        if t.get("budget"):
            back = dataclasses.replace(back, context_budget=max(4096, min(int(t["budget"]), back.max_context)))
        prof = dataclasses.replace(prof, raw={**(prof.raw or {}), "tier": SP.tier_raw(t, back)})   # 판 132: 기본 생각 깊이
        if r.get("manual") and r.get("external"):
            back = self.guard_outside(back, tier, t)
        return prof, back

    def guard_outside(self, back, tier: str, t: dict | None):
        """판 125: 바깥 사람만 등급 — 키 파일은 읽지 않고(키체인·환경 변수만), 돈 상한(하루·대화)을 붙인다."""
        import dataclasses
        from . import spend as SPD
        if back.api_key_file:
            back = dataclasses.replace(back, api_key_file=None)
        SPD.attach(back, Path(self._root().hub.dir) / "spend", tier, t)
        return back

    def run_tier(self, run_dir: Path) -> str | None:
        """판 125: 지난 기록이 사람만 등급의 백엔드로 돌았으면 그 등급 이름(다시 열 때 같은 모델로). 아니면 None(그 자리의 기본)."""
        try:
            with open(Path(run_dir) / "events.jsonl", encoding="utf-8") as f:
                bname = ((json.loads(f.readline()).get("meta") or {}).get("backend") or {}).get("name")
        except (OSError, ValueError):
            return None
        root = self._root()
        if not bname or bname == root._backend.name and not os.environ.get("STEWART_INSTANCE"):
            return None
        for r in self.tier_rows()["rows"]:
            if r.get("backend") == bname and r.get("manual"):
                return r["tier"]
        return None

    def kind_profile(self, kind: str):
        """판 120: 그 종류 대화의 (프로필, 백엔드). 메인과 같은 종류면 메인 것. 종류마다 한 번만 풀어 둔다."""
        root = self._root()
        if kind == root.kind:
            return root._profile, root._backend
        cache = root.__dict__.setdefault("_kind_cache", {})
        if kind not in cache:
            if root.kind_resolver is None:
                raise ValueError(f"이 허브는 {'대화' if kind == 'talk' else '코드'} 칸을 열 수 없다(종류 풀이가 없음)")
            p, b = root.kind_resolver(kind)
            cache[kind] = (with_role(p), b)
        return cache[kind]

    def new_chat(self, how: str = "fresh", source: str = "main", run_dir: str = "", title: str = "", kind: str | None = None,
                 tier: str | None = None) -> "HubChat":
        """새 대화. fresh = 빈 문맥(격리), fork = source 대화를 지금까지 복사해 갈라짐(이후 서로 독립),
        reopen = 지난 대화 기록(run_dir)을 새 칸으로. 같은 프로젝트·기억·현황판·허용 규칙을 쓰고, 기록 폴더·압축·문맥 % 는 따로."""
        root = self._root()
        history, resumed = None, None
        if kind not in (None, "talk", "code"):
            raise ValueError("kind 는 talk·code")
        if how == "fork":
            src = root.conv(source)
            if src.busy:
                raise RuntimeError(f"{'메인' if src.name == 'main' else src.title} 이 일하는 중이다 — 차례가 끝난 뒤 가른다")
            history = json.loads(json.dumps(src.sess.messages, ensure_ascii=False))
            resumed = str(src.sess.rec.dir)
            kind = getattr(src, "kind", root.kind)   # 갈래는 원래 대화와 같은 종류
            tier = getattr(src, "tier", "") or None   # 판 125: 같은 등급
        elif how == "reopen":
            from .resume import load_messages
            if run_dir not in {x["run_dir"] for x in root.past_mains(100000)}:
                raise ValueError("지난 대화 목록에 없는 기록")
            history, resumed = load_messages(Path(run_dir)), run_dir
            kind = kind or _run_kind(Path(run_dir), root.kind)
            tier = tier or root.run_tier(Path(run_dir))   # 판 125: 사람만 등급(바깥 등)으로 하던 대화는 그 등급으로
        elif how != "fresh":
            raise ValueError("how 는 fresh·fork·reopen")
        kind = kind or root.kind
        root._chat_n += 1
        while f"chat-{root._chat_n}" in root.chats:
            root._chat_n += 1
        name = f"chat-{root._chat_n}"
        if not title:
            title = {"fresh": f"대화 {root._chat_n}", "fork": f"갈래 {root._chat_n}", "reopen": f"다시 연 대화 {root._chat_n}"}[how]
        c = HubChat(root, name, title=title, origin=how, history=history, resumed_from=resumed, kind=kind, tier=tier)
        root.chats[name] = c
        root.hub.event("chat_new", chat=name, how=how, chat_kind=kind, tier=tier or None, source=source if how == "fork" else None,
                       run_dir=str(c.sess.rec.dir))
        return c

    def close_chat(self, name: str) -> str:
        """대화 닫기(기록은 runs 에 남아 지난 세션에서 다시 연다). 일하는 중이면 거절. 메인은 닫지 않는다."""
        root = self._root()
        c = root.chats.get(name)
        if c is None:
            raise KeyError(name)
        if c.busy:
            raise RuntimeError("일하는 중이다 — 멈춘 뒤 닫는다")
        run = str(c.sess.rec.dir)
        c.close()
        root.chats.pop(name, None)
        root.hub.event("chat_close", chat=name, run_dir=run)
        return run

    # ── 쉬는 메인 깨우기(판 109, SW-43 ③): 메인 차례가 끝난 뒤 작업자가 끝나면 사람 입력 없이 한 차례(연달아 AUTO_WAKE_MAX 번까지) ──
    AUTO_WAKE_MAX = 3

    def auto_wake(self) -> str | None:
        if os.environ.get("STEWART_AUTO_WAKE", "1") == "0" or self.busy:
            return None
        pool = getattr(self.sess, "pool", None)
        if pool is None:
            return None
        done = [k for k, r in list(pool.w.items()) if r["status"] == "done" and not r["collected"] and not r.get("woke")]
        if not done:
            return None
        for k in done:
            pool.w[k]["woke"] = True
            pool.w[k]["noticed"] = True
        if getattr(self, "_auto_wakes", 0) >= self.AUTO_WAKE_MAX:
            self.ui._add("hub", f"작업자 {', '.join(done)} 끝 — 자동으로 이어 가기를 {self.AUTO_WAKE_MAX}번 해서 멈췄다. 이어 가려면 말을 건다")
            return None
        if not self._busy.acquire(blocking=False):
            return None
        self._auto_wakes = getattr(self, "_auto_wakes", 0) + 1
        self.ui._add("hub", f"자동으로 이어 감 — 작업자 {', '.join(done)} 끝")
        text = f"[허브] 작업자 {', '.join(done)} 가 끝났다 — workers(action='collect') 로 결과를 받아 이어 간다."
        self.hub.event("auto_wake", ids=done)
        self._turn = threading.Thread(target=self._run_turn, args=(text, None), daemon=True)
        self._turn.start()
        return "woke"

    def status_info(self) -> dict:
        """판 111(SW-45 ⑩): [상태] 탭 — 켜진 손잡이(환경에 값 있는 것)·도구 목록·고정 앞부분 토큰·모드·생각·문맥 예산. 모델 문맥과 무관."""
        from . import features as FT
        sess = self.sess
        sysm = sess.messages[0].get("content") if sess.messages and sess.messages[0].get("role") == "system" else ""
        f = FT.fingerprint(sysm, sess.tool_defs, "chat")
        return {"knobs": f["knobs"], "tools": f["tools"], "prefix_tokens": f["prefix_tokens"], "prefix_sha": f["prefix_sha"],
                "mode": sess.mode, "think": sess.think, "budget": sess.backend.budget(), "window": sess.backend.max_context,
                "backend": sess.backend.name, "model": sess.backend.model, "toggled_off": sorted(getattr(sess, "disabled_tools", ())),
                "all_knobs": list(FT.KNOBS),
                # 판 126: 이 대화에서 모델로 실제 가는 것 — 지시문 본문·도구 정의(이름·설명 첫 줄)·지금 문맥의 메시지 수(과정은 다 닿게)
                "system": str(sysm or ""), "messages": len(sess.messages),
                "tool_defs": [{"name": (d.get("function") or {}).get("name", ""),
                               "desc": str((d.get("function") or {}).get("description") or "").split("\n")[0][:200]} for d in (sess.tool_defs or [])]}

    def workers_view(self) -> list[dict]:
        pool = getattr(self.sess, "pool", None)
        if pool is None:
            return []
        now = time.monotonic()
        return [{"id": k, "tier": r.get("tier") or "", "status": r["status"], "task": r["task"][:80], "collected": r["collected"],
                 "secs": round(((r["ended"] or now) - r["started"]) if r["started"] else 0)} for k, r in list(pool.w.items())][-12:]

    # ── 허브 고리 ──
    def tick(self) -> dict:
        for cv in [self] + list(getattr(self, "chats", {}).values()):
            try:
                cv.auto_wake()
            except Exception as e:   # noqa: BLE001 — 깨우기 실패가 고리를 멈추지 않게
                self.hub.event("auto_wake_error", error=type(e).__name__)
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
        self._stop_running_turn()
        for c in list(self.chats.values()):
            try:
                c.close()
            except Exception:
                pass
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
        root = self._root()
        return {"ts": now(), "chat": self.name, "hub_label": getattr(root, "label", ""), "hub_color": getattr(root, "color", ""),
                "busy": self.busy, "sessions": self.hub.list(), "queue": self.hub.queue(),
                "main_log": log, "stopping": self.ui.halt_req.is_set(),
                "live": {"text": live["text"][-4000:], "think": live["think"], "think_text": live.get("think_text", "")[-4000:],
                         "think_since": live.get("think_since")} if self.busy else None,
                "now": self.ui.now if self.busy else None, "server_time": time.time(),
                "todos": list(getattr(self.sess.env, "todos", None) or []), "todos_at": getattr(self.sess.env, "todos_at", None),
                "main_run": str(self.sess.rec.dir),
                "vision": bool(self.sess.backend.vision), "image_tiles": bool(getattr(root, "image_tiles", False)),
                "pending": list(self.ui.pending),
                "backend": {"name": self.sess.backend.name, "model": self.sess.backend.model,
                            **({"spend": self.sess.backend.spend.public()} if getattr(self.sess.backend, "spend", None) is not None else {}),
                            **({"external": self.sess.backend.provider_label(), "used": dict(self.sess.backend.used)}
                               if self.sess.backend.external() else {})},
                "tokens": self.tokens(), "resume_note": self.resume_note(), "model": self.watch.status,
                "web": getattr(self.sess, "web_status", None),
                "bans": [b.view() for b in getattr(self.sess, "bans", [])], "board": self.hub.board.view(),
                "chats": self._chat_list(), "mode": self.sess.mode, "mode_max": getattr(self._root(), "_mode", "edits"),
                "think": self.sess.think, "effort": self._effort_state(), "workers": self.workers_view(), "budget": self.sess.backend.budget(), "budget_set": getattr(self.sess, "_backend0", None) is not None
                and self.sess.backend is not self.sess._backend0, **self._tool_ui()}

    def _effort_state(self) -> dict:
        """판 132: 이 대화의 생각 깊이(reasoning_effort)와 출처 — "이 대화" 판. value None = 보내지 않음(서버 기본)."""
        tks = getattr(self.sess, "tks", None) or {}
        rej = tks.get("effort_rejected")
        note = tks.get("effort_note") or (f"{rej['value']} 은 백엔드 {rej.get('backend', '')} 가 받는 낱말"
                                          f"({'·'.join(rej.get('allowed') or [])})이 아니라 보내지 않는다" if rej else "")
        return {"value": tks.get("effort"), "source": (tks.get("source") or {}).get("effort") or "", "note": note}

    def _tool_ui(self) -> dict:
        """판 99: 켬·끔 손잡이(프로필에 toggle_tools 가 없으면 비어 있다)."""
        sess = self.sess
        tg = [{"name": n, "on": n not in sess.disabled_tools} for n in getattr(sess, "toggleable", [])]
        return {"tool_toggles": tg} if tg else {}

    # ---- 차례 되감기(판 105, SW-45 ①) ----
    def undo_turn(self, turn: int, cut: bool) -> dict:
        if not self._busy.acquire(blocking=False):
            return {"ok": False, "error": "메인이 일하는 중이다 — 멈추거나 끝난 뒤에"}
        try:
            r = self.sess.undo_turn(int(turn), files=True, cut=cut)
        except (ValueError, OSError) as e:
            return {"ok": False, "error": str(e)}
        finally:
            self._busy.release()
        if cut:   # 화면 기록도 그 차례 사람 말부터 지운다
            with self.ui._lock:
                keep = []
                for e in list(self.ui.log):
                    if e.get("kind") == "user" and e.get("turn") == int(turn):
                        break
                    keep.append(e)
                self.ui.log.clear()
                self.ui.log.extend(keep)
        msg = (f"차례 {turn} 을 " + ("지웠다 — 내 말을 입력 칸에 되돌렸다" if cut else "파일만 되돌렸다")
               + (f" · 되돌린 파일 {len(r['restored'])}개" if r["restored"] else " · 되돌릴 파일 없음")
               + " (셸 명령이 바꾼 파일은 되돌리지 못한다)")
        self.ui._add("info", msg)
        return {"ok": True, **r}

    # ---- 머리줄 모드·생각(판 103, SW-45 ②): 문맥 값 0 — 계획 모드 안내만 지금처럼 사람 말 앞에 ----
    def set_mode(self, mode: str) -> dict:
        from .hub import MODE_RANK
        top = getattr(self._root(), "_mode", "edits")
        if mode not in MODE_RANK or MODE_RANK[mode] > MODE_RANK.get(top, 0):
            return {"ok": False, "error": f"허브를 켤 때 정한 모드({top})보다 넓힐 수 없다"}
        msg = self.sess.set_mode(mode)
        self.tools.main_mode = mode
        self.ui._add("info", f"모드 {mode}" + (f" — {msg}" if msg else ""))
        return {"ok": True, "mode": mode}

    def set_budget(self, tokens) -> dict:
        if tokens is not None and not (isinstance(tokens, int) and tokens > 0):
            return {"ok": False, "error": "tokens"}
        w = self.sess.set_budget(tokens)
        self.ui._add("info", f"문맥 예산 {w:,} 토큰" + (" (백엔드 설정 그대로)" if tokens is None else "")
                     + f" — 서버 창 {self.sess.backend.max_context:,} 안. 줄이면 압축·요약이 일찍 돈다")
        return {"ok": True, "budget": w}

    def set_think(self, think) -> dict:
        if think not in (True, False, None):
            return {"ok": False, "error": "think"}
        self.sess.think = think
        self.sess.rec.event("think", value=think)
        self.ui._add("info", "생각(thinking) " + {None: "백엔드 기본", True: "켜짐", False: "꺼짐"}[think])
        return {"ok": True, "think": think}

    def set_tool(self, name: str, on: bool) -> dict:
        ok = self.sess.set_tool(name, on)
        if ok:
            self.ui._add("info", f"{name} 도구를 {'켰다 — 다음 걸음부터 모델이 쓸 수 있다' if on else '껐다 — 모델에게 안 보인다'}")
        return {"ok": ok}

    def tokens(self) -> dict:
        """화면 문맥 막대·토큰 줄: 지난 단계 입력(= 지금 문맥), 창, 줄이기 선, 누계, 이번(마지막) 차례 몫."""
        st, comp = self.sess.st, self.sess.comp
        at = getattr(comp, "next_at", None)
        out = {"last_prompt": st.get("last_prompt_tokens") or 0, "window": getattr(comp, "full_context", None) or self.sess.backend.budget(),
               "model_window": self.sess.backend.max_context, "estimated": bool(st.get("prompt_estimated")),
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


class HubChat(HubMain):
    """사람이 연 대화 하나: 메인과 같은 채팅(같은 도구·명령·사진·압축·승인·생각), 허브 안에서 돈다.
    현황판·기억·모델 감시·허용 규칙은 메인 것을 함께 쓰고, ChatSession·기록 폴더·화면 기록·바쁨은 따로."""

    def __init__(self, main: HubMain, name: str, title: str = "", origin: str = "fresh",
                 history: list | None = None, resumed_from: str | None = None, kind: str | None = None, tier: str | None = None):
        from .record import Recorder
        hub = main.hub
        self.hub, self.main, self.name, self.title, self.origin = hub, main, name, title, origin
        self.kind = kind or main.kind   # 판 120: 만들 때 정하고 바뀌지 않는다(지시문·도구 고정 → 앞부분 캐시 유지)
        self.tier = tier or ""          # 판 125: 사람이 고른 등급(만들 때 정하고 바뀌지 않는다)
        prof, back = main.tier_profile(tier, self.kind) if tier else main.kind_profile(self.kind)
        self.chats = {}
        hub.register_chat(name, prof.workdir, main._mode, title=title, origin=origin, kind=self.kind)
        self.ui = HubMainUI(hub, name)
        self.ui.hub_rules = hub.allow_rules
        hub.chat_answers[name] = self.ui.put_answer
        self.tools = HT.MainTools(hub, main_mode=main._mode, default_profile=main._worker_profile,
                                  default_backend=main._worker_backend)
        self._profile, self._backend, self._mode, self._runs_dir = prof, back, main._mode, main._runs_dir
        self._worker_profile, self._worker_backend = main._worker_profile, main._worker_backend
        rec = Recorder(self._runs_dir, "hub-main")
        if origin == "fork":   # 판 124.2: 갈래는 원래 대화를 숨기지 않는다(지난 대화 목록은 다시 열기만 이어 받은 것으로 본다)
            try:
                (Path(rec.dir) / "hub-origin.json").write_text(json.dumps({"how": "fork"}), encoding="utf-8")
            except OSError:
                pass
        self.sess = ChatSession(self._profile, self._backend, rec, ui=self.ui, stream=True, mode=self._mode, default_mode="edits",
                                extra_tools=(HT.DEFINITIONS, self.tools.execute), history=history, resumed_from=resumed_from)
        if history:
            _replay(self.ui, history, resumed_from)
            self.ui._add("meta", f"{'갈라 온' if origin == 'fork' else '다시 연'} 대화다 — 앞 문맥을 이어 받았다({resumed_from}). "
                                 "이제부터는 원래 대화와 서로 독립이다.")
            if origin == "fork":   # 갈래는 원래 대화가 따로 이어지므로 화면에 한 줄 남긴다(다시 열기는 같은 대화처럼 그대로 이어 쓴다)
                self.ui._add("info", "여기서 갈라졌다 — 이제부터 원래 대화와 서로 독립이다.")
        self.ui.env = self.sess.env
        self.ui.run_rec = self.sess.rec   # 판 127: 승인 기록을 이 대화 기록 폴더에도
        self.sess.env.on_quota = main._quota_alert
        self.watch = main.watch
        self.ui.on_backend_error = main.ui.on_backend_error
        self.ui.on_backend_ok = main.ui.on_backend_ok
        self.ui.sd.set_status(status="idle", run_dir=str(rec.dir))
        self._idle_since = time.monotonic()
        self._busy = threading.Lock()
        self._stop = threading.Event()
        self._loop = None
        self._turn = None
        self._turn0 = None
        self._hist_est = (None, 0)
        self._resumed = bool(history)

    def _notify_turn_end(self, reason: str, secs: float, stopped: bool = False) -> str | None:
        return None   # 폰 알림은 메인 차례만(대화가 여럿이면 시끄럽다) — 승인 대기 알림은 허브 고리가 그대로 보낸다

    def maybe_dream(self, closing: bool = False, ask_model=None) -> dict | None:
        return None   # 꿈꾸기는 메인 고리만

    def start_loop(self) -> None:
        return None

    def close(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        self._stop_running_turn()
        try:
            self.sess.close()
        finally:
            self.hub.drop_chat(self.name)


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
             "accounts", "listen", "user_root",
             "label", "color", "notify_tag",   # 판 77: 허브가 여럿일 때 이름표·색 띠·알림 꼬리표
             "image_ocr", "image_tiles"}        # 판 82: 사진 OCR 글 덧붙이기·긴 그림 조각(둘 다 기본 끔)
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
    project = Path(a.project or _profile_workdir(getattr(a, "profile", None)) or os.getcwd()).resolve()
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
            profile, backend = main_tier(self.a, str(wd))
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
    project = Path(a.project or _profile_workdir(getattr(a, "profile", None)) or os.getcwd()).resolve()
    st = Store(_state_dir(a, cfg, project))
    try:
        if a.action == "list":
            for n, u in sorted(st.users().items()):
                print(f"{n}\t{'막힘' if u.get('disabled') else '쓰는 중'}\t만듦 {u.get('created', '?')}")
            return 0
        if not a.name:
            raise AccountError("사용자 이름을 준다")
        if a.action in ("add", "passwd"):
            if a.action == "passwd" and a.name not in st.users():   # 없는 사용자면 비밀번호를 읽지 않는다(입력을 기다리며 멈추지 않게)
                raise AccountError(f"없는 사용자: {a.name}")
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
    project = Path(a.project or _profile_workdir(getattr(a, "profile", None)) or os.getcwd()).resolve()
    project.mkdir(parents=True, exist_ok=True)   # 판 118: 프로필의 대화 폴더가 처음이면 만든다
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
    if getattr(a, "kind", None) == "talk" and a.profile in (None, "chat"):   # 판 120: 메인 칸을 대화로(코드 칸은 이 프로젝트)
        from . import surfaces as SF
        a.profile = SF.TALK_PROFILE
    elif getattr(a, "kind", None) == "code" and a.profile == "talk":
        a.profile = "chat"
    main_wd = _profile_workdir(a.profile) if a.profile == "talk" else None
    if main_wd:
        Path(main_wd).mkdir(parents=True, exist_ok=True)
    profile, backend = main_tier(a, str(main_wd or project))
    history, prev = last_main(hub) if not getattr(a, "new_main", False) else (None, None)
    rec = Recorder(Path(a.runs_dir), "hub-main")
    main = HubMain(hub, profile, backend, rec, mode=a.mode, worker_profile=a.worker_profile or a.profile,
                   worker_backend=a.backend, history=history, resumed_from=prev)
    main.kind_resolver = kind_resolver(a, project, backend.name)   # 판 120: ＋새 대화에서 대화·코드 칸
    if os.environ.get("STEWART_INSTANCE") and backend.external():   # 판 125: 따로 도는 허브 = rubato 만 연결 — 메인·기본 칸도 돈 상한
        from . import spawn as _SP
        from . import spend as _SPD
        _SPD.attach(backend, Path(hub.dir) / "spend", "rubato", _SP.load_tiers().get("rubato"))
    main._project = project
    main.label, main.color = str(cfg.get("label") or ""), str(cfg.get("color") or "")   # 허브가 여럿일 때 가름(머리 이름·색 띠)
    main.image_ocr, main.image_tiles = bool(cfg.get("image_ocr", False)), bool(cfg.get("image_tiles", False))   # 판 82 손잡이
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
