# stewart

> A small, self-hosted agent runner for OpenAI-compatible `/v1/chat/completions` servers (vLLM, llama.cpp).
> It runs a tool-calling loop (read/write/edit files, shell, search, web) on a local folder, records every run,
> and adds safety rails (plan/ask modes, working-folder boundary, checkpoints and rollback, hooks, opt-in memory).
> The prompts, tool messages and docs are in Korean. Python 3.12+, two runtime dependencies.

stewart 는 OpenAI `/v1/chat/completions` 꼴 서버(vLLM·llama.cpp 등)에 붙어 도는 작은 에이전트 실행기다.
작업 폴더 하나를 받아 모델이 도구(파일 읽기·쓰기·고치기, 셸, 검색, 웹)를 부르며 일을 하게 하고,
그 과정을 모두 기록으로 남긴다. 모델·서버는 직접 띄운다(이 저장소에는 모델이 없다).

- 무인 실행 `stewart run` 과 대화형 `stewart chat`(입력창이 고정된 화면)
- 실행 모드: `auto`(바로) · `edits`(파일 편집은 바로, 셸은 묻기) · `ask`(읽기 말고 다 묻기) · `plan`(읽기만 하고 계획)
- 체크포인트와 되돌리기(`/undo`·`/rewind`·`stewart rewind`), 끝내지 못한 무인 실행의 자동 되돌림(프로필 칸)
- 작업 폴더 경계(밖 쓰기는 chat 은 묻고 run 은 거부), 비밀 파일 보호, stewart 설정 폴더 보호
- 프로필(TOML)로 도구·지시문·상한을 정한다. 내장 프로필: `code`·`chat`·`research`·`organize`·`media` 등

---

## 1. 설치

필요한 것: macOS 또는 Linux, [uv](https://docs.astral.sh/uv/), git. (ripgrep `rg` 이 있으면 검색이 빨라진다.)

```bash
git clone <이 저장소 주소> stewart
cd stewart
uv sync
uv run stewart --help
```

백엔드 설정 파일을 만든다. 예시를 복사해 고친다.

```bash
mkdir -p ~/.config/stewart
cp backends.example.toml ~/.config/stewart/backends.toml
```

stewart 는 백엔드 설정을 이 차례로 찾는다: `STEWART_BACKENDS`(파일 경로) → `~/.config/stewart/backends.toml` → 저장소 뿌리 `backends.toml`.

## 2. 먼저 내 컴퓨터에서 작은 모델로 해 보기(선택)

GPU 를 빌리기 전에 흐름을 익히려면 [llama.cpp](https://github.com/ggml-org/llama.cpp) 의 `llama-server` 로 작은 GGUF 모델을 띄운다.
도구 호출을 지원하는 instruct 모델을 고른다.

```bash
llama-server -m <모델.gguf> --port 8080 --ctx-size 16384 --jinja
```

`~/.config/stewart/backends.toml` 의 `[backends.local]` 이 `http://127.0.0.1:8080/v1` 을 가리킨다. `model` 은 서버가 아는 이름으로 바꾼다.

```bash
uv run stewart ping -b local
uv run stewart chat -b local --workdir <작업 폴더>
```

## 3. GPU 를 빌려 모델을 띄우고 stewart 로 쓰기

아래는 처음 하는 사람 기준의 순서다. 우리는 27B 급 4비트 모델을 RTX 3090(24 GB) 한 장에서 vLLM 으로 띄워 썼다.
더 작은 모델이면 더 작은 GPU 로도 된다.

### 3-1. GPU 인스턴스 빌리기

1. GPU 를 시간 단위로 빌려 주는 곳(예: Vast.ai·RunPod·Lambda 등)에 계정을 만든다. 돈이 드는 일이다 — 켜 둔 시간만큼 낸다.
2. 인스턴스를 고른다.
   - GPU: 모델 크기에 맞게. 27B 4비트 + 긴 문맥이면 VRAM 24 GB 이상.
   - 디스크: 모델 크기 + 여유(27B 4비트면 50 GB 이상).
   - 이미지: vLLM 공식 이미지 `vllm/vllm-openai` 를 쓰면 설치가 없다. 판을 고정한다(태그나 digest).
3. **공개 포트를 열지 않는다.** vLLM 포트(8000)를 인터넷에 내놓지 않고, 아래 3-3 의 SSH 터널이나 사설망으로만 닿게 한다.

### 3-2. 인스턴스에서 vLLM 띄우기

인스턴스에 SSH 로 들어가 띄운다(공식 이미지면 `vllm` 명령이 있다). 모델은 Hugging Face 저장소 이름이나 내려받은 폴더다.

```bash
vllm serve <Hugging Face 모델 저장소 또는 폴더> \
  --served-model-name my-model \
  --host 127.0.0.1 --port 8000 \
  --max-model-len 65536 \
  --gpu-memory-utilization 0.92 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --reasoning-parser qwen3
```

- `--host 127.0.0.1`: 인스턴스 밖에서 바로 닿지 않게 한다(터널로만).
- `--enable-auto-tool-choice --tool-call-parser …`: stewart 는 도구 호출이 되어야 돈다. 파서는 모델 계열에 맞춘다
  (위 값은 Qwen3 계열에 우리가 쓴 것. 다른 계열은 vLLM 문서의 tool calling 표를 본다).
- `--max-model-len`: GPU 메모리에 맞게. stewart 는 시작할 때 `/v1/models` 의 `max_model_len` 을 읽어 그 길이에 맞춰 문맥을 접는다.
- 키를 쓰려면 `--api-key <값>` 을 더하고, stewart 쪽은 그 값을 환경 변수로 준다(파일에 적지 않는다).

로그에 서버가 떴다고 나오면 인스턴스 안에서 확인한다:

```bash
curl -s http://127.0.0.1:8000/v1/models
```

### 3-3. 내 컴퓨터에서 터널로 붙기

빌려 주는 곳이 알려 준 SSH 주소·포트로 터널을 연다(이 창은 열어 둔다).

```bash
ssh -N -L 8000:127.0.0.1:8000 -p <SSH 포트> root@<인스턴스 주소>
```

이제 내 컴퓨터의 `127.0.0.1:8000` 이 인스턴스의 vLLM 이다. 사설망(예: Tailscale)을 쓰면 터널 대신 그 주소를 쓴다.

### 3-4. stewart 설정과 실행

`~/.config/stewart/backends.toml` 의 `[backends.cloud]` 를 고친다.

```toml
[backends.cloud]
base_url = "http://127.0.0.1:8000/v1"
model = "my-model"                 # vllm --served-model-name 과 같게
max_context = 65536                # 서버 값과 다르면 시작할 때 서버 값을 쓰고 알려 준다
concurrency = 8
api_key_env = "STEWART_CLOUD_API_KEY"   # --api-key 를 썼으면 이 환경 변수에 값을 넣는다
timeout_s = 240
```

```bash
uv run stewart ping -b cloud                        # 연결·한 줄 답·도구 호출 확인
uv run stewart chat -b cloud --workdir ~/my-project # 대화형
uv run stewart run -p code -b cloud --workdir ~/my-project --task "실패하는 시험을 고쳐라"
```

- `run` 은 무인 실행이다. 기본 모드는 `auto` 라 도구를 바로 쓴다. 처음에는 `chat`(기본 `edits`) 이나 `run --plan` 으로 보는 것이 안전하다.
- 실행 기록은 `~/.local/state/stewart/runs/<시각>-<프로필>/` 에 남는다(`transcript.md`·`events.jsonl`·`summary.json`·`facts.md`).

### 3-5. 다 쓰면 반드시 끈다

빌려 주는 곳의 화면이나 명령으로 인스턴스를 **정지하거나 지운다.** 터널만 닫으면 돈은 계속 나간다.

## 4. 알아 둘 것

| 주제 | 요지 |
|---|---|
| 프로필 | `profiles/*.toml`. 사용자 프로필은 `~/.config/stewart/profiles/`, `extends = "code"` 로 내장 프로필을 물려받고 바꿀 칸만 적는다. `uv run stewart profiles` |
| 모드 | `--mode auto\|edits\|ask\|plan`(chat), `run --plan`. chat 에서 `/mode`·`/plan on\|off` |
| 되돌리기 | 도구가 쓴 파일은 차례마다 체크포인트. chat `/undo`·`/rewind N`, `stewart rewind <기록>`. 셸이 바꾼 파일은 되돌리지 못한다 |
| 작업 폴더 경계 | 작업 폴더 밖 쓰기: chat 은 묻고(그 경로만 승인), run 은 거부. 프로필 `extra_write_dirs`·`read_outside`. 셸은 흔한 쓰기 꼴만 본다 — 완전한 격리가 아니다 |
| 비밀 파일 | `.env`·키·인증서 꼴은 도구가 읽지도 쓰지도 않는다(프로필 `[secret_files]`) |
| 셸 환경 | 모델의 셸에서 stewart 자신의 가상 환경·`STEWART_*` 변수를 뺀다 |
| 웹 | `web_search` 는 SearXNG JSON API(`STEWART_SEARXNG_URL`), `web_fetch` 는 사설 주소를 막는다. Brave 엔진 결과는 기록에 남기지 않는다 |
| 기억(기본 끔) | 프로필 `memory = true` 또는 `STEWART_MEMORY=1`. 실행 색인·메모·대상별 쪽·주입 색인. 쪽과 색인은 `stewart dream` 이 쓴다 |
| hooks | 프로필 `[hooks]`: `SessionStart`·`PreToolUse`·`PostToolUse`·`Stop`. 표준 입력 JSON, exit 2 = 막음 |
| 자기 점검(기본 끔) | `STEWART_CHECK_ON_STOP`(code 는 켬)·`STEWART_REVIEW_ON_STOP`·`STEWART_ASK_CHECK`·`STEWART_BASELINE_DIFF`·`STEWART_TODO_CHECK` |
| 생각(thinking) | `--think`·`/think`. 생각 켬 차례의 상한·샘플링: `STEWART_THINK_MAX_TOKENS`·`STEWART_THINK_SAMPLING` |
| 시험 | `uv run python -m unittest discover -s tests -t .`(가짜 서버로만, 망 없음) |

**안전에 대해.** stewart 는 모델이 셸 명령을 돌리게 한다. 경계·모드·확인은 실수를 줄이는 장치이지 격리가 아니다.
중요한 자료가 있는 기기에서 `auto` 로 돌리지 말고, 운영체제 샌드박스·전용 계정·컨테이너 안에서 쓰는 것을 권한다.

## 5. 우리가 한 것

이 저장소에서 실제로 만들고 시험한 것만 적는다.

- OpenAI chat/completions 도구 호출 고리: 깨진 툴콜 다시 묻기, 같은 호출·같은 결과 되풀이 감지와 마무리 강제, 남은 단계 알림, 한국어 보고 강제, JSON 스키마 출력
- 서버 문맥 길이(`max_model_len`) 읽기, 응답 상한 몫을 뺀 문맥 접기, 문맥 넘침 뒤 한 번 접고 다시 보내기
- 생각만 하다 생성 상한에 닿아 빈 답이 된 차례를 생각 끔으로 한 번 다시 보내기, 잘린 생각의 되풀이 지표 기록
- 실행 기록(`events.jsonl`·대화록·`facts`) — 모델의 보고와 실제로 한 일을 대조하게
- 체크포인트·되돌리기, 끝내지 못한 실행의 되돌림, 작업 폴더 경계(realpath·inode 로 판정), 비밀 파일·설정 폴더 보호, 셸 환경 정리
- 실행 모드(auto·edits·ask·plan), 되묻기 도구(`ask_user`), 하위 작업자(`delegate`), 스킬, hooks 최소판
- 끝내기 전 확인 명령(실패면 출력을 돌려줌)과 켬·끔 손잡이(요구 대조·되묻기 판단·회귀 기준선·할 일 확인)
- 기억: 실행 색인·메모·대상별 쪽·주입 색인과 `stewart dream`(사람이 부르는 정리, 코드가 근거·꼴·크기를 확인)
- 폴더 정리 도구(훑기·계획·검토 문서·승인 sha 로만 실행·되돌리기), 미디어 분류 보조
- 대화형 화면(입력창 고정, 진행 표시, 답변 중 끼워 넣기)

## 6. 출처·참조

설계에 참고한 것과 쓰는 바깥 것이다. 코드를 가져온 곳은 없다(바깥 꾸러미는 NOTICE).

- Anthropic, *Claude Code* 문서 — 실행 모드·hooks·할 일 목록·기억 파일·하위 에이전트의 사용 꼴을 참고했다.
- Anthropic, "The components of agent memory"(2026-11) — 기억의 층(기록·메모·쪽·주입 색인)과 꿈꾸기, "주입층은 작게·대상마다 한 쪽" 권고.
- Qwen3 모델 카드의 생각 모드 샘플링 권장값 — `STEWART_THINK_SAMPLING=qwen` 묶음.
- [vLLM](https://github.com/vllm-project/vllm)(OpenAI 호환 서버·도구 호출 파서), [llama.cpp](https://github.com/ggml-org/llama.cpp), [SearXNG](https://github.com/searxng/searxng), [uv](https://github.com/astral-sh/uv).
- OpenAI Chat Completions API 의 도구 호출 꼴.

## 라이선스

Apache License 2.0. `LICENSE`·`NOTICE` 를 본다.
