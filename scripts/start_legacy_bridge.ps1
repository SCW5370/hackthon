param(
    [string]$BindAddress = "127.0.0.1",
    [int]$Port = 8791
)

$ErrorActionPreference = "Stop"
if ($env:ALLOW_UNSAFE_LEGACY_DEMO -ne "1") {
    throw "Set ALLOW_UNSAFE_LEGACY_DEMO=1 explicitly."
}
if ([string]::IsNullOrWhiteSpace($env:LAB_LEGACY_TOKEN)) {
    throw "Set LAB_LEGACY_TOKEN before starting the Legacy bridge."
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$joy = Get-Process -Name "JoyOfProgramming-Win64-Shipping" |
    Select-Object -First 1
if (-not $joy -or -not $joy.Path) {
    throw "JOY is not running."
}
$joyProject = Split-Path (Split-Path (Split-Path $joy.Path))
$python = Join-Path $joyProject `
    "Content\000_MyContent\External\python-3.10.4-embed-amd64\python.exe"

$env:LAB_LEGACY_BIND = $BindAddress
$env:LAB_LEGACY_PORT = "$Port"
Set-Location $repoRoot
& $python -m joy.legacy_bridge
