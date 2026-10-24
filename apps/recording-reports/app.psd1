# This app's identity for the deployment scripts in scripts\ (docs/DEPLOYMENT.md).
@{
    AppId       = 'RecordingReports'
    AppName     = 'Recording Reports'
    Description = 'Analyse folders of recordings into reports'
    Project     = 'RecordingReports\RecordingReports.csproj'
    # Earlier names of this app: their install is removed, their data is copied by the app on its first start.
    Previous    = @(@{ AppId = 'MediaAnalysis'; AppName = 'Media Analysis' })
}
