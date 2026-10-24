namespace MediaAnalysis.Tray;

/// <summary>
/// Which projects to analyse without being asked: projects created after the option was turned on, never analysed
/// or attempted before, whose media files have stopped changing for <see cref="SettleTime"/> (so a recording that is
/// still being copied in isn't analysed half-way). A failed or stopped attempt is not retried automatically.
/// </summary>
static class AutoAnalysis
{
    public static readonly TimeSpan SettleTime = TimeSpan.FromMinutes(2);

    public static bool IsCandidate(Project project, AppSettings settings, RunResult? lastResult) =>
        settings.AutoAnalyse
        && settings.AutoAnalyseSinceUtc is { } since
        && project.CreatedUtc >= since
        && project.State == ProjectState.NotAnalysed
        && !settings.IsIgnored(project.Path)
        && project.ReportWrittenUtc == null
        && lastResult == null;

    /// <summary>A candidate whose files are still changing.</summary>
    public static bool IsSettling(Project project, AppSettings settings, RunResult? lastResult, DateTime nowUtc) =>
        IsCandidate(project, settings, lastResult) && nowUtc - project.NewestMediaUtc < SettleTime;

    public static bool ShouldStart(Project project, AppSettings settings, RunResult? lastResult, DateTime nowUtc) =>
        IsCandidate(project, settings, lastResult) && nowUtc - project.NewestMediaUtc >= SettleTime;
}
