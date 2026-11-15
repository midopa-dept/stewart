# 공개 벤치 조사 2 — 웹 검색·조사·질의응답

> 원래 기록: 공개 벤치 조사 2(2026-11-12, 서브에이전트 보고, 총괄이 줄여 저장). 숫자·출처는 그대로 옮겼다.
> 꼴: 문헌·리더보드 조사(실행 없음, 판정 없음).
> 실행 조건: 해당 없음(조사만). 과제당 분·GPU 시간은 전부 추정이다. 이 문서는 벤치의 문항·정답·문서 원문을 싣지 않는다.

표기: [확인] = 원문(저장소·논문·리더보드)을 열어 봄 · [간접] = 검색 요약·집계 사이트 · [기억] · [확인 못 함]. 과제당 분·GPU 시간은 전부 **추정** — 20–30문항 시험 주행으로 먼저 잰다. 우리 모델의 원본(Qwen 계열 27B)의 이 벤치들 점수는 어디에도 없다.

## 요약
- **고정 말뭉치로 재현되는 1순위는 BrowseComp-Plus.** 830문항, 약 10만 문서 고정 말뭉치, BM25·Qwen3-Embedding 색인 제공, 공식 채점 스크립트가 **Qwen3-32B 로컬 채점기**(유료 API 불필요). 난도 적정: Qwen3-32B 10.4%, gpt-oss-20B 34.6%, gpt-oss-120B 42.9%, 30B 조사 특화 모델 44.5–54.8%.
- **"알아서 검색하는가"를 그대로 재는 공개 벤치는 없다.** 가까운 것은 OverSearchQA(Apple, 1,188문항, 고정 위키)와 When2Call(NVIDIA, 객관식). 둘 다 "폴더를 뒤졌다" 같은 도구 간 오선택은 안 잰다. 다만 BrowseComp-Plus 논문에서 Qwen3-32B 가 **문항당 평균 0.92번만 검색**해 무너진 것이 바로 우리 증상 — 모든 벤치에서 "검색 0회 비율·평균 검색 횟수"를 부지표로 뽑는다.
- **실시간 웹 1순위는 LiveNewsBench**(최신성, 사람 검증 200문항, 검색 5 + 읽기 5 예산 — 우리 도구 꼴과 같음), **한국어는 K-BrowseComp**(2026-06, 300문항 Verified, MIT). 한국어 넓이 검색은 Ko-WideSearch.
- SimpleQA(검색 켬)는 27B급에서 **천장**(로컬 Qwen3.6-27B 95.7% 보고) — 배선 점검용. GAIA 는 텍스트 부분집합(103문항)만.

## 1. 벤치별 — 실행 조건
| 벤치 | 무엇·과제 수 | 실시간 / 고정 | 우리 도구(SearXNG + HTTP)로 | 채점 |
|---|---|---|---|---|
| **BrowseComp-Plus** [확인] | 830문항, 다단계 탐색, 문서 100,195개 | **고정 말뭉치** | 검색 도구를 말뭉치 검색기로 바꿔 끼움, get_document = web_fetch 대응 | 공식 스크립트는 Qwen3-32B 로컬(논문 본표는 gpt-4.1) |
| BrowseComp 원판 | 1,266문항 | 실시간 | 가능하나 매우 어려움 | LLM 채점 |
| **OverSearchQA** [확인] | 불필요 검색·기권, 1,188문항 | **고정**(enwiki-20250801 + E5-base) | 검색기 교체 | LLM 채점(GPT-4o-mini, 사람 일치 84%) |
| **When2Call** [확인] | 부를지/되물을지/못 한다 할지, 약 3,652문항 | 웹 불필요 | 판단만 | 로그확률 객관식(채점기 불필요) |
| "When Should LLMs Search?" | 무검색/강제검색 두 번 돌려 정답표 생성(방법론) | — | 자기 실행기에 적용 가능 | — |
| **LiveNewsBench** [확인] | 최신 뉴스 다단계 QA, 사람 검증 200, 분기 갱신 | 실시간(기사 보관) | **맞음**(검색 상위 10 + 본문 읽기) | GPT-4.1 + SimpleQA 채점 문구 |
| **K-BrowseComp** [확인] | 한국 맥락 탐색, Verified 300 | 실시간(텍스트 공개 페이지만) | 원리상 맞음 — SearXNG 어댑터 필요 | GPT-5.4-mini 로 답 추출·대조. MIT |
| Ko-WideSearch [간접] | 한국어 넓이 검색(열거 + 표), 228표 | 실시간 | 가능 추정 | 정규화 대조기(F1) |
| GAIA [확인] | 검증 165, 텍스트 부분집합 103 | 실시간 + 첨부 파일 | 일부만 | 정답 문자열 대조. 게이트·재배포 금지 |
| WebWalkerQA [확인] | 한 사이트 안 깊이 탐색, 680문항 | 실시간 | **맞음** | LLM 채점. Apache-2.0 |
| FRAMES | 위키 다단계 824문항 | 실시간 위키(덤프로 고정 가능) | 맞음 | LLM 채점 |
| SealQA | 상충·잡음 결과 속 추론, Seal-0 111 / Seal-Hard 254 / LongSeal 254(문서 동봉) | 일부 고정 | 맞음 | LLM 채점 |
| HotpotQA·2Wiki·MuSiQue | 고전 다단계 QA | 고정 위키 덤프 | 검색기 교체 | EM/F1 |
| DeepSearchQA [확인] | 빠짐없는 집합 답, 900문항 | 실시간 | 맞음 | Gemini 채점 |
| FreshQA | 최신성·거짓 전제 | 실시간 | 맞음 | 2026-05 이후 갱신 불명 |
| Mind2Web 2 · DeepResearch Bench · LiveResearchBench | 보고서·출처 귀속 | 실시간 | 맞음 | **큰 채점기 필수** |
| DeepResearchGym [확인] | 보고서 채점 | **고정**(ClueWeb22·FineWeb, 공개 검색 API) | 그쪽 검색 API | gpt-4.1-mini |

## 2. 난도 — 27B급 공개 모델 점수
| 벤치 | 점수 | 판정 |
|---|---|---|
| BrowseComp-Plus [확인] | Qwen3-32B: BM25 3.49 / Qwen3-Embed-8B 10.36(검색 0.92–0.94회). gpt-oss-20B-high 21.08 / 34.58(검색 24–27회). gpt-oss-120B 28.67 / 42.89. gpt-4.1 35.42, o3 63.49, gpt-5 70.12. Nemotron-3-Nano-30B 20.8, Tongyi DeepResearch-30B 44.5, OpenResearcher-30B 54.8 | **적정**. 검색을 많이 하는 모델일수록 오른다 — 실행기 차이가 그대로 보임 |
| BrowseComp 원판 | 32B 계열 3.8–26.3 | 바닥권 |
| GAIA 텍스트 103 | 30.3–64.1(공개 모델·조사 특화) | 적정이나 문항이 적어 ±약 10%p |
| SimpleQA 검색 켬 | Qwen3.6-27B 95.7%, 9B 약 91% | **천장** |
| LiveNewsBench [확인] | DeepSeek V3.2 83–84.5, Qwen3-235B 60–61.5, **Qwen3 8B 49.5**, GPT-OSS-120B 27.0, Llama 3.1 8B 11.0. 무검색 3.5–21.5 | **적정**. 도구 호출 형식 오류가 점수를 가름 |
| K-BrowseComp Verified [확인] | GPT-5.5 45.67, DeepSeek-V4-Pro 30.00, **Gemma-4-31B 23.33, Qwen3.6-35B-A3B 12.00**, HyperCLOVAX-SEED-Think-32B 2.33 | 적정–어려움, 27B 는 10–25% 예상 |
| When2Call [확인] | Qwen 2.5 32B F1 32.9, GPT-4o 61.3 | 적정 |
| OverSearchQA [확인] | 검색을 켜면 답 정확도 +24.0%p, 기권 정확도 −12.8%p. 검색의 70.5% 가 불필요 | 적정 |

**검색 도구의 질이 점수에 주는 영향**
- BrowseComp-Plus: 검색기만 BM25 → Qwen3-Embed-8B 로 바꿔 Qwen3-32B 3.5 → 10.4. **정답 문서를 전부 주면 Qwen3-32B 도 83.25%** — 약점은 추론이 아니라 검색 행동이다.
- search_evals: 같은 모델에 엔진만 바꾸면 순위가 바뀐다.
- LiveNewsBench: 검색 예산 1 → 7 로 4–64.5%p 상승, 공개 모델이 더 크게 오른다.
- **SearXNG 로 낸 공개 수치는 못 찾았다** → 실시간 웹 벤치의 절대 점수는 논문과 못 맞댄다. 우리 안의 상대 비교(판 대 판)로만 쓴다.

## 3. 채점·오염·부패·약관
- 채점: 단답 대조형은 작은 모델로도 안정적. BrowseComp-Plus 는 로컬 Qwen3-32B 가 공식. 보고서형은 큰 채점기 필수이고 채점기 판이 바뀌면 점수가 달라진다.
- 오염: SimpleQA·HotpotQA 류는 외웠을 위험 큼. GAIA 검증 답은 웹에 퍼져 있어 차단 목록 필요. BrowseComp-Plus 는 고정 말뭉치라 유출 페이지가 검색에 안 잡힌다. 복호한 문항을 저장소에 올리지 말 것.
- 부패: 실시간 웹 벤치는 답과 링크가 변한다.
- **대량 자동 검색**: SearXNG 는 상위 엔진을 긁는 꼴이라 약관상 회색이고, CAPTCHA·429 가 나면 엔진을 정지시킨다. 데이터센터 IP 에서 더 잘 막힌다. 문항 수백 × 검색 수십 회를 병렬로 쏘면 하루 안에 막힐 수 있다.

## 4. 물음별 적합도
| 물음 | 맞는 벤치 | 평 |
|---|---|---|
| 알아서 검색하는가 | OverSearchQA, When2Call, LiveNewsBench 무검색/검색 차, **모든 벤치의 "검색 0회 비율"** | "검색 대 폴더 뒤지기"는 공개 벤치에 없다 → 공개 문항을 실행기의 **전체 도구 세트**(파일 도구 포함)로 돌려 첫 도구 선택을 세는 자체 지표 |
| 검색 → 읽기 → 출처 | BrowseComp-Plus(정답 문서 재현율·인용 문서 id), SealQA, LiveNewsBench | 출처 귀속 정식 채점은 큰 채점기 필요 |
| 여러 걸음 조사 | BrowseComp-Plus, GAIA 텍스트, FRAMES, WebWalkerQA | 문맥 수만 토큰 제약이 여기서 드러남 |
| 최신성 | LiveNewsBench | 모델 컷오프 뒤 문항이어야 뜻이 있다 — 분기 새 판 |
| 한국어 | K-BrowseComp(깊이), Ko-WideSearch(넓이) | SearXNG 에 네이버 엔진을 켜야 한국 웹 재현율이 나올 것 [기억] |

## 5. 권고 — 먼저 돌릴 것 (3090 한 장 $0.3/h 가정)
**고정 말뭉치(재현됨) — 먼저**
1. **BrowseComp-Plus (주 벤치)** — 830문항 × 약 4분 ≈ 55 GPU시간(순차), 동시 6이면 10–12시간, **$3–4**. 처음에는 150–200문항 표본(약 $1, ±약 7%p). 채점 로컬 Qwen3-32B 약 $0.5. 준비 2–3일(색인 내려받기 — 크기 [확인 못 함], BM25 는 CPU·Java 21, Qwen3-Embed-8B 질의 인코딩은 GPU 한 장 더, web_search/web_fetch 꼴 어댑터). 함정: 논문 표는 gpt-4.1 채점, 4비트 채점기는 공식 조건과 다름.
2. **OverSearchQA + When2Call (도구 결정)** — OverSearchQA 약 $1, When2Call $0.3 미만. 준비 2–3일(위키 색인을 직접 만들어야 할 수 있음). 채점기를 로컬로 바꾸면 논문 수치와 못 맞댄다.

**실시간 웹 — 그다음**
3. **LiveNewsBench + K-BrowseComp Verified** — 각 $1–2. 채점은 짧은 문구라 작은 유료 모델이면 벤치당 $1 미만(유료 채점은 승인 사항), 대안은 로컬 Qwen3-32B. 준비 3–4일(SearXNG 어댑터). 함정: **차단**(가정용 IP·동시 1–2·엔진 여러 개·정지 감시, 며칠에 나눠야 할 수 있음), **재현성**(검색 결과와 읽은 본문을 전부 녹화, 판 비교는 같은 날 것끼리), JS 렌더링 페이지에서 빈 본문(실패율을 따로 셈), 논문 점수와 절대 비교 불가.

**하지 말거나 뒤로**: SimpleQA(천장), GAIA(텍스트 103 만 보조), 보고서 채점류(큰 유료 채점기).

## 출처(주요)
github.com/texttron/BrowseComp-Plus · arxiv.org/html/2508.06600 · arxiv.org/html/2603.20278v1(OpenResearcher) · arxiv.org/html/2606.02404(K-BrowseComp) · github.com/prometheus-eval/K-BrowseComp · arxiv.org/html/2602.13543v1(LiveNewsBench) · arxiv.org/html/2601.05503v2 · github.com/apple/ml-over-searching · arxiv.org/html/2504.18851v1 · github.com/NVIDIA/When2Call · github.com/perplexityai/search_evals · github.com/LearningCircuit/local-deep-research · hal.cs.princeton.edu/gaia · huggingface.co/datasets/callanwu/WebWalkerQA · arxiv.org/html/2505.19253v1(DeepResearchGym)
