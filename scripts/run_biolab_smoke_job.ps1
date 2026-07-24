param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runDir = Join-Path $repoRoot ".run"
$stdoutLog = Join-Path $runDir "smoke.stdout.log"
$stderrLog = Join-Path $runDir "smoke.stderr.log"
$exitFile = Join-Path $runDir "smoke.exit"
$smokeScript = Join-Path $repoRoot "joy\smoke_test.py"

New-Item -ItemType Directory -Force -Path $runDir | Out-Null
Remove-Item $stdoutLog, $stderrLog, $exitFile -ErrorAction SilentlyContinue

$joy = Get-Process -Name "JoyOfProgramming-Win64-Shipping" |
    Select-Object -First 1
if (-not $joy -or -not $joy.Path) {
    throw "JOY is not running."
}
$joyProject = Split-Path (Split-Path (Split-Path $joy.Path))
$python = Join-Path $joyProject `
    "Content\000_MyContent\External\python-3.10.4-embed-amd64\python.exe"

$process = Start-Process `
    -FilePath $python `
    -ArgumentList "`"$smokeScript`"" `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru `
    -Wait
$process.ExitCode | Set-Content -LiteralPath $exitFile
