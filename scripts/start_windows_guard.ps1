param(
    [int]$Port = 8788
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runDir = Join-Path $repoRoot ".run"
$pidFile = Join-Path $runDir "safeexec-guard.pid"
$publicKey = Join-Path $runDir "public_key.txt"
$runner = Join-Path $PSScriptRoot "run_windows_guard.py"
$stdoutLog = Join-Path $runDir "safeexec-guard.stdout.log"
$stderrLog = Join-Path $runDir "safeexec-guard.stderr.log"

New-Item -ItemType Directory -Force $runDir | Out-Null
if (-not (Test-Path -LiteralPath $publicKey)) {
    throw "Missing Guard public key: $publicKey"
}

if (Test-Path -LiteralPath $pidFile) {
    $existingPid = [int](Get-Content -Raw -LiteralPath $pidFile)
    if (Get-Process -Id $existingPid -ErrorAction SilentlyContinue) {
        Write-Host "SafeExec Guard is already running (PID $existingPid)."
        exit 0
    }
    Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen `
    -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($listener) {
    try {
        $health = Invoke-RestMethod `
            -Uri "http://127.0.0.1:$Port/healthz" `
            -TimeoutSec 2
        if ($health.status -eq "ok") {
            Set-Content -LiteralPath $pidFile -Value $listener.OwningProcess
            Write-Host "SafeExec Guard is already serving (PID $($listener.OwningProcess))."
            exit 0
        }
    } catch {
        throw "Port $Port is occupied by PID $($listener.OwningProcess), but Guard health failed."
    }
}

$joy = Get-Process -Name "JoyOfProgramming-Win64-Shipping" |
    Select-Object -First 1
if (-not $joy -or -not $joy.Path) {
    throw "JOY is not running."
}
$joyProject = Split-Path (Split-Path (Split-Path $joy.Path))
$python = Join-Path $joyProject `
    "Content\000_MyContent\External\python-3.10.4-embed-amd64\python.exe"
$arguments = @(
    "`"$runner`"",
    "--key", "`"$publicKey`"",
    "--executor", "joy",
    "--host", "0.0.0.0",
    "--port", "$Port",
    "--joy-host", "127.0.0.1",
    "--joy-port", "18189"
)

$process = Start-Process `
    -FilePath $python `
    -ArgumentList $arguments `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden `
    -PassThru
Set-Content -LiteralPath $pidFile -Value $process.Id
Write-Host "Started SafeExec Guard (PID $($process.Id), port $Port)."
