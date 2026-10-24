namespace MediaAnalysis.Tray;

/// <summary>What the main window needs from the running app (implemented by <see cref="TrayApp"/>).</summary>
interface IAppHost
{
    AppSettings Settings { get; }
    AnalysisRunner Runner { get; }

    /// <summary>How long each project's recordings are (the Length column).</summary>
    MediaDurations Durations { get; }
    IReadOnlyList<Project> Projects { get; }

    /// <summary>Things the user must act on; the tray icon shows a warning until they are dismissed.</summary>
    IReadOnlyList<string> Problems { get; }

    /// <summary>Raised on the UI thread when projects, runs, problems or settings change.</summary>
    event Action? Changed;

    CoreConfig? LoadCore();
    void Rescan();
    void DismissProblems();

    /// <summary>Saves the settings; <paramref name="foldersChanged"/> re-watches the projects folder.</summary>
    void SaveSettings(bool foldersChanged);
}
