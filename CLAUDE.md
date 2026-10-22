# media-analysis

Design: docs/PLAN.md. Deployment: docs/DEPLOYMENT.md.

## Deployment

This app follows the App deployment standard v1.3
(https://github.com/dgtvan/local-app-deployment-strategy), adapted for a CLI with shared runtimes
(docs/DEPLOYMENT.md). The agent runs as a separate Windows account and cannot touch a user's install directly;
each user's `media-analysis` command installs the newest build from D:\Apps\MediaAnalysis by itself on its next run.

After every change to the app that passes the tests, publish it (sandbox disabled):

    cmd //c '<repo path>\publish.cmd /quiet'

Report the result in one line: exit 0 published (accounts on an older build update on their next run) ·
1 error (show output).

To withdraw a bad build, delete its folder under D:\Apps\MediaAnalysis\builds\; accounts go back to the previous one
on their next run.

Never run install.cmd from the agent account except for deployment tests (uninstall afterwards): it creates a
separate copy under the agent account. The user runs install.cmd once in each account themselves. Never delete
anything under D:\Apps\_runtimes or D:\Apps\_models: other apps may use them.
