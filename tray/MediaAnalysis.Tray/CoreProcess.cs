using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace MediaAnalysis.Tray;

/// <summary>
/// How to start the Python core, from <c>core.json</c> next to the exe (written by build.ps1): the shared Python
/// environment, the core's code folder and the shared ffmpeg/model folders.
/// </summary>
sealed class CoreConfig
{
    public const string FileName = "core.json";

    public string Python { get; set; } = "";

    /// <summary>Folder holding the <c>media_analysis</c> package, relative to the exe folder or absolute.</summary>
    public string PythonPath { get; set; } = "app";

    public Dictionary<string, string> Environment { get; set; } = [];

    /// <summary>Loads core.json from <paramref name="folder"/>; returns null with a reason when it can't.</summary>
    public static CoreConfig? Load(string folder, out string? error)
    {
        error = null;
        string path = Path.Combine(folder, FileName);
        try
        {
            var config = JsonSerializer.Deserialize<CoreConfig>(File.ReadAllText(path));
            if (config == null || !File.Exists(config.Python))
            {
                error = $"The analysis engine is missing ({config?.Python ?? path}). Run deploy.cmd to repair the install.";
                return null;
            }
            config.PythonPath = Path.GetFullPath(Path.Combine(folder, config.PythonPath));
            return config;
        }
        catch (Exception ex) when (ex is IOException or JsonException or UnauthorizedAccessException)
        {
            error = $"The analysis engine settings could not be read ({path}): {ex.Message}. Run deploy.cmd to repair the install.";
            return null;
        }
    }

    /// <summary>A hidden process running <c>media-analysis run &lt;input&gt; &lt;output&gt; --json [options]</c>.</summary>
    public ProcessStartInfo RunCommand(string inputDir, string outputDir, IEnumerable<string> options) =>
        Command(["run", inputDir, outputDir, "--json", .. options], inputDir);

    /// <summary>A hidden process running <c>media-analysis &lt;args&gt;</c> with output captured.</summary>
    public ProcessStartInfo Command(IEnumerable<string> args, string? workingDirectory = null)
    {
        var info = new ProcessStartInfo(Python)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
            WorkingDirectory = workingDirectory ?? System.IO.Path.GetTempPath(),
        };
        info.ArgumentList.Add("-m");
        info.ArgumentList.Add("media_analysis");
        foreach (string arg in args)
            info.ArgumentList.Add(arg);
        foreach (var (name, value) in Environment)
            info.Environment[name] = value;
        info.Environment["PYTHONPATH"] = PythonPath;
        info.Environment["MEDIA_ANALYSIS_HOME"] = AppPaths.Data;
        info.Environment["PYTHONIOENCODING"] = "utf-8";
        return info;
    }

    /// <summary>The built-in templates shipped with the core.</summary>
    public string BuiltinTemplatesDir => System.IO.Path.Combine(PythonPath, "media_analysis", "templates", "builtin");
}

/// <summary>One line of the core's <c>--json</c> output (see src/media_analysis/events.py and cli.py).</summary>
sealed record CoreEvent(string Event, string Stage, string Message, double? Completed, double? Total, bool Generic = false)
{
    /// <summary>Parses one JSON line; null for anything that isn't an event object.</summary>
    public static CoreEvent? Parse(string line)
    {
        if (string.IsNullOrWhiteSpace(line) || line[0] != '{')
            return null;
        try
        {
            using var doc = JsonDocument.Parse(line);
            var root = doc.RootElement;
            string Text(string name) => root.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString()! : "";
            double? Number(string name) => root.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetDouble() : null;
            string kind = Text("event");
            bool generic = root.TryGetProperty("generic", out var g) && g.ValueKind == JsonValueKind.True;
            return kind.Length == 0 ? null : new CoreEvent(kind, Text("stage"), Text("message"), Number("completed"), Number("total"), generic);
        }
        catch (JsonException)
        {
            return null;
        }
    }

    /// <summary>Progress in percent for "progress" events with a known total; otherwise null.</summary>
    public double? Percent => Event == "progress" && Total > 0 ? Math.Min(100, Completed!.Value / Total!.Value * 100) : null;
}
