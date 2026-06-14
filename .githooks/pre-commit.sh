#!/usr/bin/env bash
# Gitleaks pre-commit hook (defense-in-depth)
# - Scans staged changes (and the working tree as a safety net) for secrets.
# - Exits 1 on any finding, which aborts the commit.
# - Bypassed only when the user explicitly sets GITLEAKS_SKIP=1 (e.g., emergency WIP).

set -u

# 1. Skip switch (escape hatch)
if [ "${GITLEAKS_SKIP:-0}" = "1" ]; then
  echo "[gitleaks] skipped (GITLEAKS_SKIP=1)"
  exit 0
fi

# 2. Locate the gitleaks binary
GITLEAKS_BIN="${GITLEAKS_BIN:-gitleaks}"
if ! command -v "$GITLEAKS_BIN" >/dev/null 2>&1; then
  if [ -x "/c/Users/nwakw/AppData/Local/Microsoft/WinGet/Links/gitleaks.exe" ]; then
    GITLEAKS_BIN="/c/Users/nwakw/AppData/Local/Microsoft/WinGet/Links/gitleaks.exe"
  elif [ -x "/mnt/c/Users/nwakw/AppData/Local/Microsoft/WinGet/Links/gitleaks.exe" ]; then
    GITLEAKS_BIN="/mnt/c/Users/nwakw/AppData/Local/Microsoft/WinGet/Links/gitleaks.exe"
  else
    echo "[gitleaks] ERROR: gitleaks binary not found in PATH." >&2
    echo "[gitleaks] Install from https://github.com/gitleaks/gitleaks or set GITLEAKS_BIN." >&2
    exit 1
  fi
fi

echo "[gitleaks] scanning staged + unstaged working tree..."

# 3. Primary scan: staged changes (pre-commit semantics)
"$GITLEAKS_BIN" protect --staged --redact --verbose
PRIMARY_RC=$?

# 4. Secondary safety net: re-scan every file git already knows about
#    (tracked + untracked-but-not-ignored). This honors .gitignore so we
#    don't waste 17s scanning 400MB of .venv/site-packages garbage, and
#    it catches edits to tracked files that weren't restaged.
WORKING_FILES=$(git ls-files -co --exclude-standard 2>/dev/null | tr '\n' ' ')
if [ -n "${WORKING_FILES// }" ]; then
  # shellcheck disable=SC2086
  "$GITLEAKS_BIN" detect --no-banner --redact $WORKING_FILES >/dev/null 2>&1
  SECONDARY_RC=$?
else
  SECONDARY_RC=0
fi

if [ $PRIMARY_RC -ne 0 ] || [ $SECONDARY_RC -ne 0 ]; then
  echo "" >&2
  echo "[gitleaks] COMMIT BLOCKED: secrets detected in working tree." >&2
  echo "[gitleaks] If this is a false positive, audit the finding, fix the source," >&2
  echo "[gitleaks] then retry. To bypass in an emergency: GITLEAKS_SKIP=1 git commit ..." >&2
  exit 1
fi

echo "[gitleaks] clean. Proceeding with commit."
exit 0
