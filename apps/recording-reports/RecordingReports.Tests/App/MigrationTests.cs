namespace RecordingReports.Tests.App;

public sealed class MigrationTests : IDisposable
{
    private readonly string _dir = Directory.CreateDirectory(
        Path.Combine(Path.GetTempPath(), "ma-migration-tests-" + Guid.NewGuid().ToString("N"))).FullName;

    public void Dispose() => Directory.Delete(_dir, recursive: true);

    private string Previous => Path.Combine(_dir, "VanDang", "MediaAnalysis", "data");
    private string Data => Path.Combine(_dir, "VanDang", "RecordingReports", "data");

    [Fact]
    public void Data_from_media_analysis_is_copied_once_and_kept()
    {
        Directory.CreateDirectory(Path.Combine(Previous, "templates"));
        Directory.CreateDirectory(Path.Combine(Previous, "stats"));
        File.WriteAllText(Path.Combine(Previous, "settings.json"), "{\"ProjectsRoot\": \"D:\\Rec\"}");
        File.WriteAllText(Path.Combine(Previous, "templates", "sales.md"), "t");
        File.WriteAllText(Path.Combine(Previous, "durations.json"), "{}");
        string oldLog = WriteLog(Previous);
        foreach (string file in new[] { "history.json", "pending.json", Path.Combine("stats", "runs.jsonl") })
            File.WriteAllText(Path.Combine(Previous, file), $"{{\"LogPath\": \"{Json(oldLog)}\"}}");

        Assert.Empty(AppInitializer.MigratePreviousData(Previous, Data));

        Assert.Contains("Rec", File.ReadAllText(Path.Combine(Data, "settings.json")));
        Assert.True(File.Exists(Path.Combine(Data, "templates", "sales.md")));
        Assert.True(File.Exists(Path.Combine(Data, "durations.json")));
        Assert.True(File.Exists(Path.Combine(Data, "logs", "a.log")));
        foreach (string file in new[] { "history.json", "pending.json", Path.Combine("stats", "runs.jsonl") })
            Assert.Contains(Json(Path.Combine(Data, "logs", "a.log")), File.ReadAllText(Path.Combine(Data, file)));
        Assert.True(File.Exists(Path.Combine(Previous, "settings.json")));
        Assert.True(File.Exists(oldLog));

        File.WriteAllText(Path.Combine(Data, "settings.json"), "{}");
        File.Delete(Path.Combine(Data, "templates", "sales.md"));
        AppInitializer.MigratePreviousData(Previous, Data);
        Assert.Equal("{}", File.ReadAllText(Path.Combine(Data, "settings.json")));
        Assert.False(File.Exists(Path.Combine(Data, "templates", "sales.md")));
    }

    [Fact]
    public void Nothing_is_created_without_old_data()
    {
        Assert.Empty(AppInitializer.MigratePreviousData(Previous, Data));
        Assert.False(Directory.Exists(Data));
    }

    [Theory]
    [InlineData(new[] { "--data-dir", @"C:\x\data" }, @"C:\x\data")]
    [InlineData(new[] { "--DATA-DIR", @"C:\x\data" }, @"C:\x\data")]
    [InlineData(new[] { "--data-dir" }, null)]
    [InlineData(new[] { "--data-dir", " " }, null)]
    [InlineData(new string[0], null)]
    public void Data_dir_argument_is_required(string[] args, string? expected) =>
        Assert.Equal(expected, Program.DataDirArgument(args));

    private static string WriteLog(string folder)
    {
        Directory.CreateDirectory(Path.Combine(folder, "logs"));
        string log = Path.Combine(folder, "logs", "a.log");
        File.WriteAllText(log, "# log");
        return log;
    }

    private static string Json(string path) => path.Replace("\\", "\\\\");
}
