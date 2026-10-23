# Deployment

Media Analysis follows the [App deployment standard v1.4](https://github.com/dgtvan/local-app-deployment-strategy):
publish folder, per-account install, immutable builds, self-update by the running app, pickup status. On top of the
standard, the heavy parts of the analysis core are shared by every account and every build.

The installed app is the **tray app** (`tray/`, .NET 10 WinForms). It owns everything about running in the
background: start at sign-in, the projects menu, and self-update. The **core** (`src/media_analysis`, Python) only
analyses: the tray starts it as a hidden child process (`python -m media_analysis run <project> <output> --json`)
and follows its JSON progress. The same core is available on the command line as `media-analysis`.

## What lives where

```
D:\Apps\
  _runtimes\                         shared by all apps; written only by publish scripts, never deleted by them
    python\cpython-3.12-...\         Python interpreter (~70 MB)
    uv-cache\                        package store: each library version stored once (~730 MB)
    ffmpeg\<version>\bin\            ffmpeg.exe + ffprobe.exe (~435 MB)
  _models\huggingface\               Whisper models, Hugging Face cache layout (large-v3: 2.9 GB)
  MediaAnalysis\
    envs\<env id>\                   the core's dependencies for one uv.lock; files are hardlinks into uv-cache
    builds\<build id>\               never changed after build-id.txt
      MediaAnalysis.exe, *.dll       tray app (~200 KB, framework-dependent)
      app\media_analysis\            core code (~200 KB)
      core.json                      how the tray starts the core: env python, code folder, ffmpeg/model folders
      media-analysis.cmd             the core on the command line (same settings)
      apply-update.cmd, uninstall.ps1, env-id.txt, update-source.txt, build-id.txt (last)
    pickup\<user>\status.txt         each account's update state

%LOCALAPPDATA%\MediaAnalysis\            per account, everything the app keeps in the profile:
  app\                                   mirror of the installed build, on the account's PATH; the app never writes here
  data\                                  settings.json, templates\, logs\ (one per analysis), history.json,
                                         update-state.txt, models\ (only if the shared model folder isn't writable)
%USERPROFILE%\.claude\                   per account: Claude login (sign in once with Claude Code); analyses don't add
                                         session history there (Claude Code runs with --no-session-persistence)
```

The environment id is a hash of the Python path and the locked requirements, so builds that don't change
dependencies reuse the same environment. An environment is a folder of hardlinks: it costs almost no disk, because
the data is stored once in `uv-cache`. Hardlinks need the store and the environments on the same volume (`D:`).

When an account asks for a Whisper model that isn't in `D:\Apps\_models\huggingface` yet (e.g.
`--whisper-model medium`), it is downloaded into that folder once and every account uses it from then on. If the
account can't write there, the model goes into that account's own cache instead. The scripts never change folder
permissions.

## Scripts

| Script | Run by | What it does |
|---|---|---|
| `publish.cmd` | agent / developer | Ensures Python, ffmpeg and the Whisper model exist in the shared folders, creates the core environment if `uv.lock` changed, builds the tray app, drops tray + core as a new build, keeps the newest 3 builds plus any build an account has installed, removes environments no kept build uses, then waits up to 30 s for each account's tray app to report. Exit 0 every account runs it · 5 an account waits until idle · 6 an account didn't answer (app not running) or nobody installed yet · 7 an account failed to install it · 1 error. Option: `-WhisperModels large-v3,large-v3-turbo`. |
| `install.cmd` | each account, once | Installs .NET 10 Desktop Runtime if missing, copies the latest build into the install folder, registers start at sign-in (HKCU Run), a Start Menu shortcut, a Settings > Apps entry and the PATH entry for `media-analysis`, then starts the tray app. Refuses (exit 3) while an analysis runs, unless `-Force`. `-NoStart` skips starting. Re-run to repair. |
| `uninstall.cmd` | each account | Stops the tray app and removes all of the above and the account's pickup folder. `-RemoveData` also removes settings, templates and logs. Reports and shared files are never touched. |

## Updates

The tray app finds new builds itself (on start, when `builds\` changes, and after each analysis) and installs one
only when no analysis runs or is queued and no warning is unread: it starts `apply-update.cmd` from `%TEMP%`, exits,
and the script mirrors the build into the install folder and starts the new version. Both `install.ps1` and
`apply-update.cmd` refuse to mirror into a folder that isn't named `app` (mirror guard), so a wrong path can never wipe
`data\`. A build that failed to install
is not retried. Rollback: delete the bad folder under `builds\`; apps go back to the previous build.

The core has no update logic. Because the core's code ships inside the tray's build, it updates together with the
tray, including `media-analysis.cmd` on the command line.

## Moving from an install made before v1.4

Before v1.4 the app was installed in `%LOCALAPPDATA%\Programs\MediaAnalysis` with settings and templates in
`%APPDATA%\MediaAnalysis` and logs, history and update state directly in `%LOCALAPPDATA%\MediaAnalysis`. Such a copy
can't move itself: its next self-update is refused by the mirror guard (the target isn't named `app`), and it shows a
warning to run `install.cmd`. Running `install.cmd` once installs into `app\`, removes the old install folder and PATH
entry, and the app copies the old settings, templates, logs and history into `data\` on its first start. Old data is
never deleted automatically; `uninstall.cmd -RemoveData` removes it.

## Differences from the standard

- Builds carry only the app code; the core's runtime (Python, libraries, ffmpeg, model) is shared under
  `D:\Apps\_runtimes` and `D:\Apps\_models`. Retention never deletes shared runtimes, since other apps may use them.
- The install also adds a PATH entry, for the `media-analysis` command.
- The Python core is compiled to bytecode at publish time and runs with `PYTHONDONTWRITEBYTECODE=1`, so nothing is
  written into `app\` at run time.

## Development on this machine

A git-ignored `uv.toml` in the repo points uv at the shared store (`cache-dir`, `link-mode = "hardlink"`), so the
development `.venv` is hardlinked too. Set `MEDIA_ANALYSIS_MODELS_DIR=D:\Apps\_models\huggingface` to use the shared
model when running from source.

Tray app: `dotnet test tray` runs its tests. One test runs a real analysis through the tray's runner when
`MEDIA_ANALYSIS_TEST_CORE_DIR` (a folder with `core.json`, e.g. the install folder) and `MEDIA_ANALYSIS_TEST_PROJECT`
(a folder with a short recording) are set.
