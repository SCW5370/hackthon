param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runDir = Join-Path $repoRoot ".run"
$pidFile = Join-Path $runDir "biolab-level.pid"
$levelScript = Join-Path $repoRoot "joy\BioLab_Guardian.py"
$stdoutLog = Join-Path $runDir "biolab.stdout.log"
$stderrLog = Join-Path $runDir "biolab.stderr.log"

New-Item -ItemType Directory -Force -Path $runDir | Out-Null

if (Test-Path -LiteralPath $pidFile) {
    $existingPid = [int](Get-Content -Raw -LiteralPath $pidFile)
    $existing = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "BioLab_Guardian is already running (PID $existingPid)."
        exit 0
    }
}

$existingRuntime = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -like "python*" -and
        $_.CommandLine -like "*BioLab_Guardian.py*"
    } |
    Select-Object -First 1
if ($existingRuntime) {
    $existingRuntime.ProcessId | Set-Content -LiteralPath $pidFile
    Write-Host "BioLab_Guardian is already running (PID $($existingRuntime.ProcessId))."
    exit 0
}

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) {
    $python = $venvPython
} else {
    $joy = Get-Process -Name "JoyOfProgramming-Win64-Shipping" |
        Select-Object -First 1
    if (-not $joy -or -not $joy.Path) {
        throw "JOY is not running. Start Steam and JOY, then open Level Editor."
    }
    $joyProject = Split-Path (Split-Path (Split-Path $joy.Path))
    $python = Join-Path $joyProject `
        "Content\000_MyContent\External\python-3.10.4-embed-amd64\python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        throw "JOY's bundled Python was not found at: $python"
    }
}

$process = Start-Process `
    -FilePath $python `
    -ArgumentList "`"$levelScript`"" `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru
$process.Id | Set-Content -LiteralPath $pidFile

Write-Host "Started BioLab_Guardian (PID $($process.Id))."
Write-Host "Logs: $stdoutLog and $stderrLog"
Write-Host "Keep JOY and Level Editor open."
