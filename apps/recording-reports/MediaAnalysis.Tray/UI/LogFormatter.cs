using System.Text.Json;

namespace MediaAnalysis.Tray;

/// <summary>
/// Turns lines of an analysis log (header lines, the core's JSON events, and anything the core wrote to stderr)
/// into a readable view: one line per stage start/end, warnings, errors and the final performance summary.
/// </summary>
static class LogFormatter
{
    /// <summary>The readable text for one log line, or null for lines the readable view leaves out (progress ticks).</summary>
    public static string? Format(string line)
    {
        if (line.StartsWith("# "))
            return line[2..];
        if (line.Length == 0 || line[0] != '{')
            return line;
        try
        {
            using var doc = JsonDocument.Parse(line);
            var root = doc.RootElement;
            string Text(string name) => root.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString()! : "";
            string at = root.TryGetProperty("elapsed", out var e) && e.ValueKind == JsonValueKind.Number
                ? RunStatus.FormatElapsed(TimeSpan.FromSeconds(e.GetDouble()))
                : "";
            string stage = RunStatus.StageName(Text("stage"));
            string message = Text("message");
            string Line(string mark, string text) => $"[{at,8}] {mark,-6} {text}".TrimEnd();
            return Text("event") switch
            {
                "stage_started" => Line("start", Join(stage, message)),
                "stage_done" => Line("done", Join(stage, message)),
                "stage_skipped" => Line("skip", Join(stage, message)),
                "warning" => Line("WARN", message),
                "info" => Line("", message),
                "error" => Line("ERROR", message),
                "finished" => Line("done", root.TryGetProperty("generic", out var g) && g.ValueKind == JsonValueKind.True
                    ? "Report written (general report: no template fits)"
                    : $"Report written ({Text("template")})"),
                "summary" => "\n" + message + "\n",
                _ => null,
            };
        }
        catch (JsonException)
        {
            return line;
        }
    }

    private static string Join(string stage, string message) => message.Length > 0 ? $"{stage}: {message}" : stage;
}
