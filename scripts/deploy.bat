@echo off
rem ---------------------------------------------------------------------------
rem Builds, tests and deploys the whole stack to the local aws-emulator.
rem cmd.exe equivalent of scripts\deploy.ps1.
rem
rem   1. Loads the emulator's current keys/CA (scripts\emulator-env.bat).
rem   2. Runs "mvn -Pdeploy install": pytest, lambda zips, web UI, then
rem      terraform init + terraform apply -auto-approve.
rem   3. Prints the Terraform outputs.
rem
rem Usage:  scripts\deploy.bat [--skip-tests]
rem ---------------------------------------------------------------------------
setlocal

set "_MVN_EXTRA="
if /i "%~1"=="--skip-tests" set "_MVN_EXTRA=-DskipTests"
if /i "%~1"=="-SkipTests" set "_MVN_EXTRA=-DskipTests"
if /i "%~1"=="/?" goto :usage
if /i "%~1"=="--help" goto :usage
if not "%~1"=="" if "%_MVN_EXTRA%"=="" (
    echo unknown argument: %~1 1>&2
    goto :usage
)

call "%~dp0emulator-env.bat"
if errorlevel 1 exit /b 1

pushd "%~dp0.."
rem "call" is required: mvn is mvn.cmd, and without call this script would end here.
call mvn -Pdeploy install %_MVN_EXTRA%
if errorlevel 1 goto :failed

pushd terraform
terraform output
popd
popd
exit /b 0

:failed
set "_RC=%ERRORLEVEL%"
popd
echo ERROR: deploy failed 1>&2
exit /b %_RC%

:usage
echo Usage: scripts\deploy.bat [--skip-tests]
exit /b 2
