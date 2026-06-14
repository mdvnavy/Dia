# Implementation Plan: DIA Last-Minute Feature Pack

## Date: 2026-06-10
## Design Reference: docs/2026-06-10_overnight-features_design.md

### Task 1: Theme system + light mode
- Scope: refactor hardcoded colors into CSS variables, add
  `[data-theme="light"]` palette, topbar sun/moon toggle, localStorage
  persistence, `prefers-color-scheme` default.
- Depends on: nothing (foundation).
- Output: working theme toggle, both themes readable (contrast checked).
- Verify: browser screenshot in both themes.

### Task 2: Accessibility pass
- Scope: tablist/tab/tabpanel roles + arrow-key nav, aria-live regions
  (status, agent output, issues), skip link, focus-visible styles,
  aria-labels on icon-only buttons, reduced-motion guard.
- Depends on: Task 1 (focus colors use theme vars).
- Output: keyboard-navigable, screen-reader-announced UI.
- Verify: keyboard walkthrough in browser; check roles in DOM snapshot.

### Task 3: Draft email editor
- Scope: editor section with textarea, "Edit in draft editor" action on the
  active document, toolbar with Copy / Paste / Clear using standard clipboard
  SVG icons + visible text labels.
- Depends on: Tasks 1-2 (theme + a11y patterns).
- Output: editable draft with working copy/paste.
- Verify: load proposal into editor, edit, copy, paste in browser.

### Task 4: TTS + voice responses
- Scope: speechSynthesis wrapper; Listen toggle buttons on document + agent
  output; Auto-speak toggle (persisted) that speaks agent replies.
- Depends on: Task 2 (toggle button a11y pattern).
- Output: working read-aloud and auto-speak.
- Verify: browser test — audio fires (observe speaking state), buttons toggle.

### Task 5: Conversation mode
- Scope: SpeechRecognition mic dictation + hands-free loop
  (listen → send → speak → listen). Graceful unsupported-browser handling.
- Depends on: Task 4 (speech helpers).
- Output: conversation toggle runs the loop; mic button dictates.
- Verify: browser test where supported; verify disabled state otherwise.

### Task 6: Share on X
- Scope: Share button in export bar building x.com/intent/post URL with
  tier/score summary; disabled until results exist.
- Depends on: Task 1 (button styling only).
- Output: opens prefilled X compose in new tab.
- Verify: inspect generated URL; click test in browser.

### Task 7: Final verification + docs
- Scope: run pytest suite, full browser pass over all features both themes,
  README feature list update, self-review against this plan.
- Depends on: Tasks 1-6.
- Output: green tests, verified features, updated README, clean commits.
