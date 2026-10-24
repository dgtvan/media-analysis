namespace MediaAnalysis.Tray;

/// <summary>
/// Where the app keeps its data: the folder given with <c>--data-dir</c> (App deployment standard v2.3, section 4.1),
/// normally <c>%LOCALAPPDATA%\VanDang\MediaAnalysis\data\</c>. The app never writes next to its exe.
/// </summary>
static class AppPaths
{
    public static string Data { get; private set; } = "";

    public static string Settings => Path.Combine(Data, "settings.json");
    public static string Templates => Path.Combine(Data, "templates");
    public static string Logs => Path.Combine(Data, "logs");
    public static string History => Path.Combine(Data, "history.json");

    public static void Initialize(string dataDir) => Data = Path.GetFullPath(dataDir);

    /// <summary>Where settings and templates lived before standard v1.4 (Roaming).</summary>
    public static readonly string LegacyRoaming = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "MediaAnalysis");

    /// <summary>
    /// The local folder used before standard v2.2: logs and history at its top level before v1.4, everything in its
    /// <c>data\</c> subfolder from v1.4.
    /// </summary>
    public static readonly string LegacyLocal = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MediaAnalysis");
}
