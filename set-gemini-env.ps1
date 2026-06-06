$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$envPath = Join-Path $repoRoot ".env"
$managedKeys = @(
    "GEMINI_API_KEY"
)

$secureKey = Read-Host "Gemini API key" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

if ([string]::IsNullOrWhiteSpace($plainKey)) {
    throw "GEMINI_API_KEY cannot be empty."
}

$existingLines = @()
if (Test-Path -LiteralPath $envPath) {
    foreach ($line in Get-Content -LiteralPath $envPath) {
        $name = ($line -split "=", 2)[0]
        if ($managedKeys -notcontains $name) {
            $existingLines += $line
        }
    }
}

$newLines = @(
    "GEMINI_API_KEY=$plainKey"
)

@($existingLines + $newLines) | Set-Content -LiteralPath $envPath -Encoding ascii
$plainKey = $null

Write-Host "Wrote local .env with GEMINI_API_KEY."
