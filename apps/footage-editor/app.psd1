# This app's identity for the deployment scripts in scripts\ (docs/DEPLOYMENT.md).
@{
    AppId         = 'FootageEditor'
    AppName       = 'Footage Editor'
    Description   = 'Turn footage into a suggested edit and refine it by chatting with Claude'
    Project       = 'FootageEditor\FootageEditor.csproj'
    # A windowed app: it doesn't start at sign-in, and Recording Reports already provides the media-analysis command.
    StartAtSignIn = $false
    AddToPath     = $false
    # Folders of the app copied into the build: the presentation the core's service serves.
    CopyDirs      = @('ui')
}
