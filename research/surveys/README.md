# 조사 — 값싼 API 모델과 로컬 24/32GB 모델 후보

> 원래 기록: 조사 보고 둘(비공개 운영 기록, 2026-11-11). 가격·점수 숫자는 원문 그대로 옮겼다.
> 꼴: 조사(판정 없음). 우리가 잰 값이 아니라 공개 가격표·모델 카드·남의 측정을 모은 것이다.
> 실행 조건: 해당 없음(조사만). 확인 날짜는 둘 다 2026-11-11 이다. 가격·점수는 그날 기준이라 지금은 다를 수 있다. 우리 서빙 모델은 판 이름을 가리고 "원본 27B"로 적었다.

**맨 위 요약**
- **값싼 API 모델**: 코딩 에이전트 실행기에 별도 인스턴스로 붙일 값싼 API 후보 22줄을 값·캐시·기능·데이터 정책·작업당 어림 비용·공개 코딩 벤치 점수로 견줬다. 값 대비로는 DeepSeek `deepseek-flash`(작업당 $0.027–0.053, Terminal-Bench 4.0 31.8%)가 앞서고, 데이터 정책이 무난한 쪽은 OpenAI `gpt-6-luna`, 무료는 Z.ai `glm-4.7-flash` 였다.
- **로컬 24/32GB 모델 후보**: "왜 Flash-Next 만 되풀이하나"라는 물음에 답한 조사다. Flash-Next 가 틀렸다는 근거는 없지만, 선택지는 하나가 아니라 4–5개(원본 27B 를 더 높은 정밀도로, Qwen3.6-27B, Devstral Small 2, Qwen3-Coder-Next 등)였다. 장비를 정하기 전에 실측할 셋을 적었다.

## 파일
| 파일 | 확인 날짜 | 꼴 | 무엇 |
|---|---|---|---|
| [cheap-api-models.md](cheap-api-models.md) | 2026-11-11 | 조사 | 값싼 API 모델 후보 — 가격·캐시 방식·문맥, 도구 호출·생각 끄기·속도 제한, 데이터 정책, 기준 쓰임새(30단계·단계당 입력 20k·80% 캐시)의 작업당·월 어림, Vals AI·Artificial Analysis 점수, 직접 API 대 중계 |
| [model-candidates-32gb.md](model-candidates-32gb.md) | 2026-11-11 | 조사 | 로컬 24/32GB VRAM 코딩 에이전트 모델 후보 — 후보 8줄(총/활성·양자 크기·자체 보고 지표·도구 호출·라이선스·abliteration 판 유무), 장비 크기별 상위, 결론, 실측할 셋 |

## 함께 볼 것
- [../public-bench/survey-coding-terminal.md](../public-bench/survey-coding-terminal.md) — 코딩·터미널 벤치 조사(원본 27B 의 공개 점수가 여기에도 있다).
- [../registry/](../registry/README.md) — abliteration 판 대 원본 판을 잰 사전 등록 P-001.
