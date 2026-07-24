param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("normal", "legacy-attack")]
    [string]$Scenario,

    [switch]$ConfirmUnsafeDemo
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

if ($Scenario -eq "legacy-attack" -and -not $ConfirmUnsafeDemo) {
    throw "Legacy simulation requires -ConfirmUnsafeDemo."
}

if (Test-Path -LiteralPath $venvPython) {
    $argsList = @("-m", "joy.dev_control", $Scenario)
    if ($Scenario -eq "legacy-attack") {
        $argsList += "--confirm-unsafe-demo"
    }
    & $venvPython @argsList
    exit $LASTEXITCODE
}

$joy = Get-Process -Name "JoyOfProgramming-Win64-Shipping" |
    Select-Object -First 1
if (-not $joy -or -not $joy.Path) {
    throw "JOY is not running."
}
$joyProject = Split-Path (Split-Path (Split-Path $joy.Path))
$python = Join-Path $joyProject `
    "Content\000_MyContent\External\python-3.10.4-embed-amd64\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "JOY's bundled Python was not found at: $python"
}

$scenarioArgs = if ($Scenario -eq "legacy-attack") {
    "['joy.dev_control','legacy-attack','--confirm-unsafe-demo']"
} else {
    "['joy.dev_control','normal']"
}
$escapedRoot = $repoRoot.Replace("'", "\'")
$pythonCode = "import sys,runpy; sys.path.insert(0,r'$escapedRoot'); " +
    "sys.argv=$scenarioArgs; " +
    "runpy.run_module('joy.dev_control',run_name='__main__')"

Push-Location $repoRoot
try {
    & $python -c $pythonCode
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
