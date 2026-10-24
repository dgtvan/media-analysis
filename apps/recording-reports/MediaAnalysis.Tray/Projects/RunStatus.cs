namespace MediaAnalysis.Tray;

/// <summary>Plain-language names for the core's stages (core/src/media_analysis/workflows/report/workflow.py, ReportWorkflow.stages).</summary>
static class RunStatus
{
    private static readonly Dictionary<string, string> StageNames = new()
    {
        ["ingest"] = "Reading files",
        ["transcribe"] = "Transcribing audio",
        ["scan"] = "Scanning video",
        ["classify"] = "Choosing a template",
        ["plan"] = "Picking key moments",
        ["extract"] = "Extracting frames",
        ["read"] = "Reading frames",
        ["report"] = "Writing the report",
        ["assemble"] = "Saving the output",
    };

    public static string StageName(string stage) => StageNames.GetValueOrDefault(stage, stage);

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
