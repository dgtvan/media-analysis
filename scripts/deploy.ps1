<#
.SYNOPSIS
    Builds an app, uninstalls it and installs the new build for the current account.

.DESCRIPTION
    Follows the App deployment standard v2.3, section 3.4 (https://github.com/dgtvan/local-app-deployment-strategy).
    Stops at the first failure; a failed build stops it before anything is uninstalled.

    Exit codes: 0 installed and running, 1 error, 3 refused because the app is busy (an analysis is running or
    queued) or didn't answer.

.PARAMETER App
    The app's folder in the repo (apps\<app>), which holds its app.psd1. Its .cmd files pass it.

.PARAMETER Force
    Passed to uninstall and install: close the app even during an analysis. The analysis stops.
#>
param([Parameter(Mandatory)][string]$App, [switch]$Force)

$ErrorActionPreference = 'Stop'

function Invoke-Step($script, [string[]]$arguments) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot $script) @arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$appArg = @('-App', (Resolve-Path $App).Path)
$forceArg = @(if ($Force) { '-Force' })
Invoke-Step 'build.ps1' $appArg
Invoke-Step 'uninstall.ps1' ($appArg + @('-NoDataWarning', '-KeepDependencies') + $forceArg)
Invoke-Step 'install.ps1' ($appArg + $forceArg)
