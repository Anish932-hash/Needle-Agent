# Needle Agent

A compact local OS-assistant starter built around **Cactus Needle 3**. It asks Needle to map natural-language requests to structured tool calls, then applies a deliberately small, auditable policy before any action is run.

> This is a lightweight extensible agent, not unrestricted computer control. Its initial tools inspect basic system information, list/search/read files, and write text files inside a dedicated workspace. It does not run shell commands, access arbitrary paths, or interact with the network. Add OS integrations narrowly and review their permissions before enabling them.

## Install and run

Requires Python 3.10+. The bootstrapper creates an isolated `.venv`, installs Needle Agent and its Python dependencies, creates the workspace directory, and detects compatible model/engine files already staged in `google_drive/`. It uses a compatible local wheel when available and otherwise lets `pip` use its configured package index. No administrator privileges are needed.

```sh
python install.py
```

To install and immediately launch the agent:

```sh
python install.py --run
```

The installer supports Windows, macOS, and Linux. Options include `--workspace PATH`, `--venv PATH`, `--weights PATH`, `--engine PATH`, and `--no-venv` (install into the active Python environment instead). On Windows, use `py install.py` if `python` is not on `PATH`. Launch the full-screen terminal UI with `needle-agent-tui` (or select `--run` to start the interactive CLI after setup). The TUI accepts `NEEDLE3_WEIGHTS` and `NEEDLE3_LIB_PATH` environment variables for model assets; otherwise, it detects the repository's optional `google_drive/` staging directory. If no compatible local model/engine pair is staged, Needle may download/cache runtime assets on first launch, which requires network access. The installer does not fetch or install arbitrary OS tools, request elevated privileges, or modify system settings.

## Terminal UI and cloud APIs

`needle-agent-tui` provides a keyboard-driven chat interface with separate Chat and Cloud setup tabs, model selection, a scrollable transcript, a visible runtime/mode status bar, and explicit approval dialogs. The chat toolbar shows the active execution mode and workspace name; **Clear chat** clears only visible transcript entries, never workspace files. During a request, the prompt and mode/action controls are locked to prevent overlapping model requests. Fixed log labels are styled, while user prompts and model/tool output are rendered as literal text so markup-looking content cannot alter the transcript's formatting. Choose **Cloud plan + local Needle** for cloud planning and review with local Needle proposing actions, or **Hybrid: Needle + Cloud tools** to combine local Needle proposals with cloud-proposed allowlisted tool calls. For setup, enter an OpenAI-compatible API base URL and API key, then choose **Validate key & detect models**. The UI calls `/models`, validates authentication, and populates the model picker; select a model and save.

In **Cloud plan + local Needle** mode, the cloud model returns advisory steps and reviews recorded outcomes; local Needle proposes actions. In **Hybrid: Needle + Cloud tools** mode, the cloud model also proposes structured calls to a small, explicit allowlist (`system_info`, `list_files`, `search_workspace`, `read_file`, `write_file`, `save_workflow`, and `run_capability`). Cloud proposals are untrusted suggestions, not executable instructions: the local host validates each call against its schema and allowlist, shows the exact arguments for approval, and performs the action locally only after approval. The cloud model never receives shell or arbitrary-code execution tools, never directly accesses the workspace, and cannot execute local actions itself. Duplicate proposals are collapsed, exact actions already attempted in a turn are suppressed, and the hybrid loop stops after three proposal rounds or six distinct actions. Each local action still requires approval. After execution, the host performs deterministic postcondition checks for file writes and reads (including exact read-back comparison); other tools receive output-shape checks where available. This local evidence is reported separately from the cloud model's advisory review, which is not proof of task completion. Prior outcomes sent to the models are bounded and treated as untrusted data. Each local action approval warns that its result may be shared with the configured provider for review.  No workspace content is sent just because a provider is configured. Missing model/API-key settings, model-discovery failures, and configuration-save errors are reported in the UI; request controls are restored after failures so the TUI can recover without restarting.

Remote endpoints must use HTTPS; plain HTTP is allowed only for localhost. The API key is held in memory for the session by default and is not written to disk. The optional **Remember API key** checkbox stores it in the platform's user configuration directory (for example, `$XDG_CONFIG_HOME/needle-agent/cloud.json` on Linux) with restrictive file permissions where supported; on Windows, protect the user account and profile. Base URL and selected model are saved without a key. Environment variable `NEEDLE_API_KEY` can supply the key instead. Cloud support targets OpenAI-compatible `/models` and `/chat/completions` endpoints; provider-specific APIs requiring different authentication or request formats are not supported. The cloud provider receives user prompts for planning and approved local action outcomes for review; do not use cloud mode for data you cannot share with that provider.


For a manual install, Python 3.10+ is required:

```sh
python -m venv .venv
source .venv/bin/activate   # macOS/Linux
. .venv\Scripts\Activate.ps1  # Windows PowerShell
python -m pip install -e .
needle-agent --workspace ./needle-workspace
```

When a compatible local model archive and native engine library are available, avoid downloading them at startup by providing both paths:

```sh
needle-agent --workspace ./needle-workspace --weights ./needle3.cact --engine ./needle/libneedle3.so
```

`--engine` expects the native shared library (for example, `libneedle3.so` on Linux), not the deployment bundle's static `libneedle.a` or CLI executable. The Linux `manylinux` engine wheel contains `needle/libneedle3.so`; extract that library and pass its path. Do not `pip install` the engine-only wheel over the Python package: install `cactus-needle` separately, then use `--engine` for the shared object.

The workspace is created if needed. Use `/quit` to exit. Needle 3 supports a 2–20 layer ladder; the installed package's documented runtime options determine model depth. This starter deliberately does not guess an undocumented constructor argument.

## Safety behavior

- Tool schemas are intentionally narrow and described literally, following Needle's integration guidance.
- Calls below confidence `0.80` require confirmation. Empty/refused calls are not treated as permission to act.
- **Every write requires explicit confirmation**, regardless of confidence.
- File paths are resolved and checked against the workspace; path inputs are limited to 4,096 characters, and reads and writes are capped at 64 KiB. Directory listings are capped at 200 entries, and text search at 500 files / 4 MiB per query (50 results max). Search skips symlinks and the agent's own memory files. The three internal state files (`.needle-agent-memory.json`, `.needle-agent-conversation.json`, and `.needle-agent-capabilities.json`) are reserved and cannot be accessed through ordinary workspace file tools. On POSIX systems that support descriptor-relative no-follow I/O, read_file opens targets relative to a pinned workspace directory and write_file uses a temporary file plus atomic rename to prevent parent/final symlink swaps from redirecting those operations. Other platforms use canonical path confinement and atomic replacement but do not receive the same guarantee against a concurrent local filesystem attacker.
- Needle output is a proposal, never executable code. Only locally implemented, schema-validated tools can be dispatched; saved workflows can compose only the five workspace tools.

Confidence is not a security guarantee. Review every proposed action and do not use this starter for safety-critical tasks. The confidence threshold is configurable in `src/needle_agent/agent.py`.

## Independent Needle sub-agents

Enable one or two additional model instances per request with `--subagents 1` or `--subagents 2`. Each reviewer runs in a separate Python process with an isolated temporary workspace and never executes tools. The agent only surfaces a proposal when a strict majority of successful model instances independently returns the same allowlisted tool and exact arguments; conflicting proposals fail closed. The least-confident supporting score controls confirmation. Sub-agents increase latency, CPU use, and memory use, so they are off by default. For example:

```sh
needle-agent --workspace ./needle-workspace --weights ./needle3.cact --engine ./needle/libneedle3.so --subagents 2
```

Consensus reduces some single-output mistakes but is not proof of correctness; Needle instances can share the same blind spots.

## Hallucination resistance

The system policy tells Needle to avoid guessing, to use earlier context only to interpret the current request, and not to claim tools ran. Runtime guardrails independently validate proposals: only explicitly allowlisted local tools and validated capability recipes can execute; tool names, argument keys, types, required fields, confidence values, and workspace paths are checked locally. Unknown or malformed calls are rejected, low-confidence actions require confirmation, and file writes always require approval. When Needle proposes no call, the CLI reports that no action occurred rather than presenting model-generated reasoning as fact. The agent does not claim a tool succeeded until local execution returns. These layers reduce hallucination impact but cannot guarantee model correctness; verify important results independently.

## Skill practice and verification sandbox

Before using a real workspace task, type `/verify` to run deterministic checks for workspace read/write/list/search behavior, path traversal blocking, and the tool allowlist. Use `/practice <request>` to ask Needle to propose and try a request against a fresh temporary workspace. Practice calls—including file writes—are restricted to that disposable workspace, which is deleted after the run. The report shows proposed calls, confidence, outcomes, and suppressed proposals. An unsupported or failed call marks practice as failed. Practice does not execute any action in the real workspace; when satisfied, submit the request normally to use the actual workspace and its normal confirmation policy.

## Persistent memory and context compaction

The agent keeps a bounded local conversation memory across launches. It records each request (truncated to 500 characters) and a short outcome label, not tool output contents; memory is provided as context to later Needle requests. Older turns are compacted into a bounded rolling text summary. An automatic context manager assembles approved preferences, lexically relevant prior turns, compacted summary, and the current request before each inference. It always retains the latest exchange for follow-up references, prioritizes the current request, and enforces a conservative approximate token budget (default 1,600 tokens; configure with `--context-tokens`). Oversized current requests are rejected with a request to shorten them rather than silently truncating task details. `/context` reports budget and compaction stats. Compaction is deterministic, not an LLM-generated summary, so older details can be lost.

Use `/context` to inspect memory size/compaction status, `/clear-memory` to clear conversation history, or `/forget` to clear both conversation history and learned preferences. Conversation data is saved in `.needle-agent-conversation.json` inside the workspace. Do not include secrets in requests if you do not want them stored locally; delete that file or use `/clear-memory` to remove the history. Tuning is available in `ConversationMemory` in `src/needle_agent/conversation.py`.

## Self-evolving behavior (bounded and opt-in)

The agent can adapt to user preferences across runs without rewriting its own code or changing its safety policy. Use `/learn <preference or correction>` to save a short lesson; for example, `/learn Use concise responses.` Future requests include up to the ten most recent user-approved lessons as contextual guidance. `/memory` lists them and `/forget` erases them. Lessons are stored locally in `.needle-agent-memory.json` inside the workspace (up to 50, 500 characters each); they are not automatically inferred from private conversations. Treat the file as user data and back it up or remove it as desired.

The agent can also create reusable capabilities when explicitly asked. A capability is a named, parameterized workflow built from tested recipes (`find_files`, `find_and_read`, `read_text_file`, `write_text_file`, `list_workspace`, or `system_check`) and existing workspace tools. Say, for example, “Create a capability named find_and_read that searches for a phrase and opens the first matching file.” Needle proposes a recipe; before it is saved, the agent validates it and executes a seeded smoke test in a disposable workspace. The CLI then asks approval before saving and refreshes Needle's tool list. A failed test prevents saving; redefining a capability replaces it only after the new definition passes. Run `/capabilities` to inspect saved workflows. Execute one with `/run-capability find_and_read {"query":"Aurora"}`; every run displays its plan and requires approval. Remove one with `/forget-capability find_and_read` (confirmation required). Workflows are bounded and path-confined; they cannot run shell commands or arbitrary generated code. `/practice <request>` can exercise a capability in an isolated workspace.

This intentionally does **not** self-modify source, install packages, expand permissions, or learn from unreviewed actions. New capabilities compose only abilities already provided by trusted tools. Truly new OS integrations still require adding and reviewing a trusted tool. This keeps adaptation inspectable, reversible, and user-controlled; it is not model fine-tuning or unrestricted self-programming.

## Architecture and extension

- `src/needle_agent/agent.py` — Needle 3 tool declarations and allowlisted dispatch.
- `src/needle_agent/tools.py` — workspace boundary and basic local tools.
- `src/needle_agent/cli.py` — interactive confirmation and local CLI.

For additional OS abilities, add one narrow function at a time, provide a clear docstring and typed arguments, validate inputs independently of the model, and require confirmation for destructive or externally visible effects. Keep per-turn toolsets small. For an optimized device deployment, see Cactus's Needle build targets and choose an appropriate layer depth/model artifact for that device.

## Tests

```sh
python -m unittest discover -s tests -v
```
