# Design: DIA Last-Minute Feature Pack (Overnight Run)

## Date: 2026-06-10
## Context

Navy requested 7 last-minute features for the DIA demo before ~9am EST, built
autonomously overnight. The app is a zero-dependency stack: Python stdlib HTTP
server (`app.py`) serving `templates/index.html`, `static/app.js`,
`static/style.css`. Constraint: keep it dependency-free and avoid touching the
tested backend surface (`app.py`, `client_discovery/`).

## Approach

Frontend-only implementation using built-in browser APIs. Rejected
alternatives: server-side TTS via Gemini/Minimax (adds API cost + latency +
backend changes), a rich-text editor library (violates zero-dependency
constraint), X API integration (needs OAuth keys; web intent needs none).

## Specification

1. **TTS (text-to-speech)** — Web Speech `speechSynthesis`. "Listen" button on
   the document output and the agent output. Click toggles speak/stop. Hidden
   if the browser lacks support.
2. **Voice responses** — "Auto-speak" toggle; when on, agent replies are
   spoken aloud automatically as they arrive. Persisted in localStorage.
3. **Conversation mode** — Web Speech `SpeechRecognition`. Mic button dictates
   into the Ask-DIA box. "Conversation" toggle runs the hands-free loop:
   listen → send to agent → speak reply → listen again. Stops on toggle-off,
   error, or unsupported browser (button disabled with explanation).
4. **Draft email editor** — lightweight editor section under the results pane:
   "Edit in draft editor" loads the active document (typically the proposal)
   into a textarea for tweaks; toolbar has Copy and Paste buttons using
   standard clipboard SVG icons, plus Clear. Copy uses the existing clipboard
   helper; Paste uses `navigator.clipboard.readText` with fallback messaging.
5. **Accessibility** — proper tablist/tab/tabpanel roles with arrow-key
   navigation; `aria-live` on status, agent output, and issues; `aria-pressed`
   on toggles; `aria-label` on icon buttons; skip-to-content link; visible
   `:focus-visible` outlines; `prefers-reduced-motion` respected (no
   animations added that ignore it).
6. **Light mode** — theme toggle (sun/moon) in the topbar. Defaults to
   `prefers-color-scheme`, persists choice in localStorage, implemented as a
   `[data-theme="light"]` variable override. Hardcoded `#101012` backgrounds
   move into a `--inset` variable so both themes work.
7. **Share to X** — "Share on X" button in the export bar, builds an
   `https://x.com/intent/post` URL with the tier/score summary text and opens
   in a new tab. No API keys needed.

All changes confined to `templates/index.html`, `static/app.js`,
`static/style.css` (+ README feature list). One commit per feature cluster.

## Open Questions

None blocking. Speech APIs degrade gracefully in unsupported browsers; share
text content is conservative (tier + score only, no client PII like company
names in the prefilled tweet).
