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
        Assert.Equal(["--claude-profile", AppSettings.DefaultClaudeProfile], new AppSettings().CoreArguments());
        var s = new AppSettings { WhisperModel = "large-v3-turbo", Language = "vi", Effort = "low", MaxFrames = 10, ClaudeProfile = @"C:\p" };
        Assert.Equal(["--whisper-model", "large-v3-turbo", "--language", "vi", "--effort", "low", "--claude-profile", @"C:\p", "--max-frames", "10"],
            s.CoreArguments());
    }

    [Fact]
    public void Claude_profile_is_always_explicit_and_logged()
    {
        var s = new AppSettings { ClaudeProfile = @"C:\Users\me\.claude-work" };
        Assert.Equal(@"C:\Users\me\.claude-work", AnalysisRunner.ClaudeProfile(s.CoreArguments()));
        Assert.Equal(AppSettings.DefaultClaudeProfile, AnalysisRunner.ClaudeProfile(new AppSettings().CoreArguments()));
    }

    [Fact]
    public void Only_claude_folders_with_a_login_are_offered_as_profiles()
    {
        foreach (string name in new[] { ".claude", ".claude-work", ".claude-empty", "other" })
            Directory.CreateDirectory(Path.Combine(_dir, name));
        foreach (string name in new[] { ".claude", ".claude-work", "other" })
            File.WriteAllText(Path.Combine(_dir, name, ".credentials.json"), "{}");

        Assert.Equal([Path.Combine(_dir, ".claude"), Path.Combine(_dir, ".claude-work")], AppSettings.FindClaudeProfiles(_dir));
    }

    [Fact]
    public void Reports_open_in_a_new_vs_code_window_with_the_claude_profile()
    {
        var info = ReportOpener.VsCodeCommand(@"C:\VS Code\bin\code.cmd", @"D:\Out\Call 1\report.md", @"C:\Users\me\.claude-work");
        Assert.Equal(["--new-window", @"D:\Out\Call 1", @"D:\Out\Call 1\report.md"], info.ArgumentList);
        Assert.Equal(@"C:\Users\me\.claude-work", info.Environment["CLAUDE_CONFIG_DIR"]);
        Assert.False(info.UseShellExecute);
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

    [Fact]
    public void A_deleted_project_is_forgotten()
    {
        var settings = new AppSettings();
        string project = Path.Combine(_dir, "call");
        settings.SetDisplayName(project, "Call");
        settings.SetIgnored(project, true);

        settings.Forget(project);

        Assert.Empty(settings.ProjectNames);
        Assert.False(settings.IsIgnored(project));
    }

    [Fact]
    public void Project_length_counts_video_or_else_audio()
    {
        var lengths = new Dictionary<string, TimeSpan?>
        {
            ["video.mp4"] = TimeSpan.FromMinutes(62.05), ["audio-mic.wav"] = TimeSpan.FromMinutes(62), ["a.mp3"] = TimeSpan.FromSeconds(95),
            ["b.mp3"] = TimeSpan.FromSeconds(30), ["bad.mp3"] = null, ["audio-system.m4a"] = TimeSpan.FromMinutes(61),
        };
        using var durations = new MediaDurations(path => lengths[Path.GetFileName(path)]);
        Project Make(params string[] names) => new("p", _dir, default, names.Length, "", null, ProjectState.NotAnalysed,
            MediaFiles: names.Select(n => new MediaFile(Path.Combine(_dir, n), 1, default, n.EndsWith(".mp4"))).ToList());

        var meeting = Make("video.mp4", "audio-mic.wav");
        var audio = Make("a.mp3", "b.mp3");
        var broken = Make("bad.mp3");
        var audioSession = Make("audio-mic.wav", "audio-system.m4a");
        Assert.Equal("measuring...", durations.Describe(meeting));
        durations.Describe(audio);
        durations.Describe(broken);
        durations.Describe(audioSession);
        SpinWait.SpinUntil(() => durations.Describe(meeting) != "measuring..." && durations.Describe(audio) != "measuring..."
            && durations.Describe(broken) != "measuring..." && durations.Describe(audioSession) != "measuring...", 5000);

        Assert.Equal("1:02:03", durations.Describe(meeting));
        Assert.Equal("2:05", durations.Describe(audio));
        Assert.Equal("?", durations.Describe(broken));
        Assert.Equal("1:02:00", durations.Describe(audioSession));
        Assert.Equal("", durations.Describe(Make()));
        Assert.Equal(TimeSpan.FromSeconds(3), MediaDurations.ParseSeconds("2.6\r\n"));
        Assert.Null(MediaDurations.ParseSeconds("N/A"));
    }

    [Fact]
    public void Working_wave_moves_right_to_left()
    {
        var frames = Enumerable.Range(0, TrayIcons.FrameCount).Select(f => TrayIcons.WaveHeights(f)).ToList();
        Assert.All(frames.SelectMany(h => h), h => Assert.InRange(h, 0.25f, 1f));
        Assert.Equal(TrayIcons.FrameCount, frames.Select(h => string.Join(",", h.Select(x => x.ToString("F3")))).Distinct().Count());
        // Three frames later each bar shows about what the bar to its right showed: the wave moved one bar left.
        for (int i = 0; i < 4; i++)
            Assert.InRange(frames[3][i] - frames[0][i + 1], -0.05f, 0.05f);
    }
}
