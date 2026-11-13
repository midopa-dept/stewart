"""판 125: 바깥 등급의 돈 상한 — 대화 하나·하루(허브 전체, 등급마다). 넘으면 다음 요청을 보내지 않고 까닭을 알린다.

- 상한은 등급 칸 max_usd_day·max_usd_chat(달러, 0 = 끔)·max_tokens_day(토큰, 가격표가 없을 때 쓰는 꼴). 안 적으면 tierview.caps 의 기본값.
- 돈은 응답 usage 의 cost_usd(백엔드 price 가격표가 있을 때)로 센다. 가격표가 없으면 돈을 셀 수 없다 — 그때는 토큰 상한만 건다.
- 하루 누계는 허브 상태 폴더 spend/<날짜>.json 에 등급마다 남긴다(허브를 다시 켜도 이어 센다). 날짜는 이 기계의 날짜.
- 백엔드 객체에 .spend 로 붙는다: 보내기 전 before()(넘었으면 BackendError), 받은 뒤 after(usage). backend.chat·chat_stream 이 부른다.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

_LOCK = threading.Lock()


class Spend:
    def __init__(self, ledger_dir: Path, tier: str, usd_day: float | None, usd_chat: float | None,
                 tokens_day: int | None = None, priced: bool = True):
        self.dir, self.tier = Path(ledger_dir), tier
        self.usd_day, self.usd_chat, self.tokens_day, self.priced = usd_day, usd_chat, tokens_day, priced
        self.chat_usd, self.chat_tokens = 0.0, 0

    def _file(self) -> Path:
        return self.dir / f"{time.strftime('%Y%m%d')}.json"

    def _read(self) -> dict:
        try:
            d = json.loads(self._file().read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def today(self) -> dict:
        with _LOCK:
            e = self._read().get(self.tier) or {}
        return {"usd": float(e.get("usd") or 0.0), "tokens": int(e.get("tokens") or 0), "calls": int(e.get("calls") or 0)}

    def before(self) -> None:
        from .backend import BackendError
        day = self.today()
        if self.priced and self.usd_day and day["usd"] >= self.usd_day:
            raise BackendError(f"등급 {self.tier}: 오늘 돈 상한 ${self.usd_day:g} 에 닿았다(오늘 ${day['usd']:.2f}) — 보내지 않았다. "
                               "내일 다시 쓰거나 backends 표 [tiers." + self.tier + "] 의 max_usd_day 를 바꾼다(0 = 끔)")
        if self.priced and self.usd_chat and self.chat_usd >= self.usd_chat:
            raise BackendError(f"등급 {self.tier}: 이 대화의 돈 상한 ${self.usd_chat:g} 에 닿았다(${self.chat_usd:.2f}) — 보내지 않았다. "
                               "새 대화를 열거나 max_usd_chat 을 바꾼다(0 = 끔)")
        if self.tokens_day and day["tokens"] >= self.tokens_day:
            raise BackendError(f"등급 {self.tier}: 오늘 토큰 상한 {self.tokens_day} 에 닿았다(오늘 {day['tokens']}) — 보내지 않았다. "
                               "max_tokens_day 를 바꾼다(0 = 끔)")

    def after(self, usage: dict) -> None:
        u = usage or {}
        usd = float(u.get("cost_usd") or 0.0)
        tok = int(u.get("prompt_tokens") or 0) + int(u.get("completion_tokens") or 0)
        self.chat_usd += usd
        self.chat_tokens += tok
        with _LOCK:
            d = self._read()
            e = d.get(self.tier) or {}
            d[self.tier] = {"usd": round(float(e.get("usd") or 0.0) + usd, 6), "tokens": int(e.get("tokens") or 0) + tok,
                            "calls": int(e.get("calls") or 0) + 1}
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                tmp = self._file().with_suffix(".tmp")
                tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self._file())
            except OSError:
                pass

    def public(self) -> dict:
        day = self.today()
        return {"tier": self.tier, "usd_day": self.usd_day, "usd_chat": self.usd_chat, "tokens_day": self.tokens_day, "priced": self.priced,
                "today_usd": round(day["usd"], 4), "today_tokens": day["tokens"], "chat_usd": round(self.chat_usd, 4)}


def attach(backend, ledger_dir: Path, tier: str, t: dict | None) -> None:
    """바깥 사람만 등급의 백엔드에 상한을 붙인다(가격표가 없으면 토큰 상한만)."""
    from .tierview import caps
    c = caps(t)
    tok = (t or {}).get("max_tokens_day")
    backend.spend = Spend(ledger_dir, tier, c["usd_day"], c["usd_chat"], int(tok) if tok else None, priced=bool(getattr(backend, "price", None)))
