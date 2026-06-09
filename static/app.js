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

let documents = {};
let activeDoc = "client-profile.md";

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
    documents = payload.documents;
    tier.textContent = payload.score.tier;
    score.textContent = `${payload.score.total_score}/${payload.score.max_score} · ${payload.score.timeline}`;
    renderIssues(payload.issues);
    renderDocument(activeDoc);
    statusText.textContent = "Complete";
  } catch (error) {
    statusText.textContent = error.message;
  } finally {
    runAgent.disabled = false;
  }
});

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    activeDoc = tab.dataset.doc;
    tabs.forEach((item) => item.classList.toggle("active", item === tab));
    renderDocument(activeDoc);
  });
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

runChat.addEventListener("click", async () => {
  const message = agentMessage.value.trim();
  if (!message) {
    agentOutput.textContent = "Type a question for the agent first.";
    return;
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
  } catch (error) {
    agentOutput.textContent = error.message;
  } finally {
    runChat.disabled = false;
  }
});

refreshAgentStatus();

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
}
