# 공개 벤치 — 계획, 벤치 조사, 장부, BrowseComp-Plus 확증 A

> 원래 기록: 연구 담당의 공개 벤치 총괄 계획(2026-11-12 밤 판 0–0.5, 판 1), 벤치 조사 둘(11-12), 공개 벤치 장부(11-12 밤), 확증 A 판정 보고(11-13 14:49)와 덧붙임. 숫자는 다시 계산하지 않고 옮겼다.
> 꼴: 계획·조사·장부는 결과 전 기록이다. 확증 A 는 **확증**(봉인 묶음, 결과 전 규칙, 한 번)이고, 그 덧붙임의 보조 집계는 서술이다.
> 실행 조건: 실행 모델 = Qwen 계열 27B 4비트. 장비 = 빌린 RTX 3090 4장 기계. 엔진 = stewart 판 121.1 · 122(확증 A). 각 문서 머리에 따로 적었다.

**맨 위 요약**
- **무엇을 왜**: 우리 과제(개발 실측)만으로는 짧은 코딩 과제 밖을 못 본다. 그래서 개발 결정(기본값) 하나마다 공개 벤치 하나를 맞대어 "바깥에서도 같은가"를 보려 했다. 웹 조사(BrowseComp-Plus·OverSearchQA·When2Call), 규칙 준수(τ²-bench), 기능 수준(Terminal-Bench 2.1·SWE-rebench)이 후보였다. 이것은 개발 결정의 안전 확인이고, 연구 확증이 아니라고 계획에 적었다([plan-v1.md](plan-v1.md)).
- **어디까지 했나**
  - 1단계 BrowseComp-Plus 밤 실행(세 칸)은 답 없이 멈춘 회차가 많아 칸 비교를 못 했다(돈은 [../money/waste.md](../money/waste.md)). 그 실패 기록을 읽고 판 122 를 만들었으므로, 그 밤 실행은 개발 묶음이다([../protocol.md](../protocol.md)).
  - 그 뒤 BrowseComp-Plus 69–200 을 확증 A 66 · 예비 B 66 으로 나누고, 확증 A 를 결과 전 규칙으로 한 번 쟀다([bcp-confirm-a.md](bcp-confirm-a.md)).
  - OverSearchQA·When2Call·BrowseComp-Plus 개발 68 은 창 3 에 쓰였다([../w3-moderato-effort/](../w3-moderato-effort/README.md)). TB 2.1·SWE-rebench 는 봉인 나누기와 출처 판단까지 [../protocol.md](../protocol.md) 덧붙임에 있다.
- **확증 A 결과**: 판정 1(판 122 − 판 121.1) **효과 없음** — 17 대 17, 차이 0.0, 95% CI [−9.1, +9.1]pt. 판정 2(LS3 − 판 122) **효과 없음** — 22 대 17, 차이 +7.6pt, 95% CI [−1.5, +16.7]pt, p 0.18. 확증 전에 검정력을 적지 않았다(66문항 짝 비교의 CI 반폭 약 ±9pt).
- 이 폴더는 공개 벤치의 문항·정답·문서 원문을 싣지 않는다. 문항 수·점수·통계·설계만 싣는다.

## 파일
| 파일 | 원래 기록 | 꼴 | 무엇 |
|---|---|---|---|
| [plan-master.md](plan-master.md) | 총괄 계획 뼈대 판 0 → 조사 반영 판 0.5 → 11-13 새벽 덧붙임 | 계획(결과 전) | 물음 → 벤치 대응표, 병목(평가 대기열·과제 실행 쪽), 예산 셈 꼴, BrowseComp-Plus 를 stewart 를 고치지 않고 붙이는 길, 두 실행 길이 섞일 때의 셈 규칙, 되풀이 멈춤 = 오답 판정 |
| [plan-v1.md](plan-v1.md) | 총괄 계획 판 1(11-12 밤) | 계획(결과 전) | 사람 충전 판단용 한 장: 단계표와 돈(깨끗한·현실 ×1.37·상한), 벽시계 일정, 사람 결정·손, 첫 48시간 |
| [survey-coding-terminal.md](survey-coding-terminal.md) | 벤치 조사 1(11-12) | 조사 | 코딩·터미널·도구 호출 벤치: Harbor, Terminal-Bench 2.1, SWE 계열, τ²-bench, 같은 모델·다른 하니스 선례 |
| [survey-web-research.md](survey-web-research.md) | 벤치 조사 2(11-12) | 조사 | 웹 검색·조사 벤치: BrowseComp-Plus, OverSearchQA, When2Call, 실시간 웹, 난도와 채점·오염·약관 |
| [ledger-stage1.md](ledger-stage1.md) | 공개 벤치 장부 판 1(11-12 밤) | 돈 장부 | 1·2단계 벤치별 상한·현실 어림과 장부 규칙(실제 지출은 [../money/](../money/README.md)) |
| [bcp-confirm-a.md](bcp-confirm-a.md) | 확증 A 판정 보고(11-13 14:49)와 덧붙임 셋 | 확증 + 서술 | 판정 둘(효과 없음), 판정 전 검사, 장비별 보조, 칸별 끝 이유, 판 122 의 마지막 답이 안 붙는 까닭, timeout 이 쓴 시간 |

## 함께 볼 것
- [../protocol.md](../protocol.md) — 개발·봉인 규약. BrowseComp-Plus·TB 2.1·SWE-rebench 의 나누기와 확증 A 의 결과 전 규칙(§6)·셈 스크립트·결과가 여기 있다.
- [../trust-frame.md](../trust-frame.md) — 공개 벤치 결과를 "개발 과제 + 바깥 대조" 등급으로 읽는 까닭.
- [../registry/prereg/P-003-DRAFT.md](../registry/prereg/P-003-DRAFT.md) — 같은 27B 에서 동료 실행기 기준선 + Terminal-Bench 2.1 파일럿(초안).
- [../money/](../money/README.md) — 공개 벤치 몫 지출과 낭비 표.
