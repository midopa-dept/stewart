# P-001 개정 4 초안(미등록) — 공개 벤치 + 표준 실행기 기준선 팔, 남은 §9 값 (연구 단계 11, 2026-11-10)

> 원래 기록: 연구 저장소에 남은 P-001 개정 4 초안. 등록된 본문은 [P-001.md](P-001.md) 의 "개정 4" 다. 이 초안과 등록 본문은 머리와, smoke 뒤 채울 두 줄(§N config_sha, §T smoke 뒤 칸 — 여기서는 비어 있음)만 다르다. 숫자·sha·커밋은 그대로다.
> 꼴: 초안(결과 전). 공개판에서 바꾼 것은 [P-001.md](P-001.md) 머리의 "공개판에서 바꾼 것" 과 같다.

**초안(DRAFT)**: 등록 전 초안이다. 파일 이름의 -DRAFT 도 원래 기록 그대로 둔다.

- 상태: 초안. **총괄 결정(빈 칸 13)**: 변환 sha 를 기다리고, X1-01 시작 전까지 안 오면 총괄이 "나눠 등록" 지시를 따로 보냄 — 그때까지 커밋(등록)하지 않는다. 값 도착 예정: 1–3 = 15:00–17:30 한 번에, 10·11 = 평가 담당 보정 뒤, 12 = v12.11·평가 보고 뒤. 변환 sha(quant-v11)·감사 값이 오면 채워 [registry/prereg/P-001.md](P-001.md) 에 붙여 커밋(= 개정 4). 이 팔의 데이터를 보기 전에 붙인다. X1-01(우리 과제)은 이 개정과 무관하게 예정대로.

- 까닭(총괄, 연구 단계 11, 사람 승인): 지금 비교는 우리 과제 × 우리 실행기 + Claude Code(모델·실행기 둘 다 다름)뿐이라 "같은 모델에서 표준 실행기로는 떨어지고 stewart 로 되찾는다" 를 받칠 외부 기준이 없다.

## A. 남은 §9 값(오후에 채움)
- [ ] 두 W4A16 sha(orig·abl, + abl-b) — quant-v11 manifest
- [ ] X0-01 감사 요약(export.json audit) — 받은 예비값(orig→abl): changed 70 · same 1129 텐서, 바뀐 층 17-51, mtp 0 · visual 0, audit sha 1e62a0d4266aa486(앞자리). **관찰**: abl 카드는 "layers 18 to 51" — 0부터 세는지 1부터 세는지로 갈리는지 확인 필요(17-51 은 35층, 18-51 은 34층)
- [ ] quant-v11 템플릿·커밋(main 합친 뒤)·PS — 받은 앞자리(도구 담당 12:20): 템플릿 b1be94bc…, 커밋 fcf05f5, PS 07d35e02(전체 값은 내보내기 뒤)
- [ ] 분석 스크립트 커밋, 차선 배정표

## B. 새 팔 X1-05 — 공개 벤치 × 표준 실행기

### B1. 벤치
| 벤치 | 판·과제 | 쓰는 까닭 | 걸림 |
|---|---|---|---|
| **SWE-bench Verified**(princeton-nlp/SWE-bench_Verified, test 500) — **주** | 고정 층화 부분집합 **sub200**(저장소 × 난이도 비례, 시드 20261110, 앞 100 = sub100 포함). id 목록 sha256: sub200 `6591c82c…050c`, sub100 `de6d8bc4…0e173`, 전체 `a6b0fd7c…9b9`(비공개 운영 기록) | 공개 순위표(bash-only = mini-SWE-agent)가 있어 비교 기준이 많고, 프런티어는 97% 근처(천장)지만 27B 급은 그 아래(같은 계열 최상위 모델 85.6% — 이차 출처)라 바닥·천장이 아님(어림) | x86 Docker 필요(과제마다 환경 이미지), 2024년부터 공개라 오염 위험 큼 |
| **Terminal-Bench 2.1**(Harbor) — **보조(측정 장치 점검)** | 2.1 전체(과제 수 미확인 — 2.0 은 89과제(Terminal-Bench 논문 2601.11868, 하위 조사 본문 일부), 실행 전 2.1 수 확인). 최신은 4.1 이지만 **원본 카드 공개 수치가 2.1** 이라 2.1 고정 | 원본 모델 카드: Terminal Bench 2.1 (Terminus) **73.0**(직접 확인, 설정 표기 없음) → 원본 × Terminus 로 우리 서빙·양자화 측정 장치를 점검할 수 있는 유일한 공개 수치 | Docker 필요(공식 CI 는 Modal), 일부 과제는 망이 필요할 수 있음(미확인), stewart 를 Harbor 에이전트로 붙이는 일이 큼 → 이 판에서는 표준 실행기만 |
- 뺀 것: SWE-bench Pro(원본 카드 61.7 은 Claude Code 하네스 — 모델·실행기 동시 차이라 점검으로 못 씀, 과제 크기 큼), 그 밖(Multi-SWE 등)은 공개 비교 기준이 적음.
- 오염: 공개 과제라 원본이 학습했을 수 있다. 그러나 이 팔의 주장은 **같은 사전학습을 공유하는 원본·abl·abl-b 사이의 대비**와 **실행기 대비**라, 학습 오염은 모델 사이에서 대체로 상쇄된다(같은 데이터). 남는 위험: 특정 실행기 궤적(예: Claude Code 하네스로 한 사후학습)에 맞춰진 모델이면 실행기 대비가 치우침 — 원본 카드가 Claude Code 하네스로 평가한 점을 결과에 적는다. 결과는 "절대 성능" 이 아니라 "대비" 로만 해석.

### B2. 표준 실행기
- **mini-SWE-agent v2**(MIT, bash 만 — swebench.com bash-only 순위표의 실행기) — SWE-bench 의 표준 실행기. 판: 실행 전 커밋 고정(PyPI 판 + git sha). 설정 = 공식 swebench.yaml 기본값(step_limit 250, cost_limit 3 → 로컬 모델은 비용 0 이라 사실상 step 한도, docker 환경 timeout 60s, cwd /testbed). litellm 로 우리 vLLM(OpenAI 호환) 엔드포인트.
- **Terminus**(Terminal-Bench 기준 에이전트, Harbor 안) — TB 2.1 점검용. 판 고정.
- OpenHands·Aider: **이번엔 넣지 않음**. OpenHands 는 런타임이 무겁고(컨테이너 안 컨테이너) 판 따라 결과가 크게 바뀌어 고정 비용이 큼, Aider 는 SWE-bench 표준 에이전트 실행기가 아님. mini 와 Terminus 둘로 "표준" 을 두 갈래 잡는다. 필요하면 다음 개정에서 OpenHands 를 견고성 칸으로.
- 모델 설정: 실행기마다 **공식 기본값 그대로**(out-of-box 비교가 주장에 맞음). 단 서빙은 세 모델·모든 실행기가 같은 vLLM 설정(P-001 §2), 생각 모드는 Qwen 기본(켬)으로 모든 칸 같게. 샘플링 인자는 실행기가 보내는 값을 회차 기록에 남긴다.

### B3. 칸
| 칸 | 모델 | 실행기 | 벤치 | 회차 | 구분 |
|---|---|---|---|---|---|
| S-o-m | orig(우리 W4A16) | mini-SWE-agent | SWE-V sub200 | 3 | 주 |
| S-a-m | abl(우리 W4A16) | mini | sub200 | 3 | 주 |
| S-d-m | abl-b(같은 원본의 다른 제작자 abliteration 판 — 저장소·리비전 가림, 우리 W4A16) | mini | sub200 | 3 | 주 |
| S-o-s | orig | stewart tag deploy-20261109-24 = 커밋 **80eb34ac276d71303de1b8491ac9e565b64ede47**, env **STEWART_BASELINE_DIFF=1**, 생각 켬, REVIEW·ASK·TODO 끔(총괄, 연구 단계 13) | sub200 | 3 | 주 |
| S-a-s | abl | stewart | sub200 | 3 | 주 |
| S-d-s | abl-b | stewart | sub200 | 3 | 주 |
| T-o-t | orig | Terminus | TB 2.1 | 3 | 점검 + 보조 |
| T-a-t | abl | Terminus | TB 2.1 | 3 | 보조 |
| T-d-t | abl-b | Terminus | TB 2.1 | 3 | 보조 |
- stewart 를 SWE-bench 에 쓰는 방법: 과제 컨테이너 안에서 명령이 돌고 파일 도구가 /testbed 를 고치게(셸 백엔드 = 컨테이너 exec, /testbed 를 호스트 폴더로 bind) — 도구 담당 작업(아래 D). 끝에 /testbed 의 git diff 를 예측으로 내고 swebench 하네스로 채점(두 실행기 같은 채점).

- **총괄 결정(연구 단계 12)**: sub200 × 3(≈ $130), abl-b 포함. 돌리는 순서 = **sub100 몫(앞 100)을 6칸 × 3회 먼저 채운 뒤 나머지 100** — 중간 결과가 sub100 × 3 으로 그 자체로 선다(중간 분석을 할 경우 그 분석도 등록된 꼴 그대로, 판정은 sub200 끝에서만).
- **stewart 판 고정의 전제**: 그림 계획(논문 원고 쪽 — 논문 원고는 공개하지 않음)의 "모자란 기록" 사건(component 꼴)이 판에 들어간 뒤 고정한다(그림 b·c 용, 모델 메시지는 안 바뀜).
- **등록 밖 서술 칸(그림 b 기여 폭포용, 총괄 결정 1(연구 단계 12) — H7·H8 판정에 쓰지 않는다)**
  - N1: v2(v2.0.2 32과제) × mini-SWE-agent(로컬 환경, 공식 기본값), 모델 A(abl-3p) — A 의 serve 인자: FAST_VARIANT=1(E13·E18 과 같음), 5회 — ≈ $2. 평가기 `--agent mini`(평가 담당).
  - N2: SWE-V **sub100** × 3, **abl 만** — stewart tag 24 **장치 끔 1칸**(고정 설정의 장치가 BASELINE 하나라 누적 중간 칸 없음, 총괄, 연구 단계 13) — ≈ $7.5. orig 폭포는 넣지 않는다(상호작용은 그림 d).
- **그림 c(실패 이동도)**: 실패 분류표 F0–F10 과 두 번 부호화(규칙 분류기 + 사람, 층화 25%·최소 80회차, Cohen κ ≥ 0.6) — 그림 계획 §c(논문 원고 쪽, 공개하지 않음). 분류기 코드는 데이터 보기 전에 커밋.
  - **부호화자 B = 사람(제품 주인) 본인(총괄 보충, 연구 단계 12, 확정).** B 화면 요건(확정 — 구현은 X1-05 회차가 나온 뒤 총괄이 맡길 곳을 정함):
    - 폰 브라우저에서 한 장씩 넘기는 꼴. 카드 = 과제 요약 · 마지막 완료 발언 · 고친 파일 대 정답 파일 · 시험별 결과(숨은 시험 통과/실패, 원래 되던 것 깨짐) · 종료 사유 · 진행 요약.
    - 모델·실행기 이름은 가린다(A 판정도 가림). 고를 것 = F1–F10 중 하나 + 메모(선택). 배타 순서(F10 → F6 → F7 → F8 → F5 → F2 → F4 → F3 → F1 → F9)를 화면에 띄운다. F0(인프라)은 A 가 먼저 걸러 카드로 내지 않는다.
    - 저장은 사람만 쓰는 곳에. 중간에 멈췄다 이어 하기. 결과 파일 = 회차 id·코드·메모·시각.
    - 분량 어림: 80장 이상 × 2–3분 ≈ 3–4시간, 나눠서 한다.

### B4. 가설·주 지표(이 팔 전용 묶음 — P-001 H1–H6 와 따로 Holm)
- 주 지표: % resolved(swebench 하네스, 과제별 3회 평균). TB 는 해결률.
- **H7**(표준 실행기에서 손상): mini 에서 orig − abl > 0, orig − abl-b > 0(두 대비).
- **H8**(실행기 회복의 손상 특이성): I_x = (x_stewart − x_mini) − (orig_stewart − orig_mini) > 0, x ∈ {abl, abl-b}(두 대비).
- **점검 C1**(가설 아님, 측정 장치): T-o-t 해결률이 카드 73.0 과 ±10%p 안. 벗어나면 X1-05 해석 전에 원인(양자화·문맥 150k vs 카드 미기재·서빙·생각 설정) 보고. 카드 조건(BF16, 설정 미기재)과 우리 조건(W4A16)이 달라 정확히 같을 수는 없다 — 점검은 "크게 어긋나지 않는가" 만.
- 보조(탐색): TB 2.1 에서 orig − abl·orig − abl-b(Terminus), SWE 에서 결함 지표(D1·D2·D4 — mini 는 툴콜 대신 bash 블록이라 D1 은 "형식 오류 횟수" 로 정의 바꿔 셈).
- 다중 비교: H7 2 + H8 2 = 4 대비에 Holm(이 팔 안에서).
- 우리 과제와의 관계: **주 결과는 여전히 우리 과제(P-001 H1–H3, 확증 = h1.0)**. 이 팔은 "외부 타당성" 보조 — 같은 방향이면 주장 강화, 반대 방향이면 그대로 보고.

### B5. 회차·검정력(어림 — v2 의 τ²≈0.070·σ²≈0.104 를 빌림, SWE 에서는 다를 것)
반폭(95%, 과제 군집) = 1.96·√(τ² + 2σ²/r)/√T (짝), 1.96·√(2τ² + 4σ²/r)/√T (상호작용)
| T × r | 짝 차이 | 상호작용 |
|---|---|---|
| 100 × 3 | ±7.3%p | ±10.4%p |
| 100 × 5 | ±6.6 | ±9.3 |
| 200 × 3 | ±5.2 | ±7.3 |
| 500 × 1 | ±5.4(회차 분산 큼) | ±7.6 |
→ **sub200 × 3회 권고**(과제 수가 회차보다 효율적). 시간이 모자라면 sub100 × 3 먼저(포함 관계라 나중에 sub200 으로 늘림).

## C. 시간·비용 어림(계산식)
- 회차 수 N = T × r × 칸. 벽시계 h = N × t_task ÷ (차선 × 차선당 동시) ÷ 60. 비용 = h × 시간당 값 + 준비.
- 가정(실측 없음, 파일럿 10과제로 바로잡음): mini·stewart t_task ≈ 12분(27B·3090·동시 4, 단계 상한 250), Terminus ≈ 15분. 차선 8(평가 장비 2호스트 × 4장), 동시 4 → 32. 값 = 평가 장비 1·2 합 약 $3.96/h(지금 docker 인스턴스 기준 — VM 형 offer 는 다를 수 있음, 미확인).
| 묶음 | N | 벽시계 | 비용 |
|---|---|---|---|
| SWE sub200 × 3 × 6칸 | 3,600 | 3,600×12/32/60 ≈ 22.5h | ≈ $89 |
| (대안) SWE sub100 × 3 × 6칸 | 1,800 | ≈ 11.3h | ≈ $45 |
| TB 2.1 × 3 × 3칸(과제 89 가정 — 2.0 값) | 801 | 801×15/32/60 ≈ 6.3h | ≈ $25 |
| 준비(이미지 받기·짓기, 파일럿 10과제 × 6칸) | — | ≈ 3–5h | ≈ $15–20 |
| **합(권고안)** | | ≈ 32–34h | **≈ $130**(대안 sub100 이면 ≈ $85) |
- 병목 후보: 과제 컨테이너의 CPU(동시 32개 시험 실행 — 지금 offer 는 24코어 급), 디스크(SWE 환경 이미지 sub200 분 — 크기 미확인, 수백 GB 급 어림), 이미지 받기 시간.

## D. 실행 환경(총괄 결정 4(연구 단계 12): VM·서빙·판 고정·이미지·채점 = 도구 담당, 셸 컨테이너 exec 백엔드 = 개발 담당 — 범용 기능으로)
1. Docker 가 되는 x86 GPU 기계: 빌린 GPU 의 **VM 형 offer**(일반 docker 인스턴스는 docker-in-docker 불가 — 대여처 문서) 고르기·띄우기(대여 도구에 VM 역할), SSH 키·사설망, 디스크 ≥ 1 TB(어림), CPU ≥ 32코어 권장.
2. VM 안에서 vLLM 서빙(지금 pod-start 와 같은 모델 표·템플릿·sha 대조).
3. stewart 셸 백엔드를 과제 컨테이너 exec 로(shell.py spawn() 한 곳 — 개발 담당 인계 기록), 파일 도구는 bind 한 /testbed 호스트 폴더에. 망 차단(과제 컨테이너는 망 없이).
4. mini-SWE-agent·swebench·Harbor·Terminus 판 고정(uv 잠금), litellm api_base = 로컬 vLLM.
5. 이미지 미리 받기·캐시(sub200 만), 파일럿 10과제로 t_task·디스크·CPU 실측.
6. 채점: swebench 하네스 로컬(sb-cli 는 예측을 외부로 보냄 → 노출 기록 대상이라 쓰지 않음), TB 는 Harbor 로컬.

## E. 장부
- [holdout-sets.tsv](../holdout-sets.tsv) 새 줄(노출 0): `swebench-verified-sub200`(sha 6591c82c…, sub100 포함), `terminal-bench@2.1`. 둘 다 공개 과제 — 은퇴·노출 규칙은 "공개 묶음" 으로(확증 결론은 모델 사이 대비만).
- [experiments.tsv](../experiments.tsv): X1-05(이 팔).

## F. 노벨티 — 판정 **중**(하위 조사, 대부분 초록 수준)
| 문헌 | 확인 | 한 것 | 우리와 차이 |
|---|---|---|---|
| Merrill 외, *Terminal-Bench*, arXiv 2601.11868 (2026-01) | 본문 일부(하위) | TB 2.0(89과제), 같은 모델에 여러 실행기(Terminus 2·Claude Code·Codex CLI·OpenHands·mini-SWE-agent), 구성당 5회+·95% CI. GPT-5 Codex CLI 49.6% vs mini 33.9% 등. 결론 "model selection is usually more important than agent scaffold" | 손상 변인 없음, 20–40B 밀집 모델 거의 없음 |
| Fan 외 2609.20804 | 초록(하위) + 연구 단계 2 본문 일부 | SWE-bench Verified·TB 2.1 에서 구성요소 × 모델 강도 176 짝, 약한 모델일수록 계획·미리 정한 도구 이득 | 모델 축 = 서로 다른 모델의 크기, 같은 기반의 가중치 손상 아님, 과제당 1회 |
| Hu 외 2609.32459 / Li 외 2610.00917 | 초록(하위) | 같은 가중치 × 하네스, 순위 뒤집힘 | 대형 모델 중심, 다른 벤치(Pro·TB4), 반복·CI 없음 |
| Jang 외 2607.27275 | 초록(직접)+본문(하위) | 손상(양자화) → 하네스 개입(오류 수리)으로 손상 자리만 회복 | 손상 = 양자화, 벤치 = τ²-bench |
| David & Gervais 2605.19722 | 초록(직접)+본문(하위) | 원본 vs abliteration 판 에이전트 | 실행기 고정, 공개 SWE/TB 아님 |
| Bjarnason 외, *On Randomness in Agentic Evals*, 2602.07150 | 초록(하위) | SWE-bench Verified 단일 실행 pass@1 이 2.2–6.0pp 흔들림 → 반복·검정력 권고 | 우리 3회 반복·과제 군집 CI 의 근거로 인용 |
- 판정 까닭: "같은 가중치에서 실행기 효과" 는 정립된 현상(없음). "같은 기반·같은 양자화 조리법의 abliteration 변종 2종 × 표준 실행기 대 우리 실행기 상호작용을 공개 SWE-V·TB 에서 반복·과제 군집 CI 로 검정" 은 못 찾음(강에 가까움). Jang(손상→개입 회복)·Fan(약한 모델일수록 하네스 이득)이 가설을 상당 부분 예고 → 중.
- 뒤집힐 조건: Fan 의 4모델에 20–40B 공개 모델이 있으면 약해짐(본문 확인 필요). abliteration 판 모델을 SWE-bench·TB 로 잰 연구는 검색 2회에 없음.
- 차이 문단(초안): 같은 가중치에서 실행기를 바꾸면 성능이 크게 달라지고(Terminal-Bench; Hu 외; Li 외), 약한 모델일수록 하네스 요소의 이득이 크다(Fan 외)는 점은 알려져 있다. 그러나 비교한 모델 축은 서로 다른 모델이었고, 같은 기반 모델에 가한 abliteration 손상을 독립 변수로 둔 연구는 찾지 못했다. 본 연구는 원본과 abliteration 변종 둘을 같은 W4A16 조리법으로 묶어, 표준 실행기(mini-SWE-agent 기본값)와 우리 실행기를 공개 SWE-bench Verified·Terminal-Bench 에서 반복 실행과 과제 군집 신뢰구간으로 비교해 손상 × 실행기 상호작용을 직접 검정한다.
- 바로잡음: 연구 단계 2 노트의 2602.16069 설명("Qwen 3 32B 를 mini·SWE-agent·OpenHands 로 비교")은 초록과 맞지 않는다 — 초록은 mini-SWE-agent 에이전트 방식 대 단일 호출 장문맥, 모델 GPT-5-nano·DeepSeek-R1·Qwen 3 Coder 30B-A3B.

## G. 확증 묶음 바뀜(예고 — h1.1 sha 가 나오면 이 개정에 넣음)
- P-001 개정 2 의 확증 묶음 h1.0 → **h1.1**(sha256 `d4e5ff8f01533c4dcd5f0e7cfa1c7b06dc0a0a8bcacd77e7cc0abb6ddb6113f6`, 고정 2026-11-10 10:53, L 범주 6과제를 새 6과제로 바꾸고 옛 6과제 은퇴, R·G·Q·V 26과제 같음 — **맞춤 통과 뒤 확정**; h1.0 은 맞춤 실행에서 −5.2pt 로 ±5pt 밖, holdout-criteria §3·개정 1). h1.1 sha·바꾼 과제 목록은 holdout-sets.tsv 에서 옮겨 적는다(과제 목록은 봉인 묶음이라 공개판에 적지 않음). 확증 실행 전에만 바꾼다.
- P-002 도 같은 바뀜을 P-002 개정 1 로(초안: 확증 = h1.1, 나머지 그대로).

## H. X1-01 유효 조건(총괄, 연구 단계 15 — X1-01 데이터를 보기 전에 등록해야 함)
- **울타리가 준비되면**(v12.11u + 평가기 v2.0.5·v1.2.3): 모든 회차에 `meta.agent_fence` 가 있어야 유효(holdout-criteria 개정 3). 평가기 판이 v1.2/v2.0.2 → v1.2.3/v2.0.5 로 바뀌는 것은 실행 판 변경이고, 과제 sha 가 같으면 같은 과제 묶음(개정 3)이다 — 과제 sha 가 같은지 실행 전에 확인해 여기 적는다.
- **울타리가 안 되면(지금 꼴로 돌림)**: 회차마다 ① 누설 감지(평가기 판의 누설 검사)와 ② 모델이 낸 명령·도구 인자·도구 출력에서 **평가기 폴더·실행 기록 폴더·연구 저장소·정답 저장소** 경로 접근 검사가 둘 다 0 이어야 유효.
- 걸린 회차는 **무효로 빼고**(실패로 세지 않음 — P-001 §8 의 "격리 위반 = 실패" 규칙은 이 두 검사에 한해 이 조건으로 바꾼다) 칸마다 무효 수를 보고한다. 분석은 과제별로 남은 회차의 평균을 쓴다(과제 짝은 유지 — 한 칸에서 그 과제의 유효 회차가 0 이면 그 과제를 모든 칸에서 함께 뺀다). **과제 빼기의 범위는 §Q G3 이 대신한다.**
- 중단: 한 칸이라도 무효가 5% 를 넘으면 묶음을 멈추고 총괄에 보고(원인 확인 전 판정 안 함).
- **등록 시점 주의**: 이 조건은 X1-01 이 시작되기 **전에** 커밋돼야 한다. 두 W4A16 sha 가 그때까지 안 오면 개정 4 를 "유효 조건·X1-05·h1.1 예고" 로 먼저 커밋하고 sha 는 개정 5 로 미루기를 권고.

## 빈 칸 10·11·12 채움(2026-11-10, 총괄 전달 값)
- 10: h1.1 맞춤 통과(holdout-criteria 개정 4 기준 — 전체 +7.8pt, 범주 0%·100% 없음). 옛 기준(±5pt) 판정은 밖.
- 11: 과제 sha — h1.1 aa97ed44450d7e5a1eb8950a91061d59333c382f97aab36bcc0d4f4db1301266, v2.0.x bf0d62eba48692af9a5acc1ed8fdd6f3ccd821fc900280b90bf0e08513b9aa4f, v1.2.x 64565974d32f68af65c1cbf3b344618e21f7a92b6e993ac952594823af967070.
- 12: 평가기 v2.0.5·v1.2.3 은 실행 판 변경이고 과제 sha 는 위와 같다(holdout-criteria 개정 3).
- 변환: 판 이름 quant-v11. 변환 sha 는 다음 개정으로 미룬다.

## I. X1-01 울타리 조건(사람 결정 11/10, 새 총괄 전달 — 사실만)
- 모든 칸을 울타리(stewart-agent.sb 66edcd33…) 안에서 재고, 칸끼리만 비교한다. 울타리는 회차 TMPDIR 만 열고 /tmp 쓰기는 막는다 — 그대로 둔다.
- 지난 값(울타리 없음)은 따로 표시하고 직접 견주지 않는다.
- 한계 문구에 넣을 사실 값: S2(mini 2.4.6 × v2.0.5 × 5, 160회차)에서 /tmp 쓰기 거부가 든 회차 59, 그 회차 기능 통과 21/59, 없는 회차 62/101. 출처: 평가 담당 S2 보고(비공개 운영 기록).

## J. 변환 결과(새 총괄 전달 11/10, 사실만 — 빈 칸 1·3 일부)
- 로컬 catalog 등록(11/10 11:46–11:56Z), 별칭·digest(등록 도구 값, 공개판은 별칭 앞머리의 모델 판 이름을 빼고 적음):
  - orig-bf16h 5b163c0a3c68aa1d4ec7335a17a436b4f713319a892d7f26a4750efb967644b7
  - orig-int8h b9362714a6f3974740e0dd0322de23e5bf9de14b050b73a0303eb5f5e7d9e2df
  - abl-bf16h fd4d77fc44d45bfb80c5c1aca95d958de8476c0fc1a051e98e9b2ee9e7a00a52
  - abl-int8h fa82a0326619ce76454661e9dba4672ee3340e70aae59052a24e9495bd03f792
  - 파일별 sha256·크기 전문: 비공개 운영 기록
- 변환 묶음: 변환 = quant-v11(변환 장비). 대조·내보내기 = quant-v13, 커밋 c5ec60edb4bf1cf0abd7cf0ea79205a018f233db, PS 94b93689acffcad72fe1dc54f8af2d70c9f728277774f009028552adf4d47193.
- abl-b: 변환 중. 0 채우기 바이트 증명 pad_check ok(to 17536, bad_count 0), BF16 KL mean 6.57e-4, top1 0.98975. 등록 여부는 사람 결정.

## K. 빈 칸 2·3 값과 평가 준비 결정(새 총괄 전달, 도구 담당 글자 그대로 — 해석 없음)
### 빈 칸 3 — quant-v11(래퍼 publish 기록 2026-11-10T08:18:11+09:00)
- commit fcf05f5010c8107d605b39f51751cebdd4d10f85
- PS 07d35e0226b2b2c6c4505a6e20f3f4b427731c4f9960fd2a5cf451a9e9baf600
- template_hash_id b1be94bcbdf3edfff5d0765febee82c7
- 전달 묶음 quant-v11(파일 이름 가림), 묶음 판 해시 23dd7e92051d7f0cf175f5dfd7fa223dc9e386fd
### 빈 칸 2 — X0-01 감사(orig → abl, audit_delta.py) 최종값(예비값과 같다)
- tensors: total 1199 · same 1129 · changed 70 · only_base 0 · only_edited 0 · shape_diff 0
- towers(텐서 수/바뀐 수): lm_head 1/0 · embed 1/0 · layers 848/70 · other 1/0 · visual 333/0 · mtp 15/0
- changed_layers: ranges "17-51", first 17, last 51, count 35
- by_kind: model.language_model.layers.N.linear_attn.out_proj.weight 26/48 · model.language_model.layers.N.mlp.down_proj.weight 35/64 · model.language_model.layers.N.self_attn.o_proj.weight 9/16
- method "무작위 저차 SVD(상위 32, 덧붙임 8, 거듭 2, 씨앗 0)", energy_k [1, 2, 4, 8, 16, 32], version 1. 행별 상대 크기·특잇값 에너지는 파일 안 rows(70행).
- 파일: audit-delta.json 31560 B sha256 1e62a0d4266aa48635897b48b40b18fac94996848c0a30ff996a7880509901e7 · audit-delta.tsv 11121 B sha256 a2b85cc0103f8488d45e990571795c61096a32b07434baa355bc506547e0e356 (변환 장비에서 11/10 02:12Z 에 만든 것).
- 자리: HF 비공개 전달 저장소(이름 가림)의 audit/audit-delta.json·audit/audit-delta.tsv(바이트 그대로). 로컬 catalog 의 export.json "audit" 칸에는 같은 내용이 다시 써진 꼴로 들어 있어 sha 가 다르다.
### 평가 준비 결정(총괄이 정한 것)
- 쓸 세트: 두 모델 모두 int8h — orig b9362714a6f3974740e0dd0322de23e5bf9de14b050b73a0303eb5f5e7d9e2df · abl fa82a0326619ce76454661e9dba4672ee3340e70aae59052a24e9495bd03f792. 모델 A(abl-3p)도 FAST_VARIANT=0 으로 띄워 serve 인자를 맞춘다.
- X1-01 은 abl-b 를 기다리지 않는다(abl-b 칸은 따로, 넣으려면 개정 5).
- 확증(S6)을 탐색(S4) 대여에 이어 붙인다 → §G 의 확증 묶음 h1.1 확정 문장이 S6 전에 등록돼 있어야 한다.
- 배정표 꼴(평가 담당 제안): 평가 대기열은 차선을 고정 배정하지 않고 별칭이 같은 빈 차선에 준다 → "시드 20261110 으로 섞은 차례표 + runner.log 의 실제 차선 기록, 두 파일의 sha 를 개정에 넣는다".
- 서빙 표 커밋(두 모델 세트 넷의 별칭·파일 sha): stewart 저장소 개발 가지 의 e7da34d9b58f6592ce0bed05cb1a12f930710e37(deploy/serving/models.local.toml, push 전).

## L. 배정표·분석 스크립트·회차당 분(새 총괄 전달, 사실만)
- runner.log 는 조각(차례) 단위로 시각·호스트·차선·별칭을 남긴다("<시각> step <이름> → <호스트> 차선 <k>(별칭 <별칭>)", "… start <호스트> 차선 <k> HEAD <커밋>", "… <판> end rc=…"). 회차 단위 줄은 없다. 회차 → 조각은 하나로 정해진다(결과 폴더 = 조각 tag). 이어 하기로 다시 돌린 조각은 줄이 두 번 남고 마지막 rc=0 줄이 그 결과 폴더다. 회차 시작 시각은 조각 start·end 와 회차 events 시각.
- 빈 칸 4(지금 있는 것 — 작업 폴더, git 밖, 파일 sha256): merge_shards.py 95a2e8edba7127ba992c62961f74e3bdddfc46f270973c44e2e199af8bb84699 · fence_stats.py d2f4211781db49ca52fe20ae56c471a99a8baf5f89e56ea6ee83ea5792f09ba6 · path_scan.py 8ca9d799f94b14a75e71f4d434405576e55e138c6632090c886e3b1b732505c8. H1–H6 대비 계산 스크립트는 아직 없음(평가 담당이 가짜 결과로만 시험해 작성 예정).
- 빈 칸 9(X1-01 회차당 분 실측, 평가 담당 X1-01 계획 보고 §2): v2 끔 2.37–2.57, 켬 5.44, v1 0.63–0.82, h1.1 4.02. D-5 파일럿 10과제는 X1-05 쪽.

## M. 빈 칸 4 — 분석 스크립트 커밋
- 연구 저장소 커밋 9e5cb01a29cddfa8994abb168e8a5eff6b3a9189(analysis/x1/: x1_analysis.py a10d19ad… · test_x1_analysis.py e6a2abb0… · merge_shards.py 95a2e8ed… · fence_stats.py d2f42117… · path_scan.py 8ca9d799… — 평가 담당 작성, 바이트 그대로, sha256 대조 같음). 등록 문장과 어긋나거나 모호한 곳 13개의 문장 후보: 비공개 운영 기록(판정 없음).

## N. 서빙·수락률 수집·차선 계획(새 총괄 전달, 사실만)
- G11(H5 수락률 수집, 도구 담당): 파드 감시기가 1분마다, 값이 바뀐 차선만 /workspace/stewart/spec-log.tsv 에 한 줄 — 시각(UTC)·차선·별칭·어댑터·drafts·draft_tokens·accepted_tokens·자리별 수락 비율. 출처 vLLM /metrics 의 vllm:spec_decode_num_drafts_total · vllm:spec_decode_num_draft_tokens_total · vllm:spec_decode_num_accepted_tokens_total · vllm:spec_decode_num_accepted_tokens_per_pos_total{position=i}. 차선(= vLLM 프로세스 하나)마다 따로, 누계는 서버가 뜬 뒤부터(줄어든 줄 = 재시작 경계). 조각 시작·끝 앞뒤 줄의 누계 차로 조각별 값(1분 해상도), 시작점 = "첫 조각 시작 직전" 줄(품질 점검 요청을 빼려고). 평가 뒤 파드에서 꺼내 운영 기록으로 옮기고 파일 sha 를 남긴다.
- 차선 계획: x1-01-eval · x1-01-eval2(stewart 개발 가지 커밋 087a1bc, deploy/serving/models.local.toml). 둘 다 4장, env CTX=long SPEC=mtp PREFIX_CACHE=1 FAST_VARIANT=0.
  - x1-01-eval: 차선 0 A(abl-3p) · 1 orig-int8h · 2 abl-int8h · 3 orig-int8h
  - x1-01-eval2: 0 orig-int8h · 1 abl-int8h · 2 abl-int8h · 3 orig-int8h
- config_sha: 차선마다 부팅 때 계산돼 /status 에 실림 → **자리(실행 직전 덧붙임)**: 차선별 첫 부팅 config_sha = ______
- 모델 A 는 **X1-04 칸(abl-3p × fix1)에서만** FAST_VARIANT=0 으로 띄운다(같은 대여 안 abl-int8h 와 serve 인자를 맞추려고 — §O). 지난 A 값 E5–E18 은 FAST_VARIANT=1.
- 파드 묶음 **v16**: 커밋 cbb9c03e317192cc6194bb7ead8d1e207cf039b8, PS 0a093e771b6004145b3a501c14e38b31aee4057b8003925ae7ca9908ebce6551, 템플릿 3ea3d638bbc9bb55125ee737df74990b (v15 + serve 대화 글 안 남기기, eval 동작은 같음) — 개정 3 의 "파드 v13 PS" 자리가 v16 으로 바뀐다(그 사이 v14 커밋 fee8baa679c75eb3e9b31aabaf8347110ed12603·PS 2978912e159b98d0804ab7789dc1bfe8bca49d9f73a9af8d2088b85d50756835, v15 커밋 1077fd851f6e0ec124bb0da0a77caad96d3cd499·PS 7a3de435e860f9cc6de2ff3f1d8fddb5ca96cef60fa50a6be46d6590564c25ad·템플릿 9f84cea468a646b520e862546690a263 는 쓰지 않음).

## O. 모델 A 의 FAST_VARIANT(총괄 결정 — 사람 확인 대기, 사실만)
- X1-01 의 X1-04 칸(abl-3p × fix1)만 모델 A 를 FAST_VARIANT=0 으로 띄운다(같은 대여 안의 abl-int8h 와 serve 인자를 맞추려고). **이 칸의 A 값은 지난 A 값(E5–E18, FAST_VARIANT=1)과 직접 견주지 않는다.**
- 지난 A 값과 한 그림·한 표에 잇는 새 칸(X1-05 서술의 N1 = mini × A)은 FAST_VARIANT=1 이 기본(위 N1 줄).

## P. 빈 칸 4 고침·빈 칸 5·H5 계산·smoke(새 총괄 전달, 사실만)
- 빈 칸 4(고침): 분석 스크립트 커밋 = 연구 저장소 **ffb14fd66ee48f5ac096e91171e4d9e994df0428** — analysis/x1/x1_analysis.py 08899eff…d726 · test_x1_analysis.py 6b973fb0…815f · make_x1_schedule.py c4019a72…b978(나머지 셋은 9e5cb01 그대로). §M 의 9e5cb01 은 이 커밋으로 바뀐다.
- 빈 칸 5(실행 전 커밋할 값): 섞은 차례표 queue/x1-s4-schedule-20261110.json sha256 0027afbf3fda3661521df8dc5e2590eb844bc63124d6cc7f6c7f71317dc3f868(0444, 연구 담당이 sha 대조 같음). 만드는 스크립트 make_x1_schedule.py c4019a7292d56319584baf9c21bd8be801c2215f77fcacae93c8570f331cb978(차례표 안에도 적힘, --check 로 바이트까지 같은 표).
  - 담은 것: 시드 20261110, 14차례(칸 7 × 평가기 2)의 섞은 순서, 조각 61개(차례·tag·칸·평가기·별칭·커밋·백엔드 파일·과제), (칸, 평가기, 과제, 회차) → 조각 tag 2,555줄, evalq 본체 sha(7116a9fa…), timetable sha 셋, 배정 규칙 글("14차례 순서 = random.Random(seed).shuffle, 조각 = evalq LPT, 빈 차선마다 파일 순서대로 별칭이 맞는 첫 차례 — 실제 호스트·차선은 runner.log").
  - 칸 → 실행: orig-base b480fe7 · orig-fix1 914bd71 · orig-think 914bd71(cloud-think) — 별칭 orig-int8h / abl-base·fix1·think 같은 꼴 — 별칭 abl-int8h / abl3p-fix1 914bd71 — 별칭 A(abl-3p). cloud-think = 클론 기본 cloud 블록에서 enable_thinking 만 true, max_tokens 8192 그대로.
  - runner.log 의 sha 는 실행 뒤 덧붙임: ______
- H5 계산: 조각마다 runner.log 의 start·end 시각, 그 호스트·차선의 "start 직전 줄부터 end 직후 줄까지" 누계 차(줄어든 줄 = 재시작 경계), 칸마다 합해 수락률 = accepted_tokens / draft_tokens. 검정 없이 값만(등록 "속도 지표로만"). spec-log 파일 sha 도 입력 sha 에 들어간다. spec-log 실물 열 꼴은 smoke 뒤 대조하고, 어긋나면 등록 전에 스크립트를 고쳐 sha 를 다시 낸다.
- **smoke 는 등록 칸의 자료가 아니다**: 모델마다 두 과제 × 1회, 서빙·울타리 확인용. smoke 회차는 분석에 넣지 않는다.
- 등록 차례(총괄이 따로 지시): 사람이 문장 후보 G1–G13 과 FAST_VARIANT 정리를 정함 → 초안에 문장 반영 → smoke 로 config_sha·spec-log 꼴 확인 → 개정 4 등록 커밋 → 본 차례 시작.

## Q. X1 분석 문구 13개(사람 결정 11-10 밤 "권장대로" — G1·G3 = 후보 나, 나머지 = 후보 가). 문장은 문장 후보 표(비공개 운영 기록)에서 그대로 옮김
- **G1**(후보 나) — 자리: 초안 §H "울타리가 준비되면 모든 회차에 meta.agent_fence 가 있어야 유효" — 그 갈래의 leak·access_suspicious·isolation_violation 처리는 적지 않음
  - 문장: "울타리 갈래에서도 이 셋이 있는 회차는 울타리 없는 갈래와 같이 무효로 빼고 수를 보고한다. 단 access_suspicious 는 경로 검사(path_scan) 가 평가기·기록·연구·정답 경로 접근을 확인한 경우만 센다."
- **G2**(후보 가) — 자리: §8 "그 (과제, 회차)를 짝이 되는 모든 칸에서 함께 뺀다"
  - 문장: "회차 번호는 칸 사이의 짝이 아니지만, 같은 과제의 같은 회차 번호를 모든 칸에서 뺀다."
- **G3**(후보 나) — 자리: 초안 §H "한 칸에서 그 과제의 유효 회차가 0 이면 그 과제를 모든 칸에서 함께 뺀다"
  - 문장: "과제 빼기는 같은 대비에 들어가는 칸 묶음 안에서만 한다: 주 6칸끼리, spec on/off 짝끼리, 제3자 양자화 짝끼리 따로."
- **G4**(후보 가) — 자리: §4 "Holm" — Holm 에 넣을 p 의 정의 없음
  - 문장: "Holm 에 넣는 p 는 과제 군집 부트스트랩(B=10,000)의 한쪽 p(가설 방향 반대쪽 비율), α = 0.05."
- **G5**(후보 가) — 자리: §3 H2 해석 "|I| < 5pt 동등성 → 일반 개선, I < 0 → 원본에 더 이로움" — 검정 없음
  - 문장: "동등성 = I 의 90% CI 가 ±5pt 안. '원본에 더 이로움' = I 의 95% CI 상한 < 0."
- **G6**(후보 가) — 자리: §3 H3 "결함률" — 회차별 있음/없음인지 횟수인지 없음
  - 문장: "결함률 = 회차마다 그 결함이 한 번이라도 있었는지(있음/없음)의 비율."
- **G7**(후보 가) — 자리: §5 D3 "completed ∧ ¬success(성급한 완료)" — 실패와 거의 같아 J_{k,D3} 가 H2 와 독립이 아님
  - 문장: 문장 그대로 둔다(코드도 그대로).
- **G8**(후보 가) — 자리: §5 D1 "content 에 샌 툴콜 표지" — 표지 글이 없음
  - 문장: "표지 = 응답 content 에 '<tool_call>' 또는 '<function=' 가 있고 tool_calls 가 비어 있음."
- **G9**(후보 가) — 자리: §2 "호스트를 블록으로 보고 분석에서 층으로 둔다" — 계산이 없음
  - 문장: "주 CI 는 호스트 층 없이 과제 군집 부트스트랩, 호스트별 값은 보조로 보고한다."
- **G10**(후보 가) — 자리: §4 "v2 와 v1.2 를 따로도 보고" — 부트스트랩을 묶음별로 층화할지 없음
  - 문장: "73과제 주 분석의 부트스트랩은 묶음(v1·v2)별 층화로 다시 뽑는다."
- **G11**(후보 가) — 자리: §3 H5 "MTP draft 수락률" — 자료 출처 없음
  - 문장: "수락률은 파드 감시기가 1분마다(값이 바뀐 차선만) /workspace/stewart/spec-log.tsv 에 남긴 vLLM /metrics 누계(vllm:spec_decode_num_drafts_total · num_draft_tokens_total · num_accepted_tokens_total · num_accepted_tokens_per_pos_total{position=i})로 계산한다. 차선(vLLM 프로세스)마다 따로 세고, 줄어든 줄을 재시작 경계로 본다. 조각별 값 = 조각 시작·끝 앞뒤 줄의 누계 차(1분 해상도), 시작점 = 첫 조각 시작 직전 줄. 파일은 평가 뒤 운영 기록으로 옮겨 sha 를 남긴다. 한계: 1분 해상도라 같은 차선에서 잇달아 돈 조각 경계의 1분 안팎이 두 조각에 겹쳐 셀 수 있다."
- **G12**(후보 가) — 자리: §4 "결함 매개(탐색으로만): 회차 단위 로지스틱" — 모형식 없음
  - 문장: "결함 매개는 이번 분석에 넣지 않는다(탐색, 모형식은 결과를 본 뒤 정하지 않음)."
- **G13**(후보 가) — 자리: §4 "보조: 과제 고정 추론(과제 층별 회차 순열)" — 상호작용용 순열 방법이 없음
  - 문장: "상호작용의 과제 고정 추론은 정규 근사(과제별 회차 분산으로 낸 SE)로 대신한다."

## R. 회차·과제 빼기 차례(총괄 지시)
1. 먼저 §8 오류 회차를 §Q G2 대로 뺀다(같은 과제·같은 회차 번호를 모든 칸에서).
2. 남은 회차에 울타리 무효(§Q G1 후보 나 — 그 칸에서만)를 적용한다.
3. 그 뒤 유효 회차가 0 인 과제를 §Q G3 대로 뺀다.
- 한 회차에 둘 다 걸리면 G2 로 센다(넓은 쪽). 보고 표에는 두 사유의 수를 따로 적고 겹친 수도 적는다.

## S. 빈 칸 4 최종(G1·G3 고친 분석 스크립트) — 총괄 전달
- 연구 저장소 커밋 **c0abab59ceaf70a59bafcb76cef106859b5d8264**(평가 담당 작성). analysis/x1/x1_analysis.py sha256 1d077ecfcfe3d2bcc618d921e2e51353c7dd6469af425e8661c813b4c34b0342 · test_x1_analysis.py 5a7d7966cc8a54f2f31cc4c24703b3bb37354631cb8edb87ed919418e6364a7d · path_scan.py 8ca9d799f94b14a75e71f4d434405576e55e138c6632090c886e3b1b732505c8(그대로) — 연구 담당이 sha 대조 같음. §M·§P 의 앞 커밋(9e5cb01·ffb14fd)은 이 커밋으로 바뀐다.

## T. 마무리 — 문장 고침 ①–④ 와 빈 칸 4 최종(총괄 지시, 평가 담당 값)
1. **확인 경로 목록(G1·§H 를 대신함)**: access_suspicious 의 "확인" 과 §H 의 경로 접근 검사는 평가기·실행 기록(runs)·대기열(queue)·운영 기록·평가 클론·평가 cache·연구 경로(연구 저장소, 연구 작업 폴더)를 인자에서 OPEN·LIST 한 경우로 본다. "정답 저장소" 는 평가기 폴더로 갈음한다(X1 의 정답 파일은 평가기 폴더 안).
2. **다시 돌림 대상(§8·§R 을 대신함)**: stop_reason backend_error, 실행 rc timeout·spool-timeout·refused. 뜻(평가 담당): refused = evalq 의 실행 대행(울타리 밖 본체, evalq.py agent-req 검사)이 평가기의 에이전트 띄우기 요청을 검사에서 거절해 에이전트를 띄우지 않음(agentspawn.py 가 .done 의 rc "refused"·refused_why 를 결과 줄 rc 로 남김) · timeout = 대행이 시간 상한에 에이전트를 죽임 · spool-timeout = 평가기가 .done 을 timeout+120초 안에 못 받음.
3. **확증 판 이름(§G 에 덧붙임)**: 평가기 판 이름 "h1.1f" = 과제 sha aa97ed44450d7e5a1eb8950a91061d59333c382f97aab36bcc0d4f4db1301266(32과제, h1.1 과 같음)인 h1.1 의 울타리 실행 판. 실행 판 sha(run_eval --hash) = 598e8cea24b3d4a430f26303a68ffff1b4108b56f97d5bc7496ef1cd284781b7. 과제 sha 계산식 analysis/x1/task_sha.py(sha256 535af3bde7c6c351f6cd7f361d7c85400b78628bbbc06e3aa259bc8b16a6a628). holdout-criteria 개정 3 에 따라 같은 묶음.
4. **abl-fix1 과 G3 묶음**: abl-fix1 은 세 대비 묶음(주 6칸 · X1-04 짝 · X1-02 짝)에 다 들고, 대비마다 자기 묶음의 과제 집합을 쓴다. 묶음마다 과제 수가 다를 수 있으며 보고 표에 묶음별 과제 수를 적는다.
- **빈 칸 4 최종**: 분석 스크립트 = 연구 저장소 커밋 **2973470d6d7ba06d7437203259b2109f2e3bce89**(주석만 고침) — x1_analysis.py a9eb107629c26d4dcb3b4c20b659d853d22cb4a305d9be7847d34e387822a385 · test_x1_analysis.py 5a7d7966cc8a54f2f31cc4c24703b3bb37354631cb8edb87ed919418e6364a7d · path_scan.py 8ca9d799f94b14a75e71f4d434405576e55e138c6632090c886e3b1b732505c8 · task_sha.py 535af3bd…(연구 담당이 sha 대조 같음). §M·§P·§S 의 앞 커밋은 이 커밋으로 바뀐다.
- **smoke 뒤 채울 칸**(총괄 전달 대기, smoke 시작 23:01, 평가 장비 1): 차선별 첫 부팅 config_sha(§N) = ______ · spec-log 실물 열 꼴 대조 결과(§P) = ______

## U. 실행 장비 조건(총괄 전달, 사람 결정 11-11)
- 평가 장비 조건을 datacenter 에서 **verified · 신뢰도 ≥ 0.98 이면 개인 호스트도 허용** 으로 바꾼다. 본 실험 장비: RTX 3090 × 4, 개인 호스트(11-11 07:46 올림).
- 회차 기록에 호스트(장비 id·machine id·위치·verified·신뢰도)를 남긴다 — 분석의 호스트 층(G9)과 같은 칸.
