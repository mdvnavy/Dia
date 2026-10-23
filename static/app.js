const questionnaire = document.querySelector("#questionnaire");
const loadSample = document.querySelector("#loadSample");
const runAgent = document.querySelector("#runAgent");
// Run status line (landing polish): #status is the polite live region; #statusState holds the state word and
// #statusText the optional detail that every message writes to (so `statusText.textContent = ...` keeps working).
const statusLine = document.querySelector("#status");
const statusState = document.querySelector("#statusState");
const statusText = document.querySelector("#statusText");
const RUN_STATES = { ready: "Ready", processing: "Processing", complete: "Complete", error: "Error" };
function setRunState(state, detail = "") {
  statusLine.dataset.state = state;
  statusState.textContent = RUN_STATES[state];
  statusText.textContent = detail;
}
const tier = document.querySelector("#tier");
const score = document.querySelector("#score");
const issues = document.querySelector("#issues");
const output = document.querySelector("#documentOutput");
const tabs = [...document.querySelectorAll(".tab")];
const agentMessage = document.querySelector("#agentMessage");
const runChat = document.querySelector("#runChat");
const agentOutput = document.querySelector("#agentOutput");
const agentStatus = document.querySelector("#agentStatus");
const copyAll = document.querySelector("#copyAll");
const downloadMarkdown = document.querySelector("#downloadMarkdown");
const downloadJson = document.querySelector("#downloadJson");
const downloadPdf = document.querySelector("#downloadPdf");
const copySection = document.querySelector("#copySection");

let documents = {};
let latestPayload = null;
let activeDoc = "client-profile.md";
// The questionnaire text that produced latestPayload, so agent context always
// matches the run shown in the tier card even if the textarea is edited later.
let lastRunQuestionnaire = "";

const themeToggle = document.querySelector("#themeToggle");

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const isLight = theme === "light";
  themeToggle.setAttribute("aria-pressed", String(isLight));
  themeToggle.setAttribute(
    "aria-label",
    isLight ? "Switch to dark mode" : "Switch to light mode"
  );
}

themeToggle.addEventListener("click", () => {
  const next =
    document.documentElement.dataset.theme === "light" ? "dark" : "light";
  applyTheme(next);
  try {
    localStorage.setItem("dia-theme", next);
  } catch (error) {
    // Private browsing can block storage; the toggle still works for the session.
  }
});

applyTheme(document.documentElement.dataset.theme || "dark");

// Header tooltip: hover/focus shows the full app name via CSS; click/tap
// toggles it for touch devices, where hover does not exist.
const appTip = document.querySelector(".app-tip");
const appInfo = document.querySelector("#appInfo");

appInfo.addEventListener("click", (event) => {
  event.stopPropagation();
  const open = appTip.classList.toggle("open");
  appInfo.setAttribute("aria-expanded", String(open));
});

document.addEventListener("click", (event) => {
  if (appTip.classList.contains("open") && !appTip.contains(event.target)) {
    appTip.classList.remove("open");
    appInfo.setAttribute("aria-expanded", "false");
  }
});

function setBusy(button, busy) {
  button.disabled = busy;
  button.classList.toggle("busy", busy);
}

// Intake collapse: after a successful run the questionnaire folds away so the
// post-intake workflow (Ask DIA, results, draft editor) takes the stage. The
// Run button and status stay visible for quick re-runs.
const intakeBody = document.querySelector("#intakeBody");
const intakeToggle = document.querySelector("#intakeToggle");
const skipLink = document.querySelector("#skipLink");

function setIntakeCollapsed(collapsed) {
  intakeBody.hidden = collapsed;
  intakeToggle.hidden = false;
  intakeToggle.setAttribute("aria-expanded", String(!collapsed));
  intakeToggle.textContent = collapsed ? "Edit intake" : "Collapse intake";
  // Keep the skip link pointing at something visible: the hidden textarea
  // cannot receive focus while collapsed.
  skipLink.href = collapsed ? "#intakeToggle" : "#questionnaire";
  skipLink.textContent = collapsed ? "Skip to intake controls" : "Skip to questionnaire";
  if (!collapsed) autosizeComposer(questionnaire);
}

intakeToggle.addEventListener("click", () => {
  setIntakeCollapsed(!intakeBody.hidden);
  if (!intakeBody.hidden) {
    questionnaire.focus();
  }
});

loadSample.addEventListener("click", async () => {
  statusText.textContent = "Loading sample...";
  const response = await fetch("/api/sample");
  const payload = await response.json();
  questionnaire.value = payload.questionnaire;
  if (intakeBody.hidden) setIntakeCollapsed(false);
  autosizeComposer(questionnaire);
  setRunState("ready", "Sample loaded");
  InputsPrompt.schedule();
});

runAgent.addEventListener("click", async () => {
  InputsPrompt.endSession();  // hides on Run and does not come back for this intake (addendum §3.2)
  setRunState("processing");
  setBusy(runAgent, true);
  setExportAvailability(false);
  try {
    const response = await fetch("/api/process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ questionnaire: questionnaire.value }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Processing failed");
    }
    const previousId = rerunOf;
    showResults(payload, questionnaire.value);
    setRunState("complete", "Intake collapsed, ready to work");
    saveToCabinet(payload, questionnaire.value);
    if (previousId) renderBeforeAfter(payload, previousId);
  } catch (error) {
    setRunState("error", error.message);
    setExportAvailability(latestPayload !== null);
  } finally {
    setBusy(runAgent, false);
  }
});

function selectTab(tab, { focus = false } = {}) {
  activeDoc = tab.dataset.doc;
  output.setAttribute("aria-labelledby", tab.id);
  tabs.forEach((item) => {
    const isActive = item === tab;
    item.classList.toggle("active", isActive);
    item.setAttribute("aria-selected", String(isActive));
    item.tabIndex = isActive ? 0 : -1;
  });
  if (focus) {
    tab.focus();
  }
  renderDocument(activeDoc);
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => selectTab(tab));
});

document.querySelector(".tabs").addEventListener("keydown", (event) => {
  const current = tabs.indexOf(document.activeElement);
  if (current === -1) {
    return;
  }
  let next = null;
  if (event.key === "ArrowRight") {
    next = (current + 1) % tabs.length;
  } else if (event.key === "ArrowLeft") {
    next = (current - 1 + tabs.length) % tabs.length;
  } else if (event.key === "Home") {
    next = 0;
  } else if (event.key === "End") {
    next = tabs.length - 1;
  }
  if (next !== null) {
    event.preventDefault();
    selectTab(tabs[next], { focus: true });
  }
});

const ASK_DIA_UNAVAILABLE = "Ask DIA is temporarily unavailable.";

// Kill switch (ASK_DIA_ENABLED unset on the server): keep the section in
// place but make the composer inert and say so calmly.
function disableAskDia() {
  agentStatus.textContent = "temporarily unavailable";
  agentStatus.classList.remove("ok");
  agentMessage.disabled = true;
  agentMessage.placeholder = ASK_DIA_UNAVAILABLE;
  // The narrow/wide placeholder swap (landing polish) must not bring back "Ask DIA a question…" on a resize.
  agentMessage.dataset.placeholderShort = ASK_DIA_UNAVAILABLE;
  agentMessage.dataset.placeholderLong = ASK_DIA_UNAVAILABLE;
  runChat.disabled = true;
  runChat.hidden = true;
  const stage = document.querySelector("#agentStage");
  if (stage) {
    stage.dataset.disabled = "true";
    stage.querySelectorAll("#agentComposer button").forEach((button) => {
      button.disabled = true;
    });
  }
  agentOutput.textContent = ASK_DIA_UNAVAILABLE;
}

async function refreshAgentStatus() {
  try {
    const response = await fetch("/api/agent/status");
    const payload = await response.json();
    geminiConnected = Boolean(payload.configured) && payload.enabled !== false;
    if (payload.enabled === false) {
      disableAskDia();
      return;
    }
    if (payload.configured) {
      agentStatus.textContent = "Gemini connected";
      agentStatus.classList.add("ok");
    } else {
      agentStatus.textContent = "API key not set";
      agentStatus.classList.remove("ok");
    }
  } catch (error) {
    agentStatus.textContent = "status unavailable";
  }
  refreshFollowupButton();
}

// --- Assistant mode (persona-ux.md v1.2 §3): Guided / Expert / Off, per device ---
// Guided: suggestion buttons and Ask DIA. Expert: no suggestions; Ask DIA only from the DIA menu and the palette.
// Off: no Ask DIA and no Gemini call at all. The <head> script applies the saved value before first paint.
const ASSISTANT_KEY = "dia-assistant";
const ASSISTANT_MODES = ["guided", "expert", "off"];
let assistantMode = ASSISTANT_MODES.includes(document.documentElement.dataset.assistant) ? document.documentElement.dataset.assistant : "guided";
let geminiConnected = false;
const agentAiLabel = document.querySelector("#agentAiLabel");
const agentCancel = document.querySelector("#agentCancel");
const copyQuestions = document.querySelector("#copyQuestions");
const followupRow = document.querySelector("#followupRow");
const draftFollowups = document.querySelector("#draftFollowups");
const assistantOptions = [...document.querySelectorAll(".assistant-option")];
let agentController = null;
// Off mode also turns off voice input (speech recognition); Listen and Auto-speak are local speechSynthesis and stay on.
const VOICE_OFF_NOTE = "Voice input is off in Off mode.";
let voiceReady = false; // set once the mics and voice fans exist

// Same pattern as an unsupported browser: aria-disabled (still focusable), the note via aria-describedby and the tooltip.
// If the browser has no speech recognition, the mics are already disabled with their own note, so they're left alone.
function applyVoiceOff() {
  if (!voiceReady || !SpeechRecognitionImpl) return;
  const off = assistantMode === "off";
  [intakeMic, micButton].forEach((button) => {
    if (!button || (button.dataset.offMode === "true") === off) return;
    const wrap = button.closest(".composer");
    const note = wrap?.querySelector("[id$='MicNote']");
    const tip = wrap?.querySelector(".cmp-tip-mic");
    if (off) {
      if (button.getAttribute("aria-pressed") === "true") cancelRecognition();
      button.dataset.offMode = "true";
      button.dataset.describedby = button.getAttribute("aria-describedby") || "";
      button.setAttribute("aria-disabled", "true");
      if (note) { note.dataset.text = note.textContent; note.textContent = VOICE_OFF_NOTE; button.setAttribute("aria-describedby", note.id); }
      if (tip) { tip.dataset.html = tip.innerHTML; tip.textContent = VOICE_OFF_NOTE; }
    } else {
      delete button.dataset.offMode;
      button.removeAttribute("aria-disabled");
      if (button.dataset.describedby) button.setAttribute("aria-describedby", button.dataset.describedby);
      else button.removeAttribute("aria-describedby");
      if (note && note.dataset.text) note.textContent = note.dataset.text;
      if (tip && tip.dataset.html) tip.innerHTML = tip.dataset.html;
    }
  });
  document.querySelectorAll(".voice-item[id$='FanConversation']").forEach((item) => {
    if ((item.dataset.off === "true") === off) return;
    if (off) {
      item.dataset.off = "true";
      item.dataset.label = item.getAttribute("aria-label");
      item.setAttribute("aria-disabled", "true");
      item.setAttribute("aria-label", `${item.dataset.label} (${VOICE_OFF_NOTE.toLowerCase().replace(/\.$/, "")})`);
      const note = document.createElement("span");
      note.className = "voice-note voice-off-note";
      note.textContent = VOICE_OFF_NOTE;
      item.querySelector(".voice-label").append(note);
    } else {
      delete item.dataset.off;
      item.removeAttribute("aria-disabled");
      item.setAttribute("aria-label", item.dataset.label);
      item.querySelector(".voice-off-note")?.remove();
    }
  });
}

function applyAssistantMode() {
  document.documentElement.dataset.assistant = assistantMode;
  assistantOptions.forEach((option) => {
    const on = option.dataset.assistantMode === assistantMode;
    option.setAttribute("aria-checked", String(on));
    option.tabIndex = on ? 0 : -1;
  });
  if (assistantMode === "off") {
    agentController?.abort();
    if (document.body.dataset.agent === "open") delete document.body.dataset.agent;
  }
  applyVoiceOff();
  refreshFollowupButton();
}

function setAssistantMode(mode) {
  if (!ASSISTANT_MODES.includes(mode)) return;
  assistantMode = mode;
  try { localStorage.setItem(ASSISTANT_KEY, mode); } catch (error) { /* session only */ }
  applyAssistantMode(); // aria-checked announces the change
  // Off also ends a live Conversation mode and cancels its pending Ask DIA request, so a late reply is never
  // spoken and never restarts the mic (Codex review on PR #38). Only reachable after load, so the voice state exists.
  if (mode === "off") {
    chatController?.abort();
    if (conversationActive) stopConversation("Conversation ended: Assistant is Off");
  }
}

assistantOptions.forEach((option, index) => {
  option.addEventListener("click", () => setAssistantMode(option.dataset.assistantMode));
  option.addEventListener("keydown", (event) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (!step) return;
    event.preventDefault();
    const next = assistantOptions[(index + step + assistantOptions.length) % assistantOptions.length];
    setAssistantMode(next.dataset.assistantMode);
    next.focus();
  });
});

// One centred button under Validation Issues: Guided only, Gemini connected, and only when there are issues.
function refreshFollowupButton() {
  if (!followupRow) return;
  followupRow.hidden = !(assistantMode === "guided" && geminiConnected && latestPayload?.issues?.length);
}

function showAiReply(text, { questions = false } = {}) {
  agentOutput.textContent = text;
  agentAiLabel.hidden = false;
  copyQuestions.hidden = !questions;
}

// On click only: one /api/agent call, `Drafting…` with Cancel (AbortController), result in Ask DIA.
async function draftFollowUpQuestions() {
  if (assistantMode === "off" || !latestPayload?.issues?.length) return;
  openAgent();
  agentController?.abort();
  agentController = new AbortController();
  agentAiLabel.hidden = true;
  copyQuestions.hidden = true;
  agentCancel.hidden = false;
  agentOutput.textContent = "Drafting…";
  draftFollowups.disabled = true;
  const prompt =
    "Draft follow-up questions for the client, one per validation issue below. " +
    "Return only a short numbered list of client-ready questions, with no preamble. " +
    "Make no promises about price, dates or outcomes." + buildAgentContext();
  try {
    const response = await fetch("/api/agent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: prompt }),
      signal: agentController.signal,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Agent run failed");
    showAiReply((payload.reply || "").trim() || "(empty response)", { questions: Boolean(payload.reply) });
  } catch (error) {
    agentOutput.textContent = error.name === "AbortError" ? "Drafting cancelled" : error.message;
  } finally {
    agentCancel.hidden = true;
    draftFollowups.disabled = false;
    agentController = null;
  }
}

draftFollowups.addEventListener("click", draftFollowUpQuestions);
agentCancel.addEventListener("click", () => agentController?.abort());
copyQuestions.addEventListener("click", () => copyText(agentOutput.textContent, "Questions copied", document.querySelector("#aiStatus")));

// Ground the agent in the deterministic run so its answers (scores, missing
// fields, tier) agree with the tier card instead of re-scoring a lossy
// re-parse of whichever generated document happens to be on screen.
function buildAgentContext() {
  const sections = [];
  if (latestPayload) {
    const scoreInfo = latestPayload.score;
    const issueLines = latestPayload.issues.length
      ? latestPayload.issues
          .map((issue) => `  - ${issue.severity}: ${issue.message}`)
          .join("\n")
      : "  - none";
    sections.push(
      "Authoritative results from the deterministic intake run (treat these " +
        "as ground truth; do not re-score unless explicitly asked to " +
        "re-evaluate):\n" +
        `- Recommended tier: ${scoreInfo.tier}\n` +
        `- Score: ${scoreInfo.total_score}/${scoreInfo.max_score}\n` +
        `- Timeline: ${scoreInfo.timeline}\n` +
        `- Validation issues:\n${issueLines}`
    );
    sections.push(
      "Original questionnaire markdown (use THIS as input if you run any " +
        "intake tools):\n" + lastRunQuestionnaire
    );
  }
  if (documents[activeDoc]) {
    sections.push(`Currently viewed document (${activeDoc}):\n${documents[activeDoc]}`);
  }
  return sections.length ? `\n\nContext:\n${sections.join("\n\n---\n\n")}` : "";
}

// The in-flight Ask DIA request (typed or Conversation mode); switching to Off aborts it.
let chatController = null;

async function sendAgentMessage() {
  if (assistantMode === "off") return null; // Off: no Gemini call, ever
  const message = agentMessage.value.trim();
  if (!message) {
    agentOutput.textContent = "Type a question for the agent first.";
    return null;
  }
  const context = buildAgentContext();
  agentOutput.textContent = "Thinking…";
  agentAiLabel.hidden = true;
  copyQuestions.hidden = true;
  setBusy(runChat, true);
  chatController?.abort();
  const controller = new AbortController();
  chatController = controller;
  try {
    const response = await fetch("/api/agent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: message + context }),
      signal: controller.signal,
    });
    const payload = await response.json();
    if (payload.enabled === false) {
      disableAskDia();
      return null;
    }
    if (!response.ok) {
      throw new Error(payload.error || "Agent run failed");
    }
    if (controller.signal.aborted || assistantMode === "off") return null;
    showAiReply(payload.reply || "(empty response)");
    return payload.reply || "";
  } catch (error) {
    agentOutput.textContent = error.name === "AbortError" ? "Request cancelled" : error.message;
    return null;
  } finally {
    if (chatController === controller) chatController = null;
    setBusy(runChat, agentMessage.disabled);
  }
}

runChat.addEventListener("click", async () => {
  const reply = await sendAgentMessage();
  if (autoSpeakEnabled && reply && assistantMode !== "off") {
    speakText(reply, listenAgent);
  }
});

applyAssistantMode();
refreshAgentStatus();

copyAll.addEventListener("click", async () => {
  await copyText(buildMarkdownExport(), "All outputs copied", exportStatus);
});

downloadMarkdown.addEventListener("click", () => {
  downloadFile("dia-outputs.md", buildMarkdownExport(), "text/markdown");
  flashStatus(exportStatus, "Markdown downloaded");
});

downloadJson.addEventListener("click", () => {
  downloadFile(
    "dia-export.json",
    exportJson(latestPayload),
    "application/json"
  );
  flashStatus(exportStatus, "JSON downloaded");
});

copySection.addEventListener("click", async () => {
  await copyText(scrubForExport(documents)[activeDoc] || "", `${activeLabel()} copied`, exportStatus);
});

const draftEditor = document.querySelector("#draftEditor");
const draftStatus = document.querySelector("#draftStatus");
const draftLoad = document.querySelector("#draftLoad");
const draftCopyPaste = document.querySelector("#draftCopyPaste");
const draftUndo = document.querySelector("#draftUndo");
const draftRedo = document.querySelector("#draftRedo");
const draftClear = document.querySelector("#draftClear");
const formatButtons = [...document.querySelectorAll(".fmt[data-cmd]")];

function draftText() {
  return draftEditor.innerText.replace(/\u00a0/g, " ");
}

function refreshDraftControls() {
  draftClear.disabled = draftText().trim().length === 0;
  draftLoad.disabled = !documents[activeDoc];
}

draftEditor.addEventListener("input", refreshDraftControls);

// Route content changes through execCommand so they land on the editor's
// native undo stack and the visible Undo/Redo buttons can revert them.
function replaceDraftContent(text) {
  draftEditor.focus();
  document.execCommand("selectAll");
  document.execCommand("insertText", false, text);
}

draftLoad.addEventListener("click", () => {
  const content = documents[activeDoc];
  if (!content) {
    flashStatus(draftStatus, "Run an intake first");
    return;
  }
  replaceDraftContent(content);
  refreshDraftControls();
  flashStatus(draftStatus, `${activeLabel()} loaded into draft editor`);
});

async function copyDraft() {
  const text = draftText();
  if (!text.trim()) {
    flashStatus(draftStatus, "No draft to copy");
    return;
  }
  try {
    if (navigator.clipboard?.write && window.ClipboardItem && window.isSecureContext) {
      await navigator.clipboard.write([
        new ClipboardItem({
          "text/html": new Blob([draftEditor.innerHTML], { type: "text/html" }),
          "text/plain": new Blob([text], { type: "text/plain" }),
        }),
      ]);
      flashStatus(draftStatus, "Draft copied (with formatting)");
      return;
    }
  } catch (error) {
    // Rich copy denied; fall through to the plain-text helper and its fallbacks.
  }
  await copyText(text, "Draft copied", draftStatus);
}

async function pasteIntoDraft() {
  if (!navigator.clipboard?.readText || !window.isSecureContext) {
    flashStatus(draftStatus, "Paste blocked by browser - use Ctrl+V in the editor");
    draftEditor.focus();
    return;
  }
  try {
    const clip = await navigator.clipboard.readText();
    if (!clip) {
      flashStatus(draftStatus, "Clipboard is empty");
      return;
    }
    draftEditor.focus();
    document.execCommand("insertText", false, clip);
    refreshDraftControls();
    flashStatus(draftStatus, "Pasted from clipboard");
  } catch (error) {
    flashStatus(draftStatus, "Paste blocked by browser - use Ctrl+V in the editor");
    draftEditor.focus();
  }
}

// One multifunctional button: click = copy, double-click = paste. Copy must
// NOT fire while a double-click can still be recognized: it would overwrite
// the clipboard with the draft, so the paste would re-insert the draft
// instead of what the user meant to paste. Paste rides the browser's native
// dblclick recognition; copy waits out a grace period above common OS
// double-click intervals (Windows defaults to 500ms) and is cancelled the
// moment a second click arrives (event.detail > 1).
const DOUBLE_CLICK_GRACE_MS = 600;
let copyPasteTimer = null;

draftCopyPaste.addEventListener("click", (event) => {
  clearTimeout(copyPasteTimer);
  copyPasteTimer = null;
  if (event.detail > 1) {
    return; // part of a multi-click gesture; dblclick handles it
  }
  copyPasteTimer = setTimeout(() => {
    copyPasteTimer = null;
    copyDraft();
  }, DOUBLE_CLICK_GRACE_MS);
});

draftCopyPaste.addEventListener("dblclick", () => {
  clearTimeout(copyPasteTimer);
  copyPasteTimer = null;
  pasteIntoDraft();
});

draftUndo.addEventListener("click", () => {
  draftEditor.focus();
  document.execCommand("undo");
  refreshDraftControls();
});

draftRedo.addEventListener("click", () => {
  draftEditor.focus();
  document.execCommand("redo");
  refreshDraftControls();
});

draftClear.addEventListener("click", () => {
  draftEditor.focus();
  document.execCommand("selectAll");
  document.execCommand("delete");
  // Browsers often leave a stray <br> behind, which defeats the :empty
  // placeholder; strip the leftover markup once the text is gone.
  if (!draftEditor.innerText.trim()) {
    draftEditor.innerHTML = "";
  }
  refreshDraftControls();
  flashStatus(draftStatus, "Draft cleared");
});

function closestInEditor(tagName) {
  let node = window.getSelection()?.anchorNode;
  while (node && node !== draftEditor) {
    if (node.nodeName === tagName) {
      return node;
    }
    node = node.parentNode;
  }
  return null;
}

function applyFormatCommand(command) {
  draftEditor.focus();
  if (command === "letteredList") {
    document.execCommand("insertOrderedList");
    const list = closestInEditor("OL");
    if (list) {
      list.classList.add("lettered");
    }
  } else if (command === "insertOrderedList") {
    document.execCommand("insertOrderedList");
    const list = closestInEditor("OL");
    if (list) {
      list.classList.remove("lettered");
    }
  } else {
    document.execCommand(command, false, null);
  }
  refreshDraftControls();
}

formatButtons.forEach((button) => {
  // Keep focus (and the selection) in the editor while clicking the toolbar.
  button.addEventListener("mousedown", (event) => event.preventDefault());
  button.addEventListener("click", () => applyFormatCommand(button.dataset.cmd));
});

// List type dropdown: one toolbar button, menu picks bullet/numbered/lettered.
const listMenuButton = document.querySelector("#listMenuButton");
const listMenu = document.querySelector("#listMenu");
const listMenuItems = [...listMenu.querySelectorAll(".menu-item")];

function setListMenuOpen(open, { focusFirst = false } = {}) {
  listMenu.classList.toggle("open", open);
  listMenuButton.setAttribute("aria-expanded", String(open));
  if (open && focusFirst) {
    listMenuItems[0].focus();
  }
}

listMenuButton.addEventListener("mousedown", (event) => event.preventDefault());
listMenuButton.addEventListener("click", () => {
  setListMenuOpen(!listMenu.classList.contains("open"));
});
listMenuButton.addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    setListMenuOpen(true, { focusFirst: true });
  }
});

listMenu.addEventListener("keydown", (event) => {
  const index = listMenuItems.indexOf(document.activeElement);
  if (event.key === "Escape") {
    setListMenuOpen(false);
    listMenuButton.focus();
  } else if (event.key === "Tab") {
    // Menu items are out of the tab order (ARIA menu pattern); Tab leaves
    // the menu entirely, so close it and let focus move on naturally.
    setListMenuOpen(false);
  } else if (event.key === "ArrowDown") {
    event.preventDefault();
    listMenuItems[(index + 1) % listMenuItems.length].focus();
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    listMenuItems[(index - 1 + listMenuItems.length) % listMenuItems.length].focus();
  }
});

listMenuItems.forEach((item) => {
  item.addEventListener("mousedown", (event) => event.preventDefault());
  item.addEventListener("click", () => {
    applyFormatCommand(item.dataset.cmd);
    setListMenuOpen(false);
  });
});

document.addEventListener("click", (event) => {
  if (listMenu.classList.contains("open") && !event.target.closest(".list-menu")) {
    setListMenuOpen(false);
  }
});

// Render a finished intake (fresh run or reopened from the filing cabinet).
function showResults(payload, questionnaireText) {
  latestPayload = payload;
  lastRunQuestionnaire = questionnaireText;
  documents = payload.documents;
  tier.textContent = payload.score.tier;
  score.textContent = `${payload.score.total_score}/${payload.score.max_score} · ${payload.score.timeline}`;
  renderIssues(payload.issues);
  renderInputs(payload);
  renderLegacyScoreHint(payload);
  renderBeforeAfter(payload, null);
  refreshFollowupButton();
  renderDocument(activeDoc);
  setExportAvailability(true);
  setIntakeCollapsed(true);
  setStage("results");
}

function renderIssues(items) {
  issues.innerHTML = "";
  if (!items.length) {
    const item = document.createElement("li");
    item.textContent = "No blocking intake issues found.";
    issues.appendChild(item);
    return;
  }
  for (const issue of items) {
    const item = document.createElement("li");
    item.textContent = `${issue.severity.toUpperCase()}: ${issue.message}`;
    issues.appendChild(item);
  }
}

// Proto, on #39: intakes saved before dia-savant have no value_at_stake in their score. On reopen they are rescored
// under the current rules (from their saved intake, the same input the PDF uses), so the screen and the PDF never
// disagree; the hint then reads "Rescored under the current rules." If the rescore can't run (offline), the old
// score stays on screen with "Scored before value at stake".
const isLegacyScore = (payload) => Boolean(payload && payload.score && !Object.prototype.hasOwnProperty.call(payload.score, "value_at_stake"));
function renderLegacyScoreHint(payload) {
  const hint = document.querySelector("#legacyScoreHint");
  if (!hint) return;
  const rescored = Boolean(payload && payload.rescored);
  hint.hidden = !(rescored || isLegacyScore(payload));
  hint.textContent = rescored ? "Rescored under the current rules." : "Scored before value at stake";
}

async function rescoreEntry(item) {
  const response = await fetch("/api/rescore", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ result: scrubForExport(item.result) }),
  });
  if (!response.ok) throw new Error("rescore failed");
  const payload = await response.json();
  const onScreen = latestPayload === item.result || (item.id && currentEntryId === item.id);
  item.result = payload;
  if (item.id && readCabinet().some((entry) => entry.id === item.id)) {
    updateEntry(item.id, { result: payload, tier: payload.score.tier, score: payload.score.total_score, maxScore: payload.score.max_score });
  }
  if (onScreen) showResults(payload, item.questionnaire);
  return payload;
}

// dia-savant §3.5: the Inputs label (complete, partial or thin) and one sentence on what to do. Screen only.
const INPUTS_NOTES = {
  complete: "Every answer needed for a firm quote is in; confirm scope on a kickoff call.",
  partial: "The value at stake is computed, but the flags below need clearing before a firm quote.",
  thin: "Ask for weekly hours, an hourly cost and the missing answers before quoting.",
};
function renderInputs(payload) {
  const state = INPUTS_NOTES[payload.inputs_confidence] ? payload.inputs_confidence : null;
  const inputsState = document.querySelector("#inputsState");
  const inputsNote = document.querySelector("#inputsNote");
  inputsState.hidden = inputsNote.hidden = !state;
  inputsState.textContent = state ? `Inputs: ${state}` : "";
  inputsNote.textContent = state ? INPUTS_NOTES[state] : "";
  const list = document.querySelector("#flagActions");
  const actions = Array.isArray(payload.flag_actions) ? payload.flag_actions : [];
  list.innerHTML = "";
  for (const action of actions) {
    const item = document.createElement("li");
    item.textContent = action;
    list.appendChild(item);
  }
  list.hidden = document.querySelector("#flagsHeading").hidden = !actions.length;
}

function renderDocument(name) {
  output.textContent = documents[name] || "Run an intake to generate this document.";
  refreshDraftControls();
}

function setExportAvailability(isAvailable) {
  copyAll.disabled = !isAvailable;
  downloadMarkdown.disabled = !isAvailable;
  downloadJson.disabled = !isAvailable;
  downloadPdf.disabled = !isAvailable;
  copySection.disabled = !isAvailable;
  listenDocument.disabled = !isAvailable;
  shareX.disabled = !isAvailable;
  const rerun = document.querySelector("#rerunUpdated");
  if (rerun) rerun.disabled = !isAvailable;
}

// Persona v1.2 rule 1: no AI text in client exports. Mirrors scrub_for_export in client_discovery/core.py:
// drop unreviewed AI keys (strategic_analysis), cut anything appended after opportunity-analysis.md's last
// deterministic section, and remove every line carrying a "DIA Agent Error", whatever produced it.
// dia-savant documents end with "Top workflows by value at stake" (its ### title, then one block of rows or
// one sentence); older stored documents end with the Score Reasons bullets.
const UNREVIEWED_AI_KEYS = new Set(["strategic_analysis"]);
const TOP_WORKFLOWS = "\n## Top workflows by value at stake\n";
const SCORE_REASONS = "\n## Score Reasons\n";
function deterministicAnalysis(text) {
  let at = text.indexOf(TOP_WORKFLOWS);
  if (at !== -1) {
    const head = text.slice(0, at + TOP_WORKFLOWS.length);
    const kept = [];
    let seenTitle = false;
    let inBlock = false;
    for (const line of text.slice(head.length).split("\n")) {
      if (!seenTitle) { kept.push(line); seenTitle = line.startsWith("### "); continue; }
      if (line.trim()) { inBlock = true; kept.push(line); } else if (inBlock) break; else kept.push(line);
    }
    return `${head}${kept.join("\n").replace(/\n+$/, "")}\n`;
  }
  at = text.indexOf(SCORE_REASONS);
  if (at === -1) return text;
  const head = text.slice(0, at + SCORE_REASONS.length);
  const bullets = [];
  for (const line of text.slice(head.length).split("\n")) { if (!line.startsWith("- ")) break; bullets.push(line); }
  return `${head}${bullets.join("\n")}\n`;
}
function scrubForExport(value, key = null) {
  if (Array.isArray(value)) return value.map((item) => scrubForExport(item));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).filter(([k]) => !UNREVIEWED_AI_KEYS.has(k)).map(([k, v]) => [k, scrubForExport(v, k)]));
  }
  if (typeof value !== "string") return value;
  const text = key === "opportunity-analysis.md" ? deterministicAnalysis(value) : value;
  return text.split("\n").filter((line) => !line.toLowerCase().includes("dia agent error")).join("\n");
}
const MD_EXPORT_ORDER = ["opportunity-analysis.md", "client-profile.md", "proposal-draft.md"];
const exportJson = (result) => JSON.stringify(scrubForExport(result), null, 2);

function buildMarkdownExport(docs = documents) {
  const safe = scrubForExport(docs || {});
  // dia-savant §6: the Markdown export follows the PDF's answer-first order (the on-screen tabs keep theirs).
  return MD_EXPORT_ORDER
    .map((name) => safe[name])
    .filter(Boolean)
    .join("\n\n---\n\n");
}

// Inline statuses keep action feedback next to the controls that caused it;
// the intake status line reports intake state only. Messages auto-clear.
const statusTimers = new Map();

function flashStatus(statusEl, message) {
  statusEl.textContent = message;
  clearTimeout(statusTimers.get(statusEl));
  statusTimers.set(
    statusEl,
    setTimeout(() => {
      statusEl.textContent = "";
    }, 4000)
  );
}

async function copyText(text, successMessage, statusEl) {
  if (!text) {
    flashStatus(statusEl, "No output to copy");
    return;
  }

  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      copyTextFallback(text);
    }
    flashStatus(statusEl, successMessage);
  } catch (error) {
    // Embedded contexts (iframes, previews) can deny the async clipboard API
    // even on localhost; the execCommand fallback usually still works there.
    try {
      copyTextFallback(text);
      flashStatus(statusEl, successMessage);
    } catch (fallbackError) {
      flashStatus(statusEl, "Copy failed");
    }
  }
}

function copyTextFallback(text) {
  const scratch = document.createElement("textarea");
  scratch.value = text;
  scratch.setAttribute("readonly", "");
  scratch.style.position = "fixed";
  scratch.style.top = "-9999px";
  try {
    document.body.appendChild(scratch);
    scratch.select();
    if (!document.execCommand("copy")) {
      throw new Error("copy command rejected");
    }
  } finally {
    scratch.remove();
  }
}

function downloadFile(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 100);
}

function activeLabel() {
  const tab = tabs.find((item) => item.dataset.doc === activeDoc);
  return tab ? tab.textContent : activeDoc;
}

// --- Speech: text-to-speech, voice responses, conversation mode ---

const listenDocument = document.querySelector("#listenDocument");
const listenAgent = document.querySelector("#listenAgent");
const autoSpeak = document.querySelector("#autoSpeak");
const micButton = document.querySelector("#micButton");
const conversationMode = document.querySelector("#conversationMode");

const ttsSupported = "speechSynthesis" in window;
const SpeechRecognitionImpl =
  window.SpeechRecognition || window.webkitSpeechRecognition || null;

if (!ttsSupported) {
  document
    .querySelectorAll(".speech-only")
    .forEach((el) => (el.hidden = true));
}
if (!SpeechRecognitionImpl) {
  document.querySelectorAll(".speech-input-only").forEach((el) => {
    el.disabled = true;
    el.title = "Speech recognition is not supported in this browser";
  });
}

let currentSpeakButton = null;

function setSpeakingState(button, speaking) {
  if (!button) {
    return;
  }
  button.setAttribute("aria-pressed", String(speaking));
  const label = button.querySelector(".speech-label");
  if (label) {
    label.textContent = speaking ? "Stop" : "Listen";
  }
}

function stopSpeaking() {
  if (ttsSupported) {
    window.speechSynthesis.cancel();
  }
  setSpeakingState(currentSpeakButton, false);
  currentSpeakButton = null;
}

function speakText(text, button, { onDone } = {}) {
  if (!ttsSupported) {
    return;
  }
  // Strip markdown decoration so headings and emphasis read naturally.
  const clean = (text || "").replace(/[#*`_]/g, "").trim();
  if (!clean) {
    statusText.textContent = "Nothing to read aloud";
    if (onDone) onDone();
    return;
  }
  stopSpeaking();
  const utterance = new SpeechSynthesisUtterance(clean);
  currentSpeakButton = button || null;
  setSpeakingState(currentSpeakButton, true);
  const finish = () => {
    setSpeakingState(button, false);
    if (currentSpeakButton === button) {
      currentSpeakButton = null;
    }
    if (onDone) onDone();
  };
  utterance.onend = finish;
  utterance.onerror = finish;
  window.speechSynthesis.speak(utterance);
}

function toggleSpeak(text, button) {
  if (currentSpeakButton === button && window.speechSynthesis.speaking) {
    stopSpeaking();
    return;
  }
  speakText(text, button);
}

if (ttsSupported) {
  listenDocument.addEventListener("click", () => {
    toggleSpeak(documents[activeDoc] || "", listenDocument);
  });
  listenAgent.addEventListener("click", () => {
    toggleSpeak(agentOutput.textContent, listenAgent);
  });
}

let autoSpeakEnabled = false;

function applyAutoSpeak(enabled) {
  autoSpeakEnabled = enabled && ttsSupported;
  autoSpeak.setAttribute("aria-pressed", String(autoSpeakEnabled));
  autoSpeak.textContent = `Auto-speak: ${autoSpeakEnabled ? "on" : "off"}`;
}

autoSpeak.addEventListener("click", () => {
  applyAutoSpeak(!autoSpeakEnabled);
  if (!autoSpeakEnabled) {
    stopSpeaking();
  }
  try {
    localStorage.setItem("dia-autospeak", autoSpeakEnabled ? "on" : "off");
  } catch (error) {
    // Storage can be blocked; the toggle still works for this session.
  }
});

try {
  applyAutoSpeak(localStorage.getItem("dia-autospeak") === "on");
} catch (error) {
  applyAutoSpeak(false);
}

// --- Voice input: one-shot dictation and hands-free conversation mode ---

let recognition = null;
let recognizing = false;
let conversationActive = false;

// Shared Web Speech helper (Ask DIA mic, conversation mode, home-composer dictation).
// One recognizer at a time across the app: starting a new one cancels the previous.
let activeRecognizer = null;

function startRecognizer({ button, continuous = false, interimResults = false, onResult, onError, onEnd }) {
  if (activeRecognizer) {
    activeRecognizer.cancelled = true;
    activeRecognizer.abort();
  }
  const rec = new SpeechRecognitionImpl();
  rec.lang = navigator.language || "en-US";
  rec.continuous = continuous;
  rec.interimResults = interimResults;
  rec.maxAlternatives = 1;
  let settled = false;
  const setPressed = (on) => {
    if (!button) return;
    button.setAttribute("aria-pressed", String(on));
    button.classList.toggle("listening", on);
    const label = on ? button.dataset.labelOn : button.dataset.labelOff;
    if (label) button.setAttribute("aria-label", label);
  };
  rec.onstart = () => {
    recognizing = true;
    setPressed(true);
  };
  rec.onresult = (event) => {
    settled = true;
    if (!rec.cancelled) onResult?.(event);
  };
  rec.onerror = (event) => {
    settled = true;
    if (!rec.cancelled) onError?.(event.error);
  };
  rec.onend = () => {
    if (activeRecognizer === rec) {
      activeRecognizer = null;
      recognizing = false;
    }
    setPressed(false);
    if (!settled && !rec.cancelled) onError?.("no-speech");
    onEnd?.(rec);
  };
  activeRecognizer = rec;
  recognition = rec;
  rec.start();
  return rec;
}

// One-shot final transcript (conversation mode turns).
function listenOnce({ onTranscript, onError, button = micButton }) {
  return startRecognizer({
    button,
    onResult: (event) => onTranscript(event.results[0][0].transcript.trim()),
    onError,
  });
}

// Dictation into a textarea: interim words show live and are appended after whatever was
// already typed (never replacing it). Click again or press Esc to stop; stop() keeps the words.
// Without speech recognition the button stays focusable but aria-disabled, with a tooltip.
function attachDictation(button, textarea, { continuous = true, intercept, label = "Dictation" } = {}) {
  if (!button || !textarea) return null;
  // Unsupported (composer-icons.md section 3, flows 2.5c): aria-disabled, not disabled, so it stays focusable; the
  // custom tooltip and aria-describedby carry the note. No native title (it would show a second tooltip).
  const unsupported = "Voice input isn't supported in this browser. Try Chrome, Edge or Safari.";
  if (!SpeechRecognitionImpl) {
    button.setAttribute("aria-disabled", "true");
    button.removeAttribute("title");
    const wrap = button.closest(".composer");
    const note = wrap?.querySelector("[id$='MicNote']");
    if (note) button.setAttribute("aria-describedby", note.id);
    const tip = wrap?.querySelector(".cmp-tip-mic");
    if (tip) tip.textContent = unsupported;
    button.addEventListener("click", () => { statusText.textContent = unsupported; });
    return null;
  }
  let rec = null;
  let base = "";
  let finals = "";
  let lastWritten = "";
  const joiner = (text) => (text && !/\s$/.test(text) ? " " : "");
  function write(interim) {
    const tail = [finals, interim].filter(Boolean).join(" ").trim();
    const value = tail ? base + joiner(base) + tail : base;
    textarea.value = value;
    lastWritten = value;
    autosizeComposer(textarea);
  }
  function start() {
    base = textarea.value.replace(/\s+$/, "");
    finals = "";
    lastWritten = textarea.value;
    stopSpeaking(); // starting dictation always stops read-aloud, so the mic doesn't hear DIA
    statusText.textContent = "Listening…";
    const mine = startRecognizer({
      button,
      continuous,
      interimResults: true,
      onResult: (event) => {
        // The user typed while dictating: keep their edit as the new base.
        if (textarea.value !== lastWritten) {
          base = textarea.value.replace(/\s+$/, "");
          finals = "";
        }
        let interim = "";
        for (let i = event.resultIndex; i < event.results.length; i += 1) {
          const words = event.results[i][0].transcript.trim();
          if (!words) continue;
          if (event.results[i].isFinal) finals = finals ? `${finals} ${words}` : words;
          else interim = interim ? `${interim} ${words}` : words;
        }
        write(interim);
      },
      onError: (error) => {
        if (error === "aborted") return;
        statusText.textContent =
          error === "not-allowed" || error === "service-not-allowed"
            ? "Microphone blocked. Allow it in your browser's site settings, then try again."
            : error === "no-speech"
              ? "Didn't catch anything. Try again."
              : error === "audio-capture"
                ? "Voice input stopped: no microphone found"
                : error === "network"
                  ? "Voice input stopped: connection problem"
                  : `${label} failed (${error})`;
      },
      onEnd: (ended) => {
        if (ended !== rec) return;
        rec = null;
        if (textarea.value === lastWritten) write(""); // drop any unfinished interim words
        if (!ended.cancelled && finals) statusText.textContent = `${label} added`;
        if (!ended.cancelled) textarea.focus();
        if (!ended.cancelled) textarea.dispatchEvent(new CustomEvent("dictationend"));
      },
    });
    rec = mine;
  }
  function stop(message = "Stopped listening") {
    if (!rec) return;
    statusText.textContent = message;
    rec.stop();
  }
  button.addEventListener("click", () => {
    if (button.getAttribute("aria-disabled") === "true") { statusText.textContent = VOICE_OFF_NOTE; return; } // Off mode
    if (intercept?.()) return;
    if (rec) stop();
    else start();
  });
  // Esc stops dictation (capture phase), but an open popover (voice fan, plus menu) closes first.
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && rec && !Popovers.anyOpen()) {
      event.preventDefault();
      event.stopPropagation();
      stop();
    }
  }, true);
  return { start, stop, isListening: () => Boolean(rec) };
}

// Aborting a recognizer still fires its error/end events asynchronously;
// the cancelled flag keeps those late events from overwriting whatever
// status message the cancelling code just set.
function cancelRecognition() {
  if (activeRecognizer) {
    activeRecognizer.cancelled = true;
    activeRecognizer.abort();
  }
}

function stopConversation(message) {
  conversationActive = false;
  conversationMode.setAttribute("aria-pressed", "false");
  conversationMode.classList.remove("recording");
  cancelRecognition();
  stopSpeaking();
  if (message) {
    statusText.textContent = message;
  }
}

function conversationTurn() {
  if (!conversationActive) {
    return;
  }
  statusText.textContent = "Listening...";
  recognition = listenOnce({
    onTranscript: async (text) => {
      if (!conversationActive) {
        return;
      }
      if (!text) {
        conversationTurn();
        return;
      }
      agentMessage.value = text;
      autosizeComposer(agentMessage);
      statusText.textContent = "Sending to agent...";
      const reply = await sendAgentMessage();
      if (!conversationActive) {
        return;
      }
      if (reply) {
        statusText.textContent = "Speaking reply...";
        speakText(reply, listenAgent, {
          onDone: () => {
            if (conversationActive) {
              conversationTurn();
            }
          },
        });
      } else {
        stopConversation("Conversation paused: agent error");
      }
    },
    onError: (error) => {
      if (error === "no-speech" || error === "aborted") {
        if (conversationActive && error === "no-speech") {
          conversationTurn();
        }
        return;
      }
      stopConversation(
        error === "not-allowed"
          ? "Microphone permission denied"
          : `Conversation stopped (${error})`
      );
    },
  });
}

// Ask DIA mic: the shared dictation helper (one-shot), ending conversation mode first if it's on.
attachDictation(micButton, agentMessage, {
  continuous: false,
  intercept: () => {
    if (!conversationActive) return false;
    stopConversation("Conversation ended");
    return true;
  },
});
// Home composer mic: continuous dictation into the questionnaire.
const intakeMic = document.querySelector("#intakeMic");
const intakeDictation = attachDictation(intakeMic, questionnaire, { continuous: true });

if (SpeechRecognitionImpl) {
  conversationMode.addEventListener("click", () => {
    if (conversationActive) {
      stopConversation("Conversation ended");
      return;
    }
    cancelRecognition();
    conversationActive = true;
    conversationMode.setAttribute("aria-pressed", "true");
    conversationMode.classList.add("recording");
    conversationTurn();
  });
}

// --- Popovers: only one composer popover is open at a time (voice fans, plus menus, cabinet stack) ---
const Popovers = (() => {
  const list = [];
  return {
    register(popover) { list.push(popover); return popover; },
    closeOthers(except) {
      list.forEach((popover) => { if (popover !== except && popover.isOpen()) popover.hide(); });
      if (except !== "cabinet" && CabinetFan.isOpen()) CabinetFan.close({ restoreFocus: false });
    },
    anyOpen: () => list.some((popover) => popover.isOpen()),
  };
})();

// --- Voice options fan: from the 36x12 notch on each composer's top edge (glyph composer-icons.md 5, flows v8.8 2.6a) ---
// Pointer: hover on the notch opens it after --dia-delay-intent (120ms), not while dictation or Conversation mode is
// running; leaving the notch and the fan closes it after --dia-delay-grace (200ms); a click toggles. Touch: a tap toggles.
// Focus never opens it. Keyboard: Enter/Space/ArrowDown open it on Listen, ArrowUp on Auto-speak; arrows wrap,
// Home/End jump, Esc closes and returns focus to the notch, Tab closes. Items hold no state: they click the Ask DIA
// control they mirror (#conversationMode, #listenAgent, #autoSpeak) and reflect its aria-pressed.
function createVoiceFan(prefix, { conversationRun, conversationNote = null }) {
  const notch = document.querySelector(`#${prefix}VoiceNotch`);
  const fan = document.querySelector(`#${prefix}VoiceFan`);
  const tip = document.querySelector(`#${prefix}NotchTip`);
  if (!notch || !fan) return null;
  const token = (name, fallback) => parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name)) || fallback;
  const entries = [
    { item: fan.querySelector(`#${prefix}FanConversation`), source: conversationMode, supported: Boolean(SpeechRecognitionImpl),
      note: "Not supported", run: conversationRun, closes: true },
    { item: fan.querySelector(`#${prefix}FanListen`), source: listenAgent, supported: ttsSupported,
      note: "Not supported", run: () => listenAgent.click(), closes: true },
    { item: fan.querySelector(`#${prefix}FanAutoSpeak`), source: autoSpeak, supported: ttsSupported,
      note: "Not supported", run: () => autoSpeak.click(), closes: false },
  ];
  // Unsupported items stay in place, aria-disabled with a note under the label, so the row keeps its shape.
  entries.forEach((entry) => {
    if (entry.supported) return;
    entry.item.setAttribute("aria-disabled", "true");
    const note = document.createElement("span");
    note.className = "voice-note";
    note.textContent = entry.note;
    entry.item.querySelector(".voice-label").append(note);
    entry.item.setAttribute("aria-label", `${entry.item.getAttribute("aria-label")} (${entry.note.toLowerCase()})`);
  });
  if (!entries.some((entry) => entry.supported)) { notch.remove(); tip?.remove(); fan.remove(); return null; }

  const [conv, listen, auto] = entries;
  const sync = () => {
    conv.item.setAttribute("aria-checked", String(conversationMode.getAttribute("aria-pressed") === "true"));
    const speaking = listenAgent.getAttribute("aria-pressed") === "true";
    listen.item.dataset.speaking = String(speaking);
    if (listen.supported) {
      listen.item.setAttribute("aria-label", speaking ? "Stop reading aloud" : "Listen: read the latest reply aloud");
      listen.item.querySelector(".voice-label").firstChild.textContent = speaking ? "Stop" : "Listen";
    }
    auto.item.setAttribute("aria-checked", String(autoSpeak.getAttribute("aria-pressed") === "true"));
  };
  const observer = new MutationObserver(sync);
  entries.forEach(({ source }) => observer.observe(source, { attributes: true, attributeFilter: ["aria-pressed"] }));
  sync();

  // Closed = inert + visibility:hidden (CSS), so nothing in the fan can be hit, focused or painted after it closes.
  fan.inert = true;
  // Root cause of the "faint shadow" after closing (landing polish): the notch is still hovered or focused when the fan
  // closes, so its "Voice options" tooltip (a bordered, shadowed box in the fan's spot) faded straight back in. Keep the
  // tip muted after a close until the pointer leaves the notch or focus moves off it.
  const muteTip = () => { notch.dataset.tipMuted = "true"; };
  const unmuteTip = () => { delete notch.dataset.tipMuted; };
  notch.addEventListener("pointerleave", unmuteTip);
  notch.addEventListener("blur", unmuteTip);
  let open = false;
  let openedByHover = false;
  let hoverTimer = 0;
  let leaveTimer = 0;
  const items = () => entries.map((entry) => entry.item);
  const inside = (node) => Boolean(node) && (notch.contains(node) || fan.contains(node));
  const busyListening = () => document.querySelector(".cmp-mic[aria-pressed='true']") !== null || conversationActive;
  const clearTimers = () => { clearTimeout(hoverTimer); clearTimeout(leaveTimer); };
  const api = {
    isOpen: () => open,
    show({ focus = null, viaHover = false } = {}) {
      clearTimers();
      if (!open) {
        Popovers.closeOthers(api);
        open = true;
        openedByHover = viaHover;
        fan.inert = false;
        notch.setAttribute("aria-expanded", "true");
        sync();
      }
      if (focus !== null) {
        const list = items();
        list[(focus + list.length) % list.length].focus();
      }
    },
    hide({ returnFocus = false } = {}) {
      clearTimers();
      if (open) {
        open = false;
        muteTip();
        notch.setAttribute("aria-expanded", "false");
        fan.inert = true;
      }
      if (returnFocus) notch.focus();
    },
  };

  const enter = (event) => { if (event.pointerType !== "touch") clearTimeout(leaveTimer); };
  const leave = (event) => {
    if (event.pointerType === "touch") return;
    clearTimeout(hoverTimer);
    if (inside(event.relatedTarget)) return;
    if (open && !fan.querySelector(":focus-visible")) leaveTimer = setTimeout(() => api.hide(), token("--dia-delay-grace", 200));
  };
  notch.addEventListener("pointerenter", (event) => {
    if (event.pointerType === "touch") return;
    clearTimeout(leaveTimer);
    if (!open && !busyListening()) hoverTimer = setTimeout(() => api.show({ viaHover: true }), token("--dia-delay-intent", 120));
  });
  fan.addEventListener("pointerenter", enter);
  notch.addEventListener("pointerleave", leave);
  fan.addEventListener("pointerleave", leave);
  // Click toggles. Enter/Space (detail 0) open on Listen. A mouse click on a notch that hover just opened keeps it open.
  notch.addEventListener("click", (event) => {
    if (event.detail === 0) { open ? api.hide() : api.show({ focus: 1 }); return; }
    if (open && openedByHover) { openedByHover = false; return; }
    open ? api.hide() : api.show();
  });
  notch.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") { event.preventDefault(); api.show({ focus: 1 }); }
    else if (event.key === "ArrowUp") { event.preventDefault(); api.show({ focus: 2 }); }
    else if (event.key === "Escape" && open) { event.preventDefault(); event.stopPropagation(); api.hide({ returnFocus: true }); }
  });
  fan.addEventListener("keydown", (event) => {
    const list = items();
    const index = list.indexOf(document.activeElement);
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (step) { event.preventDefault(); api.show({ focus: index + step }); }
    else if (event.key === "Home" || event.key === "End") { event.preventDefault(); api.show({ focus: event.key === "Home" ? 0 : list.length - 1 }); }
    else if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); api.hide({ returnFocus: true }); }
    else if (event.key === "Tab") api.hide();
  });
  fan.addEventListener("focusout", (event) => { if (open && event.relatedTarget && !inside(event.relatedTarget)) api.hide(); });
  document.addEventListener("pointerdown", (event) => { if (open && !inside(event.target)) api.hide(); });
  entries.forEach((entry) => entry.item.addEventListener("click", () => {
    if (entry.item.dataset.off === "true") { statusText.textContent = VOICE_OFF_NOTE; return; } // Off mode
    if (entry.item.getAttribute("aria-disabled") === "true") { statusText.textContent = `${entry.item.querySelector(".voice-label").firstChild.textContent}: ${entry.note.toLowerCase()} in this browser`; return; }
    const keyboard = entry.item.matches(":focus-visible");
    if (entry.closes) api.hide({ returnFocus: keyboard });
    entry.run();
    sync();
  }));
  return Popovers.register(api);
}

const VoiceFans = {
  intake: createVoiceFan("intake", { conversationRun: () => { if (!conversationActive) openAgent(); conversationMode.click(); } }),
  agent: createVoiceFan("agent", { conversationRun: () => conversationMode.click() }),
};
voiceReady = true;
applyVoiceOff();

// --- Hints (prompt bar v2, Ikenna: icons, not pills): one shared tooltip (#uiHint) for the composer's icon buttons.
// Any [data-hint] element shows it after --dia-delay-intent on pointer hover, at once on keyboard focus, and on a
// 450ms long-press on touch (that press doesn't also click). The text is data-hint, or the element's aria-label when
// data-hint is empty, so it always matches the accessible name, plus "on"/"off" for toggles. [data-hint-touch-only]
// (the mics and notches, which keep their own .cmp-tip on hover and focus) answers the long-press only.
const Hints = (() => {
  const tip = document.createElement("div");
  tip.id = "uiHint";
  tip.className = "ui-hint";
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  document.body.append(tip);
  const LONG_PRESS = 450;
  const TOUCH_HOLD = 1600;
  let owner = null;
  let showTimer = 0;
  let hideTimer = 0;
  let pressTimer = 0;
  const intent = () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--dia-delay-intent")) || 120;
  const hintFor = (node, { touch = false } = {}) => {
    const el = node?.closest?.("[data-hint]");
    if (!el || (!touch && el.hasAttribute("data-hint-touch-only"))) return null;
    return el;
  };
  const text = (el) => {
    let value = el.dataset.hint || el.getAttribute("aria-label") || "";
    const checked = el.getAttribute("aria-checked");
    if (value && checked !== null) value += checked === "true" ? " · on" : " · off";
    return value;
  };
  function place(el) {
    const rect = el.getBoundingClientRect();
    const width = tip.offsetWidth;
    const height = tip.offsetHeight;
    const left = Math.max(8, Math.min(rect.left + rect.width / 2 - width / 2, window.innerWidth - width - 8));
    let top = rect.top - height - 8;
    if (top < 8) top = rect.bottom + 8;
    tip.style.left = `${Math.round(left)}px`;
    tip.style.top = `${Math.round(top)}px`;
  }
  function show(el) {
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
    const value = text(el);
    if (!value) { hide(); return; }
    owner = el;
    tip.textContent = value;
    tip.hidden = false;
    place(el);
  }
  function hide() {
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
    owner = null;
    tip.hidden = true;
  }
  document.addEventListener("pointerover", (event) => {
    if (event.pointerType === "touch") return;
    const el = hintFor(event.target);
    if (!el || el === owner) return;
    clearTimeout(showTimer);
    showTimer = setTimeout(() => show(el), intent());
  });
  document.addEventListener("pointerout", (event) => {
    const el = hintFor(event.target, { touch: true });
    if (!el || el.contains(event.relatedTarget)) return;
    clearTimeout(showTimer);
    if (owner === el && !el.matches(":focus-visible")) hide();
  });
  document.addEventListener("focusin", (event) => {
    const el = hintFor(event.target);
    if (el && el.matches(":focus-visible")) show(el);
    else if (owner) hide();
  });
  document.addEventListener("focusout", (event) => { if (owner && owner === hintFor(event.target, { touch: true })) hide(); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && owner) hide(); }, true);
  document.addEventListener("pointerdown", (event) => {
    clearTimeout(pressTimer);
    if (event.pointerType !== "touch") { if (owner) hide(); return; }
    const el = hintFor(event.target, { touch: true });
    if (!el) return;
    pressTimer = setTimeout(() => {
      el.dataset.longPress = "true";
      show(el);
      hideTimer = setTimeout(hide, TOUCH_HOLD);
    }, LONG_PRESS);
  });
  ["pointerup", "pointercancel"].forEach((type) => document.addEventListener(type, () => clearTimeout(pressTimer)));
  // A long-press shows the hint instead of pressing the button.
  document.addEventListener("click", (event) => {
    const el = hintFor(event.target, { touch: true });
    if (el?.dataset.longPress) {
      delete el.dataset.longPress;
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    if (owner) setTimeout(() => { if (owner) { tip.textContent = text(owner); place(owner); } }, 0);   // toggles flip on/off
  }, true);
  document.addEventListener("contextmenu", (event) => { if (hintFor(event.target, { touch: true })) event.preventDefault(); });
  window.addEventListener("scroll", () => { if (owner) hide(); }, { passive: true, capture: true });
  window.addEventListener("resize", () => { if (owner) hide(); });
  return { show, hide, text, element: tip };
})();

// Cmd/Ctrl+Shift+Space toggles voice input for the composer that has focus (intake when neither does).
document.addEventListener("keydown", (event) => {
  if (!(event.metaKey || event.ctrlKey) || !event.shiftKey || event.code !== "Space") return;
  event.preventDefault();
  const inAgent = document.activeElement?.closest?.("#agentComposer");
  (inAgent ? micButton : intakeMic).click();
});

// --- Composer state: Run (enter) appears beside the mic only when the input has text ---
// Programmatic writes (dictation, Load sample, uploads, New intake, cabinet reopen) go through the value setter,
// so the textarea's value property is wrapped once per composer instead of patching every call site.
// Narrow screens (landing polish): textarea placeholders can't ellipsize, so at <=600px each composer swaps to its
// shorter data-placeholder-short copy and never cuts off mid-word.
(() => {
  const narrow = matchMedia("(max-width: 600px)");
  const fields = [...document.querySelectorAll("textarea[data-placeholder-short]")];
  fields.forEach((field) => { field.dataset.placeholderLong = field.placeholder; });
  const apply = () => fields.forEach((field) => {
    field.placeholder = narrow.matches ? field.dataset.placeholderShort : field.dataset.placeholderLong;
  });
  narrow.addEventListener("change", apply);
  apply();
})();

function watchComposer(textarea) {
  const bar = textarea.closest(".prompt-bar");
  if (!bar) return;
  const run = bar.querySelector(".cmp-enter");
  // Run is `hidden` while the trimmed value is empty (flows 2.8); its slot stays reserved so the mic never moves.
  // It never takes focus when it appears. If it had focus when hidden, focus moves to the textarea.
  const update = () => {
    const empty = textarea.value.trim() === "";
    bar.dataset.empty = String(empty);
    if (!run || run.hidden === empty) return;
    const hadFocus = document.activeElement === run;
    run.hidden = empty;
    if (hadFocus && empty) textarea.focus();
  };
  const native = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value");
  Object.defineProperty(textarea, "value", {
    configurable: true,
    get() { return native.get.call(this); },
    set(next) { native.set.call(this, next); update(); },
  });
  textarea.addEventListener("input", update);
  update();
}
watchComposer(questionnaire);
watchComposer(agentMessage);

// --- Share on X ---

const shareX = document.querySelector("#shareX");
const exportStatus = document.querySelector("#exportStatus");

shareX.addEventListener("click", () => {
  if (!latestPayload) {
    flashStatus(exportStatus, "Run an intake first");
    return;
  }
  const scoreInfo = latestPayload.score;
  const text =
    `DIA scored this discovery intake ${scoreInfo.total_score}/${scoreInfo.max_score} ` +
    `and recommends the "${scoreInfo.tier}" tier.`;
  const url = `https://x.com/intent/tweet?text=${encodeURIComponent(text)}`;
  window.open(url, "_blank", "noopener");
  flashStatus(exportStatus, "Opening X share window");
});

// --- Tia-style shell: stages, pill composers, menu, help, command palette ---

const breadcrumbStage = document.querySelector("#breadcrumbStage");

// "landing" shows only the hero + intake pill (like Tia's empty workspace);
// "results" reveals Ask DIA and the generated documents below the composer.
function setStage(stage) {
  document.body.dataset.stage = stage;
  // Letterhead band: the visible crumb is the stage only; "Discovery intake · " stays in a visually hidden span.
  const stageLabel = stage === "results" ? "Results" : "Questionnaire";
  breadcrumbStage.textContent = stageLabel;
  const menuStage = document.querySelector("#menuStage");
  if (menuStage) menuStage.textContent = `Discovery intake · ${stageLabel}`;
  autosizeComposer(agentMessage);
}

// Ask DIA can be opened before any intake has run (the agent works on its own).
function openAgent() {
  if (assistantMode === "off") return;
  document.body.dataset.agent = "open";
  autosizeComposer(agentMessage);
  agentMessage.focus();
}

// Pill composers grow with their content; past one line they become a
// rounded panel so pasted questionnaires stay readable.
function autosizeComposer(textarea) {
  const bar = textarea.closest(".prompt-bar");
  if (!textarea.getClientRects().length) {
    // Hidden (collapsed intake / unopened Ask DIA): size it when it reappears.
    textarea.style.height = "";
    return;
  }
  textarea.style.height = "auto";
  const lineHeight = parseFloat(getComputedStyle(textarea).lineHeight) || 27;
  const multiline =
    textarea.value !== "" &&
    (textarea.value.includes("\n") || textarea.scrollHeight > lineHeight * 1.6);
  if (bar) {
    bar.classList.toggle("multiline", multiline);
  }
  textarea.style.height = "auto";
  textarea.style.height = `${textarea.scrollHeight}px`;
}

[questionnaire, agentMessage].forEach((textarea) => {
  textarea.addEventListener("input", () => autosizeComposer(textarea));
  autosizeComposer(textarea);
});

// Questionnaires are multi-line markdown: Enter adds a line, Ctrl/Cmd+Enter runs.
questionnaire.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && !event.isComposing) {
    event.preventDefault();
    if (!runAgent.disabled && questionnaire.value.trim()) runAgent.click();
  }
});

// Ask DIA behaves like Tia's prompt: Enter sends, Shift+Enter is a new line.
agentMessage.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!runChat.disabled && agentMessage.value.trim()) runChat.click();
  }
});

function newIntake() {
  questionnaire.value = "";
  rerunOf = null;
  InputsPrompt.newSession();
  // Drop the previous run so stale results and exports cannot leak into the
  // new intake: back to the landing stage with exports disabled.
  latestPayload = null;
  lastRunQuestionnaire = "";
  documents = {};
  tier.textContent = "-";
  score.textContent = "Score pending";
  issues.replaceChildren();
  renderDocument(activeDoc);
  setExportAvailability(false);
  setStage("landing");
  if (intakeBody.hidden) setIntakeCollapsed(false);
  autosizeComposer(questionnaire);
  setRunState("ready", "New intake");
  questionnaire.focus();
}

function focusComposer() {
  if (document.body.dataset.stage === "results" || document.body.dataset.agent === "open") {
    agentMessage.focus();
  } else {
    if (intakeBody.hidden) setIntakeCollapsed(false);
    questionnaire.focus();
  }
}

// Mouse-reactive liquid wordmark (ported from Tia).
(() => {
  const title = document.querySelector("#titleBrand");
  const noise = document.querySelector("#liquidNoise");
  const displace = document.querySelector("#liquidDisplace");
  if (!title || !noise || !displace) return;
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  let hovering = false;
  let currentScale = 0;
  let running = false;
  const MAX_SCALE = 3;
  const RAMP_SPEED = 0.06;
  const DECAY_SPEED = 0.04;

  function tick() {
    currentScale = hovering
      ? currentScale + (MAX_SCALE - currentScale) * RAMP_SPEED
      : currentScale * (1 - DECAY_SPEED);
    noise.setAttribute("seed", String(Math.floor(performance.now() / 200)));
    displace.setAttribute("scale", currentScale.toFixed(2));
    if (currentScale > 0.05 || hovering) {
      requestAnimationFrame(tick);
    } else {
      running = false;
      currentScale = 0;
      displace.setAttribute("scale", "0");
      title.classList.remove("liquid-active");
    }
  }

  title.addEventListener("mouseenter", () => {
    hovering = true;
    title.classList.add("liquid-active");
    if (!running) {
      running = true;
      tick();
    }
  });
  title.addEventListener("mouseleave", () => {
    hovering = false;
  });
  title.addEventListener("mousemove", (event) => {
    if (!hovering) return;
    const rect = title.getBoundingClientRect();
    const nx = (event.clientX - rect.left) / rect.width;
    const ny = (event.clientY - rect.top) / rect.height;
    noise.setAttribute(
      "baseFrequency",
      `${(0.01 + nx * 0.015).toFixed(4)} ${(0.015 + ny * 0.01).toFixed(4)}`
    );
  });
})();

// Wordmark dropdown menu (Tia's "Tia ▾").
const navBrandMenu = document.querySelector("#navBrandMenu");
const navDropdown = document.querySelector("#navDropdown");
const navItemsAll = () => [...navDropdown.querySelectorAll(".dropdown-item")].filter((el) => !el.hidden);

function setNavMenu(open, { focusFirst = false } = {}) {
  navDropdown.classList.toggle("show", open);
  navBrandMenu.setAttribute("aria-expanded", String(open));
  if (open && focusFirst) navItemsAll()[0].focus();
}

navBrandMenu.addEventListener("click", (event) => {
  event.stopPropagation();
  setNavMenu(!navDropdown.classList.contains("show"), { focusFirst: event.detail === 0 });
});

navDropdown.addEventListener("keydown", (event) => {
  const navItems = navItemsAll();
  const index = navItems.indexOf(document.activeElement);
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const step = event.key === "ArrowDown" ? 1 : -1;
    navItems[(index + step + navItems.length) % navItems.length].focus();
  } else if (event.key === "Home" || event.key === "End") {
    event.preventDefault();
    navItems[event.key === "Home" ? 0 : navItems.length - 1].focus();
  } else if (event.key === "Escape") {
    setNavMenu(false);
    navBrandMenu.focus();
  }
});

// Tabbing (or otherwise moving focus) out of the menu closes it.
document.querySelector(".nav-menu-container").addEventListener("focusout", (event) => {
  if (!event.currentTarget.contains(event.relatedTarget)) setNavMenu(false);
});

document.addEventListener("click", (event) => {
  if (navDropdown.classList.contains("show") && !event.target.closest(".nav-menu-container")) {
    setNavMenu(false);
  }
});

const helpDialog = document.querySelector("#diaHelpDialog");

function openHelp() {
  if (!helpDialog.open) helpDialog.showModal();
}

// --- Command palette (Cmd/Ctrl+K) ---
// Built fresh for DIA in Tia's visual language. Every command drives an
// existing control, so it behaves exactly like clicking that button. Commands
// that are not available right now (e.g. exports before a run) are hidden.

const palette = document.querySelector("#commandPalette");
const paletteInput = document.querySelector("#paletteInput");
const paletteList = document.querySelector("#paletteList");
const paletteTrigger = document.querySelector("#paletteTrigger");
const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
const modKey = isMac ? "⌘" : "Ctrl";

document.querySelectorAll("kbd.kbd-hint").forEach((kbd) => {
  if (kbd.textContent === "⌘K") kbd.textContent = `${modKey} K`;
});

// Donate: one config value (DIA_DONATE_URL on the server, served by /api/config). Unset = every Donate item hidden.
let donateUrl = null;
function openDonate() {
  if (donateUrl) window.open(donateUrl, "_blank", "noopener,noreferrer");
}
// Second Support option: Interac e-Transfer. Copies the payee address and confirms in the top-centre toast.
const INTERAC_EMAIL = "payments@bashsystems.net";
async function donateInterac() {
  let copied = true;
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(INTERAC_EMAIL);
    else copyTextFallback(INTERAC_EMAIL);
  } catch (error) {
    try { copyTextFallback(INTERAC_EMAIL); } catch (fallbackError) { copied = false; }
  }
  showToast(copied
    ? `Copied ${INTERAC_EMAIL}. Send an Interac e-Transfer from your bank`
    : `Couldn't copy. Send an Interac e-Transfer to ${INTERAC_EMAIL} from your bank`, [], { duration: 6000 });
}
fetch("/api/config")
  .then((response) => (response.ok ? response.json() : {}))
  .then((config) => {
    const url = typeof config.donateUrl === "string" && /^https:\/\//.test(config.donateUrl) ? config.donateUrl : null;
    donateUrl = url;
    const link = document.querySelector("#navDonate");
    if (link) { link.hidden = !url; if (url) link.href = url; }
  })
  .catch(() => { /* no config: Donate stays hidden */ });

const clickable = (selector) => () => {
  const el = document.querySelector(selector);
  return Boolean(el) && !el.disabled && !el.hidden;
};
const press = (selector) => () => document.querySelector(selector).click();
const hasResults = () => latestPayload !== null;

const commands = [
  { id: "new-intake", label: "New intake", hint: "Clear questionnaire", run: newIntake },
  { id: "load-sample", label: "Load sample questionnaire", run: press("#loadSample") },
  { id: "run-intake", label: "Run intake", hint: `${modKey} Enter`, enabled: () => !runAgent.disabled && questionnaire.value.trim() !== "", run: () => runAgent.click() },
  { id: "edit-intake", label: "Edit intake", enabled: () => !intakeToggle.hidden && intakeBody.hidden, run: () => { setIntakeCollapsed(false); questionnaire.focus(); } },
  { id: "focus", label: "Focus the input", hint: "/", run: focusComposer },
  { id: "ask", label: "Ask DIA", enabled: () => assistantMode !== "off", run: openAgent },
  { id: "dictate", label: "Dictate a message", enabled: () => assistantMode !== "off" && clickable("#micButton")(), run: () => { openAgent(); micButton.click(); } },
  { id: "conversation", label: "Toggle conversation mode", enabled: () => assistantMode !== "off" && clickable("#conversationMode")(), run: () => { openAgent(); conversationMode.click(); } },
  { id: "tab-profile", label: "View client profile", enabled: hasResults, run: () => selectTab(document.querySelector("#tab-profile"), { focus: true }) },
  { id: "tab-analysis", label: "View opportunity analysis", enabled: hasResults, run: () => selectTab(document.querySelector("#tab-analysis"), { focus: true }) },
  { id: "tab-proposal", label: "View proposal draft", enabled: hasResults, run: () => selectTab(document.querySelector("#tab-proposal"), { focus: true }) },
  { id: "copy-all", label: "Copy all outputs", enabled: clickable("#copyAll"), run: press("#copyAll") },
  { id: "download-md", label: "Download Markdown", enabled: clickable("#downloadMarkdown"), run: press("#downloadMarkdown") },
  { id: "download-json", label: "Download JSON", enabled: clickable("#downloadJson"), run: press("#downloadJson") },
  { id: "download-pdf", label: "Download PDF", hint: "Formatted report", enabled: clickable("#downloadPdf"), run: press("#downloadPdf") },
  { id: "upload", label: "Upload intake", hint: `${modKey} O`, run: () => openPicker() },
  { id: "cabinet", label: "Open filing cabinet", hint: `${modKey} Shift F`, run: () => openDrawer() },
  { id: "export-selected", label: "Export selected", hint: ".zip", enabled: () => readCabinet().length > 0, run: () => (cabinetSelected.size ? (openDrawer({ select: true }), exportSelected()) : openDrawer({ select: true })) },
  { id: "draft-load", label: "Load document into Draft Editor", enabled: clickable("#draftLoad"), run: () => { draftLoad.click(); draftEditor.focus(); } },
  { id: "share-x", label: "Share score on X", enabled: clickable("#shareX"), run: press("#shareX") },
  { id: "theme", label: "Toggle light / dark mode", run: press("#themeToggle") },
  { id: "icons-line", label: "Icon style: Line", run: () => setIconStyle("line") },
  { id: "icons-zelda", label: "Icon style: Zelda", run: () => setIconStyle("zelda") },
  { id: "icons-pixel", label: "Icon style: Dia pixel", run: () => setIconStyle("dia-pixel") },
  { id: "help", label: "Learn how to use DIA", run: openHelp },
  { id: "donate", label: "Donate", hint: "Opens in a new tab", enabled: () => Boolean(donateUrl), run: openDonate },
  { id: "donate-interac", label: "Donate by Interac e-Transfer", hint: `Copies ${INTERAC_EMAIL}`, run: donateInterac },
];

let visibleCommands = [];
let activeIndex = 0;
let paletteReturnFocus = null;

function isEnabled(command) {
  return command.enabled ? command.enabled() : true;
}

function renderPalette() {
  const query = paletteInput.value.trim().toLowerCase();
  visibleCommands = commands.filter(
    (command) =>
      isEnabled(command) &&
      `${command.label} ${command.hint || ""}`.toLowerCase().includes(query)
  );
  if (activeIndex >= visibleCommands.length) activeIndex = 0;
  paletteList.replaceChildren();
  if (!visibleCommands.length) {
    const empty = document.createElement("li");
    empty.className = "palette-empty";
    empty.textContent = "No matching commands";
    paletteList.append(empty);
    paletteInput.removeAttribute("aria-activedescendant");
    paletteInput.setAttribute("aria-expanded", "false");
    return;
  }
  paletteInput.setAttribute("aria-expanded", "true");
  visibleCommands.forEach((command, index) => {
    const item = document.createElement("li");
    item.className = "palette-item";
    item.id = `cmd-${command.id}`;
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", String(index === activeIndex));
    const label = document.createElement("span");
    label.textContent = command.label;
    item.append(label);
    if (command.hint) {
      const hint = document.createElement("small");
      hint.textContent = command.hint;
      item.append(hint);
    }
    item.addEventListener("mousemove", () => {
      if (activeIndex !== index) {
        activeIndex = index;
        updateActive();
      }
    });
    item.addEventListener("click", () => runCommand(command));
    paletteList.append(item);
  });
  updateActive();
}

function updateActive() {
  paletteList.querySelectorAll(".palette-item").forEach((item, index) => {
    item.setAttribute("aria-selected", String(index === activeIndex));
  });
  const active = visibleCommands[activeIndex];
  if (active) {
    paletteInput.setAttribute("aria-activedescendant", `cmd-${active.id}`);
    document.querySelector(`#cmd-${active.id}`)?.scrollIntoView({ block: "nearest" });
  }
}

// Where Escape returns focus. Menus hide as the palette opens, so a row inside one maps to the button that opened it
// (Codex review on PR #32: focus was lost to a hidden DIA menu row).
function paletteReturnTarget(el) {
  if (!el || el === document.body || palette.contains(el)) return null;
  if (navDropdown.contains(el)) return navBrandMenu;
  const plusMenu = el.closest?.("[id$='PlusMenu']");
  if (plusMenu) return document.querySelector(`#${plusMenu.id.replace(/Menu$/, "Button")}`) || null;
  return el;
}

function canTakeFocus(el) {
  return Boolean(el && el.isConnected && !el.closest("[hidden], [inert]") && el.getClientRects().length);
}

function openPalette() {
  if (palette.open) return;
  paletteReturnFocus = paletteReturnTarget(document.activeElement);
  setNavMenu(false);
  if (helpDialog.open) helpDialog.close();
  paletteInput.value = "";
  activeIndex = 0;
  renderPalette();
  palette.showModal();
  paletteInput.focus();
}

function closePalette({ restoreFocus = true } = {}) {
  if (!palette.open) return;
  palette.close();
  paletteInput.setAttribute("aria-expanded", "false");
  if (!restoreFocus) return;
  const target = canTakeFocus(paletteReturnFocus) ? paletteReturnFocus : paletteTrigger;
  if (canTakeFocus(target)) target.focus();
  else paletteInput.blur(); // never leave focus inside the closed dialog
}

function runCommand(command) {
  if (!command || !isEnabled(command)) return;
  closePalette({ restoreFocus: false });
  command.run();
}

// --- Plus menu (Actions): glyph composer-icons.md 6, flows v8.8 2.7 ---
// Rows come from the palette's command registry (runCommand), grouped Add / Go to / View / Help, then Support (Donate,
// only when DIA_DONATE_URL resolves) and All commands. Icon style is an inline 3-way menuitemradio row; it and Dark theme
// keep the menu open, everything else closes it. Keyboard: Enter/Space/ArrowUp open on the first row, ArrowDown on the
// last; Up/Down wrap (Left/Right inside the segmented row), Home/End, type-ahead, Esc returns focus to the plus, Tab
// closes. A click or tap toggles; an outside click closes. Only one popover at a time (Popovers).
const PLUS_GROUPS = [
  { label: "Add", rows: [{ id: "upload", text: "Attach file", icon: "menu-attach" }, { id: "load-sample", text: "Load sample", icon: "menu-sample" }] },
  { label: "Go to", rows: [
    { id: "tray", text: "Tray", icon: "tray", run: () => trayButton.click() },
    { id: "cabinet", text: "Cabinet", icon: "cabinet" },
    { id: "ask", text: "Ask DIA", icon: "menu-ask", hideIn: "agent" },
  ] },
  { label: "View", rows: [{ kind: "icon-style" }, { id: "theme", text: "Dark theme", icon: "menu-theme", kind: "theme" }] },
  { label: "Help", rows: [{ id: "help", text: "Learn how to use DIA", icon: "menu-help" }] },
  { label: "Support", rows: [
    { id: "donate", text: "Donate", icon: "menu-donate", kind: "link" },
    { id: "donate-interac", text: "Donate by Interac e-Transfer", icon: "menu-donate" }, // copies the address; no ↗
  ] },
  { label: "Commands", rows: [{ id: "palette", text: "All commands", icon: "menu-commands", key: `${modKey === "⌘" ? "⌘" : "Ctrl "}K`, run: () => openPalette() }] },
];
const ICON_STYLE_OPTIONS = [["line", "Line"], ["zelda", "Zelda"], ["dia-pixel", "Dia pixel"]];
function createPlusMenu(prefix) {
  const button = document.querySelector(`#${prefix}PlusButton`);
  const menu = document.querySelector(`#${prefix}PlusMenu`);
  if (!button || !menu) return null;
  let open = false;
  const items = () => [...menu.querySelectorAll(".pm-item")];
  const keepsOpen = (row) => row.kind === "theme";
  const isDark = () => document.documentElement.dataset.theme !== "light";
  function iconSpan(name) {
    const icon = document.createElement("span");
    icon.className = "icon pm-icon";
    icon.dataset.icon = name;
    icon.setAttribute("aria-hidden", "true");
    return icon;
  }
  function makeItem(row) {
    const command = commands.find((c) => c.id === row.id);
    const el = document.createElement(row.kind === "link" ? "a" : "button");
    el.className = "pm-item";
    el.tabIndex = -1;
    if (row.kind === "link") {
      el.href = donateUrl;
      el.target = "_blank";
      el.rel = "noopener noreferrer";
      el.setAttribute("role", "menuitem");
      el.setAttribute("aria-label", `${row.text} (opens in a new tab)`);
    } else {
      el.type = "button";
      el.setAttribute("role", row.kind === "theme" ? "menuitemcheckbox" : "menuitem");
      if (row.kind === "theme") el.setAttribute("aria-checked", String(isDark()));
    }
    const label = document.createElement("span");
    label.className = "pm-label";
    label.textContent = row.text;
    el.append(iconSpan(row.icon), label);
    if (row.kind === "theme") {
      const box = document.createElement("span");
      box.className = "pm-check";
      box.setAttribute("aria-hidden", "true");
      el.append(box);
    } else if (row.kind === "link") {
      const ext = document.createElement("span");
      ext.className = "pm-ext";
      ext.setAttribute("aria-hidden", "true");
      el.append(ext);
    } else if (row.key) {
      const key = document.createElement("kbd");
      key.className = "pm-key";
      key.textContent = row.key;
      el.append(key);
      el.setAttribute("aria-keyshortcuts", "Control+K Meta+K");
    }
    const disabled = command && !isEnabled(command) && row.kind !== "link";
    if (disabled) {
      el.setAttribute("aria-disabled", "true");
      const note = document.createElement("span");
      note.className = "pm-note";
      note.textContent = "Not available yet";
      el.append(note);
    }
    el.addEventListener("click", (event) => {
      if (disabled) { event.preventDefault(); return; }
      if (row.kind === "link") { hide(); return; } // the link itself opens the new tab (target=_blank, rel=noopener)
      event.preventDefault();
      if (keepsOpen(row)) {
        runCommand(command);
        el.setAttribute("aria-checked", String(isDark()));
        renderIcons();
        return;
      }
      hide({ returnFocus: true });
      row.run ? row.run() : runCommand(command);
    });
    return el;
  }
  function iconStyleRows() {
    const labelRow = document.createElement("div");
    labelRow.className = "pm-label-row";
    labelRow.setAttribute("aria-hidden", "true");
    const text = document.createElement("span");
    text.textContent = "Icon style";
    labelRow.append(iconSpan("menu-icon-style"), text);
    const seg = document.createElement("div");
    seg.className = "pm-seg";
    seg.setAttribute("role", "group");
    seg.setAttribute("aria-label", "Icon style");
    ICON_STYLE_OPTIONS.forEach(([value, name]) => {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "pm-item";
      option.tabIndex = -1;
      option.dataset.style = value;
      option.setAttribute("role", "menuitemradio");
      option.setAttribute("aria-checked", String(iconStyle === value));
      option.setAttribute("aria-label", `Icon style: ${name}`);
      option.textContent = name;
      option.addEventListener("click", () => {
        setIconStyle(value);
        seg.querySelectorAll("[role=menuitemradio]").forEach((o) => o.setAttribute("aria-checked", String(o.dataset.style === value)));
        option.focus();
      });
      seg.append(option);
    });
    return [labelRow, seg];
  }
  // Layout (landing polish, Glyph): every group but the last scrolls in .pm-scroll; the last group (All commands) is a
  // sticky .pm-foot row, so it stays visible when the capped menu has to scroll. DOM order (and so keyboard order) is
  // unchanged.
  const scroller = document.createElement("div");
  scroller.className = "pm-scroll";
  scroller.setAttribute("role", "none");
  const syncFade = () => {
    const more = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight > 2;   // Tia's updatePlusScrollCue threshold
    if (more) scroller.dataset.more = "true"; else delete scroller.dataset.more;
  };
  scroller.addEventListener("scroll", syncFade, { passive: true });
  // Rows rise 4px as they fade in (a transform counts toward scroll overflow), so re-check once they settle.
  menu.addEventListener("transitionend", syncFade);
  function render() {
    menu.replaceChildren();
    scroller.replaceChildren();
    const rows = [];
    const groups = PLUS_GROUPS.map((group) => ({ group, visible: group.rows.filter((row) => !(row.hideIn === prefix) && !(row.kind === "link" && !donateUrl) && !(row.id === "ask" && assistantMode !== "guided")) }))
      .filter(({ visible }) => visible.length);
    groups.forEach(({ group, visible }, groupIndex) => {
      const isFoot = groups.length > 1 && groupIndex === groups.length - 1;
      const target = isFoot ? menu : scroller;
      if (groupIndex === 0) menu.append(scroller);
      if (groupIndex > 0) {
        const sep = document.createElement("div");
        sep.className = "pm-sep";
        sep.setAttribute("role", "separator");
        target.append(sep);
      }
      const box = document.createElement("div");
      box.setAttribute("role", "group");
      box.setAttribute("aria-label", group.label);
      visible.forEach((row) => {
        (row.kind === "icon-style" ? iconStyleRows() : [makeItem(row)]).forEach((node) => {
          const wrap = document.createElement("div");
          wrap.className = "pm-row";
          wrap.setAttribute("role", "none");
          wrap.append(node);
          box.append(wrap);
          rows.push(wrap);
        });
      });
      if (isFoot) box.classList.add("pm-foot");
      target.append(box);
    });
    // 15ms stagger, the bottom row (nearest the plus) first.
    rows.forEach((wrap, index) => wrap.style.setProperty("--r", String(rows.length - 1 - index)));
    renderIcons();
  }
  function focusItem(index) {
    const list = items();
    if (list.length) list[(index + list.length) % list.length].focus();
  }
  // Viewport guard (landing polish, Glyph review): open upward by default, never over the 52px letterhead band (top
  // clamped 8px below it); flip below the bar only when there's more room there. If it still doesn't fit, cap the
  // height at the space left: the middle (.pm-scroll) scrolls with a bottom-edge fade, All commands stays as a footer.
  // Phones (<=600px) use the fixed bottom sheet, so no guard there.
  const MENU_GAP = 10;
  const MENU_MARGIN = 8;
  const HEADER_BAND = 52;
  const BAR_LIFT = 2;   // the bar rises 2px on hover/focus (style.css), carrying the menu with it
  function placeMenu() {
    delete menu.dataset.drop;
    menu.style.removeProperty("max-height");
    if (!matchMedia("(max-width: 600px)").matches) {
      const anchor = (menu.offsetParent || button).getBoundingClientRect();
      const header = document.querySelector("header.lh");
      const band = Math.max(HEADER_BAND, header ? header.getBoundingClientRect().bottom : 0);
      const height = menu.offsetHeight;
      const above = anchor.top - MENU_GAP - (band + MENU_MARGIN) - BAR_LIFT;
      const below = window.innerHeight - anchor.bottom - MENU_GAP - MENU_MARGIN;
      if (height > above) {
        if (height <= below || below > above) menu.dataset.drop = "down";
        const room = Math.max(above, below);
        if (height > room) menu.style.maxHeight = `${Math.max(room, 120)}px`;
      }
    }
    syncFade();
  }
  function show({ focus = null } = {}) {
    if (!open) {
      Popovers.closeOthers(api);
      render();
      placeMenu();
      open = true;
      button.setAttribute("aria-expanded", "true");
    }
    if (focus !== null) focusItem(focus);
  }
  function hide({ returnFocus = false } = {}) {
    if (!open) return;
    open = false;
    button.setAttribute("aria-expanded", "false");
    if (returnFocus) button.focus();
  }
  window.addEventListener("resize", () => { if (open) placeMenu(); });
  const api = { isOpen: () => open, show, hide };
  button.addEventListener("click", (event) => { open ? hide() : show({ focus: event.detail === 0 ? 0 : null }); });
  button.addEventListener("keydown", (event) => {
    if (event.key === "ArrowUp") { event.preventDefault(); show({ focus: 0 }); }
    else if (event.key === "ArrowDown") { event.preventDefault(); show({ focus: -1 }); }
    else if (event.key === "Escape" && open) { event.preventDefault(); event.stopPropagation(); hide({ returnFocus: true }); }
  });
  menu.addEventListener("keydown", (event) => {
    const list = items();
    const current = document.activeElement;
    const index = list.indexOf(current);
    const inSeg = current?.closest(".pm-seg");
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const down = event.key === "ArrowDown";
      if (inSeg) {
        // leave the segmented row as one stop
        const segItems = [...inSeg.querySelectorAll(".pm-item")];
        const edge = down ? list.indexOf(segItems[segItems.length - 1]) + 1 : list.indexOf(segItems[0]) - 1;
        focusItem(edge);
      } else {
        let next = index + (down ? 1 : -1);
        const target = list[(next + list.length) % list.length];
        const seg = target?.closest(".pm-seg");
        if (seg) { const checked = seg.querySelector("[aria-checked=true]") || seg.querySelector(".pm-item"); checked.focus(); return; }
        focusItem(next);
      }
    } else if ((event.key === "ArrowLeft" || event.key === "ArrowRight") && inSeg) {
      event.preventDefault();
      const segItems = [...inSeg.querySelectorAll(".pm-item")];
      const i = segItems.indexOf(current) + (event.key === "ArrowRight" ? 1 : -1);
      segItems[(i + segItems.length) % segItems.length].focus();
    } else if (event.key === "Home" || event.key === "End") { event.preventDefault(); focusItem(event.key === "Home" ? 0 : -1); }
    else if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); hide({ returnFocus: true }); }
    else if (event.key === "Tab") hide();
    else if (event.key.length === 1 && /\S/.test(event.key) && !event.metaKey && !event.ctrlKey && !event.altKey) {
      const letter = event.key.toLowerCase();
      const order = [...list.slice(index + 1), ...list.slice(0, index + 1)];
      const match = order.find((el) => el.textContent.trim().toLowerCase().startsWith(letter));
      if (match) { event.preventDefault(); match.focus(); }
    }
  });
  document.addEventListener("pointerdown", (event) => { if (open && !menu.contains(event.target) && !button.contains(event.target)) hide(); });
  return Popovers.register(api);
}
const PlusMenus = { intake: createPlusMenu("intake"), agent: createPlusMenu("agent") };

paletteInput.addEventListener("input", () => {
  activeIndex = 0;
  renderPalette();
});

paletteInput.addEventListener("keydown", (event) => {
  if (!visibleCommands.length) return;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const step = event.key === "ArrowDown" ? 1 : -1;
    activeIndex = (activeIndex + step + visibleCommands.length) % visibleCommands.length;
    updateActive();
  } else if (event.key === "Home" || event.key === "End") {
    event.preventDefault();
    activeIndex = event.key === "Home" ? 0 : visibleCommands.length - 1;
    updateActive();
  } else if (event.key === "Enter") {
    event.preventDefault();
    runCommand(visibleCommands[activeIndex]);
  }
});

// Esc: the native dialog fires "cancel"; route it through closePalette so
// focus returns to where the user was.
palette.addEventListener("cancel", (event) => {
  event.preventDefault();
  closePalette();
});

// Clicking the backdrop (outside the panel) closes the palette.
palette.addEventListener("click", (event) => {
  if (event.target === palette) closePalette();
});

paletteTrigger.addEventListener("click", openPalette);

document.querySelectorAll("[data-command]").forEach((el) => {
  el.addEventListener("click", () => {
    const id = el.dataset.command;
    setNavMenu(false);
    if (id === "palette") {
      openPalette();
      return;
    }
    runCommand(commands.find((command) => command.id === id));
  });
});

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === "k") {
    event.preventDefault();
    if (palette.open) {
      closePalette();
    } else {
      openPalette();
    }
    return;
  }
  if (event.key === "Escape" && navDropdown.classList.contains("show")) {
    setNavMenu(false);
    navBrandMenu.focus();
    return;
  }
  const target = event.target;
  const typing =
    target instanceof HTMLElement &&
    (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));
  if (event.key === "/" && !typing && !palette.open && !helpDialog.open) {
    event.preventDefault();
    focusComposer();
  }
});


// ===========================================================================
// Intake tray, filing cabinet, icon styles. Spec: proto/flows.md v7 + Ikenna.
// ===========================================================================

// --- Product switches (one place each) -------------------------------------
// What happens when a 16th intake is filed. Ikenna chose "drop-oldest" (with an
// Undo / Export it toast). "block" would refuse to file until one is removed.
const CABINET_CAP_POLICY = "drop-oldest";
// Several files dropped at once merge into one questionnaire (Ikenna, flows.md v8.9 §2.3).
// The merge lives only in the combineExtracted function.
const CABINET_MAX = 15;
const CABINET_PAGE = 5;
const MAX_FILES_PER_DROP = 10;
const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;
const UPLOAD_TYPES = [".txt", ".md", ".markdown", ".text", ".csv", ".tsv", ".json", ".rtf", ".docx", ".doc"];
const prefersReducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const isMobile = () => window.matchMedia("(max-width: 600px)").matches;

// --- Icon styles: one source file per icon, per set ------------------------
// line: inline SVG (so it follows CSS tokens); zelda / pixel: <img> sprites at
// a whole-number 32px, swapped per theme (never recoloured).
const ICON_STYLE_KEY = "dia-icon-style";
const ICON_SETS = {
  line: { label: "Line", inline: true, dir: { light: "line", dark: "line" } },
  zelda: { label: "Zelda", inline: false, dir: { light: "zelda", dark: "zelda" } },
  "dia-pixel": { label: "Dia pixel", inline: false, dir: { light: "pixel", dark: "pixel-dark" } },
};
const iconCache = new Map();
let iconStyle = "line";
try {
  const saved = localStorage.getItem(ICON_STYLE_KEY);
  if (saved && ICON_SETS[saved]) iconStyle = saved;
} catch (error) { /* storage blocked: default to line */ }

async function iconMarkup(url) {
  if (!iconCache.has(url)) {
    iconCache.set(url, fetch(url).then((r) => (r.ok ? r.text() : "")).then((svg) =>
      // Decorative: strip ids/titles so repeated inlining never collides.
      svg.replace(/<title[\s\S]*?<\/title>/g, "").replace(/\s(id|aria-labelledby)="[^"]*"/g, "")
    ).catch(() => ""));
  }
  return iconCache.get(url);
}

async function renderIcons() {
  const theme = document.documentElement.dataset.theme === "light" ? "light" : "dark";
  document.documentElement.dataset.iconStyle = iconStyle;
  for (const slot of document.querySelectorAll("[data-icon]")) {
    // data-icon-set pins a slot to one style (the setting's previews).
    const set = ICON_SETS[slot.dataset.iconSet] || ICON_SETS[iconStyle];
    const url = `/static/icons/${set.dir[theme]}/${slot.dataset.icon}.svg`;
    if (slot.dataset.src === url) continue;
    slot.dataset.src = url;
    if (set.inline) {
      const markup = await iconMarkup(url);
      if (slot.dataset.src !== url) continue; // a newer render (state change) already won
      slot.innerHTML = markup;
      slot.querySelector("svg")?.setAttribute("focusable", "false");
    } else {
      slot.innerHTML = `<img class="px" src="${url}" width="32" height="32" alt="" decoding="async">`;
    }
  }
  document.querySelectorAll("[data-icon-style]").forEach((item) => {
    const on = item.dataset.iconStyle === iconStyle;
    item.setAttribute("aria-checked", String(on));
    if (item.getAttribute("role") === "radio") item.tabIndex = on ? 0 : -1;
  });
}

function setIconStyle(style) {
  if (!ICON_SETS[style]) return;
  iconStyle = style;
  try { localStorage.setItem(ICON_STYLE_KEY, style); } catch (error) { /* session only */ }
  renderIcons(); // aria-checked announces the change; no extra live region (flows.md 7a.3)
}

// Stateful icon slots (Glyph's composer/voice set): data-icon-on / -off / -disabled pick the glyph
// from the owning button's aria-pressed / aria-checked / aria-disabled, in every Icon style.
function syncStateIcons(slots = document.querySelectorAll("[data-icon-on]")) {
  let changed = false;
  slots.forEach((slot) => {
    const button = slot.closest("button");
    if (!button) return;
    const on = button.getAttribute("aria-pressed") === "true" || button.getAttribute("aria-checked") === "true" || button.dataset.speaking === "true";
    const disabled = button.getAttribute("aria-disabled") === "true" && slot.dataset.iconDisabled;
    const name = disabled ? slot.dataset.iconDisabled : on ? slot.dataset.iconOn : slot.dataset.iconOff;
    if (name && slot.dataset.icon !== name) { slot.dataset.icon = name; changed = true; }
  });
  if (changed) renderIcons();
}
{
  const stateObserver = new MutationObserver(() => syncStateIcons());
  document.querySelectorAll("[data-icon-on]").forEach((slot) => {
    const button = slot.closest("button");
    if (button) stateObserver.observe(button, { attributes: true, attributeFilter: ["aria-pressed", "aria-checked", "aria-disabled", "data-speaking"] });
  });
  syncStateIcons();
}

// Help & settings: segmented radiogroup (roving tabindex; arrows move and select).
const iconStyleOptions = [...document.querySelectorAll(".icon-style-option[data-icon-style]")];
iconStyleOptions.forEach((option, index) => {
  option.addEventListener("click", () => setIconStyle(option.dataset.iconStyle));
  option.addEventListener("keydown", (event) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (!step) return;
    event.preventDefault();
    const next = iconStyleOptions[(index + step + iconStyleOptions.length) % iconStyleOptions.length];
    setIconStyle(next.dataset.iconStyle);
    next.focus();
  });
});

new MutationObserver(renderIcons).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
renderIcons();

// --- Toast --------------------------------------------------------------------
const toast = document.querySelector("#toast");
const toastText = document.querySelector("#toastText");
const toastActions = document.querySelector("#toastActions");
let toastTimer = null;
let toastDismiss = null;  // runs when the toast goes away without one of its actions (timer, ×, replaced, page left)

function settleToast() {
  const dismiss = toastDismiss;
  toastDismiss = null;
  dismiss?.();
}
function showToast(message, actions = [], { duration = 10000, onDismiss = null } = {}) {
  clearTimeout(toastTimer);
  settleToast();
  toastText.textContent = message;
  toastActions.innerHTML = "";
  for (const action of actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "toast-action";
    button.textContent = action.label;
    button.addEventListener("click", () => { toastDismiss = null; hideToast(); action.run(); });
    toastActions.appendChild(button);
  }
  // A modal dialog (the cabinet drawer) makes the rest of the page inert, so the toast joins it while it is open;
  // otherwise Remove's Undo could not be reached (v1.1.2: keep Remove's confirmation).
  const host = [...document.querySelectorAll("dialog[open]")].filter((d) => d.matches(":modal")).pop() || document.body;
  if (toast.parentElement !== host) host.appendChild(toast);
  toast.hidden = false;
  toastDismiss = onDismiss;
  toastTimer = setTimeout(hideToast, duration);
}
function hideToast() {
  clearTimeout(toastTimer);
  toast.hidden = true;
  settleToast();
}
document.querySelector("#toastClose").addEventListener("click", hideToast);
toast.addEventListener("mouseenter", () => clearTimeout(toastTimer));
toast.addEventListener("mouseleave", () => { toastTimer = setTimeout(hideToast, 4000); });

// --- Intake tray (upload) -----------------------------------------------------
const trayButton = document.querySelector("#trayButton");
const trayInput = document.querySelector("#trayInput");
const uploadLink = document.querySelector("#uploadLink");
const uploadCancel = document.querySelector("#uploadCancel");
const intakeComposer = document.querySelector("#intakeComposer");
const intakeError = document.querySelector("#intakeError");
const fileChips = document.querySelector("#fileChips");
let uploadController = null;
let pickerReturnFocus = null;

const fileSuffix = (name) => {
  const dot = name.lastIndexOf(".");
  return dot <= 0 ? "" : name.slice(dot).toLowerCase();
};
const typeMessage = (name) => {
  const suffix = fileSuffix(name);
  return `DIA can't read ${suffix || "files without an extension"} yet. Try .txt, .md, .csv, .docx, .doc, .rtf or .json.`;
};
const SIZE_MESSAGE = "That file is over 5 MB. Try a smaller export of the questionnaire.";
const NETWORK_MESSAGE = "Couldn't reach DIA. Check that it's running, then try again.";

function showIntakeError(lines, { retry } = {}) {
  intakeError.innerHTML = "";
  const list = Array.isArray(lines) ? lines : [lines];
  for (const line of list) {
    const row = document.createElement("p");
    row.textContent = line;
    intakeError.appendChild(row);
  }
  if (retry) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "text-link";
    button.textContent = "Try again";
    button.addEventListener("click", retry);
    intakeError.appendChild(button);
  }
  intakeError.hidden = false;
}
function clearIntakeError() {
  intakeError.hidden = true;
  intakeError.innerHTML = "";
}
questionnaire.addEventListener("input", clearIntakeError);
// Editing after a run (or a failed one) means the intake is idle again: back to a green Ready.
questionnaire.addEventListener("input", () => { if (statusLine.dataset.state !== "ready" && statusLine.dataset.state !== "processing") setRunState("ready"); });

function renderChips(files) {
  fileChips.innerHTML = "";
  for (const file of files) {
    const chip = document.createElement("span");
    chip.className = "file-chip";
    chip.textContent = `${file.filename} · ${file.characters.toLocaleString()} characters`;
    const clear = document.createElement("button");
    clear.type = "button";
    clear.className = "chip-x";
    clear.textContent = "×";
    clear.setAttribute("aria-label", `Dismiss ${file.filename}`);
    clear.addEventListener("click", () => {
      chip.remove();
      if (!fileChips.children.length) fileChips.hidden = true;
    });
    chip.appendChild(clear);
    fileChips.appendChild(chip);
  }
  fileChips.hidden = files.length === 0;
}

function setTrayBusy(busy) {
  trayButton.classList.toggle("busy", busy);
  trayButton.setAttribute("aria-busy", String(busy));
  intakeComposer.classList.toggle("is-reading", busy);
  questionnaire.readOnly = busy;
  runAgent.disabled = busy;
  uploadCancel.hidden = !busy;
  uploadLink.hidden = busy;
}

async function extractOne(file, signal) {
  const response = await fetch("/api/extract", {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name) },
    body: file,
    signal,
  });
  let payload = null;
  try { payload = await response.json(); } catch (error) { payload = null; }
  if (!response.ok) {
    if (!payload || !payload.error) throw Object.assign(new Error(NETWORK_MESSAGE), { network: true });
    throw new Error(payload.error);
  }
  return payload;
}

// Multi-file merge (decided, flows.md v8.9 §2.3): the only place files are joined, kept in one
// function as a cheap safeguard so it could become a queue later.
function combineExtracted(existing, parts) {
  const blocks = parts.map((part) => (parts.length > 1 ? `<!-- file: ${part.filename} -->\n${part.questionnaire.trim()}` : part.questionnaire.trim()));
  const joined = blocks.join("\n\n---\n\n");
  return existing.trim() ? `${existing.trimEnd()}\n\n---\n\n${joined}\n` : `${joined}\n`;
}

async function loadIntakeFiles(fileList) {
  let files = [...(fileList || [])];
  if (!files.length) return;
  clearIntakeError();
  const notes = [];
  if (files.length > MAX_FILES_PER_DROP) {
    notes.push(`Only the first ${MAX_FILES_PER_DROP} files were read.`);
    files = files.slice(0, MAX_FILES_PER_DROP);
  }
  const failures = [];
  const accepted = files.filter((file) => {
    if (!UPLOAD_TYPES.includes(fileSuffix(file.name))) { failures.push(`${file.name}: ${typeMessage(file.name)}`); return false; }
    if (file.size > MAX_UPLOAD_BYTES) { failures.push(`${file.name}: ${SIZE_MESSAGE}`); return false; }
    return true;
  });
  if (!accepted.length) {
    showIntakeError(files.length === 1 ? failures[0].replace(/^[^:]+: /, "") : failures);
    return;
  }

  const before = questionnaire.value;
  uploadController = new AbortController();
  setTrayBusy(true);
  const parts = [];
  let networkFailure = false;
  try {
    setRunState("processing", accepted.length === 1 ? `Reading ${accepted[0].name}…` : `Reading 1 of ${accepted.length}…`);
    for (const [index, file] of accepted.entries()) {
      if (accepted.length > 1) statusText.textContent = `Reading ${index + 1} of ${accepted.length}…`;
      try {
        const payload = await extractOne(file, uploadController.signal);
        parts.push({ filename: file.name, questionnaire: payload.questionnaire, characters: payload.characters });
      } catch (error) {
        if (error.name === "AbortError") throw error;
        if (error.network || error instanceof TypeError) networkFailure = true;
        failures.push(`${file.name}: ${error instanceof TypeError ? NETWORK_MESSAGE : error.message}`);
      }
    }
  } catch (error) {
    if (error.name === "AbortError") {
      if (!parts.length) questionnaire.value = before;
      setRunState("ready", "Upload cancelled");
    }
  } finally {
    setTrayBusy(false);
    uploadController = null;
  }
  if (parts.length) {
    questionnaire.value = combineExtracted(before, parts);
    if (intakeBody.hidden) setIntakeCollapsed(false);
    autosizeComposer(questionnaire);
    renderChips(parts);
    InputsPrompt.schedule();
    questionnaire.focus();
    questionnaire.setSelectionRange(0, 0);
    questionnaire.scrollTop = 0;
    const first = parts[0];
    const appended = before.trim() ? " Added to your existing text." : "";
    setRunState("complete", parts.length === 1
      ? `File read: ${first.filename}, ${first.characters.toLocaleString()} characters. Review it, then press Run.${appended}`
      : `${parts.length} files read. Review them, then press Run.${appended}`);
  }
  if (!parts.length && statusLine.dataset.state === "processing") setRunState(failures.length ? "error" : "ready", failures.length ? "File not read" : "");
  if (failures.length) {
    const summary = parts.length ? [`${parts.length} of ${parts.length + failures.length} files read. ${failures.length} couldn't be read:`, ...failures.map((f) => `• ${f}`)] : failures.length === 1 ? [failures[0].replace(/^[^:]+: /, "")] : failures.map((f) => `• ${f}`);
    showIntakeError([...summary, ...notes], networkFailure ? { retry: () => loadIntakeFiles(accepted) } : {});
  } else if (notes.length) {
    showIntakeError(notes);
  }
}

function openPicker() {
  pickerReturnFocus = document.activeElement;
  trayInput.click();
}
trayButton.addEventListener("click", openPicker);
uploadLink.addEventListener("click", openPicker);
uploadCancel.addEventListener("click", () => uploadController?.abort());
trayInput.addEventListener("change", () => {
  loadIntakeFiles(trayInput.files);
  trayInput.value = "";
  if (!questionnaire.value && pickerReturnFocus) pickerReturnFocus.focus();
});

// Paste a FILE into the composer: treat like a drop. Text paste is unchanged.
questionnaire.addEventListener("paste", (event) => {
  const files = [...(event.clipboardData?.files || [])];
  if (files.length) {
    event.preventDefault();
    loadIntakeFiles(files);
    return;
  }
  InputsPrompt.schedule();  // text paste: the pre-run inputs prompt checks once the text settles
});

// Drag and drop (desktop): dashed outline on the composer, tray-active icon.
let dragDepth = 0;
const hasFiles = (event) => [...(event.dataTransfer?.types || [])].includes("Files");
function setDragState(on) {
  document.body.classList.toggle("file-dragging", on);
  trayButton.dataset.drag = on ? "over" : "";
  trayButton.classList.toggle("drop-over", on);
  if (on) CabinetFan.close({ restoreFocus: false });
}
window.addEventListener("dragenter", (event) => {
  if (!hasFiles(event) || isMobile()) return;
  dragDepth += 1;
  setDragState(true);
});
window.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) setDragState(false);
});
window.addEventListener("dragover", (event) => {
  if (!hasFiles(event)) return;
  event.preventDefault(); // never let the browser navigate to the file
  event.dataTransfer.dropEffect = "copy";
});
window.addEventListener("drop", (event) => {
  if (!hasFiles(event)) return;
  event.preventDefault();
  dragDepth = 0;
  setDragState(false);
  loadIntakeFiles(event.dataTransfer.files);
});

// --- Cabinet store (localStorage, inputs addendum §7.1 / flows §3.1 v9.0.1) -------
// Approved by Ikenna: intakes survive closing the tab, so a user can come back and re-run. Items in the old
// sessionStorage key are merged once (newest first, capped at CABINET_MAX, no duplicates), then that key is removed.
// If localStorage is blocked, the cabinet falls back to sessionStorage.
const CABINET_KEY = "dia-cabinet-v2";
const CABINET_LEGACY_KEY = "dia-cabinet-v1";
let cabinetStorageOk = true;
let currentEntryId = null;
let rerunOf = null;  // the card a "Re-run with updated answers" session started from (addendum §7.2)
let cabinetBackend;  // undefined = not probed yet; null = no storage at all

function probeStorage(kind) {
  try {
    const store = window[kind];
    const probe = "__dia_probe__";
    store.setItem(probe, "1");
    store.removeItem(probe);
    return store;
  } catch (error) {
    return null;
  }
}
function cabinetStore() {
  if (cabinetBackend === undefined) cabinetBackend = probeStorage("localStorage") || probeStorage("sessionStorage");
  return cabinetBackend;
}
const byNewest = (a, b) => String(b.savedAt || "").localeCompare(String(a.savedAt || ""));
function migrateCabinet() {
  const store = cabinetStore();
  let legacy = null;
  try { legacy = sessionStorage.getItem(CABINET_LEGACY_KEY); } catch (error) { return; }
  if (legacy === null || !store) return;
  let old = [];
  try { old = JSON.parse(legacy); } catch (error) { old = []; }
  if (!Array.isArray(old)) old = [];
  const seen = new Set();
  const merged = [...readCabinet(), ...old].filter((item) => item && item.id && !seen.has(item.id) && seen.add(item.id));
  merged.sort(byNewest);
  writeCabinet(merged.slice(0, CABINET_MAX));
  if (cabinetStorageOk) { try { sessionStorage.removeItem(CABINET_LEGACY_KEY); } catch (error) { /* stays; merge is idempotent */ } }
}

// Remove with Undo: the removed entry stays in storage until its toast expires (timer, ×, another toast, or the
// page is left), but every read already leaves it out, so the screen, the counts and a reload always agree.
let pendingRemoval = null;  // { id, item, index } while the Undo toast is up
function readCabinet() {
  const items = storedCabinet();
  return pendingRemoval ? items.filter((item) => item.id !== pendingRemoval.id) : items;
}
function commitRemoval(id = pendingRemoval?.id) {
  if (!pendingRemoval || pendingRemoval.id !== id) return;
  pendingRemoval = null;
  const items = storedCabinet();
  if (items.some((item) => item.id === id)) writeCabinet(items.filter((item) => item.id !== id));
}
function cancelRemoval(id) {
  if (!pendingRemoval || pendingRemoval.id !== id) return false;
  const { item, index } = pendingRemoval;
  pendingRemoval = null;
  const items = storedCabinet();
  if (!items.some((entry) => entry.id === id)) {  // something rewrote storage meanwhile: put it back where it was
    items.splice(Math.min(index, items.length), 0, item);
    writeCabinet(items);
  }
  return true;
}
window.addEventListener("pagehide", () => commitRemoval());  // leaving the page ends the toast window
function storedCabinet() {
  const store = cabinetStore();
  if (!store) { cabinetStorageOk = false; return []; }
  try {
    const items = JSON.parse(store.getItem(CABINET_KEY) || "[]");
    cabinetStorageOk = true;
    return Array.isArray(items) ? items : [];
  } catch (error) {
    cabinetStorageOk = false;
    return [];
  }
}
function writeCabinet(items) {
  const store = cabinetStore();
  let list = items.slice(0, CABINET_MAX);
  if (!store) { cabinetStorageOk = false; return list; }
  while (list.length) {
    try {
      store.setItem(CABINET_KEY, JSON.stringify(list));
      cabinetStorageOk = true;
      return list;
    } catch (error) {
      if (error && error.name !== "QuotaExceededError" && !/quota/i.test(String(error))) { cabinetStorageOk = false; return list; }
      list = list.slice(0, -1); // storage full: drop the oldest until it fits
    }
  }
  try { store.removeItem(CABINET_KEY); } catch (error) { cabinetStorageOk = false; }
  return list;
}
function clearCabinet() {
  pendingRemoval = null;
  const store = cabinetStore();
  try { store?.removeItem(CABINET_KEY); } catch (error) { cabinetStorageOk = false; }
  currentEntryId = null;
}
migrateCabinet();
const slugify = (text) => (text || "intake").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "intake";
const tierLetter = (tier) => ({ "Quick Win": "A", "Custom AI Agent": "B", "Full Integration": "C" }[tier] || (tier || "?").slice(0, 1).toUpperCase());
const timeOf = (iso) => new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
const whenOf = (iso) => new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const dayOf = (iso) => { const d = new Date(iso); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };

function makeEntry(payload, questionnaireText) {
  const now = new Date();
  return {
    id: `${now.getTime().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
    savedAt: now.toISOString(),
    title: payload.intake?.company_name || "Untitled intake",
    tier: payload.score.tier,
    score: payload.score.total_score,
    maxScore: payload.score.max_score,
    exportedAt: null,
    questionnaire: questionnaireText,
    result: payload,
  };
}

function saveToCabinet(payload, questionnaireText) {
  const entry = makeEntry(payload, questionnaireText);
  const items = readCabinet();
  // Re-run (addendum §7.2): link the new card to the original; if the original was evicted, drop the link silently.
  if (rerunOf && items.some((item) => item.id === rerunOf)) entry.previousId = rerunOf;
  rerunOf = null;
  if (items.length >= CABINET_MAX && CABINET_CAP_POLICY === "block") {
    showToast(`Not filed: your cabinet is full (${CABINET_MAX} of ${CABINET_MAX}). Export or remove one first.`, [{ label: "Open cabinet", run: () => CabinetDrawer.open() }]);
    return;
  }
  const evicted = items.length >= CABINET_MAX ? items[items.length - 1] : null;
  const saved = writeCabinet([entry, ...items.slice(0, CABINET_MAX - 1)]);
  if (!cabinetStorageOk) {
    showToast("Couldn't file this intake. Your browser blocked storage.");
    return;
  }
  currentEntryId = entry.id;
  refreshCabinetButton(saved);
  if (evicted) {
    const already = evicted.exportedAt ? " (already exported)" : "";
    const actions = [{ label: "Undo", run: () => undoEviction(entry, evicted) }];
    if (!evicted.exportedAt) actions.push({ label: "Export it", run: () => exportOne(evicted, "json") });
    showToast(`Filed. Oldest intake “${evicted.title}” was removed${already}.`, actions);
  } else {
    showToast("Filed in your cabinet", [{ label: "Open", run: () => CabinetDrawer.open() }], { duration: 6000 });
  }
  if (CabinetDrawer.isOpen()) CabinetDrawer.render();
}

function undoEviction(newEntry, evicted) {
  const items = readCabinet().filter((item) => item.id !== newEntry.id);
  writeCabinet([...items, evicted]);
  currentEntryId = null;
  refreshCabinetButton();
  statusText.textContent = `Restored “${evicted.title}”. This result isn't filed.`;
  if (CabinetDrawer.isOpen()) CabinetDrawer.render();
}

function updateEntry(id, patch) {
  const items = readCabinet().map((item) => (item.id === id ? { ...item, ...patch } : item));
  writeCabinet(items);
  refreshCabinetButton(items);
  return items;
}

function removeEntry(id) {
  commitRemoval();  // an earlier removal whose toast is still up becomes final now
  const visible = readCabinet();
  const shownIndex = visible.findIndex((item) => item.id === id);
  if (shownIndex === -1) return;
  const stored = storedCabinet();
  const index = stored.findIndex((item) => item.id === id);
  const removed = stored[index];
  pendingRemoval = { id, item: removed, index };
  CabinetDrawer.selected.delete(id);
  refreshCabinetButton();
  CabinetDrawer.collapse(id, shownIndex);  // card and count go now; storage follows when the toast expires
  showToast(`Removed “${removed.title}”`, [{ label: "Undo", run: () => {
    if (!cancelRemoval(id)) return;
    refreshCabinetButton();
    CabinetDrawer.render({ focusId: id });
  } }], { onDismiss: () => commitRemoval(id) });
}

// --- Cabinet button ----------------------------------------------------------
const cabinetDock = document.querySelector("#cabinetDock");
const cabinetButton = document.querySelector("#cabinetButton");
const cabinetBadge = document.querySelector("#cabinetCount");

function refreshCabinetButton(items = readCabinet()) {
  const unexported = items.filter((item) => !item.exportedAt).length;
  cabinetBadge.hidden = unexported === 0;
  cabinetBadge.textContent = unexported > 9 ? "9+" : String(unexported);
  cabinetButton.setAttribute("aria-label", unexported ? `Cabinet, ${unexported} unexported` : "Cabinet");
  const fanRoute = items.length > 0 && !prefersReducedMotion() && !isMobile();
  cabinetButton.setAttribute("aria-haspopup", fanRoute ? "menu" : "dialog");
}

cabinetButton.addEventListener("click", (event) => {
  event.stopPropagation();
  if (CabinetFan.isOpen()) { CabinetFan.close(); return; }
  const items = readCabinet();
  // Reduced motion, mobile and an empty cabinet skip the fan (flows.md 3.2).
  if (!items.length || prefersReducedMotion() || isMobile()) CabinetDrawer.open();
  else CabinetFan.open(items, { focusFirst: event.detail === 0 });
});
cabinetButton.addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown" && !CabinetFan.isOpen()) {
    const items = readCabinet();
    if (items.length && !prefersReducedMotion() && !isMobile()) {
      event.preventDefault();
      CabinetFan.open(items, { focusFirst: true });
    }
  }
});

// --- CabinetFan: the quick-peek stack. Motion lives in static/cabinet-fan.css.
const CabinetFan = (() => {
  const fan = document.querySelector("#fan");
  let open = false;
  const slots = () => [...fan.querySelectorAll("[role=menuitem]")];
  function render(items) {
    fan.innerHTML = "";
    const recent = items.slice(0, CABINET_PAGE);
    recent.forEach((item, index) => {
      const li = document.createElement("li");
      li.setAttribute("role", "none");
      li.style.setProperty("--i", String(index + 1));
      const button = document.createElement("button");
      button.type = "button";
      button.className = "fan-card";
      button.setAttribute("role", "menuitem");
      button.tabIndex = -1;
      const isCurrent = item.id === currentEntryId;
      if (isCurrent) button.setAttribute("aria-current", "true");
      const meta = item.exportedAt ? `✓ ${dayOf(item.exportedAt)}` : `${timeOf(item.savedAt)} · Tier ${tierLetter(item.tier)}`;
      button.innerHTML = `<span class="fan-title"></span><span class="fan-meta${item.exportedAt ? " fan-exported" : ""}"></span><span class="fan-reveal">Score <span class="fan-score"></span></span>`;
      button.querySelector(".fan-title").textContent = item.title;
      button.querySelector(".fan-meta").textContent = meta;
      button.querySelector(".fan-score").textContent = String(item.score);
      button.setAttribute("aria-label", `${item.title}, ${timeOf(item.savedAt)}, Tier ${tierLetter(item.tier)}, score ${item.score}${isCurrent ? ", current" : ""}`);
      button.addEventListener("click", () => { close({ restoreFocus: false }); reopenIntake(item); });
      li.appendChild(button);
      fan.appendChild(li);
    });
    const last = document.createElement("li");
    last.className = "fan-open";
    last.setAttribute("role", "none");
    last.style.setProperty("--i", String(recent.length + 1));
    // Glyph v3: the last slot sits 10px under the fully visible last card.
    last.style.setProperty("--y-open", `calc(${Math.max(recent.length - 1, 0)} * var(--fan-step) + var(--fan-card-h) + 10px)`);
    const openButton = document.createElement("button");
    openButton.type = "button";
    openButton.className = "fan-open-btn";
    openButton.setAttribute("role", "menuitem");
    openButton.tabIndex = -1;
    openButton.setAttribute("aria-label", `Open cabinet, ${items.length} intake${items.length === 1 ? "" : "s"}`);
    openButton.innerHTML = `Open cabinet <span class="fan-count" aria-hidden="true">${items.length}</span>`;
    openButton.addEventListener("click", () => { close({ restoreFocus: false }); setTimeout(() => CabinetDrawer.open(), prefersReducedMotion() ? 0 : 200); });
    last.appendChild(openButton);
    fan.appendChild(last);
  }
  function focusSlot(index) {
    const all = slots();
    const target = all[Math.max(0, Math.min(index, all.length - 1))];
    all.forEach((el) => { el.tabIndex = el === target ? 0 : -1; });
    target?.focus();
  }
  // Drop down from the cabinet; drop up when the viewport has no room below (cabinet sits by the composer).
  function placeFan(count) {
    const style = getComputedStyle(cabinetDock);
    const step = parseFloat(style.getPropertyValue("--fan-step")) || 48;
    const cardH = parseFloat(style.getPropertyValue("--fan-card-h")) || 76;
    const openH = fan.querySelector(".fan-open-btn")?.offsetHeight || 34;
    const height = Math.max(count - 1, 0) * step + (count ? cardH + 10 : 0) + openH;
    const rect = cabinetButton.getBoundingClientRect();
    const below = window.innerHeight - rect.bottom - 12;
    const above = rect.top - 12;
    if (below < height && above > below) {
      cabinetDock.dataset.drop = "up";
      // Keep the top card at least 8px inside the window (eats into the 12px gap first on short screens).
      cabinetDock.style.setProperty("--fan-up-top", `${Math.max(-(height + 12), 8 - rect.top)}px`);
    } else {
      delete cabinetDock.dataset.drop;
      cabinetDock.style.removeProperty("--fan-up-top");
    }
  }
  function openFan(items, { focusFirst = true } = {}) {
    Popovers.closeOthers("cabinet");
    render(items);
    placeFan(Math.min(items.length, CABINET_PAGE));
    open = true;
    cabinetDock.dataset.open = "true";
    cabinetButton.setAttribute("aria-expanded", "true");
    cabinetButton.classList.add("open");
    if (focusFirst) requestAnimationFrame(() => focusSlot(0));
    else slots()[0] && (slots()[0].tabIndex = 0);
  }
  function close({ restoreFocus = true } = {}) {
    if (!open) return;
    open = false;
    cabinetDock.dataset.open = "false";
    cabinetButton.setAttribute("aria-expanded", "false");
    if (!CabinetDrawer.isOpen()) cabinetButton.classList.remove("open");
    if (restoreFocus) cabinetButton.focus();
  }
  fan.addEventListener("keydown", (event) => {
    const all = slots();
    const index = all.indexOf(document.activeElement);
    if (event.key === "ArrowDown") { event.preventDefault(); focusSlot(index + 1); }
    else if (event.key === "ArrowUp") { event.preventDefault(); index <= 0 ? close() : focusSlot(index - 1); }
    else if (event.key === "Home") { event.preventDefault(); focusSlot(0); }
    else if (event.key === "End") { event.preventDefault(); focusSlot(all.length - 1); }
    else if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); }
    else if (event.key === "Tab") { close({ restoreFocus: false }); }
  });
  document.addEventListener("click", (event) => { if (open && !cabinetDock.contains(event.target)) close({ restoreFocus: false }); });
  document.addEventListener("keydown", (event) => {
    if (open && event.key === "Escape") { event.preventDefault(); close(); }
  });
  window.addEventListener("scroll", () => open && close({ restoreFocus: false }), { passive: true });
  return { open: openFan, close, isOpen: () => open };
})();

// --- CabinetDrawer: right-side drawer (bottom sheet on mobile) ----------------
const CabinetDrawer = (() => {
  const drawer = document.querySelector("#cabinetDrawer");
  const title = document.querySelector("#cabinetTitle");
  const summary = document.querySelector("#cabinetSummary");
  const list = document.querySelector("#cabinetList");
  const more = document.querySelector("#cabinetMore");
  const empty = document.querySelector("#cabinetEmpty");
  const full = document.querySelector("#cabinetFull");
  const selectModeButton = document.querySelector("#cabinetSelectMode");
  const selectBar = document.querySelector("#cabinetSelectBar");
  const selectAll = document.querySelector("#cabinetSelectAll");
  const selectAllLabel = document.querySelector("#cabinetSelectAllLabel");
  const countLive = document.querySelector("#cabinetCountLive");
  const foot = document.querySelector("#cabinetFoot");
  const exportButton = document.querySelector("#cabinetExport");
  const exportCancel = document.querySelector("#cabinetExportCancel");
  const helper = document.querySelector("#cabinetHelper");
  const live = document.querySelector("#cabinetLive");
  const clearButton = document.querySelector("#cabinetClear");
  const clearConfirm = document.querySelector("#cabinetClearConfirm");
  const alertBox = document.querySelector("#cabinetAlert");
  const selected = new Set();
  let shown = CABINET_PAGE;
  let selectMode = false;
  let exporting = null;

  function cardFor(item, index, { isNew = false, newIndex = 0 } = {}) {
    const li = document.createElement("li");
    li.className = "drawer-card";
    if (isNew) { li.classList.add("is-new"); li.style.setProperty("--k", String(newIndex)); }
    li.dataset.id = item.id;
    const headingId = `card-${item.id}`;
    const article = document.createElement("article");
    article.setAttribute("aria-labelledby", headingId);
    article.innerHTML = `
      <label class="card-check" ${selectMode ? "" : "hidden"}><input type="checkbox"><span class="visually-hidden"></span></label>
      <div class="card-body">
        <h3 class="card-title" id="${headingId}"></h3>
        <p class="card-meta"></p>
        <p class="card-exported" ${item.exportedAt ? "" : "hidden"}></p>
      </div>
      <div class="card-actions" ${selectMode ? "hidden" : ""}>
        <button type="button" class="card-reopen">Reopen</button>
        <button type="button" data-kind="pdf">PDF</button>
        <button type="button" data-kind="md">MD</button>
        <button type="button" data-kind="json">JSON</button>
        <button type="button" class="card-rerun" aria-label="Re-run ${item.title.replace(/"/g, "&quot;")} with updated answers">Re-run</button>
        <button type="button" class="card-remove" aria-label="Remove ${item.title.replace(/"/g, "&quot;")}">Remove</button>
      </div>
      <p class="card-status" aria-live="polite"></p>`;
    article.querySelector(".card-title").textContent = item.title;
    article.querySelector(".card-meta").textContent = `${whenOf(item.savedAt)} · ${item.tier} · ${item.score}/${item.maxScore}`;
    if (item.previousId) article.querySelector(".card-meta").textContent = `Re-run · ${article.querySelector(".card-meta").textContent}`;  // addendum §7.2
    if (item.exportedAt) article.querySelector(".card-exported").textContent = `✓ Exported ${dayOf(item.exportedAt)}`;
    const check = article.querySelector(".card-check input");
    article.querySelector(".card-check .visually-hidden").textContent = `Select ${item.title}, ${new Date(item.savedAt).toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
    check.checked = selected.has(item.id);
    check.disabled = Boolean(exporting);
    check.addEventListener("change", () => { check.checked ? selected.add(item.id) : selected.delete(item.id); updateSelection(); });
    li.addEventListener("click", (event) => {
      if (!selectMode || exporting || event.target.closest("input, button")) return;
      check.checked = !check.checked;
      check.dispatchEvent(new Event("change"));
    });
    article.querySelector(".card-reopen").addEventListener("click", () => reopenIntake(item));
    article.querySelector(".card-remove").addEventListener("click", () => removeEntry(item.id));
    article.querySelector(".card-rerun").addEventListener("click", () => rerunIntake(item));
    for (const button of article.querySelectorAll("[data-kind]")) {
      button.addEventListener("click", () => exportOne(item, button.dataset.kind, { button, status: article.querySelector(".card-status") }));
    }
    li.appendChild(article);
    return li;
  }

  function renderChrome(items) {  // the count, empty state and header buttons
    alertBox.textContent = cabinetStorageOk ? "" : "Couldn't open the cabinet. Your browser blocked storage.";
    for (const id of [...selected]) if (!items.some((item) => item.id === id)) selected.delete(id);
    summary.textContent = items.length === 0 ? "" : items.length >= 13 ? `${items.length} of ${CABINET_MAX} slots used` : `${items.length} intake${items.length === 1 ? "" : "s"}`;
    empty.hidden = items.length > 0;
    full.hidden = items.length < CABINET_MAX;
    selectModeButton.hidden = items.length === 0;
    clearButton.hidden = items.length === 0 || selectMode;
    if (items.length === 0) clearConfirm.hidden = true;
  }

  // Remove: the count updates now and the card collapses (reduced motion: it just goes).
  function collapse(id, focusAfterRemove) {
    const li = [...list.children].find((node) => node.dataset.id === id);
    if (!li || !drawer.open || prefersReducedMotion()) return render({ focusAfterRemove });
    const items = readCabinet();
    renderChrome(items);
    updateSelection(items);
    const hadFocus = li.contains(document.activeElement);
    const others = [...list.children].filter((node) => node !== li && !node.classList.contains("is-removing"));
    li.inert = true;
    li.setAttribute("aria-hidden", "true");
    if (hadFocus) {
      const target = others[Math.min(focusAfterRemove, others.length - 1)];
      (target?.querySelector(".card-reopen") || document.querySelector("#cabinetStart")).focus();
    }
    li.style.height = `${li.offsetHeight}px`;
    void li.offsetHeight;  // start the transition from the measured height
    li.classList.add("is-removing");
    li.style.height = "0px";
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      if (!li.isConnected) return;  // already re-rendered (Undo, another change)
      const focusInList = list.contains(document.activeElement);
      const focusedId = document.activeElement?.closest?.("li")?.dataset.id;
      render(focusInList && focusedId ? { focusId: focusedId } : {});
    };
    li.addEventListener("transitionend", (event) => { if (event.target === li && event.propertyName === "height") finish(); });
    setTimeout(finish, 320);
  }

  function render({ revealFrom = null, focusAfterRemove = null, focusId = null } = {}) {
    const items = readCabinet();
    renderChrome(items);
    const visible = items.slice(0, Math.min(shown, CABINET_MAX));
    list.innerHTML = "";
    visible.forEach((item, index) => {
      const isNew = revealFrom !== null && index >= revealFrom;
      list.appendChild(cardFor(item, index, { isNew, newIndex: index - (revealFrom || 0) }));
    });
    const remaining = Math.min(items.length, CABINET_MAX) - visible.length;
    more.hidden = remaining <= 0;
    more.textContent = `Show more (${visible.length} of ${items.length})`;
    updateSelection(items);
    if (focusAfterRemove !== null) {
      const cards = list.querySelectorAll(".card-reopen");
      (cards[Math.min(focusAfterRemove, cards.length - 1)] || document.querySelector("#cabinetStart")).focus();
    }
    if (focusId !== null) [...list.children].find((node) => node.dataset.id === focusId)?.querySelector(".card-reopen")?.focus();
    return items;
  }

  function updateSelection(items = readCabinet()) {
    const n = selected.size;
    selectBar.hidden = !selectMode;
    foot.hidden = !selectMode;
    selectModeButton.textContent = selectMode ? "Cancel" : "Select";
    selectAll.checked = n > 0 && n === items.length;
    selectAll.indeterminate = n > 0 && n < items.length;
    selectAllLabel.textContent = n > 0 && n === items.length ? `All ${n} selected` : "Select all";
    countLive.textContent = selectMode ? `${n} selected` : "";
    exportButton.setAttribute("aria-disabled", String(n === 0 || Boolean(exporting)));
    exportButton.textContent = exporting ? exportButton.textContent : n ? `Export selected (${n})` : "Export selected";
    helper.hidden = n > 0;
  }

  function setSelectMode(on, { preselect = [] } = {}) {
    selectMode = on;
    if (!on) selected.clear();
    preselect.forEach((id) => selected.add(id));
    render();
    if (on) list.querySelector(".card-check input")?.focus();
    else selectModeButton.focus();
  }

  selectModeButton.addEventListener("click", () => setSelectMode(!selectMode));
  // Clear cabinet (flows §3.1 v9.0.1): a confirm in the drawer, focus on Keep. It empties dia-cabinet-v2.
  clearButton.addEventListener("click", () => {
    clearConfirm.hidden = false;
    document.querySelector("#cabinetClearKeep").focus();
  });
  document.querySelector("#cabinetClearKeep").addEventListener("click", () => {
    clearConfirm.hidden = true;
    clearButton.focus();
  });
  document.querySelector("#cabinetClearYes").addEventListener("click", () => {
    clearConfirm.hidden = true;
    clearCabinet();
    selected.clear();
    refreshCabinetButton([]);
    render();
    live.textContent = "Cabinet cleared";
    document.querySelector("#cabinetStart").focus();
  });
  clearConfirm.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); clearConfirm.hidden = true; clearButton.focus(); }
  });
  selectAll.addEventListener("change", () => {
    const items = readCabinet();
    selected.clear();
    if (selectAll.checked) items.forEach((item) => selected.add(item.id));
    render();
  });
  more.addEventListener("click", () => {
    const from = shown;
    shown = Math.min(shown + CABINET_PAGE, CABINET_MAX);
    const items = render({ revealFrom: from });
    live.textContent = `Showing ${Math.min(shown, items.length)} of ${items.length}`;
    list.querySelectorAll(".drawer-card")[from]?.querySelector(".card-reopen, input")?.focus();
    setTimeout(() => list.querySelectorAll(".is-new").forEach((el) => el.classList.remove("is-new")), 400);
  });
  document.querySelector("#cabinetSelectExport").addEventListener("click", () => {
    const oldest = readCabinet().slice(-5).map((item) => item.id);
    shown = CABINET_MAX;
    setSelectMode(true, { preselect: oldest });
  });
  document.querySelector("#cabinetStart").addEventListener("click", () => { close({ restoreFocus: false }); newIntake(); });
  document.querySelector("#cabinetClose").addEventListener("click", () => close());
  exportButton.addEventListener("click", () => {
    if (exportButton.getAttribute("aria-disabled") === "true") return;
    exportSelected();
  });
  exportCancel.addEventListener("click", () => exporting?.abort());

  drawer.addEventListener("cancel", (event) => {
    // Esc order: cancel export, leave select mode, then close (flows.md 6.2).
    event.preventDefault();
    if (exporting) exporting.abort();
    else if (selectMode) setSelectMode(false);
    else close();
  });
  drawer.addEventListener("click", (event) => { if (event.target === drawer) close(); }); // scrim
  // ⌘/Ctrl+A selects all while in select mode and focus is in the list.
  drawer.addEventListener("keydown", (event) => {
    if (selectMode && (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "a" && event.target.closest(".drawer-list")) {
      event.preventDefault();
      selectAll.checked = true;
      selectAll.dispatchEvent(new Event("change"));
    }
  });

  function open({ select = false } = {}) {
    CabinetFan.close({ restoreFocus: false });
    shown = CABINET_PAGE;
    if (!select) { selectMode = false; selected.clear(); }
    else selectMode = true;
    const items = render();
    if (!drawer.open) drawer.showModal();
    cabinetButton.classList.add("open");
    cabinetButton.setAttribute("aria-expanded", "true");
    requestAnimationFrame(() => drawer.classList.add("is-open"));
    (items.length ? title : document.querySelector("#cabinetStart")).focus();
  }
  function close({ restoreFocus = true } = {}) {
    if (!drawer.open) return;
    drawer.classList.remove("is-open");
    drawer.close();
    cabinetButton.classList.remove("open");
    cabinetButton.setAttribute("aria-expanded", "false");
    if (restoreFocus) cabinetButton.focus();
  }

  // Exporting (zip) -----------------------------------------------------------
  async function exportSelectedImpl() {
    const items = readCabinet().filter((item) => selected.has(item.id));
    if (!items.length) { helper.hidden = false; return; }
    if (typeof JSZip === "undefined") { alertBox.textContent = "Export failed. Nothing was downloaded."; return; }
    exporting = new AbortController();
    exportCancel.hidden = false;
    exportButton.setAttribute("aria-busy", "true");
    updateSelection();
    render();
    const failedPdfs = [];
    try {
      const zip = new JSZip();
      const used = new Set();
      let lastAnnounced = 0;
      for (const [index, item] of items.entries()) {
        if (exporting.signal.aborted) throw new DOMException("aborted", "AbortError");
        exportButton.textContent = `Preparing ${index + 1} of ${items.length}…`;
        const pct = Math.floor(((index + 1) / items.length) * 4);
        if (index === 0 || pct > lastAnnounced) { live.textContent = exportButton.textContent; lastAnnounced = pct; }
        const slug = slugify(item.title);
        let folder = `${slug}-${dayOf(item.savedAt)}`;
        for (let n = 2; used.has(folder); n += 1) folder = `${slug}-${dayOf(item.savedAt)}-${n}`;
        used.add(folder);
        const dir = zip.folder(folder);
        try {
          dir.file(`dia-${slug}.pdf`, await fetchPdf(item, exporting.signal));
        } catch (error) {
          if (error.name === "AbortError") throw error;
          failedPdfs.push(item.title);
        }
        dir.file(`dia-${slug}.md`, buildMarkdownExport(item.result.documents || {}));
        dir.file(`dia-${slug}.json`, exportJson(item.result));
      }
      exportButton.textContent = "Zipping…";
      const blob = await zip.generateAsync({ type: "blob" });
      if (exporting.signal.aborted) throw new DOMException("aborted", "AbortError");
      const name = `dia-cabinet-${dayOf(new Date().toISOString())}.zip`;
      downloadBlob(name, blob);
      const stamp = new Date().toISOString();
      const ids = new Set(items.map((item) => item.id));
      writeCabinet(readCabinet().map((item) => (ids.has(item.id) ? { ...item, exportedAt: stamp } : item)));
      refreshCabinetButton();
      const plural = `${items.length} intake${items.length === 1 ? "" : "s"}`;
      exporting = null;
      setSelectMode(false);
      if (failedPdfs.length) {
        alertBox.textContent = `Exported ${plural}. ${failedPdfs.length} PDF${failedPdfs.length === 1 ? "" : "s"} couldn't be made, so their MD and JSON are included.`;
      } else {
        live.textContent = `Exported ${plural}`;
        showToast(`Exported ${plural} · ${name}`, [], { duration: 6000 });
      }
    } catch (error) {
      if (error.name === "AbortError") live.textContent = "Export cancelled";
      else alertBox.textContent = "Export failed. Nothing was downloaded.";
    } finally {
      exporting = null;
      exportCancel.hidden = true;
      exportButton.removeAttribute("aria-busy");
      exportButton.textContent = "";
      updateSelection();
      if (drawer.open) render();
    }
  }

  return { open, close, render, collapse, isOpen: () => drawer.open, selected, exportSelected: exportSelectedImpl, setSelectMode };
})();

const exportSelected = () => CabinetDrawer.exportSelected();
const cabinetSelected = CabinetDrawer.selected;
const openDrawer = (options) => CabinetDrawer.open(options);

// Keyboard shortcuts: ⌘/Ctrl+O upload, ⌘/Ctrl+Shift+F cabinet.
document.addEventListener("keydown", (event) => {
  const mod = event.metaKey || event.ctrlKey;
  if (!mod || event.altKey) return;
  const key = event.key.toLowerCase();
  if (key === "o" && !event.shiftKey) { event.preventDefault(); openPicker(); }
  else if (key === "f" && event.shiftKey) {
    event.preventDefault();
    CabinetDrawer.isOpen() ? CabinetDrawer.close() : CabinetDrawer.open();
  }
});
// Esc cancels an in-flight upload.
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && uploadController) { event.preventDefault(); uploadController.abort(); }
});

// --- Reopen + exports -------------------------------------------------------
function reopenIntake(item) {
  const unsaved = questionnaire.value.trim() && questionnaire.value !== lastRunQuestionnaire && questionnaire.value !== item.questionnaire;
  if (unsaved && !window.confirm("Replace what's in the composer?\nYour unrun text will be lost.")) return;
  if (!item.result || !item.result.score || !item.result.documents) {
    showToast("This intake couldn't be reopened. Its saved data is damaged.", [{ label: "Remove it", run: () => removeEntry(item.id) }]);
    return;
  }
  CabinetDrawer.close({ restoreFocus: false });
  rerunOf = null;
  InputsPrompt.endSession();
  questionnaire.value = item.questionnaire;
  showResults(item.result, item.questionnaire);
  currentEntryId = item.id;
  statusText.textContent = `Reopened ${item.title}`;
  if (isLegacyScore(item.result)) {
    rescoreEntry(item).then(() => { statusText.textContent = `Reopened ${item.title}. Rescored under the current rules.`; })
      .catch(() => { /* offline: the saved score stays, with its "Scored before value at stake" hint */ });
  }
  const heading = document.querySelector(".score-card span");
  if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); heading.scrollIntoView({ block: "center", behavior: prefersReducedMotion() ? "auto" : "smooth" }); }
}

// Re-run with updated answers (addendum §7.2, flows §5.2): the stored questionnaire goes back into the composer,
// expanded and editable, with the caret at the section-6 heading (or the end). The original card is never changed;
// Run files a new card linked by previousId.
function rerunIntake(item) {
  const unsaved = questionnaire.value.trim() && questionnaire.value !== lastRunQuestionnaire && questionnaire.value !== item.questionnaire;
  if (unsaved && !window.confirm("Replace what's in the composer?\nYour unrun text will be lost.")) return;
  if (typeof item.questionnaire !== "string") {
    showToast("This intake couldn't be reopened. Its saved data is damaged.", [{ label: "Remove it", run: () => removeEntry(item.id) }]);
    return;
  }
  CabinetDrawer.close({ restoreFocus: false });
  questionnaire.value = item.questionnaire;
  if (intakeBody.hidden) setIntakeCollapsed(false);
  autosizeComposer(questionnaire);
  questionnaire.dispatchEvent(new Event("input"));
  InputsPrompt.newSession();
  rerunOf = item.id || null;
  const heading = item.questionnaire.search(/^##\s*6\.?\s/m);
  const caret = heading >= 0 ? heading : item.questionnaire.length;
  questionnaire.focus();
  questionnaire.setSelectionRange(caret, caret);
  statusText.textContent = `Editing a copy of ${item.title}. The original stays in the cabinet.`;
  InputsPrompt.schedule();
}

// Scored before / after (addendum §7.3): on screen only, after a re-run whose previous result is still stored.
// Question numbers mirror client_discovery.core.QUESTION_NUMBERS (the one questionnaire map; a test pins the parity).
// Older questionnaires without section 6 have no numbered questions, so their labels carry no number.
const QUESTION_NUMBERS = {
  workflow_tasks: 1, hourly_cost: 2, success_metric: 3, process_owner: 4, systems_access: 5, data_readiness: 6,
  exceptions: 7, sensitive_data: 8, past_attempts: 9, maintenance: 10, finance_signals: 11,
};
const BA_LABELS = {
  workflow_tasks: "manual tasks", hourly_cost: "hourly cost", success_metric: "success metric",
  process_owner: "process owner", systems_access: "systems", data_readiness: "data", exceptions: "exceptions",
  sensitive_data: "regulated data", past_attempts: "previous attempts", maintenance: "maintenance",
  finance_signals: "finance", budget: "budget", pain_points: "pain points", goals: "goals",
};
function answerLabel(key, numbered) {
  return numbered && QUESTION_NUMBERS[key] ? `Q${QUESTION_NUMBERS[key]} ${BA_LABELS[key]}` : BA_LABELS[key];
}
const flagWords = (flag) => flag.toLowerCase().replace(/_/g, " ");
function valueWords(payload) {
  if (!payload.score || payload.score.value_at_stake === null || payload.score.value_at_stake === undefined) return "not computed";
  return `${payload.value_at_stake_text || `about $${Number(payload.score.value_at_stake).toLocaleString("en-US")}`} a year`;
}
function beforeAfterRows(previous, current) {
  if (isLegacyScore(previous)) return null;
  const ps = previous.score, cs = current.score;
  const sv = (s) => `${s.strategic_value}${s.strategic_value_default ? " (default)" : ""}`;
  const pf = previous.flags || ps.flags || [], cf = current.flags || cs.flags || [];
  const changed = Object.keys(BA_LABELS).filter((key) => JSON.stringify((previous.intake || {})[key] ?? "") !== JSON.stringify((current.intake || {})[key] ?? ""));
  return [
    ["Score", `${ps.total_score} → ${cs.total_score} of ${cs.max_score}`],
    ["Strategic value", `${sv(ps)} → ${sv(cs)}`],
    ["Value at stake", `${valueWords(previous)} → ${valueWords(current)}`],
    ["Inputs", `${previous.inputs_confidence || "unknown"} → ${current.inputs_confidence || "unknown"}`],
    ["Flags cleared", pf.filter((f) => !cf.includes(f)).map(flagWords).join(", ") || "none"],
    ["New flags", cf.filter((f) => !pf.includes(f)).map(flagWords).join(", ") || "none"],
    ["Answers changed", changed.map((key) => answerLabel(key, Boolean((current.intake || {}).workflows_section))).join(", ") || "none"],
  ];
}
function renderBeforeAfter(current, previousId) {
  const box = document.querySelector("#beforeAfter");
  if (!box) return;
  const delta = document.querySelector("#scoreDelta");
  const previous = previousId ? readCabinet().find((item) => item.id === previousId) : null;
  box.hidden = !previous;
  if (delta) delta.hidden = true;
  if (!previous) return;
  const rows = beforeAfterRows(previous.result || {}, current);
  if (rows && delta) {  // compact one-line delta inside the tier card (v1.1.2)
    delta.textContent = `Was ${previous.result.score.total_score}, now ${current.score.total_score}`;
    delta.hidden = false;
  }
  const list = box.querySelector("dl");
  list.replaceChildren();
  if (!rows) {
    const note = document.createElement("dd");
    note.textContent = "Scored with older rules, so no comparison.";
    list.appendChild(note);
    return;
  }
  for (const [label, value] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    list.append(dt, dd);
  }
}

async function fetchPdf(item, signal) {
  const response = await fetch("/api/pdf", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      questionnaire: item.questionnaire,
      result: scrubForExport(item.result),
      date: new Date(item.savedAt).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" }),
    }),
    signal,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || "PDF rendering failed on the server.");
  }
  // Screen and PDF must never disagree: when the PDF's figures differ from the saved ones (older rules), the saved
  // result is rescored and the screen updated to match.
  const pdfScore = response.headers.get("X-DIA-Score");
  const saved = item.result && item.result.score;
  if (pdfScore && saved && pdfScore !== `${saved.tier}|${saved.total_score}/${saved.max_score}|${saved.timeline}`) {
    await rescoreEntry(item).catch(() => {});
  }
  return response.blob();
}

function downloadBlob(filename, blob) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function exportOne(item, kind, { button = null, status = null } = {}) {
  const base = `dia-${slugify(item.title)}`;
  const say = (message) => { if (status) { status.textContent = message; setTimeout(() => { status.textContent = ""; }, 3000); } };
  const label = button?.textContent;
  try {
    if (kind === "pdf") {
      if (button) { button.textContent = "PDF…"; button.setAttribute("aria-busy", "true"); }
      downloadBlob(`${base}.pdf`, await fetchPdf(item));
      say("PDF downloaded");
    } else if (kind === "md") {
      downloadFile(`${base}.md`, buildMarkdownExport(item.result.documents || {}), "text/markdown");
      say("Markdown downloaded");
    } else {
      downloadFile(`${base}.json`, exportJson(item.result), "application/json");
      say("JSON downloaded");
    }
    if (readCabinet().some((entry) => entry.id === item.id)) {
      updateEntry(item.id, { exportedAt: new Date().toISOString() });
      if (CabinetDrawer.isOpen()) CabinetDrawer.render();
    }
  } catch (error) {
    say(kind === "pdf" ? error.message : `Couldn't make the ${kind === "md" ? "Markdown" : "JSON"} file.`);
  } finally {
    if (button) { button.textContent = label; button.removeAttribute("aria-busy"); }
  }
}

downloadPdf.addEventListener("click", async () => {
  if (!latestPayload) return;
  downloadPdf.disabled = true;
  downloadPdf.setAttribute("aria-busy", "true");
  flashStatus(exportStatus, "Building PDF…");
  try {
    const item = { id: currentEntryId, title: latestPayload.intake?.company_name || "intake", questionnaire: lastRunQuestionnaire, result: latestPayload, savedAt: new Date().toISOString() };
    downloadBlob(`dia-${slugify(item.title)}.pdf`, await fetchPdf(item));
    if (currentEntryId) updateEntry(currentEntryId, { exportedAt: new Date().toISOString() });
    flashStatus(exportStatus, "PDF downloaded");
  } catch (error) {
    flashStatus(exportStatus, error.message);
  } finally {
    downloadPdf.disabled = false;
    downloadPdf.removeAttribute("aria-busy");
  }
});

refreshCabinetButton();
window.matchMedia("(prefers-reduced-motion: reduce)").addEventListener?.("change", () => refreshCabinetButton());


// --- Letterhead clock: live local 12-hour time above today's date, centred in the top band ---
// Ticks on the minute boundary (setTimeout chain, so no drift), re-syncs when the tab becomes visible.
// No seconds and no animation, so there is nothing for reduced motion to switch off.
const LetterheadClock = (() => {
  const timeEl = document.querySelector("#lhTime");
  const dateEl = document.querySelector("#lhDate");
  if (!timeEl || !dateEl) return null;
  const fmt = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", hour12: true });
  let timer = 0;
  const pad = (n) => String(n).padStart(2, "0");
  function offset(d) {
    const m = -d.getTimezoneOffset();
    return `${m >= 0 ? "+" : "-"}${pad(Math.floor(Math.abs(m) / 60))}:${pad(Math.abs(m) % 60)}`;
  }
  function render() {
    const now = new Date();
    const day = dayOf(now.toISOString());
    const hm = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
    timeEl.textContent = fmt.format(now);
    timeEl.dateTime = `${day}T${hm}${offset(now)}`;
    dateEl.textContent = day;
    dateEl.dateTime = day;
    return now;
  }
  function schedule() {
    clearTimeout(timer);
    const now = render();
    timer = setTimeout(schedule, 60000 - (now.getSeconds() * 1000 + now.getMilliseconds()) + 20);
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) schedule(); });
  schedule();
  return { render };
})();

// --- Pre-run inputs prompt (dia-savant inputs addendum v1.1.1 §3, flows §2.9 v9.0.1) ----------------------
// Client-side and deterministic: it mirrors client_discovery/core.py (QUESTION_ALIASES, _extract_table_rows,
// parse_workflows, parse_hourly_rate, _has_number, _blank) and inputs.input_gaps, and never calls the server,
// so Off mode's "Nothing leaves this browser except the deterministic run" stays true. tests/test_inputs.py keeps
// these lists equal to the Python ones.
const SECTION6_ALIASES = {
  workflow_tasks: ["manual tasks"],
  hourly_cost: ["hourly cost", "salary band", "loaded rate"],
  success_metric: ["the one number", "success metric"],
  process_owner: ["owns the process", "process owner"],
  systems_access: ["systems involved", "login or api access"],
  data_readiness: ["where does the data live", "data live", "how clean"],
  exceptions: ["what goes wrong today", "exceptions and hand-offs"],
  sensitive_data: ["regulated or sensitive"],
  past_attempts: ["previous ai", "automation attempts"],
  maintenance: ["who maintains"],
  finance_signals: ["finance only", "days overdue"],
};
const QUESTION_ALIASES = {
  ...SECTION6_ALIASES,
  company_name: ["company name"],
  website: ["website"],
  industry: ["industry / niche", "industry", "niche"],
  size: ["company size", "size"],
  years_in_business: ["years in business"],
  location: ["location"],
  tools: ["what tools/software do you use today?", "what tools/software do you use", "tools/software do you use", "tools"],
  budget: ["budget range", "budget"],
  decision_maker: ["final decision-maker", "decision maker", "decision-maker"],
  start_date: ["preferred start date", "start date"],
  tech_person: ["in-house tech person", "tech person"],
  compliance: ["compliance requirements", "compliance"],
  notes: ["anything else we should know", "notes"],
};
// Copy mirrors client_discovery/inputs.py INPUTS (label, on-screen prompt, why), in the fixed §2 order.
const INPUT_PROMPTS = {
  hours: ["Hours per task", "Which three tasks take the most hours each week?", "Hours turn the value at stake into a figure."],
  hourly_cost: ["Hourly cost", "What does an hour of that person's time cost the business, all-in?", "Hours times cost gives the yearly value at stake."],
  success_metric: ["Success metric", "What one number would show this worked, and what is it today?", "A number agreed now makes the result easy to check later."],
  people: ["People involved", "Who does each of those tasks, and how many people share them?", "It shows who the change affects."],
  systems: ["Systems", "Which systems does each task touch, and who can grant access?", "Access is where scope surprises usually start."],
};
const DIA_NUMBER = "\\d[\\d,]*(?:\\.\\d+)?";
const toNumber = (text) => Number(text.replace(/,/g, ""));
const isBlankAnswer = (text) => ["", "n/a", "none", "tbd", "your answer", "-"].includes((text || "").trim().toLowerCase());

function questionnaireRows(content) {
  const rows = [];
  for (const line of content.split(/\r\n|\r|\n/)) {
    const stripped = line.trim();
    if (!(stripped.startsWith("|") && stripped.endsWith("|")) || stripped.includes("---")) continue;
    const cells = stripped.replace(/^\|+|\|+$/g, "").split("|").map((cell) => cell.trim());
    if (cells.length < 2 || cells[0].toLowerCase() === "question") continue;
    rows.push([cells[0], cells.slice(1).join(" | ")]);
  }
  return rows;
}
function parseIntakeFields(content) {
  const values = {};
  let known = false;
  for (const [question, answer] of questionnaireRows(content)) {
    const normalized = question.trim().toLowerCase().replace(/\s+/g, " ");
    for (const [field, aliases] of Object.entries(QUESTION_ALIASES)) {
      if (aliases.some((alias) => normalized.includes(alias))) {
        values[field] = answer.trim();
        known = true;
        break;
      }
    }
  }
  const heading = /^##\s*6\.?\s/m.test(content);
  const workflowsSection = heading || Object.keys(SECTION6_ALIASES).some((field) => values[field]);
  return { values, known, heading, workflowsSection };
}
function workflowParts(text) {
  return (text || "").split(/;|<br\s*\/?>|\n/i).map((part) => part.trim())
    .filter((part) => part && !["n/a", "none", "your answer"].includes(part.toLowerCase())).slice(0, 3);
}
function parseWorkflowsJs(text) {
  return workflowParts(text).map((part) => {
    const lower = part.toLowerCase();
    const hours = lower.match(new RegExp(`(${DIA_NUMBER})\\s*(?:h\\b|hrs?\\b|hours?\\b)`));
    const people = lower.match(/(\d+)\s*(?:people|persons?|staff)\b/);
    return { hours: hours ? toNumber(hours[1]) : null, people: people ? Number(people[1]) : null };
  });
}
function parseHourlyRateJs(text) {
  const t = (text || "").toLowerCase().replace(/–/g, "-");
  const found = [...t.matchAll(new RegExp(`(${DIA_NUMBER})\\s*(k\\b)?`, "g"))].map((m) => toNumber(m[1]) * (m[2] ? 1000 : 1));
  if (!found.length) return null;
  if (/\/\s*h|\bhour|\bhr\b|\bhrs?\b|hourly|per hour/.test(t) && found[0] < 1000) return found[0];
  const annual = found.filter((n) => n >= 1000).slice(0, 2);
  if (annual.length) return annual.reduce((a, b) => a + b, 0) / annual.length / 2080;
  return found[0] > 0 ? found[0] : null;
}
// Mirrors inputs.input_gaps. Systems: the scorer's SCOPE_RISK_SYSTEMS never fires on a blank answer, so blank is enough.
function detectInputGaps(content) {
  const { values, known, heading, workflowsSection } = parseIntakeFields(content || "");
  const looksLikeQuestionnaire = known || heading;
  const tasks = parseWorkflowsJs(values.workflow_tasks);
  const gaps = [];
  if (!tasks.length || tasks.some((task) => task.hours === null)) gaps.push("hours");
  if (parseHourlyRateJs(values.hourly_cost) === null) gaps.push("hourly_cost");
  if (!/\d/.test(values.success_metric || "")) gaps.push("success_metric");
  if (workflowsSection) {
    if (tasks.length && tasks.some((task) => task.people === null)) gaps.push("people");
    if (isBlankAnswer(values.systems_access)) gaps.push("systems");
  }
  return { looksLikeQuestionnaire, workflowsSection, gaps };
}

const InputsPrompt = (() => {
  const section = document.querySelector("#inputsPrompt");
  const list = document.querySelector("#inputsPromptList");
  const oldLine = document.querySelector("#inputsPromptOld");
  const dismissButton = document.querySelector("#inputsPromptDismiss");
  const servicesWrap = document.querySelector("#inputsPromptServices");
  const servicesLink = document.querySelector("#inputsPromptLink");
  const describedBy = questionnaire.getAttribute("aria-describedby") || "intakeNote dropHint";
  const DEBOUNCE_MS = 800;
  let timer = null;
  let used = false;     // shown, dismissed or run in this intake: at most once per intake
  let showing = false;

  function render(state) {
    oldLine.hidden = state.workflowsSection;
    list.replaceChildren(...state.gaps.map((key) => {
      const [label, prompt, why] = INPUT_PROMPTS[key];
      const item = document.createElement("li");
      item.dataset.input = key;
      const strong = document.createElement("strong");
      strong.textContent = `${label}. `;
      const why_ = document.createElement("small");
      why_.className = "inputs-prompt-why";
      why_.textContent = why;
      item.append(strong, prompt, document.createElement("br"), why_);
      return item;
    }));
  }
  function hide() {
    showing = false;
    section.hidden = true;
    questionnaire.setAttribute("aria-describedby", describedBy);
  }
  function evaluate() {
    timer = null;
    if (used || questionnaire.readOnly) return;
    const state = detectInputGaps(questionnaire.value);
    if (!state.looksLikeQuestionnaire || !state.gaps.length) return;
    used = true;
    showing = true;
    render(state);
    section.hidden = false;
    questionnaire.setAttribute("aria-describedby", `${describedBy} inputsPromptTitle`);
    const n = state.gaps.length;
    statusText.textContent = `${n} key input${n === 1 ? " is" : "s are"} missing. Tips are below the composer.`;
  }
  function schedule() {
    if (used) return;
    clearTimeout(timer);
    timer = setTimeout(evaluate, DEBOUNCE_MS);
  }
  function liveUpdate() {
    if (!showing) {
      if (timer) schedule();  // still settling after a trigger: restart the debounce
      return;
    }
    const state = detectInputGaps(questionnaire.value);
    if (!state.gaps.length) { hide(); return; }  // all filled: removes itself, no announcement
    render(state);
  }
  function dismiss() {
    hide();
    questionnaire.focus();
  }
  function endSession() {
    clearTimeout(timer);
    timer = null;
    used = true;
    hide();
  }
  function newSession() {
    clearTimeout(timer);
    timer = null;
    used = false;
    hide();
  }
  function setServicesUrl(url) {
    servicesWrap.hidden = !url;
    if (url) servicesLink.href = url;
    else servicesLink.removeAttribute("href");
  }
  dismissButton.addEventListener("click", dismiss);
  section.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); dismiss(); }
  });
  questionnaire.addEventListener("input", liveUpdate);
  questionnaire.addEventListener("dictationend", schedule);
  // A cleared composer ends the intake: the next text starts a new session.
  questionnaire.addEventListener("input", () => { if (!questionnaire.value.trim()) newSession(); });
  setServicesUrl(null);
  return { schedule, endSession, newSession, setServicesUrl, evaluateNow: evaluate };
})();

// The services link (decision 1): its own endpoint, kept apart from the Donate config.
fetch("/api/consulting")
  .then((response) => (response.ok ? response.json() : {}))
  .then((config) => InputsPrompt.setServicesUrl(typeof config.consultingUrl === "string" && config.consultingUrl.startsWith("https://") ? config.consultingUrl : null))
  .catch(() => InputsPrompt.setServicesUrl(null));

// Export bar: Re-run with updated answers (addendum §7.2).
const rerunUpdated = document.querySelector("#rerunUpdated");
rerunUpdated.addEventListener("click", () => {
  const entry = readCabinet().find((item) => item.id === currentEntryId);
  if (entry) { rerunIntake(entry); return; }
  rerunIntake({ id: null, title: latestPayload?.intake?.company_name || "this intake", questionnaire: lastRunQuestionnaire || questionnaire.value });
});
