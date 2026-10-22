# Deployment

media-analysis follows the [App deployment standard v1.3](https://github.com/dgtvan/local-app-deployment-strategy)
(publish folder, per-account install, immutable builds, keep 3, pickup status), adapted for a command-line app whose
heavy parts are shared by every account and every build.

## What lives where

```
D:\Apps\
  _runtimes\                         shared by all apps; written only by publish scripts, never deleted by them
    python\cpython-3.12-...\         Python interpreter (~70 MB)
    uv-cache\                        package store: each library version stored once (~730 MB)
    ffmpeg\<version>\bin\            ffmpeg.exe + ffprobe.exe (~435 MB)
  _models\huggingface\               Whisper models, Hugging Face cache layout (large-v3: 2.9 GB)
  MediaAnalysis\
    envs\<env id>\                   dependencies for one uv.lock; files are hardlinks into uv-cache
    builds\<build id>\               app code (~200 KB) + launcher; never changed after build-id.txt
      app\media_analysis\
      run.cmd                        sets the shared ffmpeg/model folders, runs envs\<env id>\Scripts\python.exe
      media-analysis.cmd             per-account launcher (copied to the install folder)
      env-id.txt, update-source.txt, uninstall.ps1, build-id.txt (last)
    pickup\<user>\status.txt         each account's last update result

%LOCALAPPDATA%\Programs\MediaAnalysis\   per account (~200 KB), on the account's PATH
  media-analysis.cmd
  current\                           mirror of the build in use
%APPDATA%\MediaAnalysis\templates\       per account: user templates
%LOCALAPPDATA%\MediaAnalysis\            per account: update-state.txt
%USERPROFILE%\.claude\                   per account: Claude login (sign in once with Claude Code)
```

The environment id is a hash of the Python path and the locked requirements, so builds that don't change
dependencies reuse the same environment. An environment is a folder of hardlinks: it costs almost no disk, because
the data is stored once in `uv-cache`. Hardlinks need the store and the environments on the same volume (`D:`).

## Scripts

| Script | Run by | What it does |
|---|---|---|
| `publish.cmd` | agent / developer | Ensures Python, ffmpeg and the Whisper model exist in the shared folders, creates the environment if `uv.lock` changed, drops a new build, keeps the newest 3 builds plus any build an account has installed, removes environments no kept build uses. Prints each account's last status. Exit 0 published, 1 error. Options: `-WhisperModels large-v3,large-v3-turbo`. |
| `install.cmd` | each account, once | Copies the latest build into the install folder, adds it to the account's PATH and adds a Settings > Apps entry. Re-run to repair. |
| `uninstall.cmd` | each account | Removes the PATH entry, the Settings > Apps entry, the install folder and the account's pickup folder. `-RemoveData` also removes templates and state. Shared files are never touched. |

## Updates

A command-line app has no long-running process to watch for builds, so the launcher updates on each run (the
standard allows startup-only checks for apps that run briefly): before running, `media-analysis.cmd` installs the
latest ready build into `current\` if it differs from the installed one (newer, or older after a rollback), records
the result in `update-state.txt` and `pickup\<user>\status.txt`, then runs it. It also refreshes itself from the
build. `publish.cmd` therefore reports accounts on an older build as "updates on next run" rather than waiting.

Rollback: delete the bad folder under `builds\`; the next run of each account goes back to the previous build.

## Differences from the standard

- No Run key, Start Menu shortcut, single-instance mutex, tray icon or `apply-update.cmd`: it is a command, not a
  background app. Several runs can happen at once.
- Builds contain only the app code; the runtime (Python, libraries, ffmpeg, model) is shared under `D:\Apps\_runtimes`
  and `D:\Apps\_models`. Retention never deletes shared runtimes, since other apps may use them.
- When an account asks for a model that isn't in `D:\Apps\_models\huggingface` yet (e.g. `--whisper-model medium`),
  it is downloaded into that folder once and every account uses it from then on. If the account can't write there,
  the model goes into that account's own cache instead. The scripts never change folder permissions; who may write
  where is set on `D:\Apps` by an administrator.

## Development on this machine

A git-ignored `uv.toml` in the repo points uv at the shared store (`cache-dir`, `link-mode = "hardlink"`), so the
development `.venv` is hardlinked too. Set `MEDIA_ANALYSIS_MODELS_DIR=D:\Apps\_models\huggingface` to use the shared
model when running from source.
