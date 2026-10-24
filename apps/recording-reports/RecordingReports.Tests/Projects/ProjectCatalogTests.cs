namespace RecordingReports.Tests.Projects;

public sealed class ProjectCatalogTests : IDisposable
{
    private readonly string _root = Path.Combine(Path.GetTempPath(), "ma-tray-tests-" + Guid.NewGuid().ToString("N"));
    private readonly string _projects;
    private readonly string _output;

    public ProjectCatalogTests()
    {
        _projects = Directory.CreateDirectory(Path.Combine(_root, "projects")).FullName;
        _output = Directory.CreateDirectory(Path.Combine(_root, "output")).FullName;
    }

    public void Dispose() => Directory.Delete(_root, recursive: true);

    private string AddProject(string name, DateTime changed, params string[] files)
    {
        string dir = Directory.CreateDirectory(Path.Combine(_projects, name)).FullName;
        foreach (string file in files)
        {
            string path = Path.Combine(dir, file);
            File.WriteAllText(path, "x");
            File.SetCreationTimeUtc(path, changed);
            File.SetLastWriteTimeUtc(path, changed);
        }
        Directory.SetLastWriteTimeUtc(dir, changed);
        return dir;
    }

    private void AddReport(string name, DateTime written)
    {
        string dir = Directory.CreateDirectory(Path.Combine(_output, name)).FullName;
        string report = Path.Combine(dir, ProjectCatalog.ReportFileName);
        File.WriteAllText(report, "# report");
        File.SetLastWriteTimeUtc(report, written);
    }

    [Fact]
    public void States_follow_media_files_and_report_time()
    {
        var t = new DateTime(2026, 10, 1, 12, 0, 0, DateTimeKind.Utc);
        AddProject("empty", t, "notes.txt");
        AddProject("new", t, "video.mp4");
        AddProject("done", t, "audio.wav");
        AddReport("done", t.AddMinutes(30));
        AddProject("changed", t.AddHours(2), "audio.m4a");
        AddReport("changed", t.AddHours(1));

        var states = ProjectCatalog.Scan(_projects, _output).ToDictionary(p => p.Name, p => p.State);

        Assert.Equal(ProjectState.Empty, states["empty"]);
        Assert.Equal(ProjectState.NotAnalysed, states["new"]);
        Assert.Equal(ProjectState.Analysed, states["done"]);
        Assert.Equal(ProjectState.NotAnalysed, states["changed"]);
    }

    [Fact]
    public void Copied_file_with_old_write_time_counts_by_creation_time()
    {
        var t = new DateTime(2026, 10, 1, 12, 0, 0, DateTimeKind.Utc);
        string dir = AddProject("p", t, "a.mp4");
        AddReport("p", t.AddHours(1));
        string copied = Path.Combine(dir, "b.mp4");
        File.WriteAllText(copied, "x");
        File.SetLastWriteTimeUtc(copied, t.AddDays(-30));
        File.SetCreationTimeUtc(copied, t.AddHours(2));

        Assert.Equal(ProjectState.NotAnalysed, ProjectCatalog.Scan(_projects, _output).Single().State);
    }

    [Fact]
    public void Newest_first_and_output_folder_inside_projects_is_skipped()
    {
        var t = new DateTime(2026, 10, 1, 12, 0, 0, DateTimeKind.Utc);
        AddProject("older", t, "a.mp4");
        AddProject("newer", t.AddDays(1), "a.mp4");
        AddProject(".hidden", t.AddDays(2), "a.mp4");
        string innerOutput = AddProject("reports", t.AddDays(3));

        var names = ProjectCatalog.Scan(_projects, innerOutput).Select(p => p.Name).ToList();

        Assert.Equal(["newer", "older"], names);
    }

    [Fact]
    public void Missing_folders_give_no_projects()
    {
        Assert.Empty(ProjectCatalog.Scan(null, _output));
        Assert.Empty(ProjectCatalog.Scan(Path.Combine(_root, "missing"), _output));
    }

    [Fact]
    public void No_output_folder_means_nothing_is_analysed()
    {
        AddProject("p", DateTime.UtcNow, "a.mp4");
        var project = ProjectCatalog.Scan(_projects, null).Single();
        Assert.Equal(ProjectState.NotAnalysed, project.State);
        Assert.Equal("", project.OutputPath);
    }
}
