param(
    [ValidateSet("Light", "Full", "Gpu", "Upload", "Fallback")]
    [string]$Mode = "Light",
    [ValidateSet("cpu", "gpu", "none")]
    [string]$Transcription = "cpu",
    [switch]$CleanUvCache
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$TempRoot = Join-Path $Root ".installer-tmp"
$UvCache = Join-Path $Root ".uv-cache"
New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null
New-Item -ItemType Directory -Force -Path $UvCache | Out-Null

$env:TEMP = $TempRoot
$env:TMP = $TempRoot
$env:UV_CACHE_DIR = $UvCache
$env:UV_NO_CACHE = "1"
$env:VIRALCUTTER_NO_PAUSE = "1"

if ($CleanUvCache) {
    $OldUvCache = Join-Path $env:LOCALAPPDATA "uv\cache"
    if (Test-Path $OldUvCache) {
        Write-Host "Removing old uv cache from C: $OldUvCache"
        Remove-Item -Recurse -Force $OldUvCache
    } else {
        Write-Host "No old uv cache found on C."
    }
}

$BatArgs = @()
switch ($Mode) {
    "Light"  { $BatArgs = @() }
    "Full"   { $BatArgs = @("full") }
    "Gpu"    { $BatArgs = @("gpu", "full") }
    "Upload" { $BatArgs = @("upload") }
    "Fallback" { $BatArgs = @("fallback") }
}
if ($Mode -in @("Full", "Gpu") -and $Transcription -eq "gpu") {
    $BatArgs = @("gpu", "full")
} elseif ($Mode -eq "Full" -and $Transcription -eq "cpu") {
    $BatArgs = @("full")
} elseif ($Transcription -eq "none") {
    $BatArgs = @($BatArgs | Where-Object { $_ -ne "full" -and $_ -ne "gpu" })
}

Write-Host "Project drive: $Root"
Write-Host "TEMP/TMP: $TempRoot"
Write-Host "uv cache: $UvCache"
Write-Host "Install mode: $Mode"
Write-Host "Transcription mode: $Transcription"

$Installer = Join-Path $Root "install_dependencies.bat"
if (-not (Test-Path $Installer)) {
    throw "install_dependencies.bat was not found in $Root"
}

& $Installer @BatArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "OUSSAMA Cutter setup failed. Run diagnostics with:" -ForegroundColor Yellow
    Write-Host ".\\.venv\\Scripts\\python.exe -m scripts.transcription_diagnostics --json" -ForegroundColor Yellow
}
exit $LASTEXITCODE
