param([switch]$NoPause)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$candidates = @(
    (Join-Path $root ".python\tools\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe")
)
$python = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $python) {
    throw "Python 3.11-3.13 was not found. Install it for this user or place the NuGet runtime under .python."
}

Set-Location $root
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $python -m venv .venv
}
& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt
& ".venv\Scripts\python.exe" -m py_compile bot_all_in_one.py
Write-Host "Python environment is ready: $root\.venv" -ForegroundColor Green
if (-not $NoPause) { Read-Host "Press Enter to close" }
