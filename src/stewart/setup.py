"""판 125: `stewart setup` — 물어 가며 backends 표(등급 ↔ 서버·모델)를 만든다. 허브 설정 시트와 같은 길(settings.edit)로 쓴다.

- 표가 없으면: 모델 서버 주소를 묻고 GET /models 로 모델 이름·문맥 크기를 찾아 기본 백엔드를 만든다.
- 그다음 등급마다(빈칸이면 그대로): 서버를 고르거나 새 주소를 넣는다. rubato 는 바깥 API(제공자 주소·모델·이름·키·돈 상한).
- 키는 묻는 칸(가림)으로 받아 키체인에 넣는다(macOS). 다른 OS 는 환경 변수 이름만 적는다. 값은 표에 적지 않는다.
- 쓰기 전에 표를 검사하고 백업을 남긴다. 끝에 `stewart tiers` 꼴 표를 찍는다.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

from . import tierview as TV


def _ask(prompt: str, default: str = "", inp=input) -> str:
    v = inp(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return v or default


def _probe(url: str) -> tuple[list[str], int | None]:
    import dataclasses
    from . import backend as B
    from .config import Backend
    bk = Backend(name="probe", base_url=url.rstrip("/"), model="?", precision="?", max_context=8192, timeout_s=5)
    try:
        names, _ = B.models(bk, timeout=4)
    except Exception as e:   # noqa: BLE001 — 까닭만 보인다
        print(f"  닿지 않았다({str(e)[:120]}) — 주소를 확인하거나 서버를 켠 뒤 다시")
        return [], None
    ctx = B.served_context(dataclasses.replace(bk, model=names[0]), timeout=4) if names else None
    print(f"  서버가 올린 모델: {', '.join(names[:8]) or '없음'}" + (f" · 문맥 {ctx}" if ctx else ""))
    return names, ctx


def _server(path: Path, name: str, inp, out_names: list[str]) -> str | None:
    """새 서버 하나를 묻고 표에 적는다 → 백엔드 이름."""
    from . import settings as ST
    url = _ask("  서버 주소(…/v1)", "", inp)
    if not url:
        return None
    names, ctx = _probe(url)
    model = _ask("  모델 이름", names[0] if names else "", inp)
    conc = _ask("  동시 요청 수", "1", inp)
    ST.edit(path, backend=name, backend_fields={"base_url": url, "model": model, "concurrency": int(conc or 1),
                                                 **({"max_context": ctx} if ctx else {})})
    out_names.append(name)
    return name


def run(path: Path | None = None, inp=input, secret=getpass.getpass) -> int:
    from . import settings as ST
    from . import tomledit as TE
    from .config import ConfigError, backends_path, load_backends
    path = Path(path or backends_path())
    print(f"stewart setup — {path}")
    try:
        bks, default = load_backends(path) if path.exists() else ({}, "")
    except ConfigError as e:
        print(f"표를 읽지 못했다({e}) — 먼저 고치거나 지운 뒤 다시")
        return 2
    names = list(bks)
    if not bks:
        print("백엔드 표가 없다. 모델 서버 하나부터(이 기계라면 보통 http://127.0.0.1:<포트>/v1).")
        n = _server(path, _ask("  이 서버 이름", "main", inp), inp, names)
        if not n:
            print("서버 없이 끝낸다")
            return 1
        text = path.read_text(encoding="utf-8")
        if not text.startswith("default"):
            TE.write(path, f'default = "{n}"\n\n' + text)
    rows, _ = TV.load(path)
    print(TV.text(rows))
    print("\n등급마다 고친다(빈칸이면 그대로). 서버 이름을 넣거나 '새' 로 새 서버를 더한다.")
    for tier in TV.CANON:
        if tier == TV.OUTSIDE_TIER:
            if _ask(f"{tier}: 바깥 API(유료)를 쓸까? 예/아니오", "아니오", inp) not in ("예", "y", "yes"):
                continue
            url = _ask("  제공자 API 주소(https://…/v1)", "", inp)
            if not url:
                continue
            model = _ask("  모델 이름", "", inp)
            prov = _ask("  제공자 이름(화면에 '바깥 API · 이름')", "", inp)
            bname = _ask("  이 서버 이름", "outside", inp)
            kc = f"stewart-{bname}"
            fields = {"base_url": url, "model": model, "provider": prov, "concurrency": 2}
            if sys.platform == "darwin":
                fields["api_key_keychain"] = kc
            else:
                fields["api_key_env"] = _ask("  키를 둘 환경 변수 이름", "OUTSIDE_API_KEY", inp)
            ST.edit(path, backend=bname, backend_fields=fields)
            day = _ask("  하루 돈 상한(달러, 0 = 끔)", "3", inp)
            chat = _ask("  대화 하나 돈 상한(달러, 0 = 끔)", "1", inp)
            ST.edit(path, tier, {"backend": bname, "max_usd_day": float(day), "max_usd_chat": float(chat),
                                 "use": "바깥의 큰 모델(유료) — 사람이 고를 때만"})
            if sys.platform == "darwin":
                key = secret(f"  키(가림 입력, 키체인 {kc} 에 넣는다 — 빈칸이면 나중에): ").strip()
                if key:
                    try:
                        ST.keychain_put(kc, key)
                        print(f"  키체인 {kc} 에 넣었다")
                    except ValueError as e:
                        print(f"  키를 넣지 못했다: {e}")
            else:
                print(f"  키는 허브를 켜는 셸에서 환경 변수 {fields['api_key_env']} 에 넣는다")
            print("  가격표(price)가 없으면 돈을 셀 수 없다 — backends 표 그 서버 칸에 price = { input = …, output = … } 를 적는다")
            continue
        v = _ask(f"{tier}: 서버({', '.join(names)} · 새 · 빈칸 = 그대로)", "", inp)
        if not v:
            continue
        if v == "새":
            v = _server(path, _ask("  새 서버 이름", tier + "-srv", inp), inp, names)
            if not v:
                continue
        if v not in names:
            print(f"  그런 서버가 없다: {v}")
            continue
        ST.edit(path, tier, {"backend": v})
    rows, bks2 = TV.load(path)
    TV.probe(rows, bks2)
    print("\n" + TV.text(rows))
    print(f"\n적었다: {path}(앞 판은 같은 폴더의 {path.name}.bak-* — 되돌리려면 허브 설정 시트의 되돌리기)")
    return 0
