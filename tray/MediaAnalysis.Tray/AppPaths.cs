namespace MediaAnalysis.Tray;

/// <summary>
/// Everything the app keeps in a user's profile, under one folder (App deployment standard v1.4, section 2.2):
/// <c>%LOCALAPPDATA%\MediaAnalysis\app\</c> is the installed build (never written by the app) and
/// <c>%LOCALAPPDATA%\MediaAnalysis\data\</c> holds settings, templates, logs and state.
/// </summary>
static class AppPaths
{
    public static readonly string Root = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MediaAnalysis");

    public static readonly string Data = Path.Combine(Root, "data");
    public static readonly string Settings = Path.Combine(Data, "settings.json");
    public static readonly string Templates = Path.Combine(Data, "templates");
    public static readonly string Logs = Path.Combine(Data, "logs");
    public static readonly string History = Path.Combine(Data, "history.json");
    public static readonly string UpdateState = Path.Combine(Data, "update-state.txt");

    /// <summary>Where settings and templates lived before v1.4 (Roaming).</summary>
    public static readonly string LegacyRoaming = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "MediaAnalysis");
}
