param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$TempRoot = Join-Path $Root ".runtime-tmp"
$UvCache = Join-Path $Root ".uv-cache"
New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null
New-Item -ItemType Directory -Force -Path $UvCache | Out-Null

# Keep transient files and package cache off a full C: drive.
$env:TEMP = $TempRoot
$env:TMP = $TempRoot
$env:UV_CACHE_DIR = $UvCache
$env:UV_NO_CACHE = "1"
$env:VIRALCUTTER_NO_PAUSE = "1"

$Launcher = Join-Path $Root "run_webui.bat"
if (-not (Test-Path $Launcher)) {
    throw "run_webui.bat was not found in $Root"
}

Write-Host "Running OUSSAMA Cutter WebUI from: $Root"
Write-Host "TEMP/TMP: $TempRoot"
Write-Host "uv cache: $UvCache"
if ($Arguments) {
    & $Launcher @Arguments
} else {
    & $Launcher
}
$ExitCode = $LASTEXITCODE
exit $ExitCode
