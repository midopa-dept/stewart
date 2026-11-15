# 분석 스크립트

결과를 보기 전에 sha256 을 고정(등록)한 분석 스크립트와, 공개 벤치 부분 집합 id 목록이다. 공개판은 원본에서 **기계 경로·내부 이름이 든 줄만** 바꿨다. 결과 전에 고정한 해시는 원본 해시이므로, 아래 표에 원본과 공개판 sha256 을 나란히 적는다.

- 등록 문장(사전 등록·실험 표): [`../registry/prereg/`](../registry/prereg/), [`../registry/experiments.tsv`](../registry/experiments.tsv)
- 개발·봉인 규약: [`../protocol.md`](../protocol.md), 봉인 묶음 표: [`../registry/holdout-sets.tsv`](../registry/holdout-sets.tsv)
- 평가기 판 해시: [`../eval-hashes/`](../eval-hashes/)

## 1. 무엇을 셈하나

| 파일 | 셈하는 것 |
|---|---|
| `defects.py` | 합친 평가 결과 폴더마다 회차별 결함 지표(잘림·빈 답·파싱·형식 실패·도구 오류·연속 같은 호출·되돌림·언어 재시도·멈춘 까닭)를 세어 한 줄 요약을 찍고 `<폴더>.defects.jsonl` 로 쓴다. |
| `make_subset.py` | SWE-bench Verified test 500 에서 저장소 × 난이도 층화·비례 배분으로 고정 부분 집합 200 을 뽑는다(시드 20261110). 그 앞 100 이 sub100(포함 관계). |
| `reps.py` | E13 v2 결과의 과제별 분산으로 X1-01 회차 수를 어림한다(짝 차이의 과제 군집 부트스트랩 95% CI, τ² 로 투영한 회차 수별 CI 반폭). |
| `swebv-all-ids.txt` | SWE-bench Verified test 500 의 id 전체(공개 벤치). |
| `swebv-sub200.txt` · `swebv-sub100.txt` | `make_subset.py` 가 낸 부분 집합 id(공개 벤치). |
| `x1/fence_stats.py` | 울타리 안 실행의 칸별 통계(agent_fence 값, 거부 줄·/tmp 쓰기 거부 회차, 그 회차들의 통과 수, 디스크 훑기 시도, 밖 의심·누설·격리 위반 수). |
| `x1/make_x1_schedule.py` | X1-01 S4 대기열·smoke 대기열·차례표를 만든다. 14차례를 시드 20261110 으로 섞고, 조각은 evalq 의 `expand_shards` 를 그대로 쓴다. 같은 인자면 같은 바이트. |
| `x1/merge_shards.py` | 과제를 나눠 돈 조각 결과를 합쳐 평가기 자신의 `aggregate()`·`write_md()` 로 요약을 만든다. 끊긴 조각(`--partial`)·고침 차례(`--fix`)·다시 돌릴 과제 목록(`--plan-fix`)·자기 시험(`--selftest`). 다시 채점하지 않는다. |
| `x1/path_scan.py` | 회차 기록(events.jsonl)의 도구 호출 인자·출력에서 평가 쪽 민감 경로를 연 흔적을 찾아 OPEN·LIST·OUT 으로 나눈다. |
| `x1/task_sha.py` | 평가기 판 폴더의 과제 정의 sha(실행 코드와 무관)와 묶음 sha 를 낸다. 출력은 묶음 sha·과제 수·판뿐이다. |
| `x1/x1_analysis.py` | P-001(X1-01·X1-02·X1-04) 분석본. 거르기(§8·§H·G1–G3) → 과제 평균 → 층화 부트스트랩 CI(B=10,000, 시드 20261110) → H1–H6 대비 → Holm. 등록과 어긋나거나 모호한 곳은 `KNOWN_GAPS`(G1–G13)에 적혀 있다. |
| `x1/test_x1_analysis.py` | `x1_analysis.py` 시험. 가짜 결과만 쓴다(effect·null·missing 세 꼴). |

## 2. 환경 변수·인자

원본은 경로를 그 기계의 절대 경로 상수로 들고 있었다. 공개판은 이를 환경 변수로 뺐다. 기본값은 자리값이고, **원래 실행에 쓴 값은 그 기계 경로라 싣지 않는다.** 원래 값을 주면 원본과 같은 정규식·같은 경로가 된다(정규식은 원본과 문자열이 같은지 확인했다, §4).

| 스크립트 | 환경 변수 | 뜻 | 기본값 |
|---|---|---|---|
| `defects.py` | `OUT` | `.defects.jsonl` 을 쓸 폴더(원본에도 있던 것) | `.` |
| `x1/fence_stats.py` | `FENCE_MOUNT_ROOT_RX` | 디스크 훑기로 볼 외장 볼륨 뿌리(정규식 조각) | `/mnt` |
| | `FENCE_PROJECTS_RX` | `cd` 로 가면 훑기로 셀 작업 루트(정규식 조각) | `/path/to/projects` |
| `x1/path_scan.py` | `PATH_SCAN_LAB_RX` | 연구 작업 폴더(그 아래 eval·runs·queue) | `stewart-lab` |
| | `PATH_SCAN_HELDOUT_RX` | 봉인 판 폴더 | `<PATH_SCAN_LAB_RX>/eval/h1.<숫자>` 꼴 |
| | `PATH_SCAN_RECORDS_RX` | 운영 기록 폴더 | `/path/to/records` |
| | `PATH_SCAN_EVAL_CACHE_RX` | 평가 cache 뿌리(클론은 그 아래 `stewart-<7자>`) | `/path/to/eval-cache` |
| | `PATH_SCAN_AGENT_CACHE_RX` | 에이전트 평가·기억 cache | `/path/to/agent-cache/(?:eval\|memory)` |
| `x1/x1_analysis.py` | `X1_RESEARCH_RX` | 연구 저장소·연구 작업 경로(access 확인) | `/path/to/stewart-research` |
| | (위 `PATH_SCAN_*`) | access 확인에 `path_scan.py` 를 그대로 쓴다 | |
| `x1/merge_shards.py` | `STEWART_EVAL_ROOT` | 평가기 판 폴더들의 뿌리 | 스크립트의 두 단계 위 폴더(원본과 같음) |
| | `STEWART_RUNS_ROOT` | 합친 결과를 쓸 폴더 | `<STEWART_EVAL_ROOT>/../runs`(원본과 같음) |
| `x1/make_x1_schedule.py` | `STEWART_LABS` | 연구 작업 폴더(그 아래 `eval/tools`·`queue`) | `.` |
| | `STEWART_EVALQ_DIR` | `evalq.py` 가 있는 폴더 | `<STEWART_LABS>/tools/evalq` |
| | `X1_ALIAS_ORIG`·`X1_ALIAS_ABL`·`X1_ALIAS_ABL3P` | 서빙 별칭(모델 판 이름 가림) | 역할 이름 `orig`·`abl`·`abl3p` |
| `x1/test_x1_analysis.py` | `X1_TEST_B` | 부트스트랩 횟수(원본에도 있던 것) | `2000` |

명령 인자는 원본과 같다.

- `defects.py <합친 결과 폴더>…`, `reps.py <합친 결과 폴더>…`
- `make_subset.py`(입력 `swebv-meta.json` — 이 폴더에 싣지 않음, 출력 `swebv-sub200.txt`·`swebv-sub100.txt`)
- `fence_stats.py <결과 폴더 glob>…`, `path_scan.py <결과 폴더 glob>…`
- `make_x1_schedule.py [--hosts <inst>:<machine>,<inst>:<machine>] [--check]`
- `merge_shards.py --eval <판> --label <이름> <조각>… [--partial] [--fix <폴더>] [--concat-reps]`, `--plan-fix <evalq 실행 폴더> <묶음>`, `--selftest <완주 결과 폴더> [--shards N] [--keep <폴더>]`
- `task_sha.py <평가기 판 폴더>`
- `x1_analysis.py <설정.json> [--out <결과.json>] [--boot 10000]`
- `test_x1_analysis.py`(인자 없음, `PATH_SCAN_*`·`X1_RESEARCH_RX` 를 비우고 돌린다)

## 3. 원본 해시와 공개판 해시

**공개판은 경로·주석 줄만 바뀌었고 셈 줄은 같다.** 바뀐 줄은 경로 상수를 환경 변수로 뺀 줄(`import os` 한 줄 포함), docstring·주석의 내부 이름, 그리고 셈에 쓰이지 않는 글 상수(서빙 별칭·평가 장비 이름·`KNOWN_GAPS` 글 세 곳)뿐이다. 식·조건·반복·시드·문턱은 한 글자도 바꾸지 않았다.

"바뀐 줄"은 `diff` 로 센 원본에서 빠진 줄 / 공개판에 들어간 줄이다.

| 파일 | 원본 sha256 | 공개판 sha256 | 바뀐 줄(원본 − / 공개판 +) | 바뀐 것 |
|---|---|---|---|---|
| `defects.py` | `6a5fccdda0001ca15667811f53ebbff65d5be2e3679dd907d67708d8560323ac` | `a8dc1db410fdcc48c9f84842950e22b5d5f0a7b27e199fb089dcbc0604be12ba` | 1 / 1 | docstring 의 세션 이름 |
| `make_subset.py` | `8412cd3eab96bfede4469770540a36e5317370b1d29b4ac5bdfc368ada14ef22` | `8412cd3eab96bfede4469770540a36e5317370b1d29b4ac5bdfc368ada14ef22` | 0 / 0 | 그대로 |
| `reps.py` | `e023b00fc4401f36563abcf43fc56f3b86f72b015d7800690ec390f127cc904c` | `ee51579f5c17494cfa06a07d4921b41b572f61040b32c8e82da960ebbbcea38f` | 1 / 1 | docstring 의 세션 이름 |
| `swebv-all-ids.txt` | `a6b0fd7c8c2969a0eef892e032250adcfa6d32362d395c246930e61b575ac9b9` | 같음 | 0 / 0 | 그대로 |
| `swebv-sub100.txt` | `de6d8bc488f540aa170caf73a22cff5c5bb1740bbd3e431697d51296aba0e173` | 같음 | 0 / 0 | 그대로 |
| `swebv-sub200.txt` | `6591c82cfad4a524d60776254c56afad112d1f9200ed0deec353227a250d050c` | 같음 | 0 / 0 | 그대로 |
| `x1/fence_stats.py` | `d2f4211781db49ca52fe20ae56c471a99a8baf5f89e56ea6ee83ea5792f09ba6` | `bbfc1bdf663b6c7f9440b24b750aa84c519d2235da334bcb04151328d6defdd5` | 4 / 10 | 훑기 정규식의 경로 → 환경 변수, docstring |
| `x1/make_x1_schedule.py` | `c4019a7292d56319584baf9c21bd8be801c2215f77fcacae93c8570f331cb978` | `cc4b49394651d60cef012b9f4216c750a47508ff1fe944593ec1fcaad93f74c9` | 7 / 11 | 작업 폴더·evalq 경로 → 환경 변수, 서빙 별칭 → 역할 이름, 평가 장비 이름, docstring·주석 |
| `x1/merge_shards.py` | `95a2e8edba7127ba992c62961f74e3bdddfc46f270973c44e2e199af8bb84699` | `9069d6fe867372504ff65e271081102c1bb09a44afef06de0870975b21eca011` | 5 / 9 | 뿌리 폴더를 환경 변수로도 줄 수 있게(기본값은 원본과 같음), docstring |
| `x1/path_scan.py` | `8ca9d799f94b14a75e71f4d434405576e55e138c6632090c886e3b1b732505c8` | `72c9d698a13336c8f4d3b8067ba72fc24fa9b8706b3478cdb41049227f9a9501` | 13 / 25 | 민감 경로 정규식의 경로 조각 → 환경 변수, docstring |
| `x1/task_sha.py` | `535af3bde7c6c351f6cd7f361d7c85400b78628bbbc06e3aa259bc8b16a6a628` | `4bede2aeac62fb75e91cfb1825dbd72c9539bdeca02781a077bb0922f1b35465` | 2 / 2 | docstring |
| `x1/test_x1_analysis.py` | `5a7d7966cc8a54f2f31cc4c24703b3bb37354631cb8edb87ed919418e6364a7d` | `b1168e965b7b4de8ae5dd2467beeb9231d9751a3f398bfd97f09ad34a67e0b17` | 8 / 9 | 가짜 자료의 경로를 공개판 기본값에 맞춤, 가짜 장비·별칭·어댑터 이름 일반화, docstring |
| `x1/x1_analysis.py` | `a9eb107629c26d4dcb3b4c20b659d853d22cb4a305d9be7847d34e387822a385` | `89d9a61f912a50bed9bfd5822307957c5ee2e9727cda5b7d6ba1ba1d91891346` | 9 / 12 | 연구 경로 정규식 → 환경 변수, docstring, `KNOWN_GAPS` 글(G1·G8·G11) |

알아 둘 것:

- `x1_analysis.py` 는 결과 JSON 에 자기 sha(`script_sha256`)와 `path_scan.py` sha 를 적는다. 등록 때 고정한 것은 위 표의 **원본** sha 다. 공개판에 원래 경로 값을 환경 변수로 주고 같은 입력으로 돌리면, 이 두 칸과 `known_gaps` 글 세 곳만 원본 출력과 다르다(환경 변수를 비우면 access 확인 정규식이 자리값이라 밖 접근 확인 수가 달라질 수 있다).
- `make_x1_schedule.py` 도 차례표에 자기 sha 를 적는다. 공개판으로 만든 차례표는 `script_sha256`·별칭·평가 장비 이름 칸이 원본과 다르고, 차례 순서·조각·배정은 같은 시드·같은 식이다. 그래서 공개판의 `--check` 는 등록한 차례표와 "다름"으로 나온다.
- `fence_stats.py` 의 홈 표시(물결 글자, U+007E)는 정규식 안에서 `\x7e` 로 적었다. 정규식으로는 같은 글자다.
- swebv id 세 파일의 sha 는 봉인 묶음 표의 `swebench-verified-sub200` 줄(a6b0fd7c… · de6d8bc4… · 6591c82c…)과 같다.

## 4. 시험

- 문법: 공개판 `.py` 10개 모두 `py_compile` 통과(경고를 오류로 두고).
- `x1/test_x1_analysis.py`: 원본(임시 복사본)과 공개판을 각각 `python3 -I -B test_x1_analysis.py` 로 돌렸다. 둘 다 35줄, **0 실패**, rc 0, 출력이 바이트까지 같다(부트스트랩 CI·Holm·거르기 수 모두). 이 파일은 `unittest` 꼴이 아니라 스스로 도는 시험이다.
- 정규식 같음: 공개판 `path_scan.py`·`fence_stats.py` 에 원래 실행 값을 환경 변수로 주면 `SENS` 정규식 여덟 개가 원본과 문자열까지 같고, 훑기 정규식 둘도 같은 글을 같게 잡는다(물결 글자 → `\x7e` 표기만 다름).
- 외부 자료가 있어야 도는 스크립트(`defects.py`·`reps.py`·`make_subset.py`·`fence_stats.py`·`path_scan.py`·`merge_shards.py`·`task_sha.py`·`make_x1_schedule.py`)는 실제 결과·평가기 판 폴더가 없어 돌리지 않았다.
