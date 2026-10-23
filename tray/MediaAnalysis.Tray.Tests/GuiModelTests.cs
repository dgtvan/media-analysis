using MediaAnalysis.Tray;

namespace MediaAnalysis.Tray.Tests;

public sealed class GuiModelTests : IDisposable
{
    private readonly string _dir = Directory.CreateDirectory(
        Path.Combine(Path.GetTempPath(), "ma-gui-tests-" + Guid.NewGuid().ToString("N"))).FullName;

    public void Dispose() => Directory.Delete(_dir, recursive: true);

    [Theory]
    [InlineData(null, null, null, "Starting…")]
    [InlineData("transcribe", 45.4, null, "Transcribing audio… 45%")]
    [InlineData("report", null, "thinking", "Writing the report… (thinking)")]
    [InlineData("scan", null, null, "Scanning video…")]
    public void Status_is_plain_language(string? stage, double? percent, string? activity, string expected)
    {
        Assert.Equal(expected, RunStatus.Describe(stage, percent, activity));
    }

    [Fact]
    public void Elapsed_is_hours_minutes_seconds()
    {
        Assert.Equal("0:04:05", RunStatus.FormatElapsed(TimeSpan.FromSeconds(245)));
        Assert.Equal("26:00:01", RunStatus.FormatElapsed(TimeSpan.FromHours(26) + TimeSpan.FromSeconds(1)));
    }

    [Fact]
    public void Readable_log_keeps_stages_warnings_errors_and_summary()
    {
        Assert.Equal("Project:  D:\\p", LogFormatter.Format("# Project:  D:\\p"));
        Assert.Equal("[ 0:01:28] done   Transcribing audio: 7 segments",
            LogFormatter.Format("""{"elapsed": 88.3, "event": "stage_done", "stage": "transcribe", "message": "7 segments"}"""));
        Assert.Null(LogFormatter.Format("""{"elapsed": 3, "event": "progress", "stage": "scan", "message": "", "completed": 1, "total": 9}"""));
        Assert.Equal("[ 0:00:03] ERROR  boom", LogFormatter.Format("""{"elapsed": 3, "event": "error", "message": "boom"}"""));
        Assert.Contains("Performance", LogFormatter.Format("""{"event": "summary", "stage": "summary", "message": "Performance\n  stage"}"""));
        Assert.Equal("Traceback (most recent call last):", LogFormatter.Format("Traceback (most recent call last):"));
    }

    [Fact]
    public void History_survives_a_restart()
    {
        string file = Path.Combine(_dir, "history.json");
        var started = new DateTime(2026, 10, 23, 9, 0, 0);
        new RunHistory(file).Set(@"C:\Projects\a\", new RunResult(RunOutcome.Failed, "boom", "x.log", started, started.AddMinutes(3)));

        var loaded = new RunHistory(file).Get(@"c:\projects\A");

        Assert.NotNull(loaded);
        Assert.Equal(RunOutcome.Failed, loaded.Outcome);
        Assert.Equal(TimeSpan.FromMinutes(3), loaded.Elapsed);
    }

    [Fact]
    public void Settings_become_core_options()
    {
        Assert.Empty(new AppSettings().CoreArguments());
        var s = new AppSettings { WhisperModel = "large-v3-turbo", Language = "vi", Effort = "low", MaxFrames = 10 };
        Assert.Equal(["--whisper-model", "large-v3-turbo", "--language", "vi", "--effort", "low", "--max-frames", "10"], s.CoreArguments());
    }

    [Fact]
    public void User_templates_replace_built_in_ones()
    {
        string builtin = Directory.CreateDirectory(Path.Combine(_dir, "builtin")).FullName;
        string user = Directory.CreateDirectory(Path.Combine(_dir, "user")).FullName;
        File.WriteAllText(Path.Combine(builtin, "meeting.md"), "---\nid: meeting\nname: Meeting\ndescription: A meeting.\n---\nx");
        File.WriteAllText(Path.Combine(builtin, "generic.md"), "---\nid: generic\nname: General\ndescription: d\nfallback: true\n---\nx");
        File.WriteAllText(Path.Combine(user, "meeting.md"), "---\nid: meeting\nname: My meeting\ndescription: Mine.\n---\nx");

        var list = TemplateCatalog.Load(builtin, user);

        Assert.Equal(["meeting", "meeting", "generic"], list.Select(t => t.Id));
        Assert.False(list[0].BuiltIn);
        Assert.True(list[1].Overridden);
        Assert.True(list[2].Fallback);
    }

    [Fact]
    public void Project_names_default_to_the_folder_and_survive_a_reload()
    {
        var settings = new AppSettings();
        string project = Path.Combine(_dir, "2026-10-23_09-00-12-Teams");
        Assert.Equal("2026-10-23_09-00-12-Teams", settings.DisplayName(project));

        settings.SetDisplayName(project, "  Weekly standup  ");
        Assert.Equal("Weekly standup", settings.DisplayName(project.ToUpperInvariant() + "\\"));

        var reloaded = System.Text.Json.JsonSerializer.Deserialize<AppSettings>(System.Text.Json.JsonSerializer.Serialize(settings))!;
        reloaded.ProjectNames = new(reloaded.ProjectNames, StringComparer.OrdinalIgnoreCase);
        Assert.Equal("Weekly standup", reloaded.DisplayName(project));

        settings.SetDisplayName(project, "");
        Assert.Empty(settings.ProjectNames);
        settings.SetDisplayName(project, "2026-10-23_09-00-12-Teams");
        Assert.Empty(settings.ProjectNames);
    }
}
