"""백엔드 연결 확인(ping)과 서빙 속도 측정(loadtest). 둘 다 OpenAI 형식만 쓴다.

  stewart ping -b cloud              /models, 짧은 답 1회, 툴콜 1회
  stewart loadtest -b cloud -c 1,8   동시 1·8개 스트리밍 요청: TTFT, 1개당 디코드 tok/s, 합계 tok/s
  stewart loadtest -b cloud --prompt-tokens 30000 -c 1   긴 문맥(프리필) 확인
"""

from __future__ import annotations

import json
import re
import statistics
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from .backend import BackendError, chat, models, stream_measure
from .config import Backend

PING_TOOL = [{"type": "function", "function": {
    "name": "add", "description": "두 정수를 더한다.",
    "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                   "required": ["a", "b"]}}}]

LOAD_PROMPT = ("파이썬으로 작은 할 일 관리 명령줄 프로그램을 설계하고 구현하라. 자료 구조, 명령 목록, 저장 형식, "
               "오류 처리, 시험 코드까지 자세히 설명하며 코드를 길게 써라.")

FILLER = ("이 문단은 긴 문맥 시험용 채움 글이다. 숫자와 낱말을 섞어 토큰 수를 늘린다. "
          "stewart context filler 0123456789 alpha beta gamma delta. ")


def ping(backend: Backend) -> dict:
    out: dict = {"backend": backend.public(), "ok": False, "checks": []}

    def check(name, fn):
        t0 = time.monotonic()
        try:
            detail = fn()
            out["checks"].append({"check": name, "ok": True, "s": round(time.monotonic() - t0, 2), **detail})
            return True
        except BackendError as e:
            out["checks"].append({"check": name, "ok": False, "s": round(time.monotonic() - t0, 2),
                                  "error": str(e), "body": (e.body or "")[:500]})
            return False

    def c_models():
        names, _ = models(backend)
        return {"models": names, "model_listed": backend.model in names}

    def c_answer():
        resp, dt = chat(backend, [{"role": "user", "content": "2 더하기 3은? 숫자만 답하라."}])
        msg = resp["choices"][0]["message"]
        return {"content": (msg.get("content") or "")[:200], "usage": resp.get("usage")}

    def c_tool():
        resp, dt = chat(backend, [{"role": "user", "content": "add 도구로 40과 2를 더하라."}], PING_TOOL)
        msg = resp["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        fn = (calls[0].get("function") or {}) if calls else {}
        return {"tool_called": bool(calls), "name": fn.get("name"), "arguments": fn.get("arguments"),
                "content": (msg.get("content") or "")[:200]}

    ok = check("models", c_models)
    ok = check("answer", c_answer) and ok
    ok = check("tool_call", c_tool) and ok
    tc = next((c for c in out["checks"] if c["check"] == "tool_call"), {})
    out["ok"] = ok and bool(tc.get("tool_called"))
    return out


def _metrics(backend: Backend, timeout: float = 10) -> list[str]:
    """vLLM /metrics 에서 캐시·요청 지표 줄만(없거나 막혀 있으면 빈 목록)."""
    root = re.sub(r"/v1/?$", "", backend.base_url)
    req = urllib.request.Request(root + "/metrics", headers={"Authorization": f"Bearer {backend.api_key()}"}
                                 if backend.api_key() else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception:
        return []
    pat = re.compile(r"^vllm:(kv_cache_usage_perc|gpu_cache_usage_perc|num_requests_running|num_requests_waiting|"
                     r"num_preemptions_total|prompt_tokens_total|generation_tokens_total)\b")
    return [l for l in text.splitlines() if pat.match(l)]


def loadtest(backend: Backend, concurrencies: list[int], max_tokens: int, rounds: int, prompt_tokens: int,
             runs_dir: Path, ignore_eos: bool = False) -> dict:
    """동시 c개를 한꺼번에 보내 한 묶음으로 잰다(rounds번). 백엔드 동시 상한은 무시한다(측정 목적)."""
    if prompt_tokens > 0:
        # 한국어·숫자 섞인 채움 글: 대략 1문단 ≈ 45토큰으로 어림하고, 서버가 센 prompt_tokens 를 기록에 남긴다.
        filler = FILLER * max(1, prompt_tokens // 45)
        content = filler + "\n\n위 글은 무시하고, 다음을 하라: " + LOAD_PROMPT
    else:
        content = LOAD_PROMPT
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = Path(runs_dir) / f"loadtest-{stamp}-{backend.name}"
    d.mkdir(parents=True)
    result = {"backend": backend.public(), "max_tokens": max_tokens, "rounds": rounds, "ignore_eos": ignore_eos,
              "prompt_tokens_target": prompt_tokens, "started": datetime.now().isoformat(timespec="seconds"),
              "metrics_before": _metrics(backend), "levels": []}
    for c in concurrencies:
        for rnd in range(1, rounds + 1):
            res: list[dict] = [{} for _ in range(c)]

            def one(i):
                # 같은 글을 보내면 접두어 캐시가 맞으므로 앞에 번호를 붙여 조금씩 다르게 한다
                msgs = [{"role": "user", "content": f"[{i}-{rnd}-{time.time_ns()}] " + content}]
                try:
                    res[i] = stream_measure(backend, msgs, max_tokens, use_slot=False,
                                            extra={"ignore_eos": True} if ignore_eos else None)
                except BackendError as e:
                    res[i] = {"error": str(e), "body": (e.body or "")[:300]}

            t0 = time.monotonic()
            ths = [threading.Thread(target=one, args=(i,)) for i in range(c)]
            for t in ths:
                t.start()
            for t in ths:
                t.join()
            wall = time.monotonic() - t0
            good = [r for r in res if "error" not in r]
            toks = sum(r["completion_tokens"] for r in good)
            per = [r["decode_tok_per_s"] for r in good if r.get("decode_tok_per_s")]
            ttft = [r["ttft_s"] for r in good if r.get("ttft_s") is not None]
            lvl = {"concurrency": c, "round": rnd, "ok": len(good), "errors": c - len(good), "wall_s": round(wall, 2),
                   "completion_tokens": toks, "aggregate_tok_per_s": round(toks / wall, 1) if wall else None,
                   "per_request_decode_tok_per_s_median": round(statistics.median(per), 1) if per else None,
                   "ttft_s_median": round(statistics.median(ttft), 2) if ttft else None,
                   "ttft_s_max": round(max(ttft), 2) if ttft else None,
                   "prompt_tokens": sorted({r.get("prompt_tokens") for r in good if r.get("prompt_tokens")}),
                   "requests": res}
            result["levels"].append(lvl)
            print(json.dumps({k: v for k, v in lvl.items() if k != "requests"}, ensure_ascii=False))
    result["metrics_after"] = _metrics(backend)
    result["finished"] = datetime.now().isoformat(timespec="seconds")
    (d / "loadtest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [f"# loadtest — {backend.name} {backend.model} ({backend.precision})\n",
          f"- 시작 {result['started']}, max_tokens {max_tokens}, 프롬프트 목표 {prompt_tokens or '짧음'}\n",
          "| 동시 | 회 | 성공 | 벽시계 초 | 생성 토큰 | 합계 tok/s | 1개당 디코드 tok/s(중앙) | TTFT 중앙/최대 | 프롬프트 토큰 |",
          "|---|---|---|---|---|---|---|---|---|"]
    md += [f"| {l['concurrency']} | {l['round']} | {l['ok']}/{l['concurrency']} | {l['wall_s']} | {l['completion_tokens']} | "
           f"{l['aggregate_tok_per_s']} | {l['per_request_decode_tok_per_s_median']} | {l['ttft_s_median']}/{l['ttft_s_max']} | "
           f"{l['prompt_tokens']} |" for l in result["levels"]]
    if result["metrics_after"]:
        md += ["", "vLLM 지표(끝난 뒤):", "```", *result["metrics_after"], "```"]
    (d / "loadtest.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    result["dir"] = str(d)
    return result


def podstatus(backend: Backend, port: int, runs_dir: Path, timeout: float = 10) -> dict:
    """파드 감시기의 읽기 전용 상태(/status)를 읽어 runs/pod-status.jsonl 에 한 줄 덧붙인다(파드 시간·VRAM 기록)."""
    from urllib.parse import urlsplit
    u = urlsplit(backend.base_url)
    url = f"{u.scheme}://{u.hostname}:{port}/status"
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "backend": backend.name, "url": url}
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            rec["status"] = json.loads(r.read().decode("utf-8", "replace"))
        rec["ok"] = True
    except Exception as e:
        rec.update(ok=False, error=f"{type(e).__name__}: {e}"[:300])
    Path(runs_dir).mkdir(parents=True, exist_ok=True)
    with (Path(runs_dir) / "pod-status.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec
