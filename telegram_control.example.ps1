# OUSSAMA Cutter — Telegram Control Center (safe local setup)
# Copy this file to telegram_control.local.ps1 before running it.
# Never commit the local copy and never send the token in chat or support requests.

$ErrorActionPreference = "Stop"

Write-Host "OUSSAMA Cutter Telegram Control Center"
Write-Host "The bot uses local outbound long polling; no public port is opened."
Write-Host ""

$secureToken = Read-Host "Bot Token from BotFather (local only)" -AsSecureString
$tokenPtr = [IntPtr]::Zero
$token = $null
try {
    $tokenPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPtr)
}
finally {
    if ($tokenPtr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPtr)
    }
}

if ([string]::IsNullOrWhiteSpace($token)) {
    throw "A non-empty Bot Token is required."
}

$chatIds = Read-Host "Allowed Chat ID(s), separated by commas or spaces"
if ([string]::IsNullOrWhiteSpace($chatIds) -or $chatIds -notmatch "^-?\d+(?:[\s,;]+-?\d+)*$") {
    throw "Enter one or more numeric Chat IDs only."
}

$notify = Read-Host "Enable short terminal notifications for succeeded/failed/cancelled jobs? (y/N)"
$notifyValue = if ($notify -match "^(y|yes)$") { "1" } else { "0" }

# Set both the current PowerShell process and the persistent User environment.
$env:VIRALCUTTER_TELEGRAM_ENABLED = "1"
$env:VIRALCUTTER_TELEGRAM_BOT_TOKEN = $token
$env:VIRALCUTTER_TELEGRAM_CHAT_IDS = $chatIds
$env:VIRALCUTTER_TELEGRAM_NOTIFY_TERMINAL = $notifyValue
[Environment]::SetEnvironmentVariable("VIRALCUTTER_TELEGRAM_ENABLED", "1", "User")
[Environment]::SetEnvironmentVariable("VIRALCUTTER_TELEGRAM_BOT_TOKEN", $token, "User")
[Environment]::SetEnvironmentVariable("VIRALCUTTER_TELEGRAM_CHAT_IDS", $chatIds, "User")
[Environment]::SetEnvironmentVariable("VIRALCUTTER_TELEGRAM_NOTIFY_TERMINAL", $notifyValue, "User")

# Do not retain the token in this script's variable longer than necessary.
$token = $null
Remove-Variable secureToken -ErrorAction SilentlyContinue
Remove-Variable tokenPtr -ErrorAction SilentlyContinue
Remove-Variable token -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Saved locally in the Windows User environment."
Write-Host "Close this PowerShell window, open a new one, and restart OUSSAMA Cutter."
Write-Host "The WebUI will show only readiness and the number of allowlisted Chat IDs."
Write-Host "If the token is ever exposed, revoke it with @BotFather and run this setup again."
