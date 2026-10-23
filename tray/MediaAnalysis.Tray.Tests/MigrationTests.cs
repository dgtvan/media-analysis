using MediaAnalysis.Tray;

namespace MediaAnalysis.Tray.Tests;

public sealed class MigrationTests : IDisposable
{
    private readonly string _dir = Directory.CreateDirectory(
        Path.Combine(Path.GetTempPath(), "ma-migration-tests-" + Guid.NewGuid().ToString("N"))).FullName;

    public void Dispose() => Directory.Delete(_dir, recursive: true);

    private string Roaming => Path.Combine(_dir, "Roaming", "MediaAnalysis");
    private string Local => Path.Combine(_dir, "Local", "MediaAnalysis");
    private string Data => Path.Combine(_dir, "Local", "VanDang", "MediaAnalysis", "data");

    [Fact]
    public void Data_from_before_v1_4_is_copied_once_and_kept()
    {
        Directory.CreateDirectory(Roaming);
        Directory.CreateDirectory(Local);
        File.WriteAllText(Path.Combine(Roaming, "settings.json"), "{\"ProjectsRoot\": \"D:\\Rec\"}");
        Directory.CreateDirectory(Path.Combine(Roaming, "templates"));
        File.WriteAllText(Path.Combine(Roaming, "templates", "sales.md"), "t");
        string oldLog = WriteLogAndHistory(Local);
        File.WriteAllText(Path.Combine(Local, "update-state.txt"), "running");

        Assert.Empty(AppInitializer.MigrateLegacyData(Roaming, Local, Data));

        Assert.Contains("D:", File.ReadAllText(Path.Combine(Data, "settings.json")));
        Assert.True(File.Exists(Path.Combine(Data, "templates", "sales.md")));
        Assert.True(File.Exists(Path.Combine(Data, "logs", "a.log")));
        Assert.False(File.Exists(Path.Combine(Data, "update-state.txt")));
        Assert.Contains(Path.Combine(Data, "logs", "a.log").Replace("\\", "\\\\"), File.ReadAllText(Path.Combine(Data, "history.json")));
        Assert.True(File.Exists(Path.Combine(Roaming, "settings.json")));
        Assert.True(File.Exists(oldLog));

        File.WriteAllText(Path.Combine(Data, "settings.json"), "{}");
        AppInitializer.MigrateLegacyData(Roaming, Local, Data);
        Assert.Equal("{}", File.ReadAllText(Path.Combine(Data, "settings.json")));
    }

    [Fact]
    public void Data_from_v1_4_wins_over_older_leftovers()
    {
        string v14 = Directory.CreateDirectory(Path.Combine(Local, "data")).FullName;
        Directory.CreateDirectory(Roaming);
        File.WriteAllText(Path.Combine(Roaming, "settings.json"), "{\"old\": 1}");
        File.WriteAllText(Path.Combine(v14, "settings.json"), "{\"v14\": 1}");
        Directory.CreateDirectory(Path.Combine(v14, "templates"));
        File.WriteAllText(Path.Combine(v14, "templates", "t.md"), "t");
        WriteLogAndHistory(v14);

        Assert.Empty(AppInitializer.MigrateLegacyData(Roaming, Local, Data));

        Assert.Contains("v14", File.ReadAllText(Path.Combine(Data, "settings.json")));
        Assert.True(File.Exists(Path.Combine(Data, "templates", "t.md")));
        Assert.Contains(Path.Combine(Data, "logs", "a.log").Replace("\\", "\\\\"), File.ReadAllText(Path.Combine(Data, "history.json")));
        Assert.True(File.Exists(Path.Combine(v14, "settings.json")));
    }

    [Fact]
    public void Nothing_is_created_without_old_data()
    {
        Assert.Empty(AppInitializer.MigrateLegacyData(Roaming, Local, Data));
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

    private static string WriteLogAndHistory(string folder)
    {
        Directory.CreateDirectory(Path.Combine(folder, "logs"));
        string log = Path.Combine(folder, "logs", "a.log");
        File.WriteAllText(log, "# log");
        File.WriteAllText(Path.Combine(folder, "history.json"), $"{{\"x\": {{\"LogPath\": \"{log.Replace("\\", "\\\\")}\"}}}}");
        return log;
    }
}
