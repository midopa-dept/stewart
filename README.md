English | [한국어](README.ko.md)

# stewart

stewart is a small agent runner for OpenAI-compatible chat completion servers such as vLLM and llama.cpp.

Point it at a folder and a model you host yourself. The model works through tools: it reads and edits files, runs shell commands, searches and fetches web pages.
Every run leaves a full record, so you can compare what the model says it did with what it actually did.

Prompts, tool messages and command help are in Korean.

## What it is

- A tool-calling loop over `/v1/chat/completions`, written in Python 3.12 with two runtime dependencies (prompt_toolkit, rich).
- Two ways to run it:
  - `stewart run` takes one task and runs it unattended.
  - `stewart chat` is an interactive session with a fixed input line.
- No model is included. You start the server; stewart connects to it.

## Features

- Execution modes:
  - `auto` runs tools right away.
  - `edits` applies file edits right away and asks before shell commands.
  - `ask` asks before anything except reads.
  - `plan` only reads, then writes a plan.
- Checkpoints and rollback: `/undo`, `/rewind`, `stewart rewind`. An unattended run that does not finish can be rolled back automatically (profile setting).
- A working-folder boundary, secret-file protection, and a filtered shell environment (see [Safety model](#safety-model)).
- Context handling:
  - stewart reads the server's `max_model_len` and compacts history to fit.
  - On a context overflow it folds the history once and retries.
  - When a thinking turn hits the length limit with an empty answer, it retries that turn once with thinking off.
- Profiles (TOML) set tools, instructions and limits. Built-in profiles include `code`, `chat`, `research`, `organize` and `media`.
- `ask_user` for clarifying questions, `delegate` for a sub-worker with its own context, skills, and minimal hooks.
- Optional self-checks before finishing:
  - a check command
  - a requirements review
  - a "should I have asked?" check
  - a regression baseline
  - a todo check
- Optional memory: a run index, notes, per-subject pages and an injected index, maintained by `stewart dream`, which you run yourself.
- Run records in `events.jsonl`, `transcript.md`, `summary.json` and `facts.md`.

## Quick start

### 1. Install

You need macOS or Linux, [uv](https://docs.astral.sh/uv/) and git. [ripgrep](https://github.com/BurntSushi/ripgrep) makes search faster if it is installed.

```bash
git clone https://github.com/midopa-dept/stewart.git
cd stewart
uv sync
uv run stewart --help
```

Copy the example backend file and edit it:

```bash
mkdir -p ~/.config/stewart
cp backends.example.toml ~/.config/stewart/backends.toml
```

### 2. Try it with a local model

Start a small instruct model that supports tool calling with [llama.cpp](https://github.com/ggml-org/llama.cpp)'s `llama-server`:

```bash
llama-server -m <model.gguf> --port 8080 --ctx-size 16384 --jinja
```

In `~/.config/stewart/backends.toml`, `[backends.local]` points to `http://127.0.0.1:8080/v1`. Set `model` to the name your server reports. Then:

```bash
uv run stewart ping -b local
uv run stewart chat -b local --workdir ~/my-project
```

### 3. Use a rented GPU

To run a larger model on a rented GPU without exposing it to the internet, use [stewart-cloud](https://github.com/midopa-dept/stewart-cloud).

If you set up a server yourself, stewart works with any OpenAI-compatible endpoint that supports tool calls. With vLLM, enable a tool-call parser that matches your model family:

```bash
vllm serve <model> --served-model-name my-model \
  --host 127.0.0.1 --port 8000 --max-model-len 65536 \
  --enable-auto-tool-choice --tool-call-parser <parser>
```

Keep the port private. Reach it through an SSH tunnel or a private network, then add it as a backend (below).

## Configuration

stewart looks for the backend file in this order:

1. `STEWART_BACKENDS` (a file path)
2. `~/.config/stewart/backends.toml`
3. `backends.toml` at the repository root

```toml
[backends.cloud]
base_url = "http://127.0.0.1:8000/v1"
model = "my-model"                      # same as --served-model-name
max_context = 65536                     # replaced by the server's max_model_len at startup
concurrency = 8
api_key_env = "STEWART_CLOUD_API_KEY"   # name of the variable that holds the key; never the key itself
timeout_s = 240

[backends.cloud.params]                 # merged into every request body
temperature = 0.2
max_tokens = 8192
```

Common commands:

```bash
uv run stewart ping -b cloud
uv run stewart chat -b cloud --workdir ~/my-project
uv run stewart run -p code -b cloud --workdir ~/my-project --task "Fix the failing test"
uv run stewart run -p code -b cloud --workdir ~/my-project --plan --task "..."
uv run stewart profiles
```

- Profiles:
  - Built-in profiles live in `profiles/`.
  - Your own go in `~/.config/stewart/profiles/`.
  - Use `extends = "code"` to inherit a profile and override only what you need.
- Run records go to `~/.local/state/stewart/runs/<time>-<profile>/`.
- Web search:
  - Uses a [SearXNG](https://github.com/searxng/searxng) JSON endpoint set in `STEWART_SEARXNG_URL`.
  - `web_fetch` refuses private addresses.
- Thinking:
  - Toggle it with `--think` or `/think`.
  - Tune it with `STEWART_THINK_MAX_TOKENS` and `STEWART_THINK_SAMPLING`.
- Self-checks: `STEWART_CHECK_ON_STOP` (on in the `code` profile), `STEWART_REVIEW_ON_STOP`, `STEWART_ASK_CHECK`, `STEWART_BASELINE_DIFF` and `STEWART_TODO_CHECK`. The others are off by default.
- Memory: off by default. Turn it on with `memory = true` in a profile or `STEWART_MEMORY=1`.
- Hooks:
  - Set them in a profile's `[hooks]` table: `SessionStart`, `PreToolUse`, `PostToolUse` and `Stop`.
  - Each hook gets JSON on stdin. Exit code 2 blocks the action.
- Tests: `uv run python -m unittest discover -s tests -t .` runs against fake servers and needs no network.

## Safety model

stewart lets a model run shell commands. The rails below reduce mistakes; they are not isolation. Run it on a machine without important data, or inside an OS sandbox, a dedicated account or a container.

- **Working-folder boundary.**
  - Paths are resolved with realpath and their parent folders are checked by inode, so a symlink does not lead out of the folder.
  - A write outside the folder is refused in `run` and asked about in `chat`; an approval covers that path only.
  - `extra_write_dirs` and `read_outside` (allow, ask or deny) widen or narrow the boundary.
  - Shell commands are checked for common write forms only.
- **Secret files.** Tools neither read nor write files that look like `.env` files, keys or certificates (profile `[secret_files]`). stewart's own config folder is also protected.
- **Filtered environment.**
  - The model's shell does not see stewart's virtual environment, its `PATH` entry, `STEWART_*` variables, or any variable that contains stewart's install path.
  - API keys are read from environment variables named in the backend file; the file holds only the variable name.
- **Approvals.** Modes decide what runs without asking. Writes outside the folder and edits to stewart's config ask in every mode, and for these an empty answer counts as no.
- **Records.** Results from the Brave search engine are kept out of run records. `python -m stewart.scrub_records` removes them from older records (dry run by default).

## How it differs

stewart is a small, inspectable runner for self-hosted models, not a general agent framework.

- It assumes a model you serve yourself, and adapts to the server's context length and tool-call quirks: it re-asks after broken tool calls, detects repeated identical calls, and handles empty thinking-only turns.
- Records come first. Each run keeps enough detail to check the model's final report against the files it changed and the commands it ran.
- Self-checks and memory are separate switches that default to off, so you can turn on one at a time and see what it changes.

## Acknowledgements and references

stewart contains no code copied from the projects below. Third-party packages are listed in [NOTICE](NOTICE).

- Anthropic, [Claude Code](https://docs.anthropic.com/en/docs/claude-code) documentation: the shape of execution modes, hooks, todo lists, memory files and sub-agents.
- Anthropic, "The components of agent memory": memory layers (records, notes, pages, an injected index) and a small injected layer with one page per subject.
- Qwen3 model card: recommended sampling for thinking mode, used by `STEWART_THINK_SAMPLING=qwen`.
- [vLLM](https://github.com/vllm-project/vllm), [llama.cpp](https://github.com/ggml-org/llama.cpp), [SearXNG](https://github.com/searxng/searxng), [uv](https://github.com/astral-sh/uv).
- The tool-call format of the OpenAI Chat Completions API.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
