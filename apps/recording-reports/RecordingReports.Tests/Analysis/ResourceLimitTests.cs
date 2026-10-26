using System.Diagnostics;
using System.Text.Json;

namespace RecordingReports.Tests.Analysis;

public sealed class ResourceLimitTests
{
    [Fact]
    public void Job_limits_a_running_process_and_changes_while_it_runs()
    {
        using var process = Process.Start(new ProcessStartInfo("ping", "-n 30 127.0.0.1") { CreateNoWindow = true, UseShellExecute = false })!;
        try
        {
            using var job = new ResourceJob();
            job.Apply(ResourceLevel.Light);
            job.Add(process);
            process.Refresh();
            Assert.Equal(25, job.CpuPercentInForce());
            Assert.Equal(ProcessPriorityClass.Idle, process.PriorityClass);

            job.Apply(ResourceLevel.Fast);
            process.Refresh();
            Assert.Equal(75, job.CpuPercentInForce());
            Assert.Equal(ProcessPriorityClass.BelowNormal, process.PriorityClass);

            job.Apply(ResourceLevel.Full);
            process.Refresh();
            Assert.Null(job.CpuPercentInForce());
            Assert.Equal(ProcessPriorityClass.Normal, process.PriorityClass);
        }
        finally
        {
            process.Kill();
        }
    }

    [Fact]
    public void Levels_go_from_least_to_most()
    {
        Assert.Equal([ResourceLevel.Light, ResourceLevel.Balanced, ResourceLevel.Fast, ResourceLevel.Full], ResourceLevels.All);
        Assert.Equal(ResourceLevel.Balanced, ResourceLevels.Default);
        Assert.Equal("Balanced (up to 50% of the CPU, below-normal priority)", ResourceLevel.Balanced.Describe());
        Assert.Equal("Full (no limit, normal priority)", ResourceLevel.Full.Describe());
    }

    [Fact]
    public void Levels_are_saved_by_name_and_older_files_get_the_default()
    {
        string json = JsonSerializer.Serialize(new AnalysisRunner.PendingRun("a", false, null, ResourceLevel.Full));
        Assert.Contains("\"Level\":\"Full\"", json);
        Assert.Equal(ResourceLevel.Full, JsonSerializer.Deserialize<AnalysisRunner.PendingRun>(json)!.Level);
        Assert.Null(JsonSerializer.Deserialize<AnalysisRunner.PendingRun>("""{"Path":"a","Fresh":false}""")!.Level);

        Assert.Equal(ResourceLevel.Fast, JsonSerializer.Deserialize<AppSettings>("""{"Resources":"Fast"}""")!.Resources);
        Assert.Equal(ResourceLevels.Default, JsonSerializer.Deserialize<AppSettings>("{}")!.Resources);
    }

    [Fact]
    public void Resumed_work_keeps_the_level_chosen_for_it()
    {
        var pending = new AnalysisRunner.PendingWork(new AnalysisRunner.PendingRun("a", true, TimeSpan.FromMinutes(3), ResourceLevel.Fast),
            [new AnalysisRunner.PendingRun("b", false, null, ResourceLevel.Light)]);
        var order = AnalysisRunner.ResumeOrder(pending);
        Assert.Equal([ResourceLevel.Fast, ResourceLevel.Light], order.Select(r => r.Level));
    }
}
