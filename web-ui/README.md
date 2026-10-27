# Agent 911 Web Console

Agent 911 is a browser-based, local-first control console with a dedicated core, its own tool-schema boundary, and a Guardian policy layer. It uses Cactus Needle 3 as the local inference runtime and reuses the existing workspace and tested capability store. It provides screens for task execution, model configuration, evidence checks, upgrade prompts, and Guardian policy/audit review. The browser app has no npm runtime dependencies and does not load third-party scripts.

## Requirements

- Node.js 20 or newer
- Python 3.10 or newer
- The repository's Python dependencies installed in the active environment
- Compatible local model assets for real local inference, unless the runtime can discover or download its configured assets
- Linux bubblewrap (bwrap) for shell/Python execution; these actions remain disabled if the sandbox is missing or the app runs as root

From the repository root:

```sh
python3 -m pip install -e .
node web-ui/server.mjs --workspace ./needle-workspace
```

Open the exact `http://127.0.0.1:8765` address printed in the terminal. Keep the terminal open while using the app, and stop it with Ctrl+C. On Windows, use `python` instead of `python3` if that is the installed interpreter.

No `npm install` is required. Run the combined Node and Python bridge UI tests with:

```sh
npm --prefix web-ui test
```

Run the Python suite separately with:

```sh
python3 -m unittest discover -s tests -v
```

Optional startup flags:

- `--workspace PATH`: workspace root, default `./needle-workspace`.
- `--weights FILE`: explicit local model weights.
- `--engine FILE`: explicit native engine library.
- `--config-dir PATH`: alternate Agent 911 user configuration directory.
- `--port PORT`: local HTTP port, default 8765.
- `--python EXECUTABLE`: choose a Python interpreter.
- `--context-tokens N`: bounded context budget.

Environment variables `NEEDLE3_WEIGHTS` and `NEEDLE3_LIB_PATH` may provide local runtime asset paths. Explicit command-line paths take precedence over paths saved from the Model Configuration screen.

## Included control surfaces

### Chat and action approval

Local, cloud-planning, and hybrid modes use bounded local tools. Proposed actions are schema-validated and workspace paths are checked before approval and again immediately before dispatch. Every action requires an explicit approval decision. Cloud-mode cards disclose that approved outcomes may be sent to the configured provider for review. The Guardian may expose shell and Python execution proposals only in local mode. These proposals are assessed, displayed with high-risk metadata, and still require a separate explicit approval. Execution never occurs inside model inference.

### Model Configuration

The Models screen stores the default mode and optional local model weights/engine paths under the Agent 911 user configuration directory. Asset paths must resolve to existing files; choosing an engine requires a weights file. Changing the model configuration discards the prior in-memory runtime so the next model request initializes from the new settings. Native engine libraries execute inside the local Python process, so only configure libraries you trust.

The optional cloud provider uses the OpenAI-compatible `/models` and `/chat/completions` endpoints. Remote URLs require HTTPS; plain HTTP is allowed only for localhost. API keys are not returned to browser JavaScript. Keys are held in memory by default and are persisted only when the explicit remember-key option is enabled.

### Guardian policy, shell, and arbitrary Python execution

The Guardian checks every proposal, assigns a risk level, records policy decisions locally, and repeats its assessment immediately before dispatch. Shell and Python execution are high-risk actions and require a user request that explicitly asks to run/execute code, a local-mode session, a valid sandbox, and a separate approval click. Host-sensitive path references, privilege escalation, kernel/mount management, host service controls, storage-formatting patterns, and common network clients are blocked by deterministic checks. The browser's Guardian screen shows execution availability and recent audit events. Audit entries retain timestamps, action names, risk/status, and SHA-256 input fingerprints; they do not store the full shell command or Python source.

Execution is supported only on Linux with bwrap available and with Agent 911 running as an unprivileged user. Install bubblewrap from your distribution package manager; on Kali/Debian the package is named bubblewrap. The executor fails closed—there is no direct-host fallback—if these conditions are not met. Run the web console in local mode for execution; cloud and hybrid sessions cannot execute shell or Python code, preventing their output from being sent to the configured provider by the normal cloud-review flow.

The subprocess runs inside bubblewrap with an unshared network/PID/IPC/UTS namespace, a read-only runtime, private temporary storage, a bounded timeout (1–30 seconds), resource limits, and output truncation. The host's root filesystem and user home are not mounted; only the selected workspace is made writable. The Guardian blocks several high-risk command/code patterns, but pattern checks are not a proof of safety. Approved code can still read or modify files in the chosen workspace and may exhaust resources within the configured limits. Bubblewrap is an OS sandbox, not a virtual machine; keep the system patched, do not run Agent 911 as root, and do not approve code you do not trust. A host kernel vulnerability or a bubblewrap misconfiguration remains outside what this application can guarantee.

### Verification and Live Run Test

- **Deterministic verification** runs the existing workspace and policy checks in a disposable temporary workspace that is deleted afterward. It does not need to load a model.
- **Live Run Test** initializes and calls the currently selected local model, checks the response structure, and runs a fixed read-only system diagnostic. Model-proposed calls are never dispatched during this diagnostic. It is a real inference test, so it may take time or fail if runtime assets are unavailable.

A passing deterministic suite does not prove the model is usable; a passing live model test does not prove every model-generated proposal is correct. Read each report and verify important outcomes independently.

### Upgrade prompts

Upgrade prompts are saved in the Agent 911 user configuration directory. A configured OpenAI-compatible cloud model can draft an implementation plan with steps, failure cases, tests, acceptance criteria, and rollback considerations. If no cloud planner is configured or it fails, Agent 911 saves the request as a draft rather than presenting the local tool-calling model's short reasoning as a long-form plan. The upgrade request is sent to the configured provider when cloud planning is used. Draft plans are advisory: submitting a prompt does not edit source code, install packages, change permissions, or apply an upgrade. Archive old requests when they no longer need to be tracked.

### Memory and data

Conversation history and user-approved preferences continue to be stored locally in the selected workspace by the underlying runtime. Clearing the visible chat clears only the display; it does not erase workspace data or stored conversation context. Cloud requests and approved action outcomes may be transmitted to the configured provider during planning and review.

## Server security

- The server binds only to `127.0.0.1`; it is not a LAN or public web server.
- It checks host/origin, issues a per-run browser token, limits JSON bodies, and sets a restrictive Content Security Policy and other browser security headers.
- All model and tool dispatch remains in the Python bridge. Browser requests cannot bypass proposal validation or the approval flow. Shell/Python dispatch additionally requires a Guardian execution context and a bubblewrap sandbox.
- The Python bridge reserves stdout for its JSON-lines protocol so runtime diagnostics cannot masquerade as API responses.
- Nothing here is a claim that a native model or real cloud provider has been tested in a given environment; use the Live Run Test on the machine where Agent 911 will run.

## Troubleshooting

- If the browser cannot connect, inspect the terminal. To print Python bridge diagnostics there, start with `NEEDLE_WEB_DEBUG=1 node web-ui/server.mjs --workspace ./needle-workspace`.
- If a local model fails to initialize, confirm that the model weights and native engine match your platform and runtime; configure them in Models or pass explicit paths.
- If port 8765 is already in use, choose another with `--port 8766`.
- If you need to isolate settings during a test, pass `--config-dir /tmp/agent911-test-config`.
