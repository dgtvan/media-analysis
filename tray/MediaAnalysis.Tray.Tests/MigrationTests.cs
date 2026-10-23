using MediaAnalysis.Tray;

namespace MediaAnalysis.Tray.Tests;

public sealed class MigrationTests : IDisposable
{
    private readonly string _dir = Directory.CreateDirectory(
        Path.Combine(Path.GetTempPath(), "ma-migration-tests-" + Guid.NewGuid().ToString("N"))).FullName;

    public void Dispose() => Directory.Delete(_dir, recursive: true);

    [Fact]
    public void Old_data_is_copied_once_and_kept()
    {
        string roaming = Directory.CreateDirectory(Path.Combine(_dir, "Roaming", "MediaAnalysis")).FullName;
        string local = Directory.CreateDirectory(Path.Combine(_dir, "Local", "MediaAnalysis")).FullName;
        string data = Path.Combine(local, "data");
        File.WriteAllText(Path.Combine(roaming, "settings.json"), "{\"ProjectsRoot\": \"D:\\Rec\"}");
        Directory.CreateDirectory(Path.Combine(roaming, "templates"));
        File.WriteAllText(Path.Combine(roaming, "templates", "sales.md"), "t");
        Directory.CreateDirectory(Path.Combine(local, "logs"));
        string oldLog = Path.Combine(local, "logs", "a.log");
        File.WriteAllText(oldLog, "# log");
        File.WriteAllText(Path.Combine(local, "history.json"), $"{{\"x\": {{\"LogPath\": \"{oldLog.Replace("\\", "\\\\")}\"}}}}");
        File.WriteAllText(Path.Combine(local, "update-state.txt"), "running");

        Assert.Empty(AppInitializer.MigrateLegacyData(roaming, local, data));

        Assert.Contains("D:", File.ReadAllText(Path.Combine(data, "settings.json")));
        Assert.True(File.Exists(Path.Combine(data, "templates", "sales.md")));
        Assert.True(File.Exists(Path.Combine(data, "logs", "a.log")));
        Assert.True(File.Exists(Path.Combine(data, "update-state.txt")));
        Assert.Contains(Path.Combine(data, "logs", "a.log").Replace("\\", "\\\\"), File.ReadAllText(Path.Combine(data, "history.json")));
        Assert.True(File.Exists(Path.Combine(roaming, "settings.json")));
        Assert.True(File.Exists(oldLog));

        File.WriteAllText(Path.Combine(data, "settings.json"), "{}");
        AppInitializer.MigrateLegacyData(roaming, local, data);
        Assert.Equal("{}", File.ReadAllText(Path.Combine(data, "settings.json")));
    }
}
