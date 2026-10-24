namespace RecordingReports.App;

/// <summary>
/// Everything the app needs before it can work, run on every start (App deployment standard v2.3, section 4.2).
/// The install script deliberately does none of this, so fresh installs, reinstalls and sign-ins share one path.
/// </summary>
static class AppInitializer
{
    public const int CurrentSchemaVersion = 1;

    /// <summary>
    /// Copies the data of the install under the app's previous name (Media Analysis) into <see cref="AppPaths.Data"/>
    /// on the first start, when this app has no settings yet. Old data is never deleted (standard section 7, step 4).
    /// Runs before the settings are loaded. Returns the problems the user needs to know about.
    /// </summary>
    public static List<string> MigratePreviousData() => MigratePreviousData(AppPaths.PreviousData, AppPaths.Data);

    internal static List<string> MigratePreviousData(string previous, string data)
    {
        var problems = new List<string>();
        TryRun(problems, "Could not copy the settings and logs from Media Analysis", () =>
        {
            if (!File.Exists(Path.Combine(previous, "settings.json")) || File.Exists(Path.Combine(data, "settings.json")))
                return;
            CopyFolder(previous, data);
            // History, unfinished work and run statistics point at log files; point them at the copies.
            foreach (string file in new[] { "history.json", "pending.json", Path.Combine("stats", "runs.jsonl") })
            {
                string path = Path.Combine(data, file);
                if (File.Exists(path))
                {
                    string text = File.ReadAllText(path);
                    File.WriteAllText(path, text.Replace(
                        Escape(Path.GetFullPath(previous) + "\\"), Escape(Path.GetFullPath(data) + "\\"),
                        StringComparison.OrdinalIgnoreCase));
                }
            }
        });
        return problems;
    }

    /// <summary>
    /// Creates missing folders and default settings, and migrates older settings. Idempotent.
    /// Returns the problems the user needs to know about; the app starts regardless.
    /// </summary>
    public static List<string> Run(AppSettings settings, string? settingsLoadError)
    {
        var problems = new List<string>();
        if (settingsLoadError != null)
            problems.Add(settingsLoadError);

        if (settingsLoadError == null && (!File.Exists(AppPaths.Settings) || settings.SchemaVersion < CurrentSchemaVersion))
        {
            settings.SchemaVersion = CurrentSchemaVersion;
            TryRun(problems, "Could not write the default settings", settings.Save);
        }

        TryRun(problems, "Could not create the logs folder", () => Directory.CreateDirectory(AppPaths.Logs));
        return problems;
    }

    /// <summary>Copies a folder with its subfolders, keeping files that already exist in the target.</summary>
    private static void CopyFolder(string source, string target)
    {
        Directory.CreateDirectory(target);
        foreach (string file in Directory.GetFiles(source))
        {
            string copy = Path.Combine(target, Path.GetFileName(file));
            if (!File.Exists(copy))
                File.Copy(file, copy);
        }
        foreach (string dir in Directory.GetDirectories(source))
            CopyFolder(dir, Path.Combine(target, Path.GetFileName(dir)));
    }

    /// <summary>How a path appears inside a JSON string.</summary>
    private static string Escape(string path) => path.Replace("\\", "\\\\");

    private static void TryRun(List<string> problems, string what, Action action)
    {
        try
        {
            action();
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or NotSupportedException)
        {
            problems.Add($"{what}: {ex.Message}");
        }
    }
}
