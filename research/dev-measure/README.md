# 개발 실측 — 창 1·창 2·등급 다시 재기

> 원래 기록: 연구 담당·평가 담당이 2026-11-12–11-13 에 총괄에게 낸 개발 실측 계획·결과·메모(비공개 운영 기록). 이 폴더의 파일마다 머리에 원래 기록과 꼴을 따로 적었다.
> 꼴: **개발 점검 · 서술(판정 없음)**. 이 폴더에는 확증이 없다.
> 실행 조건: 실행 모델 = abl-3p(Qwen 계열 27B 의 abliteration 판, 제3자 4비트 양자화 — 모델 판 이름 가림), 9B 칸은 9B 급 모델 4비트(로컬 맥 llama.cpp — 모델 판 이름 가림). 장비 = 빌린 RTX 3090. 엔진·평가기 판은 파일마다 머리에 적었다.

**맨 위 요약**
- **개발 실측**은 stewart 의 기본 기능·등급 설정을 정하려고 **개발 묶음**(우리 과제 v1 41 + v2 32 = 73과제)에서 잰 것이다. 개발 묶음은 문항을 읽고 고쳐도 되는 묶음이라, 여기 숫자는 **탐색·서술이고 판정이 없다**. 봉인 과제(h1.x)는 쓰지 않았다. 규약은 [../protocol.md](../protocol.md)다.
  - 원래 기록에 "나빠진다·낫다" 같은 말이 있어도 문장은 그대로 두었다. 판정으로 읽지 않는다. 각 파일 머리에 "개발 점검 · 서술(판정 없음)"을 붙였다.
  - 고르기 규칙("B 보다 3pt 안인 가장 가벼운 칸")은 실행 전에 적었지만, **권고를 내는 규칙**이다. 73과제 × 1회의 두 칸 차이 반폭이 약 ±6–8pt 라 3pt 를 가르지 못한다는 것도 실행 전에 적혀 있다([timing-and-think-axis.md](timing-and-think-axis.md) ②).
- **흐름**
  1. 사람 결정(예산은 연구보다 개발에 먼저 쓴다)에 따라 개발 결정 D1–D5 를 재는 계획을 세웠다([plan.md](plan.md), 판 12). 창 1 = run 길(기능 사다리 L0–L5·압축 창), 창 2 = chat·허브 길(대화 전용 기능·허브 등급), 그리고 맥 로컬 9B 칸.
  2. 사람의 두 물음("빌린 3090 이 느리다", "생각 양도 등급의 내용 아니냐")으로 시간 나누기 셈과 생각 축을 창 2 에 더했다([timing-and-think-axis.md](timing-and-think-axis.md)).
  3. 창 1 결과: stewart 모든 단이 mini-SWE-agent 보다 높았고, 기능을 더 쌓아도 얻은 것이 보이지 않았으며 시간은 늘었다. 규칙대로 고른 칸은 L1(뼈대)·W32 다([w1-result.md](w1-result.md)).
  4. 창 2 를 빌리기 전, 회차 시간이 어디서 길어지는지(모델 쪽 83–91%, 병목은 차선마다 동시 회차가 적은 것)와 칸 순서(Q1·Q2)를 정했다([w2-bottleneck-and-order.md](w2-bottleneck-and-order.md)). 창 2 결과를 볼 때 따로 셀 항목은 [w2-result-checks.md](w2-result-checks.md)다.
  5. 창 2 의 결과 보고는 이 묶음에 없다. 창 2 로 등급별 생각 설정을 고르지 **못한** 사정(같은 칸 두 번의 흔들림 4pt 가 규칙의 3pt 보다 큼)과 그 돈은 [../money/waste.md](../money/waste.md)에, 창 2 가 개발 자료로만 쓰였다는 것은 [../protocol.md](../protocol.md) §5 에 있다.
  6. 그래서 등급 설정을 공개 벤치로 다시 재는 설계를 했다([tier-remeasure.md](tier-remeasure.md), 11-13 21:16). 이 설계는 같은 날 창 3 설계로 합쳐졌다.
- **창 3·창 4**: 창 3(moderato 생각 깊이)은 [../w3-moderato-effort/](../w3-moderato-effort/README.md), 창 4(presto 생각 깊이 — 확증 못 함)는 [../w4-presto-effort/](../w4-presto-effort/README.md)다.
- 돈: 계획 때 개발 실측 상한은 $15 였다([plan.md](plan.md) §4). 그 뒤 상한 $18 을 넘었다(창 1 $6.67 + 창 2 약 $13.2 = 약 $19.9, [../money/waste.md](../money/waste.md)).

## 파일
| 파일 | 원래 기록(비공개 운영 기록) | 무엇 |
|---|---|---|
| [plan.md](plan.md) | 연구 담당 → 총괄, 개발 실측 계획 판 12(11-12) | 개발 결정 D1–D5, 등급 이름 표, 칸·회차·돈, 실행 전에 적은 고르기 규칙, 맥 smoke 에서 찾은 결함 둘(비스트리밍 idle 잘림, 평가기 v2.0.8) |
| [timing-and-think-axis.md](timing-and-think-axis.md) | 연구 담당 → 총괄(11-12 14:2x) | ① 회차 시간을 여덟 몫으로 나누는 셈(dev_timing.py) ② 생각 양을 창 2 의 등급 축으로 넣는 안과 "3pt 를 가를 수 없다"는 셈 |
| [w1-result.md](w1-result.md) | 연구 담당 → 총괄, 창 1 결과(11-12 20:2x) | D1 기능 사다리 L0–L5, D5 압축 창 W24·W32·W150. 73과제 × 1회 |
| [w2-bottleneck-and-order.md](w2-bottleneck-and-order.md) | 연구 담당 → 총괄(11-12 17:3x), 평가 담당 실측 덧붙임 | 창 1 회차 시간 내역, 병목 확인(GPU·맥 아님), 창 2 대기열 Q1·Q2 와 돈 |
| [w2-result-checks.md](w2-result-checks.md) | 평가 담당 작업 계획(11-12) | 창 2 결과에서 칸마다 따로 셀 것: 고정 앞부분, 웹 도구 실림, 판 117 의 앱 여는 셸 명령 거절 |
| [tier-remeasure.md](tier-remeasure.md) | 연구 담당 → 총괄·평가 담당(11-13 21:16, 21:2x 덧붙임) | moderato xhigh 대 medium 을 공개 벤치(BrowseComp-Plus·OverSearchQA·When2Call)로 다시 재는 결과 전 설계. 1단 개발·2단 봉인 확증 규칙, 벤치별 검정력 셈. 창 3 설계로 합쳐짐 |
