using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace FootageEditor;

/// <summary>
/// How to start the Python core, from <c>core.json</c> next to the exe (written by build.ps1): the shared Python
/// environment, the core's code folder and the shared ffmpeg/model folders.
/// </summary>
sealed class CoreConfig
{
    public const string FileName = "core.json";

    public string Python { get; set; } = "";

    /// <summary>Folder holding the <c>media_analysis</c> package, relative to the exe folder or absolute.</summary>
    public string PythonPath { get; set; } = "core";

    public Dictionary<string, string> Environment { get; set; } = [];

    public static CoreConfig Load(string folder)
    {
        string path = Path.Combine(folder, FileName);
        CoreConfig? config;
        try
        {
            config = JsonSerializer.Deserialize<CoreConfig>(File.ReadAllText(path));
        }
        catch (Exception ex) when (ex is IOException or JsonException or UnauthorizedAccessException)
        {
            throw new InvalidOperationException($"The analysis engine settings could not be read ({path}): {ex.Message}. Run deploy.cmd to repair the install.");
        }
        if (config == null || !File.Exists(config.Python))
            throw new InvalidOperationException($"The analysis engine is missing ({config?.Python ?? path}). Run deploy.cmd to repair the install.");
        config.PythonPath = Path.GetFullPath(Path.Combine(folder, config.PythonPath));
        return config;
    }
}

/// <summary>The app's settings (settings.json in the data folder).</summary>
sealed class AppSettings
{
    /// <summary>Claude Code config folder whose login the analyses and the chat use; null = Claude Code's default.</summary>
    public string? ClaudeProfile { get; set; }

    [JsonIgnore]
    public string Path { get; private set; } = "";

    public static AppSettings Load(string dataDir)
    {
        string path = System.IO.Path.Combine(dataDir, "settings.json");
        AppSettings settings;
        try
        {
            settings = File.Exists(path) ? JsonSerializer.Deserialize<AppSettings>(File.ReadAllText(path)) ?? new() : FirstRun();
        }
        catch (Exception ex) when (ex is IOException or JsonException)
        {
            settings = new();
        }
        settings.Path = path;
        return settings;
    }

    /// <summary>On the first start, use the Claude profile Recording Reports uses, if it has one.</summary>
    static AppSettings FirstRun()
    {
        string other = System.IO.Path.Combine(
            System.Environment.GetFolderPath(System.Environment.SpecialFolder.LocalApplicationData),
            "VanDang", "RecordingReports", "data", "settings.json");
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(other));
            if (doc.RootElement.TryGetProperty("ClaudeProfile", out var p) && p.ValueKind == JsonValueKind.String)
                return new AppSettings { ClaudeProfile = p.GetString() };
        }
        catch (Exception ex) when (ex is IOException or JsonException or UnauthorizedAccessException)
        {
        }
        return new AppSettings();
    }

    public void Save() =>
        File.WriteAllText(Path, JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true }));

    /// <summary>Claude Code config folders in the user's home that hold a login.</summary>
    public static List<string> ClaudeProfiles()
    {
        string home = System.Environment.GetFolderPath(System.Environment.SpecialFolder.UserProfile);
        return Directory.GetDirectories(home, ".claude*")
            .Where(d => File.Exists(System.IO.Path.Combine(d, ".credentials.json")))
            .OrderBy(d => d, StringComparer.OrdinalIgnoreCase)
            .ToList();
    }
}

/// <summary>
/// The core's local service (<c>media-analysis serve</c>): started hidden, its address read from the first line it
/// prints, stopped with everything it started when the window closes.
/// </summary>
sealed class Service : IDisposable
{
    readonly Process _process;

    public string Url { get; }
    public string BaseUrl { get; }
    public string Token { get; }

    Service(Process process, string url)
    {
        _process = process;
        Url = url;
        var uri = new Uri(url);
        BaseUrl = $"{uri.Scheme}://{uri.Authority}";
        Token = System.Web.HttpUtility.ParseQueryString(uri.Query)["token"] ?? "";
    }

    public static Service Start(CoreConfig core, string uiDir, string dataDir, AppSettings settings)
    {
        var info = new ProcessStartInfo(core.Python)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            WorkingDirectory = dataDir,
        };
        foreach (string a in Arguments(uiDir, settings))
            info.ArgumentList.Add(a);
        foreach (var (name, value) in core.Environment)
            info.Environment[name] = value;
        info.Environment["PYTHONPATH"] = core.PythonPath;
        info.Environment["MEDIA_ANALYSIS_HOME"] = dataDir;
        info.Environment["PYTHONIOENCODING"] = "utf-8";

        var process = Process.Start(info) ?? throw new InvalidOperationException("The analysis engine didn't start.");
        string log = Path.Combine(dataDir, "service.log");
        // stderr goes to a log so it never fills the pipe and blocks the service.
        process.ErrorDataReceived += (_, e) =>
        {
            if (e.Data != null)
                try { File.AppendAllText(log, e.Data + System.Environment.NewLine); } catch (IOException) { }
        };
        process.BeginErrorReadLine();
        var readLine = process.StandardOutput.ReadLineAsync();
        if (!readLine.Wait(TimeSpan.FromSeconds(60)) || readLine.Result == null)
        {
            Kill(process);
            throw new InvalidOperationException($"The analysis engine didn't start. See {log}.");
        }
        string url = ListeningUrl(readLine.Result)
            ?? throw new InvalidOperationException($"The analysis engine answered unexpectedly: {readLine.Result}");
        return new Service(process, url);
    }

    internal static List<string> Arguments(string uiDir, AppSettings settings)
    {
        var args = new List<string> { "-m", "media_analysis", "serve", "--ui", uiDir };
        if (!string.IsNullOrWhiteSpace(settings.ClaudeProfile))
            args.AddRange(["--claude-profile", settings.ClaudeProfile]);
        return args;
    }

    /// <summary>The URL from the service's <c>{"event": "listening", "url": ...}</c> line.</summary>
    internal static string? ListeningUrl(string line)
    {
        try
        {
            using var doc = JsonDocument.Parse(line);
            var root = doc.RootElement;
            return root.TryGetProperty("event", out var e) && e.GetString() == "listening"
                && root.TryGetProperty("url", out var u) ? u.GetString() : null;
        }
        catch (JsonException)
        {
            return null;
        }
    }

    public bool Running => !_process.HasExited;

    /// <summary>Whether an analysis or a chat turn is running.</summary>
    public async Task<bool> IsBusyAsync()
    {
        try
        {
            using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };
            http.DefaultRequestHeaders.Add("X-Token", Token);
            using var doc = JsonDocument.Parse(await http.GetStringAsync($"{BaseUrl}/api/busy"));
            return doc.RootElement.GetProperty("busy").GetBoolean();
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or JsonException or KeyNotFoundException)
        {
            return false;
        }
    }

    static void Kill(Process p)
    {
        try
        {
            // The whole tree: analyses run as child processes with their own children (ffmpeg, Claude Code).
            p.Kill(entireProcessTree: true);
            p.WaitForExit(5000);
        }
        catch (Exception ex) when (ex is InvalidOperationException or System.ComponentModel.Win32Exception)
        {
        }
    }

    public void Dispose()
    {
        Kill(_process);
        _process.Dispose();
    }
}
