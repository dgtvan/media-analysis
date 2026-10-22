<#
.SYNOPSIS
    Publish a build of media-analysis to D:\Apps\MediaAnalysis (docs/DEPLOYMENT.md).

.DESCRIPTION
    Makes sure the shared pieces exist (Python, package store, ffmpeg, Whisper model), creates the dependency
    environment for the current uv.lock if it is new, then drops the app code as a new build folder and applies
    retention. Touches only D:\Apps; never an account's install, registry or profile.
#>
[CmdletBinding()]
param(
    [string]$AppsRoot = 'D:\Apps',
    [string[]]$WhisperModels = @('large-v3')
)

$ErrorActionPreference = 'Stop'
$AppId = 'MediaAnalysis'
$Repo = Split-Path -Parent $PSScriptRoot
$Runtimes = Join-Path $AppsRoot '_runtimes'
$ModelsDir = Join-Path $AppsRoot '_models\huggingface'
$Publish = Join-Path $AppsRoot $AppId
$KeepBuilds = 3

function Step([string]$Text) { Write-Host "-> $Text" }

function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$(Split-Path -Leaf $Exe) $($Arguments -join ' ') failed (exit $LASTEXITCODE)" }
}

function Find-Tool([string]$Name, [string]$WingetPattern) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    # winget's PATH entry only reaches shells started after the install.
    $packages = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    $found = Get-ChildItem -Path (Join-Path $packages $WingetPattern) -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1
    if ($found) { return $found.FullName }
    throw "$Name not found. Install it with winget."
}

function Get-Sha256([string]$Text) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
    $hash = [System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
    return -join ($hash | ForEach-Object { $_.ToString('x2') })
}

function Test-ReadyBuild([System.IO.DirectoryInfo]$Dir) {
    $marker = Join-Path $Dir.FullName 'build-id.txt'
    return (Test-Path $marker) -and ((Get-Content $marker -TotalCount 1) -eq $Dir.Name)
}

try {
    $uv = Find-Tool 'uv' 'astral-sh.uv*\uv.exe'
    $env:UV_CACHE_DIR = Join-Path $Runtimes 'uv-cache'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $Runtimes 'python'
    $env:UV_LINK_MODE = 'hardlink'
    $env:UV_MANAGED_PYTHON = '1'

    # 1. Shared Python
    $pyVersion = (Get-Content (Join-Path $Repo '.python-version') -TotalCount 1).Trim()
    Step "Python $pyVersion in $($env:UV_PYTHON_INSTALL_DIR)"
    Invoke-Checked $uv @('python', 'install', $pyVersion, '--no-bin', '--quiet')
    $python = (& $uv python find --no-project --directory $env:UV_PYTHON_INSTALL_DIR $pyVersion).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $python.StartsWith($env:UV_PYTHON_INSTALL_DIR)) {
        throw "Shared Python not found under $($env:UV_PYTHON_INSTALL_DIR) (got '$python')."
    }

    # 2. Dependency environment, one per (Python, locked requirements); files are hardlinks into the uv cache.
    $requirements = (& $uv export --project $Repo --frozen --no-dev --all-extras --no-emit-project --no-hashes --no-header --quiet) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw 'uv export failed; is uv.lock up to date?' }
    $envId = (Get-Sha256 "$python`n$requirements").Substring(0, 12)
    $envDir = Join-Path $Publish "envs\$envId"
    $envReady = Join-Path $envDir 'env-ready.txt'
    if (Test-Path $envReady) {
        Step "Environment $envId (exists)"
    } else {
        Step "Environment $envId (new)"
        if (Test-Path $envDir) { Remove-Item $envDir -Recurse -Force }
        $reqFile = New-TemporaryFile
        try {
            Set-Content -Path $reqFile -Value $requirements -Encoding utf8
            Invoke-Checked $uv @('venv', '--python', $python, '--quiet', $envDir)
            Invoke-Checked $uv @('pip', 'sync', '--python', (Join-Path $envDir 'Scripts\python.exe'), '--compile-bytecode', '--quiet', $reqFile)
        } finally {
            Remove-Item $reqFile -ErrorAction SilentlyContinue
        }
        Set-Content -Path $envReady -Value $envId -Encoding ascii
    }
    $envPython = Join-Path $envDir 'Scripts\python.exe'

    # 3. Shared ffmpeg, one folder per version
    $ffmpegSource = Find-Tool 'ffmpeg' 'Gyan.FFmpeg*\*\bin\ffmpeg.exe'
    $ffmpegVersion = [regex]::Match((& $ffmpegSource -version | Select-Object -First 1), 'version (\d[\d.]*)').Groups[1].Value
    if (-not $ffmpegVersion) { throw "Could not read the ffmpeg version from $ffmpegSource." }
    $ffmpegDir = Join-Path $Runtimes "ffmpeg\$ffmpegVersion\bin"
    if (Test-Path (Join-Path $ffmpegDir 'ffprobe.exe')) {
        Step "ffmpeg $ffmpegVersion (exists)"
    } else {
        Step "ffmpeg $ffmpegVersion (copying)"
        New-Item -ItemType Directory -Force -Path $ffmpegDir | Out-Null
        $sourceDir = Split-Path -Parent $ffmpegSource
        Copy-Item (Join-Path $sourceDir 'ffmpeg.exe') $ffmpegDir
        Copy-Item (Join-Path $sourceDir 'ffprobe.exe') $ffmpegDir
    }

    # 4. Shared Whisper models (Hugging Face cache layout)
    New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null
    foreach ($model in $WhisperModels) {
        Step "Whisper model $model"
        $script = 'import sys; from faster_whisper.utils import download_model as d; m, c = sys.argv[1:3]' +
            "`ntry: d(m, cache_dir=c, local_files_only=True)`nexcept Exception: d(m, cache_dir=c)"
        $env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
        Invoke-Checked $envPython @('-c', $script, $model, $ModelsDir)
    }

    # 5. The build: app code plus a launcher that points at the shared pieces above.
    $buildId = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ') + '-' + ('{0:x8}' -f (Get-Random -Maximum ([int]::MaxValue)))
    $build = Join-Path $Publish "builds\$buildId"
    Step "Build $buildId"
    New-Item -ItemType Directory -Force -Path (Join-Path $build 'app') | Out-Null
    robocopy (Join-Path $Repo 'src\media_analysis') (Join-Path $build 'app\media_analysis') /E /XD __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying the app code failed (robocopy exit $LASTEXITCODE)." }
    $run = @(
        '@echo off'
        'setlocal'
        'set "PYTHONPATH=%~dp0app"'
        "set `"MEDIA_ANALYSIS_FFMPEG_DIR=$ffmpegDir`""
        "set `"MEDIA_ANALYSIS_MODELS_DIR=$ModelsDir`""
        'set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"'
        "`"$envPython`" -m media_analysis %* & exit /b"
    )
    Set-Content -Path (Join-Path $build 'run.cmd') -Value $run -Encoding ascii
    Copy-Item (Join-Path $PSScriptRoot 'media-analysis.cmd') $build
    Copy-Item (Join-Path $PSScriptRoot 'uninstall.ps1') $build
    Set-Content -Path (Join-Path $build 'env-id.txt') -Value $envId -Encoding ascii
    Set-Content -Path (Join-Path $build 'update-source.txt') -Value $Publish -Encoding ascii
    Set-Content -Path (Join-Path $build 'build-id.txt') -Value $buildId -Encoding ascii

    # 6. Retention: newest builds plus any build an account has installed (its run.cmd still points at that
    #    build's environment), then environments no kept build uses. Shared runtimes and models are never
    #    deleted here because other apps may use them.
    $installed = Get-ChildItem (Join-Path $Publish 'pickup') -Filter status.txt -Recurse -ErrorAction SilentlyContinue |
        ForEach-Object { @(Get-Content $_.FullName)[1] }
    $builds = Get-ChildItem (Join-Path $Publish 'builds') -Directory
    $ready = $builds | Where-Object { Test-ReadyBuild $_ } | Sort-Object Name -Descending
    $stale = @($ready | Select-Object -Skip $KeepBuilds | Where-Object { $installed -notcontains $_.Name }) +
        @($builds | Where-Object { -not (Test-ReadyBuild $_) -and $_.LastWriteTimeUtc -lt (Get-Date).ToUniversalTime().AddHours(-1) })
    foreach ($dir in $stale) {
        Step "Removing build $($dir.Name)"
        Remove-Item $dir.FullName -Recurse -Force
    }
    $usedEnvs = Get-ChildItem (Join-Path $Publish 'builds') -Directory |
        ForEach-Object { Get-Content (Join-Path $_.FullName 'env-id.txt') -TotalCount 1 -ErrorAction SilentlyContinue }
    foreach ($dir in Get-ChildItem (Join-Path $Publish 'envs') -Directory | Where-Object { $usedEnvs -notcontains $_.Name }) {
        Step "Removing environment $($dir.Name)"
        try {
            Remove-Item $dir.FullName -Recurse -Force
        } catch {
            Write-Warning "Environment $($dir.Name) is in use; it will be removed by a later publish."
        }
    }

    # 7. Pickup folders: each account's launcher reports there.
    $pickup = Join-Path $Publish 'pickup'
    New-Item -ItemType Directory -Force -Path $pickup | Out-Null

    # 8. Report. A CLI only updates when it is next run, so accounts on an older build are expected.
    Write-Host ''
    Write-Host "Published $buildId"
    foreach ($status in Get-ChildItem $pickup -Filter status.txt -Recurse -ErrorAction SilentlyContinue) {
        $lines = @(Get-Content $status.FullName)
        $note = if ($lines[1] -eq $buildId) { '' } else { ' (updates on next run)' }
        Write-Host "  $($status.Directory.Name): $($lines[0]) on $($lines[1]), $($lines[3])$note"
    }
    exit 0
} catch {
    Write-Host "Publish failed: $_" -ForegroundColor Red
    exit 1
}
