# 참고 자료와 stewart 구현

stewart 의 몇몇 기능은 공개된 연구·글을 보고 설계했습니다. 이 문서는 어떤 자료의 어떤 생각이 stewart 의 어디에 들어갔는지 적습니다.

- **들어감**: 그 기능이 지금 코드에 있습니다.
- **부분**: 생각의 일부만 들어갔습니다.
- **계획**: 아직 코드에 없습니다.

자료에서 가져온 코드는 없습니다. 웹 화면이 빌려 온 MIT 코드는 [ui/THIRD_PARTY.md](../ui/THIRD_PARTY.md) 에 따로 적었습니다.

## 문맥 압축

긴 작업에서는 문맥이 차면 모델이 느려지고 앞 내용을 놓칩니다. stewart 는 두 단계로 줄입니다.

1. 문맥이 최대 길이의 50% 를 넘으면 오래된 도구 결과를 덩어리째 가립니다.
2. 그래도 70% 를 넘으면 앞 대화를 요약 한 쪽으로 접습니다. 접은 원문은 기록 폴더(`folds/`)에 남습니다.

| 자료 | 핵심 생각 | stewart 의 자리 | 상태 |
|---|---|---|---|
| Lindenbauer et al., 2025. [The Complexity Trap: Simple Observation Masking Is as Efficient as LLM Summarization for Agent Context Management](https://arxiv.org/abs/2508.21433) | 최근 몇 단계 밖의 도구 결과만 가려도 요약만큼 효율적이다 | 오래된 도구 결과 가리기(`compact.py`). 매 단계 굴러가는 가리기는 `STEWART_COMPACT_MODE=mask-roll` 로 비교할 수 있지만 기본값은 아닙니다. 프롬프트 캐시를 덜 깨려고 덩어리째 한 번 가립니다 | 들어감 |
| Kang et al., 2025. [ACON: Optimizing Context Compression for Long-horizon LLM Agents](https://arxiv.org/abs/2510.00615) | 압축이 무엇을 잃어 실패했는지 보고 압축 지침을 다듬는다 | 요약 꼴에 "해 봤는데 안 된 길" 절을 두었습니다(`compact.SUMMARY_SYSTEM`). 지침을 자동으로 다듬지는 않습니다 | 부분 |
| Anthropic, 2025. [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) | 요약은 먼저 빠짐없이 쓰고 그다음 군더더기를 뺀다. 다 쓴 도구 결과는 지운다 | 요약 지시문과 원문 보관(`compact.py`, `chat.py`) | 들어감 |
| OpenHands, [Context Condenser](https://docs.openhands.dev/sdk/guides/context-condenser) 문서 | 처음과 최근은 남기고 가운데만 요약한다 | 시스템 지시·과제와 최근 10단계를 원문 그대로 둡니다(`engine.py`) | 들어감 |
| Hong, Troynikov, Huber (Chroma), 2025. [Context Rot: How Increasing Input Tokens Impacts LLM Performance](https://www.trychroma.com/research/context-rot) | 입력이 길어지면 같은 모델도 정확도가 떨어진다 | 줄이기 문턱을 50% 로 낮게 잡았습니다(`chat.py`) | 들어감 |
| Raj et al., 2026. [Model or Harness? An Interaction-Centric Taxonomy for Localizing Agent Failures](https://arxiv.org/abs/2607.28802) | 압축 뒤에 지시는 남고 그 까닭이 빠져 실패한다 | 요약에서 사람의 지시·금지를 원문에 가깝게 남깁니다. 압축 뒤에도 금지가 지켜지는지 확인하는 시험은 아직 없습니다 | 부분 |

## 기억

| 자료 | 핵심 생각 | stewart 의 자리 | 상태 |
|---|---|---|---|
| Packer et al., 2023. [MemGPT: Towards LLMs as Operating Systems](https://arxiv.org/abs/2310.08560) | 늘 싣는 작은 핵심과, 필요할 때 찾는 보관을 나눈다 | 요약 쪽(`MEMORY.md`)과 사용자 기억은 늘 싣고, 메모는 `memory_search` 로 찾습니다(`memory.py`) | 부분 |
| Chhikara et al., 2025. [Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory](https://arxiv.org/abs/2504.19413) | 새로 뽑은 사실을 이미 있는 기억과 대조해 더하기·고치기·지우기·그대로 중에 고른다 | 차례가 끝나면 사람 말에서 기억할 것을 뽑아 더하기·고치기·버리기로 맞춥니다(`memory.py`) | 부분 |
| Martin (Anthropic), 2026. [The components of agent memory](https://www.anthropic.com/research/components-of-agent-memory) | 기억을 기록·메모·쪽·주입 색인의 층으로 나눈다 | 실행 기록, 메모(`note_add`), 대상별 `MEMORY.md`, 지시문에 싣는 색인(`memory.py`, `dream.py`) | 들어감 |
| 위 두 논문을 바탕으로 한 세 갈래 기억(방침·프로필·자료) | 종류마다 싣는 법을 다르게 한다. 방침·프로필은 크기 상한 안에서 늘 싣고, 자료는 지시문 밖에 두고 `doc_search` 로 찾는다 | `memory.py`, `memstore.py`. 마크다운이 정본이고, 옛 기억은 처음 한 번 사본을 남기고 옮깁니다(`stewart memory migrate`) | 들어감 |

## 단계 세우기, 되짚기, 사람에게 넘기기

이 기능들은 논문이 아니라 실제로 써 보며 본 실패에서 나왔습니다.

| 기능 | stewart 의 자리 | 상태 |
|---|---|---|
| 큰 일은 할 일 목록으로 계획하고, 묶음마다 요약한 뒤 계속할지 묻는다 | chat·허브 지시문, 내장 스킬 `plan-checkpoints` | 들어감 |
| 같은 호출이 되풀이되면 알리고, 계속되면 멈춘다 | `engine.py` | 들어감 |
| 실패가 이어지면 도구 없이 한 번 되짚고, 그 결론을 그 세션의 금지로 걸어 도구 층에서 막는다 | `reflect.py`, `steer.py` | 들어감 |
| 다른 명령이 같은 오류를 세 번 내면 짐작을 멈추고 오류의 뜻부터 확인한다 | `steer.py`, `chat.py` | 들어감 |
| 비밀번호·키체인·sudo·로그인처럼 사람만 풀 수 있는 곳에 닿으면 같은 시도를 그만두고 사람 손 카드로 넘긴다 | `steer.py`, 허브 화면 | 들어감 |
| "확인하고 말한다": 파일·설정·버전·바깥 사실은 도구로 보고 근거를 단다 | `profiles/chat.toml` | 들어감 |
| 잘 된 꼴을 예시로 두고 비슷한 일에 하나만 붙이기(학습 없이) | `examples.py`, `improve.py` | 들어감 |
| 표시한 실사용 기록을 학습 자료로 내보내고, 어댑터를 골라 서빙 | `train.py`, `teacher.py`, backends `adapter`(학습·판정은 stewart-tune) | 부분 |

## 스킬과 문서 찾기

| 자료 | 핵심 생각 | stewart 의 자리 | 상태 |
|---|---|---|---|
| Anthropic, [Claude Code](https://docs.anthropic.com/en/docs/claude-code) 문서의 스킬(SKILL.md) 꼴 | 지시문에는 스킬의 이름과 설명만 싣고, 본문은 필요할 때 읽는다 | `load_skill`, 내장 스킬 17개(`src/stewart/skills_builtin/`). 사용자·프로젝트 스킬이 같은 이름이면 그쪽이 이깁니다 | 들어감 |
| BM25 낱말 순위 | 색인 서버나 임베딩 없이 낱말로 찾는다 | `doc_search`: 작업 폴더의 문서·코드와 한 번 읽은 웹 글(`docsearch.py`) | 들어감 |
| 임베딩 다시 순위 | 뜻으로 찾는다 | 없습니다 | 계획 |

## 도구 호출의 견고성

| 생각 | stewart 의 자리 | 상태 |
|---|---|---|
| 429·5xx·연결 끊김은 지수로 기다렸다 다시 보낸다. `Retry-After` 를 따른다 | OpenAI 호환 백엔드(`backend.py`), Anthropic 백엔드(SDK 의 재시도). chat·허브에서만 켜고, `run`·평가 길은 기본 0회 | 들어감 |
| 깨진 도구 호출은 다시 요청한다 | `engine.py` | 들어감 |
| 본문에 글로 쓴 도구 호출을 읽어 낸다 | 함수 호출 칸이 없는 API 를 위한 `tool_mode = "text"`(`texttools.py`) | 들어감 |
| 본문에 쓴 도구 호출(`<tool_call>` 꼴 등)을 읽어 내고, 깨진 인자 JSON(끝 쉼표·따옴표 등)을 한 번 고친다. 잘려 끝난 인자는 고치지 않는다 | `toolrepair.py`. chat·허브에서 켜고 `run` 은 끔 | 들어감 |
| 편집 뒤 고친 자리 앞뒤 몇 줄과 문법 검사 한 줄(.py·.json·.toml)을 결과에 붙인다 | `toolrepair.py` | 들어감 |
| 앞 차례의 생각을 다시 보내기 | `STEWART_REASONING_PASSBACK`(기본 끔, 실험) | 부분 |

## 실행기를 재는 방법

| 자료 | 핵심 생각 | stewart 의 자리 | 상태 |
|---|---|---|---|
| Wang et al., 2026. [Rethinking the Evaluation of Harness Evolution for Agents](https://arxiv.org/abs/2607.12227) | 실행기를 고치면서 본 과제로 다시 재면 이득이 부풀려진다 | 자체 과제는 회귀 시험으로만 씁니다. 엔진의 각 장치(되풀이 알림, 잘라내기, 다시 묻기, 끝내기 전 확인 등)가 언제 무엇을 했는지 실행 기록에 남겨 장치별 효과를 볼 수 있게 했습니다(`components.py`). 같은 과제를 여러 번 돌리는 `stewart bench -n` | 부분 |
| Merrill et al., 2026. [Terminal-Bench: Benchmarking Agents on Hard, Realistic Tasks in Command Line Interfaces](https://arxiv.org/abs/2601.11868) | 명령 줄에서 푸는 공개 벤치마크 | 어댑터가 없습니다 | 계획 |

같은 Claude 모델에서 Claude Code 와 실행기만 바꿔 잰 결과는 [README 의 비교 절](../README.md#같은-모델에서-실행기만-바꿨을-때)에 조건·한계와 함께 있습니다.

## 다른 실행기 비교의 근거

README 의 "다른 실행기와 무엇이 다른가" 표는 2026-11-11 에 아래 공식 문서·저장소로 확인했습니다. "확인 못 함"은 문서에서 찾지 못했다는 뜻이지 기능이 없다는 뜻이 아닙니다. stewart 쪽은 같은 날 코드로 확인했습니다.

| 실행기 | 확인한 판 | 출처 |
|---|---|---|
| OpenCode | 릴리스 목록 맨 위 v2.2.7(2026-11-10). 문서는 v1에서 v2로 옮기는 중 | [저장소](https://github.com/anomalyco/opencode), [providers](https://opencode.ai/docs/providers), [mcp-servers](https://opencode.ai/docs/mcp-servers), [lsp](https://opencode.ai/docs/lsp), [plugins](https://opencode.ai/docs/plugins), [permissions](https://opencode.ai/docs/permissions) |
| Pi | v1.5.2(2026-11-10) | [저장소](https://github.com/earendil-works/pi), [pi.dev](https://pi.dev) |
| Claude Code | v2.1.322(2026-11-10) | [문서](https://code.claude.com/docs/en/overview), [llm-gateway](https://code.claude.com/docs/en/llm-gateway), [sandboxing](https://code.claude.com/docs/en/sandboxing), [LICENSE](https://github.com/anthropics/claude-code/blob/main/LICENSE.md) |
| Codex CLI | rust-v0.167.1(2026-11-10) | [저장소](https://github.com/openai/codex), [sandboxing](https://learn.chatgpt.com/docs/sandboxing), [MCP](https://learn.chatgpt.com/docs/extend/mcp) |

GitHub 별 수는 같은 날 각 저장소 첫 화면의 값입니다. Claude Code 는 소스가 아니라 이슈 저장소의 별입니다.
