<#
.SYNOPSIS
    Remove Media Analysis from the current account (docs/DEPLOYMENT.md).

.DESCRIPTION
    Follows the App deployment standard v1.4, section 6. Stops the app, removes start at sign-in, the Start Menu
    shortcut, the Settings > Apps entry, the PATH entry, the install folder (%LOCALAPPDATA%\MediaAnalysis\app) and
    this account's pickup folder. The data folder (settings, templates, logs) is kept unless -RemoveData is given.
    Shared files on D: and reports are never touched. A copy of this script lives in the install folder, so
    Settings > Apps can run it without the repo.

    Exit codes: 0 removed, 1 error, 3 refused because an analysis is running.

.PARAMETER RemoveData
    Also remove the data folder (%LOCALAPPDATA%\MediaAnalysis\data), data left in the locations used before
    standard v1.4, and the then-empty %LOCALAPPDATA%\MediaAnalysis.

.PARAMETER Force
    Uninstall even while an analysis is running. It stops.
#>
[CmdletBinding()]
param(
    [switch]$RemoveData,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$AppId = 'MediaAnalysis'
$AppName = 'Media Analysis'
$ExeName = "$AppId.exe"
$Root = Join-Path $env:LOCALAPPDATA $AppId
$InstallDir = Join-Path $Root 'app'
$DataDir = Join-Path $Root 'data'
$LegacyInstallDir = Join-Path $env:LOCALAPPDATA "Programs\$AppId"

function Step([string]$Text) { Write-Host "-> $Text" }

try {
    # Read before the install folder is removed.
    $publish = 'D:\Apps\MediaAnalysis'
    $sourceFile = Join-Path $InstallDir 'update-source.txt'
    if (Test-Path $sourceFile) { $publish = (Get-Content $sourceFile -Raw).Trim() }

    # 1. Stop this account's app. Only this account's processes expose their command line.
    $tray = @(Get-CimInstance Win32_Process -Filter "Name = '$ExeName'" | Where-Object { $_.CommandLine })
    $trayIds = @($tray | ForEach-Object ProcessId)
    $analyses = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -and $trayIds -contains $_.ParentProcessId })
    if ($analyses.Count -gt 0 -and -not $Force) {
        Write-Host 'An analysis is running. Let it finish, or run again with -Force.' -ForegroundColor Yellow
        exit 3
    }
    if ($tray.Count -gt 0) {
        Step 'Stopping the app'
        $tray | ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>&1 | Out-Null }
        $tray | ForEach-Object { Wait-Process -Id $_.ProcessId -Timeout 30 -ErrorAction SilentlyContinue }
    }

    # 2. Registration
    Step 'Removing start at sign-in, Start Menu shortcut, Settings > Apps entry and PATH entry'
    Remove-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name $AppId -ErrorAction SilentlyContinue
    Remove-Item (Join-Path ([Environment]::GetFolderPath('Programs')) "$AppName.lnk") -Force -ErrorAction SilentlyContinue
    Remove-Item "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppId" -Recurse -Force -ErrorAction SilentlyContinue
    $entries = @([Environment]::GetEnvironmentVariable('Path', 'User') -split ';' | Where-Object { $_ })
    $kept = @($entries | Where-Object { $_ -ne $InstallDir -and $_ -ne $LegacyInstallDir })
    if ($kept.Count -ne $entries.Count) { [Environment]::SetEnvironmentVariable('Path', ($kept -join ';'), 'User') }

    # 3. Install folder (and one from before v1.4). This script may be running from it; PowerShell has already
    #    read it, so deleting is safe.
    Step "Removing $InstallDir"
    Set-Location $env:TEMP
    foreach ($dir in @($InstallDir, $LegacyInstallDir)) { if (Test-Path $dir) { Remove-Item $dir -Recurse -Force } }

    # 4. Data, only when asked
    if ($RemoveData) {
        Step 'Removing settings, templates, logs and state'
        foreach ($dir in @($DataDir, (Join-Path $env:APPDATA $AppId))) { if (Test-Path $dir) { Remove-Item $dir -Recurse -Force } }
        # Files the app kept directly in this folder before v1.4.
        foreach ($item in @('logs', 'history.json', 'update-state.txt')) {
            $path = Join-Path $Root $item
            if (Test-Path $path) { Remove-Item $path -Recurse -Force }
        }
        if ((Test-Path $Root) -and -not (Get-ChildItem $Root -Force)) { Remove-Item $Root -Force }
    }

    # 5. This account's pickup folder, so publishing stops waiting for it
    Remove-Item (Join-Path $publish "pickup\$env:USERNAME") -Recurse -Force -ErrorAction SilentlyContinue

    Write-Host 'Media Analysis has been uninstalled.' -ForegroundColor Green
    if (-not $RemoveData) { Write-Host "Settings, templates and logs were kept in $DataDir (use -RemoveData to remove them)." }
    exit 0
} catch {
    Write-Host "Uninstall failed: $_" -ForegroundColor Red
    exit 1
}
