# 자가발전 기반 — 파인튜닝·증류 도구 조사와 권고 설계 (2026-11-12)

> 원래 기록: 총괄 지시로 조사 서브에이전트가 쓴 조사·권고 설계(비공개 운영 기록, 2026-11-12). 숫자·출처·확인 표시는 그대로 옮겼다.
> 꼴: **조사·계획(판정 없음)**. 문헌·문서·약관 조사와 설계 권고다. 학습·서빙을 실제로 돌리지 않았다. §7 의 실험(E0·E1)은 계획이고 이 기록에는 결과가 없다.
> 실행 조건: 실행 없음. 대상 모델 = Qwen 계열 27B(판 이름 가림 — 원본 판과 abliteration 판이 있다). 빌린 장비 = 빌린 GPU(RTX 3090 24GB, 48GB 급 대안). 로컬 = 맥(mlx-lm).
> 공개판에서 바꾼 것: 우리 모델의 판 이름·저장소 → "Qwen 계열 27B", 그 판 이름이 든 링크는 가림. 확인 표시 하나(제3자 글·검색 요약으로만 본 것)의 이름을 [간접] 으로 바꿨다. 대여처 이름 → "빌린 GPU". 로컬 장비 구매와 장비별 운영 몫(원래 기록 §5 의 일부, §4 표 한 칸, §8 의 두 결정)은 일반화하거나 뺐다. 내부 기록 이름 → "(비공개 운영 기록)", 세션 이름 → 역할 이름, 사람의 실사용 서술 → 일반화.

- 쓴 곳: 총괄 지시의 조사 서브에이전트. 읽기 + 웹 확인만 했고 코드·환경은 건드리지 않았다.
- 확인 날짜: 모든 웹 자료는 2026-11-12 에 직접 열거나 검색으로 확인했다. 출처는 맨 아래.
- 표시: [확인] = 원문 페이지를 직접 읽음 · [간접] = 검색 요약·제3자 글로만 봄 · [추정] = 내 계산·짐작 · [모름] = 확인 못 함.
- 읽은 내부 자료: 개선 계획([plan.md](plan.md)), 싼 API 모델 조사·모델 후보 조사·EXAONE 붙이기 계획(셋 다 2026-11-11, 비공개 운영 기록), 개발 담당의 SW-39 설계 보고(2026-11-12, 비공개 운영 기록 — 실사용 기록의 표시·내보내기 설계), stewart 상태 문서 머리 70줄, `pyproject.toml`. 사람의 실사용 자리·봉인 평가 묶음·키는 열지 않았다.
- 법률 자문이 아니다. 약관 풀이는 조항 원문을 읽은 사람의 해석이다.

## 0. 열 줄 요약

1. **되는 것**: 27B 를 QLoRA 로 학습하는 것은 24GB 한 장(빌린 3090)에서 된다고 Unsloth 공식 문서가 적는다[확인]. 어댑터를 서빙에 올리는 길도 vLLM(`--enable-lora`)·llama.cpp(`--lora`, 요청별 켬끔) 둘 다 문서에 있다[확인].
2. **안 되는 것**: 12GB 급 작은 GPU 에서 35B MoE 를 학습하는 것. Unsloth 는 이 모델의 bf16 LoRA 에 74GB 를 적고, MoE 4비트 QLoRA 는 권하지 않는다[간접]. llama.cpp 자체 학습은 FP32 전용·실험 단계다[확인]. 그런 작은 장비의 자가발전은 "학습 없는 길 + 다른 곳에서 학습한 어댑터를 들여 얹기"가 현실적이다.
3. **모르는 것(먼저 시험할 것)**: AutoRound W4A16 기반 + 초안 머리(MTP) 위에 vLLM 런타임 LoRA 가 제대로 얹히는지는 어디에도 확인된 글이 없다[모름]. 빌린 장비에서 10분이면 가려진다.
4. **증류 권리**: Anthropic·OpenAI·Google·Z.ai(API) 약관은 출력으로 "경쟁 모델"을 학습하는 것을 금지한다[확인, OpenAI 는 간접]. **DeepSeek 개방 플랫폼 약관은 "다른 모델 학습(모델 증류 등)"을 허용 용도로 명시한다**[확인]. 공개 가중치(Apache 2.0·MIT)는 출력 제한이 없다.
5. 그래서 안전한 teacher 는 (ㄱ) DeepSeek 직접 API (ㄴ) MIT·Apache 공개 가중치 큰 모델(DeepSeek-V4.1-Flash, GLM-5.3-Flash, Qwen3-Coder-480B)을 직접 또는 제3 호스트에서 (ㄷ) 27B 자신의 성공 궤적이다. Claude·GPT·Gemini 궤적은 학습에 넣지 않는다(비교·평가용으로만).
6. **기대치**: 공개된 큰 향상(SWE-bench Verified 40–62%)은 궤적 5천–30만 건 + 전체 미세조정의 결과다. 수백 건 + LoRA 로는 "실행기 습관 맞추기(도구 호출 꼴·확인 절차)" 수준의 향상을 기대하는 것이 맞다. 다만 491건으로 +13.6점을 낸 사례(SWE-Gym, 32B)가 있다[간접].
7. **추론(생각)은 학습 자료에 남긴다**: SERA 는 추론을 빼면 41.0% → 23.0% 로 떨어졌다고 보고한다[확인]. Unsloth 도 추론 자료 75% 이상을 권한다[확인].
8. **본체/별도 도구 가르기**: 본체에는 표시·내보내기·teacher 돌리기·어댑터 고르기·되돌리기와 "학습 없는 자가발전"만. torch 등 무거운 의존성이 드는 것(걸러 내기·학습·묶기·문턱 판정·판 장부)은 별도 공개 도구로 뺀다. 본체 의존성은 지금 둘뿐이다(prompt_toolkit·rich)[확인].
9. **먼저 할 것**: 돈 0 리허설(맥 mlx-lm + 작은 모델로 고리 한 바퀴) → 빌린 장비 한 번(어림 $10–20 [추정])으로 27B QLoRA + 위 3번 시험. 성공 판정은 "오르는가"가 아니라 "끝까지 돌고, 나빠지지 않고, 되돌려지는가".
10. **안전장치의 뼈대**: 자료마다 출처·약관 등급 꼬리표, 평가 과제 해시 대조, 세 단 평가(개발용·문턱용·봉인), 어댑터 판 장부와 한 줄 되돌리기, 사람 승인 두 곳(자료 묶음 확정·어댑터 채택).

## 1. 정의부터

읽는 사람: 딥러닝 내부를 배우는 중인 사람을 상정한다. 용어는 처음 나올 때 한 번씩 정의한다.

### 1-1. 모델·가중치·학습

- **모델**은 숫자 표(행렬) 여러 장으로 된 함수다. 글을 토큰(낱말 조각) 번호 열로 바꿔 넣으면 "다음 토큰이 무엇일지"의 확률을 내놓는다. 그 숫자 표의 값들이 **가중치**다. 27B 는 가중치가 약 270억 개라는 뜻이다.
- **학습**은 "정답 글을 주고, 모델이 정답의 다음 토큰에 준 확률이 높아지도록 가중치를 조금씩 고치는 일"이다. 틀린 정도를 숫자로 만든 것이 **손실**(여기서는 교차 엔트로피 = 정답 토큰 확률의 −log 평균)이고, 손실이 줄어드는 방향(기울기)으로 가중치를 움직인다.
- **파인튜닝(미세조정)**은 이미 학습된 모델을 내 자료로 조금 더 학습하는 것이다. 가중치 전부를 고치면 **전체 미세조정**이다. 27B 를 전체로 고치려면 가중치(16비트로 약 54GB) + 기울기 + 옵티마이저 상태가 다 메모리에 올라가야 해서 수백 GB 급 장비가 든다.

### 1-2. LoRA·QLoRA·DoRA — 무엇을 바꾸는가

- 모델 안의 한 행렬을 W(크기 out × in)라 하자. **LoRA** 는 W 는 얼리고(안 고치고), 작은 행렬 둘 A(r × in)·B(out × r)만 새로 학습한다. 쓸 때는 W' = W + (α/r)·B·A 로 더한다. r 이 **랭크**(보통 8–64)다.
- 왜 싸지나: 고칠 숫자 수가 out × in 에서 r × (in + out) 으로 준다. 예: in = out = 5120, r = 16 이면 2,621만 → 16만 4천(약 1/160).
- 그 A·B 묶음 파일이 **어댑터**다. 기반 모델 파일은 그대로 두고 어댑터만 갈아 끼우거나 뺄 수 있다 — "되돌리기"가 파일 하나 빼는 일이 되는 까닭이다.
- **QLoRA** 는 얼려 둔 W 를 4비트로 줄여 올리고(메모리 약 1/4) 그 위에서 LoRA 를 학습하는 것이다. 27B 가 24GB 한 장에 들어가는 까닭이다.
- **DoRA** 는 W 를 "크기"와 "방향"으로 나눠 방향 쪽에 LoRA 를 거는 변형이다. 같은 랭크에서 조금 낫다는 보고가 있으나 느리고, 서빙 쪽 지원이 LoRA 보다 좁다. 첫 고리에는 LoRA 로 충분하다.
- 한계: LoRA 는 "덜 배우고 덜 잊는다". 코드·수학에서 전체 미세조정보다 눈에 띄게 낮지만, 원래 하던 일은 더 잘 지킨다(Biderman 외 2024)[확인]. 다만 α·학습률을 잘못 잡으면 LoRA 가 더 많이 잊기도 한다(Shuttleworth 외, "intruder dimensions")[간접].

### 1-3. 궤적·증류·자기 증류

- **궤적**은 에이전트가 한 과제를 푼 기록 전체다: 지시문 → (사람 말 → 모델의 생각·답·도구 호출 → 도구 결과) 반복 → 끝. stewart 에서는 실행 폴더의 `events.jsonl`(step 별 new_messages·response)이 이것이다.
- **궤적으로 학습한다**는 것은 "그 시점까지의 대화를 넣었을 때, 기록에 있는 모델 차례(생각 + 도구 호출 꼴)를 그대로 내도록" 가중치를 고치는 것이다. 사람 말·도구 결과 부분은 입력으로만 쓰고 손실은 모델 차례 토큰에만 건다.
- **증류**는 더 센 모델(teacher)이 낸 출력을 정답 삼아 작은 모델(student)을 학습하는 것이다. 여기서는 "teacher 를 같은 실행기로 돌려 성공한 궤적"이 정답이다. teacher 의 가중치는 필요 없고 출력 글만 있으면 된다 — 그래서 API 모델도 기술적으로는 되고, 그래서 약관이 이것을 따로 막는다(§3).
- **자기 증류·자기 학습**은 teacher 가 자기 자신인 경우다: 27B 가 여러 번 시도해 시험을 통과한 궤적만 골라 다시 학습한다(연구 용어로는 rejection sampling fine-tuning). 새 지식이 들어오는 것이 아니라 "운 좋게 잘한 행동을 평소 행동으로" 굳히는 것이다.
- **사람 교정 학습(DAgger 류)**: 모델이 가다가 틀린 자리에서 전문가(사람·teacher)가 이어받아 고친 부분만 정답으로 쓴다. 모델이 실제로 빠지는 상태에서의 정답을 가르친다는 점이 핵심이다.
- **선호 학습(DPO 류)**: 같은 상황에서 "좋은 답·나쁜 답" 쌍을 주고 좋은 쪽 확률을 나쁜 쪽보다 높인다. 교정 전·후 차례가 자연스러운 쌍이 된다.

### 1-4. 보안 눈으로 한 줄

학습은 자료를 가중치에 "새기는" 일이다. 한 번 들어간 것은 가려 지울 수 없고(판을 버려야 한다), 질의로 일부가 되나올 수 있다(Carlini 외 2021: 학습 자료 원문 추출)[확인]. 그래서 학습 자료 묶음은 코드 배포물과 같은 수준으로 출처·해시·승인을 남겨야 한다.

## 2. 방법 — 연구와 실무에서 확인한 것

### 2-1. 코딩 에이전트를 궤적으로 학습해 점수를 올린 사례

| 연구 | 자료·teacher | student·방식 | 결과(SWE-bench Verified) | 표시 |
|---|---|---|---|---|
| SWE-Gym (Pan 외, arXiv 2412.21139, ICML 2025) | 자체 환경 2,438 과제. 궤적 491건 | Qwen2.5-Coder 32B, 학습 방식(LoRA/전체) [모름] | 491건으로 +13.6점(20.6%), 검증기 더해 32.0%. 자기 성공 궤적 재학습으로 Lite 19.7%까지 | 초록 [확인], 491건 수치 [간접] |
| SWE-smith (Yang 외, arXiv 2504.21798) | 과제 5만 개(저장소 128), Claude 3.7 Sonnet 궤적 5,016건 [간접] | Qwen2.5-Coder 32B | 40.2% | 초록 [확인] |
| SERA (Shen 외, arXiv 2601.20789) | teacher GLM-4.5-Air·GLM-4.6, 궤적 20만+ (시험 없이 "부드러운 검증") | Qwen3-32B **전체 미세조정**, 3 epoch, lr 1e-5 | 49.5%(32K)·54.2%(64K). 약 $2,000. 저장소 하나 특화 8,000건 약 $1,300 | [확인] |
| CoderForge-Preview (Together AI, 2026-02-25) | Qwen3-Coder-480B 가 OpenHands 로 낸 25.8만 건(통과 15.5만) | Qwen3-32B SFT(H100 64장) | 59.4% | [확인] |
| SWE-ZERO → SWE-HERO (Ludwig 외, arXiv 2604.01496) | teacher Qwen3-Coder-480B, 실행 없는 30만 + 실행 기반 1.3만 | Qwen2.5-Coder 32B, 2단 SFT | 62.2% | [확인] |
| On-policy Expert Corrections (Lauffer 외, arXiv 2512.14895) | student 가 가다가 전문가가 이어받음, student 부분은 손실에서 가림 | 7B·32B | 보통 모방 학습 대비 상대 +14%·+13% | [확인] |
| EntroPO (Yu 외, arXiv 2509.12434) | 여러 차례 궤적에 DPO·KTO 류 선호 학습 | 최대 106B | 30B 가 공개 가중치 순위 Lite 1위·Verified 4위(논문 주장) | 초록 [확인] |
| LoRA 자료 고르기 (Han, arXiv 2607.17205) | 궤적 67,074건에서 부분집합 | Qwen2.5-Coder-7B **LoRA** | 500 → 1,000건에서 손실 12.7% 감소. 품질 고르기 효과는 2,000건부터 보임(3.6%, p = 0.016). 가장 센 품질 신호는 "오류 뒤 재시도 비율" | [확인] |

읽는 법:
- 큰 점수는 전부 "수천–수십만 건 + 전체 미세조정"이다. **27–32B 급에서 LoRA 만으로 공개 벤치를 올렸다는 직접 사례는 못 찾았다**[모름]. LoRA 근거는 7B 대리 지표(손실)뿐이다.
- 그래도 수백 건이 통한다는 근거는 있다(SWE-Gym 491건). 그리고 우리 목적은 범용 실력이 아니라 "이 실행기의 도구·절차에 맞추기"라 필요한 자료가 적다. SERA 의 저장소 특화 결과(teacher 와 비슷하거나 조금 위, 단 Sphinx 에서는 아래)가 가장 가까운 선례다.
- **실행기 묶임**: CoderForge 는 "실행기·도구 묶음 하나로만 만들어 다른 구성으로 옮겨질지 모른다"고 스스로 적는다[확인]. 남이 푼 공개 궤적(OpenHands·SWE-agent 꼴)을 그대로 쓰는 것보다 같은 과제를 stewart 로 다시 돌려 얻는 것이 맞다.

### 2-2. 작은 자료에서의 요령과 실패

- **추론 포함**: SERA 는 teacher 자료에서 추론을 빼면 41.00% → 23.00% [확인]. Unsloth 의 우리 모델 계열 문서(판 이름 가림)는 "추론 능력을 지키려면 추론 자료 75% 이상"[확인]. → 내보내기에 `reasoning_content` 를 반드시 싣는다.
- **검증의 엄격함**: SERA 는 검증 문턱(0·0.25·0.75·1.0)이 통계적으로 구분되지 않았다고 보고한다[확인]. 반면 SWE-Gym·CoderForge 는 시험 통과만 썼다. 우리처럼 건수가 적으면 엄격히 거르는 쪽이 안전하다(Han: 작은 규모에서는 품질 고르기 효과가 통계적으로 안 보이지만 해가 되지도 않음).
- **긴 궤적 자르기**: SERA 는 순서를 지킨 자르기(43.00%)가 무작위 자르기(37.47%)·안 자른 것만 쓰기(40.60%)보다 낫다고 보고한다[확인]. → 앞에서부터 자르되 끝을 버린다.
- **쉬운 과제 쏠림**: 자기 성공 궤적만 모으면 쉬운 과제가 불어난다. SWE-Gym 은 과제당 건수 상한으로 막았다[간접]. → 과제당 2–3건 상한.
- **오류 연쇄**: teacher 를 토큰 단위로 따라가게 하는 증류는 도구 호출 하나가 틀리면 뒤가 줄줄이 어긋난다(SOD, arXiv 2605.07725)[확인]. 우리는 "성공 궤적 전체를 정답으로" 하는 단순한 길이라 해당이 적지만, student 가 못 따라가는 teacher 궤적은 해가 될 수 있다는 지적(SmartAD, ACL Findings 2026)[간접]은 기억해 둔다 — 너무 긴 생각·student 에 없는 도구를 쓴 궤적은 뺀다.
- **잊어버림**: LoRA 는 덜 잊지만 0 은 아니다. 연속으로 여러 번 덧학습하면 나빠진다는 보고가 있다(Shuttleworth 외)[간접]. → **어댑터 위에 어댑터를 쌓지 않는다. 매 회차 원본 기반에서, 누적된 자료 전체로 새로 학습한다.**
- **평가 누출**: 학습에 본 과제는 평가에서 통과율이 부풀려진다. §6 의 해시 대조와 세 단 평가로 막는다.
- **선호 학습(DPO)**: 실패에서 "갈림 걸음"을 찾아 쌍을 만드는 방법이 SFT 대비 상대 +26–37% 를 보고한다(Li 외, Verified Critical Step Optimization, arXiv 2602.03412 — 웹·검색 에이전트)[확인]. 사람 교정 전·후가 그대로 쌍이 되므로 2단계 후보로 둔다. 첫 고리에는 넣지 않는다(쌍 수가 적으면 불안정 [추정]).

### 2-3. 학습 자료 네 갈래에 대입

| 갈래 | 연구 대응 | 쓰는 법 | 주의 |
|---|---|---|---|
| (가) 실사용 대화(사람이 고쳐 주며 성공) | OEC(교정 뒤만 정답), DPO 쌍 | 교정 앞 모델 차례는 **문맥에는 남기고 손실에서만 가린다**. 교정 뒤 해낸 차례를 정답으로 | 사람 쪽 도구로만 내보냄. 비밀·개인정보가 가장 많은 갈래. 이 갈래로 만든 어댑터는 공개 금지 |
| (나) 더 센 모델의 성공 궤적 | 궤적 증류(SWE-smith·SERA·SWE-HERO) | 학습용 과제 풀에서 teacher 를 stewart 로 돌려 시험 통과분만 | teacher 약관(§3). student 에 없는 능력에 기대는 궤적은 뺌 |
| (다) 27B 자신의 성공 궤적 | 자기 증류(SWE-Gym rejection sampling) | 같은 과제를 여러 번 돌려 통과분, 과제당 상한 | 새 능력은 안 생김. 쏠림·자기 습관 굳히기 |
| (라) 실패를 고친 것 | OEC·교정 재표지 | 실패 자리에서 teacher 가 이어받은 뒷부분만 정답 | 이어받기 기능이 필요(되감은 상태에서 다른 백엔드로 잇기) — 2단계 |

SW-39 설계와의 차이 한 가지: SW-39 의 `after_fix` 는 "교정 표시가 붙은 차례는 뺀다"이다. OEC 근거로는 **빼지 말고 남기되 손실에서 가리는 쪽**이 "틀린 뒤 고침을 받는 상태"를 그대로 가르친다. 내보내기 꼴에 차례별 `train: true/false` 표시를 넣으면 두 방식을 다 쓸 수 있다.

### 2-4. 학습 입력은 "실행기가 실제로 보낸 것"과 같아야 한다

- 서빙 때 모델이 보는 글은 채팅 틀(chat template)이 만든다(도구 정의 자리, `<tool_call>` 꼴, 생각 블록 자리). 학습 때 다른 틀로 엮으면 배운 것이 서빙에서 안 나온다. Unsloth 도 "배포 때 같은 채팅 틀·EOS 를 쓰라"고 적는다[확인].
- 우리 모델 계열(판 이름 가림)은 `preserve_thinking` 이 기본 켬이지만[확인], stewart 는 OpenAI 길에서 지난 생각을 기본으로 되돌려 보내지 않는다(판 81, `STEWART_REASONING_PASSBACK` 기본 끔). → 학습 자료도 "지난 차례 생각은 빼고, 이번 차례 생각만 정답에" 로 엮어야 서빙과 같다.
- 문맥 압축이 낀 대화는 한 줄로 이어 붙일 수 없다. 걸음 단위(그 걸음에 실제로 보낸 messages → 그 걸음의 response)로 뽑으면 압축 뒤 꼴도 그대로 학습된다. 값은 토큰이 많이 든다는 것이다(앞부분을 걸음마다 다시 읽는다). → 기본은 "압축 경계 사이를 한 조각"으로, 조각이 창을 넘으면 앞에서부터 자른다.

## 3. 증류의 권리 — 약관 표

"개인 사용" = 내 장비에서 나만 쓰는 어댑터를 학습. "공개 배포" = 학습된 가중치·어댑터를 남에게 공개. 도구(코드)만 공개하는 것은 어느 약관과도 무관하다(도구는 남의 출력을 담지 않는다).

| 제공자·모델 | 조항(위치) | 개인 사용 | 공개 배포(가중치·어댑터) | 출처·표시 |
|---|---|---|---|---|
| Anthropic API (Commercial Terms, 2025-06-17) | D.4 Use Restrictions: 서비스에 접근해 "경쟁 제품·서비스를 만드는 것, 경쟁 AI 모델 학습 포함"을 금지(Anthropic 승인 예외). 출력 소유는 고객(B) | **금지로 본다**. "경쟁"의 범위가 개인 어댑터까지인지는 글만으로 불분명하나, Anthropic 은 2026-02 에 증류를 약관 위반으로 공개 대응했고 API 쪽 탐지를 돌린다 | 금지 | [확인] |
| Anthropic 구독(Consumer Terms 한국어판, 2025-10-08) | 제3조 2항: 경쟁 상품·서비스 개발(AI·ML 모델 개발·학습 포함) 금지. 7항: API 키·명시 허용 밖의 자동화 접속 금지 | 금지 | 금지 | [확인] |
| OpenAI (Business Terms 2026-01-01, Terms of Use) | "Permitted Exception 을 빼고, Output 을 OpenAI 제품·서비스와 경쟁하는 AI 모델 개발에 쓰지 않는다". 예외의 정의는 못 읽음 | 금지로 본다 | 금지 | [간접] — 원문 페이지가 403 이라 약관 추적 사이트 기록과 검색 요약으로만 확인 |
| Google Gemini API (Additional Terms, 2026-03-23) | Use Restrictions: "서비스와 경쟁하는 모델 개발에 서비스를 쓰지 못한다", 서비스 구성 요소의 추출·복제 시도 금지 | 금지로 본다 | 금지 | [확인] |
| **DeepSeek 개방 플랫폼 (2026-04-29 시행)** | §4.2(3): 입력·출력을 "다른 모델 학습(모델 증류 등)", 파생 제품 개발에 쓸 수 있다. §4.2(2): 출력의 권리를 사용자에게 양도. 경쟁 제한 조항 없음 | **허용(명시)** | 허용(조항상 제한 없음). 단 §5 상표·제휴 암시 금지 | [확인]. 함께 걸리는 일반 Terms of Use 는 이번에 안 읽음 [모름] |
| Z.ai API (Terms, 2026-04-14 갱신) | III.4(f): 직·간접 경쟁하는 알고리즘·모델의 개발·학습·개선 금지. Additional Terms 1.f.xii: 모델·프롬프트·생성물을 개발·학습·라벨링에 쓰는 것 금지(자기 사업 통합 예외) | **금지**(가장 넓게 적힘) | 금지 | [확인] |
| LG EXAONE 가중치 (EXAONE AI Model License 1.2-NC) | 2.1: 연구·교육 목적만. 3.1: 모델·파생물·**Output** 의 상업 사용 금지, Licensor 모델과 경쟁하는 모델의 개발·개선에 Output 사용 금지. 4.2: Output 권리는 주장 안 함 | 연구 목적이면 조건부(경쟁 모델 개발 금지에 걸릴 여지) — **쓰지 않는 쪽 권고** | 금지로 본다 | [확인] — 라이선스 DB 사본. EXAONE 4.5 가 1.2-NC 라는 것은 [간접] |
| LG EXAONE API | 내부 기록: 약관 2.1 비상업, 2.3 입력이 학습에 쓰일 수 있음. 출력의 학습 사용 조항은 이번에 못 읽음 | [모름] → 쓰지 않음 | [모름] → 금지 취급 | 내부 기록(EXAONE 붙이기 계획, 비공개 운영 기록) |
| Alibaba Model Studio (Qwen API) | 서비스별 약관에는 유료 추론 출력의 학습 사용을 막는 조항이 안 보임. 체험(trial) 출력은 "모델 평가 용도로만". 일반 약관·국제 제품 약관은 안 읽음 | [모름] — 유료 추론은 금지 조항 미발견 | [모름] | [확인: 서비스별 약관만] |
| 공개 가중치 Qwen 계열 27B(판 이름 가림), Qwen3.6-35B-A3B, Qwen3-Coder-480B | Apache 2.0 (모델 카드 표시) | 허용 | 허용(어댑터·병합본 모두, 고지 유지) | [확인] |
| 공개 가중치 DeepSeek-V4.1-Flash, GLM-5.3-Flash | MIT (모델 카드 표시) | 허용 | 허용 | [확인] |
| 공개 가중치 Kimi K3, 같은 Qwen 판의 Flash-Next·Max 계열(판 이름 가림) | 수정 MIT·qwen-community-1.0·매출 문턱 조건 등 맞춤 라이선스 | [모름] | [모름] | [간접] — 본문 미확인이라 teacher 후보에서 뺌 |
| abliteration 등 개인 파생판 | 보통 원본 라이선스를 따르나 올린 사람 표기에 달림 | 허용 [추정] | 원본이 Apache 면 허용 [추정] | [모름] |

풀이:
- **금지되는 넷(Anthropic·OpenAI·Google·Z.ai API)**: "경쟁 모델"이 개인용 27B 어댑터를 포함하는지는 조항만으로는 확정할 수 없다. 그러나 (1) 글자대로 읽으면 "코딩 에이전트 모델"은 그들 제품과 같은 일을 한다 (2) Anthropic 은 증류 탐지(추론 끌어내기·좁은 능력에 몰린 대량 요청·반복 꼴)를 API 에 돌리고 계정 검증을 조였다고 공개했다[확인] (3) 걸리면 계정이 닫히고 그 계정은 실사용에도 쓰는 것이다. → **이 넷의 출력은 학습 자료에 넣지 않는다.** 같은 실행기로 돌린 Claude·GPT 궤적은 "비교·상한 측정"에만 쓴다.
- **내보내기에서 자동으로 걸러야 하는 것**: 사람이 실사용에서 Claude·GPT 백엔드로 돈 대화는 (가) 갈래에 섞여 들어온다. 기록의 `meta.model` 로 백엔드를 보고 약관 등급을 붙여 기본으로 뺀다(§4 ②).
- **안전한 teacher**:
  - DeepSeek 직접 API(`deepseek-flash` = V4.1-Flash). 약관이 증류를 명시 허용, 값도 가장 쌈(작업당 $0.027–0.053, 내부 조사). 생각을 켜면 `reasoning_content` 가 온다(학습 자료로 쓸 수 있음). 단 서버가 중국이고 입력의 학습 사용 여부가 약관에 없다(내부 조사) → **teacher 에게 주는 과제는 공개 과제만**(사람의 코드·대화를 teacher 에게 보내지 않는다).
  - 공개 가중치를 제3 호스트(OpenRouter 경유 DeepInfra 등)나 빌린 장비에서: DeepSeek-V4.1-Flash(MIT), GLM-5.3-Flash(MIT, 320B/활성 18B), Qwen3-Coder-480B(Apache 2.0 — CoderForge·SWE-HERO 의 teacher). 가중치 라이선스에는 출력 제한이 없다. 다만 **호스트의 자체 약관**이 따로 걸린다 — 호스트별 조항은 이번에 안 읽었다[모름]. Z.ai 직접 API 의 금지는 Z.ai 서비스에 대한 것이고, 같은 MIT 가중치를 남이 돌려 주는 서비스에는 그 호스트의 약관이 걸린다 [추정].
  - 27B 자신(Apache 2.0).

## 4. 권고 설계 — 자가발전 고리 일곱 단계

원칙 셋:
1. **본체는 가볍게**: 본체 의존성은 prompt_toolkit·rich 둘[확인]. torch·peft 같은 수 GB 의존성은 본체에 넣지 않는다.
2. **자료가 있는 곳과 학습하는 곳이 다르다**: 실사용 기록은 사람의 자리(개발 담당이 못 봄), 학습은 빌린 장비. 사이를 잇는 것은 "꼴이 정해진 파일 묶음 + 해시 목록"뿐이어야 한다.
3. **모든 단계는 되돌릴 수 있게**: 채택은 설정 한 줄, 되돌리기도 한 줄.

| 단계 | 하는 일 | stewart 본체 | 별도 도구(공개) | 까닭 |
|---|---|---|---|---|
| ① 기록·표시·내보내기 | 차례별 좋음·나쁨·교정 표시, 걸음별 요청·응답 원문, JSONL 내보내기 | **전부 본체**(SW-39). 추가 권고: `reasoning_content` 싣기, 차례별 `train` 표시, `meta` 에 백엔드·모델·제공자·판, 압축 경계 표시 | — | 기록 꼴을 아는 것은 본체뿐. 사람의 셸에서만 돈다 |
| ② 걸러 내기·형식 맞추기 | 약관 등급 거르기, 평가 과제 해시 대조, 비밀 재검사, 중복·과제당 상한, 토큰 통계, 학습·검증 나누기, 모델별 채팅 틀로 엮기, 손실 가림 표, 묶음 해시 목록(manifest) | 비밀 가림 첫 단(`nostore.scrub` 등 이미 있음) | **prep** | 틀 엮기는 tokenizer(transformers)가 필요. 다른 실행기 사용자도 쓸 수 있는 일반 기능 |
| ③ teacher 궤적 만들기 | 학습용 과제 풀을 teacher 백엔드로 돌리고 시험 통과분만 남김 | **본체**(이미 있는 것의 조합: 인스턴스·백엔드 손잡이·batch). 새로 넣을 것: 과제 풀 지정, 통과 판정을 기록에 남기기, 회차 예산 상한 | 과제 풀 꾸러미(공개 과제 목록·시험 명령)는 도구 쪽 자료로 | "같은 실행기로 돌린 궤적"이어야 하므로 실행기 자신이 돈다 |
| ④ 학습 | QLoRA 실행, 설정·씨앗·판 고정, 손실 곡선·검증 손실 기록 | — | **train**(얇은 껍데기: Unsloth/PEFT+TRL 또는 mlx-lm 을 부르는 설정 생성 + 실행) | 무거운 의존성·GPU. 빌린 장비·맥·(작은 모델이면) 작은 GPU 장비에서 따로 돈다 |
| ⑤ 평가 문턱 | 기준(어댑터 없음) 대 후보를 같은 과제·같은 회차 수로 돌려 비교, 채택·기각 기록 | 평가 실행(기존 평가기·bench) | **gate**(두 결과 파일을 받아 짝 비교 통계와 판정만) | 판정 규칙은 실행기와 무관. 평가 묶음 내용은 도구가 보지 않고 과제 id·통과 여부만 받는다 |
| ⑥ 서빙에 올리기·되돌리기 | 어댑터 → (a) vLLM 런타임 적재 (b) 병합·재양자화 (c) GGUF 어댑터. 판 장부, 한 줄 되돌리기 | 백엔드 설정에 어댑터 고르기(vLLM 은 `model` 이름, llama.cpp 는 요청의 `lora` 필드), 허브에 "지금 판" 표시, 되돌리기 명령 | **pack**(병합, GGUF 어댑터 변환 호출, 재양자화 각본, 해시) + **registry**(판 장부) | 변환은 torch·llama.cpp 스크립트가 필요. 고르기·되돌리기는 본체 설정 일 |
| ⑦ 학습 없는 판 | 학습 없는 자가발전 | 예시 은행·스킬 뽑기·지시문 고치기(§5) | (장비별 운영 몫은 비공개 운영 기록) | (비공개 운영 기록) |

### 별도 도구의 범위

- 하는 일: `prep` · `train` · `pack` · `gate` · `registry` 다섯 명령. 입력은 "OpenAI chat 꼴 JSONL + meta" 하나로 정해 stewart 가 아닌 실행기도 쓸 수 있게 한다.
- 하지 않는 일: 실행기 기록 꼴 읽기(본체 몫), 평가 과제 보관, 모델 내려받기 자동화, 서버 띄우기.
- 의존성(선택 묶음으로 나눔):
  - 기본: 표준 라이브러리 + `tokenizers` 정도(통계·gate·registry 는 이것만으로).
  - `[cuda]`: torch, transformers(v5 — Unsloth 의 우리 모델 계열 문서 요구[확인]), peft, trl, bitsandbytes, 선택으로 unsloth.
  - `[mlx]`: mlx-lm.
  - 변환: llama.cpp 저장소의 `convert_lora_to_gguf.py`·`convert_hf_to_gguf.py` 를 경로로 받아 부른다(끼워 넣지 않는다).
- 이름 후보(충돌 여부는 검색하지 않았다 [모름]): `trajtune`, `loopsmith`, `retrace-ft`, `adapter-loop`. 실행기 이름을 넣지 않는 쪽을 권한다(다른 실행기 사용자도 쓰게).
- 공개 대상: 도구 코드·설정 예시·공개 과제 풀 목록. **공개하지 않는 것**: (가) 갈래가 한 건이라도 들어간 자료·어댑터, 봉인 평가 관련 일체.

### 단계별 세부

**① 내보내기 꼴(SW-39 에 더할 것)**
- 한 줄 = 한 조각: `{"messages": [...], "tools": [...], "meta": {...}}`. assistant 메시지에 `reasoning_content`·`tool_calls` 를 원문대로, 그리고 차례별 `train` 불리언.
- `meta`: run id(가명), 백엔드 종류·모델 이름·제공자, 실행기 판, 프로필, 갈래(가·나·다·라), 표시(좋음·교정됨·시험 통과), 과제 지문 정규화 해시, 가린 비밀 수.
- 도구 정의(`tools`)는 그 걸음에 실제로 보낸 것 그대로(도구 묶음이 바뀌면 학습 입력도 달라진다).

**② prep 이 기본으로 거르는 것**
- 약관 등급: `meta` 의 제공자·모델을 표(§3)에 대어 `train-ok`(자기 27B·DeepSeek API·MIT/Apache 가중치) / `private-eval-only`(Anthropic·OpenAI·Google·Z.ai API·EXAONE) / `unknown` 으로 나누고 `train-ok` 만 통과. 표는 설정 파일이고 날짜를 적는다(약관은 바뀐다).
- 평가 누출: 평가기 쪽이 내는 해시 목록(내용 없음)과 과제 지문 해시 대조. 공개 벤치는 인스턴스 id·저장소 이름 목록으로도 막는다(SWE-bench Verified 의 저장소는 학습 풀에서 통째로 뺀다).
- 비밀: 내보내기가 먼저 가렸어도 다시 훑는다(키 꼴·사설 주소·홈 경로·사용자 이름). 걸리면 그 조각을 버리고 수만 센다.
- 품질: 시험 통과(갈래 나·다), 사람 좋음(갈래 가), 오류 뒤 재시도 비율 상한(Han 의 신호), 과제당 상한, 길이 상한(순서 자르기).
- 산출: `train.jsonl`·`valid.jsonl`(과제 단위로 나눔 — 같은 과제가 양쪽에 들어가지 않게)·`manifest.json`(파일별 sha256, 갈래별 건수, 거른 까닭별 수, 약관 표 판).

**④ 학습 기본값(첫 판)**
- QLoRA, r 16, α 16, dropout 0, lr 2e-4, 1–2 epoch, 배치 1 + 기울기 누적(Unsloth 의 우리 모델 계열 문서의 기본[확인]). 대상 모듈은 주의·MLP 투영, **임베딩·lm_head 는 학습하지 않는다**(vLLM 양자화 기반에서 어댑터의 임베딩 가중치가 "지원 안 됨" 오류를 낸 사례[간접]).
- 시각 층은 얼린다(`finetune_vision_layers=False`[확인]). 사진 입력은 서빙에서 그대로 쓰되 어댑터는 글 쪽만 고친다. vLLM 의 다중 모달 탑·연결부 LoRA 는 실험 단계다[확인].
- **기반 가중치는 서빙하는 것과 같은 계열로**: 서빙이 abliteration 판의 양자화라면 학습도 그 판의 원 정밀도 가중치 위에서 한다. 원본에서 배운 어댑터를 다른 파생판에 얹는 것은 어긋남이 생긴다 [추정].
- 검증 손실이 오르기 시작하면 멈춘다(과적합). 수백 건에서는 1 epoch 뒤부터 오르는 일이 흔하다 [추정].

**⑥ 서빙 길 셋 — 지금 되는가**

| 길 | 상태 | 알려진 문제 | 판정 |
|---|---|---|---|
| (a) vLLM 런타임 LoRA: `--enable-lora --lora-modules 이름=경로`, 요청의 `model` 로 고름. `/v1/load_lora_adapter`(`load_inplace` 로 같은 이름 갈아 끼우기), MoE 용 `--enable-mixed-moe-lora-format` | 문서에 있음[확인] | 양자화 기반과의 조합은 공식 문서에 없다. 포럼 답(자동 응답 봇·사용자)은 GPTQ·AWQ 는 되고 bitsandbytes 는 따로라고 한다[간접]. **AutoRound W4A16 + LoRA: 확인된 글 없음[모름]**. **MTP 초안 머리 + LoRA: 확인된 글 없음[모름]** — Eagle3 추측 디코딩 + LoRA 에서 출력이 LoRA 단독과 달라진다는 이슈(#26679)는 답 없이 닫혔다[확인]. 런타임 갱신 환경 변수는 문서가 "보안 위험, 운영에 쓰지 말라"고 적는다[확인] | **첫 실험에서 10분 시험.** 되면 가장 좋다(기반 파일 그대로, 판 여럿을 이름으로 고름, 되돌리기 = 이름 바꾸기) |
| (b) 병합 → 재양자화(AutoRound W4A16) → 지금과 같은 꼴로 서빙 | 원리상 언제나 됨 | 판마다 약 17–18GB 파일이 새로 생기고 양자화 시간이 든다(27B AutoRound 소요 [모름], 빌린 장비 시간 = 돈). MTP 머리는 원 정밀도로 따로 실어야 한다(AutoRound 가 MTP 층까지 양자화하면 vLLM 로더가 거부한 사례[간접]). 병합 뒤에는 초안 머리가 본체와 덜 맞아 수락률이 떨어질 수 있다 [추정] | (a) 가 안 될 때의 확실한 길. 채택된 판만 병합 |
| (c) llama.cpp GGUF 어댑터: `convert_lora_to_gguf.py` 로 변환, `--lora`·`--lora-scaled`, `--lora-init-without-apply`, `GET/POST /lora-adapters`, 요청의 `lora` 필드로 판별 켬끔 | 문서에 있음[확인]. 변환기에 MoE LoRA 변환 지원이 들어가 있다[간접] | 서로 다른 LoRA 설정의 요청은 한 배치로 묶이지 않는다[확인]. 요청별 `lora` 필드는 `/completion` 문서에만 보였다(chat 쪽 [모름]). 35B-A3B 에서 실제로 되는지 확인된 글은 없다[모름] — 공개된 어댑터들은 전문가 256개·라우터를 얼리고 주의 + 공유 전문가만 학습한다[간접]. Qwen3.6-A3B GGUF 는 upstream llama.cpp 로더 문제 보고가 있었다[간접, 낡았을 수 있음] | 로컬 맥·llama.cpp 길. 쓰기 전 리허설에 넣는다 |

되돌리기: (a)·(c) 는 어댑터를 빼거나 scale 0 이면 끝. (b) 는 이전 판 파일을 지우지 않고 두었다가 서버를 그 파일로 다시 띄운다.

### 학습 도구 지형(지금 쓸 만한 것)

| 도구 | 쓸 자리 | 확인한 것 | 모르는 것 |
|---|---|---|---|
| Unsloth | 빌린 3090·48GB 에서 27B QLoRA | Qwen 계열 27B(판 이름 가림): QLoRA 24GB, LoRA 36GB 초과, 전체는 약 4배. 4비트 시작점(Unsloth 의 bnb-4bit 판 — 저장소 이름 가림). GGUF·병합 16비트·어댑터만 내보내기. Windows 설치 안내 있음[확인] | 문서의 24GB 는 `max_seq_length=2048` 예시 기준이다. 궤적은 한 조각이 수천–수만 토큰이라 24GB 에서 어디까지 되는지 [모름] — 48GB 를 권하는 까닭 |
| PEFT + TRL (+ bitsandbytes) | 같은 자리. 컴파일 없는 가장 단순한 조합, 오프라인 휠 묶음에 맞음 | 표준 조합 | 속도는 Unsloth 보다 느림(Unsloth 주장 1.5배[확인: 업체 주장]) |
| Axolotl · LLaMA-Factory | 설정 파일 하나로 도는 대안 | 이번에 따로 확인하지 않음 | 우리 모델 계열(새 구조: Gated DeltaNet) 지원 여부 [모름] |
| mlx-lm | 맥에서 리허설·작은 모델 | lora·dora·full, 자료 꼴 chat·**tools**·completions·text, 이미 양자화된 모델이면 자동 QLoRA, `--num-layers`·`--grad-checkpoint`·배치로 메모리 줄임. 32GB 에서 Mistral 7B 가 배치 1·4층으로 돈다[확인] | GGUF 내보내기는 Mistral·Mixtral·Llama 꼴 fp16 뿐[확인] → Qwen 어댑터를 llama.cpp 로 바로 못 가져간다. 우리 모델 계열 구조 지원 [모름] |
| llama.cpp 자체 학습(`examples/training`) | 쓰지 않음 | "기술적으로 동작하나 매우 WIP", FP32 모델만, 1B 급에 24GB[확인] | — |

메모리별 어림(전부 [추정], 조각 길이 4k 토큰·배치 1 가정):
- 12GB: 4비트 7–8B 까지 편함, 12B 는 빠듯. 27B·35B MoE 는 안 됨.
- 24GB(3090): 27B QLoRA 가 짧은 조각(2k)에서 됨[확인], 4–8k 는 빠듯.
- 48GB(A6000 약 $0.33–0.36/h[간접]): 27B QLoRA 8–16k 조각.
- 74GB 이상: 35B-A3B bf16 LoRA(Unsloth 표)[간접].
- 맥 16GB: 4B 이하 / 32GB: 7–8B / 64GB: 12–14B 편함, 27B 4비트는 층 수를 줄여 느리게 가능 [추정].

## 5. 학습 없는 자가발전

(장비별 운영 몫 — 작은 장비에 무엇을 들이고 어디까지 되는지 — 은 비공개 운영 기록이다.)

가중치를 안 고치고 "모델에게 주는 글"을 궤적에서 뽑아 고치는 방식이다. 장점: GPU 학습 불필요, 고친 것이 사람이 읽는 글이라 검토·되돌리기가 쉽고, 비밀이 가중치에 새겨지지 않는다.

| 방식 | 하는 일 | 근거 | stewart 에 이미 있는 발판 |
|---|---|---|---|
| 예시 은행(few-shot) | 성공 궤적에서 "상황 → 올바른 도구 호출" 조각을 모아 두고, 비슷한 과제 때 한두 개를 문맥에 넣음 | ReasoningBank(Ouyang 외, arXiv 2509.25140, ICLR 2026): 성공·실패 모두에서 전략을 뽑아 기억으로, 원 궤적 저장·성공 절차만 저장보다 낫다고 보고 | 기억 세 갈래(memstore), doc_search(BM25) |
| 문맥 고쳐 쓰기 | 실패를 되짚어 "다음엔 이렇게"를 누적 규칙집으로 | ACE(Zhang 외, arXiv 2510.04618): 에이전트 벤치 +10.6% | policy.md·되짚기(reflect) |
| 지시문 최적화 | 궤적을 읽고 지시문 후보를 내고, 개발용 과제로 재서 좋은 것만 남김 | GEPA(Agrawal 외, arXiv 2507.19457, ICLR 2026): LoRA 를 쓴 강화학습(GRPO 24,000회) 대비 평균 +6%, 실행 횟수 최대 35배 적게 | 프로필(toml), 평가 손잡이 |
| 스킬 뽑기 | 되풀이되는 절차를 스킬 파일로 | 스킬 라이브러리 계열 | 스킬(내장 16), procedure.md·replay |
| 실행기 자체 고치기 | 관찰 기록을 보고 도구·미들웨어·기억을 고침 | AHE(Lin 외, arXiv 2604.25850): Terminal-Bench 2 에서 69.7% → 77.0%, 이득은 지시문이 아니라 도구·미들웨어·장기 기억에서 | 개선 계획의 "다-1"(뒤로 미뤄 둠, [plan.md](plan.md) §2) |

학습과의 비교:
- teacher 가 없는 곳(인터넷 없음)에서는 뽑고 고치는 일도 그 자리의 서빙 모델 자신이 한다. 품질 상한이 낮아 **사람 승인이 꼭 낀다**(뽑은 예시·규칙을 사람이 훑고 채택).
- 문맥을 먹는다(예시 하나에 수백–수천 토큰). 14–16 tok/s 급 느린 장비에서는 예시 한두 개가 한계 [추정].
- 학습은 "매번 문맥에 안 넣어도 되는 습관"을 만든다. 둘은 겹치지 않는다: 자주 쓰이고 안정된 예시는 다음 학습 자료가 되고, 학습으로 굳은 것은 은행에서 뺀다.
- GEPA 가 "LoRA 강화학습보다 적은 시도로 더 낫다"고 보고한 것은 과제 묶음이 작을 때 글 고치기가 가중치 고치기보다 표본 효율이 좋다는 뜻이다. **자료가 수백 건인 지금 단계에서는 27B 에서도 학습 없는 길을 먼저·같이 돌리는 것이 맞다.**

## 6. 위험과 안전장치

| 위험 | 어떻게 생기나 | 대응 |
|---|---|---|
| 자기 출력으로 학습해 나빠짐 | 자기 궤적만 되먹이면 드문 행동이 사라지고 쉬운 과제 습관이 굳는다(Shumailov 외, arXiv 2305.17493: 생성물로 거듭 학습하면 분포 꼬리가 사라짐) | 갈래 (다) 비중 상한(예: 전체의 절반 이하 [추정]), 과제당 상한, 매 회차 **원본 기반 + 누적 자료 전체**로 새로 학습(어댑터 위에 어댑터 금지), teacher·사람 교정 자료를 섞음 |
| 평가 누출 | 평가 과제나 그 풀이가 학습에 섞임. 또는 같은 문턱 묶음으로 여러 번 채택 판정을 하다 보면 그 묶음에 맞춰짐 | (1) prep 의 해시·id·저장소 대조 (2) 평가를 세 단으로: **개발용**(자주, 내용 봐도 됨) / **문턱용**(채택 판정에만, 통과 여부만 돌려받고 판정 횟수를 장부에 셈) / **봉인**(분기에 한 번쯤, 학습·판정 고리가 절대 못 봄) (3) teacher 에게도 평가 과제를 주지 않는다 |
| 비밀·개인정보가 가중치에 새겨짐 | 실사용 대화의 키·주소·이름·고객 코드가 학습 자료로 들어감. 학습된 모델에서 원문이 되나올 수 있다(Carlini 외) | 내보내기 가림 + prep 재검사(걸리면 조각 폐기), (가) 갈래가 든 어댑터는 **공개 금지·빌린 장비에 남기지 않기**(학습 뒤 장비에서 자료·체크포인트 삭제 확인), 자료 묶음에 "포함된 실사용 조각 수"를 적어 판 장부에 남김. 빌린 장비는 남의 기계이므로 (가) 갈래 학습은 "믿을 수 있는 장비"에서만 할지 사람 결정(§8) |
| 원치 않는 습관 강화 | 사람이 "좋음"을 누른 것 가운데 우연히 섞인 버릇(지나친 확인 질문, 특정 도구 편애, 말투)이 굳음 | 자료 묶음 확정 전 `show` 로 표본 훑기(사람), 채택 뒤 며칠은 "새 판" 표시를 허브에 띄우고 나쁨 표시가 늘면 자동으로 알림, 행동 통계(단계 수·도구 분포·질문 빈도)를 기준과 나란히 보고 |
| 되돌리기 불가 | 병합본만 남기고 원본·이전 판을 지움 | 기반 파일은 읽기 전용으로 두고 절대 덮지 않음. 판 장부(registry)에 판마다: 기반 모델 해시, 자료 manifest 해시, 학습 설정·씨앗·도구 판, 평가 결과, 부모 판, 채택·기각과 까닭. 되돌리기는 설정 한 줄 |
| 약관 위반 자료 혼입 | 실사용에서 Claude·GPT 백엔드로 돈 대화가 (가) 에 섞임 | prep 의 약관 등급 거르기(기본 거부), manifest 에 제공자별 건수 |
| 공급망 | 학습 도구·휠·기반 모델을 빌린 장비에서 내려받음 | 판 고정(잠금 파일·모델 revision 해시), 내려받은 기반 모델 해시를 장부에. 어댑터는 safetensors 만(pickle 꼴 체크포인트를 들이지 않는다) |
| 런타임 어댑터 갈아 끼우기 통로 | vLLM 의 런타임 적재 끝점은 문서가 보안 위험이라 적는다[확인] | 켜지 않는다. 어댑터는 서버 시작 때 `--lora-modules` 로만 싣고, 바꾸려면 서버를 다시 띄운다(사람 손 또는 래퍼) |

**채택 문턱(제안)**: 후보 판은 다음을 모두 넘을 때만 채택한다.
1. 문턱용 묶음에서 기준 대비 통과율이 나빠지지 않음(같은 과제·같은 회차 수의 짝 비교, 미리 정한 여유 안). "오름"을 요구하지 않고 "안 나빠짐 + 아래 2번 가운데 하나가 좋아짐"으로 둔다.
2. 겨냥한 지표 하나 이상 개선: 도구 호출 꼴 오류 수(components 의 `broken_toolcall`), 과제당 단계 수, 사람 교정 비율.
3. 실행기 무관 건전성: 학습에 없던 종류의 과제 몇 개(한국어 대화, 사진 입력 한 건)에서 눈에 띄는 퇴행 없음.
4. 사람 승인.

**사람 승인 지점은 둘**: (1) 자료 묶음 확정(manifest 와 표본을 보고) (2) 어댑터 채택. 그 사이(학습·평가)는 자동으로 돌아도 된다. 돈이 드는 장비 빌리기는 기존 장비 빌리기 승인 규칙을 그대로 따른다.

## 7. 가장 싼 첫 실험 — 끝까지 한 번 도는지

목표는 점수가 아니라 **고리 한 바퀴**(내보내기 → prep → 학습 → 서빙 → 문턱 → 되돌리기)와 **모르는 것 셋**(AutoRound + LoRA, MTP + LoRA, 24GB 에서 되는 조각 길이)을 가리는 것이다.

### E0 — 돈 0, 맥에서 리허설

| 항목 | 내용 |
|---|---|
| 자료 | 시험대(testbed)에서 만든 가짜·공개 과제 궤적 30–50건(갈래 다 흉내). 사람의 실사용 자료는 쓰지 않는다 |
| 모델 | 맥에서 도는 작은 4비트 모델(맥 메모리 [모름] — 16GB 면 4B 이하, 32GB 면 7–8B) |
| 도구 | mlx-lm(`tools` 자료 꼴 지원[확인]), 서빙은 mlx-lm 의 OpenAI 호환 서버 |
| 시간 | 학습 수십 분 [추정] |
| 성공 판정 | manifest 가 나오고, 평가 해시와 겹치게 심어 둔 조각 하나·비밀을 심어 둔 조각 하나가 걸러지고, 어댑터를 얹은 서버로 stewart 개발용 과제가 돌고, gate 가 판정을 내고, 어댑터를 빼면 기준 출력으로 돌아온다 |
| 한계 | 서빙 길이 실사용(vLLM·llama.cpp)과 다르다. 고리와 도구의 뼈대만 본다 |

### E1 — 빌린 장비 한 번(어림 $10–20 [추정])

| 항목 | 내용 |
|---|---|
| 자료 | 학습용 공개 과제 풀에서 100과제 안팎(평가 묶음·SWE-bench Verified 저장소와 겹치지 않는 것). (다) 27B 를 과제당 2–3회 돌려 통과분 + (나) DeepSeek `deepseek-flash` 를 같은 과제에 돌려 통과분. 합쳐 통과 궤적 100–200건을 목표 |
| 자료 값 | teacher: 100과제 × $0.027–0.053 × 실패 포함 1.5배 = 약 $4–8 [추정]. 27B 쪽은 장비 시간에 포함 |
| 학습 | Qwen 계열 27B(판 이름 가림 — 서빙과 같은 계열의 원 정밀도 가중치) QLoRA, r 16, 1–2 epoch. 조각 길이는 24GB 에서 2k → 4k → 8k 로 올려 보며 어디서 메모리가 넘치는지 적는다. 넘치면 48GB 급(약 $0.33–0.36/h[간접])으로 |
| 장비·시간 | 3090 한 장 $0.2–0.6/h. 자료 만들기 6–8시간(지금 처리량 기준 [추정]) + 학습 2–4시간 [추정 — 학습 처리량은 처음 50걸음을 재서 다시 셈] + 시험 1시간 = 약 10–13시간 → $2–8 |
| 서빙 시험 | (1) 지금 쓰는 AutoRound W4A16 기반에 `--enable-lora --lora-modules` 로 얹어 뜨는지, 출력이 기준과 달라지는지(어댑터가 실제로 먹는지) (2) 초안 머리(MTP)를 켠 채로도 같은지, 수락률·tok/s 변화 (3) 안 되면 GGUF 길: 병합 없이 `convert_lora_to_gguf.py` → `llama-server --lora` |
| 평가 | 개발용 과제(봉인·문턱용 아님) 30과제 × 3회, 기준 대 후보. 통과율·단계 수·도구 호출 꼴 오류 수 |
| 성공 판정 | (가) 고리가 사람 손 없이(승인 두 곳 빼고) 끝까지 돎 (나) 서빙 길 (a)·(c) 가운데 하나 이상이 됨이 가려짐 (다) 후보가 개발용에서 기준보다 나쁘지 않음 (라) 되돌린 뒤 기준과 같은 결과 (마) 24GB 에서 되는 조각 길이와 학습 처리량이 숫자로 남음 |
| 상한 | $20. 넘을 것 같으면 자료를 50과제로 줄인다 |
| 하지 않는 것 | 실사용 대화(가) 사용, 봉인·문턱 묶음 사용, 어댑터 공개 |

E1 뒤에 정할 것: 서빙 길, 조각 길이, 한 바퀴의 실제 값. 그 다음이 (가) 갈래 투입과 교정 학습·DPO 다.

## 8. 사람에게 물어야 할 결정(돈·방향·권리만)

1. **증류 teacher 범위(권리)**: "약관이 명시로 허용하거나 제한이 없는 것만"(DeepSeek 직접 API, MIT·Apache 공개 가중치, 27B 자신)으로 하고 Claude·GPT·Gemini·GLM 직접 API·EXAONE 출력은 학습에서 빼는 것을 권한다. 이대로 갈지.
2. **첫 실험 예산(돈)**: E1 상한 $20, teacher 로 DeepSeek 직접 API 를 쓰려면 최소 충전이 필요하다(금액 [모름]). 승인할지.
3. **공개 범위(권리)**: 도구만 공개하고, 실사용 대화가 한 건이라도 들어간 자료·어댑터는 공개하지 않는다. 공개 과제 + 허용 teacher 로만 만든 어댑터를 따로 공개할지는 나중 결정으로 둘지.
4. **실사용 대화를 어디서 학습할지(방향·보안)**: (가) 갈래는 빌린 남의 장비에 올라간다. 빌린 장비에서 해도 되는지, 로컬 장비(구매 결정과 묶임 — 48GB 급이면 로컬에서 27B QLoRA 가 된다)가 생길 때까지 (가) 는 "학습 없는 길"에만 쓸지.
5. **학습 기반 가중치(방향)**: 실사용이 abliteration 판이면 그 판 위에서 학습해야 맞는다. 공개용 어댑터를 따로 낼 때는 원본 기반으로 한 벌 더 만들어야 한다(값 두 배). 어느 쪽을 먼저 할지.

## 9. 이번에 확인하지 못한 것

- OpenAI 약관 원문(403). "Permitted Exception" 의 정의.
- DeepSeek 일반 Terms of Use, Alibaba 일반·국제 제품 약관, EXAONE API 약관의 출력 조항, 제3 호스트(OpenRouter·DeepInfra·Together 등)의 출력 사용 조항.
- Kimi K3·같은 Qwen 판의 Flash-Next·Max 라이선스 본문.
- vLLM: AutoRound W4A16 기반 + LoRA, MTP + LoRA 의 실제 동작. llama.cpp: 35B-A3B 에 `--lora`, chat 끝점의 요청별 `lora`.
- 27B QLoRA 의 학습 처리량(tok/s)과 조각 길이별 VRAM. AutoRound 재양자화 소요 시간.
- SWE-Gym 491건 결과의 학습 방식(LoRA/전체). 27–32B 급 LoRA 만으로 공개 벤치를 올린 사례.
- Axolotl·LLaMA-Factory·mlx-lm 의 우리 모델 계열 구조 지원.
- 학습용 공개 과제 풀(SWE-smith·SWE-Gym·R2E-Gym)과 SWE-bench Verified·Terminal-Bench 의 저장소 겹침 목록.
- 로컬 맥 메모리.
- 별도 도구 이름 후보의 충돌 여부.

## 10. 출처(모두 2026-11-12 확인)

연구
- SWE-Gym: Pan, Wang, Neubig, Jaitly, Ji, Suhr, Zhang. "Training Software Engineering Agents and Verifiers with SWE-Gym". ICML 2025. https://arxiv.org/abs/2412.21139
- SWE-smith: Yang, Lieret, Jimenez, Wettig, Khandpur, Zhang, Hui, Press, Schmidt, Yang. "SWE-smith: Scaling Data for Software Engineering Agents". 2025. https://arxiv.org/abs/2504.21798
- SERA: Shen, Tormoen, Shah, Farhadi, Dettmers. "SERA: Soft-Verified Efficient Repository Agents". 2026. https://arxiv.org/abs/2601.20789 (본문 https://arxiv.org/html/2601.20789v3)
- CoderForge-Preview(Together AI, 2026-02-25): https://together.ai/blog/coderforge-preview
- Ludwig, Ahmad, Majumdar, Ginsburg. "From SWE-ZERO to SWE-HERO: Execution-free to Execution-based Fine-tuning for Software Engineering Agents". 2026. https://arxiv.org/abs/2604.01496
- Han. "A Systematic Evaluation of Trajectory Data Curation for LoRA Fine-Tuning of Code Agents". 2026. https://arxiv.org/abs/2607.17205
- Lauffer, Deng, Kundurthy, Kenstler, Da. "Imitation Learning for Multi-turn LM Agents via On-policy Expert Corrections". 2025. https://arxiv.org/abs/2512.14895
- Yu, Cheng, Wu, Xing. "Building Coding Agents via Entropy-Enhanced Multi-Turn Preference Optimization". 2025. https://arxiv.org/abs/2509.12434
- Li, Zeng, Fang, Liang, Song, Liu, Mi, Yu. "Verified Critical Step Optimization for LLM Agents". 2026. https://arxiv.org/abs/2602.03412
- Kang, Jeong, Lee, Cho, Hwang. "Distilling LLM Agent into Small Models with Retrieval and Code Tools". NeurIPS 2025. https://arxiv.org/abs/2505.17612
- Zhong, Zheng, Song, Lin, Sun, Jiang, Wang, Fang. "SOD: Step-wise On-policy Distillation for Small Language Model Agents". 2026. https://arxiv.org/abs/2605.07725
- SmartAD(ACL Findings 2026, 검색 요약으로만): https://preview.aclanthology.org/ingest-acl/2026.findings-acl.1349/
- Biderman 외. "LoRA Learns Less and Forgets Less". TMLR 2024. https://arxiv.org/abs/2405.09673
- Shuttleworth 외. "LoRA vs Full Fine-tuning: An Illusion of Equivalence"(검색 요약으로만). https://arxiv.org/abs/2410.21228
- Shumailov, Shumaylov, Zhao, Gal, Papernot, Anderson. "The Curse of Recursion: Training on Generated Data Makes Models Forget". 2023. https://arxiv.org/abs/2305.17493
- Carlini 외. "Extracting Training Data from Large Language Models". 2020. https://arxiv.org/abs/2012.07805
- Agrawal 외. "GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning". ICLR 2026(검색 요약으로만). https://arxiv.org/abs/2507.19457
- Ouyang 외. "ReasoningBank: Scaling Agent Self-Evolving with Reasoning Memory". ICLR 2026. https://arxiv.org/abs/2509.25140
- Zhang 외. "Agentic Context Engineering: Evolving Contexts for Self-Improving Language Models". 2025. https://arxiv.org/abs/2510.04618
- Lin 외. "Agentic Harness Engineering: Observability-Driven Automatic Evolution of Coding-Agent Harnesses". 2026. https://arxiv.org/abs/2604.25850

약관·라이선스
- Anthropic Commercial Terms: https://www.anthropic.com/legal/commercial-terms
- Anthropic Consumer Terms: https://www.anthropic.com/legal/consumer-terms
- Anthropic "Detecting and preventing distillation attacks": https://www.anthropic.com/news/detecting-and-preventing-distillation-attacks
- OpenAI Business Terms 조항 기록(제3자 추적): https://conductatlas.com/platform/openai/openai-business-terms/provision/CA-P-059973/prohibition-on-using-output-to-develop-competing-ai-models/ · 원문(403 으로 못 읽음): https://openai.com/policies/business-terms/ , https://openai.com/policies/services-agreement/
- Gemini API Additional Terms: https://aistudio.google.com/docs/terms
- DeepSeek Open Platform Terms of Service: https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html
- Z.ai Terms of Use: https://docs.z.ai/legal-agreement/terms-of-use
- EXAONE AI Model License Agreement 1.2-NC(라이선스 DB 사본): https://scancode-licensedb.aboutcode.org/exaone-ai-model-1.2-nc.LICENSE
- Alibaba Cloud Model Studio 서비스별 약관: https://help.aliyun.com/en/model-studio/bailian-service-notes
- 모델 카드: Qwen 계열 27B(판 이름 가림 — 링크 뺌) · https://huggingface.co/Qwen/Qwen3.6-35B-A3B · https://huggingface.co/Qwen/Qwen3-Coder-480B-A35B-Instruct · https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash · https://huggingface.co/zai-org/GLM-5.3-Flash
- 같은 Qwen 판의 Max 모델 라이선스 보도(간접 — 기사 주소에 판 이름이 들어 링크 뺌)

도구
- Unsloth 의 우리 모델 계열 학습 문서(주소에 판 이름이 들어 링크 뺌)
- Unsloth Qwen3.5 미세조정 안내(35B-A3B 74GB·MoE QLoRA 비권장, 검색 요약으로만): https://unsloth.ai/docs/models/qwen3.6
- Unsloth Windows 설치: https://docs.unsloth.ai/get-started/install-and-update/windows-installation
- vLLM LoRA 문서: https://docs.vllm.ai/en/latest/features/lora/
- vLLM 포럼(양자화 기반 + LoRA): https://discuss.vllm.ai/t/support-for-deploying-4-bit-fine-tuned-model-with-lora-on-vllm/1186
- vLLM 이슈 #26679(LoRA + Eagle3): https://github.com/vllm-project/vllm/issues/26679
- AutoRound W4A16 + MTP 로더 사례(모델 카드, 검색 요약으로만): https://huggingface.co/Lorbus/Qwen3.6-27B-int4-AutoRound
- llama.cpp 서버 README(LoRA 플래그·끝점): https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md
- llama.cpp 학습 예제 README: https://raw.githubusercontent.com/ggml-org/llama.cpp/master/examples/training/README.md
- GGUF LoRA 변환 소개(간접): https://huggingface.co/blog/ngxson/gguf-my-lora
- mlx-lm LORA.md: https://raw.githubusercontent.com/ml-explore/mlx-lm/main/mlx_lm/LORA.md
- GPU 대여 값(간접): 대여처 가격표 둘(대여처 이름 가림)
