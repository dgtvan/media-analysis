namespace RecordingReports.Projects;

/// <summary>Plain-language names for the core's stages (core/src/media_analysis/workflows/report/workflow.py, ReportWorkflow.stages).</summary>
static class RunStatus
{
    private static readonly Dictionary<string, string> StageNames = new()
    {
        ["ingest"] = "Reading files",
        ["transcribe"] = "Transcribing audio",
        ["correct"] = "Correcting the transcript",
        ["scan"] = "Scanning video",
        ["condense"] = "Condensing the transcript",
        ["classify"] = "Choosing a template",
        ["plan"] = "Picking key moments",
        ["extract"] = "Extracting frames",
        ["read"] = "Reading frames",
        ["report"] = "Writing the report",
        ["assemble"] = "Saving the output",
    };

    public static string StageName(string stage) => StageNames.GetValueOrDefault(stage, stage);

    private static readonly Dictionary<string, string> LanguageNames = new()
    {
        ["en"] = "English", ["vi"] = "Vietnamese", ["zh"] = "Chinese", ["ja"] = "Japanese", ["ko"] = "Korean",
        ["fr"] = "French", ["de"] = "German", ["es"] = "Spanish", ["th"] = "Thai", ["id"] = "Indonesian",
    };

    private static string LanguageName(string code) => LanguageNames.TryGetValue(code, out string? name) ? name : code;

    /// <summary>
    /// The languages a meeting is held in: those with at least a fifth of the speech (the core's MAIN_SHARE),
    /// largest first, e.g. "Vietnamese + English"; the largest alone when none has that much.
    /// </summary>
    public static string DescribeLanguages(IReadOnlyDictionary<string, double>? shares)
    {
        if (shares is not { Count: > 0 })
            return "";
        var ordered = shares.OrderByDescending(kv => kv.Value).ToList();
        var main = ordered.Where(kv => kv.Value >= 0.2).Select(kv => LanguageName(kv.Key)).ToList();
        return main.Count > 0 ? string.Join(" + ", main) : LanguageName(ordered[0].Key);
    }

    /// <summary>E.g. "Vietnamese 85%, English 15%", leaving out shares under 5% (usually misrecognised words).</summary>
    public static string DescribeShares(IReadOnlyDictionary<string, double>? shares) =>
        shares is null ? "" : string.Join(", ", shares.Where(kv => kv.Value >= 0.05).OrderByDescending(kv => kv.Value)
            .Select(kv => $"{LanguageName(kv.Key)} {kv.Value:P0}".Replace(" %", "%")));

    /// <summary>E.g. "Transcribing audio… 45%" or "Writing the report… (thinking)".</summary>
    public static string Describe(string? stage, double? percent, string? activity)
    {
        if (string.IsNullOrEmpty(stage))
            return "Starting…";
        string text = StageName(stage) + "…";
        if (percent is { } p)
            text += $" {Math.Clamp(p, 0, 100):0}%";
        else if (!string.IsNullOrEmpty(activity))
            text += $" ({activity})";
        return text;
    }

    /// <summary>"0:04:12" style, hours not padded.</summary>
    public static string FormatElapsed(TimeSpan elapsed) =>
        $"{(int)elapsed.TotalHours}:{elapsed.Minutes:00}:{elapsed.Seconds:00}";
}
