import http from "node:http";
import { spawn } from "node:child_process";
import { createReadStream, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { randomBytes } from "node:crypto";
import { createInterface } from "node:readline";

const WEB_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(WEB_DIR, "..");
const PUBLIC_DIR = path.join(WEB_DIR, "public");
const MAX_BODY_BYTES = 1024 * 1024;
const BRIDGE_TIMEOUT_MS = 10 * 60 * 1000;
const HOSTS = new Set(["127.0.0.1", "localhost"]);

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    "Cache-Control": "no-store",
  });
  res.end(body);
}

function getSafeErrorStatus(error) {
  const message = String(error?.message || error || "");
  if (/^ValueError:|^TypeError:|^Request must|^Unknown bridge operation|^Action id/i.test(message)) return 400;
  if (/timed out/i.test(message)) return 504;
  if (/not running|exited|spawn/i.test(message)) return 503;
  return 502;
}

class PythonBridge {
  constructor({ workspace, weights, engine, contextTokens = 1600, pythonExecutable }) {
    const executable = pythonExecutable || process.env.NEEDLE_PYTHON
      || (process.platform === "win32" ? "python" : "python3");
    const args = ["-u", "-m", "needle_agent.web_bridge", "--workspace", workspace,
      "--context-tokens", String(contextTokens)];
    if (weights) args.push("--weights", weights);
    if (engine) args.push("--engine", engine);

    const inheritedPythonPath = process.env.PYTHONPATH || "";
    const pythonPath = [path.join(REPO_ROOT, "src"), inheritedPythonPath].filter(Boolean).join(path.delimiter);
    this.child = spawn(executable, args, {
      cwd: REPO_ROOT,
      env: { ...process.env, PYTHONPATH: pythonPath, PYTHONUNBUFFERED: "1" },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    this.pending = new Map();
    this.sequence = 0;
    this.closed = false;

    this.child.stderr.on("data", (chunk) => {
      const value = chunk.toString();
      if (process.env.NEEDLE_WEB_DEBUG === "1") process.stderr.write("[needle-python] " + value);
    });
    this.child.on("error", (error) => this.rejectAll(new Error("Python bridge could not start: " + error.message)));
    this.child.on("exit", (code, signal) => {
      this.closed = true;
      const detail = code === 0 ? "exited" : "exited (" + (code ?? signal ?? "unknown") + ")";
      this.rejectAll(new Error("Python bridge " + detail + ". Check the terminal for startup errors."));
    });

    const lines = createInterface({ input: this.child.stdout, crlfDelay: Infinity });
    lines.on("line", (line) => this.handleLine(line));
  }

  rejectAll(error) {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }

  handleLine(line) {
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      const error = new Error("Python bridge emitted unexpected output. Restart the web UI and inspect the terminal.");
      this.rejectAll(error);
      return;
    }
    const pending = this.pending.get(String(message.id));
    if (!pending) return;
    this.pending.delete(String(message.id));
    clearTimeout(pending.timer);
    if (message.ok) pending.resolve(message.result);
    else pending.reject(new Error(String(message.error || "Python bridge request failed.")));
  }

  call(op, payload = {}, timeoutMs = BRIDGE_TIMEOUT_MS) {
    if (this.closed || this.child.killed || !this.child.stdin.writable) {
      return Promise.reject(new Error("Python bridge is not running. Restart the web UI."));
    }
    const id = String(++this.sequence);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error("Python operation timed out. Check the Python runtime and terminal output."));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.child.stdin.write(JSON.stringify({ id, op, payload }) + "\n", (error) => {
        if (error && this.pending.has(id)) {
          this.pending.delete(id);
          clearTimeout(timer);
          reject(new Error("Could not contact Python bridge: " + error.message));
        }
      });
    });
  }

  close() {
    this.closed = true;
    this.rejectAll(new Error("Web UI is shutting down."));
    if (!this.child.killed) this.child.kill("SIGTERM");
  }
}

function requestOriginIsSafe(req, port) {
  const hostHeader = req.headers.host;
  if (typeof hostHeader !== "string") return false;
  let hostUrl;
  try {
    hostUrl = new URL("http://" + hostHeader);
  } catch {
    return false;
  }
  if (!HOSTS.has(hostUrl.hostname.toLowerCase()) || Number(hostUrl.port || 80) !== Number(port)) {
    return false;
  }
  const origin = req.headers.origin;
  if (!origin) return true;
  try {
    const originUrl = new URL(origin);
    return originUrl.protocol === "http:"
      && HOSTS.has(originUrl.hostname.toLowerCase())
      && originUrl.host.toLowerCase() === hostHeader.toLowerCase();
  } catch {
    return false;
  }
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    if (!(req.headers["content-type"] || "").toLowerCase().startsWith("application/json")) {
      reject(Object.assign(new Error("Content-Type must be application/json."), { statusCode: 415 }));
      req.resume();
      return;
    }
    let size = 0;
    let rejected = false;
    const chunks = [];
    req.on("data", (chunk) => {
      if (rejected) return;
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        rejected = true;
        reject(Object.assign(new Error("Request body exceeds the 1 MiB limit."), { statusCode: 413 }));
        req.resume();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      if (rejected) return;
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
      } catch {
        reject(Object.assign(new Error("Request body must be valid JSON."), { statusCode: 400 }));
      }
    });
    req.on("error", (error) => {
      if (!rejected) reject(error);
    });
  });
}

function setSecurityHeaders(res) {
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("X-Frame-Options", "DENY");
  res.setHeader("Referrer-Policy", "no-referrer");
  res.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  res.setHeader("Content-Security-Policy",
    "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'");
}

export function createWebServer(options = {}) {
  const workspace = path.resolve(options.workspace || process.env.NEEDLE_WORKSPACE || "./needle-workspace");
  const weights = options.weights ? path.resolve(options.weights) : (process.env.NEEDLE3_WEIGHTS || undefined);
  const engine = options.engine ? path.resolve(options.engine) : (process.env.NEEDLE3_LIB_PATH || undefined);
  const contextTokens = Number.isInteger(options.contextTokens) ? options.contextTokens : 1600;
  const token = randomBytes(32).toString("hex");
  const bridge = new PythonBridge({
    workspace, weights, engine, contextTokens,
    pythonExecutable: options.pythonExecutable,
  });

  const server = http.createServer(async (req, res) => {
    setSecurityHeaders(res);
    const currentPort = server.address()?.port;
    if (!requestOriginIsSafe(req, currentPort)) {
      sendJson(res, 403, { error: "Request host/origin is not permitted. Open the local URL printed in the terminal." });
      return;
    }

    let url;
    try {
      url = new URL(req.url || "/", "http://" + req.headers.host);
    } catch {
      sendJson(res, 400, { error: "Invalid request URL." });
      return;
    }
    if (url.pathname.startsWith("/api/")) {
      if (req.headers["x-needle-token"] !== token) {
        sendJson(res, 403, { error: "This browser session is not authorized. Reload the local page." });
        return;
      }
      try {
        if (req.method === "GET" && url.pathname === "/api/status") {
          sendJson(res, 200, await bridge.call("status", {}));
          return;
        }
        if (req.method === "GET" && url.pathname === "/api/settings") {
          sendJson(res, 200, await bridge.call("settings", {}));
          return;
        }
        if (req.method === "GET" && url.pathname === "/api/model-configuration") {
          sendJson(res, 200, await bridge.call("model_configuration", {}));
          return;
        }
        if (req.method === "GET" && url.pathname === "/api/upgrades") {
          sendJson(res, 200, await bridge.call("upgrades", {}));
          return;
        }
        if (req.method === "GET" && url.pathname === "/api/health") {
          sendJson(res, 200, await bridge.call("health", {}));
          return;
        }
        if (req.method === "POST" && [
          "/api/chat", "/api/action", "/api/models", "/api/settings",
          "/api/model-configuration", "/api/verify", "/api/live-test",
          "/api/upgrade-prompts", "/api/upgrade-prompts/archive"
        ].includes(url.pathname)) {
          const body = await readJsonBody(req);
          if (!body || typeof body !== "object" || Array.isArray(body)) {
            sendJson(res, 400, { error: "Request body must be a JSON object." });
            return;
          }
          let op;
          let payload;
          if (url.pathname === "/api/chat") {
            op = "chat";
            payload = body;
          } else if (url.pathname === "/api/action") {
            op = "resolve_action";
            payload = { id: body.id, approved: body.approved };
          } else if (url.pathname === "/api/models") {
            op = "detect_models";
            payload = body;
          } else if (url.pathname === "/api/settings") {
            op = "save_settings";
            payload = body;
          } else if (url.pathname === "/api/model-configuration") {
            op = "save_model_configuration";
            payload = body;
          } else if (url.pathname === "/api/verify") {
            op = "verify";
            payload = {};
          } else if (url.pathname === "/api/live-test") {
            op = "live_test";
            payload = {};
          } else if (url.pathname === "/api/upgrade-prompts") {
            op = "draft_upgrade";
            payload = body;
          } else if (url.pathname === "/api/upgrade-prompts/archive") {
            op = "archive_upgrade";
            payload = body;
          }
          sendJson(res, 200, await bridge.call(op, payload));
          return;
        }
        sendJson(res, 404, { error: "API route not found." });
      } catch (error) {
        const status = Number.isInteger(error?.statusCode) ? error.statusCode : getSafeErrorStatus(error);
        sendJson(res, status, { error: String(error?.message || "Request failed.") });
      }
      return;
    }

    if (req.method !== "GET" && req.method !== "HEAD") {
      sendJson(res, 405, { error: "Method not allowed." });
      return;
    }
    const staticFiles = new Map([
      ["/", ["index.html", "text/html; charset=utf-8"]],
      ["/app.js", ["app.js", "text/javascript; charset=utf-8"]],
      ["/styles.css", ["styles.css", "text/css; charset=utf-8"]],
    ]);
    const entry = staticFiles.get(url.pathname);
    if (!entry) {
      res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" });
      res.end("Not found");
      return;
    }
    try {
      const filePath = path.join(PUBLIC_DIR, entry[0]);
      const content = entry[0] === "index.html"
        ? readFileSync(filePath, "utf8").replace('content="__NEEDLE_BOOT_TOKEN__"', 'content="' + token + '"')
        : null;
      res.setHeader("Content-Type", entry[1]);
      res.setHeader("Cache-Control", "no-store");
      if (content !== null) {
        res.setHeader("Content-Length", Buffer.byteLength(content));
        res.end(req.method === "HEAD" ? undefined : content);
      } else {
        const fileStat = statSync(filePath);
        res.setHeader("Content-Length", fileStat.size);
        if (req.method === "HEAD") res.end();
        else createReadStream(filePath).pipe(res);
      }
    } catch {
      sendJson(res, 500, { error: "The web UI files could not be read. Check the project installation." });
    }
  });

  return {
    server,
    bridge,
    workspace,
    close: () => new Promise((resolve) => {
      bridge.close();
      if (!server.listening) resolve();
      else server.close(() => resolve());
    }),
  };
}

function parseArgs(argv) {
  const options = {};
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--workspace" && argv[i + 1]) options.workspace = argv[++i];
    else if (arg === "--weights" && argv[i + 1]) options.weights = argv[++i];
    else if (arg === "--engine" && argv[i + 1]) options.engine = argv[++i];
    else if (arg === "--port" && argv[i + 1]) options.port = Number(argv[++i]);
    else if (arg === "--python" && argv[i + 1]) options.pythonExecutable = argv[++i];
    else if (arg === "--context-tokens" && argv[i + 1]) options.contextTokens = Number(argv[++i]);
    else if (arg === "--help" || arg === "-h") options.help = true;
    else throw new Error("Unknown argument: " + arg);
  }
  return options;
}

async function main() {
  let options;
  try {
    options = parseArgs(process.argv.slice(2));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 2;
    return;
  }
  if (options.help) {
    console.log("Needle Agent Web UI\nUsage: node web-ui/server.mjs [--workspace PATH] [--weights FILE] [--engine FILE] [--port PORT] [--python EXECUTABLE] [--context-tokens N]");
    return;
  }
  const port = options.port ?? Number(process.env.PORT || 8765);
  if (!Number.isInteger(port) || port < 0 || port > 65535) {
    console.error("Port must be an integer from 0 to 65535.");
    process.exitCode = 2;
    return;
  }
  const app = createWebServer(options);
  const host = "127.0.0.1";
  app.server.on("error", (error) => {
    console.error("Could not start web UI: " + error.message);
    app.bridge.close();
    process.exitCode = 1;
  });
  app.server.listen(port, host, () => {
    const actualPort = app.server.address().port;
    console.log("Needle Agent Web UI: http://" + host + ":" + actualPort);
    console.log("Workspace: " + app.workspace);
    console.log("Bound to loopback only. Keep this terminal open while using the UI.");
  });
  const shutdown = async () => {
    await app.close();
    process.exit(0);
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error("Web UI startup failed: " + error.message);
    process.exitCode = 1;
  });
}
