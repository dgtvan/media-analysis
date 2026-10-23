# media-analysis

Design: docs/PLAN.md. Deployment: docs/DEPLOYMENT.md.

## Deployment

This app follows the App deployment standard v2.3
(https://github.com/dgtvan/local-app-deployment-strategy). Its Python runtime, ffmpeg and Whisper model live in the
dependency folders of section 1.1 (docs/DEPLOYMENT.md).

After every change to the app that passes the tests (`uv run pytest`, `dotnet test tray`), deploy it
(sandbox disabled):

    cmd //c '<repo path>\deploy.cmd /quiet'

Report the result in one line by exit code: 0 installed and running · 3 the app is busy, ask the user to close
it and run again · 1 error (show output). Never pass -Force unless the user asks.

Never delete anything under %LOCALAPPDATA%\VanDang\dependencies: other apps may use it.
