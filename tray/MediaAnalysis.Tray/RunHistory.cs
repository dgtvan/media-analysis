using System.Text.Json;

namespace MediaAnalysis.Tray;

enum RunOutcome { Succeeded, Failed, Cancelled }

/// <summary>How the last analysis of a project ended.</summary>
sealed record RunResult(
    RunOutcome Outcome,
    string Message,
    string LogPath,
    DateTime StartedAt,
    DateTime FinishedAt,
    bool GenericReport = false)
{
    public TimeSpan Elapsed => FinishedAt - StartedAt;
}

/// <summary>
/// The last result per project, kept in %LOCALAPPDATA%\MediaAnalysis\data\history.json so a failure or the time an
/// analysis took is still shown after the app restarts.
/// </summary>
sealed class RunHistory
{
    private readonly string _path;
    private readonly Dictionary<string, RunResult> _results;

    public RunHistory(string path)
    {
        _path = path;
        _results = Load(path);
    }

    public RunResult? Get(string projectPath) => _results.GetValueOrDefault(Key(projectPath));

    /// <summary>Records a result and saves the file; a failed save only loses history, never the analysis.</summary>
    public void Set(string projectPath, RunResult result)
    {
        _results[Key(projectPath)] = result;
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(_path)!);
            File.WriteAllText(_path, JsonSerializer.Serialize(_results, new JsonSerializerOptions { WriteIndented = true }));
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
        }
    }

    private static Dictionary<string, RunResult> Load(string path)
    {
        try
        {
            if (File.Exists(path))
            {
                var loaded = JsonSerializer.Deserialize<Dictionary<string, RunResult>>(File.ReadAllText(path));
                if (loaded != null)
                    return new Dictionary<string, RunResult>(loaded, StringComparer.OrdinalIgnoreCase);
            }
        }
        catch (Exception ex) when (ex is IOException or JsonException or UnauthorizedAccessException)
        {
        }
        return new Dictionary<string, RunResult>(StringComparer.OrdinalIgnoreCase);
    }

    private static string Key(string projectPath) => Path.GetFullPath(projectPath).TrimEnd('\\');
}
