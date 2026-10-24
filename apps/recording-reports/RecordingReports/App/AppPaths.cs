namespace RecordingReports.App;

/// <summary>
/// Where the app keeps its data: the folder given with <c>--data-dir</c> (App deployment standard v2.3, section 4.1),
/// normally <c>%LOCALAPPDATA%\VanDang\RecordingReports\data\</c>. The app never writes next to its exe.
/// </summary>
static class AppPaths
{
    public static string Data { get; private set; } = "";

    public static string Settings => Path.Combine(Data, "settings.json");
    public static string Templates => Path.Combine(Data, "templates");
    public static string Logs => Path.Combine(Data, "logs");
    public static string History => Path.Combine(Data, "history.json");

    public static void Initialize(string dataDir) => Data = Path.GetFullPath(dataDir);

    /// <summary>The data folder of this app's install under its previous name, Media Analysis.</summary>
    public static readonly string PreviousData = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "VanDang", "MediaAnalysis", "data");
}
