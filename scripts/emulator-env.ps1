<#
.SYNOPSIS
    Connects the current PowerShell session to the local aws-emulator container.

.DESCRIPTION
    The emulator generates new SigV4 keys every time it starts and serves HTTPS
    with its own CA. This script copies the per-run keys, CA certificate and Java
    truststore out of the container into .emulator/ (git-ignored), writes a
    host-side AWS config file, and sets the environment variables that the AWS
    CLI, AWS SDKs and Terraform read.

    Re-run it whenever the emulator container restarts.

    It must be DOT-SOURCED so the environment variables persist in your session:

        . .\scripts\emulator-env.ps1

.PARAMETER ContainerName
    Name of the emulator container. Default: aws-emulator

.PARAMETER Port
    Port the emulator listens on (also the folder name of its config). Default: 9297
#>
param(
    [string]$ContainerName = 'aws-emulator',
    [int]$Port = 9297
)

$ErrorActionPreference = 'Stop'

if ($MyInvocation.InvocationName -ne '.') {
    Write-Warning "Run this script dot-sourced so the variables persist:  . .\scripts\emulator-env.ps1"
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$outDir = Join-Path $repoRoot '.emulator'
$endpoint = "https://127.0.0.1:$Port"
$srcDir = "/home/emulator/.aws/emulator/$Port"

# 1. The container must be running.
$running = docker ps --filter "name=^/$ContainerName$" --format '{{.Names}}'
if ($running -ne $ContainerName) {
    throw "Container '$ContainerName' is not running. Start it first (docker start $ContainerName)."
}

# 2. Copy the per-run files out of the container.
New-Item -ItemType Directory -Force $outDir | Out-Null
foreach ($file in 'ca.pem', 'truststore.p12', 'emulator_credentials') {
    docker cp "${ContainerName}:$srcDir/$file" (Join-Path $outDir $file) | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to copy $file from $ContainerName" }
}

$caFile = Join-Path $outDir 'ca.pem'
$credFile = Join-Path $outDir 'emulator_credentials'
$configFile = Join-Path $outDir 'config'

# 3. Write a host-side config. The emulator's own config points ca_bundle at a
#    path inside the container, so it can't be used as-is.
@"
[default]
region = us-east-1
endpoint_url = $endpoint
ca_bundle = $caFile
s3 =
  signature_version = s3v4
  addressing_style = path
"@ | Set-Content -Path $configFile -Encoding ascii

# 4. Environment for the AWS CLI / SDKs / Terraform in this session.
$env:AWS_CONFIG_FILE = $configFile
$env:AWS_SHARED_CREDENTIALS_FILE = $credFile
$env:AWS_PROFILE = 'default'
$env:AWS_REGION = 'us-east-1'
$env:AWS_DEFAULT_REGION = 'us-east-1'
$env:AWS_ENDPOINT_URL = $endpoint
$env:AWS_CA_BUNDLE = $caFile
$env:EMULATOR_TRUSTSTORE = Join-Path $outDir 'truststore.p12'

# 5. Health check (curl.exe trusts the emulator CA explicitly; no -k).
$health = curl.exe -s --cacert $caFile "$endpoint/__admin/health"
if ($health -ne 'ok') {
    throw "Emulator health check failed at $endpoint (response: '$health')"
}

Write-Host "Connected to aws-emulator at $endpoint (health: ok). Files in $outDir"
