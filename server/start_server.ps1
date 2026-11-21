param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not $env:FOREX_API_KEY -or $env:FOREX_API_KEY.Length -lt 32) {
    throw "Set FOREX_API_KEY to a random secret of at least 32 characters before starting."
}

if (-not (Test-Path ".\\.venv\\Scripts\\python.exe")) {
    py -3.11 -m venv .venv
}

& ".\\.venv\\Scripts\\python.exe" -m pip install --upgrade pip
& ".\\.venv\\Scripts\\python.exe" -m pip install -r requirements.txt
& ".\\.venv\\Scripts\\python.exe" -m uvicorn app:app --host $HostAddress --port $Port
