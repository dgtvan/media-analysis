using MediaAnalysis.Tray;

namespace MediaAnalysis.Tray.Tests;

public sealed class AutoAnalysisTests
{
    private static readonly DateTime Since = new(2026, 10, 23, 9, 0, 0, DateTimeKind.Utc);
    private static readonly AppSettings On = new() { AutoAnalyse = true, AutoAnalyseSinceUtc = Since };

    private static Project NewProject(DateTime created, DateTime newestMedia, ProjectState state = ProjectState.NotAnalysed) =>
        new("p", @"C:\p", newestMedia, 3, @"C:\out\p", null, state, created, newestMedia);

    [Fact]
    public void New_project_starts_once_its_files_have_settled()
    {
        var project = NewProject(Since.AddMinutes(10), Since.AddMinutes(11));

        Assert.True(AutoAnalysis.IsSettling(project, On, null, Since.AddMinutes(12)));
        Assert.False(AutoAnalysis.ShouldStart(project, On, null, Since.AddMinutes(12)));
        Assert.True(AutoAnalysis.ShouldStart(project, On, null, Since.AddMinutes(13)));
    }

    [Fact]
    public void Projects_from_before_the_option_was_turned_on_are_left_alone()
    {
        var old = NewProject(Since.AddMinutes(-1), Since.AddMinutes(-1));
        Assert.False(AutoAnalysis.ShouldStart(old, On, null, Since.AddHours(1)));
    }

    [Fact]
    public void Off_analysed_empty_or_already_attempted_projects_are_not_started()
    {
        var later = Since.AddHours(1);
        var project = NewProject(Since.AddMinutes(1), Since.AddMinutes(1));
        var failed = new RunResult(RunOutcome.Failed, "x", "", Since, Since);

        Assert.False(AutoAnalysis.ShouldStart(project, new AppSettings(), null, later));
        Assert.False(AutoAnalysis.ShouldStart(project, On, failed, later));
        Assert.False(AutoAnalysis.ShouldStart(project with { State = ProjectState.Analysed }, On, null, later));
        Assert.False(AutoAnalysis.ShouldStart(project with { State = ProjectState.Empty }, On, null, later));
    }

    [Fact]
    public void Ignored_projects_are_not_started()
    {
        var settings = new AppSettings { AutoAnalyse = true, AutoAnalyseSinceUtc = Since };
        var project = NewProject(Since.AddMinutes(1), Since.AddMinutes(1));
        settings.SetIgnored(project.Path + "\\", true);

        Assert.True(settings.IsIgnored(project.Path));
        Assert.False(AutoAnalysis.ShouldStart(project, settings, null, Since.AddHours(1)));

        settings.SetIgnored(project.Path, false);
        Assert.Empty(settings.IgnoredProjects);
        Assert.True(AutoAnalysis.ShouldStart(project, settings, null, Since.AddHours(1)));
    }
}
