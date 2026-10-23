@echo off
rem Started hidden by the running app, from a copy in %TEMP%, to install a published build.
rem   apply-update.cmd <app PID> "<build folder>" "<install folder>"
rem Waits for the app to exit, mirrors the build into the install folder and starts the new version.
setlocal
set "APP_PID=%~1"
set "BUILD=%~2"
set "TARGET=%~3"

:wait
tasklist /FI "PID eq %APP_PID%" /NH 2>nul | find "%APP_PID%" >nul
if not errorlevel 1 (
    ping -n 2 127.0.0.1 >nul
    goto wait
)

rem Mirror guard (deployment standard 2.2): /MIR deletes whatever the build doesn't contain, so only ever mirror
rem into a folder named "app". An install in an older location is left as it is and reports the failed update.
for %%I in ("%TARGET%") do set "TARGET_NAME=%%~nxI"
if /i not "%TARGET_NAME%"=="app" goto start

rem If the copy fails, the app still starts; it sees it is not on the attempted build and reports the failure.
robocopy "%BUILD%" "%TARGET%" /MIR /R:10 /W:1 /NFL /NDL /NJH /NJS /NP >nul

:start
start "" "%TARGET%\MediaAnalysis.exe"

rem Delete this script after cmd has finished reading it.
(goto) 2>nul & del "%~f0"
