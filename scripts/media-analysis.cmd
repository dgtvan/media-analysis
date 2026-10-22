@echo off
rem Per-account entry point, installed as %LOCALAPPDATA%\Programs\MediaAnalysis\media-analysis.cmd (on PATH).
rem Before each run: install the latest ready build from the publish folder into current\, record the result,
rem then run it. See docs/DEPLOYMENT.md.
setlocal EnableDelayedExpansion
set "INSTALL=%~dp0"
set "CURRENT_DIR=%INSTALL%current"
set "STATE_DIR=%LOCALAPPDATA%\MediaAnalysis"

set "SOURCE="
set "CURRENT="
set "LATEST="
set "ATTEMPT="
if exist "%CURRENT_DIR%\update-source.txt" set /p SOURCE=<"%CURRENT_DIR%\update-source.txt"
if exist "%CURRENT_DIR%\build-id.txt" set /p CURRENT=<"%CURRENT_DIR%\build-id.txt"

if defined SOURCE if exist "!SOURCE!\builds" (
    for /f "delims=" %%d in ('dir /b /ad /o-n "!SOURCE!\builds" 2^>nul') do (
        if not defined LATEST if exist "!SOURCE!\builds\%%d\build-id.txt" (
            set "ID="
            set /p ID=<"!SOURCE!\builds\%%d\build-id.txt"
            if "!ID!"=="%%d" set "LATEST=%%d"
        )
    )
)

set "STATE=running"
if defined LATEST if not "!LATEST!"=="!CURRENT!" (
    set "ATTEMPT=!LATEST!"
    rem build-id.txt goes last, so an interrupted copy is retried on the next run.
    robocopy "!SOURCE!\builds\!LATEST!" "%CURRENT_DIR%" /MIR /XF build-id.txt /R:3 /W:1 /NFL /NDL /NJH /NJS /NP >nul
    if errorlevel 8 (
        set "STATE=failed: could not copy build !LATEST!"
    ) else (
        copy /y "!SOURCE!\builds\!LATEST!\build-id.txt" "%CURRENT_DIR%\build-id.txt" >nul
        set "CURRENT=!LATEST!"
    )
)

if not exist "%STATE_DIR%" mkdir "%STATE_DIR%" 2>nul
> "%STATE_DIR%\update-state.txt" (
    echo(!STATE!
    echo(!CURRENT!
    echo(!ATTEMPT!
    echo(%DATE% %TIME%
)
if defined SOURCE (
    mkdir "!SOURCE!\pickup\%USERNAME%" 2>nul
    copy /y "%STATE_DIR%\update-state.txt" "!SOURCE!\pickup\%USERNAME%\status.txt" >nul 2>&1
)
if not "!STATE!"=="running" echo media-analysis: !STATE!; using build !CURRENT! 1>&2

if not exist "%CURRENT_DIR%\run.cmd" (
    echo media-analysis is not installed correctly. Run install.cmd again. 1>&2
    exit /b 1
)
call "%CURRENT_DIR%\run.cmd" %*
set "RC=!ERRORLEVEL!"

rem Keep this launcher in step with the build. The block is parsed before it runs, so replacing this file
rem inside it is safe.
fc /b "%CURRENT_DIR%\media-analysis.cmd" "%~f0" >nul 2>&1 || (
    copy /y "%CURRENT_DIR%\media-analysis.cmd" "%~f0" >nul 2>&1
    exit /b !RC!
)
exit /b %RC%
