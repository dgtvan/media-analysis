<#
.SYNOPSIS
    Names, paths and helpers shared by build.ps1, install.ps1 and uninstall.ps1 (dot-sourced).
#>

$AppId     = 'MediaAnalysis'
$AppName   = 'Media Analysis'
$ExeName   = "$AppId.exe"
$Publisher = 'VanDang'
$Repo      = Split-Path -Parent $PSScriptRoot
$Dist      = Join-Path $Repo 'dist'
$Root      = Join-Path $env:LOCALAPPDATA "$Publisher\$AppId"
$AppDir    = Join-Path $Root 'app'
$DataDir   = Join-Path $Root 'data'
$ExePath   = Join-Path $AppDir $ExeName
$RunKey    = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$Shortcut  = Join-Path ([Environment]::GetFolderPath('Programs')) "$AppName.lnk"

# The core's runtime (standard section 1.1): what other apps can use is shared in <Publisher>\dependencies, what only
# this app uses is in <AppId>\dependencies. Environments are folders of hardlinks into the uv cache.
$Runtimes  = Join-Path $env:LOCALAPPDATA "$Publisher\dependencies"
$AppDeps   = Join-Path $Root 'dependencies'
$EnvsDir   = Join-Path $AppDeps 'envs'
$ModelsDir = Join-Path $Runtimes 'models\huggingface'
$WhisperModels = @('large-v3')
$FfmpegVersion = '9.0.2'   # a Gyan.FFmpeg winget version

# Install locations used before standard v2.2; their data is copied by the app, never deleted by the scripts.
$LegacyInstallDirs = @((Join-Path $env:LOCALAPPDATA "Programs\$AppId"), (Join-Path $env:LOCALAPPDATA "$AppId\app"))
$LegacyPickup = "D:\Apps\$AppId\pickup\$env:USERNAME"

function Write-Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$(Split-Path -Leaf $Exe) $($Arguments -join ' ') failed (exit code $LASTEXITCODE)." }
}

function Find-Tool([string]$Name, [string]$WingetPattern, [string]$WingetId, [switch]$Optional) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    # winget's PATH entry only reaches shells started after the install.
    $packages = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    $found = Get-ChildItem -Path (Join-Path $packages $WingetPattern) -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1
    if ($found) { return $found.FullName }
    if ($Optional) { return $null }
    throw "$Name not found. Install it with: winget install $WingetId"
}

function Use-SharedUv {
    $env:UV_CACHE_DIR = Join-Path $Runtimes 'uv-cache'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $Runtimes 'python'
    $env:UV_LINK_MODE = 'hardlink'
    $env:UV_MANAGED_PYTHON = '1'
    return Find-Tool 'uv' 'astral-sh.uv*\uv.exe' 'astral-sh.uv'
}

function Set-UserPath([string[]]$Add, [string[]]$Remove) {
    $current = @([Environment]::GetEnvironmentVariable('Path', 'User') -split ';' | Where-Object { $_ })
    $entries = @($current | Where-Object { $Remove -notcontains $_ })
    foreach ($dir in $Add) { if ($entries -notcontains $dir) { $entries += $dir } }
    if (($entries -join ';') -ne ($current -join ';')) {
        [Environment]::SetEnvironmentVariable('Path', ($entries -join ';'), 'User')
    }
}

# Standard section 3.5: ask the app to close, and let it refuse while it is busy (an analysis runs or is queued).
function Stop-App([switch]$Force) {
    $own = @(Get-CimInstance Win32_Process -Filter "Name = '$ExeName'" | Where-Object {
        (Invoke-CimMethod -InputObject $_ -MethodName GetOwner).User -eq $env:USERNAME
    })
    if ($own.Count -eq 0) { return }

    Write-Step "Asking $AppName to close"
    $refused = $null
    try {
        $refused = [Threading.EventWaitHandle]::OpenExisting("Local\$AppId-ExitRefused")
        $null = $refused.Reset()
        $exit = [Threading.EventWaitHandle]::OpenExisting("Local\$AppId-Exit")
        $null = $exit.Set()
        $exit.Dispose()
    } catch {
        # An app from before standard v2.2 has no close events; it counts as not answering.
    }
    $answer = 'none'
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-Process -Id $own.ProcessId -ErrorAction SilentlyContinue)) { $answer = 'closed'; break }
        if ($refused -and $refused.WaitOne(250)) { $answer = 'refused'; break }
        if (-not $refused) { Start-Sleep -Milliseconds 250 }
    }
    if ($refused) { $refused.Dispose() }
    if ($answer -eq 'closed') { return }

    if (-not $Force) {
        if ($answer -eq 'refused') {
            Write-Host "$AppName is busy (an analysis is running or queued). Let it finish or stop it, then run this again, or use -Force (the analysis stops; analysing the project again continues where it stopped)." -ForegroundColor Yellow
        } else {
            Write-Host "$AppName did not answer the close request within 10 s. Exit it from the tray menu, then run this again, or use -Force." -ForegroundColor Yellow
        }
        exit 3
    }

    Write-Step "Closing $AppName (-Force)"
    # /T also ends the analysis and its helpers (ffmpeg, Claude Code).
    $own | ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>&1 | Out-Null }
    Wait-Process -Id $own.ProcessId -Timeout 30 -ErrorAction SilentlyContinue
}
