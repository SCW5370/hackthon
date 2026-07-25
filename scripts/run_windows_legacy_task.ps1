param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "D:\steam\steamapps\common\JOY OF PROGRAMMING\JoyOfProgramming\Content\000_MyContent\External\python-3.10.4-embed-amd64\python.exe",
    [int]$Port = 8791,
    [int]$JoyWaitSeconds = 180
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path $RepoRoot).Path
$Runner = Join-Path $RepoRoot "scripts\run_windows_legacy_bridge.py"
$TokenFile = Join-Path $RepoRoot ".run\legacy_token.txt"
$RunDir = Join-Path $RepoRoot ".run"
$StdoutLog = Join-Path $RunDir "legacy-bridge.stdout.log"
$StderrLog = Join-Path $RunDir "legacy-bridge.stderr.log"

foreach ($Required in @($PythonExe, $Runner, $TokenFile)) {
    if (-not (Test-Path -LiteralPath $Required)) {
        throw "Missing required file: $Required"
    }
}

$Deadline = (Get-Date).AddSeconds($JoyWaitSeconds)
while ((Get-Date) -lt $Deadline) {
    $Joy = Get-Process -Name "JoyOfProgramming-Win64-Shipping" `
        -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $Joy) {
        try {
            $Rpc = Get-NetTCPConnection `
                -LocalPort 18189 `
                -State Listen `
                -ErrorAction Stop |
                Select-Object -First 1
            if ($null -ne $Rpc) {
                break
            }
        } catch {
            # JOY is still opening the level; keep waiting.
        }
    }
    Start-Sleep -Seconds 2
}
if ($null -eq $Joy -or $null -eq $Rpc) {
    throw "JOY RPC did not become ready within $JoyWaitSeconds seconds."
}

$Token = (Get-Content -Raw -LiteralPath $TokenFile).Trim()
if ([string]::IsNullOrWhiteSpace($Token)) {
    throw "Legacy demo token is empty."
}

$env:ALLOW_UNSAFE_LEGACY_DEMO = "1"
$env:LAB_LEGACY_TOKEN = $Token
$env:LAB_LEGACY_BIND = "0.0.0.0"
$env:LAB_LEGACY_PORT = "$Port"

& $PythonExe $Runner 1>>$StdoutLog 2>>$StderrLog
exit $LASTEXITCODE
