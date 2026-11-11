"""기억 M1: 모든 프로필에 같은 층. 쓰는 법은 모델에게, 받침은 규칙으로.

층
- L0 기록 색인: 실행이 끝나면 기록 폴더의 부모(runs)/index.jsonl 에 한 줄(대상 키·과제 첫 줄·종료 사유·바꾼 파일).
  도구 memory_search(읽기 전용) 가 그 색인으로 같은 대상의 실행을 좁혀 transcript.md·facts.md·메모에서 맞는 줄을 찾는다.
- L1 메모: <기억 폴더>/<대상>/notes/<실행 id>.jsonl — 실행마다 따로라 동시 쓰기 충돌이 없다.
  · 엔진 자동 메모(모델 판단 없음): 판정 명령 결과, 끝내기 전 확인 실패, 되돌림, 빈 잘림, 실패한 셸 명령 첫 줄 — status "confirmed", by "engine"
  · 모델 메모: 도구 note_add(claim, evidence, status) — 실행당 NOTE_MAX(10)개, evidence 필수(없으면 "미확인" 으로 쓰게)
- L3 주입 색인: <기억 폴더>/<대상>/MEMORY.md 를 지시문 끝에 넣는다(대상당 40줄·4 KB, 합 6 KB, 넘으면 자르고 표시).
  M1 에서는 사람이 쓰거나 비워 둔다. 쪽(L2)·꿈꾸기는 M2(쓰는 자는 꿈꾸기 하나).
대상(코드가 정한다): 자리 = git 저장소 뿌리(있으면) 또는 작업 폴더 realpath / 종류 = 프로필 이름.
켜기: STEWART_MEMORY=0|1 이 이긴다 > 프로필 memory = true|false > 정하지 않으면 chat·허브(실사용) 켬, run·평가 끔.
스스로 기억(사용자 전역, 판 50): 사람 말에서 다음에도 지킬 지시·선호("앞으로는 …", "… 하지 마라", "기억해 …")를 알아채
<기억 폴더>/user/remember.jsonl 에 남기고 모든 프로젝트의 지시문 맨 앞에 넣는다. 비밀 꼴은 남기지 않는다. 지우기는 forget(id).
기억 폴더: STEWART_MEMORY_DIR, 없으면 ~/.local/share/stewart/memory. 작업자(delegate)는 기억을 쓰지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

from .nostore import scrub

ENV_ON, ENV_DIR = "STEWART_MEMORY", "STEWART_MEMORY_DIR"
INJECT_LINES, INJECT_BYTES, INJECT_TOTAL = 40, 4096, 6144
NOTE_MAX = 10
STATUSES = ("confirmed", "unconfirmed", "trap")
SEARCH_MAX = 20
PAGE_MAX = 8192            # L2 쪽 하나의 상한(바이트에 가깝게 글자로)

PURPOSE = ("[기억] 아래는 같은 대상(이 폴더·저장소, 이 종류의 일)에서 앞서 알아낸 것의 색인이다. 기억은 다음에 같은 일을 할 때 "
           "다시 알아내지 않게 하려고 있다. 일을 시작하기 전에 관련 기억을 보고(대상 쪽은 memory_read, 앞 실행의 기록은 memory_search), "
           "기억의 주장은 지금 환경에서 확인한 뒤 쓴다. 확인한 사실·함정·결정은 note_add 로 남긴다(근거와 함께, 확인 못 했으면 unconfirmed).")


def enabled(profile, env=None, interactive: bool = False) -> bool:
    """켜짐: STEWART_MEMORY=0|1 이 이기고, 다음은 프로필 memory = true|false, 정하지 않았으면 실사용(chat·허브, interactive)은 켬,
    run·bench·평가는 끔 — 평가 결과에 기억이 섞이지 않게(시험이 못박는다)."""
    env = os.environ if env is None else env
    v = env.get(ENV_ON)
    if v in ("0", "1"):
        return v == "1"
    pv = getattr(profile, "memory", None)
    if pv is not None:
        return pv is True
    return bool(interactive)


def memory_dir(env=None) -> Path:
    env = os.environ if env is None else env
    return Path(os.path.expanduser(env.get(ENV_DIR) or "~/.local/share/stewart/memory"))


def _slug(s: str) -> str:
    s = re.sub(r"[^\w.-]+", "-", s, flags=re.U).strip("-")
    return (s or "x")[:40]


def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def place_of(path: Path) -> dict:
    """폴더 하나의 자리 대상: git 뿌리가 있으면 repo-…, 없으면 dir-…"""
    from .memfile import git_root
    wd = Path(path).resolve()
    root = git_root(wd)
    if root is not None:
        return {"key": f"repo-{_slug(root.name)}-{_h(str(root))}", "kind": "repo", "name": root.name, "path": str(root)}
    return {"key": f"dir-{_slug(wd.name)}-{_h(str(wd))}", "kind": "dir", "name": wd.name, "path": str(wd)}


def projects_root_cfg(env=None) -> str:
    """여러 프로젝트를 품은 작업 뿌리(예: 허브 작업 폴더): STEWART_PROJECTS_ROOT → hub.toml [memory] projects_root."""
    env = os.environ if env is None else env
    v = env.get("STEWART_PROJECTS_ROOT")
    if v:
        return os.path.realpath(os.path.expanduser(v))
    try:
        import tomllib
        from .forge import config_path
        p = config_path()
        if p.is_file():
            v = (tomllib.loads(p.read_text(encoding="utf-8")).get("memory") or {}).get("projects_root")
            if isinstance(v, str) and v:
                return os.path.realpath(os.path.expanduser(v))
    except (OSError, ValueError):
        pass
    return ""


def project_of(workdir: Path, path, env=None) -> Path | None:
    """작업 폴더가 여러 프로젝트의 뿌리일 때, path 가 든 첫 단계 하위 폴더(그 프로젝트). 아니면 None.
    뿌리로 보는 때: 작업 폴더가 git 저장소 안이 아니고, (설정 projects_root 와 같거나) 그 하위 폴더가 git 저장소."""
    from .memfile import git_root
    wd = Path(workdir).resolve()
    if git_root(wd) is not None or not path:
        return None
    p = Path(path)
    p = (p if p.is_absolute() else wd / p).resolve()
    try:
        rel = p.relative_to(wd)
    except ValueError:
        return None
    if not rel.parts or rel.parts[0].startswith("."):
        return None
    child = wd / rel.parts[0]
    if not child.is_dir():
        return None
    if projects_root_cfg(env) == str(wd) or (child / ".git").exists():
        return child
    return None


def subjects(profile) -> list[dict]:
    """[{key, kind, name, path}] — 자리 하나 + 종류 하나."""
    from .memfile import git_root
    wd = Path(profile.workdir).resolve()
    root = git_root(wd)
    out = []
    if root is not None:
        out.append({"key": f"repo-{_slug(root.name)}-{_h(str(root))}", "kind": "repo", "name": root.name, "path": str(root)})
    else:
        out.append({"key": f"dir-{_slug(wd.name)}-{_h(str(wd))}", "kind": "dir", "name": wd.name, "path": str(wd)})
    out.append({"key": f"kind-{_slug(profile.name)}", "kind": "kind", "name": profile.name, "path": ""})
    return out


USER = {"key": "user", "kind": "user", "name": "사용자(모든 프로젝트)", "path": ""}
USER_BYTES = 2048          # 지시문의 "스스로 기억함" 몫(합 INJECT_TOTAL 안) — 옛 한 갈래
POLICY_BYTES = 1536        # 세 갈래(판 83): 방침 몫
PROFILE_BYTES = 600        #                 프로필 몫(넘으면 오래된 것부터 빠지고 화면에 "줄여 달라")
REMEMBER_MAX = 200
_EXPLICIT = re.compile(r"^\s*(?:꼭\s*|이것?\s*)?기억해\s*(?:둬|줘|두어라|라|두라)?\s*[:：,.]?\s*(?P<a>.+)$|^(?P<b>.+?)\s*(?:을|를)?\s*(?:꼭\s*)?기억해\s*(?:둬|줘|두어라|라|두라)?\s*[.!]?$")
_RULE = re.compile(r"(앞으로(?:는|도)?|다음부터(?:는)?|이제부터(?:는)?|항상|언제나|절대(?:로)?|매번|늘\s)|"
                   r"(하지\s*(?:마|말)|쓰지\s*(?:마|말)|하면\s*안\s*(?:돼|된다)|금지|말고\s)")
_PREF = re.compile(r"(나는|저는|내가|제가).{0,40}(좋아|싫어|선호|편하|불편)")
_SECRETISH = re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key|비밀번호|토큰\s*[:=]|[A-Za-z0-9_\-]{32,}|-----BEGIN)")


def detect(text: str) -> list[tuple[str, str]]:
    """사람 말에서 다음에도 지킬 것을 뽑는다(모델을 부르지 않는 규칙). [(kind, 문장)] — kind = explicit·feedback·user.
    물음·짧은 말·비밀 꼴은 뺀다."""
    out = []
    t = (text or "").strip()
    if not t:
        return out
    m = _EXPLICIT.match(t.split("\n", 1)[0])
    if m:
        body = (m.group("a") or m.group("b") or "").strip()
        if 4 <= len(body) <= 300 and not _SECRETISH.search(body):
            return [("explicit", body)]
    for sent in re.split(r"(?<=[.!。])\s+|\n+", t):
        s = sent.strip().strip("-*· ").strip()
        if not (8 <= len(s) <= 220) or "?" in s or s.endswith("까") or _SECRETISH.search(s):
            continue
        if _PREF.search(s):
            out.append(("user", s))
        elif _RULE.search(s):
            out.append(("feedback", s))
    return out[:3]


def route(kind: str, sent: str) -> tuple[str | None, str]:
    """detect 가 고른 말 → (갈래, 어떻게). 어떻게: save(바로 남기고 보이며 되돌리기) · ask(사람에게 [허용]·[이번만]) · skip.
    - "기억해 …"(explicit): 바로 남긴다 — 지시 꼴이면 방침, 선호면 프로필, 그 밖은 자료.
    - 선호(user): 프로필(짧으면) 또는 자료로 바로.
    - 지시(feedback): "앞으로·항상·다음부터 …"처럼 오래 지킬 말이면 방침으로 묻고, 아니면 이번 차례 지시라 남기지 않는다."""
    from . import memstore as MS
    if kind == "explicit":
        if MS.PERSIST.search(sent) or MS.DIRECTIVE.search(sent) or _RULE.search(sent):
            return "policy", "save"
        return ("profile" if _PREF.search(sent) and len(sent) <= MS.PROFILE_MAX_CHARS else "knowledge"), "save"
    if kind == "user":
        return ("profile" if len(sent) <= MS.PROFILE_MAX_CHARS else "knowledge"), "save"
    if kind == "feedback":
        return ("policy", "ask") if MS.PERSIST.search(sent) else (None, "skip")
    return "knowledge", "save"


class Memory:
    """한 실행(또는 chat 세션)의 기억 손잡이. 도구(memory_search·note_add)와 엔진이 쓴다."""

    def __init__(self, profile, run_dir: Path, env=None):
        # 계정 모드(U2-7): 사람마다 기억 폴더가 따로(profile.memory_root) — 같은 OS 계정을 여럿이 써도 섞이지 않게
        root = getattr(profile, "memory_root", None)
        self.dir = Path(root) if root else memory_dir(env)
        self.subjects = subjects(profile)
        self.place = self.subjects[0]
        self.run_dir = Path(run_dir)
        self.runs_dir = self.run_dir.parent
        self.run_id = self.run_dir.name
        self.notes_written = 0
        self.workdir = Path(profile.workdir)
        self.target = None           # 여러 프로젝트 뿌리에서 이번 대상 프로젝트(판 54) — 없으면 place
        self._shown: set[str] = set()   # 대상 색인을 이미 대화에 넣은 프로젝트
        self._shell_seen: set[str] = set()
        from . import memstore as MS   # 판 83: 방침·프로필·자료 세 갈래(마크다운이 정본) — 옛 remember.jsonl 은 처음 한 번 옮긴다
        try:
            self.migrated = MS.auto_migrate(self.dir / USER["key"])
        except OSError:
            self.migrated = None

    @property
    def store(self):
        from . import memstore as MS
        return MS.Store(self.dir / USER["key"])

    def md(self) -> bool:
        """세 갈래(마크다운) 쓰기인가: 되돌리지 않았고(옮겼거나 옛 파일이 없음)."""
        if self.store.marker().get("reverted"):
            return False
        return self.store.active() or not (self.dir / USER["key"] / "remember.jsonl").is_file()

    # ---- 대상 가르기(판 54): stewart-work 같은 뿌리 아래 폴더별 프로젝트 기억 ----
    def here(self) -> dict:
        return self.target or self.place

    def retarget(self, paths: list) -> dict | None:
        """이번 차례에 만진 경로들에서 대상 프로젝트를 고른다(가장 많이 만진 첫 단계 하위 폴더). 바뀌면 새 대상."""
        cnt: dict[str, int] = {}
        for x in paths or []:
            c = project_of(self.workdir, x)
            if c is not None:
                cnt[str(c)] = cnt.get(str(c), 0) + 1
        if not cnt:
            return None
        best = max(cnt, key=lambda k: cnt[k])
        t = place_of(Path(best))
        if self.target is None or self.target["key"] != t["key"]:
            self.target = t
            return t
        return None

    def target_note(self) -> str:
        """대상 프로젝트의 색인(MEMORY.md)을 대화에 한 번 넣을 글. 없거나 이미 넣었으면 빈 글."""
        t = self.target
        if t is None or t["key"] in self._shown:
            return ""
        self._shown.add(t["key"])
        f = self.dir / t["key"] / "MEMORY.md"
        body = ""
        if f.is_file():
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()[:INJECT_LINES]
            body = "\n".join(lines)[:INJECT_BYTES]
        head = f"[기억] 이번 대상 프로젝트: {t['name']} ({t['path']}) — 메모·검색은 이 대상으로 간다."
        rem = [x for x in self.remembered() if x.get("scope") == "project" and x.get("place") == t["key"]
               and x.get("kind") != "knowledge"][-10:]
        if rem:
            body = "이 프로젝트에서 스스로 기억함:\n" + "\n".join(f"- {x['text']}" for x in rem) + ("\n" + body if body else "")
        return head + ("\n" + body if body else "\n(이 프로젝트의 색인은 아직 없다.)")

    def turn_notes(self, shells: list[dict]) -> int:
        """차례 끝 엔진 자동 메모(실사용에서 쌓이게): 실패한 셸(세션에서 같은 명령은 한 번). 남긴 수."""
        n = 0
        for c in shells or []:
            cmd = str(c.get("command") or "")[:160]
            if not cmd or c.get("exit") in (None, 0) or cmd in self._shell_seen or n >= 5:
                continue
            self._shell_seen.add(cmd)
            self._append(self.here(), {"by": "engine", "status": "confirmed", "claim": f"셸 `{cmd}` → exit {c['exit']}",
                                       "evidence": f"run {self.run_id} 차례 셸"})
            n += 1
        return n

    # ---- 스스로 기억(사용자 전역) ----
    def _rem_file(self) -> Path:
        d = self.dir / USER["key"]
        d.mkdir(parents=True, exist_ok=True)
        return d / "remember.jsonl"

    def remembered(self) -> list[dict]:
        if self.md():
            return self.store.items()[-REMEMBER_MAX:]
        f = self.dir / USER["key"] / "remember.jsonl"
        if not f.is_file():
            return []
        items, gone = {}, set()
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                x = json.loads(line)
            except ValueError:
                continue
            if x.get("forget"):
                gone.add(x["forget"])
            elif x.get("id"):
                items[x["id"]] = x
        return [x for k, x in items.items() if k not in gone][-REMEMBER_MAX:]

    def _deny_file(self) -> Path:
        return self.dir / USER["key"] / "deny.jsonl"

    def denied(self) -> list[str]:
        f = self._deny_file()
        if not f.is_file():
            return []
        out = []
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line)["text"])
            except (ValueError, KeyError):
                continue
        return out

    def _is_denied(self, text: str) -> bool:
        t = text.lower()
        return any(d.lower() in t or t in d.lower() for d in self.denied() if d)

    def remember(self, kind: str, text: str, source: str = "said", scope: str = "user") -> dict | None:
        """남긴 것(dict) 또는 None(비었음·비밀 꼴·이미 있음·"기억하지 마" 목록에 걸림).
        scope: user(모든 프로젝트) | project(이 대상 — git 뿌리 또는 작업 폴더)."""
        text = " ".join(str(text or "").split())[:300]
        if not text or _SECRETISH.search(text) or self._is_denied(text):
            return None
        if self.md():   # 세 갈래: 옛 종류 이름도 받는다(feedback·explicit → 방침/자료, user → 프로필/자료, project·reference → 자료)
            from . import memstore as MS
            k = MS.kind_of_legacy(kind, text)
            if k == "profile" and len(text) > MS.PROFILE_MAX_CHARS:
                k = "knowledge"
            place = self.here()["key"] if scope == "project" else None
            old = self.store.same(k, text, scope, place)
            if old is not None:   # 거의 같은 것이 있으면 새로 쌓지 않고 고친다(Mem0 의 UPDATE)
                return None if old["text"] == text else self.update(old["id"], text, source=source)
            x = scrub({"id": _h(text + datetime.now().isoformat()), "ts": datetime.now().isoformat(timespec="seconds"),
                       "kind": k, "text": text, "run": self.run_id, "source": source, "scope": scope, "place": place,
                       "where": self.here()["name"] if scope == "project" else None})
            return self.store.add(x)
        if any(x["text"] == text for x in self.remembered()):
            return None
        x = scrub({"id": _h(text + datetime.now().isoformat()), "ts": datetime.now().isoformat(timespec="seconds"),
                   "kind": kind, "text": text, "run": self.run_id, "source": source, "scope": scope,
                   "place": self.here()["key"] if scope == "project" else None, "where": self.here()["name"] if scope == "project" else None})
        with open(self._rem_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
        return x

    def update(self, item_id: str, text: str, source: str = "human", kind: str | None = None) -> dict | None:
        """같은 기억을 새 말로 덮는다(같은 id 로 한 줄 더 — 뒤의 것이 이긴다). kind 를 주면 갈래를 바꾼다(세 갈래)."""
        text = " ".join(str(text or "").split())[:300]
        old = next((x for x in self.remembered() if x["id"] == item_id), None)
        if old is None or not text or _SECRETISH.search(text):
            return None
        if self.md():
            from . import memstore as MS
            new = {"text": text, "ts": datetime.now().isoformat(timespec="seconds"), "source": source, "run": self.run_id,
                   **({"kind": kind} if kind in MS.KINDS else {})}
            return {**old, **new} if self.store.replace(item_id, scrub(new)) is not None else None
        x = {**old, "text": text, "ts": datetime.now().isoformat(timespec="seconds"), "source": source, "run": self.run_id}
        with open(self._rem_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(scrub(x), ensure_ascii=False) + "\n")
        return x

    def forget(self, item_id: str) -> bool:
        if not any(x["id"] == item_id for x in self.remembered()):
            return False
        if self.md():
            return self.store.replace(item_id, None) is not None
        with open(self._rem_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps({"forget": item_id, "ts": datetime.now().isoformat(timespec="seconds")}) + "\n")
        return True

    def deny(self, item_id: str | None = None, text: str | None = None) -> bool:
        """"이건 기억하지 마": 그 기억을 지우고, 같은 글은 앞으로 알아채도 남기지 않는다."""
        if item_id:
            x = next((x for x in self.remembered() if x["id"] == item_id), None)
            if x is None:
                return False
            text = x["text"]
            self.forget(item_id)
        text = " ".join(str(text or "").split())[:300]
        if not text:
            return False
        self._deny_file().parent.mkdir(parents=True, exist_ok=True)
        with open(self._deny_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps({"text": text, "ts": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False) + "\n")
        return True

    # ---- 묻는 기억(판 83): "앞으로 …" 같은 오래 지킬 지시는 바로 남기지 않고 사람에게 [허용]·[이번만] ----
    def _pend_file(self) -> Path:
        return self.dir / USER["key"] / "pending.jsonl"

    def pending(self) -> list[dict]:
        f = self._pend_file()
        if not f.is_file():
            return []
        out = []
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out[-20:]

    def propose(self, kind: str, text: str, scope: str = "user", source: str = "said") -> dict | None:
        """남길지 사람에게 물을 기억. 이미 있거나·비밀 꼴·기억하지 말라 한 것이면 None."""
        text = " ".join(str(text or "").split())[:300]
        if not text or _SECRETISH.search(text) or self._is_denied(text):
            return None
        if any(x["text"] == text for x in self.remembered()) or any(x["text"] == text for x in self.pending()):
            return None
        x = scrub({"id": "p" + _h(text + datetime.now().isoformat()), "ts": datetime.now().isoformat(timespec="seconds"),
                   "kind": kind, "text": text, "scope": scope, "source": source, "run": self.run_id})
        f = self._pend_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(x, ensure_ascii=False) + "\n")
        return x

    def confirm(self, pend_id: str, keep: bool) -> dict | None:
        """[허용] 이면 남기고 그 기억을, [이번만] 이면 버리고 {}. 없는 id 면 None."""
        items = self.pending()
        x = next((p for p in items if p.get("id") == pend_id), None)
        if x is None:
            return None
        rest = [p for p in items if p.get("id") != pend_id]
        self._pend_file().write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in rest), encoding="utf-8")
        if not keep:
            return {}
        return self.remember(x["kind"], x["text"], source=x.get("source") or "said", scope=x.get("scope") or "user") or {}

    def _user_part(self) -> tuple[str, dict]:
        if self.md():
            return self._user_part3()
        # 사용자 전역 + 이 대상(git 뿌리 또는 작업 폴더)의 프로젝트 기억
        items = [x for x in self.remembered() if x.get("scope", "user") == "user" or x.get("place") == self.place["key"]]
        label = {"explicit": "기억해 달라 함", "feedback": "지적·지시", "user": "사용자", "project": "이 프로젝트", "reference": "참고"}
        lines, size = [], 0
        for x in reversed(items):   # 새것부터 상한까지
            ln = f"- [{label.get(x['kind'], x['kind'])} {x['ts'][5:10]}] {x['text']}"
            b = len((ln + "\n").encode("utf-8"))
            if size + b > USER_BYTES:
                break
            lines.append(ln)
            size += b
        log = {"subject": USER["key"], "lines": len(lines), "bytes": size, "of_lines": len(items), "truncated": len(lines) < len(items)}
        if not lines:
            return "", log
        return ("## 사용자(스스로 기억함 — 새것부터. 손으로 쓴 규칙 파일(STEWART.md·AGENTS.md)과 부딪히면 규칙 파일이 이긴다)\n"
                + "\n".join(lines), log)

    def _user_part3(self) -> tuple[str, dict]:
        """세 갈래 주입: 방침(이 범위, POLICY_BYTES)·프로필(PROFILE_BYTES — 넘으면 오래된 것부터 빼고 표시)은 싣고, 자료는 건수 한 줄."""
        items = [x for x in self.remembered() if x.get("scope", "user") == "user" or x.get("place") == self.place["key"]]

        def take(kind, cap):
            lines, size, n = [], 0, [x for x in items if x["kind"] == kind]
            for x in reversed(n):
                ln = f"- {x['text']}"
                b = len((ln + "\n").encode("utf-8"))
                if size + b > cap:
                    break
                lines.append(ln)
                size += b
            return lines, size, len(n)
        pol, ps, pn = take("policy", POLICY_BYTES)
        pro, rs, rn = take("profile", PROFILE_BYTES)
        kn = sum(1 for x in self.remembered() if x["kind"] == "knowledge")
        log = {"subject": USER["key"], "lines": len(pol) + len(pro), "bytes": ps + rs, "of_lines": pn + rn,
               "truncated": len(pol) < pn or len(pro) < rn, "policy": [len(pol), pn], "profile": [len(pro), rn], "knowledge": kn,
               "profile_over": len(pro) < rn}
        if not (pol or pro or kn):
            return "", log
        out = ["## 사용자(스스로 기억함 — 새것부터. 손으로 쓴 규칙 파일(STEWART.md·AGENTS.md)과 부딪히면 규칙 파일이 이긴다)"]
        if pol:
            out.append("### 방침(어떻게 일할지)\n" + "\n".join(pol))
        if pro:
            out.append("### 사용자에 대해\n" + "\n".join(pro))
        if kn:
            out.append(f"자료 기억 {kn}건(고쳐 준 사실·참고) — 지시문에는 싣지 않았다. 관련 있을 때 doc_search(query, source=\"memory\") 로 찾는다.")
        return "\n".join(out), log

    # ---- L3 주입 ----
    def inject(self) -> tuple[str, list[dict]]:
        parts, log, total = [], [], 0
        up, ulog = self._user_part()
        if up:
            parts.append(up)
            total += ulog["bytes"]
            log.append(ulog)
        for s in self.subjects:
            f = self.dir / s["key"] / "MEMORY.md"
            if not f.is_file():
                continue
            raw = f.read_text(encoding="utf-8", errors="replace")
            lines = raw.splitlines()
            keep, size, cut = [], 0, False
            for ln in lines:
                b = len((ln + "\n").encode("utf-8"))
                if len(keep) >= INJECT_LINES or size + b > INJECT_BYTES or total + size + b > INJECT_TOTAL:
                    cut = True
                    break
                keep.append(ln)
                size += b
            total += size
            log.append({"subject": s["key"], "lines": len(keep), "bytes": size, "of_lines": len(lines), "truncated": cut})
            if keep:
                head = f"## {s['kind']}: {s['name']}" + (f" ({s['path']})" if s["path"] else "")
                parts.append(head + "\n" + "\n".join(keep) + ("\n…(색인이 상한에서 잘림 — 나머지는 memory_search)" if cut else ""))
        text = PURPOSE + ("\n\n" + "\n\n".join(parts) if parts else "\n\n(아직 색인이 없다.)")
        return text, log

    # ---- L1 메모 ----
    def _note_file(self, subject: dict) -> Path:
        d = self.dir / subject["key"] / "notes"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{self.run_id}.jsonl"

    def _append(self, subject: dict, rec: dict) -> None:
        rec = scrub({"ts": datetime.now().isoformat(timespec="seconds"), "run": self.run_id, "subject": subject["key"], **rec})
        with open(self._note_file(subject), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def note_add(self, claim: str, evidence: str, status: str, scope: str = "place") -> tuple[bool, str]:
        claim, evidence = (claim or "").strip(), (evidence or "").strip()
        if self.notes_written >= NOTE_MAX:
            return False, f"이 실행의 메모 상한({NOTE_MAX}개)에 닿았다. 가장 중요한 것만 남겼는지 보고 일로 돌아가라."
        if not claim or len(claim) > 500:
            return False, "claim 은 1~500자 한 사실(문장 하나 또는 두셋)"
        if not evidence:
            return False, "evidence 가 비었다 — 이번 실행에서 확인한 근거(도구 호출·파일·명령 결과)를 적거나, 확인 못 했으면 '미확인' 이라 쓰고 status 를 unconfirmed 로"
        if status not in STATUSES:
            return False, f"status 는 {'|'.join(STATUSES)}"
        if scope not in ("place", "kind"):
            return False, "scope 는 place(이 폴더·저장소) 또는 kind(이 종류의 일)"
        subj = self.here() if scope == "place" else self.subjects[1]
        self._append(subj, {"by": "model", "claim": claim, "evidence": evidence[:500], "status": status})
        self.notes_written += 1
        return True, f"메모 남김({subj['key']}, {status}, 이 실행 {self.notes_written}/{NOTE_MAX})"

    def auto_notes(self, summary: dict, facts: dict | None) -> int:
        """엔진 자동 메모(모델 판단 없음). 남긴 수."""
        n = 0
        v = summary.get("verify")
        if isinstance(v, dict) and v.get("command"):
            self._append(self.here(), {"by": "engine", "status": "confirmed",
                                      "claim": f"판정 명령 `{v['command']}` → exit {v.get('exit')} ({'통과' if v.get('passed') else '실패'})",
                                      "evidence": f"run {self.run_id} 끝 판정"})
            n += 1
        cs = summary.get("check_on_stop") or {}
        if cs.get("fails"):
            self._append(self.here(), {"by": "engine", "status": "confirmed",
                                      "claim": f"끝내기 전 확인 `{cs.get('command')}` 이 {cs['fails']}번 실패했다",
                                      "evidence": f"run {self.run_id} check_on_stop"})
            n += 1
        rb = summary.get("rolled_back")
        if isinstance(rb, dict) and not rb.get("error"):
            self._append(self.here(), {"by": "engine", "status": "confirmed",
                                      "claim": f"끝내지 못해({summary.get('stop_reason')}) 바꾼 파일을 되돌렸다",
                                      "evidence": f"run {self.run_id} rollback"})
            n += 1
        if summary.get("stop_reason") == "length_empty":
            self._append(self.subjects[1], {"by": "engine", "status": "confirmed",
                                            "claim": "생각만 하다 생성 상한에 닿아 답하지 못하고 끝났다(length_empty)",
                                            "evidence": f"run {self.run_id}"})
            n += 1
        for c in ((facts or {}).get("shell") or [])[:50]:
            if isinstance(c, dict) and isinstance(c.get("exit"), int) and c["exit"] != 0 and n < 12:
                self._append(self.here(), {"by": "engine", "status": "confirmed",
                                          "claim": f"셸 `{str(c.get('command', ''))[:160]}` → exit {c.get('exit')}",
                                          "evidence": f"run {self.run_id} 셸 기록"})
                n += 1
        return n

    # ---- L0 색인 ----
    def index_run(self, task: str, summary: dict, files: list[str]) -> None:
        rec = scrub({"run": self.run_id, "ts": datetime.now().isoformat(timespec="seconds"),
                     "subjects": [s["key"] for s in self.subjects] + ([self.target["key"]] if self.target else []), "profile": summary.get("profile"),
                     "task": (task or "").strip().splitlines()[0][:200] if (task or "").strip() else "",
                     "stop_reason": summary.get("stop_reason"), "files": files[:50]})
        with open(self.runs_dir / "index.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def read_page(self, subject: str = "") -> tuple[bool, str]:
        """L2 쪽 읽기(쓰는 것은 stewart dream 하나). 대상 키를 안 주면 이 폴더·저장소 쪽."""
        want = subject or self.here()["key"]
        if not re.fullmatch(r"[\w.-]{1,80}", want):
            return False, f"subject 꼴이 아니다: {want}"
        f = self.dir / want / "page.md"
        if not f.is_file():
            known = [s["key"] for s in self.subjects if (self.dir / s["key"] / "page.md").is_file()]
            return False, f"{want} 의 쪽이 아직 없다(stewart dream 이 만든다). 있는 쪽: {', '.join(known) or '없음'}"
        return True, f.read_text(encoding="utf-8", errors="replace")[:PAGE_MAX]

    def search(self, query: str, subject: str = "", limit: int = SEARCH_MAX) -> tuple[bool, str]:
        words = [w.lower() for w in (query or "").split() if w.strip()]
        if not words:
            return False, "query 가 비었다"
        want = subject or self.here()["key"]
        if want not in [s["key"] for s in self.subjects] and not re.fullmatch(r"[\w.-]{1,80}", want):
            return False, f"subject 꼴이 아니다: {want}"
        limit = max(1, min(int(limit or SEARCH_MAX), SEARCH_MAX))
        runs = []
        idx = self.runs_dir / "index.jsonl"
        if idx.is_file():
            for ln in idx.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if want in (r.get("subjects") or []) and r.get("run") != self.run_id:
                    runs.append(r)
        hits = []

        def scan(label: str, path: Path):
            if not path.is_file():
                return
            for i, ln in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                low = ln.lower()
                if all(w in low for w in words):
                    hits.append(f"{label}:{i}: {ln.strip()[:240]}")
                    if len(hits) >= limit:
                        return

        for r in reversed(runs):                    # 최근 실행부터
            d = self.runs_dir / r["run"]
            for name in ("facts.md", "transcript.md"):
                if len(hits) < limit:
                    scan(f"{r['run']}/{name}", d / name)
        nd = self.dir / want / "notes"
        if nd.is_dir():
            for f in sorted(nd.glob("*.jsonl"), reverse=True):
                if len(hits) < limit:
                    scan(f"메모 {f.stem}", f)
        head = f"memory_search '{query}' — 대상 {want}, 앞 실행 {len(runs)}개"
        return True, head + "\n" + ("\n".join(hits) if hits else "(맞는 줄 없음)")


def tool_defs() -> list[dict]:
    return [
        {"type": "function", "function": {"name": "memory_search", "description": (
            "같은 대상(이 폴더·저장소)의 앞 실행 기록(대화록·한 일)과 메모에서 낱말이 모두 든 줄을 찾는다. 읽기만 한다."),
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "찾을 낱말들(빈칸으로 나눔, 모두 든 줄)"},
                "subject": {"type": "string", "description": "대상 키(없으면 이 폴더·저장소)"},
                "limit": {"type": "integer", "description": f"최대 줄 수(기본·상한 {SEARCH_MAX})"}},
                "required": ["query"]}}},
        {"type": "function", "function": {"name": "memory_read", "description": (
            "대상(이 폴더·저장소, 또는 이 종류의 일)의 기억 쪽을 읽는다. 확인된 사실·함정·결정이 원문 인용과 함께 있다. 읽기만 한다."),
            "parameters": {"type": "object", "properties": {
                "subject": {"type": "string", "description": "대상 키(없으면 이 폴더·저장소)"}}, "required": []}}},
        {"type": "function", "function": {"name": "note_add", "description": (
            f"다음에 같은 일을 할 때 다시 알아내지 않아도 되게 사실·함정·결정 하나를 기억에 남긴다(실행당 {NOTE_MAX}개). "
            "화면에서 바로 읽을 수 있는 것(지금 파일 내용 등)은 남기지 않는다."),
            "parameters": {"type": "object", "properties": {
                "claim": {"type": "string", "description": "남길 사실 하나(짧게)"},
                "evidence": {"type": "string", "description": "이번 실행에서 확인한 근거(도구·명령·파일), 없으면 '미확인'"},
                "status": {"type": "string", "enum": list(STATUSES), "description": "confirmed(확인함)·unconfirmed(미확인)·trap(함정)"},
                "scope": {"type": "string", "enum": ["place", "kind"], "description": "place = 이 폴더·저장소(기본), kind = 이 종류의 일"}},
                "required": ["claim", "evidence", "status"]}}},
    ]


NAMES = ("memory_search", "note_add", "memory_read")


def execute(mem: Memory, name: str, args: dict) -> tuple[bool, str]:
    from .tools import _outside
    if name == "memory_search":
        ok, out = mem.search(str(args.get("query", "")), str(args.get("subject") or ""), args.get("limit") or SEARCH_MAX)
        return ok, _outside("앞 실행 기록·메모", out) if ok else out
    if name == "memory_read":
        ok, out = mem.read_page(str(args.get("subject") or ""))
        return ok, _outside("기억 쪽", out) if ok else out
    if name == "note_add":
        return mem.note_add(str(args.get("claim", "")), str(args.get("evidence", "")), str(args.get("status", "")),
                            str(args.get("scope") or "place"))
    return False, f"없는 기억 도구: {name}"


# ── 모델로 알아채기(판 52): 차례 끝 도구 없는 짧은 한 번 ──
EXTRACT_PROMPT = """[기억 고르기] 아래는 방금 끝난 차례의 사람 말과 그 차례의 마지막 답이다. 다음 세션에도 지켜야 할 것만 고른다.
고를 것: ① 사용자(누구인지·선호·일하는 방식) ② 지적·지시(사람이 고쳐 준 것, "그렇게 하지 마라", "앞으로는 …" — 까닭이 있으면 함께)
③ 이 프로젝트(진행 중인 일·결정·함정) ④ 참고(자리·이름 — 주소·토큰·비밀번호·비밀 파일 내용은 절대 넣지 않는다).
고르지 않을 것: 이번 차례에만 해당하는 일, 이미 기억한 것과 같은 것, 추측.
이미 기억한 것(id: 글):
{known}
기억하지 말라 한 것:
{deny}
답은 JSON 줄만(설명 없이, 없으면 아무것도 쓰지 않는다). 한 줄에 하나:
{{"op":"add","kind":"user|feedback|project|reference","text":"한 문장(까닭이 있으면 '— 까닭' 으로)"}}
{{"op":"update","id":"<이미 기억한 id>","text":"고친 한 문장"}}   (사람 말이 앞 기억을 바꾸거나 더 정확히 했을 때)
{{"op":"drop","id":"<이미 기억한 id>"}}   (사람 말로 더는 맞지 않게 됐을 때)
사람 말:
{said}
마지막 답(앞부분):
{answer}"""
EXTRACT_MAX_TOKENS = 400


def extract_ops(text: str) -> list[dict]:
    """모델 답에서 op 줄만 거른다(모르는 꼴·비밀 꼴은 버림)."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip().strip("`").strip()
        if not line.startswith("{"):
            continue
        try:
            x = json.loads(line)
        except ValueError:
            continue
        op = x.get("op")
        if op == "add" and x.get("kind") in ("user", "feedback", "project", "reference", "policy", "profile", "knowledge") \
                and isinstance(x.get("text"), str):
            out.append({"op": "add", "kind": x["kind"], "text": x["text"], **({"scope": x["scope"]} if x.get("scope") in ("user", "project") else {})})
        elif op == "update" and isinstance(x.get("id"), str) and isinstance(x.get("text"), str):
            out.append({"op": "update", "id": x["id"], "text": x["text"]})
        elif op == "drop" and isinstance(x.get("id"), str):
            out.append({"op": "drop", "id": x["id"]})
    return [o for o in out if not _SECRETISH.search(o.get("text", ""))][:6]


EXTRACT_PROMPT3 = """[기억 고르기] 아래는 방금 끝난 차례의 사람 말과 그 차례의 마지막 답이다. 다음 세션에도 쓸 것만 고른다.
갈래(kind)는 셋 중 하나를 꼭 고른다:
- policy: 사람이 "앞으로·항상·다음부터 …"처럼 오래 지키라고 한 일하는 방식. 이번 차례에만 한 지시("이번엔 …", "이 파일은 …")는 고르지 않는다.
- profile: 사용자에 대한 짧은 사실(언어·역할·기기·선호) — 120자 안 한 문장.
- knowledge: 모델이 몰랐거나 틀려서 사람이 고쳐 준 사실·참고(이 프로젝트의 결정·함정·이름·자리 포함). 애매하면 knowledge.
고르지 않을 것: 이번 차례에만 해당하는 일, 이미 기억한 것과 같은 것, 추측, 주소·토큰·비밀번호·비밀 파일 내용.
이미 기억한 것(id: 갈래: 글):
{known}
기억하지 말라 한 것:
{deny}
답은 JSON 줄만(설명 없이, 없으면 아무것도 쓰지 않는다). 한 줄에 하나:
{{"op":"add","kind":"policy|profile|knowledge","scope":"user|project","text":"한 문장(까닭이 있으면 '— 까닭' 으로)"}}
{{"op":"update","id":"<이미 기억한 id>","text":"고친 한 문장"}}   (사람 말이 앞 기억을 바꾸거나 더 정확히 했을 때)
{{"op":"drop","id":"<이미 기억한 id>"}}   (사람 말로 더는 맞지 않게 됐을 때)
사람 말:
{said}
마지막 답(앞부분):
{answer}"""


def extract_prompt(mem: "Memory", said: list[str], answer: str) -> str:
    deny = "\n".join(mem.denied()[-20:]) or "(없음)"
    said_t = "\n".join(f"- {t[:800]}" for t in said[-6:])
    if mem.md():
        known = "\n".join(f"{x['id']}: {x['kind']}: {x['text']}" for x in mem.remembered()[-40:]) or "(없음)"
        return EXTRACT_PROMPT3.format(known=known, deny=deny, said=said_t, answer=(answer or "")[:1500])
    known = "\n".join(f"{x['id']}: {x['text']}" for x in mem.remembered()[-40:]) or "(없음)"
    return EXTRACT_PROMPT.format(known=known, deny=deny, said=said_t, answer=(answer or "")[:1500])


def apply_ops(mem: "Memory", ops: list[dict], said: list[str] | None = None) -> list[dict]:
    """op 를 적용하고 한 일을 돌려준다: [{op, item}]. 세 갈래(판 83): 모델이 고른 방침은 사람 말에 "앞으로 …" 꼴이 있을 때만
    사람에게 묻고(op "ask"), 그 밖은 자료로 내린다 — 모델이 이번 차례 지시를 제멋대로 방침으로 남기지 않게."""
    from . import memstore as MS
    done = []
    said_t = "\n".join(said or [])
    for o in ops:
        if o["op"] == "add" and mem.md():
            k = MS.kind_of_legacy(o["kind"], o["text"]) if o["kind"] not in MS.KINDS else o["kind"]
            scope = o.get("scope") or ("project" if o["kind"] in ("project", "reference") else "user")
            if k == "policy":
                if said is not None and not MS.PERSIST.search(said_t):
                    k = "knowledge"
                else:
                    it = mem.propose("policy", o["text"], scope=scope, source="model")
                    if it is not None:
                        done.append({"op": "ask", "item": it})
                    continue
            it = mem.remember(k, o["text"], source="model", scope=scope)
            if it is not None:
                done.append({"op": "add", "item": it})
            continue
        if o["op"] == "add":
            scope = "project" if o["kind"] in ("project", "reference") else "user"
            it = mem.remember(o["kind"], o["text"], source="model", scope=scope)
            if it is not None:
                done.append({"op": "add", "item": it})
        elif o["op"] == "update":
            it = mem.update(o["id"], o["text"], source="model")
            if it is not None:
                done.append({"op": "update", "item": it})
        elif o["op"] == "drop":
            it = next((x for x in mem.remembered() if x["id"] == o["id"]), None)
            if it is not None and mem.forget(o["id"]):
                done.append({"op": "drop", "item": it})
    return done
