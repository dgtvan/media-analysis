<#
.SYNOPSIS
    Install media-analysis for the current account from the latest published build (docs/DEPLOYMENT.md).

.DESCRIPTION
    Copies the build (a few hundred KB of app code) into %LOCALAPPDATA%\Programs\MediaAnalysis, puts the
    media-analysis command on this account's PATH and adds a Settings > Apps entry. Python, libraries, ffmpeg
    and the Whisper model stay shared on D:. Safe to re-run; it repairs the install with the latest build.
#>
[CmdletBinding()]
param(
    [string]$PublishFolder = 'D:\Apps\MediaAnalysis'
)

$ErrorActionPreference = 'Stop'
$AppId = 'MediaAnalysis'
$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\$AppId"
$Current = Join-Path $InstallDir 'current'
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppId"

try {
    $latest = Get-ChildItem (Join-Path $PublishFolder 'builds') -Directory -ErrorAction SilentlyContinue |
        Where-Object {
            $marker = Join-Path $_.FullName 'build-id.txt'
            (Test-Path $marker) -and ((Get-Content $marker -TotalCount 1) -eq $_.Name)
        } |
        Sort-Object Name -Descending | Select-Object -First 1
    if (-not $latest) { throw "No published build in $PublishFolder. Run publish.cmd first." }

    New-Item -ItemType Directory -Force -Path $Current | Out-Null
    robocopy $latest.FullName $Current /MIR /R:3 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying build $($latest.Name) failed (robocopy exit $LASTEXITCODE)." }
    Copy-Item (Join-Path $Current 'media-analysis.cmd') $InstallDir -Force

    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $entries = @($userPath -split ';' | Where-Object { $_ })
    if ($entries -notcontains $InstallDir) {
        [Environment]::SetEnvironmentVariable('Path', (($entries + $InstallDir) -join ';'), 'User')
    }

    New-Item -Path $UninstallKey -Force | Out-Null
    $uninstall = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$Current\uninstall.ps1`""
    $values = @{
        DisplayName     = 'Media Analysis'
        DisplayVersion  = $latest.Name
        Publisher       = 'Van Dang'
        InstallLocation = $InstallDir
        UninstallString = $uninstall
    }
    foreach ($name in $values.Keys) { Set-ItemProperty -Path $UninstallKey -Name $name -Value $values[$name] }
    Set-ItemProperty -Path $UninstallKey -Name NoModify -Value 1 -Type DWord
    Set-ItemProperty -Path $UninstallKey -Name NoRepair -Value 1 -Type DWord
    $sizeKb = [int]((Get-ChildItem $InstallDir -Recurse -File | Measure-Object Length -Sum).Sum / 1KB)
    Set-ItemProperty -Path $UninstallKey -Name EstimatedSize -Value $sizeKb -Type DWord

    Write-Host "Installed build $($latest.Name) in $InstallDir"
    Write-Host 'Open a new terminal and run: media-analysis doctor'
    if (-not (Test-Path (Join-Path $HOME '.claude\.credentials.json')) -and -not $env:CLAUDE_CODE_OAUTH_TOKEN) {
        Write-Host 'No Claude login found for this account: sign in once with Claude Code (claude, then /login).' -ForegroundColor Yellow
    }
    exit 0
} catch {
    Write-Host "Install failed: $_" -ForegroundColor Red
    exit 1
}
