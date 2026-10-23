using System.Text.Json;

namespace MediaAnalysis.Tray;

/// <summary>
/// User choices persisted between runs in %LOCALAPPDATA%\MediaAnalysis\data\settings.json.
/// </summary>
sealed class AppSettings
{
    public static string SettingsPath => AppPaths.Settings;

    /// <summary>Version of the settings layout; <see cref="AppInitializer"/> migrates older files once.</summary>
    public int SchemaVersion { get; set; }

    /// <summary>Folder whose subfolders are projects (one recording session each). Null until the user picks one.</summary>
    public string? ProjectsRoot { get; set; }

    /// <summary>Folder that receives one output folder per project, named like the project. Null until chosen.</summary>
    public string? OutputRoot { get; set; }

    /// <summary>Analyse new projects without waiting for the user (see <see cref="AutoAnalysis"/>).</summary>
    public bool AutoAnalyse { get; set; }

    /// <summary>When <see cref="AutoAnalyse"/> was turned on; only projects created after it are analysed automatically.</summary>
    public DateTime? AutoAnalyseSinceUtc { get; set; }

    /// <summary>Full paths of projects the user chose to ignore: never counted as waiting, never started automatically.</summary>
    public List<string> IgnoredProjects { get; set; } = [];

    public bool IsIgnored(string projectPath) =>
        IgnoredProjects.Any(p => string.Equals(p, Normalize(projectPath), StringComparison.OrdinalIgnoreCase));

    public void SetIgnored(string projectPath, bool ignored)
    {
        IgnoredProjects.RemoveAll(p => string.Equals(p, Normalize(projectPath), StringComparison.OrdinalIgnoreCase));
        if (ignored)
            IgnoredProjects.Add(Normalize(projectPath));
    }

    /// <summary>Names the user gave projects, by full path. Only shown in the app; folders are never renamed.</summary>
    public Dictionary<string, string> ProjectNames { get; set; } = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>The user's name for a project, or its folder name.</summary>
    public string DisplayName(string projectPath) =>
        ProjectNames.TryGetValue(Normalize(projectPath), out string? name) ? name : Path.GetFileName(Normalize(projectPath));

    /// <summary>Sets the project's name; empty or the folder name itself goes back to the folder name.</summary>
    public void SetDisplayName(string projectPath, string? name)
    {
        string key = Normalize(projectPath);
        ProjectNames.Remove(key);
        name = name?.Trim();
        if (!string.IsNullOrEmpty(name) && name != Path.GetFileName(key))
            ProjectNames[key] = name;
    }

    private static string Normalize(string path) => Path.GetFullPath(path).TrimEnd('\\');

    /// <summary>Transcription model (core's <c>--whisper-model</c>); null = the core's default.</summary>
    public string? WhisperModel { get; set; }

    /// <summary>Spoken language hint (<c>--language</c>, e.g. "en", "vi"); null = detect, mixed allowed.</summary>
    public string? Language { get; set; }

    /// <summary>Claude thinking effort (<c>--effort</c>); null = the model's default.</summary>
    public string? Effort { get; set; }

    /// <summary>Maximum moments to capture as evidence (<c>--max-frames</c>); null = the core's default.</summary>
    public int? MaxFrames { get; set; }

    /// <summary>The core options these settings select, in addition to input and output.</summary>
    public List<string> CoreArguments()
    {
        var args = new List<string>();
        if (!string.IsNullOrEmpty(WhisperModel))
            args.AddRange(["--whisper-model", WhisperModel]);
        if (!string.IsNullOrEmpty(Language))
            args.AddRange(["--language", Language]);
        if (!string.IsNullOrEmpty(Effort))
            args.AddRange(["--effort", Effort]);
        if (MaxFrames is { } frames)
            args.AddRange(["--max-frames", frames.ToString()]);
        return args;
    }

    /// <summary>
    /// Loads the settings, or defaults when the file is missing. <paramref name="error"/> is set when the file
    /// exists but can't be read, in which case it must not be overwritten.
    /// </summary>
    public static AppSettings Load(out string? error)
    {
        error = null;
        try
        {
            if (File.Exists(SettingsPath))
            {
                var settings = JsonSerializer.Deserialize<AppSettings>(File.ReadAllText(SettingsPath)) ?? new AppSettings();
                // JSON gives a case-sensitive dictionary; Windows paths aren't.
                settings.ProjectNames = new(settings.ProjectNames ?? [], StringComparer.OrdinalIgnoreCase);
                return settings;
            }
        }
        catch (Exception ex) when (ex is IOException or JsonException or UnauthorizedAccessException)
        {
            error = $"Settings could not be read ({SettingsPath}): {ex.Message}. Using defaults; the file was left as it is.";
        }
        return new AppSettings();
    }

    /// <summary>Writes the settings file. Throws on I/O failure.</summary>
    public void Save()
    {
        Directory.CreateDirectory(Path.GetDirectoryName(SettingsPath)!);
        File.WriteAllText(SettingsPath, JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true }));
    }
}
