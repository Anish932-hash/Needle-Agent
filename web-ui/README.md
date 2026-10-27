# Agent 911 Web Console

Agent 911 is a browser-based, local-first control console with a dedicated Agent 911 core and its own tool-schema/validation boundary. It uses Cactus Needle 3 as the local inference runtime and reuses the existing workspace and tested capability store. It provides distinct screens for task execution, model configuration, evidence checks, and user-directed upgrade prompts. The browser app has no npm runtime dependencies and does not load third-party scripts.

## Requirements

- Node.js 20 or newer
- Python 3.10 or newer
- The repository's Python dependencies installed in the active environment
- Compatible local model assets for real local inference, unless the runtime can discover or download its configured assets

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

Local, cloud-planning, and hybrid modes use bounded local tools. Proposed actions are schema-validated and workspace paths are checked before approval and again immediately before dispatch. Every action requires an explicit approval decision. Cloud-mode cards disclose that approved outcomes may be sent to the configured provider for review. This UI does not add shell or arbitrary-code execution.

### Model Configuration

The Models screen stores the default mode and optional local model weights/engine paths under the Agent 911 user configuration directory. Asset paths must resolve to existing files; choosing an engine requires a weights file. Changing the model configuration discards the prior in-memory runtime so the next model request initializes from the new settings. Native engine libraries execute inside the local Python process, so only configure libraries you trust.

The optional cloud provider uses the OpenAI-compatible `/models` and `/chat/completions` endpoints. Remote URLs require HTTPS; plain HTTP is allowed only for localhost. API keys are not returned to browser JavaScript. Keys are held in memory by default and are persisted only when the explicit remember-key option is enabled.

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
- All model and tool dispatch remains in the Python bridge. Browser requests cannot supply a raw executable or an arbitrary tool to dispatch.
- The Python bridge reserves stdout for its JSON-lines protocol so runtime diagnostics cannot masquerade as API responses.
- Nothing here is a claim that a native model or real cloud provider has been tested in a given environment; use the Live Run Test on the machine where Agent 911 will run.

## Troubleshooting

- If the browser cannot connect, inspect the terminal. To print Python bridge diagnostics there, start with `NEEDLE_WEB_DEBUG=1 node web-ui/server.mjs --workspace ./needle-workspace`.
- If a local model fails to initialize, confirm that the model weights and native engine match your platform and runtime; configure them in Models or pass explicit paths.
- If port 8765 is already in use, choose another with `--port 8766`.
- If you need to isolate settings during a test, pass `--config-dir /tmp/agent911-test-config`.
