"""판 125: 등급 표 한눈에 — 등급마다 어느 서버·모델을 가리키는지, 닿는지, 키·돈 상한. `stewart tiers` 와 허브 설정 시트가 같이 쓴다.

- 늘 보이는 등급은 넷(CANON)이다. backends 표 [tiers.*] 에 다른 이름이 있으면 그 뒤에 더해 보인다.
- 기본 허브: 표에 없는 presto·moderato·largo 는 "기본 모델"을 쓴다(지금 규칙). rubato 는 표에 없으면 "설정 없음"이다.
- 따로 도는 허브(instance): "rubato 만 연결된 stewart" 다. 그 허브의 백엔드(instance.toml 의 backend)가 rubato 이고,
  표에 따로 적지 않은 presto·moderato·largo 는 "연결 없음"이다(옛 instance 설정을 고치지 않고 이 꼴로 읽는다).
- 바깥 백엔드(Backend.external())를 가리키는 등급은 기본이 사람만(manual) — 작업자·자동 분배가 스스로 부르지 않는다.
  돈 상한은 적지 않아도 하루 DEFAULT_USD_DAY·대화 DEFAULT_USD_CHAT 달러(0 이면 끔). 가격표가 없으면 돈을 셀 수 없다고 보인다.
- 키 값은 읽어도 화면·기록에 내지 않는다(있음·없음만). 바깥 사람만 등급은 키 파일(api_key_file)을 쓰지 않는다.
- 판 136: 안쪽(바깥 아닌) 백엔드는 키 자리를 적었는데 값이 비어도 등급을 막지 않는다 — 그때는 키 없이 보낸다(KEY_EMPTY_LOCAL).
  막는 것은 찔러 본 서버가 401·403 을 낼 때(상태 "인증 거부")뿐이다. 바깥 백엔드의 "키 없음"은 그대로 막는다.
- 판 132: 등급마다 생각 깊이(reasoning_effort)와 출처 — 표(등급 칸)·기본(spawn.TIER_DEFAULT_EFFORT)·환경(STEWART_REASONING_EFFORT).
  값 없음은 노력 값을 보내지 않는다는 뜻이다(서버 기본). 기본값을 싣지 못하면 그 까닭을 표 밑에 적는다.
"""

from __future__ import annotations

import os
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CANON = ("presto", "moderato", "largo", "rubato")
OUTSIDE_TIER = "rubato"          # 따로 도는 허브(instance)의 백엔드가 앉는 등급
DEFAULT_USD_DAY = 3.0
DEFAULT_USD_CHAT = 1.0

EFFORT_SRC = {"default": "기본", "tier": "표", "env": "환경", "tier:off": "표", "env:off": "환경"}
KEY_EMPTY_LOCAL = "키 없이 보냄"   # 판 136: 안쪽 백엔드의 키 자리가 비었다(막지 않는다)
STATUS_LABEL = {"ok": "준비됨", "down": "닿지 않음", "auth": "인증 거부", "nokey": "키 없음", "none": "연결 없음",
                "unset": "설정 없음", "unchecked": "확인 안 함"}


def is_manual(t: dict | None, backend) -> bool:
    """사람이 고를 때만 쓰는 등급인가: 표에 manual 을 적었으면 그 값, 아니면 바깥 백엔드면 참."""
    if t and t.get("manual") is not None:
        return bool(t["manual"])
    return bool(backend is not None and backend.external())


def caps(t: dict | None) -> dict:
    """돈 상한(달러). 표에 없으면 기본값, 0 이면 끔(None)."""
    t = t or {}

    def one(k, d):
        v = t.get(k)
        v = d if v is None else float(v)
        return v if v > 0 else None
    return {"usd_day": one("max_usd_day", DEFAULT_USD_DAY), "usd_chat": one("max_usd_chat", DEFAULT_USD_CHAT)}


def key_state(b, manual: bool) -> tuple[str, str]:
    """('있음'|'없음'|'필요 없음', 어디서). 값은 돌려주지 않는다. 사람만 바깥 등급은 키 파일을 보지 않는다."""
    where = [x for x in (f"환경 변수 {b.api_key_env}" if b.api_key_env else "",
                         f"키체인 {b.api_key_keychain}" if b.api_key_keychain else "",
                         f"키 파일" if b.api_key_file and not (manual and b.external()) else "") if x]
    if not where:
        return ("없음", "키 자리를 적지 않음(api_key_keychain 또는 api_key_env)") if b.external() else ("필요 없음", "")
    if b.api_key_env and os.environ.get(b.api_key_env):
        return "있음", where[0]
    if b.api_key_keychain:
        from .config import keychain_secret
        if keychain_secret(b.api_key_keychain):
            return "있음", f"키체인 {b.api_key_keychain}"
    if b.api_key_file and not (manual and b.external()):
        try:
            if Path(os.path.expanduser(b.api_key_file)).read_text(encoding="utf-8").strip():
                return "있음", "키 파일"
        except OSError:
            pass
    if not b.external():   # 판 136: 안쪽 서버 — 값이 비면 키 없이 보낸다(예시 표의 "비어 있으면 키 없이 보냄"). 막지 않는다
        return KEY_EMPTY_LOCAL, " 또는 ".join(where) + " 가 비어 있음"
    return "없음", " 또는 ".join(where)


def rows(backends: dict, default: str, tiers: dict, instance_backend: str | None = None, instance_keychain: str | None = None) -> list[dict]:
    """등급 줄들(probe 전). backends = {이름: Backend}, tiers = spawn.load_tiers() 꼴(+ 원래 칸 raw)."""
    out = []
    for name in list(CANON) + [n for n in tiers if n not in CANON]:
        t = tiers.get(name)
        if t:
            bname, src = t.get("backend") or (instance_backend or default), "표"
        elif instance_backend:
            bname, src = (instance_backend, "이 허브의 모델") if name == OUTSIDE_TIER else (None, "연결 없음")
        else:
            bname, src = (default, "기본 모델") if name != OUTSIDE_TIER else (None, "설정 없음")
        b = backends.get(bname) if bname else None
        r = {"tier": name, "source": src, "backend": bname or "", "connected": b is not None, "use": (t or {}).get("use") or ""}
        if b is None:
            r.update(status="none" if instance_backend or src == "연결 없음" else "unset", manual=False)
            if bname and bname not in backends:
                r.update(status="unset", note=f"백엔드 '{bname}' 가 표에 없다")
            out.append(r)
            continue
        manual = is_manual(t, b) or (bool(instance_backend) and name == OUTSIDE_TIER and t is None and b.external())
        ks, kw = key_state(b, manual)
        if ks == "없음" and instance_keychain and name == OUTSIDE_TIER and bname == instance_backend:
            from .config import keychain_secret   # 따로 도는 허브는 켤 때 instance.toml 의 키체인 항목에서 키를 읽어 넘긴다
            ks, kw = ("있음", f"키체인 {instance_keychain}") if keychain_secret(instance_keychain) else ("없음", f"키체인 {instance_keychain}")
            b_kc = instance_keychain
        else:
            b_kc = None
        from . import spawn as SP
        r["effort"] = SP.effort_view(name, (t or {}).get("think"), b)   # 판 132: 생각 깊이와 출처
        r.update(keychain=b_kc or b.api_key_keychain or "", key_env=b.api_key_env or "")
        r.update(model=b.model, url=b.base_url, external=b.external(), provider=b.provider_label() if b.external() else "",
                 concurrency=b.concurrency, manual=manual, key=ks, key_where=kw, priced=bool(b.price),
                 status="nokey" if ks == "없음" else "unchecked")
        if manual and b.external():
            r["caps"] = caps(t)
        out.append(r)
    return out


def probe(rows_: list[dict], backends: dict, timeout: float = 3.0) -> list[dict]:
    """연결된 줄마다 GET /models 를 한 번(같은 백엔드는 한 번만, 나란히). 키 없는 줄은 찌르지 않는다."""
    from . import backend as B
    want = sorted({r["backend"] for r in rows_ if r.get("connected") and r.get("status") == "unchecked"})

    def one(name):
        try:
            B.models(backends[name], timeout=timeout)
            return name, "ok"
        except Exception as e:   # noqa: BLE001 — 상태 글로만 쓴다
            s = str(e)
            return name, "auth" if (" 401" in s or " 403" in s or "Unauthorized" in s or "Forbidden" in s) else "down"
    got = dict(ThreadPoolExecutor(max_workers=max(1, min(8, len(want)))).map(one, want)) if want else {}
    for r in rows_:
        if r.get("backend") in got and r.get("status") == "unchecked":
            r["status"] = got[r["backend"]]
    return rows_


def load(backends_file=None, instance_backend: str | None = None, default_backend: str | None = None,
         instance_keychain: str | None = None) -> tuple[list[dict], dict]:
    """지금 설정에서 등급 줄을 만든다 → (줄들, backends). instance_backend 가 없으면 환경 STEWART_INSTANCE 의 허브에서는 기본 백엔드를 그것으로 본다."""
    from . import spawn as SP
    from .config import load_backends
    backends, default = load_backends(Path(backends_file) if backends_file else None)
    default = default_backend or default
    tiers = SP.load_tiers(backends_file)
    if instance_backend is None and os.environ.get("STEWART_INSTANCE"):
        instance_backend = default
    return rows(backends, default, tiers, instance_backend, instance_keychain), backends


def _w(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s: str, n: int) -> str:
    return s + " " * max(0, n - _w(s))


def key_hint(r: dict) -> str:
    """키 넣는 한 줄(값은 묻는 칸에 — 명령 줄·기록에 남지 않게)."""
    kc = r.get("keychain") or ""
    env = r.get("key_env") or ""
    if kc:
        return f"키체인에 넣는다: security add-generic-password -U -s {kc} -a api -w   (값은 묻는 칸에 붙여 넣는다)"
    if env:
        return f"허브를 켜는 셸에서 환경 변수 {env} 에 넣고 다시 켠다(따로 도는 허브는 instance.toml 의 키체인 항목에)"
    return "백엔드 표에 api_key_keychain = \"<키체인 항목 이름>\" 을 적고 그 항목에 키를 넣는다"


def effort_label(e: dict | None) -> str:
    """판 132: '낱말 (출처)' — 값 없음은 노력 값을 보내지 않는다(서버 기본)."""
    if not e:
        return ""
    src = EFFORT_SRC.get(e.get("source") or "")
    v = e.get("value") or "값 없음"
    return f"{v} ({src})" if src else v


def text(rows_: list[dict], title: str = "") -> str:
    """사람이 읽는 표(키 값은 없다)."""
    head = ["등급", "모델", "서버", "상태", "동시", "생각 깊이", "키", "돈 상한"]
    body = []
    for r in rows_:
        if not r.get("connected"):
            body.append([r["tier"], r.get("note") or r["source"], "", STATUS_LABEL[r["status"]], "", "", "", ""])
            continue
        model = r["model"] + ("" if r["source"] == "표" else f" ({r['source']})")
        where = f"바깥 API · {r['provider']}" if r["external"] else r["url"].split("//", 1)[-1].split("/", 1)[0]
        st = STATUS_LABEL[r["status"]] + (" · 사람만" if r["manual"] else "")
        cap = ""
        if r.get("caps") is not None:
            c = r["caps"]
            cap = "가격표 없음 — 돈을 셀 수 없다" if not r["priced"] else (
                " · ".join(x for x in (f"하루 ${c['usd_day']:g}" if c["usd_day"] else "하루 끔", f"대화 ${c['usd_chat']:g}" if c["usd_chat"] else "대화 끔")))
        body.append([r["tier"], model, where, st, str(r["concurrency"]), effort_label(r.get("effort")),
                     r["key"] if r["key"] != "필요 없음" else "-", cap])
    widths = [max(_w(x[i]) for x in [head] + body) for i in range(len(head))]
    lines = ([title] if title else []) + ["  ".join(_pad(c, widths[i]) for i, c in enumerate(head)).rstrip()]
    lines += ["  ".join(_pad(c, widths[i]) for i, c in enumerate(row)).rstrip() for row in body]
    for r in rows_:
        if r.get("key") == "없음" and r.get("connected"):
            lines.append(f"· {r['tier']}: 키 없음 — {key_hint(r)}")
        elif r.get("key") == KEY_EMPTY_LOCAL and r.get("connected"):
            lines.append(f"· {r['tier']}: {r['key_where']} — 키 없이 보낸다(서버가 키를 요구하면 상태가 '인증 거부'로 바뀐다)")
    for r in rows_:
        if (r.get("effort") or {}).get("note"):
            lines.append(f"· {r['tier']} 생각 깊이: {r['effort']['note']}")
    if any(r.get("effort") for r in rows_):
        lines.append("· 생각 깊이 '값 없음' = 노력 값을 보내지 않는다(서버 기본). 출처: 표 = 등급 칸 reasoning_effort, 기본 = 등급 기본값, "
                     "환경 = STEWART_REASONING_EFFORT")

    return "\n".join(lines)
