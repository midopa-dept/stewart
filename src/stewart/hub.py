"""허브: 여러 작업자 세션을 띄우고 지시·보고를 파일 편지함으로 주고받는다(웹 화면·메인 도구는 아직 없다).

허브 폴더(state_dir):
  sessions.json                 세션 등록부(허브만 쓴다. 임시 파일에 쓰고 rename)
  events.jsonl                  허브 기록
  sessions/<이름>/
    session.json                작업자 설정(프로필·백엔드·작업 폴더·모드·runs 폴더)
    inbox/NNNN.md               지시(머리 줄 JSON: seq·kind·replaces·from·ts)
    outbox/NNNN.md              보고(머리 줄 JSON: answers·reason·steps·files_changed·report_kind)
    ask/NNNN.json               작업자가 기다리는 확인 질문
    answer/NNNN.json            사람의 답 기록. 판정 근거로 읽지 않는다(답은 승인 통로로만 간다)
    status                      작업자 상태 한 줄(JSON, 작업자만 쓴다)
    state.json                  작업자가 넘겨받은 지시 번호(작업자만 쓴다)
    control                     허브 → 작업자 요청(pause·stop). 작업자는 단계 경계에서 읽는다

알림 훅(notify_url): 승인 대기가 새로 생기면 POST 한 번. 본문은 "<세션>: <묻는 종류> 승인 대기" 뿐이다
(명령·경로·diff 는 넣지 않는다). 실패해도 다시 보내지 않고 기록에 notify_fail 만 남긴다.

승인 통로: 작업자를 띄울 때 만든 파이프. 읽는 쪽 fd 는 작업자에게만 넘기고(POSIX pass_fds, Windows 는 핸들 목록
상속 — osdep.child_pipe), 작업자는 받자마자 상속 불가로 둔다. 셸 도구가 띄우는 자식은 그 fd 를 갖지 않는다. 답은 {"seq", "answer"} 한 줄 JSON 이고,
작업자는 지금 기다리는 질문 번호와 같은 답만 받는다. answer/ 파일을 써도 승인은 일어나지 않는다.
"""

from __future__ import annotations

import functools
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
import weakref
from datetime import datetime
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,31}$")
KINDS = ("지시", "긴급", "대체")
STATUSES = ("starting", "idle", "working", "waiting_approval", "waiting_main", "paused", "stopped", "failed")
HEAD = "<!-- stewart "
HEAD_END = " -->"
HUB_ENV_PREFIX = "STEWART_HUB_"
LIVE = ("starting", "idle", "working", "waiting_approval", "waiting_main")
KIND_LABEL = {"outside_write": "밖 쓰기", "outside_read": "밖 읽기", "config": "설정 폴더", "more_steps": "단계 더"}
MODE_RANK = {"plan": 0, "ask": 1, "edits": 2, "auto": 3}   # 넓을수록 크다(작업자는 메인보다 넓을 수 없다)


def kind_label(ask: dict) -> str:
    """알림에 쓰는 묻는 종류: 실행 확인이면 도구 이름, 아니면 한국어 이름표. 명령·경로는 넣지 않는다."""
    k = ask.get("kind")
    if k == "confirm":
        tool = (ask.get("args_summary") or {}).get("tool")
        return tool if isinstance(tool, str) and re.fullmatch(r"[a-z_]{1,40}", tool) else "실행 확인"
    return KIND_LABEL.get(k, "확인")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 같은 프로세스의 여러 스레드(웹 처리기·허브 고리·화면 스트림)가 겹쳐 써도 임시 파일이 섞이지 않게
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(text, encoding="utf-8")
    from . import osdep
    try:
        osdep.replace_retry(tmp, path)     # Windows: 웹·작업자가 대상을 열고 있으면 잠깐씩 다시(WIN-5)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def write_json(path: Path, obj) -> None:
    write_atomic(path, json.dumps(obj, ensure_ascii=False, indent=1) + "\n")


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def pack(head: dict, body: str) -> str:
    return HEAD + json.dumps(head, ensure_ascii=False) + HEAD_END + "\n" + body.rstrip("\n") + "\n"


def unpack(text: str) -> tuple[dict, str]:
    first, _, body = text.partition("\n")
    if first.startswith(HEAD) and first.endswith(HEAD_END):
        try:
            return json.loads(first[len(HEAD):-len(HEAD_END)]), body
        except ValueError:
            pass
    return {}, text


def seq_name(n: int, ext: str) -> str:
    return f"{n:04d}.{ext}"


def seqs(folder: Path, ext: str) -> list[int]:
    if not folder.is_dir():
        return []
    out = []
    for p in folder.iterdir():
        m = re.fullmatch(r"(\d{4})\." + ext, p.name)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


class SessionDir:
    """세션 하나의 편지함. 허브와 작업자가 같이 쓰되, 쓰는 파일은 나뉜다."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.inbox, self.outbox = self.root / "inbox", self.root / "outbox"
        self.ask_dir, self.answer_dir = self.root / "ask", self.root / "answer"
        self.status_file, self.state_file = self.root / "status", self.root / "state.json"
        self.control_file, self.config_file = self.root / "control", self.root / "session.json"

    def make(self) -> None:
        for d in (self.inbox, self.outbox, self.ask_dir, self.answer_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ── 지시(허브가 쓴다) ──
    def send(self, text: str, kind: str = "지시", replaces: int | None = None, sender: str = "main") -> int:
        if kind not in KINDS:
            raise ValueError(f"지시 종류는 {', '.join(KINDS)} 중 하나")
        if kind == "대체" and not replaces:
            raise ValueError("대체는 replaces(바꿀 지시 번호)가 있어야 한다")
        n = (seqs(self.inbox, "md") or [0])[-1] + 1
        head = {"seq": n, "kind": kind, "replaces": replaces, "from": sender, "ts": now()}
        write_atomic(self.inbox / seq_name(n, "md"), pack(head, text))
        return n

    def instruction(self, n: int) -> tuple[dict, str]:
        return unpack((self.inbox / seq_name(n, "md")).read_text(encoding="utf-8"))

    # ── 보고(작업자가 쓴다) ──
    def report(self, n: int, text: str, **head) -> None:
        write_atomic(self.outbox / seq_name(n, "md"), pack({"answers": n, "ts": now(), **head}, text))

    def read_report(self, n: int) -> tuple[dict, str] | None:
        p = self.outbox / seq_name(n, "md")
        return unpack(p.read_text(encoding="utf-8")) if p.is_file() else None

    # ── 상태 ──
    def status(self) -> dict:
        return read_json(self.status_file, {}) or {}

    def set_status(self, **fields) -> None:
        write_json(self.status_file, {**self.status(), **fields, "ts": now()})

    def state(self) -> dict:
        return read_json(self.state_file, {}) or {"taken": [], "urgent_taken": []}

    def save_state(self, st: dict) -> None:
        write_json(self.state_file, st)

    def control(self) -> str | None:
        try:
            v = self.control_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return v or None

    def set_control(self, v: str | None) -> None:
        if v is None:
            self.control_file.unlink(missing_ok=True)
        else:
            write_atomic(self.control_file, v + "\n")

    # ── 작업자가 다음에 받을 지시 ──
    def next_instruction(self) -> tuple[int, dict, str] | None:
        """아직 넘겨받지 않은 일반 지시(지시·대체) 가운데 가장 앞 번호. 뒤에 온 대체가 바꾼 지시는 건너뛴다."""
        st = self.state()
        taken = set(st.get("taken", []))
        heads = {n: self.instruction(n) for n in seqs(self.inbox, "md")}
        replaced = {h.get("replaces") for h, _ in heads.values() if h.get("kind") == "대체"}
        for n, (h, body) in heads.items():
            if n in taken or h.get("kind") == "긴급":
                continue
            if n in replaced:
                continue
            return n, h, body
        return None

    def take(self, n: int, urgent: bool = False) -> None:
        st = self.state()
        key = "urgent_taken" if urgent else "taken"
        st.setdefault(key, [])
        if n not in st[key]:
            st[key].append(n)
        # 대체로 버려진 지시도 넘겨받은 것으로 친다(다시 고르지 않게)
        if not urgent:
            h, _ = self.instruction(n)
            if h.get("kind") == "대체" and h.get("replaces") and h["replaces"] not in st["taken"]:
                st["taken"].append(h["replaces"])
                st.setdefault("dropped", []).append(h["replaces"])
        self.save_state(st)

    def urgent_pending(self) -> list[tuple[int, str]]:
        st = self.state()
        done = set(st.get("urgent_taken", []))
        out = []
        for n in seqs(self.inbox, "md"):
            h, body = self.instruction(n)
            if h.get("kind") == "긴급" and n not in done:
                out.append((n, body))
        return out


def _alive(pid: int | None) -> bool:
    from . import osdep
    return osdep.pid_alive(pid)     # Windows 에서 kill(pid, 0) 은 Ctrl-C 를 보낸다 — osdep 이 가른다


def worker_env(base: dict | None = None) -> dict:
    """작업자 프로세스 env: 허브 비밀(STEWART_HUB_*)을 뺀다. 나머지 STEWART_*(백엔드 파일 등)는 작업자에게 필요하다."""
    env = dict(os.environ if base is None else base)
    for k in list(env):
        if k.startswith(HUB_ENV_PREFIX):
            env.pop(k)
    return env


def _locked(fn):
    """등록부를 읽고 고쳐 쓰는 메서드는 한 번에 하나씩(스레드 사이 경합으로 등록부가 비지 않게)."""
    @functools.wraps(fn)
    def wrap(self, *a, **k):
        with self._lock:
            return fn(self, *a, **k)
    return wrap


LIVE_HUBS: "weakref.WeakSet[Hub]" = weakref.WeakSet()
WORKERS: list = []   # 시험 묶음이 끝나지 않은 작업자를 찾는 데 쓴다


class Hub:
    def __init__(self, state_dir: Path, runs_dir: Path, notify_url: str | None = None, notify=None,
                 worktree_root: str | None = None):
        self.dir = Path(state_dir)
        LIVE_HUBS.add(self)
        self._lock = threading.RLock()
        self.worktree_root = worktree_root   # "{repo}" 자리 글을 받는다. 없으면 default_worktree_dir
        self.main_answer = None              # 메인 세션의 답 받기(허브 프로세스 안 큐). HubMain 이 붙인다
        self.chat_answers: dict = {}         # 허브 안 대화(메인 말고 사람이 연 대화)의 답 받기: 이름 → 고리
        self.runs_dir = Path(runs_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.reg_file = self.dir / "sessions.json"
        self.procs: dict[str, subprocess.Popen] = {}
        self.pipes: dict[str, int] = {}   # 이름 → 승인 통로 쓰는 쪽 fd(허브만 가진다)
        self.notify_url = (notify_url or "").strip() or None
        self._notify = notify or self._post   # 시험은 바꿔 끼운다
        self._notify_on = bool(self.notify_url or notify)
        self.notices: list[str] = []          # 메인 차례에 끼워 넣을 알림(보고 도착)
        from .allowrules import HubStore
        self.allow = HubStore(self.dir)        # "이 꼴 늘 허용"(허브 폴더, 저장소 밖). 허브가 뜰 때 한 번 읽고 메모리에 둔다
        self._allow_rules = self.allow.load()
        self._session_rules: list = []          # "이 세션만" 늘 허용(허브가 끝나면 사라진다)
        self._rulesets: dict = {}               # 세션별 사용자·저장소 규칙(승인 화면의 "이미 허용" 표시용)

    # ── 등록부 ──
    def registry(self) -> dict:
        return read_json(self.reg_file, {"sessions": {}}) or {"sessions": {}}

    def _save(self, reg: dict) -> None:
        write_json(self.reg_file, reg)

    @_locked
    def _update(self, name: str, **fields) -> dict:
        reg = self.registry()
        e = reg["sessions"][name]
        e.update(fields)
        self._save(reg)
        return e

    def event(self, kind: str, **data) -> None:
        with (self.dir / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": now(), "actor": "hub", "kind": kind, **data}, ensure_ascii=False) + "\n")

    def sdir(self, name: str) -> SessionDir:
        return SessionDir(self.dir / "sessions" / name)

    # ── 세션 ──
    @_locked
    def spawn(self, name: str, task: str, workdir: str | Path | None, profile: str, backend: str | None = None,
              mode: str = "edits", role: str = "worker", worktree: dict | None = None, sender: str = "main") -> dict:
        if not NAME_RE.match(name):
            raise ValueError("세션 이름은 영숫자·'-' 32자까지")
        reg = self.registry()
        if name in reg["sessions"]:
            raise ValueError(f"이미 있는 세션: {name}")
        if mode not in MODE_RANK:
            raise ValueError(f"모드는 {', '.join(MODE_RANK)} 중 하나")
        wt = None
        if worktree:
            wt = self._add_worktree(name, worktree)
            workdir = wt["path"]
        if workdir is None:
            raise ValueError("workdir 이나 worktree 가 있어야 한다")
        wd = Path(workdir).resolve()
        if not wd.is_dir():
            raise ValueError(f"작업 폴더가 없다: {wd}")
        sd = self.sdir(name)
        sd.make()
        write_json(sd.config_file, {"name": name, "profile": profile, "backend": backend, "workdir": str(wd),
                                    "mode": mode, "runs_dir": str(self.runs_dir)})
        reg["sessions"][name] = {"name": name, "role": role, "workdir": str(wd), "worktree": wt, "profile": profile,
                                 "backend": backend, "mode": mode, "executor": {"kind": "local"}, "status": "starting",
                                 "pid": None, "started": now(), "last_event": None, "run_dir": None,
                                 "inbox_seq": 0, "outbox_seq": 0, "read_reports": [], "notified_asks": [],
                                 "noticed_reports": 0}
        self._save(reg)
        n = sd.send(task, sender=sender)
        self._update(name, inbox_seq=n)
        self.event("session_spawn", session=name, profile=profile, backend=backend, mode=mode)
        self._start(name)
        return self.registry()["sessions"][name]

    def _add_worktree(self, name: str, w: dict) -> dict:
        """git worktree add -b <가지> <허브 폴더>/worktrees/<이름> <바탕>. 지우기는 하지 않는다(사람이 한다)."""
        repo = Path(str(w.get("repo") or "")).expanduser().resolve()
        branch, base = str(w.get("branch") or ""), str(w.get("base") or "HEAD")
        if not (repo / ".git").exists():
            raise ValueError(f"git 저장소가 아니다: {repo}")
        if not re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", branch) or branch.startswith(("-", "/")) or ".." in branch:
            raise ValueError("가지 이름이 바르지 않다")
        if base.startswith("-"):
            raise ValueError("바탕 이름이 바르지 않다")
        path = self.worktree_dir(repo) / name
        if path.exists():
            raise ValueError(f"worktree 자리가 이미 있다: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        p = subprocess.run(["git", "worktree", "add", "-b", branch, str(path), base], cwd=str(repo),
                           capture_output=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
        if p.returncode != 0:
            raise ValueError(f"git worktree add 실패: {(p.stderr or p.stdout).strip()[:300]}")
        self.event("worktree_add", session=name, branch=branch)
        return {"repo": str(repo), "branch": branch, "base": base, "path": str(path.resolve())}

    def worktree_dir(self, repo: Path) -> Path:
        """worktree 를 둘 폴더. 설정 worktree_root("{repo}" = 저장소 이름)가 이기고, 없으면
        저장소가 <뿌리>/repos/<이름> 꼴이고 <뿌리>/labs 가 있으면 <뿌리>/labs/<이름>/worktrees, 아니면 <허브 폴더>/worktrees."""
        if self.worktree_root:
            return Path(os.path.expanduser(self.worktree_root.replace("{repo}", repo.name))).resolve()
        if repo.parent.name == "repos" and (repo.parent.parent / "labs").is_dir():
            return repo.parent.parent / "labs" / repo.name / "worktrees"
        return self.dir / "worktrees"

    @_locked
    def register_main(self, workdir: Path, mode: str) -> None:
        """메인 세션을 등록부에 넣는다(프로세스 없음, 허브 안에서 돈다). 묻기는 같은 대기열에 session=main 으로 온다."""
        reg = self.registry()
        sd = self.sdir("main")
        sd.make()
        reg["sessions"]["main"] = {**reg["sessions"].get("main", {}), "name": "main", "role": "main",
                                   "workdir": str(Path(workdir).resolve()), "worktree": None, "profile": None,
                                   "backend": None, "mode": mode, "executor": {"kind": "local"}, "status": "idle",
                                   "pid": os.getpid(), "started": now(), "last_event": None, "run_dir": None,
                                   "inbox_seq": 0, "outbox_seq": 0, "read_reports": [], "notified_asks": [],
                                   "noticed_reports": 0}
        self._save(reg)

    @_locked
    def register_chat(self, name: str, workdir: Path, mode: str, title: str = "", origin: str = "fresh") -> None:
        """사람이 연 대화(메인과 같은 채팅, 허브 안에서 돈다 — 프로세스 없음). 승인은 같은 대기열에 session=이름 으로 온다."""
        reg = self.registry()
        self.sdir(name).make()
        reg["sessions"][name] = {"name": name, "role": "chat", "title": title, "origin": origin,
                                 "workdir": str(Path(workdir).resolve()), "worktree": None, "profile": None,
                                 "backend": None, "mode": mode, "executor": {"kind": "local"}, "status": "idle",
                                 "pid": os.getpid(), "started": now(), "last_event": None, "run_dir": None,
                                 "inbox_seq": 0, "outbox_seq": 0, "read_reports": [], "notified_asks": [],
                                 "noticed_reports": 0}
        self._save(reg)

    @_locked
    def drop_chat(self, name: str) -> None:
        """닫은 대화를 등록부에서 뺀다(기록 runs 는 그대로 — 지난 세션에서 다시 연다)."""
        reg = self.registry()
        if reg["sessions"].get(name, {}).get("role") == "chat":
            reg["sessions"].pop(name)
            self._save(reg)
        self.chat_answers.pop(name, None)

    def _start(self, name: str, resume: str | None = None) -> None:
        sd = self.sdir(name)
        sd.set_control(None)
        from . import osdep
        r, w, chan, kw = osdep.child_pipe()
        argv = [sys.executable, "-m", "stewart.worker", "--session-dir", str(sd.root), *chan]
        if resume:
            argv += ["--resume", resume]
        log = (sd.root / "worker.log").open("ab")
        try:
            # 새 프로세스 묶음: 허브 콘솔의 Ctrl-C 가 작업자에게 가지 않는다(멈춤은 허브가 control 로 요청한다).
            # Windows 는 Job 에 넣는다 — 끝내기가 묶음째, 허브가 죽으면 작업자도 끝난다(다음 허브가 paused 로 본다)
            p = subprocess.Popen(argv, cwd=str(sd.root), env=worker_env(), stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=subprocess.STDOUT, **kw, **osdep.group_popen_kwargs())
            osdep.attach_group(p)           # 풀지 못하면 작업자를 끝내고 OSError
            WORKERS.append(p)               # 강한 참조(시험 감시가 허브 객체가 버려져도 남은 작업자를 찾게, 판 68)
            del WORKERS[:-200]
        except BaseException:
            os.close(w)
            raise
        finally:
            os.close(r)
            log.close()
        self.procs[name], self.pipes[name] = p, w
        self._push_rules(name)
        self._update(name, pid=p.pid, status="starting")
        self.event("session_start", session=name, pid=p.pid, resume=bool(resume))

    # ── 셸 허용 규칙("이 꼴 늘 허용") ──
    def allow_rules(self) -> list:
        """허브 "늘 허용": 이 프로젝트 늘(allow-rules.json) + 이 세션만(허브 메모리, 허브가 끝나면 사라짐)."""
        return list(self._allow_rules) + list(self._session_rules)

    def _push_rules(self, name: str) -> None:
        """작업자에게는 승인 통로로만 보낸다(작업자는 규칙 파일을 읽지 않는다)."""
        from .allowrules import rules_payload
        w = self.pipes.get(name)
        if w is not None:
            os.write(w, (json.dumps({"type": "rules", "rules": rules_payload(self.allow_rules())}, ensure_ascii=False)
                         + "\n").encode())

    def add_allow(self, pattern: str, scope: str = "session"):
        """사람이 승인 화면에서 고른 꼴을 늘 허용에 더한다(웹 처리기만 부른다).
        scope=session(기본): 허브 메모리에만, project: 허브 폴더 allow-rules.json 에도. 위험 명령(NEVER)은 ValueError,
        넓은 꼴(python3 -c *)은 받는다 — 사람 결정(U2-8: "막지 않는다", 화면이 "모든 코드를 묻지 않고 돌린다"를 보인다)."""
        from .allowrules import Rule, check_always, parse_pattern
        if scope not in ("session", "project"):
            raise ValueError("scope 는 session 또는 project")
        if scope == "project":
            r = self.allow.add(pattern, broad_ok=True)     # 사람이 고른 꼴: 넓은 꼴도 받는다(화면이 경고를 보인다)
            self._allow_rules = self.allow.load()
        else:
            pat = parse_pattern(pattern)
            if pat is None:
                raise ValueError("규칙 꼴이 아니다(정확한 명령 또는 '앞부분 *')")
            why = check_always(pat, broad_ok=True)
            if why:
                raise ValueError(why)
            r = Rule(pat, "allow", "hub", "session")
            if all(x.id != r.id for x in self._session_rules):
                self._session_rules.append(r)
        for name in list(self.pipes):
            self._push_rules(name)
        self.event("allow_rule_add", rule=r.id, scope=scope)
        return r

    def remove_allow(self, rule_id: str) -> bool:
        before = len(self._session_rules)
        self._session_rules = [r for r in self._session_rules if r.id != rule_id]
        ok = len(self._session_rules) != before or self.allow.remove(rule_id)
        if ok:
            self._allow_rules = self.allow.load()
            for name in list(self.pipes):
                self._push_rules(name)
            self.event("allow_rule_remove", rule=rule_id)
        return ok

    def send(self, name: str, text: str, kind: str = "지시", replaces: int | None = None, sender: str = "main") -> int:
        sd = self.sdir(name)
        n = sd.send(text, kind, replaces, sender=sender)
        self._update(name, inbox_seq=n)
        self.event("instruction_in", session=name, seq=n, instruction_kind=kind, sender=sender)
        return n

    def read_report(self, name: str, seq: int) -> tuple[dict, str] | None:
        return self.sdir(name).read_report(seq)

    @_locked
    def mark_read(self, name: str, seq: int) -> None:
        e = self.registry()["sessions"][name]
        if seq not in e["read_reports"]:
            self._update(name, read_reports=sorted(e["read_reports"] + [seq]))

    def wait_report(self, name: str, seq: int, timeout_s: float = 30) -> tuple[dict, str] | None:
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            r = self.read_report(name, seq)
            if r is not None:
                return r
            time.sleep(0.05)
        return None

    def pending_asks(self, name: str) -> list[dict]:
        sd = self.sdir(name)
        done = set(seqs(sd.answer_dir, "json"))
        return [read_json(sd.ask_dir / seq_name(n, "json")) for n in seqs(sd.ask_dir, "json") if n not in done]

    def answer(self, name: str, seq: int, text: str, by: str = "human", via: str = "api") -> None:
        """사람의 답을 승인 통로로 보내고, 기록으로 answer/ 에 남긴다. 메인·작업자 도구에서는 부르지 않는다."""
        if name == "main":
            if self.main_answer is None:
                raise RuntimeError("메인 세션이 붙어 있지 않다")
            self.main_answer(seq, text)
        elif name in self.chat_answers:
            self.chat_answers[name](seq, text)
        else:
            w = self.pipes.get(name)
            if w is None:
                raise RuntimeError(f"승인 통로가 없다(허브가 띄운 작업자가 아님): {name}")
            os.write(w, (json.dumps({"seq": seq, "answer": text}, ensure_ascii=False) + "\n").encode())
        write_json(self.sdir(name).answer_dir / seq_name(seq, "json"), {"seq": seq, "by": by, "via": via, "ts": now()})
        self.event("approval_answer", session=name, seq=seq, by=by, via=via)

    @_locked
    def refresh(self) -> dict:
        """작업자 status 파일을 등록부에 옮기고, 죽은 작업자를 가려낸다."""
        reg = self.registry()
        for name, e in reg["sessions"].items():
            st = self.sdir(name).status()
            for k in ("status", "run_dir", "last_event", "outbox_seq"):
                if st.get(k) is not None:
                    e[k] = st[k]
            e["now"] = st.get("now") if st.get("status") in ("working", "waiting_approval") else None   # 화면의 "지금 하는 일" 한 줄
            if e.get("role") in ("main", "chat"):
                continue
            proc = self.procs.get(name)
            alive = proc.poll() is None if proc is not None else _alive(e.get("pid"))
            if not alive and e["status"] in ("starting", "idle", "working", "waiting_approval", "waiting_main"):
                e["status"] = "failed"
                self.event("session_fail", session=name, pid=e.get("pid"))
        self._save(reg)
        return reg

    def list(self) -> list[dict]:
        return list(self.refresh()["sessions"].values())

    def _halt(self, name: str, how: str, timeout_s: float) -> dict:
        sd = self.sdir(name)
        sd.set_control(how)
        proc = self.procs.get(name)
        if proc is not None:
            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:   # 단계 경계에 닿지 않으면 프로세스 묶음째 끝낸다
                from . import osdep
                osdep.kill_group(proc, hard=False)
                proc.wait(timeout=10)
        w = self.pipes.pop(name, None)
        if w is not None:
            os.close(w)
        self.procs.pop(name, None)
        st = sd.status()
        e = self._update(name, status="paused" if how == "pause" else "stopped", pid=None,
                         run_dir=st.get("run_dir") or self.registry()["sessions"][name].get("run_dir"))
        self.event("session_pause" if how == "pause" else "session_stop", session=name)
        return e

    def pause(self, name: str, timeout_s: float = 60, resume_on_start: bool = False) -> dict:
        e = self._halt(name, "pause", timeout_s)
        if resume_on_start:
            e = self._update(name, resume_on_start=True)
        return e

    def stop(self, name: str, timeout_s: float = 60) -> dict:
        return self._halt(name, "stop", timeout_s)

    def resume(self, name: str) -> dict:
        """멈춘(paused) 세션을 이어 간다. 끝낸(stopped)·실패한(failed) 세션도 기록(run_dir)이 있으면 그 대화로 다시 연다(U2-2 2b)."""
        e = self.registry()["sessions"][name]
        if e["status"] in ("stopped", "failed") and e.get("run_dir") and (Path(e["run_dir"]) / "messages.json").is_file():
            self.event("session_reopen", session=name, was=e["status"])
        elif e["status"] != "paused":
            raise ValueError(f"멈춘(paused) 세션이나 기록이 남은 끝낸 세션만 다시 연다: {name} 은 {e['status']}")
        self._start(name, resume=e.get("run_dir"))
        self.event("session_resume", session=name)
        return self.registry()["sessions"][name]

    # ── 대기열·알림 ──
    def queue(self) -> list[dict]:
        """모든 세션의 답을 기다리는 질문(답 기록이 없고 취소되지 않은 것), 만든 차례로."""
        from .allowrules import RuleSet, candidates, suggest
        out = []
        for name, e in self.registry()["sessions"].items():
            for q in self.pending_asks(name):
                if q and not q.get("cancelled"):
                    cmd = (q.get("args_summary") or {}).get("command")
                    extra = {}
                    if cmd and q.get("kind") == "confirm":
                        try:   # 늘 허용 후보를 못 만들어도 그 승인 건은 대기열에 남는다(U2-8 — 승인 칸이 사라지지 않게)
                            rs = self._rulesets.get(name) or self._rulesets.setdefault(name, RuleSet(Path(e["workdir"]), compat=True))
                            extra = {"suggest": suggest(cmd), "always": candidates(cmd, self.allow_rules() + rs.all())}
                        except Exception as ex:
                            extra = {"always": {"ok": False, "pieces": [], "reason": f"늘 허용 후보를 못 만들었다({type(ex).__name__}) — 한 번 허용을 쓴다"}}
                    out.append({**q, "session": name, **extra})
        return sorted(out, key=lambda q: (q.get("created") or "", q["session"], q["seq"]))

    def _post(self, text: str) -> None:
        req = urllib.request.Request(self.notify_url, data=text.encode("utf-8"), method="POST",
                                     headers={"Content-Type": "text/plain; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read(1)

    @_locked
    def tick(self) -> dict:
        """한 번 둘러보기: 등록부 맞추기, 새 승인 대기 알림, 새 보고 도착 알림. (허브 고리가 자주 부른다)"""
        reg = self.refresh()
        sent, noticed = [], []
        for q in self.queue():
            e = reg["sessions"][q["session"]]
            if q["seq"] in e["notified_asks"]:
                continue
            e["notified_asks"].append(q["seq"])
            text = f"{q['session']}: {kind_label(q)} 승인 대기"
            if self._notify_on:
                try:
                    self._notify(text)
                    sent.append(text)
                    self.event("notify", session=q["session"], seq=q["seq"])
                except Exception as ex:   # 알림이 안 돼도 승인 흐름은 막지 않는다(다시 보내지 않음)
                    self.event("notify_fail", session=q["session"], seq=q["seq"], error=type(ex).__name__)
        for name, e in reg["sessions"].items():
            sd = self.sdir(name)
            for n in seqs(sd.outbox, "md"):
                if n <= e.get("noticed_reports", 0):
                    continue
                head, body = sd.read_report(n)
                kind = head.get("report_kind") or "보고"
                note = f"[허브] {name} {kind} {n:04d} 도착 — session_read_report(name={name!r}, seq={n})"
                board = getattr(self, "board", None)
                if board is not None:   # 보고의 '사용자 할 일:' 절 → 현황판(판 53). 깨져도 보고 알림은 그대로
                    try:
                        from .board import take_from_report
                        got = take_from_report(board, name, body)
                        if got:
                            note += " · 보고의 사용자 할 일을 판에 올림: " + ", ".join(f"{t['id']} {t['title']}" for t in got)
                            self.event("board_from_report", session=name, seq=n, ids=[t["id"] for t in got])
                    except Exception as ex:
                        self.event("board_from_report_fail", session=name, seq=n, error=type(ex).__name__)
                self.notices.append(note)
                noticed.append(note)
                e["noticed_reports"] = n
                self.event("report_out", session=name, seq=n, report_kind=kind)
        self._save(reg)
        return {"notified": sent, "reports": noticed}

    def take_notices(self) -> list[str]:
        out, self.notices = self.notices, []
        return out

    @_locked
    def recover(self, timeout_s: float = 60) -> list[str]:
        """허브가 다시 떴을 때: 살아 있는 작업자는 pause 로 돌리고 새 승인 통로로 resume 한다. 죽은 것은 paused 로만 둔다."""
        reg = self.registry()
        resumed = []
        for name, e in reg["sessions"].items():
            if e.get("role") in ("main", "chat") or name in self.procs:
                continue
            if e["status"] == "paused" and e.get("resume_on_start"):   # 허브를 끌 때 허브가 멈춘 것은 이어 간다
                self._update(name, resume_on_start=False)
                self.resume(name)
                resumed.append(name)
                continue
            if e["status"] not in LIVE:
                continue
            pid = e.get("pid")
            if _alive(pid):
                sd = self.sdir(name)
                sd.set_control("pause")
                end = time.monotonic() + timeout_s
                while _alive(pid) and time.monotonic() < end:
                    time.sleep(0.05)
                if _alive(pid):
                    from . import osdep
                    osdep.kill_pid_group(pid, hard=False)
                    end = time.monotonic() + 10
                    while _alive(pid) and time.monotonic() < end:
                        time.sleep(0.05)
                self._update(name, status="paused", pid=None, run_dir=sd.status().get("run_dir") or e.get("run_dir"))
                self.event("session_pause", session=name, why="hub_restart")
                self.resume(name)
                resumed.append(name)
            else:
                self._update(name, status="paused", pid=None)
                self.event("session_pause", session=name, why="hub_restart_dead")
        return resumed

    def close(self) -> None:
        reg = self.registry()
        if "main" in reg["sessions"]:
            self._update("main", status="stopped", pid=None)
        for name in list(self.procs):
            self.stop(name, timeout_s=10)
