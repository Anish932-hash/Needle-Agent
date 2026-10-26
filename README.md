# Needle Agent

A compact local OS-assistant starter built around **Cactus Needle 3**. It asks Needle to map natural-language requests to structured tool calls, then applies a deliberately small, auditable policy before any action is run.

> This is a lightweight extensible agent, not unrestricted computer control. Its initial tools inspect basic system information, list/search/read files, and write text files inside a dedicated workspace. It does not run shell commands, access arbitrary paths, or interact with the network. Add OS integrations narrowly and review their permissions before enabling them.

## Install and run

Requires Python 3.10+. Needle's published Python package fetches/caches the inference engine and model assets as documented by Cactus; first use may require network access.

```sh
python -m venv .venv
. .venv/bin/activate        # Windows: .venv\\Scripts\\activate
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
- File paths are resolved and checked against the workspace; reads and writes are capped at 64 KiB, directory listings at 200 entries, and text search at 500 files / 4 MiB per query (50 results max). Search skips symlinks and the agent's own memory files.
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
