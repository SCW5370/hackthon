param(
    [Parameter(Mandatory = $true)]
    [string]$Token,
    [int]$Port = 8791
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runDir = Join-Path $repoRoot ".run"
$pidFile = Join-Path $runDir "legacy-bridge.pid"
$runner = Join-Path $PSScriptRoot "run_windows_legacy_bridge.py"
$stdoutLog = Join-Path $runDir "legacy-bridge.stdout.log"
$stderrLog = Join-Path $runDir "legacy-bridge.stderr.log"
New-Item -ItemType Directory -Force $runDir | Out-Null

if (Test-Path -LiteralPath $pidFile) {
    $existingPid = [int](Get-Content -Raw -LiteralPath $pidFile)
    if (Get-Process -Id $existingPid -ErrorAction SilentlyContinue) {
        Write-Host "Legacy bridge is already running (PID $existingPid)."
        exit 0
    }
    Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
}

$joy = Get-Process -Name "JoyOfProgramming-Win64-Shipping" |
    Select-Object -First 1
if (-not $joy -or -not $joy.Path) {
    throw "JOY is not running."
}
$joyProject = Split-Path (Split-Path (Split-Path $joy.Path))
$python = Join-Path $joyProject `
    "Content\000_MyContent\External\python-3.10.4-embed-amd64\python.exe"

$env:ALLOW_UNSAFE_LEGACY_DEMO = "1"
$env:LAB_LEGACY_TOKEN = $Token
$env:LAB_LEGACY_BIND = "0.0.0.0"
$env:LAB_LEGACY_PORT = "$Port"
$process = Start-Process `
    -FilePath $python `
    -ArgumentList "`"$runner`"" `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden `
    -PassThru
Set-Content -LiteralPath $pidFile -Value $process.Id
Write-Host "Started UNSAFE Legacy demo bridge (PID $($process.Id), port $Port)."
