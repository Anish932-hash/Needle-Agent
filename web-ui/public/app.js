const csrfToken = document.querySelector('meta[name="needle-boot-token"]')?.content || "";
const $ = (selector) => document.querySelector(selector);

const ui = {
  messages: $("#messages"),
  welcome: $("#welcome-card"),
  composer: $("#composer-form"),
  prompt: $("#prompt-input"),
  send: $("#send-button"),
  busy: $("#busy-row"),
  busyLabel: $("#busy-label"),
  mode: $("#mode-select"),
  workspace: $("#workspace-name"),
  connectionDot: $("#connection-dot"),
  connectionLabel: $("#connection-label"),
  breadcrumb: $("#breadcrumb-current"),
  modelConfigForm: $("#model-config-form"),
  defaultMode: $("#default-mode-setting"),
  localWeights: $("#local-weights-path"),
  localEngine: $("#local-engine-path"),
  modelConfigStatus: $("#model-config-status"),
  runtimeLoaded: $("#runtime-loaded"),
  settingsForm: $("#settings-form"),
  baseUrl: $("#base-url"),
  apiKey: $("#api-key"),
  keyHint: $("#key-hint"),
  cloudModel: $("#model-select"),
  rememberKey: $("#remember-key"),
  settingsStatus: $("#settings-status"),
  verificationStatus: $("#verification-status"),
  liveTestStatus: $("#live-test-status"),
  verificationReport: $("#verification-report"),
  reportMeta: $("#report-meta"),
  upgradeForm: $("#upgrade-prompt-form"),
  upgradeRequest: $("#upgrade-request"),
  upgradeStatus: $("#upgrade-status"),
  upgradeList: $("#upgrade-list"),
  toastRegion: $("#toast-region"),
};

let busy = false;
let turnLocked = false;
let verificationBusy = false;
let liveTestBusy = false;
let upgradeBusy = false;

async function api(url, options = {}) {
  const headers = { "X-Needle-Token": csrfToken, ...(options.headers || {}) };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(url, {
    ...options,
    headers,
    cache: "no-store",
    credentials: "same-origin",
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("The local server returned an unreadable response.");
  }
  if (!response.ok) throw new Error(data.error || "Request failed with HTTP " + response.status + ".");
  return data;
}

function setConnection(online, label) {
  ui.connectionDot.classList.toggle("online", Boolean(online));
  ui.connectionDot.classList.toggle("offline", !online);
  ui.connectionLabel.textContent = label;
}

function showToast(message, type = "") {
  const toast = document.createElement("div");
  toast.className = "toast" + (type ? " " + type : "");
  toast.textContent = String(message);
  ui.toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 5200);
}

function setBusy(value, label = "Working…") {
  busy = value;
  ui.busy.hidden = !value;
  ui.busyLabel.textContent = label;
  ui.send.disabled = value || turnLocked;
  ui.prompt.disabled = value || turnLocked;
  ui.mode.disabled = value || turnLocked;
}

function scrollMessages() {
  ui.messages.scrollTop = ui.messages.scrollHeight;
}

function addMeta(container, text, badgeText) {
  const meta = document.createElement("div");
  meta.className = "message-meta";
  const label = document.createElement("span");
  label.textContent = text;
  meta.append(label);
  if (badgeText) {
    const badge = document.createElement("span");
    badge.className = "message-badge";
    badge.textContent = badgeText;
    meta.append(badge);
  }
  container.append(meta);
}

function addMessage(role, text, badgeText = "") {
  if (ui.welcome?.isConnected) ui.welcome.remove();
  const article = document.createElement("article");
  article.className = "message " + role;
  addMeta(article, role === "user" ? "YOU" : role === "system" ? "AGENT 911 STATUS" : "AGENT 911", badgeText);
  const body = document.createElement("div");
  body.className = "message-body";
  body.textContent = String(text);
  article.append(body);
  ui.messages.append(article);
  scrollMessages();
  return article;
}

function addPlan(plan) {
  if (!plan) return;
  if (ui.welcome?.isConnected) ui.welcome.remove();
  const block = document.createElement("section");
  block.className = "plan-block";
  const title = document.createElement("strong");
  title.textContent = "CLOUD PLAN · ADVISORY ONLY";
  const body = document.createElement("p");
  body.textContent = String(plan);
  block.append(title, body);
  ui.messages.append(block);
  scrollMessages();
}

function addRejected(rejected) {
  for (const item of rejected || []) {
    addMessage("system",
      (item.proposal ? item.proposal + ": " : "") + (item.reason || "A proposal was rejected by local validation."),
      "NOT EXECUTED");
  }
}

function addActionCard(proposal) {
  if (ui.welcome?.isConnected) ui.welcome.remove();
  const card = document.createElement("section");
  card.className = "action-card";
  card.dataset.actionId = proposal.id;

  const head = document.createElement("div");
  head.className = "action-card-head";
  const titleWrap = document.createElement("div");
  titleWrap.className = "action-title-row";
  const kicker = document.createElement("div");
  kicker.className = "action-kicker";
  kicker.textContent = "PROPOSED LOCAL ACTION";
  const title = document.createElement("div");
  title.className = "action-name";
  title.textContent = proposal.name;
  const source = document.createElement("div");
  source.className = "action-source";
  source.textContent = "Proposed by " + (proposal.source || "local model");
  titleWrap.append(kicker, title, source);
  const state = document.createElement("span");
  state.className = "action-state";
  state.textContent = "AWAITING APPROVAL";
  head.append(titleWrap, state);

  const args = document.createElement("pre");
  args.className = "action-json";
  args.textContent = JSON.stringify(proposal.arguments || {}, null, 2);
  card.append(head, args);

  if (proposal.corrected_to_match_request) {
    const corrected = document.createElement("p");
    corrected.className = "action-warning";
    corrected.textContent = "The local request guard adjusted the literal filename/content to match your current request. Review the displayed arguments before approving.";
    card.append(corrected);
  }

  const warning = document.createElement("p");
  warning.className = "action-warning";
  warning.textContent = proposal.cloud_data_notice
    ? "Approval runs this action in the selected workspace. Its result may be sent to the configured cloud provider for review."
    : "This action runs in the selected local workspace only. The workspace boundary and tool schema are checked again before execution.";
  card.append(warning);

  const controls = document.createElement("div");
  controls.className = "action-controls";
  const deny = document.createElement("button");
  deny.type = "button";
  deny.className = "deny-button";
  deny.textContent = "Deny";
  const approve = document.createElement("button");
  approve.type = "button";
  approve.className = "approve-button";
  approve.textContent = "Approve action";
  deny.addEventListener("click", () => resolveAction(proposal.id, false, card, deny, approve));
  approve.addEventListener("click", () => resolveAction(proposal.id, true, card, deny, approve));
  controls.append(deny, approve);
  card.append(controls);
  ui.messages.append(card);
  scrollMessages();
}

function addActionOutcome(data) {
  const title = data.approved
    ? (data.status === "succeeded" ? "Action executed" : "Action failed")
    : "Action denied";
  const details = [];
  if (data.status) details.push("Status: " + data.status);
  if (data.output !== undefined) details.push("Output:\n" + JSON.stringify(data.output, null, 2));
  if (data.verification) details.push("Local postcondition:\n" + JSON.stringify(data.verification, null, 2));
  if (data.error) details.push("Error: " + data.error);
  if (!data.approved) details.push("No action was executed.");
  addMessage("system", title + (details.length ? "\n\n" + details.join("\n\n") : ""),
    data.status === "succeeded" ? "RESULT RECORDED" : "DECISION RECORDED");
}

function addTurnResult(turnResult) {
  if (!turnResult || !turnResult.completed) return;
  if (turnResult.summary) {
    addMessage("system", "Turn closed\n" + turnResult.summary, "LOCAL OUTCOME SUMMARY");
  }
  if (turnResult.review) addMessage("assistant", turnResult.review, "CLOUD REVIEW · ADVISORY");
  turnLocked = false;
  ui.send.disabled = busy;
  ui.prompt.disabled = busy;
  ui.mode.disabled = busy;
}

async function resolveAction(id, approved, card, denyButton, approveButton) {
  denyButton.disabled = true;
  approveButton.disabled = true;
  const state = card.querySelector(".action-state");
  if (state) state.textContent = approved ? "APPROVING…" : "DENYING…";
  try {
    const result = await api("/api/action", {
      method: "POST",
      body: JSON.stringify({ id, approved }),
    });
    card.classList.add("resolved");
    if (state) state.textContent = approved
      ? (result.status === "succeeded" ? "EXECUTED" : "FAILED")
      : "DENIED";
    denyButton.remove();
    approveButton.remove();
    addActionOutcome(result);
    if (result.turn_result) {
      addTurnResult(result.turn_result);
      showToast("All actions in this request have been resolved.");
    } else if (Number.isInteger(result.pending_actions)) {
      showToast(result.pending_actions + " action(s) still await a decision.");
    }
    if (approved && result.status !== "succeeded") {
      showToast(result.error || "The action did not complete.", "error");
    }
  } catch (error) {
    denyButton.disabled = false;
    approveButton.disabled = false;
    if (state) state.textContent = "AWAITING APPROVAL";
    showToast(error.message, "error");
  }
}

async function sendPrompt() {
  if (busy || turnLocked) return;
  const prompt = ui.prompt.value.trim();
  if (!prompt) return;
  addMessage("user", prompt);
  ui.prompt.value = "";
  setBusy(true, "Preparing request and validating proposals…");
  setConnection(true, "Processing request");
  try {
    const result = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ prompt, mode: ui.mode.value }),
    });
    if (result.advisory_plan) addPlan(result.advisory_plan);
    if (result.assistant_message) addMessage("assistant", result.assistant_message);
    else if (!(result.proposals || []).length) {
      addMessage("assistant", "No assistant text was returned. No action was executed unless an approved action card appears.");
    }
    addRejected(result.rejected);
    for (const proposal of result.proposals || []) addActionCard(proposal);
    if (result.message) addMessage("system", result.message, "SAFETY STATUS");
    if ((result.proposals || []).length) {
      turnLocked = true;
      ui.send.disabled = true;
      ui.prompt.disabled = true;
      ui.mode.disabled = true;
      setConnection(true, "Awaiting explicit action approval");
    } else {
      addTurnResult(result.turn_result);
      setConnection(true, "Request completed");
    }
  } catch (error) {
    addMessage("system", error.message, "REQUEST FAILED · NO ACTION WAS APPROVED");
    setConnection(false, "Request failed");
    showToast(error.message, "error");
  } finally {
    setBusy(false);
    if (turnLocked) {
      ui.send.disabled = true;
      ui.prompt.disabled = true;
      ui.mode.disabled = true;
    } else {
      ui.prompt.focus();
    }
  }
}

ui.composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  await sendPrompt();
});

ui.prompt.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    ui.composer.requestSubmit();
  }
});

$("#clear-visible").addEventListener("click", () => {
  if (turnLocked) {
    showToast("Resolve pending approval cards before clearing the transcript.", "error");
    return;
  }
  ui.messages.replaceChildren();
  addMessage("system", "Visible transcript cleared. Workspace files and saved context were not changed.", "CHAT DISPLAY ONLY");
});

function switchView(viewId) {
  const views = ["chat-view", "models-view", "verification-view", "upgrades-view"];
  for (const id of views) $("#" + id).classList.toggle("active-view", id === viewId);
  const navMap = {
    "chat-view": "nav-chat",
    "models-view": "nav-models",
    "verification-view": "nav-verification",
    "upgrades-view": "nav-upgrades",
  };
  for (const navId of Object.values(navMap)) $("#" + navId).classList.remove("active");
  $("#" + navMap[viewId]).classList.add("active");
  const titles = {
    "chat-view": "CHAT",
    "models-view": "MODELS",
    "verification-view": "VERIFICATION",
    "upgrades-view": "UPGRADE PROMPTS",
  };
  ui.breadcrumb.textContent = titles[viewId] || "AGENT 911";
}

$("#nav-chat").addEventListener("click", () => switchView("chat-view"));
$("#nav-models").addEventListener("click", async () => {
  switchView("models-view");
  await loadModelConfiguration();
});
$("#nav-verification").addEventListener("click", () => switchView("verification-view"));
$("#nav-upgrades").addEventListener("click", async () => {
  switchView("upgrades-view");
  await loadUpgradePrompts();
});

function setStatusBox(element, message, type = "") {
  element.textContent = String(message);
  element.classList.remove("success", "error");
  if (type) element.classList.add(type);
}

function renderChecks(report, title) {
  ui.verificationReport.replaceChildren();
  ui.reportMeta.textContent = report?.started_at
    ? new Date(report.started_at).toLocaleString()
    : "Completed report";
  const summary = document.createElement("div");
  summary.className = "report-summary " + (report?.passed ? "passed" : "failed");
  const headline = document.createElement("strong");
  headline.textContent = title + ": " + (report?.passed ? "PASSED" : "FAILED");
  const meta = document.createElement("span");
  const checks = Array.isArray(report?.checks) ? report.checks : [];
  meta.textContent = checks.filter((item) => item.passed).length + " / " + checks.length + " checks passed";
  summary.append(headline, meta);
  ui.verificationReport.append(summary);
  for (const check of checks) {
    const row = document.createElement("article");
    row.className = "check-row";
    const badge = document.createElement("span");
    badge.className = "check-badge " + (check.passed ? "passed" : "failed");
    badge.textContent = check.passed ? "PASS" : "FAIL";
    const body = document.createElement("div");
    body.className = "check-body";
    const name = document.createElement("strong");
    name.textContent = String(check.name || "Check");
    const detail = document.createElement("p");
    detail.textContent = String(check.detail || "");
    body.append(name, detail);
    row.append(badge, body);
    ui.verificationReport.append(row);
  }
  if (report?.note) {
    const note = document.createElement("p");
    note.className = "report-note";
    note.textContent = report.note;
    ui.verificationReport.append(note);
  }
  if (report?.duration_seconds !== undefined) {
    const duration = document.createElement("p");
    duration.className = "report-note";
    duration.textContent = "Duration: " + report.duration_seconds + " seconds.";
    ui.verificationReport.append(duration);
  }
  scrollMessages();
}

$("#run-verification").addEventListener("click", async () => {
  if (verificationBusy || liveTestBusy) return;
  verificationBusy = true;
  const button = $("#run-verification");
  button.disabled = true;
  setStatusBox(ui.verificationStatus, "Running isolated deterministic checks…");
  setConnection(true, "Verification running");
  try {
    const response = await api("/api/verify", {
      method: "POST",
      body: JSON.stringify({}),
    });
    const report = response.report || {};
    const passed = (report.checks || []).filter((item) => item.passed).length;
    setStatusBox(ui.verificationStatus,
      (report.passed ? "PASSED" : "FAILED") + " · " + passed + "/" + (report.checks || []).length + " checks · temporary workspace removed",
      report.passed ? "success" : "error");
    renderChecks(report, "Deterministic verification");
    setConnection(true, report.passed ? "Verification passed" : "Verification needs attention");
  } catch (error) {
    setStatusBox(ui.verificationStatus, error.message, "error");
    showToast(error.message, "error");
    setConnection(false, "Verification failed");
  } finally {
    verificationBusy = false;
    button.disabled = false;
  }
});

$("#run-live-test").addEventListener("click", async () => {
  if (liveTestBusy || verificationBusy) return;
  liveTestBusy = true;
  const button = $("#run-live-test");
  button.disabled = true;
  setStatusBox(ui.liveTestStatus, "Initializing the configured local runtime and running a real inference call…");
  setConnection(true, "Live model test running");
  try {
    const response = await api("/api/live-test", {
      method: "POST",
      body: JSON.stringify({}),
    });
    const report = response.report || {};
    setStatusBox(ui.liveTestStatus,
      (report.passed ? "PASSED" : "FAILED") + " · " + (report.duration_seconds ?? "—") + " seconds · model proposals executed: 0",
      report.passed ? "success" : "error");
    renderChecks(report, "Live run test");
    setConnection(true, report.passed ? "Live runtime test passed" : "Live runtime test failed");
  } catch (error) {
    setStatusBox(ui.liveTestStatus, error.message, "error");
    showToast(error.message, "error");
    setConnection(false, "Live runtime test failed");
  } finally {
    liveTestBusy = false;
    button.disabled = false;
  }
});

async function loadModelConfiguration() {
  try {
    const [configResponse, settingsResponse] = await Promise.all([
      api("/api/model-configuration"),
      api("/api/settings"),
    ]);
    const config = configResponse.model_configuration || {};
    ui.defaultMode.value = config.default_mode || "local";
    ui.mode.value = config.default_mode || "local";
    ui.localWeights.value = config.local?.weights_path || "";
    ui.localEngine.value = config.local?.engine_path || "";
    ui.runtimeLoaded.textContent = config.runtime_initialized ? "Initialized" : "Not initialized";
    setStatusBox(ui.modelConfigStatus,
      "Loaded Agent 911 runtime configuration. Saving changed paths will rebuild the runtime on the next model operation.",
      "success");

    const settings = settingsResponse.settings || {};
    ui.baseUrl.value = settings.base_url || "https://api.openai.com/v1";
    if (settings.model) {
      ui.cloudModel.replaceChildren(new Option(settings.model, settings.model));
      ui.cloudModel.value = settings.model;
    }
    ui.rememberKey.checked = Boolean(settings.key_saved_on_device);
    ui.keyHint.textContent = settings.key_configured
      ? "A key is available to this session. Leave this field blank to reuse it; the key is never returned to the browser."
      : "The key is kept in memory by default and is not shown again.";
    setStatusBox(ui.settingsStatus, settings.model
      ? "Saved cloud model: " + settings.model + ". Validate again to refresh the model list."
      : "No cloud model saved. Validate the endpoint and select a model.");
  } catch (error) {
    setStatusBox(ui.modelConfigStatus, error.message, "error");
    showToast(error.message, "error");
  }
}

ui.modelConfigForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#save-model-config").disabled = true;
  setStatusBox(ui.modelConfigStatus, "Validating and saving runtime configuration…");
  try {
    const result = await api("/api/model-configuration", {
      method: "POST",
      body: JSON.stringify({
        default_mode: ui.defaultMode.value,
        local: {
          weights_path: ui.localWeights.value.trim(),
          engine_path: ui.localEngine.value.trim(),
        },
      }),
    });
    const config = result.model_configuration || {};
    ui.mode.value = config.default_mode || "local";
    ui.runtimeLoaded.textContent = config.runtime_initialized ? "Initialized" : "Not initialized · will load on next request";
    setStatusBox(ui.modelConfigStatus, result.message || "Model configuration saved.", "success");
    showToast("Model configuration saved.");
  } catch (error) {
    setStatusBox(ui.modelConfigStatus, error.message, "error");
    showToast(error.message, "error");
  } finally {
    $("#save-model-config").disabled = false;
  }
});

$("#detect-models").addEventListener("click", async () => {
  $("#detect-models").disabled = true;
  setStatusBox(ui.settingsStatus, "Validating the endpoint and loading models…");
  try {
    const result = await api("/api/models", {
      method: "POST",
      body: JSON.stringify({
        base_url: ui.baseUrl.value.trim(),
        api_key: ui.apiKey.value,
      }),
    });
    ui.cloudModel.replaceChildren(new Option("Select a model…", ""));
    for (const model of result.models || []) ui.cloudModel.add(new Option(model, model));
    if (result.models?.length) ui.cloudModel.value = result.models[0];
    setStatusBox(ui.settingsStatus,
      "Endpoint validated. " + result.models.length + " model(s) found. Select a model and save.",
      "success");
    showToast("Endpoint validated and models loaded.");
  } catch (error) {
    setStatusBox(ui.settingsStatus, error.message, "error");
    showToast(error.message, "error");
  } finally {
    $("#detect-models").disabled = false;
  }
});

ui.settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#save-settings").disabled = true;
  setStatusBox(ui.settingsStatus, "Saving provider preferences…");
  try {
    const result = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({
        base_url: ui.baseUrl.value.trim(),
        model: ui.cloudModel.value,
        api_key: ui.apiKey.value,
        remember_key: ui.rememberKey.checked,
      }),
    });
    const settings = result.settings || {};
    ui.rememberKey.checked = Boolean(settings.key_saved_on_device);
    ui.apiKey.value = "";
    ui.keyHint.textContent = settings.key_configured
      ? "A key is available to this session. Leave the field blank to reuse it; it will never be displayed."
      : "Preferences saved. No API key is stored; use NEEDLE_API_KEY or enter a key before using cloud mode.";
    setStatusBox(ui.settingsStatus, result.message || "Cloud settings saved.", "success");
    setConnection(true, "Cloud provider settings saved");
    showToast("Cloud provider settings saved.");
  } catch (error) {
    setStatusBox(ui.settingsStatus, error.message, "error");
    showToast(error.message, "error");
  } finally {
    $("#save-settings").disabled = false;
  }
});

function renderUpgradeItems(items) {
  ui.upgradeList.replaceChildren();
  const list = Array.isArray(items) ? items : [];
  if (!list.length) {
    const empty = document.createElement("div");
    empty.className = "report-empty";
    empty.textContent = "No upgrade prompts saved yet.";
    ui.upgradeList.append(empty);
    return;
  }
  for (const item of list) {
    const article = document.createElement("article");
    article.className = "upgrade-item";
    const header = document.createElement("div");
    header.className = "upgrade-item-header";
    const state = document.createElement("span");
    state.className = "upgrade-state " + String(item.status || "draft");
    state.textContent = String(item.status || "draft").toUpperCase();
    const date = document.createElement("time");
    date.textContent = item.created_at ? new Date(item.created_at).toLocaleString() : "Saved locally";
    header.append(state, date);
    const request = document.createElement("h3");
    request.textContent = item.request || "Untitled upgrade request";
    article.append(header, request);
    if (item.plan) {
      const planTitle = document.createElement("div");
      planTitle.className = "upgrade-plan-title";
      planTitle.textContent = "DRAFT IMPLEMENTATION PLAN";
      const plan = document.createElement("pre");
      plan.className = "upgrade-plan";
      plan.textContent = item.plan;
      article.append(planTitle, plan);
    } else {
      const noPlan = document.createElement("p");
      noPlan.className = "message-note";
      noPlan.textContent = "No plan was generated. The request is still saved for later review.";
      article.append(noPlan);
    }
    if (item.status !== "archived") {
      const controls = document.createElement("div");
      controls.className = "upgrade-actions";
      const archive = document.createElement("button");
      archive.type = "button";
      archive.className = "text-button";
      archive.textContent = "Archive prompt";
      archive.addEventListener("click", async () => {
        archive.disabled = true;
        try {
          const response = await api("/api/upgrade-prompts/archive", {
            method: "POST",
            body: JSON.stringify({ id: item.id }),
          });
          renderUpgradeItems(response.items);
          showToast("Upgrade prompt archived.");
        } catch (error) {
          archive.disabled = false;
          showToast(error.message, "error");
        }
      });
      controls.append(archive);
      article.append(controls);
    }
    ui.upgradeList.append(article);
  }
}

async function loadUpgradePrompts() {
  try {
    const result = await api("/api/upgrades");
    renderUpgradeItems(result.items);
  } catch (error) {
    setStatusBox(ui.upgradeStatus, error.message, "error");
    ui.upgradeList.replaceChildren();
    const message = document.createElement("div");
    message.className = "report-empty";
    message.textContent = error.message;
    ui.upgradeList.append(message);
  }
}

ui.upgradeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (upgradeBusy) return;
  const request = ui.upgradeRequest.value.trim();
  if (!request) return;
  upgradeBusy = true;
  $("#draft-upgrade").disabled = true;
  setStatusBox(ui.upgradeStatus, "Checking cloud planner configuration and drafting an upgrade plan…");
  setConnection(true, "Drafting upgrade plan");
  try {
    const result = await api("/api/upgrade-prompts", {
      method: "POST",
      body: JSON.stringify({ request }),
    });
    ui.upgradeRequest.value = "";
    setStatusBox(ui.upgradeStatus, result.message || "Upgrade prompt saved.", "success");
    renderUpgradeItems(result.items);
    if (result.item?.plan) {
      showToast("Upgrade plan drafted and saved. The configured provider received the request.");
    } else {
      showToast(result.message || "Upgrade prompt saved as a draft; no plan was generated.", "error");
    }
    setConnection(true, "Upgrade prompt saved");
  } catch (error) {
    setStatusBox(ui.upgradeStatus, error.message, "error");
    showToast(error.message, "error");
    setConnection(false, "Upgrade plan failed");
  } finally {
    upgradeBusy = false;
    $("#draft-upgrade").disabled = false;
  }
});

async function loadStatus() {
  try {
    const [status, modelConfig] = await Promise.all([
      api("/api/status"),
      api("/api/model-configuration"),
    ]);
    ui.workspace.textContent = status.workspace || "Workspace ready";
    const config = modelConfig.model_configuration || {};
    ui.mode.value = config.default_mode || "local";
    ui.runtimeLoaded.textContent = config.runtime_initialized ? "Initialized" : "Not initialized";
    setConnection(true, status.pending_actions ? "Action approval pending" : "Local server connected");
    await loadModelConfiguration();
    await loadUpgradePrompts();
  } catch (error) {
    setConnection(false, "Local bridge unavailable");
    ui.workspace.textContent = "Unavailable";
    showToast(error.message, "error");
  }
}

loadStatus();
