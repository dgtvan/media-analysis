<#
.SYNOPSIS
    Install Media Analysis for the current account from the latest published build (docs/DEPLOYMENT.md).

.DESCRIPTION
    Follows the App deployment standard v1.4, section 5.2. Run once by each account, from that account; after
    that the app updates itself. Copies the build (tray app + core code, a few MB) into
    %LOCALAPPDATA%\MediaAnalysis\app, registers start at sign-in, a Start Menu shortcut and a Settings > Apps entry,
    puts the media-analysis command on this account's PATH and starts the app. Python, libraries, ffmpeg and the
    Whisper model stay shared on D:. It does no app initialization and never touches the data folder; re-running it
    repairs the install. An install in the old location (%LOCALAPPDATA%\Programs\MediaAnalysis) is removed; the app
    copies its data to the new data folder itself on first start.

    Exit codes: 0 installed, 1 error, 3 refused because an analysis is running.

.PARAMETER NoStart
    Don't start the app after installing.

.PARAMETER Force
    Install even while an analysis is running. It stops; analysing the project again continues where it stopped.
#>
[CmdletBinding()]
param(
    [switch]$NoStart,
    [switch]$Force,
    [string]$PublishFolder = 'D:\Apps\MediaAnalysis'
)

$ErrorActionPreference = 'Stop'
$AppId = 'MediaAnalysis'
$AppName = 'Media Analysis'
$ExeName = "$AppId.exe"
$InstallDir = Join-Path $env:LOCALAPPDATA "$AppId\app"
$LegacyInstallDir = Join-Path $env:LOCALAPPDATA "Programs\$AppId"
$ExePath = Join-Path $InstallDir $ExeName
$RunKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppId"
$Shortcut = Join-Path ([Environment]::GetFolderPath('Programs')) "$AppName.lnk"
$IdPattern = '^\d{8}T\d{6}Z-[0-9a-f]{8}$'

function Step([string]$Text) { Write-Host "-> $Text" }

function Set-UserPath([string[]]$Add, [string[]]$Remove) {
    $entries = @([Environment]::GetEnvironmentVariable('Path', 'User') -split ';' | Where-Object { $_ -and $Remove -notcontains $_ })
    foreach ($dir in $Add) { if ($entries -notcontains $dir) { $entries += $dir } }
    [Environment]::SetEnvironmentVariable('Path', ($entries -join ';'), 'User')
}

try {
    # 1. Prerequisites
    if (-not (Get-ChildItem "$env:ProgramFiles\dotnet\shared\Microsoft.WindowsDesktop.App" -Directory -Filter '10.*' -ErrorAction SilentlyContinue)) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw '.NET 10 Desktop Runtime is missing and winget is not available.' }
        Step 'Installing .NET 10 Desktop Runtime (Windows may ask for permission)'
        winget install --id Microsoft.DotNet.DesktopRuntime.10 --exact --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { throw "Installing .NET 10 Desktop Runtime failed (winget exit code $LASTEXITCODE)." }
    }

    # 2. Latest ready build
    $latest = Get-ChildItem (Join-Path $PublishFolder 'builds') -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match $IdPattern } |
        Where-Object {
            $marker = Join-Path $_.FullName 'build-id.txt'
            (Test-Path $marker) -and ((Get-Content $marker -Raw).Trim() -eq $_.Name)
        } |
        Sort-Object Name -Descending | Select-Object -First 1
    if (-not $latest) { throw "No published build in $PublishFolder. Run publish.cmd first." }

    # 3. Stop this account's app, wherever it is installed. Only this account's processes expose their command line.
    $tray = @(Get-CimInstance Win32_Process -Filter "Name = '$ExeName'" | Where-Object { $_.CommandLine })
    $trayIds = @($tray | ForEach-Object ProcessId)
    $analyses = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -and $trayIds -contains $_.ParentProcessId })
    if ($analyses.Count -gt 0 -and -not $Force) {
        Write-Host 'An analysis is running. Let it finish, or run again with -Force (analysing again continues where it stopped).' -ForegroundColor Yellow
        exit 3
    }
    if ($tray.Count -gt 0) {
        Step 'Stopping the running app'
        # taskkill /T also ends the analysis and its helpers (ffmpeg, Claude Code).
        $tray | ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>&1 | Out-Null }
        $tray | ForEach-Object { Wait-Process -Id $_.ProcessId -Timeout 30 -ErrorAction SilentlyContinue }
    }

    # 4. Copy. Mirror guard (standard 2.2): /MIR deletes everything the build doesn't contain.
    if ((Split-Path -Leaf $InstallDir) -ne 'app') { throw "Refusing to mirror into ${InstallDir}: the install folder must be named 'app'." }
    Step "Installing build $($latest.Name) to $InstallDir"
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    robocopy $latest.FullName $InstallDir /MIR /R:5 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying build $($latest.Name) failed (robocopy exit $LASTEXITCODE)." }

    # 5. Register
    Step 'Registering start at sign-in, Start Menu shortcut, Settings > Apps entry and the media-analysis command'
    Set-ItemProperty $RunKey -Name $AppId -Value "`"$ExePath`""

    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Shortcut)
    $link.TargetPath = $ExePath
    $link.WorkingDirectory = $InstallDir
    $link.Description = 'Analyse folders of recordings into reports'
    $link.Save()

    New-Item -Path $UninstallKey -Force | Out-Null
    $values = @{
        DisplayName     = $AppName
        DisplayIcon     = $ExePath
        DisplayVersion  = $latest.Name
        Publisher       = 'Van Dang'
        InstallLocation = $InstallDir
        UninstallString = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\uninstall.ps1`""
    }
    foreach ($name in $values.Keys) { Set-ItemProperty -Path $UninstallKey -Name $name -Value $values[$name] }
    Set-ItemProperty -Path $UninstallKey -Name NoModify -Value 1 -Type DWord
    Set-ItemProperty -Path $UninstallKey -Name NoRepair -Value 1 -Type DWord
    $sizeKb = [int]((Get-ChildItem $InstallDir -Recurse -File | Measure-Object Length -Sum).Sum / 1KB)
    Set-ItemProperty -Path $UninstallKey -Name EstimatedSize -Value $sizeKb -Type DWord

    Set-UserPath -Add $InstallDir -Remove $LegacyInstallDir

    # 6. Clean up the install from before standard v1.4 (its data is copied by the app, never deleted here).
    if (Test-Path $LegacyInstallDir) {
        Step "Removing the old install in $LegacyInstallDir"
        Remove-Item $LegacyInstallDir -Recurse -Force
    }

    if (-not (Test-Path (Join-Path $HOME '.claude\.credentials.json')) -and -not $env:CLAUDE_CODE_OAUTH_TOKEN) {
        Write-Host 'No Claude login found for this account: sign in once with Claude Code (claude, then /login).' -ForegroundColor Yellow
    }

    # 7. Start
    if ($NoStart) {
        Write-Host "Installed build $($latest.Name) (not started). It starts at sign-in or from the Start Menu." -ForegroundColor Green
        exit 0
    }
    Step 'Starting Media Analysis'
    Start-Process $ExePath -WorkingDirectory $InstallDir
    Write-Host "Installed build $($latest.Name). Media Analysis is in the system tray, starts at sign-in and updates itself." -ForegroundColor Green
    exit 0
} catch {
    Write-Host "Install failed: $_" -ForegroundColor Red
    exit 1
}
