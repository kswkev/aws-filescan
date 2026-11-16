<#
.SYNOPSIS
    Builds the web UI and starts the local dev proxy on http://127.0.0.1:8080.

.DESCRIPTION
    1. Loads the emulator env (refreshes .emulator/ca.pem, which the proxy uses for TLS).
    2. Builds web/target/dist (mvn package -pl web -DskipTests).
    3. Runs web/src/main/python/devproxy.py, which reads the filegateway Function URL
       from `terraform output` (deploy first with scripts/deploy.ps1).

    Usage:  .\scripts\start-web.ps1 [-Port 8080]      Stop with Ctrl+C.
#>
param([int]$Port = 8080)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot

. (Join-Path $PSScriptRoot 'emulator-env.ps1')

Push-Location $repoRoot
try {
    mvn -q package -pl web -DskipTests
    if ($LASTEXITCODE -ne 0) { throw "building the web UI failed (exit $LASTEXITCODE)" }

    $python = Join-Path $repoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) { $python = 'python' }   # the proxy needs only the standard library
    & $python (Join-Path $repoRoot 'web\src\main\python\devproxy.py') --port $Port
}
finally {
    Pop-Location
}
