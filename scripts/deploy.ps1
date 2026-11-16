<#
.SYNOPSIS
    Builds, tests and deploys the whole stack to the local aws-emulator in one command.

.DESCRIPTION
    1. Loads the emulator's current keys/CA (scripts/emulator-env.ps1).
    2. Runs `mvn -Pdeploy install`: pytest for both lambdas, packages the lambda zips and
       the web UI, then `terraform init` + `terraform apply -auto-approve`.
    3. Prints the Terraform outputs.

    Usage:   .\scripts\deploy.ps1            (add -SkipTests to skip pytest)
#>
param([switch]$SkipTests)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot

. (Join-Path $PSScriptRoot 'emulator-env.ps1')

$mvnArgs = @('-Pdeploy', 'install')
if ($SkipTests) { $mvnArgs += '-DskipTests' }

Push-Location $repoRoot
try {
    & mvn @mvnArgs
    if ($LASTEXITCODE -ne 0) { throw "mvn $($mvnArgs -join ' ') failed (exit $LASTEXITCODE)" }
    Push-Location terraform
    try { terraform output } finally { Pop-Location }
}
finally {
    Pop-Location
}
