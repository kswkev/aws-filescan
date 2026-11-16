@echo off
rem ---------------------------------------------------------------------------
rem Connects the CURRENT cmd session to the local aws-emulator container.
rem cmd.exe equivalent of scripts\emulator-env.ps1.
rem
rem The emulator generates new SigV4 keys every time it starts and serves HTTPS
rem with its own CA. This copies the per-run keys, CA certificate and Java
rem truststore out of the container into .emulator\ (git-ignored), writes a
rem host-side AWS config file and sets the variables the AWS CLI, SDKs and
rem Terraform read.
rem
rem Usage (from a cmd prompt or another .bat file):
rem     call scripts\emulator-env.bat [container-name] [port]
rem
rem The variables are set in the calling cmd session. Do not run it from
rem PowerShell (it would start a separate cmd and the variables would be lost);
rem use scripts\emulator-env.ps1 there. Re-run after every emulator restart.
rem ---------------------------------------------------------------------------

set "_EMU_CONTAINER=%~1"
if "%_EMU_CONTAINER%"=="" set "_EMU_CONTAINER=aws-emulator"
set "_EMU_PORT=%~2"
if "%_EMU_PORT%"=="" set "_EMU_PORT=9297"
for %%I in ("%~dp0..") do set "_EMU_ROOT=%%~fI"
set "_EMU_OUT=%_EMU_ROOT%\.emulator"
set "_EMU_ENDPOINT=https://127.0.0.1:%_EMU_PORT%"
set "_EMU_SRC=/home/emulator/.aws/emulator/%_EMU_PORT%"
set "_EMU_RC=1"

rem 1. The container must be running.
set "_EMU_RUNNING="
for /f "delims=" %%N in ('docker ps --filter "name=^/%_EMU_CONTAINER%$" --format "{{.Names}}" 2^>nul') do set "_EMU_RUNNING=%%N"
if /i not "%_EMU_RUNNING%"=="%_EMU_CONTAINER%" (
    echo ERROR: container '%_EMU_CONTAINER%' is not running. Start it first: docker start %_EMU_CONTAINER% 1>&2
    goto :_emu_done
)

rem 2. Copy the per-run files out of the container.
if not exist "%_EMU_OUT%" mkdir "%_EMU_OUT%"
for %%F in (ca.pem truststore.p12 emulator_credentials) do (
    docker cp "%_EMU_CONTAINER%:%_EMU_SRC%/%%F" "%_EMU_OUT%\%%F" >nul
    if errorlevel 1 (
        echo ERROR: failed to copy %%F from %_EMU_CONTAINER% 1>&2
        goto :_emu_done
    )
)

rem 3. Host-side config (the emulator's own config points ca_bundle inside the container).
>  "%_EMU_OUT%\config" echo [default]
>> "%_EMU_OUT%\config" echo region = us-east-1
>> "%_EMU_OUT%\config" echo endpoint_url = %_EMU_ENDPOINT%
>> "%_EMU_OUT%\config" echo ca_bundle = %_EMU_OUT%\ca.pem
>> "%_EMU_OUT%\config" echo s3 =
>> "%_EMU_OUT%\config" echo   signature_version = s3v4
>> "%_EMU_OUT%\config" echo   addressing_style = path

rem 4. Environment for the AWS CLI / SDKs / Terraform in this session.
set "AWS_CONFIG_FILE=%_EMU_OUT%\config"
set "AWS_SHARED_CREDENTIALS_FILE=%_EMU_OUT%\emulator_credentials"
set "AWS_PROFILE=default"
set "AWS_REGION=us-east-1"
set "AWS_DEFAULT_REGION=us-east-1"
set "AWS_ENDPOINT_URL=%_EMU_ENDPOINT%"
set "AWS_CA_BUNDLE=%_EMU_OUT%\ca.pem"
set "EMULATOR_TRUSTSTORE=%_EMU_OUT%\truststore.p12"

rem 5. Health check, trusting the emulator CA explicitly (no -k).
set "_EMU_HEALTH="
for /f "delims=" %%H in ('curl.exe -s --cacert "%_EMU_OUT%\ca.pem" "%_EMU_ENDPOINT%/__admin/health" 2^>nul') do set "_EMU_HEALTH=%%H"
if not "%_EMU_HEALTH%"=="ok" (
    echo ERROR: emulator health check failed at %_EMU_ENDPOINT% - response: '%_EMU_HEALTH%' 1>&2
    goto :_emu_done
)

echo Connected to aws-emulator at %_EMU_ENDPOINT% (health: ok). Files in %_EMU_OUT%
set "_EMU_RC=0"

:_emu_done
set "_EMU_CONTAINER="
set "_EMU_PORT="
set "_EMU_ROOT="
set "_EMU_OUT="
set "_EMU_ENDPOINT="
set "_EMU_SRC="
set "_EMU_RUNNING="
set "_EMU_HEALTH="
rem %_EMU_RC% is expanded when this block is parsed, i.e. before it is cleared.
(
    set "_EMU_RC="
    exit /b %_EMU_RC%
)
