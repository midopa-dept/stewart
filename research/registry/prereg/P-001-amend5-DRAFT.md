# P-001 개정 5 초안(미등록) — 셋째 모델 abl-b (2026-11-10, 새 총괄 전달 사실만)

> 원래 기록: 연구 저장소에 남은 P-001 개정 5 초안. 숫자·sha·커밋은 그대로다.
> 꼴: 초안(결과 전). 이 초안은 등록되지 않았고, 등록본 [P-001.md](P-001.md) 에는 개정 5 가 없다.
> 실행 조건: abl-b = 같은 원본 모델(Qwen 계열 27B)의 다른 제작자 abliteration 판(저장소·리비전 가림)을 우리 조리법으로 W4A16(4비트)에 맞춘 것. 별칭은 모델 판 이름을 빼고 abl-b-bf16h·abl-b-int8h 로 적었다.

**초안(DRAFT)**: 등록 전 초안이다. 파일 이름의 -DRAFT 도 원래 기록 그대로 둔다.

- 상태: 초안. X1-01 에는 들어가지 않는다(개정 5 몫). 칸·회차·가설은 총괄 지시 뒤 덧붙인다.

## abl-b 변환 결과
- digest: abl-b-bf16h 36e2a93c7126aca5631d1acbf777e82b3017642bcd81f32b2b4488818364e334(18파일) · abl-b-int8h 39a407aaa47e305759fe984fbd39c3f6eaa835cbbed5742683032270d51d8abc(19파일). 파일별 sha·크기 전문: 비공개 운영 기록.
- 변환 숫자(도구 담당 보고 그대로): pad_check ok(to 17536, tensors 1199, mlp_checked 195, other_tensors_same 1004, files_same 11, config_changed ["text_config.intermediate_size"], bad_count 0) · kl_pad mean 6.57e-4, top1 0.98975 · kl_quant mean 0.0368, top1 0.9155, PPL 9.935 → 10.142 · verify OK · 품질 점검의 추측 디코딩 acceptance 0.8629, 평균 수락 길이 3.589.
- 변환 묶음: quant-v13(커밋 c5ec60edb4bf1cf0abd7cf0ea79205a018f233db, PS 94b93689acffcad72fe1dc54f8af2d70c9f728277774f009028552adf4d47193), 변환 장비(대여 번호 가림). HF 사본 저장소(이름 가림) 커밋 3ad1d25391733d0b9ba7d0d19db6cbdbe0758c7e.

## 등록 문턱 규칙 변경(사람 결정 11/10)
- 로컬 등록 도구의 문턱 "0 채우기 전후 KL 평균 ≤ 1e-4 · top-1 ≥ 0.999" 에 abl-b 가 걸린다(6.57e-4 · 0.98975).
- 바뀐 규칙: 바이트 증명(pad_check)이 통과한 보고는 이 문턱을 보지 않는다. 증명이 없거나 실패하면 문턱 그대로. 등록 도구를 새 판으로 바꾼 뒤 abl-b 를 등록한다.
- 까닭(전달 그대로): 0 채운 자리는 계산에 0 을 더하므로 함수가 같고, 차이는 BF16 덧셈 차례의 반올림 — 양자화 자체의 KL 0.0368 의 약 56분의 1.
