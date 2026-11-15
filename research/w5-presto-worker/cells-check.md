# 창 5 칸 다섯 — 작업자 첫 요청 가짜 서버 대조(판 141, 판 138 대조)

> 원래 기록: 평가 담당이 연구 담당·총괄에게 낸 대조 보고(2026-11-14 22:52)와 덧붙임(22:55), 바로잡음(11-15 11:5x). 숫자·sha 는 그대로 옮겼다.
> 꼴: 걸기 전 점검(판정 아님). **1–5절은 본 차례와 다른 프로필(code)로 돌았다.** 6절에서 본 차례 프로필(chat)로 다시 봤고 결론은 같았지만, 1–5절에서 "같다"고 적은 지시문·도구 해시(367bf532… · 777c5454…)는 본 차례 꼴의 해시가 아니었다. 이 잘못도 그대로 둔다([../lessons.md](../lessons.md)).
> 실행 조건: 엔진 판 141(f1b0224) 사본과 판 138(2f991c2) 사본, 평가기 rq1.1(얼린 폴더), 로컬 가짜 서버. 모델 호출 없음, 돈 0.

**맨 위 요약**
- 판 141(f1b0224) 사본으로 칸 다섯의 **작업자** 첫 요청 본문을 받아 봤다. 칸 정의대로 나왔다.
- 작업자 지시문(system)과 도구 설명이 판 138(2f991c2) 점검 때와 **바이트가 같다**. system sha 367bf53270f4…(2,450자), tools sha 777c5454c853…(6개). (6절: 본 차례 프로필 chat 으로는 c6d6030c454e…(4,031자) · 56758cdb06ef…(7개)이고, 일곱 칸이 모두 같다.)
- 덧붙여 알아낸 것이 하나 있다. 판 141 의 자리 안내(STEWART_PLACE_HINTS)는 **메인 chat 쪽에만** 붙고, 읽기 작업자 요청에는 닿지 않는다.
  - 켬(h1)에서도 작업자 system·tools 해시가 끔과 같았다.
  - 코드도 같은 말을 한다. chat.py:471 이 `self.slim is None` 일 때만 켜고, 도구 설명 덧붙임은 chat 도구 쪽이다.
  - 그러니 주 칸의 `STEWART_PLACE_HINTS=0` 고정은 해가 없다. 다만 창 5 주 칸에서는 이 값이 결과를 바꾸지 않는다.
- 설치(rq1.1 EVALS · evalq path worker · ENV_ALLOW)는 아직 들어오지 않았다. 설치판 evalq 에 rq1.1 줄이 없다. 대기열 생성기는 설치 뒤에 만든다.
- 덧붙임(5절): presto 한 칸 표와 세 칸 표의 읽기 작업자 요청은 작업 폴더 경로 한 줄만 다르다.

## 1. 실행
- 가짜 서버는 fake_rq_w5.py(d49a09b7…)다. fake_rq.py 에 system·tools·첫 user 의 sha256 과 글자 수 칸만 더했다. 글은 남기지 않는다.
- 평가기는 rq1.1(얼린 폴더)이고 `run_eval.py --path worker --tasks C001 --reps 1 --max-steps 6 --backend cloud --instance fake --machine fake` 로 돌렸다. STEWART_BACKENDS 는 칸마다 -fake 표를 줬다.
- 엔진 사본:
  - 판 141(f1b0224). 오늘 git archive 로 풀고 `uv sync --frozen` 했다.
  - 판 138(2f991c2), 첫째·둘째 점검 때 사본.
- 실행 기록: `eval-rq1.1-20261114-2250xx–2251xx-smoke-w5f-*` 폴더.
  - 같은 이름의 앞 7 폴더는 첫 시도다. --instance 를 빠뜨려 rc 2 로 끝났고 결과가 없다.

## 2. 작업자 첫 요청(칸마다 요청 2개 = 도구 한 번 + 끝맺음)
| 칸 | 엔진·자리 안내 | chat_template_kwargs | reasoning_effort(맨 위) | max_tokens · 온도 | system sha · 글자 | tools sha · 개수 | worker meta |
|---|---|---|---|---|---|---|---|
| W0 | 판 138 | enable_thinking true | 없음 | 16384 · 0.2 | 367bf532… · 2450 | 777c5454… · 6 | depth 1, tier 없음 |
| W0 | 판 141 · 끔 | enable_thinking true | 없음 | 16384 · 0.2 | 367bf532… · 2450 | 777c5454… · 6 | depth 1, tier 없음 |
| W0 | 판 141 · 켬(참고) | enable_thinking true | 없음 | 16384 · 0.2 | 367bf532… · 2450 | 777c5454… · 6 | depth 1, tier 없음 |
| W-ex | 판 141 · 끔 | enable_thinking true | 없음 | 16384 · 0.2 | 같음 | 같음 | tier presto, budget_source tier, think {} |
| W-med | 판 141 · 끔 | enable_thinking true, reasoning_effort medium | 없음 | 16384 · 0.2 | 같음 | 같음 | presto, effort 출처 tier |
| W-low | 판 141 · 끔 | enable_thinking true, reasoning_effort low | 없음 | 16384 · 0.2 | 같음 | 같음 | presto, effort 출처 tier |
| W-off | 판 141 · 끔 | enable_thinking **false** | 없음 | 16384 · 0.2 | 같음 | 같음 | presto, think false |

- W0 는 깊이 값이 없다(가장 깊게). moderato medium 이 새지 않는다. W0 표에는 등급 표가 없고, 요청에도 effort 가 없다.
- W-ex 의 문맥 창 24,000 은 요청 본문에 나오지 않는다(엔진 안 압축 예산). meta 의 `budget_source: tier` 와 표의 `budget = 24000` 로 확인했다.
- 첫 user 해시는 회차마다 다르다. 일하는 폴더 경로 같은 회차별 글이 들어 있어서다. 이 점검에서 비교할 칸이 아니다.

## 3. 칸 표(이번에 만든 것)
- 생성기는 make_w5_backends.py(70a931ce…)다. 두 번 돌려 같은 바이트였다.
  - 바탕은 dev-w3-tiers.toml(0f743c09…)이다.
  - W0(a8fe5ac3…)의 본문이 바탕의 [tiers.presto] 앞부분과 같은지 대 본다.
- W-ex 는 W0 본문에 **[tiers.presto] 한 칸**(budget 24000 · max 2 · slim · default_for read)만 더한 것이다. 설계 "예시 presto 한 칸"을 따랐다.
  - moderato·largo 는 넣지 않았다. 창 4 의 p 칸 표(셋 다 있음)와 여기가 다르다. 연구 담당이 다르게 뜻했으면 알려 달라.
  - W-med · W-low · W-off 는 W-ex 의 presto 끝에 생각 칸만 더한 것이다(창 4 와 같은 줄).

| 표 | sha256 |
|---|---|
| dev-w5-w0.toml | a8fe5ac3e1a3a1032644d5b8208efe303dd9e65c07c4305504ccfc601dec2600 |
| dev-w5-wex.toml | 9e8f74a2921e36e057fadc5eadd122f61c27555cf4c79e36e51fcd8e1f9cce4a |
| dev-w5-wmed.toml | 3cc74af9d90446ce1d62cfa64470acc5cff32a69a7e43352749170d0ada66e9f |
| dev-w5-wlow.toml | 1cdce81cb6a6e397e6dc8acdde11fef0b51f5ca624b58651c36e1bb47a12b9c5 |
| dev-w5-woff.toml | a880f44186f36ba93f48315ff9f900484638ea1867283f32741c4c720f04fee6 |

(-fake 판은 base_url 만 로컬 가짜 서버 주소로 바꾼 것이다. 가짜 서버 전용이다.)

## 4. 남은 것(설치 뒤)
- 대기열 생성기를 만든다. 작업자 꼴 차례에 env STEWART_PLACE_HINTS=0, 차선 균형 ±1 이고, 개발 1,200 · 실제 300 · 탐색 ② 이다. 설치판 evalq 로 validate 한 뒤 sha 를 적는다.
- 걸기 전에 설치판 evalq 실행 대행으로 칸 하나를 다시 받아, 위 표와 같은지 본다.

## 5. 덧붙임(22:55, 연구 담당 청): presto 한 칸 표 대 세 칸 표 — 읽기 작업자 요청 본문
- 견준 두 표:
  - W-ex(dev-w5-wex, presto 만)
  - 창 4 p0 표(dev-w4-p0, presto·moderato·largo). presto 칸과 backends 칸은 W-ex 와 같은 값이다(tomllib 로 대 봄).
- 둘 다 판 141, STEWART_PLACE_HINTS=0, C001 로 돌렸다. 가짜 서버(임시 폴더의 fake_dump.py = fake_rq_w5 + 본문 통째 저장)로 작업자 요청 둘의 본문을 통째로 받았다.
- 결과: **작업 폴더 경로 한 곳만 다르다.** 첫 user 메시지의 "작업 폴더: …/<회차>/<과제>-<반복>" 줄이다(이번에는 과제 C001, 반복 1).
  - 그 경로를 `<WD>/` 로 가리면 두 요청 모두 바이트가 같다.
    - 첫 요청: 7,362 바이트, 가린 sha 098f56154468febd…
    - 둘째 요청: 7,776 바이트, 7a0b9a864d2f6149…
  - 경로는 회차마다 새로 만드는 폴더다. 같은 표로 두 번 돌려도 다르다.
- worker meta 도 둘이 같다(depth 1, tier presto, budget_source tier, think {}).
- 그러니 읽기 작업자에게는 moderato·largo 칸이 있든 없든 요청이 같다.

## 6. 바로잡음(2026-11-15 11:5x): chat 프로필로 다시 본 대조
- §1–§5 는 run_eval 기본 프로필 code 로 돌았다. 본 차례는 chat 이다. 그래서 chat 으로 다시 돌렸고, 결론은 둘 다 같다.
- 칸 일곱 대조(판 138 W0 · 판 141 W0 끔·켬 · W-ex·med·low·off):
  - 작업자 system 은 c6d6030c454e…(4,031자), tools 는 56758cdb06ef…(7개)다. 일곱이 모두 같다.
  - 생각 칸은 §2 표와 같다.
- presto 한 칸 대 세 칸(판 141, 끔, C001): 작업 폴더 경로 한 줄만 다르다. 가리면 바이트가 같다.
  - 첫 요청: 14,834 바이트, 가린 sha 017b6513daab0d70…
  - 둘째 요청: 15,258 바이트, 42ff024ea04f7a71…

> (2026-11-15 13:1x 덧붙임) 이 문서가 근거로 든 직접 길 실행 기록 일부(부모 chat 의 messages.json·events.jsonl)와 가짜 서버 본문 덤프는 사람의 실사용 설정 값이 섞여, 사람의 결정으로 11-15 에 지웠다(지운 목록과 보고는 비공개 운영 기록). 결과 줄(results.jsonl)·요약(summary.*)·판정은 남았고, 요약은 이 문서다.
