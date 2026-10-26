using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace RecordingReports.App;

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

    /// <summary>Core's <c>--transcriber</c> value for Azure AI Speech.</summary>
    public const string AzureTranscriber = "azure-speech";

    /// <summary>Environment variable the core reads the Azure key from; never passed on the command line.</summary>
    public const string AzureKeyVariable = "MEDIA_ANALYSIS_AZURE_KEY";

    /// <summary>Transcription engine (core's <c>--transcriber</c>): null = Whisper on this computer, or <see cref="AzureTranscriber"/>.</summary>
    public string? Transcriber { get; set; }

    public bool UsesAzure => Transcriber == AzureTranscriber;

    /// <summary>Azure AI Speech resource endpoint (<c>--azure-endpoint</c>), e.g. https://&lt;name&gt;.services.ai.azure.com/.</summary>
    public string? AzureEndpoint { get; set; }

    /// <summary>The Azure key, encrypted with DPAPI for the current Windows account, base64.</summary>
    public string? AzureKeyProtected { get; set; }

    /// <summary>The Azure key in plain text; stored only as <see cref="AzureKeyProtected"/>.</summary>
    [JsonIgnore]
    public string? AzureKey
    {
        get
        {
            if (string.IsNullOrEmpty(AzureKeyProtected))
                return null;
            try
            {
                return Encoding.UTF8.GetString(ProtectedData.Unprotect(Convert.FromBase64String(AzureKeyProtected), null, DataProtectionScope.CurrentUser));
            }
            catch (Exception ex) when (ex is CryptographicException or FormatException)
            {
                // Encrypted by another Windows account or damaged: as if no key had been entered.
                return null;
            }
        }
        set => AzureKeyProtected = string.IsNullOrWhiteSpace(value)
            ? null
            : Convert.ToBase64String(ProtectedData.Protect(Encoding.UTF8.GetBytes(value.Trim()), null, DataProtectionScope.CurrentUser));
    }

    /// <summary>The technical terms file the core reads from the data folder (core's <c>TERMS_FILE_NAME</c>).</summary>
    public static string TermsFile(string dataDir) => System.IO.Path.Combine(dataDir, "transcription-terms.txt");

    /// <summary>Creates the terms file with instructions when it doesn't exist yet; returns its path.</summary>
    public static string EnsureTermsFile(string dataDir)
    {
        string path = TermsFile(dataDir);
        if (!File.Exists(path))
        {
            Directory.CreateDirectory(dataDir);
            File.WriteAllText(path, """
                # Technical terms and names Azure AI Speech should expect, one per line, e.g.
                # product names, acronyms and the English terms your meetings use.
                # Lines starting with # are ignored. Azure uses the list for English speech;
                # its Vietnamese model ignores it.
                # Changing the list transcribes projects again the next time they are analysed.

                """);
        }
        return path;
    }

    /// <summary>Environment variables the core needs besides <c>core.json</c>'s: the Azure key when Azure transcribes.</summary>
    public Dictionary<string, string> CoreEnvironment() =>
        UsesAzure && AzureKey is { } key ? new() { [AzureKeyVariable] = key } : [];

    /// <summary>Transcription model on this computer (core's <c>--whisper-model</c>); null = the core's default.</summary>
    public string? WhisperModel { get; set; }

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

    /// <summary>Have Claude fix clearly misheard words in the transcript before the report (<c>--correct-transcript</c>).</summary>
    public bool CorrectTranscript { get; set; } = true;

    /// <summary>How much of the computer analyses may use, unless a project is given its own level.</summary>
    public ResourceLevel Resources { get; set; } = ResourceLevels.Default;

    /// <summary>The core options these settings select, in addition to input and output.</summary>
    public List<string> CoreArguments()
    {
        var args = new List<string>();
        if (UsesAzure)
            args.AddRange(["--transcriber", AzureTranscriber, "--azure-endpoint", AzureEndpoint ?? ""]);
        else if (!string.IsNullOrEmpty(WhisperModel))
            args.AddRange(["--whisper-model", WhisperModel]);
        if (!string.IsNullOrEmpty(Effort))
            args.AddRange(["--effort", Effort]);
        // Always explicit, so the core never falls back to a CLAUDE_CONFIG_DIR the app happened to inherit.
        args.AddRange(["--claude-profile", ClaudeProfileInUse()]);
        if (CorrectTranscript)
            args.Add("--correct-transcript");
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
