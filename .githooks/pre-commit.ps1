# Gitleaks pre-commit hook (PowerShell mirror)
# - Mirror of pre-commit.sh for native Windows users invoking the hook
#   via PowerShell or for users without a bash.exe on PATH.
# - Scans staged + working tree; exits 1 on findings (blocks commit).
# - Bypassed only when $env:GITLEAKS_SKIP = "1".

$ErrorActionPreference = 'Stop'

if ($env:GITLEAKS_SKIP -eq '1') {
    Write-Host "[gitleaks] skipped (GITLEAKS_SKIP=1)"
    exit 0
}

# Locate gitleaks
$gitleaks = $env:GITLEAKS_BIN
if (-not $gitleaks) {
    $candidate = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\gitleaks.exe'
    if (Test-Path $candidate) { $gitleaks = $candidate }
}
if (-not $gitleaks -or -not (Test-Path $gitleaks)) {
    $cmd = Get-Command gitleaks -ErrorAction SilentlyContinue
    if ($cmd) { $gitleaks = $cmd.Source }
}
if (-not $gitleaks) {
    Write-Error "[gitleaks] ERROR: gitleaks binary not found. Install from https://github.com/gitleaks/gitleaks or set `$env:GITLEAKS_BIN."
    exit 1
}

Write-Host "[gitleaks] scanning staged + unstaged working tree..."

# Primary: staged changes
& $gitleaks protect --staged --redact --verbose
$primary = $LASTEXITCODE

# Secondary: re-scan every file git knows about (tracked + non-ignored untracked).
# Using --no-banner and a narrow file list (per git ls-files) keeps this fast
# and honors .gitignore so we skip .venv, node_modules, etc.
$files = & git ls-files -co --exclude-standard 2>$null
if ($LASTEXITCODE -eq 0 -and $files) {
    $null = & $gitleaks detect --no-banner --redact $files
    $secondary = $LASTEXITCODE
}
else {
    $secondary = 0
}

if ($primary -ne 0 -or $secondary -ne 0) {
    Write-Error ""
    Write-Error "[gitleaks] COMMIT BLOCKED: secrets detected in working tree."
    Write-Error "[gitleaks] If this is a false positive, audit the finding, fix the source, then retry."
    Write-Error "[gitleaks] To bypass in an emergency: `$env:GITLEAKS_SKIP='1'; git commit ..."
    exit 1
}

Write-Host "[gitleaks] clean. Proceeding with commit."
exit 0
