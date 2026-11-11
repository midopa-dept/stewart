"""backends.toml·프로필 파일 읽기."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BACKENDS = REPO_ROOT / "backends.toml"   # 개발 체크아웃의 로컬 파일(저장소에는 backends.example.toml 만)


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.path.expanduser(os.environ.get(var) or fallback))


# 실행 기록 기본 위치: $XDG_STATE_HOME/stewart/runs(없으면 ~/.local/state/stewart/runs). STEWART_RUNS_DIR·--runs-dir 이 이긴다
DEFAULT_RUNS_DIR = _xdg("XDG_STATE_HOME", "~/.local/state") / "stewart" / "runs"
PROFILES_DIR = REPO_ROOT / "profiles"


def user_profiles_dir() -> Path:
    """사용자 프로필 폴더: STEWART_PROFILES_DIR, 없으면 ~/.config/stewart/profiles. 없어도 된다."""
    return Path(os.path.expanduser(os.environ.get("STEWART_PROFILES_DIR") or "~/.config/stewart/profiles"))


class ConfigError(Exception):
    pass


@dataclass
class Backend:
    name: str
    base_url: str
    model: str
    precision: str
    max_context: int
    enabled: bool = True
    api_key_env: str | None = None
    timeout_s: float = 600
    params: dict = field(default_factory=dict)
    description: str = ""
    model_file: str = ""
    concurrency: int = 1        # 이 백엔드에 한 프로세스가 동시에 보내는 요청 수 상한(mac 1, cloud·box N)
    api_key_file: str | None = None   # 환경변수가 비었을 때 읽을 키 파일 경로(값은 기록·출력하지 않는다)
    configured_context: int | None = None   # backends.toml 의 max_context(서버 값으로 바꾸기 전). None 이면 아직 서버에 묻지 않음
    context_source: str = "backends"        # max_context 를 어디서 얻었나: backends | server(/v1/models max_model_len)
    served_max_len: int | None = None       # 서버가 알려 준 max_model_len(없거나 못 물었으면 None)
    dropped_params: list = field(default_factory=list)   # 서버가 모른다고 거절해 빼고 보내는 샘플링 칸(think.py)
    vision: bool = False        # 이미지 입력을 받는 모델인가(images.py). 기본 끔 — 꺼진 백엔드에는 이미지를 붙이지 않는다

    def api_key(self) -> str | None:
        if self.api_key_env and os.environ.get(self.api_key_env):
            return os.environ[self.api_key_env]
        if self.api_key_file:
            try:
                return Path(os.path.expanduser(self.api_key_file)).read_text(encoding="utf-8").strip() or None
            except OSError:
                return None
        return None

    def public(self) -> dict:
        """기록용(키 값은 넣지 않는다)."""
        return {
            "name": self.name,
            "base_url": self.base_url,
            "model": self.model,
            "model_file": self.model_file,
            "precision": self.precision,
            "max_context": self.max_context,
            "params": self.params,
            "api_key_env": self.api_key_env,
            "api_key_file": self.api_key_file,
            "concurrency": self.concurrency,
            "configured_context": self.configured_context,
            "context_source": self.context_source,
        }


def backends_path() -> Path:
    """찾는 차례: STEWART_BACKENDS → $XDG_CONFIG_HOME/stewart/backends.toml(~/.config/…) → 저장소 뿌리 backends.toml."""
    env = os.environ.get("STEWART_BACKENDS")
    if env:
        return Path(os.path.expanduser(env))
    user = _xdg("XDG_CONFIG_HOME", "~/.config") / "stewart" / "backends.toml"
    if user.is_file():
        return user
    if DEFAULT_BACKENDS.is_file():
        return DEFAULT_BACKENDS
    raise ConfigError(f"백엔드 설정이 없다: {REPO_ROOT / 'backends.example.toml'} 을 {user} 로 복사해 고치거나 "
                      "STEWART_BACKENDS=<파일> 을 준다")


def load_backends(path: Path | None = None) -> tuple[dict[str, Backend], str]:
    path = Path(path) if path else backends_path()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out: dict[str, Backend] = {}
    for name, b in (data.get("backends") or {}).items():
        try:
            out[name] = Backend(
                name=name,
                base_url=b["base_url"].rstrip("/"),
                model=b["model"],
                precision=b.get("precision", "?"),
                max_context=int(b.get("max_context", 8192)),
                enabled=bool(b.get("enabled", True)),
                api_key_env=b.get("api_key_env"),
                timeout_s=float(b.get("timeout_s", 600)),
                params=dict(b.get("params") or {}),
                description=b.get("description", ""),
                model_file=b.get("model_file", ""),
                concurrency=max(1, int(b.get("concurrency", 1))),
                api_key_file=b.get("api_key_file"),
                vision=bool(b.get("vision", False)),
            )
        except KeyError as e:
            raise ConfigError(f"{path}: backends.{name}에 {e} 가 없다") from None
    default = data.get("default") or next(iter(out), "")
    if default not in out:
        raise ConfigError(f"{path}: 기본 백엔드 '{default}' 정의가 없다")
    return out, default


@dataclass
class Profile:
    name: str
    path: Path
    system: str
    tools: list[str]
    workdir: Path
    max_steps: int = 30
    max_consecutive_errors: int = 4
    shell_timeout_s: float = 60
    backend: str | None = None
    output_format: str = "free"  # free | json
    output_schema: dict | None = None
    action_check: str | None = None
    verify_command: str | None = None
    check_on_stop: str | bool | None = None   # 최종 답 직전 확인 명령(hooks.py). true = verify_command, 글 = 그 명령
    check_on_stop_max: int = 2                # 실패를 모델에 돌려주는 최대 횟수
    check_on_stop_skip_exit: list[int] = field(default_factory=lambda: [5])   # '확인할 것 없음' 종료 코드(unittest 시험 없음=5)
    hooks: dict = field(default_factory=dict)   # [hooks] SessionStart·PreToolUse·PostToolUse·Stop(hooks.py, 읽을 때 꼴 검사)
    memory: bool | None = None                  # 기억(memory.py). None = chat·허브 켬·run(평가) 끔, true·false = 그대로. STEWART_MEMORY 가 이긴다
    memory_root: str | None = None          # 기억 폴더를 이 프로필만 따로(계정 모드: 사람마다). 프로필 파일에서는 읽지 않는다
    think_max_tokens: int | None = None         # 생각 켬 차례의 단계당 생성 상한(think.py). STEWART_THINK_MAX_TOKENS 가 이긴다
    think_sampling: str | None = None           # 생각 켬 차례의 샘플링 묶음 이름(think.PRESETS). STEWART_THINK_SAMPLING 가 이긴다
    extra_write_dirs: list[str] = field(default_factory=list)   # 작업 폴더 밖에 미리 열어 둘 쓰기 폴더(bounds.py)
    read_outside: str = "allow"                 # 작업 폴더 밖 읽기: allow|ask|deny(ask 는 run 에서 거부)
    review_on_stop: bool | None = None          # 끝내기 전 요구 대조(selfcheck.py). STEWART_REVIEW_ON_STOP 가 이긴다
    ask_check: bool | None = None               # 첫 쓰기 전 되묻기 판단 받침. STEWART_ASK_CHECK 가 이긴다
    baseline_diff: bool | None = None           # 시작 때 확인 명령 기준선으로 회귀를 가름. STEWART_BASELINE_DIFF 가 이긴다
    todo_check: bool | None = None              # 남은 할 일 확인·상기. STEWART_TODO_CHECK 가 이긴다
    todo_remind_every: int = 8                  # todo_write 없이 이 단계 수가 지나면 남은 할 일을 상기(todo_check 켬일 때)
    description: str = ""
    language: str | None = None          # "ko" 이면 최종 보고 한국어 강제
    language_fields: list[str] = field(default_factory=list)  # json 출력에서 한국어여야 하는 키
    web_search: dict = field(default_factory=dict)  # [web_search] url_env, max_per_file, max_results, timeout_s
    max_tokens: int | None = None        # 단계당 생성 상한. 있으면 백엔드 params.max_tokens 를 덮어쓴다
    confirm: bool | None = None          # chat 의 쓰기·셸 전 확인 기본값(명령줄 --confirm/--no-confirm 이 이긴다). run 에는 쓰이지 않는다
    memory_files: list[str] | None = None  # 프로젝트 기억 파일 이름(None 이면 AGENTS.md·STEWART.md, [] 이면 끔)
    memory_max_chars: int | None = None    # 기억 파일 합 상한(기본 12,000자)
    compact_ratio: float | None = None     # 자동 압축 문턱(마지막 프롬프트/max_context). None 이면 run 0.7·chat 0.75, 0 이면 끔
    compact_keep_recent: int | None = None # 압축하지 않고 둘 최근 메시지 수(기본 8). compact_fold 면 최근 단계 수
    compact_fold: bool | None = None       # 도구 결과를 한 줄 머리로 접는다(정리 프로필). 없으면 끔(300자 자르기)
    mode: str | None = None                # 실행 모드 auto|edits|ask|plan(modes.py). None 이면 auto(chat 은 --confirm 이면 ask)
    manager_tools: list[str] | None = None  # 관리자 모드: 모델에 보이는 자기 도구(나머지 tools 는 작업자에게만 넘긴다)
    delegate_budget: int | None = None      # 작업자 단계 합 상한(None 이면 max_steps × 2)
    rollback_unfinished: bool | None = None  # run 이 끝내지 못하면(completed·asked 아님) 이번 실행 체크포인트로 되돌림
    reflect_on_fail: bool | None = None    # 실패 문턱에서 끝내기 전 도구 없는 되짚기 한 번(reflect.py). None 이면 chat·허브 켬, run 끔. STEWART_REFLECT 가 이긴다
    reflect_max: int | None = None         # 되짚기 횟수(run 실행당·chat 차례당, 기본 1)
    reflect_max_tokens: int | None = None  # 되짚기 응답 상한(기본 1200)
    skills: bool | None = None             # 스킬(skills.py). None 이면 켬(chat·run 둘 다), false 면 끔
    claude_compat: bool | None = None      # Claude Code 꼴(CLAUDE.md·.claude/) 호환 읽기. None 이면 chat·허브 켬, run 끔(projcfg)
    claude_compat_user: bool | None = None  # 사용자 전역 ~/.claude 도 읽기. None 이면 끔
    secret_files: dict = field(default_factory=dict)  # [secret_files] globs(기본 목록 대신)·extra(기본에 더함)·allow(견본 허용)
    raw: dict = field(default_factory=dict)


def find_profile(name_or_path: str) -> Path:
    """찾는 순서: .toml 경로 그대로 → 이름이면 내장(PROFILES_DIR)과 사용자 폴더(user_profiles_dir).
    이름 겹침 규칙: 같은 이름이 두 곳에 다 있으면 오류다(조용히 덮어쓰지 않는다 — 평가기가 부르는 code 같은 내장 이름이
    사용자 파일로 바뀌는 일을 막는다). 사용자 파일을 쓰려면 이름을 바꾸거나 경로로 준다."""
    p = Path(os.path.expanduser(name_or_path))
    if p.suffix == ".toml":
        if p.exists():
            return p.resolve()
        raise ConfigError(f"프로필 파일이 없다: {name_or_path}")
    if "/" in name_or_path:
        raise ConfigError(f"프로필은 이름 또는 .toml 경로로 준다: {name_or_path}")
    builtin = PROFILES_DIR / f"{name_or_path}.toml"
    user = user_profiles_dir() / f"{name_or_path}.toml"
    if builtin.exists() and user.exists():
        raise ConfigError(f"프로필 이름 '{name_or_path}' 이 내장({builtin})과 사용자 폴더({user})에 둘 다 있다. "
                          "사용자 파일 이름을 바꾸거나 경로(.toml)로 지정하라.")
    for cand in (builtin, user):
        if cand.exists():
            return cand
    raise ConfigError(f"프로필을 찾을 수 없다: {name_or_path} (찾은 곳: {PROFILES_DIR}, {user_profiles_dir()})")


def list_profiles() -> list[tuple[str, Path, str]]:
    """(이름, 경로, 출처 'builtin'|'user'). 겹치는 이름은 둘 다 보인다(쓰려 하면 find_profile 이 오류를 낸다)."""
    out = [(f.stem, f, "builtin") for f in sorted(PROFILES_DIR.glob("*.toml"))]
    u = user_profiles_dir()
    if u.is_dir():
        out += [(f.stem, f, "user") for f in sorted(u.glob("*.toml"))]
    return out


OUTPUT_KEYS = {"format", "schema", "schema_file", "language_fields"}
TOP_KEYS = {"name", "description", "system", "tools", "workdir", "max_steps", "max_consecutive_errors", "shell_timeout_s",
            "backend", "action_check", "verify_command", "language", "web_search", "output", "max_tokens", "confirm",
            "memory_files", "memory_max_chars",
            "compact_ratio", "compact_keep_recent", "compact_fold", "mode", "secret_files", "extends",
            "skills", "claude_compat", "claude_compat_user", "manager_tools", "delegate_budget", "rollback_unfinished",
            "check_on_stop", "check_on_stop_max", "check_on_stop_skip_exit", "hooks", "memory",
            "think_max_tokens", "think_sampling", "extra_write_dirs", "read_outside",
            "review_on_stop", "ask_check", "baseline_diff", "todo_check", "todo_remind_every",
            "reflect_on_fail", "reflect_max", "reflect_max_tokens"}
SECRET_KEYS = {"globs", "extra", "allow"}


def _opt_names(d: dict, key: str, path: Path) -> list[str] | None:
    v = d.get(key)
    if v is None:
        return None
    if not isinstance(v, list) or not all(isinstance(x, str) and x and "/" not in x for x in v):
        raise ConfigError(f"{path}: {key} 는 파일 이름 목록(경로 없이)")
    return list(v)


def _opt_int(d: dict, key: str, path: Path) -> int | None:
    v = d.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        raise ConfigError(f"{path}: {key} 는 양의 정수")
    return v


def _check_on_stop(d: dict, path: Path):
    v = d.get("check_on_stop")
    if v is None or isinstance(v, bool) or (isinstance(v, str) and v.strip()):
        return v
    raise ConfigError(f"{path}: check_on_stop 는 true·false 또는 명령 글")


def _hooks(d: dict, path: Path) -> dict:
    from .hooks import parse
    t = d.get("hooks") or {}
    try:
        parse(t, str(path))
    except ValueError as e:
        raise ConfigError(str(e)) from None
    return dict(t)


def _str_list(d: dict, key: str, path: Path) -> list[str]:
    v = d.get(key, [])
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        raise ConfigError(f"{path}: {key} 는 경로 글 목록")
    return list(v)


def _choice(d: dict, key: str, choices: tuple, default: str, path: Path) -> str:
    v = d.get(key, default)
    if v not in choices:
        raise ConfigError(f"{path}: {key} 는 {'|'.join(choices)} 중 하나")
    return v


def _think_max(d: dict, path: Path):
    v = d.get("think_max_tokens")
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v < 256:
        raise ConfigError(f"{path}: think_max_tokens 는 256 이상 정수")
    return v


def _think_sampling(d: dict, path: Path):
    from .think import PRESETS
    v = d.get("think_sampling")
    if v is None:
        return None
    if v not in PRESETS:
        raise ConfigError(f"{path}: think_sampling 은 {', '.join(PRESETS)} 중 하나")
    return v


def _remind_every(d: dict, path: Path) -> int:
    v = d.get("todo_remind_every", 8)
    if isinstance(v, bool) or not isinstance(v, int) or v < 2:
        raise ConfigError(f"{path}: todo_remind_every 는 2 이상 정수")
    return v


def _pos_int(d: dict, key: str, default: int, path: Path) -> int:
    v = d.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise ConfigError(f"{path}: {key} 는 1 이상 정수")
    return v


def _opt_bool(d: dict, key: str, path: Path) -> bool | None:
    v = d.get(key)
    if v is None or isinstance(v, bool):
        return v
    raise ConfigError(f"{path}: {key} 는 true 또는 false")


def _opt_ratio(d: dict, key: str, path: Path) -> float | None:
    v = d.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v < 1:
        raise ConfigError(f"{path}: {key} 는 0 이상 1 미만(0 이면 끔)")
    return float(v)


def _opt_mode(d: dict, path: Path) -> str | None:
    v = d.get("mode")
    MODES = ("auto", "edits", "ask", "plan")   # modes.MODES 와 같다(순환 import 피함)
    if v is None:
        return None
    if v not in MODES:
        raise ConfigError(f"{path}: mode 는 {', '.join(MODES)} 중 하나")
    return v


def merge_profile(base: dict, child: dict) -> dict:
    """extends 합치기: 표(dict)는 키 단위로 재귀 합치고, 그 밖(글·수·목록)은 자식 값이 통째로 덮는다.
    name 은 물려받지 않는다(자식 파일 이름). 목록을 더하고 싶으면 자식에 전체 목록을 적는다 —
    단 [secret_files] 의 extra 는 원래 '기본에 더함' 뜻이라, 부모 extra 와 자식 extra 를 이어 붙인다(중복 없이)."""
    out = {k: v for k, v in base.items() if k not in ("name", "extends")}
    for k, v in child.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            m = merge_profile(out[k], v)
            if k == "secret_files" and isinstance(out[k].get("extra"), list) and isinstance(v.get("extra"), list):
                m["extra"] = list(dict.fromkeys(out[k]["extra"] + v["extra"]))
            out[k] = m
        else:
            out[k] = v
    return out


def _read_with_extends(path: Path) -> dict:
    d = tomllib.loads(path.read_text(encoding="utf-8"))
    ext = d.get("extends")
    if ext is None:
        return d
    if not isinstance(ext, str) or not ext or "/" in ext or ext.endswith(".toml"):
        raise ConfigError(f"{path}: extends 는 내장 프로필 이름 하나(경로 아님)")
    bpath = PROFILES_DIR / f"{ext}.toml"
    if not bpath.exists():
        raise ConfigError(f"{path}: extends 의 내장 프로필이 없다: {ext}(있는 것: {', '.join(f.stem for f in sorted(PROFILES_DIR.glob('*.toml')))})")
    if bpath.resolve() == path.resolve():
        raise ConfigError(f"{path}: 자기 자신을 extends 할 수 없다")
    b = tomllib.loads(bpath.read_text(encoding="utf-8"))
    if "extends" in b:
        raise ConfigError(f"{bpath}: 내장 프로필은 extends 를 쓰지 않는다(한 단계만)")
    sf = (b.get("output") or {}).get("schema_file")
    if sf and not Path(os.path.expanduser(sf)).is_absolute():      # 부모가 적은 상대 경로는 부모 파일 기준
        b["output"]["schema_file"] = str((bpath.parent / sf).resolve())
    wd = b.get("workdir")
    if isinstance(wd, str) and not Path(os.path.expanduser(wd)).is_absolute():   # workdir 도 같다
        b["workdir"] = str((bpath.parent / wd).resolve())
    merged = merge_profile(b, d)
    merged["extends"] = ext
    return merged


def load_profile(name_or_path: str) -> Profile:
    path = find_profile(name_or_path)
    d = _read_with_extends(path)
    base = path.parent

    def rel(p: str) -> Path:
        q = Path(os.path.expanduser(p))
        return q if q.is_absolute() else (base / q).resolve()

    out = d.get("output") or {}
    bad_out = set(out) - OUTPUT_KEYS
    if bad_out:
        raise ConfigError(f"{path}: [output] 에 모르는 키 {sorted(bad_out)}"
                          + (" — system 은 [output] 같은 표보다 앞(맨 위)에 둬야 한다" if "system" in bad_out else ""))
    bad_top = set(d) - TOP_KEYS
    if bad_top:
        raise ConfigError(f"{path}: 모르는 키 {sorted(bad_top)}")
    sec = d.get("secret_files") or {}
    bad_sec = set(sec) - SECRET_KEYS
    if bad_sec or not all(isinstance(v, list) and all(isinstance(x, str) for x in v) for v in sec.values()):
        raise ConfigError(f"{path}: [secret_files] 는 globs·extra·allow(문자열 목록)만 받는다")
    schema = out.get("schema")
    if out.get("schema_file"):
        schema = json.loads(rel(out["schema_file"]).read_text(encoding="utf-8"))
    fmt = out.get("format", "free")
    if fmt not in ("free", "json"):
        raise ConfigError(f"{path}: output.format 은 free 또는 json")
    if fmt == "json" and not schema:
        raise ConfigError(f"{path}: output.format=json 이면 schema 또는 schema_file 이 필요하다")

    return Profile(
        name=d.get("name", path.stem),
        path=path,
        system=d.get("system", ""),
        tools=list(d.get("tools") or []),
        workdir=rel(d.get("workdir", ".")),
        max_steps=int(d.get("max_steps", 30)),
        max_consecutive_errors=int(d.get("max_consecutive_errors", 4)),
        shell_timeout_s=float(d.get("shell_timeout_s", 60)),
        backend=d.get("backend"),
        output_format=fmt,
        output_schema=schema,
        action_check=d.get("action_check"),
        verify_command=d.get("verify_command"),
        check_on_stop=_check_on_stop(d, path),
        check_on_stop_max=_pos_int(d, "check_on_stop_max", 2, path),
        check_on_stop_skip_exit=[int(x) for x in d.get("check_on_stop_skip_exit", [5])],
        hooks=_hooks(d, path),
        memory=_opt_bool(d, "memory", path),
        think_max_tokens=_think_max(d, path),
        think_sampling=_think_sampling(d, path),
        extra_write_dirs=_str_list(d, "extra_write_dirs", path),
        read_outside=_choice(d, "read_outside", ("allow", "ask", "deny"), "allow", path),
        review_on_stop=_opt_bool(d, "review_on_stop", path),
        ask_check=_opt_bool(d, "ask_check", path),
        baseline_diff=_opt_bool(d, "baseline_diff", path),
        todo_check=_opt_bool(d, "todo_check", path),
        todo_remind_every=_remind_every(d, path),
        description=d.get("description", ""),
        language=d.get("language"),
        language_fields=list(out.get("language_fields") or []),
        web_search=dict(d.get("web_search") or {}),
        max_tokens=_opt_int(d, "max_tokens", path),
        confirm=_opt_bool(d, "confirm", path),
        memory_files=_opt_names(d, "memory_files", path),
        memory_max_chars=_opt_int(d, "memory_max_chars", path),
        compact_ratio=_opt_ratio(d, "compact_ratio", path),
        compact_keep_recent=_opt_int(d, "compact_keep_recent", path),
        compact_fold=_opt_bool(d, "compact_fold", path),
        mode=_opt_mode(d, path),
        secret_files=dict(sec),
        skills=_opt_bool(d, "skills", path),
        claude_compat=_opt_bool(d, "claude_compat", path),
        claude_compat_user=_opt_bool(d, "claude_compat_user", path),
        manager_tools=_opt_names(d, "manager_tools", path),
        delegate_budget=_opt_int(d, "delegate_budget", path),
        rollback_unfinished=_opt_bool(d, "rollback_unfinished", path),
        reflect_on_fail=_opt_bool(d, "reflect_on_fail", path),
        reflect_max=_opt_int(d, "reflect_max", path),
        reflect_max_tokens=_opt_int(d, "reflect_max_tokens", path),
        raw=d,
    )
