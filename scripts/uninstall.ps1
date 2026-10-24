<#
.SYNOPSIS
    Removes an app for the current account.

.DESCRIPTION
    Follows the App deployment standard v2.3, section 3.3 (https://github.com/dgtvan/local-app-deployment-strategy).
    Stops the app, removes the start-at-sign-in entry, the Start Menu shortcut, the PATH entry and
    %LOCALAPPDATA%\VanDang\<AppId>\app, and this app's own dependencies
    (%LOCALAPPDATA%\VanDang\<AppId>\dependencies). Settings, templates, logs
    (%LOCALAPPDATA%\VanDang\<AppId>\data), reports and the dependencies shared by apps
    (%LOCALAPPDATA%\VanDang\dependencies) are never deleted. Succeeds and does nothing when the app isn't installed.

    Exit codes: 0 removed, 1 error, 3 refused because the app is busy (an analysis is running or queued) or didn't
    answer the close request.

.PARAMETER App
    The app's folder in the repo (apps\<app>), which holds its app.psd1. Its .cmd files pass it.

.PARAMETER NoDataWarning
    Don't print where the data was kept (used by deploy.ps1, which installs again right away).

.PARAMETER KeepDependencies
    Keep this app's own dependencies (used by deploy.ps1, so the next install doesn't recreate them).

.PARAMETER Force
    Close the app even during an analysis. The analysis stops.
#>
param([Parameter(Mandatory)][string]$App, [switch]$NoDataWarning, [switch]$KeepDependencies, [switch]$Force)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

try {
    # Read before anything is removed: where the reports go.
    $outputRoot = $null
    $settingsFile = Join-Path $DataDir 'settings.json'
    if (Test-Path $settingsFile) {
        try { $outputRoot = (Get-Content $settingsFile -Raw | ConvertFrom-Json).OutputRoot } catch { }
    }

    # 1. Stop
    Stop-App -Force:$Force

    # 2. Registration, including entries from earlier install methods
    Remove-ItemProperty $RunKey -Name $AppId -ErrorAction SilentlyContinue
    Remove-Item $Shortcut -Force -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $AppId -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item (Join-Path ([Environment]::GetFolderPath('Startup')) "$AppName.lnk") -Force -ErrorAction SilentlyContinue
    Remove-Item "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppId" -Recurse -Force -ErrorAction SilentlyContinue
    Set-UserPath -Remove (@($AppDir) + $LegacyInstallDirs)

    # 3. App folder, then the app's and the publisher's folders when nothing else is left in them
    if (Test-Path $AppDir) {
        Write-Step "Removing $AppDir"
        Remove-Item $AppDir -Recurse -Force
    }
    if (-not $KeepDependencies -and (Test-Path $AppDeps)) {
        Write-Step "Removing $AppDeps"
        Remove-Item $AppDeps -Recurse -Force
    }
    foreach ($dir in $Root, (Split-Path -Parent $Root)) {
        if ((Test-Path $dir) -and -not (Get-ChildItem $dir -Force)) { Remove-Item $dir -Force }
    }

    # 4. Data is kept
    if (-not $NoDataWarning) {
        Write-Host "$AppName has been uninstalled." -ForegroundColor Green
        if (Test-Path $DataDir) {
            Write-Host "WARNING: $AppName data was kept in $DataDir" -ForegroundColor Yellow
            Write-Host "         Delete that folder yourself if you no longer need it." -ForegroundColor Yellow
        }
        if ($outputRoot -and (Test-Path $outputRoot)) {
            Write-Host "WARNING: Reports were kept in $outputRoot" -ForegroundColor Yellow
        }
        if (Test-Path $Runtimes) {
            Write-Host "Dependencies other apps may use (Python, libraries, ffmpeg, Whisper models) were kept in $Runtimes"
        }
    }
    exit 0
} catch {
    Write-Host "Uninstall failed: $_" -ForegroundColor Red
    exit 1
}
