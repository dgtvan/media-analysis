# media-analysis

Architecture: docs/ARCHITECTURE.md. Deployment: docs/DEPLOYMENT.md.

The repo holds the shared Python core (`core/`) and the Windows apps that use it (`apps/<app>/`).

## Deployment

The apps follow the App deployment standard v2.3
(https://github.com/dgtvan/local-app-deployment-strategy). The core's Python runtime, ffmpeg and Whisper model live
in the dependency folders of section 1.1 (docs/DEPLOYMENT.md).

After every change that passes the tests (`uv run --project core pytest`, `dotnet test apps/recording-reports`),
deploy the app (sandbox disabled):

    cmd //c '<repo path>\apps\recording-reports\deploy.cmd /quiet'

Report the result in one line by exit code: 0 installed and running · 3 the app is busy, ask the user to close
it and run again · 1 error (show output). Never pass -Force unless the user asks.

Never delete anything under %LOCALAPPDATA%\VanDang\dependencies: other apps may use it.
