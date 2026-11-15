# 보안 벤치 — T4 자체 보안 과제 벤치(SB-0) 명세, CyberGym 실행 장소, 상용 제품 조사

> 원래 기록: 연구 담당의 보안 벤치 폴더(비공개 운영 기록, 2026-11-09–10, 연구 단계 2·3·4·6). 원래 폴더 머리글은 한 줄 — "T4 자체 보안 벤치 명세(정답·PoC 는 여기 넣지 않는다)" — 이었고, 이 README 는 공개판에서 새로 썼다. 규칙·숫자는 다시 계산하지 않고 옮겼다.
> 꼴: 설계·명세·조사(모두 결과 전). 파일럿을 아직 돌리지 않았으므로 결과는 없다.
> 실행 조건: 측정 없음. 잴 대상 = Qwen 계열 27B 4비트 원본 판(orig)·abliteration 판(abl)을 stewart 로 · Claude Code · OSS 보안 에이전트 · 상용 도구.
> 공개판에서 바꾼 것: 과제 표(sb0-tasks.tsv)는 싣지 않는다(봉인 장부에 올라 있는 묶음의 과제 목록) — sha·개수·계열 구성만 아래에 적는다. 과제별 id·upstream 이름·과제별 취약점 내용은 명세에서도 빼거나 계열 수준으로 일반화했다. 공급사 문의 초안은 싣지 않는다(비공개 운영 기록). 로컬 장비의 사양·망 구성은 뺐다. 대여처 이름 → "빌린 GPU". 범위 표시의 물결표 → en dash.

**맨 위 요약**
- **무엇을 왜**: T4([../related-work/T4-security.md](../related-work/T4-security.md))의 남은 기여는 "찾기 → 분류 → 재현 → 고치기" 단계별 점수 + 오탐률 + 거부율 + 비용을 한 벤치에서, 우리 27B(orig·abl)·OSS 에이전트·상용 도구에 같이 재는 것이다. 공통 공개 벤치 점수가 거의 없는 상용 제품을 직접 재는 것이 핵심이다.
- **SB-0 원칙**: 대상은 우리가 만든 로컬 사본뿐, 외부 접속 없음. PoC 는 무해 표지(`SB0-VULNERABLE` 출력)로만 증명한다. 정답·숨긴 시험·기준 PoC 는 비공개 정답 저장소에만 두고, 공개 쪽에는 과제 묶음 sha256 만 둔다(해시 약속).
- **파일럿 20 구성(개정 v0.2)**: R 9(공개 CVE 수정 커밋 되돌림, Python 6 · JS 3) + W 7(자체 작성 웹앱, Python 5 · Node 2) + N 4(음성 대조 — R·W 의 고친 판 그대로, Python 2 · JS 2). 원래 v0 는 R 12 · W 4 · N 4 였고, 총괄 결정으로 서비스 거부·명령 주입·C 메모리 크래시 유형을 빼서 바뀌었다.
  - 파일럿 1단계 = 11(R 7 · N 4), 파일럿 2단계 = 9(R 2 · W 7).
- **과제 표 고정값**: `sb0-tasks.tsv`(머리 1줄 + 과제 20줄) sha256 `084705d708bb84bef930b64114cd7689a5771ea7ba2aa01dd8b55ba291391d6b`. 칸은 id·group·lang·source·license·size_loc·stages·prompt_variants·pilot·bundle_sha256 이고, CWE·CVE 칸은 v0.1 에서 뺐다(분류 정답이 드러나므로). 과제별 묶음 sha 는 싣지 않는다.
- **봉인 장부**: SB-0(pilot 20)은 [../registry/holdout-sets.tsv](../registry/holdout-sets.tsv) 에 올라 있다(구성 R9·W7·N4, 사용 0, 상태 pilot). 외부 API 에이전트로 돌리면 그 줄의 external_exposure 에 적는다([sb0-run-spec.md](sb0-run-spec.md) §7-6).
- **CyberGym**: 채점 실행 장소를 비교했고, 로컬 장비의 격리 VM 을 조건부 권고로 두었으나 **보류**(X1-01 뒤로).
- **상용 제품**: 약관 기준 공표 판단 — 실명 공표(Claude Code security-review·CodeQL·CodeMender 조건부·OSS), 애매(Codex Security·Semgrep 익명 권고·Xint 서면 확인 전 측정 안 함), 제외(Snyk·XBOW·HackerOne·Big Sleep·Socket).

## 파일
| 파일 | 원래 기록 | 꼴 | 무엇 |
|---|---|---|---|
| [sb0-spec.md](sb0-spec.md) | SB-0 명세 v0(11-09, 연구 단계 3) + 개정 v0.1(연구 단계 4)·v0.2(11-10, 연구 단계 6) | 설계(결과 전) | 과제 묶음 꼴(R·W·N), 정답 분리 방식, 채점기 입출력(find·classify·reproduce·fix·fp_count·refusal·cost·valid), 음성 대조, 거부 판정 규칙, 파일럿 게이트, 개정 이력 |
| [sb0-run-spec.md](sb0-run-spec.md) | SB-0 실행 명세 v1(11-10, 연구 단계 6) | 실행 명세(결과 전) | 평가 담당이 받을 꼴: 입력, 작업 폴더 만들기, audit·hinted 지시문, 실행 환경, 회차·상한(54 회차/에이전트), 정답을 못 보게 하는 경로 조건과 외부 노출 기록, 파일럿 2단계 |
| [cybergym-placement.md](cybergym-placement.md) | CyberGym 채점 실행 장소(11-09, 연구 단계 3) | 설계 비교(보류) | CyberGym 구성·크기·망 격리, 실행 장소 후보 넷 비교, 부분집합 크기와 돈 어림, 사람이 확인할 것 |
| [commercial-products.md](commercial-products.md) | 상용·OSS 보안 도구 조사(11-09, 연구 단계 2) + 공표 판단(연구 단계 3) | 조사 | 제품별 가용·대상 지정·닫힌 망·가격·벤치/공표 조항, 측정 후보 권고, 공급사에 서면으로 물을 것, 약관 기준 공표 판단 |

싣지 않는 것: 과제 표 `sb0-tasks.tsv`(위 sha·구성만), 공급사 문의 초안(비공개 운영 기록), 정답 저장소 내용 일체.

## 함께 볼 것
- [../related-work/T4-security.md](../related-work/T4-security.md) — T4 노벨티 판정(약–중)과 선점 문헌.
- [../registry/holdout-criteria.md](../registry/holdout-criteria.md) — 해시 약속(기준 6)과 외부 노출을 세는 개정 2.
- [../registry/experiments.tsv](../registry/experiments.tsv) — X5-03(Claude Code 기준선, SB-0 실행 명세의 Claude Code 모델 ID 와 같음).
