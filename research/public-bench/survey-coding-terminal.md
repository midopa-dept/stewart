# 공개 벤치 조사 1 — 코딩·터미널·도구 호출

> 원래 기록: 공개 벤치 조사 1(2026-11-12, 서브에이전트 보고, 총괄이 줄여 저장). 숫자·출처는 그대로 옮겼다.
> 꼴: 문헌·리더보드 조사(실행 없음, 판정 없음).
> 실행 조건: 해당 없음(조사만). 공개 점수는 모두 원본 정밀도·API 기준이다. 우리 모델의 원본(Qwen 계열 27B) 점수는 모델 판 이름을 가리고 "원본 27B"로 적었다.

표기: "확인" = 원문을 열어 봄 · "간접" = 집계 사이트·검색 요약 · "기억" · "확인 못 함". **4비트 W4A16 점수는 공개 숫자가 없다** — 아래 공개 점수는 모두 원본 정밀도·API 기준. GPU 시간은 추정(과제당 분 × 과제 수 ÷ 4, 3090 한 장 $0.3/h).

## 요약
- **먼저 돌릴 것**: ① Terminal-Bench 2.1(Harbor) ② τ²-bench 텍스트(airline·retail 우선) ③ SWE-rebench 최신 창. "기능을 얹는 것이 이득인가"에는 SkillsBench 가 가장 직접적이라 넷째 후보.
- **27B 급에 맞는 난도**: Terminal-Bench 2.x(45–73), SWE-rebench(31), SWE-bench Pro(28–62), SkillsBench(48).
- **천장이라 빼는 것**: SWE-bench Verified(원본 27B 86.0, 오염·깨진 시험 지적), LiveCodeBench v6(90.3), BFCL 단일 턴(88–90).
- **바닥이라 빼는 것**: Harbor-Index(최고 25.6), SWE-EVO, FeatureBench 완전 해결률, Terminal-Bench 3.0·4.x.
- **실행 틀은 Harbor 하나로** 모으면 된다(어댑터 85개 넘음, 바깥 에이전트 방식이 "로컬에서 나가는 연결만" 제약에 맞음).
- **병목은 GPU 가 아니라 과제를 돌리는 쪽의 동시 컨테이너 수**다. GPU 값은 벤치당 한 벌 $3–5.

## 1. 실행 틀
- **Harbor**(Terminal-Bench 팀, Apache-2.0): `harbor run -d <dataset> -a <agent> -m <model> --n-concurrent N`. 로컬 Docker 기본, Daytona·Modal 등 클라우드 샌드박스도. 어댑터: swebench·swebenchpro·multi-swe-bench·swelancer·aider_polyglot·bfcl·tau3-bench·theagentcompany·featurebench·gaia2·locomo 등. 자기 실행기는 `-a module.path:ClassName` — `BaseAgent`(바깥 에이전트: 루프가 Harbor 프로세스에서 돌고 `environment.exec()` 로만 컨테이너를 만짐) 또는 `BaseInstalledAgent`(컨테이너 안에 CLI 설치).
- mini-swe-agent: SWE 계열의 "뼈대" 기준선으로 그대로 쓸 수 있다(rebench 부분집합 포함).
- HAL: 새 모델 갱신 멈춤 — 선례 자료로만.

## 2. 벤치별
**터미널**
| 벤치 | 과제·시간 | 27B 급 점수 | 채점·문제 | 하니스 차이 선례 |
|---|---|---|---|---|
| **Terminal-Bench 2.0 / 2.1** | 89과제(2.1 은 28개를 고친 판). 과제당 7–33분(Vals — Qwen 3.6 27B 33분) | 원본 27B 2.1 에서 73.0(모델 카드). Qwen3.6-27B: 59.3(카드, 넉넉한 자원·5회) 대 44.94(Vals, Terminus 2, 1회) 대 약 45.5(개인 재현). Gemma4-31B 39–43 | pytest 결정적. 시간 제한 변경은 리더보드에서 부정으로 봄. Apache-2.0 | **가장 많다.** 공식 2.1 글: GPT-5.4 전용 하니스 77.3 대 Terminus 2 54.8, Sonnet 4.6 58.5 대 51.5, Gemini 3.1 Pro 67.1 대 70.7 |
| **SkillsBench 1.1** | 87과제, **스킬 없음 대 있음**의 해결률 차이 | Qwen3.6-27B 48.2 | 결정적으로 보임(확인 못 함), 라이선스 확인 못 함 | **"기능을 얹는 것"의 직접 선례.** GLM 5.1 + OpenHands 32.7 → 58.4 |
| Terminal-Bench 3.0·4.x, Harbor-Index | 최전선용 | 바닥 | — | — |

**SWE 계열**
| 벤치 | 과제 | 27B 급 점수 | 평 |
|---|---|---|---|
| SWE-bench Verified | 500 | **원본 27B 86.0** | 천장·오염 — 연기 시험으로만 |
| **SWE-rebench**(Nebius) | 매달 새 이슈, 현재 창 111문제 | Qwen3.6-27B 31.2 ±1.68 | 오염 대책 있음. 스스로 돌리는 절차·이미지 크기는 확인 못 함 |
| **SWE-bench Pro**(Scale) | 공개 731, 검증 V2 642 | 원본 27B 61.7 | Harbor 로 돈다. **27B 선례: Qwen3.6-27B 가 bash 만으로 약 28%, 편집 도구 하나를 더하니 50.7%**(간접) |
| Multi-SWE-bench | 7개 언어 1,632(mini 400) | 확인 못 함 | — |
| SWE-Lancer | 198 | 확인 못 함 | 과제당 이미지 약 14GB — 피한다 |
| FeatureBench·SWE-EVO | 기능 단위·긴 지평 | 바닥 | — |

**도구 호출·규칙 준수**
| 벤치 | 무엇 | 인프라 | 27B 급 | 평 |
|---|---|---|---|---|
| **τ²-bench**(Sierra, MIT) | 모의 사용자와 대화하며 정책을 지키고 도구로 DB 를 바꿈. airline 50·retail 114·telecom 114·banking 97 | **Docker 불필요.** **사용자 시뮬레이터 LLM 이 필요**(기본 gpt-4.1) | Gemma4-31B airline 76.7(간접), telecom 은 천장일 수 있음 | "물어야 할 때 묻는가·하지 말 것을 안 하는가"를 재는 것은 이 범위에서 이것뿐. HAL: o3 전용 틀 54 대 범용 틀 20 |
| BFCL V4 | 함수 호출, 다중 턴, 웹 검색, **기억**(여러 세션) | vLLM 직접 | Qwen3-32B 종합 48.71, 기억 26.67 | 자기 에이전트 루프를 끼우는 길이 없다 |
| AppWorld | 9개 앱·457 API, DB 상태 채점 + 부수 피해 검사 | 외부 키 없음 | 확인 못 함 | 후보 |
| TheAgentCompany | 모의 회사 175과제 | 무겁고 NPC·채점용 LLM 필요 | — | 뺀다 |

## 3. 같은 모델, 다른 하니스 — 선례
- Terminal-Bench 2.1 공식 글: 일곱 모델의 전용 하니스 대 Terminus 2 쌍, 차이 −3.6 에서 +29 점.
- **arXiv 2607.22585**: Goose·OpenCode·OpenHands-SDK × Qwen 3.6 Plus·MiniMax M2.5, 50과제 — **통과율 차이는 0–8점으로 대부분 잡음 안이고, 해결 과제당 토큰은 약 40배 차이.**
- **arXiv 2608.26218**: Qwen3.6, SWE-bench Verified 169과제 — 문맥을 20,480 토큰으로 조이면 "오래된 도구 결과 줄이기" 처치가 완전 해결을 43 → 72 로 올렸다. 넓은 문맥에서는 비슷.
- Qwen3.6-27B, SWE-bench Pro: 편집 도구 하나로 28 → 50.7(간접).
- SkillsBench: 스킬 켬·끔 쌍이 모델·하니스별로 공개.

## 4. 물음별 적합도
| 벤치 | 뼈대 대 풀 | 생각 양 | 압축·기억 | 묻기·규칙 준수 |
|---|---|---|---|---|
| Terminal-Bench 2.1 | 높음 | 높음(시간 제한과 얽힘) | 중간 | 낮음 |
| SkillsBench | **가장 높음** | 중간 | 낮음 | 낮음 |
| SWE-rebench / SWE-bench Pro | 높음 | 높음 | 문맥을 조이면 높음 | 낮음 |
| τ²-bench | 중간(대화 자리) | 중간 | 낮음 | **가장 높음** |
| BFCL V4 | 낮음 | 중간 | 기억 범주(자체 도구만) | 무관 함수 거절만 |

## 5. 먼저 돌릴 것
| 순서 | 벤치 | 한 벌(1회) | 팔 하나에 3회 | 준비 |
|---|---|---|---|---|
| 1 | **Terminal-Bench 2.1** | 89 × 약 40분 ≈ 59 과제시간 → 약 15 GPU 시간 ≈ **$4.5** | 약 $14 | 2–3일 |
| 2 | **τ²-bench 텍스트** | 278 × 약 2분 → 약 2.5 GPU 시간 ≈ **$1 미만** + 사용자 시뮬레이터 API(한 벌에 수 달러–수십 달러 추정, **승인 필요**) | 약 $3 + API | 1–2일 |
| 3 | **SWE-rebench 최신 창** | 111 × 약 20분 → 약 9 GPU 시간 ≈ **$3** | 약 $8 | 3–4일 |

넷째: SkillsBench(실행 방식·라이선스 확인 뒤). SWE-rebench 의 대안은 SWE-bench Pro V2 150개 층화 추출(약 $4).

## 6. 함정
- **과제를 돌리는 쪽이 병목**: 동시 3이면 Terminal-Bench 한 벌이 약 20시간, 3회에 60시간.
- **시간 제한과 자원이 점수를 크게 흔든다**: 같은 Qwen3.6-27B 가 설정에 따라 59.5 대 45.5. 로컬 3090 은 API 보다 느려 시간 초과가 는다. 내부 비교에서는 시간 제한 배수를 고정하고 공개 숫자와 직접 비교하지 않는다.
- **생각 양은 시간 제한과 얽힌다**: 걸음·토큰 기준 예산도 같이 기록.
- **잡음**: 89과제 1회면 표준오차 약 5점. 3회 이상 권장. 선례에서 하니스 차이는 대개 0–8점.
- **통과율만 보면 차이가 안 보일 수 있다**: 해결 과제당 토큰, 빈 걸음, 실패 유형을 같이.
- τ² 는 사용자 시뮬레이터를 고정해야 — 로컬 모델로 대신하면 공개 숫자와 비교 불가.
- SWE 계열은 x86 이미지 — 로컬 맥(ARM)에서 돌리지 않는다.
- **인터넷이 켜진 과제는 답을 내려받을 수 있다** — 웹 도구를 끄거나 궤적을 검사.

## 7. "한 번짜리가 아닌" 벤치
깔끔하게 맞는 것은 없다. 가까운 것: BFCL V4 기억 범주(자체 도구만), **Compact-Bench**(arXiv 2610.31710 — "전체 이력으로 이어 가기" 대 "요약만으로 이어 가기", 코드 공개), CompactionBench(Microsoft), **기존 벤치에 문맥 상한 조이기**(가장 싼 길), 대화형 기억 벤치 LoCoMo(Harbor 어댑터 있음).

출처(주요): tbench.ai/news/terminal-bench-2-1 · github.com/harbor-framework/harbor · docs.harborframework.com/agents/custom-agents · 원본 27B 모델 카드(판 이름 가림) · vals.ai/benchmarks/terminal-bench-2 · swe-rebench.com · github.com/scaleapi/SWE-bench_Pro-os · arxiv.org/html/2607.22585v1 · arxiv.org/abs/2608.26218 · skillsbench.ai · github.com/sierra-research/tau2-bench · hal.cs.princeton.edu · gorilla.cs.berkeley.edu · arxiv.org/abs/2610.31710
