param()

$ErrorActionPreference = "Stop"
$joy = Get-Process -Name "JoyOfProgramming-Win64-Shipping" |
    Select-Object -First 1
if (-not $joy -or -not $joy.Path) {
    throw "JOY is not running."
}
$joyProject = Split-Path (Split-Path (Split-Path $joy.Path))
$python = Join-Path $joyProject `
    "Content\000_MyContent\External\python-3.10.4-embed-amd64\python.exe"

& $python -m pip install --disable-pip-version-check "PyNaCl>=1.5,<2"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install the Windows Guard dependency."
}
