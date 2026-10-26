<#
.SYNOPSIS
    Builds an app (its Windows app + the analysis core) into <app folder>\dist\.

.DESCRIPTION
    Follows the App deployment standard v2.3, section 3.1 (https://github.com/dgtvan/local-app-deployment-strategy).
    Builds whatever is in the working directory and writes nothing outside the repo. The core's runtime (Python,
    libraries, ffmpeg, Whisper model) is not part of the build: dist\runtime\ describes it, core.json points at where
    install.ps1 puts it (the dependency folders of standard section 1.1), and install.ps1 creates whatever is
    missing (docs/DEPLOYMENT.md).

    Exit codes: 0 built, 1 error.

.PARAMETER App
    The app's folder in the repo (apps\<app>), which holds its app.psd1. Its .cmd files pass it.
#>
param([Parameter(Mandatory)][string]$App)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

function Get-Sha256([string]$Text) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
    $hash = [System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
    return -join ($hash | ForEach-Object { $_.ToString('x2') })
}

try {
    if (Test-Path $Dist) {
        Write-Step 'Removing the previous build'
        Remove-Item $Dist -Recurse -Force
    }

    Write-Step "Building the tray app into $Dist"
    & dotnet publish (Join-Path $AppSource $AppDef.Project) -c Release -o $Dist --nologo -v quiet | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Building the tray app failed (dotnet exit code $LASTEXITCODE)." }

    Write-Step 'Adding the analysis core'
    $coreDir = Join-Path $Dist 'core'
    & robocopy (Join-Path $Core 'src\media_analysis') (Join-Path $coreDir 'media_analysis') /E /XD __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying the core failed (robocopy exit code $LASTEXITCODE)." }
    $uv = Find-Tool 'uv' 'astral-sh.uv*\uv.exe' 'astral-sh.uv'
    # The installed app never writes into its folder, so the bytecode ships with the build. The development
    # environment uses the same Python version (.python-version) as the shared runtime.
    Invoke-Checked $uv @('run', '--project', $Core, '--frozen', '--quiet', 'python', '-m', 'compileall', '-q', $coreDir)

    # The runtime this build needs. One environment per (Python version, locked requirements), shared by every build
    # that doesn't change dependencies.
    $pyVersion = (Get-Content (Join-Path $Core '.python-version') -TotalCount 1).Trim()
    $requirements = (& $uv export --project $Core --frozen --no-dev --all-extras --no-emit-project --no-hashes --no-header --quiet) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw 'uv export failed; is uv.lock up to date?' }
    $envId = (Get-Sha256 "$pyVersion`n$requirements").Substring(0, 12)
    $envDir = Join-Path $EnvsDir $envId
    $ffmpegDir = Join-Path $Runtimes "ffmpeg\$FfmpegVersion\bin"

    $runtimeDir = New-Item (Join-Path $Dist 'runtime') -ItemType Directory
    Set-Content (Join-Path $runtimeDir 'requirements.txt') $requirements -Encoding utf8
    [ordered]@{
        PythonVersion = $pyVersion
        EnvDir        = $envDir
        FfmpegVersion = $FfmpegVersion
        FfmpegDir     = $ffmpegDir
        ModelsDir     = $ModelsDir
        WhisperModels = $WhisperModels
    } | ConvertTo-Json | Set-Content (Join-Path $runtimeDir 'runtime.json') -Encoding utf8

    $envPython = Join-Path $envDir 'Scripts\python.exe'
    $coreEnv = [ordered]@{
        MEDIA_ANALYSIS_FFMPEG_DIR       = $ffmpegDir
        MEDIA_ANALYSIS_MODELS_DIR       = $ModelsDir
        HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
        PYTHONDONTWRITEBYTECODE         = '1'
    }
    # How the tray starts the core.
    [ordered]@{ Python = $envPython; PythonPath = 'core'; Environment = $coreEnv } | ConvertTo-Json |
        Set-Content (Join-Path $Dist 'core.json') -Encoding utf8
    # The same core on the command line; on PATH through the app folder.
    $cli = @('@echo off', 'setlocal', 'set "PYTHONPATH=%~dp0core"') +
        @($coreEnv.Keys | ForEach-Object { "set `"$_=$($coreEnv[$_])`"" }) +
        @("`"$envPython`" -m media_analysis %*")
    Set-Content (Join-Path $Dist 'media-analysis.cmd') $cli -Encoding ascii

    if (-not (Test-Path (Join-Path $Dist $ExeName))) { throw "Build finished but $ExeName is missing from $Dist." }
    Write-Host "Built $Dist\$ExeName (core environment $envId)" -ForegroundColor Green
    exit 0
} catch {
    Write-Host "Build failed: $_" -ForegroundColor Red
    exit 1
}
