@echo off
setlocal
set "HERE=%~dp0"
set "QUIET="
if /i "%~1"=="/quiet" (set "QUIET=1" & shift)
set "ARGS="
:collect
if "%~1"=="" goto run
set ARGS=%ARGS% %1
shift
goto collect
:run
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%HERE%scripts\deploy.ps1"%ARGS%
set "RC=%ERRORLEVEL%"
if not defined QUIET pause
exit /b %RC%
