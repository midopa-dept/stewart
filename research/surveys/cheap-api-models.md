# 값싼 API 모델 조사 — 코딩 에이전트 실행기에 별도 인스턴스로 붙일 후보

> 원래 기록: 연구 쪽 조사 보고(비공개 운영 기록, 2026-11-11). 가격·점수 숫자는 원문 그대로 옮겼다.
> 꼴: 조사(판정 없음). 우리가 잰 값은 없고, 품질 숫자는 모두 남이 잰 공개 값이다.
> 실행 조건: 해당 없음(조사만). 확인 날짜 2026-11-11. 우리 서빙 모델과 같은 판을 파는 API 줄(Groq·Cerebras)은 판 이름을 가리고 "원본 27B"로 적었고, 같은 세대의 Alibaba flash 판도 판 이름을 가렸다.

확인 날짜: 2026-11-11 (모든 가격·문서는 이날 웹에서 직접 읽음. 출처는 맨 아래)
표기: [모름] = 공식 자료에서 확인 못 함, [추정] = 내가 계산·짐작, [제3자] = 공식이 아닌 집계·블로그 값

## 5줄 요약

1. **"DeepSeek 4.1 flash"는 실제로 있다.** 공식 모델 ID 는 `deepseek-flash`(실제 판 DeepSeek-V4.1-Flash-1017), 문맥 1M, 캐시 적중 입력 $0.003–0.006/M, 미적중 $0.15–0.30/M, 출력 $0.60–1.20/M(한산 시간/혼잡 시간). 캐시는 접두 일치 **자동**, 생각은 `thinking: {"type":"disabled"}` 로 끈다.
2. 기준 쓰임새(30단계·단계당 입력 20k·80% 캐시·출력 400)로 **작업당 $0.027(한산)–$0.053(혼잡), 월 600작업 $16–32**. 같은 값대 경쟁자는 OpenAI `gpt-6-luna`($0.023/작업, 월 $14), Anthropic Haiku 5.5(캐시 쓰면 $0.026, OpenAI 호환 경로는 캐시가 안 돼 $0.066), MiniMax-M3.1-Flash($0.032), GLM-5.3-Flash($0.038).
3. 품질 근거(Vals AI, 같은 실행기로 잰 값): Terminal-Bench 4.0 에서 GLM-5.3-Flash 37.4% · Haiku 5.5 36.4% · **DeepSeek V4.1 Flash 1017 31.8%** · MiniMax-M3.1-Flash 25.3% · GPT-6 Luna 13.6% · GPT-5.4 mini 2.5%. 값 대비로는 DeepSeek flash 가 가장 앞선다(시험당 비용 $0.54 대 Haiku $3.12, GLM flash $1.22).
4. 데이터 정책은 갈린다. DeepSeek 는 서버가 중국, API 입력의 학습 사용 여부와 옵트아웃이 약관에 **명시돼 있지 않다**. OpenAI·Anthropic·Google(유료)은 "기본으로 학습에 안 씀"을 명시한다. Z.ai(GLM)는 싱가포르 법인·싱가포르 처리, API 내용 미저장이라고 DPA 에 적는다.
5. 무료로는 Z.ai `GLM-4.7-Flash`(가격표에 Free, 동시 1개[제3자])와 Gemini 무료 등급(입력이 제품 개선에 쓰임)이 있다. OpenRouter `:free` 는 하루 50회(크레딧 $10 이상 사면 1000회)라 30단계 작업에는 모자란다.

## 권고

| 항목 | 고른 것 | 까닭 |
|---|---|---|
| (가) 가장 싼데 쓸 만한 것 | **DeepSeek `deepseek-flash` 직접 API** | 캐시 적중 값이 후보 중 가장 낮고($0.003–0.006/M) 캐시가 자동이라 실행기를 안 고쳐도 된다. 생각을 끌 수 있어 출력도 짧다. 작업당 $0.027–0.053. Terminal-Bench 4.0 31.8%로 같은 값대에서 상위. 사람(제품 주인)이 말한 그 모델이다. |
| (나) 값 대비 품질 | **DeepSeek `deepseek-flash`** (품질을 더 원하면 Claude Haiku 5.5) | 같은 벤치에서 Haiku 5.5(36.4%)·GLM-5.3-Flash(37.4%)가 조금 높지만, Haiku 는 OpenAI 호환 경로에서 프롬프트 캐시가 **지원되지 않아** 작업당 $0.066(월 $40)이 되고, 캐시를 쓰려면 Anthropic 고유 API 나 OpenRouter 의 `cache_control` 을 써야 한다. GLM-5.3-Flash 는 생각을 끌 수 없고 Vals 측정에서 시험당 1시간대로 느리다. |
| (다) 데이터 정책이 무난한 것 | **OpenAI `gpt-6-luna`** (대안 Haiku 5.5) | API 입력을 기본으로 학습에 안 씀, 남용 감시 기록 최대 30일, 캐시 자동, 값도 $0.10/$0.01/$0.50 로 가장 싼 축(작업당 $0.023). 단 Terminal-Bench 4.0 은 13.6%로 낮고, **Chat Completions 에서 함수 호출은 `reasoning_effort: none` 일 때만** 된다고 모델 문서에 적혀 있다. 민감한 코드를 넣는 작업은 이쪽, 아니면 (가). |
| (라) 무료 | **Z.ai `glm-4.7-flash`** | 공식 가격표에 입력·캐시·출력 모두 Free. OpenAI 호환(`https://api.z.ai/api/paas/v4/`). 한도는 공식 문서에서 못 찾았고 제3자 자료는 "동시 1개"라고 한다. EXAONE 처럼 "느려도 공짜" 자리에 맞는다. 품질 근거는 옛 판(GLM 4.7 유료판 SWE-bench Verified 69.4%, Terminal-Bench 2.0 38.2%)뿐이고 Flash 판 점수는 [모름]. |

### 직접 API 대 중계(OpenRouter 등)

- **값**: 직접이 싸다. OpenRouter 는 추론 값에 웃돈은 없지만 크레딧을 살 때 수수료가 붙는다(5.5%, 최소 $0.80 [제3자] — 공식 FAQ 는 숫자가 빈칸으로 나옴). $5 를 사면 최소 수수료 때문에 실질 16% [제3자].
- **캐시 할인 전달**: OpenRouter 문서상 DeepSeek·OpenAI·Grok·Moonshot·Z.AI 는 자동 캐시가 그대로 전달되고, Anthropic·Qwen 은 `cache_control` 을 넣어야 한다. 캐시를 살리려고 같은 제공자로 붙여 주는 "sticky routing"이 있다(60분 무활동 시 풀림). 다만 DeepSeek V4.1 Flash 는 OpenRouter 에 제공자가 30곳 넘게 걸려 있고(DeepInfra $0.112/$0.336 등) 제공자마다 캐시 값이 달라, 직접 API 의 $0.003 적중가를 그대로 받는다는 보장은 [모름].
- **편의**: 키 하나로 여러 모델을 바꿔 가며 시험하기에는 OpenRouter 가 편하다. 프롬프트·응답은 기본으로 기록하지 않고, 학습에 쓰는 제공자로는 설정을 켜지 않으면 보내지 않는다.
- **결론**: 한 모델을 정해 오래 쓸 때는 직접 API(DeepSeek 직접). 후보 서너 개를 같은 과제로 비교하는 시험 기간에만 OpenRouter 에 $10 을 넣어 쓰는 것이 합리적이다($10 이상 사면 무료 모델 하루 한도도 50→1000회).

### 붙일 때 주의(실행기 쪽)

- DeepSeek: 생각을 켠 채 `tools` 를 쓰면 **이후 모든 요청에 `reasoning_content` 를 되돌려 보내야 하고, 안 보내면 400** 이다. 실행기가 이 필드를 보존하지 않으면 생각을 끄는 것이 안전하다(값도 줄어든다). 캐시 사용량은 `usage.prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` 로 나온다(OpenAI 의 `prompt_tokens_details.cached_tokens` 와 이름이 다름).
- DeepSeek 혼잡 시간은 평일 UTC 01:00–04:00, 06:00–10:00 = **한국 시간 평일 10–13시, 15–19시**(값 2배). 그 밖과 주말·중국 공휴일은 한산 값.
- MiniMax-M3.1-Flash 와 GLM-5.3-Flash 는 생각을 **끌 수 없다**(M3.1-Flash 는 끄면 400). 출력 토큰이 표의 어림보다 늘어난다.
- Gemini 는 3 세대 이상에서 생각을 끌 수 없다고 OpenAI 호환 문서에 적혀 있다(2.5 계열만 `reasoning_effort: "none"`).
- Haiku 5.5 는 요청 하나의 프롬프트가 100k 토큰을 넘으면 그 요청 전체가 5배 값($0.50/$0.05/$2.50)이 된다.

## 후보 표 1 — 값·문맥·캐시

값은 백만 토큰당 USD, 공식 가격표 기준.

| # | 후보(모델 ID) | 입력 | 캐시 적중 | 출력 | 문맥 | 캐시 방식 |
|---|---|---|---|---|---|---|
| 1 | DeepSeek `deepseek-flash` (V4.1-Flash-1017) | 0.15 한산 / 0.30 혼잡 | 0.003 / 0.006 | 0.60 / 1.20 | 1M (출력 최대 384K) | 자동, 접두 일치. 최소 토큰 명시 없음, 보관 "몇 시간에서 며칠", 적중 보장 없음 |
| 2 | OpenAI `gpt-6-luna` | 0.10 | 0.01 | 0.50 | 1.05M (272K 초과 요청은 0.20/0.02/0.75) | 자동(1024토큰 이상 [OpenRouter 문서 기준]) |
| 3 | OpenAI `gpt-5.6-luna` | 0.20 | 0.02 | 1.20 | [모름] | 자동 |
| 4 | OpenAI `gpt-5.4-nano` / `gpt-5.4-mini` / `gpt-5-nano` | 0.20 / 0.75 / 0.05 | 0.02 / 0.075 / 0.005 | 1.25 / 4.50 / 0.40 | [모름] | 자동 |
| 5 | Anthropic `claude-haiku-5-5` | 0.10 (100k 초과 요청 0.50) | 0.01 (0.05) · 캐시 쓰기 5분 0.125 | 0.50 (2.50) | 1M, 출력 128k [제3자] | **명시**(`cache_control`). OpenAI 호환 경로는 캐시 미지원 |
| 6 | Anthropic `claude-haiku-4-5-20251001` | 1.00 | 0.10 · 쓰기 1.25 | 5.00 | 200k [제3자] | 명시 |
| 7 | Google `gemini-3.5-flash-lite` | 0.30 | 0.03 (+보관 $1.00/M·시간) | 2.50 | [모름] | 암시 캐시(2.5 이후, OpenRouter 문서 기준)·명시 캐시 둘 다. 공식 세부 [모름] |
| 8 | Google `gemini-3.1-flash-lite` / `gemini-2.5-flash-lite` | 0.25 / 0.10 | 0.025 / 0.01 | 1.50 / 0.40 | [모름] | 위와 같음. `gemini-2.5-flash` 는 12-01 부터 3.5-flash-lite 로 넘어감 |
| 9 | Google `gemini-3.8-flash` | 0.75 (2027-01-01 부터 1.50) | 0.075 (0.15) | 3.75 (7.50) | [모름] | 위와 같음 |
| 10 | Z.ai `glm-5.3-flash` | 0.15 | 0.03 | 0.50 | 1M (출력 128K) | 자동(OpenRouter 문서 기준) |
| 11 | Z.ai `GLM-4.7-FlashX` | 0.07 | 0.01 | 0.40 | [모름] | 자동 |
| 12 | Z.ai `GLM-4.7-Flash`, `GLM-4.5-Flash` | **무료** | 무료 | 무료 | [모름] | — |
| 13 | MiniMax `MiniMax-M3.1-Flash` | 0.15 | 0.015 | 0.60 | 1M | [모름] (캐시 쓰기 값 표에 없음) |
| 14 | MiniMax `MiniMax-M3` | 0.30 (상시 50% 할인가, 512k 초과 0.60) | 0.06 | 1.20 | 1M | [모름] |
| 15 | Alibaba Qwen 최신 flash 판 (싱가포르, 판 이름 가림) | 0.15 | 암시 적중 = 입력의 20%(0.03 [추정]), 명시 적중 10%·생성 125% | 0.47 | 1M | 암시(자동, 1024토큰 이상, 보장 없음) 또는 명시(5분) |
| 16 | Alibaba `qwen3.7-flash` (싱가포르) | 0.03 (요청 32K 이하) / 0.10 (32K–256K) / 0.20 (1M 까지) | 위 비율 | 0.13 / 0.40 / 0.80 | 1M | 위와 같음 |
| 17 | Mistral Small 4 | 0.15 [제3자, docs.mistral.ai 10-17 인용] | 0.015 [제3자] | 0.60 [제3자] | [모름] | [모름]. 공식 가격 페이지는 "캐시 입력 최대 90% 할인"만 적음 |
| 18 | xAI `grok-build-0.1` / `grok-4.3` | 1.00 / 1.25 | 0.20 / 0.20 | 2.00 / 2.50 | 256k / 1M | 자동(OpenRouter 문서 기준) |
| 19 | Moonshot `kimi-k3` | 3.00 [제3자] | 0.30 [제3자] | 15.00 [제3자] | 1,048,576 | 자동. 공식 가격표는 숫자가 안 읽혀 [모름] |
| 20 | Groq `openai/gpt-oss-120b` / 원본 27B(미리보기, 판 이름 가림) | 0.15 / 0.80 | [모름] | 0.60 / 4.00 | 131k | [모름] |
| 21 | Cerebras `gpt-oss-120b` / 원본 27B(판 이름 가림) | 0.35 / 0.99 [제3자] | [모름] | 0.75 / 1.49 [제3자] | 131k / 128k (유료) | [모름] |
| 22 | OpenRouter 경유 DeepSeek V4.1 Flash | 제공자별 0.112–0.45 (DeepInfra 0.112, Together·Fireworks 0.30 등) | 제공자별 [모름] | 0.336–1.80 | [모름] | 제공자 따라 |

참고: xAI 공식 모델 목록에는 지금 "fast" 이름의 글 모델이 없다(가장 싼 것이 `grok-build-0.1`). 예전 Grok 4 Fast / 4.1 Fast 는 벤치 표에만 남아 있다. Kimi K2 계열은 10-31 부터 새 가입자에게 닫혔고 12월 말 종료 예정이다.

## 후보 표 2 — 기능·속도·접속

| # | 후보 | 도구 호출 | 스트리밍·usage | 속도(tok/s) | 생각 끄기 | base_url | 속도 제한 | 결제 최소 |
|---|---|---|---|---|---|---|---|---|
| 1 | DeepSeek flash | 지원. 생각 켠 채 쓰면 `reasoning_content` 되돌려 보내기 필수(아니면 400) | 지원. 대기 중 `: keep-alive` 주석이 섞여 옴 | 직접 API [모름] (Artificial Analysis 는 제3 제공자만 측정, 첫 답 토큰까지 8–59초) | 가능: `thinking: {"type":"disabled"}` | `https://api.deepseek.com` (Anthropic 형식 `/anthropic` 도 있음) | 동시 2500, RPM 한도 없음, 10분 안에 추론 시작 못 하면 끊김 | [모름] |
| 2 | gpt-6-luna | 지원. **Chat Completions 에서는 `reasoning_effort: none` 일 때만 함수 호출** | 지원 | [모름] | 가능: `reasoning_effort: none` | `https://api.openai.com/v1` | 첫 등급 2,000,000 TPM · 5,000 RPM | [모름] |
| 3–4 | gpt-5.6-luna, 5.4-nano/mini | 지원 | 지원 | GPT-5.4 nano 155–170 (Artificial Analysis) | [모름] | 위와 같음 | [모름] | [모름] |
| 5 | Haiku 5.5 | 지원. OpenAI 호환에서는 `strict` 무시, `reasoning_effort` 무시 | 지원(`stream_options` 포함). `prompt_tokens_details` 는 늘 빈값 | 156–235 (Artificial Analysis, effort 따라) | 기본 켜짐. 호환 경로에서 끄는 법 [모름] | `https://api.anthropic.com/v1/` (호환 계층은 "시험·비교용, 운영용 아님"이라고 문서에 적힘) | Messages API 한도와 같음, 숫자 [모름] | [모름] |
| 7–9 | Gemini Flash-Lite/Flash | 지원 | 지원(`stream_options.include_usage`) | 3.5 Flash-Lite 344–490 (Artificial Analysis) | 2.5 계열만 가능, 3 이상은 **불가** | `https://generativelanguage.googleapis.com/v1beta/openai/` | 문서에 숫자 없음(AI Studio 콘솔에서 확인) | 무료 등급 있음 |
| 10 | glm-5.3-flash | 지원 | [모름] | Flash [모름], FlashX 는 200(공식) | **불가**(`thinking.type` 이 enabled 뿐) | `https://api.z.ai/api/paas/v4/` | [모름] | [모름] |
| 12 | GLM-4.7-Flash(무료) | 지원 [제3자] | [모름] | [모름] | [모름] | 위와 같음 | 동시 1 [제3자] | 없음 |
| 13 | MiniMax-M3.1-Flash | 지원(`tool_calls`·`reasoning_content` 통째로 되돌려 보내라고 안내) | [모름] | [모름] (M3 는 98, Artificial Analysis) | **불가**(끄면 400). M3 는 가능 | `https://api.minimax.io/v1` | [모름] | [모름] |
| 15–16 | Qwen 최신 flash 판 / qwen3.7-flash | [모름] (이번에 문서 확인 안 함) | [모름] | [모름] | [모름] | `https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1` | [모름] | 무료 100만 토큰(개통 후 90일) |
| 17 | Mistral Small 4 | [모름] | [모름] | [모름] | [모름] | [모름] | [모름] | 2026-08 부터 API 는 사업자 전용이라는 말이 있음 [제3자] |
| 20 | Groq gpt-oss-120b | [모름] | [모름] | 500 (공식), 원본 27B 450 | [모름] | [모름] (이번에 확인 안 함) | 250K TPM · 1K RPM | [모름] |
| 21 | Cerebras | [모름] | [모름] | gpt-oss-120b 약 3000, 원본 27B 약 1850 (공식) | [모름] | [모름] | 무료 등급 종료, 결제수단 등록 시 $5(30일) | [모름] |
| 22 | OpenRouter | 제공자 따라(Artificial Analysis: V4.1 Flash 를 올린 17곳 모두 함수 호출 지원) | 지원 | 제공자 따라 | 모델 따라 | [모름] (이번에 문서에서 주소를 읽지 않음) | `:free` 분당 20, 하루 50(크레딧 10 이상 구매 시 1000) | 수수료 5.5%·최소 $0.80 [제3자] |

## 후보 표 3 — 데이터 정책

| 후보 | 입력을 학습에 쓰는가 | 옵트아웃 | 보관 | 서버·관할 |
|---|---|---|---|---|
| DeepSeek | 개방 플랫폼 약관(2026-04-29 시행)에는 **언급 없음**. 개인정보 처리방침(2026-05-06)은 "기술 학습·개선에 쓸 수 있다"고 하나 API 에 적용되는지 본문으로는 불분명 | 앱에는 설정 있음. API 용은 [모름] (제3자 자료: 콘솔 스위치 없음, 메일 요청) | 기간 명시 없음 | 서버 중국, 준거법 중국, 관할 항저우 |
| OpenAI | 기본으로 안 씀(직접 동의해야 씀) | 해당 없음 | 남용 감시 기록 최대 30일. 무보관(ZDR)은 승인제 | 미국 등. 한국은 저장만 지역 지정 가능 |
| Anthropic | 상용 제품 입력·출력은 기본으로 안 씀(피드백 보낸 것은 예외) | 해당 없음 | 일반 보관 기간은 이번에 확인 못 함 [모름] | [모름] |
| Google Gemini | **무료 등급: 씀**(사람 검토 가능, 계정과 분리). 유료: 안 씀 | 유료로 바꾸는 것이 사실상 옵트아웃 | 유료는 "제한된 기간" 기록, 일수 없음 | 사용 가능 지역에서만 |
| Z.ai (GLM) | DPA 는 API 제공·지원 목적으로만 처리한다고 적음(학습 언급 없음). 무료 모델에 같은 조건인지 [모름] | [모름] | "API 내용은 서버에 저장하지 않는다"(DPA 4(b)) | 싱가포르 법인, 주로 싱가포르 처리 |
| MiniMax | [모름] | [모름] | [모름] | [모름] |
| Alibaba Model Studio | [모름] | [모름] | [모름] | 싱가포르 리전 선택 가능 |
| OpenRouter | 자체로는 프롬프트·응답 기록 안 함(동의하면 1% 할인). 학습하는 제공자로는 설정을 켜야만 보냄 | 설정으로 조절 | 메타데이터만 | 실제 처리는 고른 제공자 정책을 따름 |
| Moonshot·Mistral·xAI·Groq·Cerebras | [모름] (이번에 확인 안 함) | | | |

## 어림 비용

기준: 한 작업 = 30단계 × 입력 20k = 입력 600k(그중 480k 캐시 적중, 120k 미적중), 출력 30 × 400 = 12k. 월 = 하루 20작업 × 30일 = 600작업.
식: 작업당 = 0.48 × 적중가 + 0.12 × 입력가 + 0.012 × 출력가. 모두 [추정] — 생각 토큰(끌 수 없는 모델), 캐시 미스, 100k·272k 초과 요청 할증은 넣지 않았다.

| 후보 | 작업당 USD | 월 USD | 캐시 | 비고 |
|---|---|---|---|---|
| GLM-4.7-Flash | 0 | 0 | — | 무료, 한도 [모름] |
| qwen3.7-flash (모든 요청 32K 이하일 때) | 0.008 | 4.8 | 자동(암시 20% 가정) | 32K 넘는 요청은 3배 값 구간. 품질 근거 없음 |
| gpt-5-nano | 0.013 | 7.9 | 자동 | 옛 모델 |
| GLM-4.7-FlashX | 0.018 | 10.8 | 자동 | |
| gemini-2.5-flash-lite | 0.022 | 13.0 | 암시 | 옛 모델 |
| **gpt-6-luna** | **0.023** | **13.7** | 자동 | |
| Haiku 5.5 (고유 API, 캐시 사용) | 0.026 | 15.5 | 명시 | 미적중분을 캐시 쓰기 값 0.125 로 계산 |
| **deepseek-flash 한산** | **0.027** | **16.0** | 자동 | |
| MiniMax-M3.1-Flash | 0.032 | 19.4 | [모름] | 생각 못 끔 → 실제는 더 큼 |
| Mistral Small 4 | 0.032 | 19.4 | [모름] | 값이 [제3자] |
| Qwen 최신 flash 판 (암시 캐시) | 0.038 | 22.8 | 자동 | |
| glm-5.3-flash | 0.038 | 23.0 | 자동 | 생각 못 끔 → 실제는 더 큼 |
| gpt-5.6-luna | 0.048 | 28.8 | 자동 | |
| gpt-5.4-nano | 0.049 | 29.2 | 자동 | |
| **deepseek-flash 혼잡** | **0.053** | **32.0** | 자동 | 한국 평일 10–13시·15–19시 |
| gemini-3.1-flash-lite | 0.060 | 36.0 | 암시 | |
| Haiku 5.5 (OpenAI 호환, 캐시 없음) | 0.066 | 39.6 | 안 됨 | |
| OpenRouter→DeepInfra V4.1 Flash (캐시 없다고 가정) | 0.071 | 42.7 | [모름] | + 크레딧 수수료 |
| MiniMax-M3 | 0.079 | 47.5 | [모름] | |
| gemini-3.5-flash-lite | 0.080 | 48.2 | 암시 | 생각 못 끔 |
| Groq gpt-oss-120b (캐시 없음 가정) | 0.097 | 58.3 | [모름] | |
| gpt-5.4-mini | 0.180 | 108 | 자동 | |
| grok-build-0.1 | 0.240 | 144 | 자동 | |
| kimi-k3 | 0.684 | 410 | 자동 | 값이 [제3자]. 값싼 후보가 아님 |

캐시가 전혀 안 맞을 때의 deepseek-flash: 한산 $0.097, 혼잡 $0.194/작업 — 도구 정의·지시문이 매 단계 같은 바이트로 맨 앞에 오게 하는 것이 값의 대부분을 정한다.

## 코딩 에이전트 품질 근거

같은 기관(Vals AI)이 같은 실행기(Mini-SWE-agent)로 잰 값을 우선했다. 회사 자체 발표값은 따로 적었다.

| 모델 | Terminal-Bench 4.0 (Vals, 11-09 갱신) | 시험당 비용 | SWE-bench Verified (Vals, 9-01 로 보관 처리) | Terminal-Bench 2.0 (Vals, 6-04 로 보관) |
|---|---|---|---|---|
| GLM 5.3 Flash | 37.37% | $1.22 | 92.00% ($0.02, 시험당 1시간 4분) | — |
| Claude Haiku 5.5 | 36.36% | $3.12 | 없음(Anthropic 미발표라는 보도) | — (Haiku 4.5: 38.20%) |
| DeepSeek V4.1 Flash 1017 | 31.82% | $0.54 | 없음 (앞 판 V4 Flash 0731: 88.80%, $0.01) | — |
| MiniMax-M3.1-Flash | 25.25% | $0.73 | 없음 (M3: 75.00%) | M3 46.07% |
| DeepSeek V4.1 Flash(첫 판) | 19.70% | $0.50 | — | — |
| GPT-6 Luna | 13.64% | $0.35 | 없음 | — |
| GPT-5.6 Luna | 11.62% | $0.73 | 93.00% ($0.04) | — |
| GPT 5.4 Mini | 2.52% | $1.44 | 73.00% | 44.94% |
| GPT 5.4 Nano | 없음 | — | 69.80% | 39.89% |
| Gemini 3.5 Flash Lite | 없음 | — | 75.00% ($0.31) | — |
| Gemini 3.1 Flash Lite Preview | 없음 | — | 62.80% | 24.72% |
| Gemini 3.8 Flash | 19.70% | $8.65 | 80.00% | — |
| Qwen 3.5 Flash (3.7 flash·최신 flash 판은 없음) | 없음 | — | 64.40% | 24.72% |
| Mistral Small 4 | 없음 | — | 없음 | 16.85% |
| GPT OSS 120B | 없음 | — | 33.60% | 19.10% |
| Kimi K3 | 17.17% ($12.48) | | 93.40% | — |
| Grok 4.3 | 없음 | — | 71.40% | 43.45% |

- DeepSeek 자체 발표(V4.1 Flash): Terminal-Bench 2.1 90.6%, DeepSWE v1.1 74.2%, Terminal-Bench 4.0 31.2(10월 판은 42.3이라는 집계 사이트 값, Claude Code 실행기) [제3자 인용]. Vals 의 독립 측정은 Terminal-Bench 2.1 74.5% [제3자 인용]. 자체 발표값이 독립 측정보다 높다는 사용기 지적이 있다.
- Artificial Analysis 지능 지수(같은 날 읽음): Haiku 5.5(max) 36 · DeepSeek V4.1 Flash 1017 (max) 33 / (high) 29 · MiniMax-M3.1-Flash 29 · GLM-5.3-Flash 28 · MiniMax-M3 19 · Gemini 3.5 Flash-Lite 12 · Mistral Small 4 4 · gpt-oss-120b 4.
- Aider polyglot: 이번 조사에서 이 후보들의 최근 점수를 찾지 못했다 [없음].
- 한국어 대화 품질: 후보별 공개 근거를 찾지 못했다 [모름]. 붙인 뒤 자체 과제로 봐야 한다.
- SWE-bench Verified 와 Terminal-Bench 2.0 은 Vals 가 "포화"로 보관 처리했다. 지금 변별력이 있는 것은 Terminal-Bench 4.0 이다.

## 이번에 확인하지 못한 것

- DeepSeek·Z.ai·MiniMax 의 최소 충전 금액, DeepSeek 직접 API 의 생성 속도.
- Moonshot 공식 가격표 숫자(페이지 표가 읽히지 않음), Mistral 공식 모델별 가격(가격 페이지에 Large 만 나옴).
- Gemini 무료 등급의 모델별 한도(문서에는 없고 AI Studio 콘솔에만 있음), GLM-4.7-Flash 무료 한도의 공식 숫자.
- OpenRouter 의 전체 무료 모델 목록(샌드박스에서 모델 목록 API 에 못 붙음. 웹 페이지로 본 것은 ERNIE-5.5-Flash-Preview, Inkling 2 Small, LFM3-1.7B 등 일부).
- Together·Fireworks·DeepInfra 의 자체 가격표(OpenRouter 의 제공자별 값으로만 봄).

## 출처 (모두 2026-11-11 확인)

공식
- DeepSeek 가격·모델: https://api-docs.deepseek.com/quick_start/pricing
- DeepSeek 캐시: https://api-docs.deepseek.com/guides/kv_cache
- DeepSeek 생각 모드: https://api-docs.deepseek.com/guides/thinking_mode
- DeepSeek 속도 제한: https://api-docs.deepseek.com/quick_start/rate_limit
- DeepSeek 개방 플랫폼 약관: https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html
- DeepSeek 개인정보 처리방침: https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html
- OpenAI 가격: https://developers.openai.com/api/docs/pricing
- OpenAI gpt-6-luna: https://developers.openai.com/api/docs/models/gpt-6-luna
- OpenAI 데이터: https://developers.openai.com/api/docs/guides/your-data
- Anthropic 가격: https://platform.claude.com/docs/en/about-claude/pricing
- Anthropic OpenAI SDK 호환: https://platform.claude.com/docs/en/api/openai-sdk
- Anthropic 학습 사용: https://privacy.claude.com/en/articles/7996868-is-my-data-used-for-model-training
- Gemini 가격: https://aistudio.google.com/docs/pricing (ai.google.dev/gemini-api/docs/pricing 에서 넘어감)
- Gemini OpenAI 호환: https://aistudio.google.com/docs/openai
- Gemini 속도 제한: https://aistudio.google.com/docs/rate-limits
- Gemini 약관: https://aistudio.google.com/docs/terms
- Z.ai 가격: https://docs.z.ai/guides/overview/pricing
- Z.ai GLM-5.3-Flash: https://docs.z.ai/guides/llm/glm-5.3-flash
- Z.ai 시작하기(base_url): https://docs.z.ai/guides/overview/quick-start
- Z.ai 개인정보·DPA: https://docs.z.ai/legal-agreement/privacy-policy
- MiniMax 가격: https://platform.minimax.io/docs/guides/pricing-paygo
- MiniMax OpenAI 호환: https://platform.minimax.io/docs/api-reference/text-openai-api
- Alibaba Model Studio 가격: https://www.alibabacloud.com/help/en/model-studio/model-pricing
- Alibaba 문맥 캐시: https://www.alibabacloud.com/help/en/model-studio/context-cache
- Moonshot(Kimi) 가격 안내: https://platform.kimi.ai/docs/pricing/chat , https://www.kimi.ai/help/kimi-api/api-pricing
- Mistral 가격: https://mistral.ai/pricing , 모델 목록 https://docs.mistral.ai/getting-started/models/models_overview/
- xAI 모델·가격: https://docs.x.ai/docs/models , https://docs.x.ai/developers/models
- Groq 모델: https://console.groq.com/docs/models
- Cerebras: https://www.cerebras.ai/pricing , https://inference-docs.cerebras.ai/models/overview
- OpenRouter 한도: https://openrouter.ai/docs/api-reference/limits
- OpenRouter FAQ: https://openrouter.ai/docs/faq
- OpenRouter 프롬프트 캐시: https://openrouter.ai/docs/features/prompt-caching
- OpenRouter DeepSeek V4.1 Flash: https://openrouter.ai/deepseek/deepseek-v4.1-flash
- OpenRouter 무료 모델: https://openrouter.ai/models?max_price=0

벤치·측정
- Vals AI Terminal-Bench 4.0: https://www.vals.ai/benchmarks/terminal-bench-4
- Vals AI SWE-bench Verified(보관): https://www.vals.ai/benchmarks/swebench
- Vals AI Terminal-Bench 2.0(보관): https://www.vals.ai/benchmarks/terminal-bench-2
- Artificial Analysis 순위표: https://artificialanalysis.ai/leaderboards/models
- Artificial Analysis DeepSeek V4.1 Flash 제공자: https://artificialanalysis.ai/models/deepseek-v4-1-flash/providers
- Artificial Analysis 비교(속도): https://artificialanalysis.ai/models/comparisons/gemini-3-5-flash-lite-vs-gpt-5-4-nano
- DeepSeek V4.1 Flash 논문: https://arxiv.org/pdf/2609.19969

제3자(표에 [제3자]로 쓴 것)
- Kimi K3 값: https://pricepertoken.com/pricing-page/model/moonshotai-kimi-k3 , https://www.eesel.ai/blog/kimi-k3-pricing
- Mistral Small 4 값: https://developer.puter.com/tutorials/mistral-api-pricing/
- Cerebras 값·등급: https://www.activepieces.com/blog/cerebras-api-pricing-2026-all-models-and-plan-limits (등급 합침 출처 하나는 모델 판 이름이 주소에 있어 뺐다)
- OpenRouter 수수료: https://dupple.com/learn/openrouter-pricing , https://apicostcalc.com/ja/blog/openrouter-credit-fee-is-5-5-not-5-percent.html
- Haiku 5.5 출시 보도: https://venturebeat.com/ai/anthropic-launches-claude-haiku-5-5-with-90-api-price-reduction-matching-gpt-6-luna/ , https://computingforgeeks.com/claude-haiku-5-5-released-features-benchmarks/
- GLM-4.7-Flash 무료 한도: https://toolnavs.com/en/article/1100-zai-released-glm-47-flash-weights-and-api-free-tier-1-concurrency-and-launched-f , https://openrouter.ai/z-ai/glm-4.7-flash:free
- DeepSeek API 데이터 정책 해설: https://www.developersdigest.tech/models/data-retention/deepseek , https://standardcompute.com/providers/deepseek
- DeepSeek V4.1 Flash 벤치 집계: https://benchlm.ai/models/deepseek-v4-1-flash , https://www.mindstudio.ai/blog/deepseek-v4-1-flash-benchmarks/
