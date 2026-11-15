# 24/32GB VRAM 로컬 코딩 에이전트 모델 후보 (2026-11-11)

> 원래 기록: 조사 보고(비공개 운영 기록, 2026-11-11). 총괄 지시로 조사 서브에이전트가 웹과 앞선 로컬 장비 검토 보고(비공개 운영 기록)를 읽고 썼다. 점수 숫자는 원문 그대로 옮겼다.
> 꼴: 조사(판정 없음). 후보 점수는 모두 업체 자체 하네스 값이고, 우리가 잰 값은 기준선 한 줄뿐이다.
> 실행 조건: 해당 없음(조사만). 확인 날짜 2026-11-11. 전제는 로컬 24/32GB 장비다. 우리 서빙 모델은 판 이름을 가리고 "원본 27B"로 적었고, 같은 세대의 Qwen MoE 는 "Flash-Next"로만 적었다. abliteration 판은 제작자·저장소 이름을 적지 않았다.

- 물음: 사람(제품 주인) — "32GB 에 더 나은 해결책은 없나, 왜 Flash-Next 만 되풀이하나".
- 주의: 점수는 업체 자체 하네스 값. 모델 카드를 직접 못 연 것은 [간접], 짐작은 [어림].
- 우리 기준선(E17, 11-09–10, 평가 담당 보고): v2.0.3 32과제×5 — Opus 5.5 83.1% · Sonnet 5.5 77.5% · Haiku 5.5 75.0%, 27B W4(우리) 약 51–58%.

## 1. 앞선 검토 보고가 Flash-Next 를 되풀이한 까닭
- 잣대가 AA 지수(v4.3.2) 한 줄 + 1인 tok/s. 로컬 24/32GB 장비 크기 안 1위가 Flash-Next(40), 27B 34. 활성 6B 라 램 내림에도 빠르다는 식이 결정타.
- "32GB 에 27B 를 W8 로"·코딩 전용 점수(SWE-V·TB) 비교는 거의 없었다. Qwen3.6-27B·abliteration 판 유무가 빠졌다.
- 최신 AA v5.0.1 에서는 간격이 달라졌다(원본 27B 20, DeepSeek V4.1 Flash 33, GLM-5.3 32, Flash-Next 는 페이지에 없음).

## 2. 후보
| 모델 | 총/활성 | 양자·크기 | 지표(자체 보고) | 도구 호출 | 라이선스 | abliteration 판(HF) |
|---|---|---|---|---|---|---|
| 원본 27B 밀집(우리 서빙 모델, 판 이름 가림) | 27B | Q4_K_M 17.8GB, Q8_0 29.1GB [간접] | SWE-Pro 61.7, TB2.1 73.0, DeepSWE 42.2 | 우리 vLLM 검증 | Apache 2.0 | 있음(개인 업로드 여럿) |
| Qwen3.6-27B 밀집 | 27B | Q4_K_M 16.8GB | SWE-V 77.2, SWE-Pro 53.5, TB2.0 59.3 | qwen3_coder | Apache 2.0 | 있음 |
| Flash-Next MoE(같은 세대 Qwen, 판 이름 가림) | 125B/6B | Q2_0 37.6GB, IQ3_S 54.8GB | SWE-Pro 62.5, DeepSWE 58.7, Toolathlon 73.5 | 카드에 파서 없음, 로컬은 Strata/llama.cpp | qwen-community-1.0 | 있음 [간접] |
| Qwen3.6-35B-A3B MoE | 35B/3B | UD-Q4_K_M 22.1GB | SWE-V 73.4, TB2.0 51.5 | qwen3_coder | Apache 2.0 | 모름 |
| Qwen3-Coder-Next MoE | 80B/3B | Q4 35–46GB | SWE-V 70.6 [간접] | vLLM·llama.cpp | Apache 2.0 | 있음 |
| Devstral Small 2 밀집 | 24B | Q4 14GB, Q8 26GB | SWE-V 68.0 [간접] | Mistral | Apache 2.0 | 있음 [간접] |
| gpt-oss-120b MoE | 117B/5.1B | MXFP4 63GB | SWE-V 62.4(2025-08) | vLLM·llama.cpp | Apache 2.0 | 있음 |
| GLM-5.3-Flash MoE | 320B/18B | 2비트 약 115GB | TB2.1 84.3, DeepSWE 63.4 [간접] | 모름 | MIT | 있음 [간접] |

장비 크기별 상위 [어림]:
- 24GB(3090): Qwen3.6-27B·원본 27B Q4 → 35B-A3B Q4 → Flash-Next Q2/IQ3 + 램 → Devstral Small 2 Q4
- 32GB: 27B Q6–Q8(Q6 + KV 가 현실적) → Devstral Small 2 Q8 → Flash-Next Q2_0(겨우) → Qwen3-Coder-Next Q4(일부 램)
- 32GB + 램 128GB: Flash-Next IQ3_S → Qwen3-Coder-Next Q4–Q6 → gpt-oss-120b → GLM-5.3-Flash 2비트(느림)

## 3. 결론
- "Flash-Next 가 틀렸다"는 근거는 없다(SWE-Pro 62.5 대 27B 61.7, DeepSWE·Toolathlon 에서만 앞섬). 다만 2–3비트 품질·도구 호출 경로가 모름.
- 32GB 에 27B 를 더 높은 정밀도로는 쓸 만한 길. 가중치 W8 보다 KV 캐시 q8_0 이상이 더 중요하다는 간접 근거.
- Qwen3.6-27B(SWE-V 77.2)도 후보. 코딩 특화 밀집은 Devstral Small 2 뿐. 선택지는 Flash-Next 하나가 아니라 4–5개.
- abliteration 판: 8개 중 7개에 HF 판(35B-A3B 만 모름). 전부 개인 업로드, 도구 호출·장문 성능이 원본과 같은지 자료 없음.

## 4. 장비를 정하기 전 실측할 셋
1. 원본 27B Q6–Q8 + KV q8_0 — W4 대비 정밀도 이득(32GB 장비를 쓰는 까닭)
2. Flash-Next Q2_0 / IQ3_S (+ abliteration 판 1회) — 2–3비트 품질·도구 호출
3. Qwen3-Coder-Next Q4 (램 128GB 구성) — 대안. 또는 Qwen3.6-27B(가장 쌈)
- 각 abliteration 판은 원본 대비 성공률 하락 여부를 1회씩.

출처: https://huggingface.co/Qwen/Qwen3.6-27B · https://artificialanalysis.ai/models/open-source · https://localbench.substack.com/p/kv-cache-quantization-benchmark · https://insiderllm.com/guides/best-local-llms-coding/ · https://www.datacamp.com/blog/best-llm-for-coding · https://apidog.com/blog/glm-5-3-flash-uncensored/ (원본 27B·Flash-Next 의 모델 카드·양자화 저장소·abliteration 판 소식 주소 넷은 판 이름이 주소에 있어 뺐다)
