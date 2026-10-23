using System.Text.Json;

namespace MediaAnalysis.Tray;

/// <summary>
/// User choices persisted between runs in settings.json in the data folder.
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

    /// <summary>Drops what the settings remember about a deleted project (ignored flag, name).</summary>
    public void Forget(string projectPath)
    {
        SetIgnored(projectPath, false);
        ProjectNames.Remove(Normalize(projectPath));
    }

    private static string Normalize(string path) => Path.GetFullPath(path).TrimEnd('\\');

    /// <summary>Transcription model (core's <c>--whisper-model</c>); null = the core's default.</summary>
    public string? WhisperModel { get; set; }

    /// <summary>Spoken language hint (<c>--language</c>, e.g. "en", "vi"); null = detect, mixed allowed.</summary>
    public string? Language { get; set; }

    /// <summary>Claude thinking effort (<c>--effort</c>); null = the model's default.</summary>
    public string? Effort { get; set; }

    /// <summary>
    /// Claude Code config folder (CLAUDE_CONFIG_DIR) whose login analyses use (<c>--claude-profile</c>);
    /// null = Claude Code's default.
    /// </summary>
    public string? ClaudeProfile { get; set; }

    /// <summary>
    /// The profile used when none is chosen: the account's own CLAUDE_CONFIG_DIR as saved in Windows, else
    /// %USERPROFILE%\.claude. A value inherited from whatever started the app is ignored on purpose, so the default
    /// doesn't depend on who launched it.
    /// </summary>
    public static string DefaultClaudeProfile =>
        System.Environment.GetEnvironmentVariable("CLAUDE_CONFIG_DIR", EnvironmentVariableTarget.User) is { Length: > 0 } dir
            ? dir
            : Path.Combine(System.Environment.GetFolderPath(System.Environment.SpecialFolder.UserProfile), ".claude");

    /// <summary>The profile analyses and opened reports use: the chosen one, else <see cref="DefaultClaudeProfile"/>.</summary>
    public string ClaudeProfileInUse() => string.IsNullOrEmpty(ClaudeProfile) ? DefaultClaudeProfile : ClaudeProfile;

    /// <summary>True when <paramref name="profile"/> holds a Claude Code login.</summary>
    public static bool HasClaudeLogin(string profile) =>
        File.Exists(Path.Combine(profile, ".credentials.json"))
        || !string.IsNullOrEmpty(System.Environment.GetEnvironmentVariable("CLAUDE_CODE_OAUTH_TOKEN"));

    /// <summary>Claude Code profiles in the user's home folder: <c>.claude*</c> folders that hold a login.</summary>
    public static List<string> FindClaudeProfiles(string? home = null)
    {
        home ??= System.Environment.GetFolderPath(System.Environment.SpecialFolder.UserProfile);
        try
        {
            return Directory.GetDirectories(home, ".claude*")
                .Where(d => File.Exists(Path.Combine(d, ".credentials.json")))
                .Order(StringComparer.OrdinalIgnoreCase)
                .ToList();
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return [];
        }
    }

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
        // Always explicit, so the core never falls back to a CLAUDE_CONFIG_DIR the app happened to inherit.
        args.AddRange(["--claude-profile", ClaudeProfileInUse()]);
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
