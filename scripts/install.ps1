<#
.SYNOPSIS
    Installs the build in dist\ for the current account.

.DESCRIPTION
    Follows the App deployment standard v2.3, section 3.2 (https://github.com/dgtvan/local-app-deployment-strategy).
    Creates the core's runtime if it is missing: Python, the library store, ffmpeg and the Whisper model in
    %LOCALAPPDATA%\VanDang\dependencies (shared by apps), the library environment in
    %LOCALAPPDATA%\VanDang\MediaAnalysis\dependencies (this app only). Then copies dist\ to %LOCALAPPDATA%\VanDang\MediaAnalysis\app, starts it at sign-in (HKCU Run) and from the Start
    Menu, puts the media-analysis command on this account's PATH, and starts the app. It does no app initialization
    (the app does that itself at startup) and never touches the data folder. Re-running it is safe.

    Exit codes: 0 installed (and started unless -NoStart), 1 error, 3 refused because the app is busy (an analysis
    is running or queued) or didn't answer the close request.

.PARAMETER NoStart
    Don't start the app after installing.

.PARAMETER Force
    Close the app even during an analysis. The analysis stops; analysing the project again continues where it stopped.
#>
param([switch]$NoStart, [switch]$Force)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

function Install-WithWinget($id, $label, $version) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "$label is missing and winget is not available. Install it manually, then run this again."
    }
    Write-Step "Installing $label (Windows may ask for permission)"
    $versionArgs = @(if ($version) { '--version', $version })
    winget install --id $id --exact @versionArgs --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "Installing $label failed (winget exit code $LASTEXITCODE)." }
}

function Find-WingetFfmpeg($version) {
    $pattern = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\Gyan.FFmpeg*\ffmpeg-$version-*\bin\ffmpeg.exe"
    Get-ChildItem $pattern -ErrorAction SilentlyContinue | Select-Object -First 1
}

# The core's runtime described by dist\runtime\runtime.json. Shared folders are only added to, never changed.
function Install-CoreRuntime($runtime) {
    if (-not (Find-Tool 'uv' 'astral-sh.uv*\uv.exe' 'astral-sh.uv' -Optional)) { Install-WithWinget 'astral-sh.uv' 'uv' }
    $uv = Use-SharedUv

    if (Test-Path (Join-Path $runtime.EnvDir 'env-ready.txt')) {
        Write-Step "Core environment $(Split-Path -Leaf $runtime.EnvDir) (exists)"
    } else {
        Write-Step "Python $($runtime.PythonVersion) in $($env:UV_PYTHON_INSTALL_DIR)"
        Invoke-Checked $uv @('python', 'install', $runtime.PythonVersion, '--no-bin', '--quiet')
        $python = (& $uv python find --no-project --directory $env:UV_PYTHON_INSTALL_DIR $runtime.PythonVersion).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $python.StartsWith($env:UV_PYTHON_INSTALL_DIR)) {
            throw "Shared Python not found under $($env:UV_PYTHON_INSTALL_DIR) (got '$python')."
        }
        Write-Step "Core environment $(Split-Path -Leaf $runtime.EnvDir) (creating)"
        if (Test-Path $runtime.EnvDir) { Remove-Item $runtime.EnvDir -Recurse -Force }
        Invoke-Checked $uv @('venv', '--python', $python, '--quiet', $runtime.EnvDir)
        Invoke-Checked $uv @('pip', 'sync', '--python', (Join-Path $runtime.EnvDir 'Scripts\python.exe'),
            '--compile-bytecode', '--quiet', (Join-Path $Dist 'runtime\requirements.txt'))
        Set-Content (Join-Path $runtime.EnvDir 'env-ready.txt') (Split-Path -Leaf $runtime.EnvDir) -Encoding ascii
    }

    if (-not (Test-Path (Join-Path $runtime.FfmpegDir 'ffprobe.exe'))) {
        Write-Step "ffmpeg $($runtime.FfmpegVersion) (copying to $($runtime.FfmpegDir))"
        $ffmpeg = Find-WingetFfmpeg $runtime.FfmpegVersion
        if (-not $ffmpeg) {
            Install-WithWinget 'Gyan.FFmpeg' "ffmpeg $($runtime.FfmpegVersion)" $runtime.FfmpegVersion
            $ffmpeg = Find-WingetFfmpeg $runtime.FfmpegVersion
            if (-not $ffmpeg) { throw "ffmpeg $($runtime.FfmpegVersion) was installed but can't be found." }
        }
        $source = $ffmpeg.DirectoryName
        New-Item $runtime.FfmpegDir -ItemType Directory -Force | Out-Null
        Copy-Item (Join-Path $source 'ffmpeg.exe'), (Join-Path $source 'ffprobe.exe') $runtime.FfmpegDir
    }

    New-Item $runtime.ModelsDir -ItemType Directory -Force | Out-Null
    $envPython = Join-Path $runtime.EnvDir 'Scripts\python.exe'
    $env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
    foreach ($model in $runtime.WhisperModels) {
        $script = 'import sys; from faster_whisper.utils import download_model as d; m, c = sys.argv[1:3]' +
            "`ntry: d(m, cache_dir=c, local_files_only=True)`nexcept Exception: print('Downloading ' + m); d(m, cache_dir=c)"
        Invoke-Checked $envPython @('-c', $script, $model, $runtime.ModelsDir)
    }
}

try {
    # 1. Build present
    if (-not (Test-Path (Join-Path $Dist $ExeName))) {
        Write-Host "No build in $Dist. Run build.cmd first." -ForegroundColor Red
        exit 1
    }

    # 2. Prerequisites
    if (-not (Get-ChildItem "$env:ProgramFiles\dotnet\shared\Microsoft.WindowsDesktop.App" -Directory -Filter '10.*' -ErrorAction SilentlyContinue)) {
        Install-WithWinget 'Microsoft.DotNet.DesktopRuntime.10' '.NET 10 Desktop Runtime'
    }
    $runtime = Get-Content (Join-Path $Dist 'runtime\runtime.json') -Raw | ConvertFrom-Json
    Install-CoreRuntime $runtime | Out-Host

    # 3. Stop
    Stop-App -Force:$Force

    # 4. Copy. The guard makes sure a wrong path can never mirror over the data folder.
    if (-not $AppDir.TrimEnd('\').EndsWith("\$Publisher\$AppId\app", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to install into $AppDir (it must end in \$Publisher\$AppId\app)."
    }
    Write-Step "Installing to $AppDir"
    New-Item $AppDir -ItemType Directory -Force | Out-Null
    & robocopy $Dist $AppDir /MIR /R:5 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying the build failed (robocopy exit code $LASTEXITCODE)." }

    # 5. Register
    Write-Step 'Registering start at sign-in, the Start Menu shortcut and the media-analysis command'
    Set-ItemProperty $RunKey -Name $AppId -Value "`"$ExePath`" --data-dir `"$DataDir`""

    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Shortcut)
    $link.TargetPath = $ExePath
    $link.Arguments = "--data-dir `"$DataDir`""
    $link.WorkingDirectory = $AppDir
    $link.Description = 'Analyse folders of recordings into reports'
    $link.Save()

    Set-UserPath -Add $AppDir -Remove $LegacyInstallDirs

    # 6. Entries from earlier install methods (standard v1.x). Their data is left where it is; the app copies it.
    Unregister-ScheduledTask -TaskName $AppId -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item (Join-Path ([Environment]::GetFolderPath('Startup')) "$AppName.lnk") -Force -ErrorAction SilentlyContinue
    Remove-Item "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppId" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $LegacyPickup -Recurse -Force -ErrorAction SilentlyContinue
    foreach ($dir in $LegacyInstallDirs) {
        if (Test-Path $dir) {
            Write-Step "Removing the old install in $dir"
            Remove-Item $dir -Recurse -Force
        }
    }
    # Core environments of earlier builds (hardlinks, so this frees little but keeps the folder tidy).
    Get-ChildItem $EnvsDir -Directory -ErrorAction SilentlyContinue | Where-Object { $_.FullName -ne $runtime.EnvDir } |
        ForEach-Object {
            try { Remove-Item $_.FullName -Recurse -Force } catch { Write-Warning "Could not remove $($_.FullName): $_" }
        }

    if (-not (Test-Path (Join-Path $HOME '.claude\.credentials.json')) -and -not $env:CLAUDE_CODE_OAUTH_TOKEN) {
        Write-Host 'No Claude login found for this account: sign in once with Claude Code (claude, then /login).' -ForegroundColor Yellow
    }

    # 7. Start
    if ($NoStart) {
        Write-Host "Installed to $AppDir (not started). It starts at sign-in or from the Start Menu." -ForegroundColor Green
        exit 0
    }
    Write-Step "Starting $AppName"
    Start-Process $ExePath -ArgumentList '--data-dir', "`"$DataDir`"" -WorkingDirectory $AppDir
    Write-Host "Installed to $AppDir. $AppName is in the system tray and starts at sign-in." -ForegroundColor Green
    exit 0
} catch {
    Write-Host "Install failed: $_" -ForegroundColor Red
    exit 1
}
