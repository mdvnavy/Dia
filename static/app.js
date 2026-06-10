const questionnaire = document.querySelector("#questionnaire");
const loadSample = document.querySelector("#loadSample");
const runAgent = document.querySelector("#runAgent");
const statusText = document.querySelector("#status");
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
const copySection = document.querySelector("#copySection");

let documents = {};
let latestPayload = null;
let activeDoc = "client-profile.md";

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

function setIntakeCollapsed(collapsed) {
  intakeBody.hidden = collapsed;
  intakeToggle.hidden = false;
  intakeToggle.setAttribute("aria-expanded", String(!collapsed));
  intakeToggle.textContent = collapsed ? "Edit intake" : "Collapse intake";
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
  statusText.textContent = "Sample loaded";
});

runAgent.addEventListener("click", async () => {
  statusText.textContent = "Processing...";
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
    latestPayload = payload;
    documents = payload.documents;
    tier.textContent = payload.score.tier;
    score.textContent = `${payload.score.total_score}/${payload.score.max_score} · ${payload.score.timeline}`;
    renderIssues(payload.issues);
    renderDocument(activeDoc);
    setExportAvailability(true);
    setIntakeCollapsed(true);
    statusText.textContent = "Complete - intake collapsed, ready to work";
  } catch (error) {
    statusText.textContent = error.message;
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

async function refreshAgentStatus() {
  try {
    const response = await fetch("/api/agent/status");
    const payload = await response.json();
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
}

async function sendAgentMessage() {
  const message = agentMessage.value.trim();
  if (!message) {
    agentOutput.textContent = "Type a question for the agent first.";
    return null;
  }
  const context = documents[activeDoc] ? `\n\nContext:\n${documents[activeDoc]}` : "";
  agentOutput.textContent = "Thinking...";
  setBusy(runChat, true);
  try {
    const response = await fetch("/api/agent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: message + context }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Agent run failed");
    }
    agentOutput.textContent = payload.reply || "(empty response)";
    return payload.reply || "";
  } catch (error) {
    agentOutput.textContent = error.message;
    return null;
  } finally {
    setBusy(runChat, false);
  }
}

runChat.addEventListener("click", async () => {
  const reply = await sendAgentMessage();
  if (autoSpeakEnabled && reply) {
    speakText(reply, listenAgent);
  }
});

refreshAgentStatus();

copyAll.addEventListener("click", async () => {
  await copyText(buildMarkdownExport(), "All outputs copied");
});

downloadMarkdown.addEventListener("click", () => {
  downloadFile("dia-outputs.md", buildMarkdownExport(), "text/markdown");
  statusText.textContent = "Markdown downloaded";
});

downloadJson.addEventListener("click", () => {
  downloadFile(
    "dia-export.json",
    JSON.stringify(latestPayload, null, 2),
    "application/json"
  );
  statusText.textContent = "JSON downloaded";
});

copySection.addEventListener("click", async () => {
  await copyText(documents[activeDoc] || "", `${activeLabel()} copied`);
});

const draftEditor = document.querySelector("#draftEditor");
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
    statusText.textContent = "Run an intake first";
    return;
  }
  replaceDraftContent(content);
  refreshDraftControls();
  statusText.textContent = `${activeLabel()} loaded into draft editor`;
});

async function copyDraft() {
  const text = draftText();
  if (!text.trim()) {
    statusText.textContent = "No draft to copy";
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
      statusText.textContent = "Draft copied (with formatting)";
      return;
    }
  } catch (error) {
    // Rich copy denied; fall through to the plain-text helper and its fallbacks.
  }
  await copyText(text, "Draft copied");
}

async function pasteIntoDraft() {
  if (!navigator.clipboard?.readText || !window.isSecureContext) {
    statusText.textContent = "Paste blocked by browser - use Ctrl+V in the editor";
    draftEditor.focus();
    return;
  }
  try {
    const clip = await navigator.clipboard.readText();
    if (!clip) {
      statusText.textContent = "Clipboard is empty";
      return;
    }
    draftEditor.focus();
    document.execCommand("insertText", false, clip);
    refreshDraftControls();
    statusText.textContent = "Pasted from clipboard";
  } catch (error) {
    statusText.textContent = "Paste blocked by browser - use Ctrl+V in the editor";
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
  statusText.textContent = "Draft cleared";
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

function renderDocument(name) {
  output.textContent = documents[name] || "Run an intake to generate this document.";
  refreshDraftControls();
}

function setExportAvailability(isAvailable) {
  copyAll.disabled = !isAvailable;
  downloadMarkdown.disabled = !isAvailable;
  downloadJson.disabled = !isAvailable;
  copySection.disabled = !isAvailable;
  listenDocument.disabled = !isAvailable;
  shareX.disabled = !isAvailable;
}

function buildMarkdownExport() {
  return tabs
    .map((tab) => tab.dataset.doc)
    .map((name) => documents[name])
    .filter(Boolean)
    .join("\n\n---\n\n");
}

async function copyText(text, successMessage) {
  if (!text) {
    statusText.textContent = "No output to copy";
    return;
  }

  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      copyTextFallback(text);
    }
    statusText.textContent = successMessage;
  } catch (error) {
    // Embedded contexts (iframes, previews) can deny the async clipboard API
    // even on localhost; the execCommand fallback usually still works there.
    try {
      copyTextFallback(text);
      statusText.textContent = successMessage;
    } catch (fallbackError) {
      statusText.textContent = "Copy failed";
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

function listenOnce({ onTranscript, onError }) {
  const rec = new SpeechRecognitionImpl();
  rec.lang = navigator.language || "en-US";
  rec.interimResults = false;
  rec.maxAlternatives = 1;
  let settled = false;
  rec.onstart = () => {
    recognizing = true;
    micButton.setAttribute("aria-pressed", "true");
  };
  rec.onresult = (event) => {
    settled = true;
    if (!rec.cancelled) {
      onTranscript(event.results[0][0].transcript.trim());
    }
  };
  rec.onerror = (event) => {
    settled = true;
    if (!rec.cancelled) {
      onError(event.error);
    }
  };
  rec.onend = () => {
    recognizing = false;
    micButton.setAttribute("aria-pressed", "false");
    if (!settled && !rec.cancelled) {
      onError("no-speech");
    }
  };
  rec.start();
  return rec;
}

// Aborting a recognizer still fires its error/end events asynchronously;
// the cancelled flag keeps those late events from overwriting whatever
// status message the cancelling code just set.
function cancelRecognition() {
  if (recognition && recognizing) {
    recognition.cancelled = true;
    recognition.abort();
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

if (SpeechRecognitionImpl) {
  micButton.addEventListener("click", () => {
    if (conversationActive) {
      stopConversation("Conversation ended");
      return;
    }
    if (recognizing) {
      cancelRecognition();
      statusText.textContent = "Dictation cancelled";
      return;
    }
    statusText.textContent = "Listening...";
    recognition = listenOnce({
      onTranscript: (text) => {
        if (text) {
          const existing = agentMessage.value.trim();
          agentMessage.value = existing ? `${existing} ${text}` : text;
          statusText.textContent = "Dictation added";
          agentMessage.focus();
        }
      },
      onError: (error) => {
        statusText.textContent =
          error === "not-allowed"
            ? "Microphone permission denied"
            : error === "no-speech"
              ? "No speech detected"
              : `Dictation failed (${error})`;
      },
    });
  });

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

// --- Share on X ---

const shareX = document.querySelector("#shareX");

shareX.addEventListener("click", () => {
  if (!latestPayload) {
    statusText.textContent = "Run an intake first";
    return;
  }
  const scoreInfo = latestPayload.score;
  const text =
    `DIA scored this discovery intake ${scoreInfo.total_score}/${scoreInfo.max_score} ` +
    `and recommends the "${scoreInfo.tier}" tier. Profile, analysis, and proposal ` +
    `drafted in seconds by a Gemini ADK agent.`;
  const url = `https://x.com/intent/tweet?text=${encodeURIComponent(text)}`;
  window.open(url, "_blank", "noopener");
  statusText.textContent = "Opening X share window";
});
