<#
.SYNOPSIS
    Remove media-analysis from the current account (docs/DEPLOYMENT.md).

.DESCRIPTION
    Removes the PATH entry, the Settings > Apps entry, the install folder and this account's pickup folder.
    Templates and logs are kept unless -RemoveData is given. Shared files on D: are never touched.
#>
[CmdletBinding()]
param(
    [switch]$RemoveData
)

$ErrorActionPreference = 'Stop'
$AppId = 'MediaAnalysis'
$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\$AppId"

try {
    $sourceFile = Join-Path $InstallDir 'current\update-source.txt'
    $publish = if (Test-Path $sourceFile) { (Get-Content $sourceFile -TotalCount 1).Trim() } else { $null }

    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $entries = @($userPath -split ';' | Where-Object { $_ })
    if ($entries -contains $InstallDir) {
        [Environment]::SetEnvironmentVariable('Path', (($entries | Where-Object { $_ -ne $InstallDir }) -join ';'), 'User')
    }
    Remove-Item "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppId" -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
    if ($publish) {
        Remove-Item (Join-Path $publish "pickup\$env:USERNAME") -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($RemoveData) {
        foreach ($dir in @((Join-Path $env:APPDATA $AppId), (Join-Path $env:LOCALAPPDATA $AppId))) {
            if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }
        }
    }
    Write-Host 'media-analysis uninstalled.'
    exit 0
} catch {
    Write-Host "Uninstall failed: $_" -ForegroundColor Red
    exit 1
}
