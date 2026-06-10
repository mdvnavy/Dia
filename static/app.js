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

loadSample.addEventListener("click", async () => {
  statusText.textContent = "Loading sample...";
  const response = await fetch("/api/sample");
  const payload = await response.json();
  questionnaire.value = payload.questionnaire;
  statusText.textContent = "Sample loaded";
});

runAgent.addEventListener("click", async () => {
  statusText.textContent = "Processing...";
  runAgent.disabled = true;
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
    statusText.textContent = "Complete";
  } catch (error) {
    statusText.textContent = error.message;
    setExportAvailability(latestPayload !== null);
  } finally {
    runAgent.disabled = false;
  }
});

function selectTab(tab, { focus = false } = {}) {
  activeDoc = tab.dataset.doc;
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
  runChat.disabled = true;
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
    runChat.disabled = false;
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
const draftCopy = document.querySelector("#draftCopy");
const draftPaste = document.querySelector("#draftPaste");
const draftClear = document.querySelector("#draftClear");

function refreshDraftControls() {
  const hasText = draftEditor.value.trim().length > 0;
  draftCopy.disabled = !hasText;
  draftClear.disabled = !hasText;
  draftLoad.disabled = !documents[activeDoc];
}

draftEditor.addEventListener("input", refreshDraftControls);

draftLoad.addEventListener("click", () => {
  const content = documents[activeDoc];
  if (!content) {
    statusText.textContent = "Run an intake first";
    return;
  }
  draftEditor.value = content;
  refreshDraftControls();
  draftEditor.focus();
  statusText.textContent = `${activeLabel()} loaded into draft editor`;
});

draftCopy.addEventListener("click", async () => {
  await copyText(draftEditor.value, "Draft copied");
});

draftPaste.addEventListener("click", async () => {
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
    const start = draftEditor.selectionStart ?? draftEditor.value.length;
    const end = draftEditor.selectionEnd ?? draftEditor.value.length;
    draftEditor.setRangeText(clip, start, end, "end");
    refreshDraftControls();
    draftEditor.focus();
    statusText.textContent = "Pasted from clipboard";
  } catch (error) {
    statusText.textContent = "Paste blocked by browser - use Ctrl+V in the editor";
    draftEditor.focus();
  }
});

draftClear.addEventListener("click", () => {
  draftEditor.value = "";
  refreshDraftControls();
  draftEditor.focus();
  statusText.textContent = "Draft cleared";
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
  let gotResult = false;
  rec.onstart = () => {
    recognizing = true;
    micButton.setAttribute("aria-pressed", "true");
  };
  rec.onresult = (event) => {
    gotResult = true;
    onTranscript(event.results[0][0].transcript.trim());
  };
  rec.onerror = (event) => {
    gotResult = true;
    onError(event.error);
  };
  rec.onend = () => {
    recognizing = false;
    micButton.setAttribute("aria-pressed", "false");
    if (!gotResult) {
      onError("no-speech");
    }
  };
  rec.start();
  return rec;
}

function stopConversation(message) {
  conversationActive = false;
  conversationMode.setAttribute("aria-pressed", "false");
  conversationMode.classList.remove("recording");
  if (recognition && recognizing) {
    recognition.abort();
  }
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
      recognition.abort();
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
    if (recognizing) {
      recognition.abort();
    }
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
  const url = `https://x.com/intent/post?text=${encodeURIComponent(text)}`;
  window.open(url, "_blank", "noopener");
  statusText.textContent = "Opening X share window";
});
