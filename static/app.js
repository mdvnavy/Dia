const questionnaire = document.querySelector("#questionnaire");
const loadSample = document.querySelector("#loadSample");
const runAgent = document.querySelector("#runAgent");
const statusText = document.querySelector("#status");
const tier = document.querySelector("#tier");
const score = document.querySelector("#score");
const issues = document.querySelector("#issues");
const output = document.querySelector("#documentOutput");
const tabs = [...document.querySelectorAll(".tab")];
const copyAll = document.querySelector("#copyAll");
const downloadMarkdown = document.querySelector("#downloadMarkdown");
const downloadJson = document.querySelector("#downloadJson");
const copySection = document.querySelector("#copySection");

let documents = {};
let latestPayload = null;
let activeDoc = "client-profile.md";
const documentOrder = [
  "client-profile.md",
  "opportunity-analysis.md",
  "proposal-draft.md",
];

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

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    activeDoc = tab.dataset.doc;
    tabs.forEach((item) => item.classList.toggle("active", item === tab));
    renderDocument(activeDoc);
  });
});

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

function setExportAvailability(isAvailable) {
  copyAll.disabled = !isAvailable;
  downloadMarkdown.disabled = !isAvailable;
  downloadJson.disabled = !isAvailable;
  copySection.disabled = !isAvailable;
}

function buildMarkdownExport() {
  return documentOrder
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
    statusText.textContent = "Copy failed";
  }
}

function copyTextFallback(text) {
  const scratch = document.createElement("textarea");
  scratch.value = text;
  scratch.setAttribute("readonly", "");
  scratch.style.position = "fixed";
  scratch.style.top = "-9999px";
  document.body.appendChild(scratch);
  scratch.select();
  document.execCommand("copy");
  scratch.remove();
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
