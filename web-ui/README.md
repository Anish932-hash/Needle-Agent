# Needle Agent Web UI

This is a browser-based alternative to the Textual terminal UI. The Node.js server serves a self-contained interface and starts a persistent Python bridge that reuses Needle Agent's existing model, workspace, cloud, validation, and action-dispatch code. No npm dependencies or third-party browser scripts are needed.

## Requirements

- Node.js 20 or newer
- Python 3.10 or newer
- Needle Agent and its Python dependencies installed in the active Python environment

From the repository root, install the Python project once:

```sh
python -m pip install -e .
```

On Kali/Linux systems where the command is `python3`, use `python3 -m pip install -e .`.

## Start

From the repository root:

```sh
node web-ui/server.mjs --workspace ./needle-workspace
```

Open the exact `http://127.0.0.1:8765` address printed in the terminal. Leave that terminal running while using the app. Stop it with Ctrl+C.

Optional paths can be provided with `--weights FILE` and `--engine FILE`, or through `NEEDLE3_WEIGHTS` and `NEEDLE3_LIB_PATH`. Use `--python EXECUTABLE` if your Python interpreter is not called `python3` (or `python` on Windows). Use `--port PORT` to choose a different local port.

No `npm install` is required. Run the Node server tests with:

```sh
npm --prefix web-ui test
```

## Safety and runtime behavior

- The HTTP server binds only to `127.0.0.1`; it is not a LAN or public web server.
- The server checks the request host/origin and issues a per-server browser token for API requests. It sets a restrictive Content Security Policy and other browser security headers.
- The Python bridge is the authority for all model calls, schema checks, path confinement, and tool dispatch. Browser-supplied tool names or arguments are not directly executed.
- Every proposed tool action receives its own approval card. Approve or deny each action; nothing executes simply because a model proposed it. Paths and schemas are checked again at execution time.
- Local tools remain restricted to Needle's existing allowlist and workspace. The web UI does not add shell execution, arbitrary code execution, or remote computer control.
- Cloud planning/hybrid modes require cloud configuration. The prompt and results of approved actions may be sent to the configured provider for planning/review; approval cards disclose this. The provider never executes local tools.
- API keys are not returned to browser JavaScript. By default, keys are held only in the Python process unless the user explicitly selects the remember-key option. A key may also be loaded from `NEEDLE_API_KEY` or the existing local config.
- Conversation memory and preferences continue to use Needle's existing local workspace stores. Clearing the visible browser transcript only clears the display; it does not erase agent memory or workspace files.

The UI uses one bounded hybrid proposal round per user request; if more work is required, review the outcomes and submit a follow-up request. As in the terminal UI, the cloud review is advisory and is not proof that a task is complete.

## Troubleshooting

- If the browser cannot connect, inspect the terminal where the server was started. Python bridge errors are printed there.
- If the local model is not found, pass the compatible model archive and native engine paths or set the documented environment variables.
- If port 8765 is already in use, choose another with `--port 8766`.
- The first local model initialization may take time and may need to download the runtime assets when no compatible local pair is staged.
