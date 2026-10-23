# media-analysis

Design: docs/PLAN.md. Deployment: docs/DEPLOYMENT.md.

## Deployment

This app follows the App deployment standard v1.4
(https://github.com/dgtvan/local-app-deployment-strategy), with the core's runtime shared on D: (docs/DEPLOYMENT.md).
The agent runs as a separate Windows account and cannot touch a user's install directly; each user's running tray
app installs new builds from D:\Apps\MediaAnalysis by itself.

After every change to the app that passes the tests (`uv run pytest`, `dotnet test tray`), publish it
(sandbox disabled):

    cmd //c '<repo path>\publish.cmd /quiet'

It prints one line per account. Report the result in one line by exit code: 0 updated and running ·
5 waiting until the app is idle (no analysis running) · 6 an app didn't answer, it installs on next start ·
7 an app failed to install it (show output) · 1 error (show output).

To withdraw a bad build, delete its folder under D:\Apps\MediaAnalysis\builds\; apps go back to the previous one.

Never run install.cmd or start the exe from the agent account except for the tests in the standard (uninstall
afterwards): it creates a separate copy under the agent account. The user runs install.cmd once in each account
themselves; after that, publishing is all an update needs. Never pass -Force. Never delete anything under
D:\Apps\_runtimes or D:\Apps\_models: other apps may use them.
