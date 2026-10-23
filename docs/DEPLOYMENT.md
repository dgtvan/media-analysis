# Deployment

Media Analysis follows the [App deployment standard v2.3](https://github.com/dgtvan/local-app-deployment-strategy):
build into `dist\`, install per account into `%LOCALAPPDATA%\VanDang\MediaAnalysis\app`, no publish folder and no
self-update. The core's heavy runtime (Python, libraries, ffmpeg, Whisper model) lives in the standard's dependency
folders (section 1.1), so it isn't copied on every install.

The installed app is the **tray app** (`tray/`, .NET 10 WinForms). It owns everything about running in the
background: start at sign-in, the projects window, closing on request. The **core** (`src/media_analysis`, Python)
only analyses: the tray starts it as a hidden child process (`python -m media_analysis run <project> <output> --json`)
and follows its JSON progress. The same core is available on the command line as `media-analysis`.

## What lives where

```
<repo>\dist\                             build output (gitignored), replaced by every build
  MediaAnalysis.exe, *.dll               tray app (~200 KB, framework-dependent)
  core\media_analysis\                   core code, with its bytecode
  core.json                              how the tray starts the core: env python, code folder, ffmpeg/model folders
  media-analysis.cmd                     the core on the command line (same settings)
  runtime\runtime.json, requirements.txt the runtime this build needs (read by install.ps1)

%LOCALAPPDATA%\VanDang\
  dependencies\                          shared by apps; install.ps1 only adds to it, nothing deletes it
    python\cpython-3.12-...\             Python interpreter (~70 MB)
    uv-cache\                            package store: each library version stored once (~730 MB)
    ffmpeg\<version>\bin\                ffmpeg.exe + ffprobe.exe (~435 MB), version pinned in scripts\common.ps1
    models\huggingface\                  Whisper models, Hugging Face cache layout (large-v3: 2.9 GB)
  MediaAnalysis\
    app\                                 mirror of dist\, on the account's PATH; the app never writes here
    data\                                settings.json, templates\, logs\ (one per analysis), history.json,
                                         models\ (only if the shared model folder isn't writable)
    dependencies\envs\<id>\              the core's libraries for one uv.lock; files are hardlinks into uv-cache

%USERPROFILE%\.claude\                   Claude login (sign in once with Claude Code), or the profile folder chosen in
                                         Config; analyses don't add session history there (--no-session-persistence)
```

The environment id is a hash of the Python version and the locked requirements, so builds that don't change
dependencies reuse the same environment. An environment is a folder of hardlinks: it costs almost no disk, because
the data is stored once in `uv-cache`. Hardlinks need the store and the environments on the same volume.

When the app asks for a Whisper model that isn't in the shared model folder yet (e.g. `--whisper-model medium`), it is
downloaded there once and used from then on. If that folder isn't writable, the model goes into `data\models`.

## Scripts

| Script | What it does |
|---|---|
| `build.cmd` | Deletes `dist\`, builds the tray app into it, adds the core code (compiled to bytecode), `core.json`, `media-analysis.cmd` and the runtime description. Writes nothing outside the repo. Needs .NET 10 SDK and uv. |
| `install.cmd` | Installs .NET 10 Desktop Runtime and uv if missing, creates whatever the build's runtime needs (Python, package store, ffmpeg and model in the shared folder; the core environment in the app's folder), asks the running app to close, mirrors `dist\` into `app\`, registers start at sign-in (HKCU Run) and the Start Menu shortcut, both with `--data-dir`, adds `app\` to PATH, removes older installs and core environments the build no longer uses, and starts the app. `-NoStart`, `-Force`. |
| `uninstall.cmd` | Asks the app to close, removes the Run value, shortcut, PATH entry, `app\` and the app's `dependencies\`, and prints where the data, reports and shared dependencies were kept. Never deletes data, reports or shared dependencies. `-NoDataWarning`, `-KeepDependencies`, `-Force`. |
| `deploy.cmd` | `build`, `uninstall -NoDataWarning -KeepDependencies`, `install`; a failed build stops it before anything is uninstalled. `-Force`. |

Exit codes: 0 success, 1 error, 3 the app is busy (an analysis runs or is queued) or didn't answer the close request.
With `-Force` the app and its analysis are killed instead; analysing the project again continues from the cache.

## Differences from the standard

- The install also adds a PATH entry, for the `media-analysis` command.
- The Python core is compiled to bytecode at build time and runs with `PYTHONDONTWRITEBYTECODE=1`, so nothing is
  written into `app\` at run time.

## Moving from an older install

Before v2.2 the app was published to `D:\Apps\MediaAnalysis` and installed in `%LOCALAPPDATA%\Programs\MediaAnalysis`
(settings and templates in `%APPDATA%\MediaAnalysis`, logs at the top of `%LOCALAPPDATA%\MediaAnalysis`) or, from
v1.4, in `%LOCALAPPDATA%\MediaAnalysis\app` with data in `%LOCALAPPDATA%\MediaAnalysis\data`. Close the old app
(*Exit* in its tray menu) and run `deploy.cmd`: install removes the old install folder, its Run value, Start Menu
shortcut, Settings > Apps entry, PATH entry and pickup folder, and the app copies the old settings, templates, logs
and history into the new data folder on its first start (only into an empty data folder; old data is never deleted).
Then delete the old data by hand as in
[CLEANUP-V1-INSTALL.md](https://github.com/dgtvan/local-app-deployment-strategy/blob/main/CLEANUP-V1-INSTALL.md),
step 6.

## Development on this machine

A git-ignored `uv.toml` in the repo points uv at the shared package store (`cache-dir`); since the repo is on another
volume, the development `.venv` gets copies (`link-mode = "copy"`). Create it with
`UV_PYTHON_INSTALL_DIR=%LOCALAPPDATA%\VanDang\dependencies\python` so it uses the shared Python. Set
`MEDIA_ANALYSIS_MODELS_DIR=%LOCALAPPDATA%\VanDang\dependencies\models\huggingface` to use the shared model when
running from source. `MEDIA_ANALYSIS_HOME` sets the core's data folder (the tray passes its own; the command line
defaults to `%LOCALAPPDATA%\VanDang\MediaAnalysis\data`).

Tray app: `dotnet test tray` runs its tests. One test runs a real analysis through the tray's runner when
`MEDIA_ANALYSIS_TEST_CORE_DIR` (a folder with `core.json`, e.g. `dist\` or the app folder) and
`MEDIA_ANALYSIS_TEST_PROJECT` (a folder with a short recording) are set. To try a build without touching the
installed app, close it and run `dist\MediaAnalysis.exe --data-dir <scratch folder>`.
