namespace MediaAnalysis.Tray;

/// <summary>
/// Everything the app needs before it can work, run on every start (App deployment standard v1.4, section 7.1).
/// The installer deliberately does none of this, so fresh installs, self-updates and sign-ins share one path.
/// </summary>
static class AppInitializer
{
    public const int CurrentSchemaVersion = 1;

    /// <summary>
    /// Copies data from the locations used before v1.4 into <see cref="AppPaths.Data"/> when the new location doesn't
    /// have it yet. Old data is never deleted (standard section 10, step 3). Runs before the settings are loaded.
    /// Returns the problems the user needs to know about.
    /// </summary>
    public static List<string> MigrateLegacyData() => MigrateLegacyData(AppPaths.LegacyRoaming, AppPaths.Root, AppPaths.Data);

    internal static List<string> MigrateLegacyData(string legacyRoaming, string legacyLocal, string data)
    {
        var problems = new List<string>();
        TryRun(problems, "Could not move the settings and logs to the new data folder", () =>
        {
            Directory.CreateDirectory(data);
            CopyFileIfMissing(Path.Combine(legacyRoaming, "settings.json"), Path.Combine(data, "settings.json"));
            CopyFolderIfMissing(Path.Combine(legacyRoaming, "templates"), Path.Combine(data, "templates"));
            CopyFileIfMissing(Path.Combine(legacyLocal, "update-state.txt"), Path.Combine(data, "update-state.txt"));
            string oldLogs = Path.Combine(legacyLocal, "logs");
            string newLogs = Path.Combine(data, "logs");
            bool movedLogs = CopyFolderIfMissing(oldLogs, newLogs);
            string oldHistory = Path.Combine(legacyLocal, "history.json");
            string newHistory = Path.Combine(data, "history.json");
            if (File.Exists(oldHistory) && !File.Exists(newHistory))
            {
                string text = File.ReadAllText(oldHistory);
                // History points at log files; point it at the copies.
                if (movedLogs)
                    text = text.Replace(Escape(oldLogs + "\\"), Escape(newLogs + "\\"), StringComparison.OrdinalIgnoreCase);
                File.WriteAllText(newHistory, text);
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

    private static void CopyFileIfMissing(string source, string target)
    {
        if (File.Exists(source) && !File.Exists(target))
            File.Copy(source, target);
    }

    /// <summary>Copies a folder's files when the target folder is missing or empty; true when it copied.</summary>
    private static bool CopyFolderIfMissing(string source, string target)
    {
        if (!Directory.Exists(source) || (Directory.Exists(target) && Directory.EnumerateFileSystemEntries(target).Any()))
            return false;
        Directory.CreateDirectory(target);
        foreach (string file in Directory.GetFiles(source))
            File.Copy(file, Path.Combine(target, Path.GetFileName(file)), overwrite: false);
        return true;
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
