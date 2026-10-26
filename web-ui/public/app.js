const csrfToken = document.querySelector('meta[name="needle-boot-token"]')?.content || "";
const $ = (selector) => document.querySelector(selector);

const elements = {
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
  settingsForm: $("#settings-form"),
  baseUrl: $("#base-url"),
  apiKey: $("#api-key"),
  keyHint: $("#key-hint"),
  model: $("#model-select"),
  rememberKey: $("#remember-key"),
  settingsStatus: $("#settings-status"),
  toastRegion: $("#toast-region"),
};

let busy = false;
let turnLocked = false;
let keyConfigured = false;

async function api(path, options = {}) {
  const headers = { "X-Needle-Token": csrfToken, ...(options.headers || {}) };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    ...options,
    headers,
    cache: "no-store",
    credentials: "same-origin",
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("The local web server returned an unreadable response.");
  }
  if (!response.ok) throw new Error(data.error || "Request failed with HTTP " + response.status + ".");
  return data;
}

function setConnection(online, label) {
  elements.connectionDot.classList.toggle("online", Boolean(online));
  elements.connectionDot.classList.toggle("offline", !online);
  elements.connectionLabel.textContent = label;
}

function showToast(message, type = "") {
  const toast = document.createElement("div");
  toast.className = "toast" + (type ? " " + type : "");
  toast.textContent = String(message);
  elements.toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 5200);
}

function setBusy(value, label = "Working…") {
  busy = value;
  elements.busy.hidden = !value;
  elements.busyLabel.textContent = label;
  elements.send.disabled = value || turnLocked;
  elements.prompt.disabled = value || turnLocked;
  elements.mode.disabled = value || turnLocked;
}

function scrollMessages() {
  elements.messages.scrollTop = elements.messages.scrollHeight;
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
  if (elements.welcome?.isConnected) elements.welcome.remove();
  const article = document.createElement("article");
  article.className = "message " + role;
  addMeta(article, role === "user" ? "YOU" : role === "system" ? "NEEDLE STATUS" : "NEEDLE AGENT", badgeText);
  const body = document.createElement("div");
  body.className = "message-body";
  body.textContent = String(text);
  article.append(body);
  elements.messages.append(article);
  scrollMessages();
  return article;
}

function addPlan(plan) {
  if (!plan) return;
  if (elements.welcome?.isConnected) elements.welcome.remove();
  const block = document.createElement("section");
  block.className = "plan-block";
  const title = document.createElement("strong");
  title.textContent = "CLOUD PLAN · ADVISORY ONLY";
  const body = document.createElement("p");
  body.textContent = String(plan);
  block.append(title, body);
  elements.messages.append(block);
  scrollMessages();
}

function addRejected(rejected) {
  for (const item of rejected || []) {
    addMessage("system", (item.proposal ? item.proposal + ": " : "") + (item.reason || "A proposal was rejected by local validation."), "NOT EXECUTED");
  }
}

function addActionCard(proposal) {
  if (elements.welcome?.isConnected) elements.welcome.remove();
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
  source.textContent = "Proposed by " + (proposal.source || "Needle");
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
  elements.messages.append(card);
  scrollMessages();
}

function addActionOutcome(data) {
  const title = data.approved ? (data.status === "succeeded" ? "Action executed" : "Action failed") : "Action denied";
  const details = [];
  if (data.status) details.push("Status: " + data.status);
  if (data.output !== undefined) details.push("Output:\n" + JSON.stringify(data.output, null, 2));
  if (data.verification) details.push("Local postcondition:\n" + JSON.stringify(data.verification, null, 2));
  if (data.error) details.push("Error: " + data.error);
  if (!data.approved) details.push("No action was executed.");
  addMessage("system", title + (details.length ? "\n\n" + details.join("\n\n") : ""), data.status === "succeeded" ? "RESULT RECORDED" : "DECISION RECORDED");
}

function addTurnResult(turnResult) {
  if (!turnResult || !turnResult.completed) return;
  if (turnResult.summary) addMessage("system", "Turn closed\n" + turnResult.summary, "LOCAL OUTCOME SUMMARY");
  if (turnResult.review) addMessage("assistant", turnResult.review, "CLOUD REVIEW · ADVISORY");
  turnLocked = false;
  elements.send.disabled = busy;
  elements.prompt.disabled = busy;
  elements.mode.disabled = busy;
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
    if (state) state.textContent = approved ? (result.status === "succeeded" ? "EXECUTED" : "FAILED") : "DENIED";
    denyButton.remove();
    approveButton.remove();
    addActionOutcome(result);
    if (result.turn_result) {
      addTurnResult(result.turn_result);
      showToast("All actions in this request have been resolved.");
    } else if (Number.isInteger(result.pending_actions)) {
      showToast(result.pending_actions + " action(s) still await a decision.");
    }
    if (approved && result.status !== "succeeded") showToast(result.error || "The action did not complete.", "error");
  } catch (error) {
    denyButton.disabled = false;
    approveButton.disabled = false;
    if (state) state.textContent = "AWAITING APPROVAL";
    showToast(error.message, "error");
  }
}

function setSettingsStatus(message, type = "") {
  elements.settingsStatus.textContent = String(message);
  elements.settingsStatus.classList.remove("success", "error");
  if (type) elements.settingsStatus.classList.add(type);
}

async function loadStatus() {
  try {
    const [status, settings] = await Promise.all([api("/api/status"), api("/api/settings")]);
    elements.workspace.textContent = status.workspace || "Workspace ready";
    keyConfigured = Boolean(settings.settings?.key_configured);
    elements.baseUrl.value = settings.settings?.base_url || "https://api.openai.com/v1";
    if (settings.settings?.model) {
      elements.model.replaceChildren(new Option(settings.settings.model, settings.settings.model));
      elements.model.value = settings.settings.model;
    }
    elements.keyHint.textContent = keyConfigured
      ? "A key is already available to this session. Leave this field blank to reuse it; the existing key will never be displayed."
      : "The key is kept in memory by default and is not shown again.";
    setConnection(true, status.pending_actions ? "Action approval pending" : "Local server connected");
    setSettingsStatus(settings.settings?.model
      ? "Saved model: " + settings.settings.model + ". Validate the endpoint to refresh its model list."
      : "No saved model yet. Validate the endpoint and select a model.", "");
  } catch (error) {
    setConnection(false, "Local bridge unavailable");
    elements.workspace.textContent = "Unavailable";
    showToast(error.message, "error");
  }
}

async function sendPrompt() {
  if (busy || turnLocked) return;
  const prompt = elements.prompt.value.trim();
  if (!prompt) return;
  addMessage("user", prompt);
  elements.prompt.value = "";
  setBusy(true, "Needle is preparing a response and validating proposals…");
  setConnection(true, "Processing local request");
  try {
    const result = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ prompt, mode: elements.mode.value }),
    });
    if (result.advisory_plan) addPlan(result.advisory_plan);
    if (result.assistant_message) addMessage("assistant", result.assistant_message);
    else if (!(result.proposals || []).length) addMessage("assistant", "No assistant text was returned. No action was executed unless an approved action card appears.");
    addRejected(result.rejected);
    for (const proposal of result.proposals || []) addActionCard(proposal);
    if (result.message) addMessage("system", result.message, "SAFETY STATUS");
    if ((result.proposals || []).length) {
      turnLocked = true;
      elements.send.disabled = true;
      elements.prompt.disabled = true;
      elements.mode.disabled = true;
      setConnection(true, "Awaiting explicit action approval");
    } else {
      addTurnResult(result.turn_result);
      setConnection(true, "Request completed");
    }
  } catch (error) {
    addMessage("system", error.message, "REQUEST FAILED · NO NEW ACTION WAS APPROVED");
    setConnection(false, "Request failed");
    showToast(error.message, "error");
  } finally {
    setBusy(false);
    if (turnLocked) {
      elements.send.disabled = true;
      elements.prompt.disabled = true;
      elements.mode.disabled = true;
    } else {
      elements.prompt.focus();
    }
  }
}

elements.composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  await sendPrompt();
});

elements.prompt.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    elements.composer.requestSubmit();
  }
});

$("#clear-visible").addEventListener("click", () => {
  if (turnLocked) {
    showToast("Resolve pending approval cards before clearing the transcript.", "error");
    return;
  }
  elements.messages.replaceChildren();
  addMessage("system", "Visible transcript cleared. Workspace files and the agent's saved context were not changed.", "CHAT DISPLAY ONLY");
});

function switchView(viewId) {
  const chatActive = viewId === "chat-view";
  $("#chat-view").classList.toggle("active-view", chatActive);
  $("#settings-view").classList.toggle("active-view", !chatActive);
  $("#nav-chat").classList.toggle("active", chatActive);
  $("#nav-settings").classList.toggle("active", !chatActive);
  elements.breadcrumb.textContent = chatActive ? "CHAT" : "CLOUD SETUP";
}
$("#nav-chat").addEventListener("click", () => switchView("chat-view"));
$("#nav-settings").addEventListener("click", () => {
  switchView("settings-view");
  loadSettings();
});

async function loadSettings() {
  try {
    const result = await api("/api/settings");
    const settings = result.settings || {};
    elements.baseUrl.value = settings.base_url || "https://api.openai.com/v1";
    keyConfigured = Boolean(settings.key_configured);
    elements.keyHint.textContent = keyConfigured
      ? "A key is already available to this session. Leave this field blank to reuse it; the existing key will never be displayed."
      : "The key is kept in memory by default and is not shown again.";
    if (settings.model && !Array.from(elements.model.options).some((option) => option.value === settings.model)) {
      elements.model.add(new Option(settings.model, settings.model));
    }
    if (settings.model) elements.model.value = settings.model;
  } catch (error) {
    setSettingsStatus(error.message, "error");
  }
}

$("#detect-models").addEventListener("click", async () => {
  $("#detect-models").disabled = true;
  setSettingsStatus("Validating the endpoint and loading models…");
  try {
    const result = await api("/api/models", {
      method: "POST",
      body: JSON.stringify({ base_url: elements.baseUrl.value.trim(), api_key: elements.apiKey.value }),
    });
    elements.model.replaceChildren(new Option("Select a model…", ""));
    for (const model of result.models || []) elements.model.add(new Option(model, model));
    if (result.models?.length) elements.model.value = result.models[0];
    if (elements.apiKey.value) {
      keyConfigured = true;
      elements.keyHint.textContent = "The key validated successfully and is kept in this local process. It is not shown again.";
    }
    setSettingsStatus("Endpoint validated. " + result.models.length + " model(s) found. Select a model and save settings.", "success");
    showToast("Endpoint validated and models loaded.");
  } catch (error) {
    setSettingsStatus(error.message, "error");
    showToast(error.message, "error");
  } finally {
    $("#detect-models").disabled = false;
  }
});

elements.settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#save-settings").disabled = true;
  setSettingsStatus("Saving cloud preferences…");
  try {
    const result = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({
        base_url: elements.baseUrl.value.trim(),
        model: elements.model.value,
        api_key: elements.apiKey.value,
        remember_key: elements.rememberKey.checked,
      }),
    });
    keyConfigured = Boolean(result.settings?.key_configured);
    elements.apiKey.value = "";
    elements.keyHint.textContent = keyConfigured
      ? "A key is available to this session. Leave the field blank to reuse it; it will never be displayed."
      : "Preferences saved. No API key is stored; set NEEDLE_API_KEY or enter a key before using cloud mode.";
    setSettingsStatus(result.message || "Cloud settings saved.", "success");
    setConnection(true, "Cloud settings saved");
    showToast("Cloud settings saved.");
  } catch (error) {
    setSettingsStatus(error.message, "error");
    showToast(error.message, "error");
  } finally {
    $("#save-settings").disabled = false;
  }
});

loadStatus();
