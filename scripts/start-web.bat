@echo off
rem ---------------------------------------------------------------------------
rem Builds the web UI and starts the local dev proxy (default http://127.0.0.1:8080).
rem cmd.exe equivalent of scripts\start-web.ps1.
rem
rem   1. Loads the emulator env (refreshes .emulator\ca.pem, used by the proxy for TLS).
rem   2. Builds web\target\dist (mvn package -pl web -DskipTests).
rem   3. Runs web\src\main\python\devproxy.py, which reads the filegateway Function
rem      URL from "terraform output" (deploy first with scripts\deploy.bat).
rem
rem Usage:  scripts\start-web.bat [--port N]     (--port 0 = any free port). Stop with Ctrl+C.
rem ---------------------------------------------------------------------------
setlocal

set "_PORT=8080"
if /i "%~1"=="/?" goto :usage
if /i "%~1"=="--help" goto :usage
if /i "%~1"=="--port" set "_PORT=%~2"
if /i "%~1"=="-Port" set "_PORT=%~2"
if not "%~1"=="" if /i not "%~1"=="--port" if /i not "%~1"=="-Port" (
    echo unknown argument: %~1 1>&2
    goto :usage
)
if "%_PORT%"=="" (
    echo --port needs a value 1>&2
    goto :usage
)

call "%~dp0emulator-env.bat"
if errorlevel 1 exit /b 1

pushd "%~dp0.."
call mvn -q package -pl web -DskipTests
if errorlevel 1 (
    popd
    echo ERROR: building the web UI failed 1>&2
    exit /b 1
)

rem The proxy needs only the standard library, so any Python 3.12+ works.
set "_PY=%CD%\.venv\Scripts\python.exe"
if not exist "%_PY%" set "_PY=python"

"%_PY%" "%CD%\web\src\main\python\devproxy.py" --port %_PORT%
set "_RC=%ERRORLEVEL%"
popd
exit /b %_RC%

:usage
echo Usage: scripts\start-web.bat [--port N]
exit /b 2
