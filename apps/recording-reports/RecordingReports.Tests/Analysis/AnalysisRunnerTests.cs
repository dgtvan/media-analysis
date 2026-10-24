namespace RecordingReports.Tests.Analysis;

/// <summary>
/// Runs the real core end to end. Opt-in, because it needs an installed build and a Claude login and takes
/// minutes: set MEDIA_ANALYSIS_TEST_CORE_DIR (a folder with core.json, e.g. the install folder) and
/// MEDIA_ANALYSIS_TEST_PROJECT (a folder with a short recording).
/// </summary>
public sealed class AnalysisRunnerTests
{
    [Fact]
    public async Task Analyses_a_project_with_the_installed_core()
    {
        string? coreDir = Environment.GetEnvironmentVariable("MEDIA_ANALYSIS_TEST_CORE_DIR");
        string? projectDir = Environment.GetEnvironmentVariable("MEDIA_ANALYSIS_TEST_PROJECT");
        if (coreDir == null || projectDir == null)
            return;

        string output = Path.Combine(Path.GetTempPath(), "ma-runner-test-" + Guid.NewGuid().ToString("N"));
        var project = ProjectCatalog.Read(new DirectoryInfo(projectDir), output);
        var statuses = new List<string>();
        var finished = new TaskCompletionSource<RunResult>();
        using var runner = new AnalysisRunner(new SynchronizationContext(), () => CoreConfig.Load(coreDir, out _),
            () => ["--effort", "low"], new RunHistory(Path.Combine(output, "history.json")));
        runner.Changed += () =>
        {
            if (runner.Current?.Stage is { } s)
                lock (statuses)
                    statuses.Add(s);
        };
        runner.Finished += (_, result) => finished.TrySetResult(result);

        runner.Enqueue(project);
        RunResult result = await finished.Task.WaitAsync(TimeSpan.FromMinutes(15));

        Assert.True(result.Outcome == RunOutcome.Succeeded, $"{result.Outcome}: {result.Message} (log {result.LogPath})");
        Assert.True(File.Exists(Path.Combine(output, project.Name, ProjectCatalog.ReportFileName)));
        Assert.Contains(statuses, s => s.StartsWith("transcribe"));
        Assert.Equal(ProjectState.Analysed, ProjectCatalog.Read(new DirectoryInfo(projectDir), output).State);
        string log = File.ReadAllText(result.LogPath);
        Assert.Contains("# Project:", log);
        Assert.Contains("\"event\": \"summary\"", log);
        Assert.Contains("# Finished:", log);
    }
}
