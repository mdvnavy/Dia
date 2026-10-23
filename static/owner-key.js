// Owner sign-in and visitor quota for Ask DIA.
//
// Visitors get a few free questions a day (the server enforces it). The owner
// pastes the owner key once; it stays in this browser's localStorage and is
// sent as `Authorization: Bearer <key>` on Ask DIA requests. The key is never
// logged, shown back, or sent anywhere except this site's /api/agent routes.
(function () {
  "use strict";

  const STORAGE_KEY = "dia-owner-key";

  function readKey() {
    try {
      return localStorage.getItem(STORAGE_KEY) || "";
    } catch (error) {
      return "";
    }
  }

  function writeKey(value) {
    try {
      if (value) localStorage.setItem(STORAGE_KEY, value);
      else localStorage.removeItem(STORAGE_KEY);
    } catch (error) {
      /* storage blocked: session only */
    }
  }

  function authHeaders() {
    const key = readKey();
    return key ? { Authorization: `Bearer ${key}` } : {};
  }

  // --- quota badge next to the Ask DIA status ------------------------------

  const agentStatus = document.querySelector("#agentStatus");
  const quotaBadge = document.createElement("small");
  quotaBadge.id = "agentQuota";
  quotaBadge.className = "agent-quota";
  quotaBadge.setAttribute("aria-live", "polite");
  quotaBadge.style.marginLeft = "0.5em";
  quotaBadge.style.opacity = "0.75";
  quotaBadge.style.fontWeight = "normal";
  if (agentStatus) agentStatus.insertAdjacentElement("afterend", quotaBadge);

  let lastStatus = null;

  function describe(status) {
    if (!status) return "";
    if (status.owner) return "Owner: unlimited";
    if (status.owner_key === "invalid") return "Owner key not recognised";
    const quota = status.quota;
    if (!quota) return "";
    if (quota.remaining <= 0) return `Today's ${quota.limit} questions used`;
    return `${quota.remaining} of ${quota.limit} questions left today`;
  }

  async function refreshQuota() {
    try {
      const response = await fetch("/api/agent/status", { headers: authHeaders() });
      lastStatus = await response.json();
    } catch (error) {
      lastStatus = null;
    }
    quotaBadge.textContent = describe(lastStatus);
    renderSignIn();
    return lastStatus;
  }

  // Refresh after every agent reply (app.js writes replies and errors,
  // including the daily-limit message, into #agentOutput).
  const agentOutput = document.querySelector("#agentOutput");
  if (agentOutput && "MutationObserver" in window) {
    let timer = null;
    new MutationObserver(() => {
      clearTimeout(timer);
      timer = setTimeout(refreshQuota, 300);
    }).observe(agentOutput, { childList: true, characterData: true, subtree: true });
  }

  // --- owner sign-in panel --------------------------------------------------

  const panel = document.createElement("section");
  panel.className = "owner-signin help-settings";
  panel.setAttribute("aria-labelledby", "ownerSignInTitle");
  panel.innerHTML = `
    <h3 id="ownerSignInTitle">Owner sign-in</h3>
    <p class="owner-signin-help">Visitors get a few free Ask DIA questions a day.
      The owner can paste the owner key once to ask without limits; it stays in
      this browser only.</p>
    <form id="ownerKeyForm" autocomplete="off">
      <label for="ownerKeyInput">Owner key</label>
      <input id="ownerKeyInput" type="password" autocomplete="off" spellcheck="false" />
      <button type="submit" id="ownerKeySave">Sign in</button>
      <button type="button" id="ownerKeyForget">Sign out</button>
    </form>
    <p id="ownerKeyState" class="owner-signin-state" aria-live="polite"></p>`;

  const helpDialog = document.querySelector("#diaHelpDialog");
  if (helpDialog) {
    // Help & settings dialog (feature/tia-ui and later): add a section.
    const closeForm = helpDialog.querySelector('form[method="dialog"]');
    helpDialog.insertBefore(panel, closeForm || null);
  } else {
    // Older layout without a settings dialog: a collapsible under Ask DIA.
    const details = document.createElement("details");
    details.className = "owner-signin-details";
    const summary = document.createElement("summary");
    summary.textContent = "Owner sign-in";
    details.append(summary, panel);
    const heading = agentStatus ? agentStatus.closest("h2") : null;
    if (heading) heading.insertAdjacentElement("afterend", details);
    else document.body.append(details);
  }

  const form = panel.querySelector("#ownerKeyForm");
  const input = panel.querySelector("#ownerKeyInput");
  const forget = panel.querySelector("#ownerKeyForget");
  const state = panel.querySelector("#ownerKeyState");

  function renderSignIn() {
    const hasKey = Boolean(readKey());
    forget.hidden = !hasKey;
    if (!hasKey) {
      state.textContent = "Not signed in.";
    } else if (lastStatus && lastStatus.owner) {
      state.textContent = "Signed in as owner: unlimited questions.";
    } else if (lastStatus && lastStatus.owner_key === "invalid") {
      state.textContent = "That key wasn't recognised.";
    } else {
      state.textContent = "Key saved.";
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const value = input.value.trim();
    input.value = "";
    if (!value) return;
    writeKey(value);
    refreshQuota();
  });

  forget.addEventListener("click", () => {
    writeKey("");
    refreshQuota();
  });

  window.DIA_OWNER = { authHeaders, refreshQuota, describe };
  renderSignIn();
  refreshQuota();
})();
