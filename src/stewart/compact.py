"""문맥 압축(engine.run·chat 공통). 문턱식: 프롬프트가 문턱을 넘을 때만 한 번에 한 덩어리를 줄인다.

- 덩어리 = 아직 안 줄인 앞쪽 메시지(시스템·첫 과제 다음부터, 최근 keep_recent 개 앞까지).
  최근 몫이 max_context 의 25%(글자/3 어림)를 넘으면 경계를 뒤로 당긴다(마지막 2개는 늘 남김).
- 줄이는 것: 도구 결과(tool), 툴콜 인자 속 긴 글(write_file content 등), 긴 중간 답(assistant 글).
  사람 입력(user)과 시스템 지시는 그대로 둔다. 메시지 수·순서·툴콜 id 는 바꾸지 않는다(형식이 깨지지 않게).
- 이미 줄인 덩어리는 다시 고치지 않는다(self.upto 앞은 얼어 있다) → prefix cache 는 그 앞까지 그대로 맞는다.
- 줄인 뒤에는 프롬프트가 다시 일정량(re_arm, 기본 max_context 의 10%) 자라야 다음 압축이 걸린다(매 단계 조금씩 고쳐 cache 를 깨지 않게).
- 문턱·최근 몫·re_arm 은 max_context 가 아니라 "쓸 수 있는 창" = max_context − 응답 상한(reserve, 단계당 max_tokens)으로 잰다.
  vLLM 은 프롬프트 + max_tokens 가 max_model_len 을 넘으면 400 으로 거부한다. reserve 는 max_context 의 절반을 넘기지 않는다.
- 백엔드가 문맥 넘침으로 거부하면 maybe(force=True, keep=1)로 한 번 접고 같은 단계를 다시 보낸다(engine·chat).
"""

from __future__ import annotations

import json

CLIP = 300
NOTE = "\n…(압축: 원래 {n}자. 필요하면 다시 읽어라)"


def est_tokens(text: str) -> int:
    """토큰 어림(토크나이저 없이 위로 어림). Qwen 계열은 숫자를 한 자리씩 끊고 한글·한자는 한 자에 1토큰 남짓이라
    글자/3 어림이 폴더 목록(크기·날짜·한글 이름)에서 크게 모자랐다. 숫자 1, 영문자 0.3, 그 밖 ASCII(공백·기호) 0.5,
    ASCII 밖 글자 1.2 토큰으로 센다."""
    d = a = o = n = 0
    for ch in text:
        if ch.isascii():
            if ch.isdigit():
                d += 1
            elif ch.isalpha():
                a += 1
            else:
                o += 1
        else:
            n += 1
    from .images import IMAGE_TOKENS, SCHEME
    return int(d + 0.3 * a + 0.5 * o + 1.2 * n + 0.999) + text.count(SCHEME) * IMAGE_TOKENS   # 이미지 자리표는 고정값


def repeat_stats(text: str, n: int = 16, tail: int = 600) -> dict:
    """되풀이 지표(생각 잘림 판정용 — 글은 기록에 따로 있다). 낱말·기호 단위.
    dup: n-gram 가운데 두 번 이상 나온 것의 비율, tail_seen: 끝 tail 낱말 속 n-gram 이 그 앞에 이미 나온 비율
    (고리면 1 가까이, 정상 긴 생각이면 0 가까이)."""
    import collections
    import re
    toks = re.findall(r"\w+|[^\w\s]", text or "")
    grams = [tuple(toks[i:i + n]) for i in range(max(0, len(toks) - n + 1))]
    cnt = collections.Counter(grams)
    dup = sum(v for v in cnt.values() if v > 1) / len(grams) if grams else 0.0
    cut = max(0, len(toks) - tail)
    head = {tuple(toks[i:i + n]) for i in range(max(0, cut - n + 1))}
    tg = [tuple(toks[i:i + n]) for i in range(cut, max(cut, len(toks) - n + 1))]
    seen = sum(1 for g in tg if g in head) / len(tg) if tg else 0.0
    return {"chars": len(text or ""), "tokens_est": est_tokens(text or ""), "dup16": round(dup, 3), "tail_seen16": round(seen, 3)}


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + NOTE.format(n=len(s))


def clip_block(messages: list[dict], start: int, end: int, clip: int = CLIP) -> dict:
    """messages[start:end] 를 제자리에서 줄인다. {"clipped": 줄인 메시지 수, "chars_before", "chars_after"}."""
    n = before = after = 0
    for m in messages[start:end]:
        role = m.get("role")
        changed = False
        if role == "tool" and isinstance(m.get("content"), str) and len(m["content"]) > clip:
            before += len(m["content"])
            m["content"] = _clip(m["content"], clip)
            after += len(m["content"])
            changed = True
        elif role == "assistant":
            c = m.get("content")
            if isinstance(c, str) and len(c) > 2 * clip:
                before += len(c)
                m["content"] = _clip(c, 2 * clip)
                after += len(m["content"])
                changed = True
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                raw = fn.get("arguments")
                if not isinstance(raw, str) or len(raw) <= clip:
                    continue
                try:
                    args = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(args, dict):
                    continue
                if any(isinstance(v, str) and len(v) > clip for v in args.values()):
                    args = {k: (_clip(v, clip) if isinstance(v, str) else v) for k, v in args.items()}
                    new = json.dumps(args, ensure_ascii=False)
                    before += len(raw)
                    after += len(new)
                    fn["arguments"] = new
                    changed = True
        n += changed
    return {"clipped": n, "chars_before": before, "chars_after": after}


FOLD_KEEP_TOOLS = {"plan_add", "plan_show", "plan_drop", "todo_write", "todo_read"}   # 계획·할 일 상태는 원문 유지
FOLD_MIN = 200
FOLD_HEAD = "[접힘] {name}({args}) 원래 {n:,}자{plan} — 다시 필요하면 같은 도구를 같은 인자로 다시 부른다"


def _args_short(raw: str, n: int = 120) -> str:
    try:
        a = json.loads(raw or "{}")
    except ValueError:
        return (raw or "")[:n]
    if not isinstance(a, dict):
        return str(a)[:n]
    s = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in a.items())
    return s if len(s) <= n else s[:n] + "…"


def fold_block(messages: list[dict], start: int, end: int, plan_items: int | None = None, clip: int = CLIP) -> dict:
    """정리 실행용: messages[start:end] 의 도구 결과를 한 줄 머리로 접는다(계획·할 일 도구는 그대로).
    긴 중간 답·툴콜 인자는 clip_block 과 같이 줄인다. 메시지 수·순서·툴콜 id 는 바꾸지 않는다."""
    calls: dict = {}
    n = before = after = 0
    plan = f", 접을 때 plan 에 {plan_items}건 반영" if plan_items is not None else ""
    for i, m in enumerate(messages[:end]):     # 앞에서부터: 도구 결과는 바로 앞 assistant 의 호출과 맞춘다(id 가 겹쳐도)
        if m.get("role") == "assistant":
            calls = {tc.get("id"): ((tc.get("function") or {}).get("name", "?"), (tc.get("function") or {}).get("arguments", ""))
                     for tc in m.get("tool_calls") or []}
            continue
        if i < start or m.get("role") != "tool" or not isinstance(m.get("content"), str) or m["content"].startswith("[접힘]"):
            continue
        name, raw = calls.get(m.get("tool_call_id"), ("?", ""))
        if name in FOLD_KEEP_TOOLS or len(m["content"]) <= FOLD_MIN:
            continue
        before += len(m["content"])
        m["content"] = FOLD_HEAD.format(name=name, args=_args_short(raw), n=len(m["content"]), plan=plan)
        after += len(m["content"])
        n += 1
    r = clip_block([m for m in messages[start:end] if m.get("role") != "tool"], 0, end - start, clip)
    return {"clipped": n + r["clipped"], "folded": n, "chars_before": before + r["chars_before"],
            "chars_after": after + r["chars_after"]}


def reply_reserve(profile, backend) -> int:
    """단계당 응답 상한: 프로필 max_tokens, 없으면 백엔드 params 의 max_tokens, 둘 다 없으면 0."""
    v = getattr(profile, "max_tokens", None) or (getattr(backend, "params", None) or {}).get("max_tokens") or 0
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


ENV_FOLD, ENV_RATIO, ENV_KEEP = "STEWART_COMPACT_FOLD", "STEWART_COMPACT_RATIO", "STEWART_COMPACT_KEEP"


def settings(profile, default_ratio: float, default_keep: int, env=None) -> dict:
    """압축 설정: 프로필 값, 그 위에 환경 변수(평가 대기열이 프로필을 바꾸지 않고 '접기 켬' 판을 돌리게).
    STEWART_COMPACT_FOLD=1|0, STEWART_COMPACT_RATIO=0~1 미만(0 이면 끔), STEWART_COMPACT_KEEP=양의 정수. 잘못된 값은 무시하고 기록한다."""
    import os
    env = os.environ if env is None else env
    ratio = default_ratio if profile.compact_ratio is None else profile.compact_ratio
    keep = profile.compact_keep_recent or default_keep
    fold = bool(profile.compact_fold)
    src, bad = [], []
    v = env.get(ENV_FOLD)
    if v is not None:
        if v in ("1", "0"):
            fold, _ = v == "1", src.append(ENV_FOLD)
        else:
            bad.append(ENV_FOLD)
    v = env.get(ENV_RATIO)
    if v is not None:
        try:
            r = float(v)
            if not 0 <= r < 1:
                raise ValueError
            ratio, _ = r, src.append(ENV_RATIO)
        except ValueError:
            bad.append(ENV_RATIO)
    v = env.get(ENV_KEEP)
    if v is not None:
        if v.isdigit() and int(v) > 0:
            keep, _ = int(v), src.append(ENV_KEEP)
        else:
            bad.append(ENV_KEEP)
    return {"ratio": ratio, "keep_recent": keep, "fold": fold, "env": src, "ignored": bad}


class Compactor:
    def __init__(self, max_context: int, ratio: float = 0.7, keep_recent: int = 8, clip: int = CLIP,
                 start: int = 2, re_arm: float = 0.1, keep_share: float = 0.25, fold: bool = False, plan_count=None,
                 reserve: int = 0, tail_tokens: bool = False):
        """fold=True(정리 실행): 도구 결과를 한 줄 머리로 접고, keep_recent 를 메시지 수가 아니라 최근 단계 수
        (assistant 메시지 수)로 센다. plan_count() 는 접을 때 계획 건수를 머리에 적는다.
        reserve: 단계당 응답 상한(max_tokens). 판단은 창 = max_context − reserve 로 한다."""
        self.fold = fold
        self.plan_count = plan_count
        self.reserve = max(0, min(int(reserve or 0), max_context // 2))
        self.max_context = max_context - self.reserve      # 판단에 쓰는 창(프롬프트가 쓸 수 있는 몫)
        self.full_context = max_context
        # 최근 몫 상한: 남길 최근 메시지 글자 합이 창의 keep_share 만큼(토큰≈글자/3)을 넘으면 더 앞당겨 줄인다.
        # 큰 도구 결과 몇 개가 최근 keep_recent 안에 다 들어 있으면 줄일 것이 없어지기 때문(작은 모델 시험에서 본 것)
        # 최근 몫을 무엇으로 재나: tail_tokens=True(chat·허브, 평가 손잡이)는 토큰 어림 — 글자/3 으로 재면 한글·숫자에서 최근 몫이
        # 창 대부분이 되어 줄일 것이 없었다(판 65). 평가 길 기본(False)은 기준선을 흔들지 않게 예전 글자 셈 그대로
        self.tail_tokens = tail_tokens
        self.keep_tokens = int(keep_share * self.max_context)
        self.keep_chars = int(keep_share * self.max_context * 3)
        self.ratio = ratio
        self.keep_recent = keep_recent
        self.clip = clip
        self.upto = start          # 이 앞은 고치지 않는다(시스템 + 첫 과제, 그리고 이미 줄인 덩어리)
        self.re_arm = re_arm
        self.next_at = ratio * self.max_context if ratio > 0 else float("inf")
        self.log: list[dict] = []
        self._pending: dict | None = None

    def threshold(self) -> float:
        return self.next_at

    def maybe(self, messages: list[dict], last_prompt_tokens: int, step: int | None = None, force: bool = False,
              keep: int | None = None) -> dict | None:
        """문턱을 넘었으면(또는 force) 한 덩어리를 줄이고 기록을 돌려준다. 아니면 None.
        keep(넘침 재시도): 이번만 최근 몫을 keep 으로 줄인다(접기는 단계 수, 아니면 메시지 수)."""
        if not force and last_prompt_tokens < self.next_at:
            return None
        k = self.keep_recent if keep is None else max(1, keep)
        if self.fold:   # 최근 k 단계(= assistant 메시지 k 개와 그 결과)는 원문 유지
            idx = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
            end = idx[-k] if len(idx) >= k else self.upto
            end = max(self.upto, end)
        else:
            end = max(self.upto, len(messages) - k)
            meas = (lambda m: est_tokens(json.dumps(m, ensure_ascii=False))) if self.tail_tokens \
                else (lambda m: len(json.dumps(m, ensure_ascii=False)))
            lim = self.keep_tokens if self.tail_tokens else self.keep_chars
            sizes = [meas(m) for m in messages[end:]]
            tail = sum(sizes)
            j = 0
            while end < len(messages) - 2 and tail > lim:   # 마지막 2개(직전 호출과 그 결과)는 늘 남긴다
                tail -= sizes[j]
                j += 1
                end += 1
        if end <= self.upto:
            return None
        if self.fold:
            r = fold_block(messages, self.upto, end, self.plan_count() if self.plan_count else None, self.clip)
        else:
            r = clip_block(messages, self.upto, end, self.clip)
        rec = {"step": step, "forced": force, **({"keep": k} if keep is not None else {}), "from": self.upto, "to": end, "before_tokens": last_prompt_tokens,
               "after_tokens": None, **r}
        self.upto = end
        if r["clipped"]:
            # 다음 압축은 프롬프트가 지금보다 re_arm 만큼 더 자란 뒤(줄인 효과는 다음 응답 usage 로 본다).
            # 줄인 것이 없으면 문턱을 올리지 않는다(빈 압축이 다음 압축을 re_arm 만큼 늦추던 것)
            self.next_at = max(self.ratio * self.max_context if self.ratio > 0 else float("inf"),
                               last_prompt_tokens + self.re_arm * self.max_context)
            self.log.append(rec)
            self._pending = rec
            return rec
        return None

    def observe(self, prompt_tokens: int | None) -> dict | None:
        """압축 뒤 첫 응답의 프롬프트 토큰을 적는다. 적은 기록을 돌려준다."""
        if self._pending is None or not prompt_tokens:
            return None
        p, self._pending = self._pending, None
        p["after_tokens"] = prompt_tokens
        if self.ratio > 0:   # 실제로 줄어든 크기 기준으로 다시 문턱을 건다
            self.next_at = max(self.ratio * self.max_context, prompt_tokens + self.re_arm * self.max_context)
        return p


# ── 요약 접기 공용(판 65~66: chat·허브, 판 66: 평가 손잡이 STEWART_COMPACT_MODE) ──
SUMMARY_SYSTEM = ("너는 대화 요약가다. 코딩 에이전트와 사람의 앞 대화를 받아, 에이전트가 이어서 일하는 데 필요한 것만 한국어로 정리한다. "
                  "빠뜨리는 것이 더 나쁘다(먼저 빠짐없이, 그다음 군더더기를 뺀다). "
                  "꼴: ## 목표·사람 요구(사람 말의 지시·금지·선호는 빠짐없이, 가능하면 원문 그대로) / ## 파일 상태(바꾼 파일과 지금 어떻게 돼 있나) / "
                  "## 한 일과 결과(돌린 명령·확인된 사실, 파일:줄) / ## 정한 것(설계·방향과 까닭) / ## 남은 일·안 풀린 문제 / "
                  "## 실패 기록(해 봤는데 안 된 길과 까닭 — 되풀이하지 않게). 확인 안 된 것은 '추측'이라 적는다. 비밀 값은 옮기지 않는다. 2500자 안.")
SUMMARY_MAX_CHARS = 60000


def summary_text(span: list[dict]) -> str:
    """접을 메시지들을 요약기에 넘길 글로(도구 결과 600자·나머지 1500자에서 자르고, 너무 길면 끝 부분만)."""
    lines = []
    for m in span:
        role = {"user": "사람", "assistant": "에이전트", "tool": "도구 결과"}.get(m.get("role"), m.get("role"))
        body = str(m.get("content") or "")
        lim = 600 if m.get("role") == "tool" else 1500
        body = body if len(body) <= lim else body[:lim] + f"…({len(body)}자)"
        calls = "; ".join(f"{(tc.get('function') or {}).get('name')}({str((tc.get('function') or {}).get('arguments') or '')[:200]})"
                          for tc in m.get("tool_calls") or [])
        lines.append(f"[{role}] {body}" + (f"\n  호출: {calls}" if calls else ""))
    text = "\n".join(lines)
    if len(text) > SUMMARY_MAX_CHARS:
        text = "…(앞은 너무 길어 끝 부분만)\n" + text[-SUMMARY_MAX_CHARS:]
    return text


def ask_summary(chat_fn, backend, text: str) -> str:
    """도구 없는 한 번(생각 끔)으로 요약을 받는다. 빈 글이면 빈 글."""
    resp, _dt = chat_fn(backend, [{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content": "앞 대화:\n" + text}],
                        None, max_tokens=1500, thinking=False)
    return (((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()


def mask_roll(messages: list[dict], keep_steps: int = 10, start: int = 2) -> int:
    """굴러가는 가리기(arXiv 2508.21433 의 M=10 꼴): 최근 keep_steps 단계(assistant 메시지) 앞의 도구 결과를 자리표로.
    추론·행동(assistant)은 그대로. 이미 가린 것은 다시 고치지 않는다. 가린 수."""
    idx = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    if len(idx) <= keep_steps:
        return 0
    end = idx[-keep_steps]
    n = 0
    for m in messages[start:end]:
        c = m.get("content")
        if m.get("role") == "tool" and isinstance(c, str) and not c.startswith(MASKED):
            m["content"] = MASKED + f"(원래 {len(c)}자 — 필요하면 다시 부른다)"
            n += 1
    return n


MASKED = "[가림: 이전 관찰]"
