import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { after, test } from "node:test";
import { createWebServer } from "./server.mjs";

const runningApps = [];
after(async () => {
  await Promise.all(runningApps.map((app) => app.close()));
});

test("serves the UI locally, protects its API, and validates requests", async () => {
  const workspace = await mkdtemp(path.join(os.tmpdir(), "needle-web-ui-test-"));
  const app = createWebServer({ workspace });
  runningApps.push(app);
  await new Promise((resolve, reject) => {
    app.server.once("error", reject);
    app.server.listen(0, "127.0.0.1", resolve);
  });
  const address = app.server.address();
  const base = "http://127.0.0.1:" + address.port;

  try {
    const pageResponse = await fetch(base + "/");
    assert.equal(pageResponse.status, 200);
    assert.match(pageResponse.headers.get("content-security-policy") || "", /default-src 'self'/);
    assert.equal(pageResponse.headers.get("x-frame-options"), "DENY");
    const html = await pageResponse.text();
    const match = html.match(/<meta name="needle-boot-token" content="([a-f0-9]{64})">/);
    assert.ok(match, "server should insert a per-instance browser token");
    const token = match[1];

    const scriptResponse = await fetch(base + "/app.js");
    assert.equal(scriptResponse.status, 200);
    assert.match(scriptResponse.headers.get("content-type") || "", /javascript/);
    assert.match(await scriptResponse.text(), /\/api\/chat/);

    const cssResponse = await fetch(base + "/styles.css");
    assert.equal(cssResponse.status, 200);
    assert.match(cssResponse.headers.get("content-type") || "", /text\/css/);
    assert.match(await cssResponse.text(), /--accent/);

    const noToken = await fetch(base + "/api/status");
    assert.equal(noToken.status, 403);

    const badOrigin = await fetch(base + "/api/status", {
      headers: { "X-Needle-Token": token, Origin: "http://not-local.example" },
    });
    assert.equal(badOrigin.status, 403);

    const statusResponse = await fetch(base + "/api/status", {
      headers: { "X-Needle-Token": token },
    });
    assert.equal(statusResponse.status, 200);
    const status = await statusResponse.json();
    assert.equal(status.workspace, workspace);
    assert.equal(status.pending_actions, 0);

    const invalidChat = await fetch(base + "/api/chat", {
      method: "POST",
      headers: { "X-Needle-Token": token, "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: "", mode: "local" }),
    });
    assert.equal(invalidChat.status, 400);
    assert.match((await invalidChat.json()).error, /Enter a request/);

    const wrongContentType = await fetch(base + "/api/chat", {
      method: "POST",
      headers: { "X-Needle-Token": token, "Content-Type": "text/plain" },
      body: "{}",
    });
    assert.equal(wrongContentType.status, 415);

    const notFound = await fetch(base + "/not-a-route");
    assert.equal(notFound.status, 404);
  } finally {
    await app.close();
    await rm(workspace, { recursive: true, force: true });
  }
});
