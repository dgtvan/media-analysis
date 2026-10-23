using System.Diagnostics;

namespace MediaAnalysis.Tray;

/// <summary>
/// Runs in the background: watches the projects folder, runs analyses, updates itself, and shows the state in the
/// tray icon (blue working, amber waiting, grey idle). The tray menu has Status (the main window) and Exit.
/// </summary>
/// <remarks>
/// Working: an analysis runs or is queued. Waiting: nothing runs, but at least one project has media files and no
/// up-to-date report. Idle: everything is analysed. Anything the user must act on (a failed analysis, a broken
/// install) switches the icon to a warning that stays until the user dismisses it in the window.
/// </remarks>
sealed class TrayApp : ApplicationContext, IAppHost
{
    private enum State { Idle, Waiting, Working }

    private readonly AppSettings _settings;
    private readonly NotifyIcon _tray;
    private readonly ContextMenuStrip _menu;
    // Also how soon a settled new project starts when auto-analysis is on.
    private readonly System.Windows.Forms.Timer _rescanTimer = new() { Interval = 30_000 };
    private readonly List<string> _problems = [];
    private readonly SelfUpdater _updater = new();
    private readonly AnalysisRunner _runner;
    private readonly SynchronizationContext _ui;
    private MainForm? _window;
    private FileSystemWatcher? _watcher;
    private System.Threading.Timer? _watchDebounce;
    private List<Project> _projects = [];
    private string? _coreProblemReported;
    private string? _failedBuildReported;
    private string? _lastReport;

    public event Action? Changed;

    public TrayApp()
    {
        List<string> migrationProblems = AppInitializer.MigrateLegacyData();
        _settings = AppSettings.Load(out string? settingsLoadError);
        _ui = SynchronizationContext.Current ?? new WindowsFormsSynchronizationContext();
        _runner = new AnalysisRunner(_ui, LoadCore, _settings.CoreArguments,
            new RunHistory(AppPaths.History));

        _menu = new ContextMenuStrip();
        _menu.Items.Add(new ToolStripMenuItem("Status", null, (_, _) => ShowWindow()) { Font = new Font(_menu.Font, FontStyle.Bold) });
        _menu.Items.Add(new ToolStripSeparator());
        _menu.Items.Add(new ToolStripMenuItem("Exit", null, (_, _) => Exit()));

        _tray = new NotifyIcon { ContextMenuStrip = _menu, Visible = true };
        _tray.MouseDoubleClick += (_, e) =>
        {
            if (e.Button == MouseButtons.Left)
                ShowWindow();
        };
        _tray.BalloonTipClicked += (_, _) =>
        {
            if (_problems.Count == 0 && _lastReport != null && File.Exists(_lastReport))
                Process.Start(new ProcessStartInfo(_lastReport) { UseShellExecute = true });
            else
                ShowWindow();
        };

        _runner.Changed += Refresh;
        _runner.Finished += OnAnalysisFinished;
        _rescanTimer.Tick += (_, _) => Rescan();
        _updater.BuildsChanged += () => _ui.Post(_ => CheckForUpdate(), null);
        Application.ThreadException += (_, e) => AddProblem($"Unexpected error: {e.Exception.Message}");

        foreach (string problem in migrationProblems.Concat(AppInitializer.Run(_settings, settingsLoadError)))
            AddProblem(problem);
        LoadCore();
        WatchProjects();
        Rescan();
        _rescanTimer.Start();
        if (_settings.ProjectsRoot == null || _settings.OutputRoot == null)
        {
            ShowBalloon("Media Analysis", "Choose a projects folder and an output folder on the Config tab.", ToolTipIcon.Info);
            _ui.Post(_ => ShowWindow(), null);
        }
        // Also picks up a build published while the app wasn't running, and reports state at startup.
        _ui.Post(_ => CheckForUpdate(), null);
    }

    // ---- IAppHost -------------------------------------------------------------------------------------------------

    public AppSettings Settings => _settings;
    public AnalysisRunner Runner => _runner;
    public IReadOnlyList<Project> Projects => _projects;
    public IReadOnlyList<string> Problems => _problems;
    public string? InstalledBuildId => _updater.InstalledBuildId;
    public string? UpdateSource => _updater.PublishRoot;

    public CoreConfig? LoadCore()
    {
        CoreConfig? core = CoreConfig.Load(AppContext.BaseDirectory, out string? error);
        if (error != null && error != _coreProblemReported)
        {
            _coreProblemReported = error;
            AddProblem(error);
        }
        return core;
    }

    /// <summary>Re-reads the projects folder and updates the icon and the window.</summary>
    public void Rescan()
    {
        _projects = ProjectCatalog.Scan(_settings.ProjectsRoot, _settings.OutputRoot);
        if (_settings.OutputRoot != null)
        {
            var now = DateTime.UtcNow;
            foreach (var p in _projects.Where(p => AutoAnalysis.ShouldStart(p, _settings, _runner.LastResult(p.Path), now))
                         .OrderBy(p => p.CreatedUtc))
                _runner.Enqueue(p);
        }
        Refresh();
    }

    public void DismissProblems()
    {
        _problems.Clear();
        _coreProblemReported = null;
        Refresh();
        CheckForUpdate();
    }

    public void SaveSettings(bool foldersChanged)
    {
        try
        {
            _settings.Save();
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            AddProblem($"Could not save the settings: {ex.Message}");
        }
        if (foldersChanged)
            WatchProjects();
        Rescan();
    }

    /// <summary>
    /// Installs the latest published build when it differs from this one (newer, or older after a rollback),
    /// but never during an analysis or while a warning is undismissed (restarting would lose it). A build whose
    /// install already failed is not retried, so a broken build can't cause a restart loop.
    /// </summary>
    public void CheckForUpdate()
    {
        if (!_updater.IsEnabled)
            return;
        string? latest = _updater.FindLatestBuild();
        if (latest == null || latest == _updater.InstalledBuildId)
        {
            _updater.WriteState("running");
            return;
        }
        if (_updater.LastAttemptId == latest)
        {
            _updater.WriteState($"failed: build {latest} did not install");
            if (_failedBuildReported != latest)
            {
                _failedBuildReported = latest;
                AddProblem(Path.GetFileName(AppContext.BaseDirectory.TrimEnd('\\')).Equals("app", StringComparison.OrdinalIgnoreCase)
                    ? $"Update {latest} could not be installed. Run install.cmd to repair the install."
                    : "This copy is installed in an old location and can't update itself any more. "
                        + "Run install.cmd once to move it; after that it updates itself again.");
            }
            return;
        }
        if (_runner.IsBusy)
        {
            _updater.WriteState($"waiting: build {latest} until the analysis in progress ends");
            return;
        }
        if (_problems.Count > 0)
        {
            _updater.WriteState($"waiting: build {latest} until the warning is dismissed");
            return;
        }
        try
        {
            _updater.LaunchUpdate(latest);
            ExitNow();
        }
        catch (Exception ex)
        {
            _updater.WriteState($"failed: build {latest}: {ex.Message}", latest);
            AddProblem($"Could not install update {latest}: {ex.Message}");
        }
    }

    // ---- Internals ------------------------------------------------------------------------------------------------

    private void ShowWindow()
    {
        if (_window == null || _window.IsDisposed)
            _window = new MainForm(this);
        _window.ShowInFront();
    }

    private void WatchProjects()
    {
        _watcher?.Dispose();
        _watcher = null;
        if (_settings.ProjectsRoot == null || !Directory.Exists(_settings.ProjectsRoot))
            return;
        try
        {
            // Copying recordings writes many files; rescan once it has settled.
            _watchDebounce ??= new System.Threading.Timer(_ => _ui.Post(_ => Rescan(), null));
            _watcher = new FileSystemWatcher(_settings.ProjectsRoot)
            {
                IncludeSubdirectories = true,
                NotifyFilter = NotifyFilters.FileName | NotifyFilters.DirectoryName | NotifyFilters.LastWrite,
            };
            FileSystemEventHandler onChange = (_, _) => _watchDebounce.Change(2000, Timeout.Infinite);
            _watcher.Created += onChange;
            _watcher.Changed += onChange;
            _watcher.Deleted += onChange;
            _watcher.Renamed += (_, e) => onChange(this, e);
            _watcher.EnableRaisingEvents = true;
        }
        catch (Exception ex) when (ex is IOException or ArgumentException or UnauthorizedAccessException)
        {
            AddProblem($"Can't watch the projects folder {_settings.ProjectsRoot}: {ex.Message}");
        }
    }

    private void Refresh()
    {
        int waiting = _projects.Count(p =>
            p.State == ProjectState.NotAnalysed && !_settings.IsIgnored(p.Path) && !_runner.IsRunning(p.Path) && !_runner.IsQueued(p.Path));
        State state = _runner.IsBusy ? State.Working : waiting > 0 ? State.Waiting : State.Idle;
        bool hasProblem = _problems.Count > 0;

        string status;
        if (_runner.Current is { } run)
        {
            status = $"{_settings.DisplayName(run.ProjectPath)}: {run.Status}";
            if (_runner.QueuedCount > 0)
                status += $" (+{_runner.QueuedCount} queued)";
        }
        else if (_settings.ProjectsRoot == null || _settings.OutputRoot == null)
            status = "Choose a projects folder and an output folder";
        else if (waiting > 0)
            status = waiting == 1 ? "1 project not analysed" : $"{waiting} projects not analysed";
        else
            status = _projects.Count == 0 ? "No projects yet" : "All projects analysed";

        _tray.Icon = (state, hasProblem) switch
        {
            (State.Working, true) => TrayIcons.WorkingWarning,
            (State.Working, false) => TrayIcons.Working,
            (State.Waiting, true) => TrayIcons.WaitingWarning,
            (State.Waiting, false) => TrayIcons.Waiting,
            (_, true) => TrayIcons.Warning,
            _ => TrayIcons.Idle,
        };
        // NotifyIcon.Text throws above 127 characters.
        _tray.Text = Truncate("Media Analysis\n" + (hasProblem ? "NEEDS ATTENTION (open Status)\n" : "") + status, 127);
        Changed?.Invoke();
    }

    private void OnAnalysisFinished(string projectPath, RunResult result)
    {
        string name = _settings.DisplayName(projectPath);
        switch (result.Outcome)
        {
            case RunOutcome.Succeeded:
                _lastReport = Path.Combine(_settings.OutputRoot ?? "", Path.GetFileName(projectPath), ProjectCatalog.ReportFileName);
                if (_problems.Count == 0)
                    ShowBalloon("Report ready", result.GenericReport
                        ? $"{name}\nNo template fits, so this is a general report. Click to open it."
                        : $"{name}\nClick to open the report.", ToolTipIcon.Info);
                break;
            case RunOutcome.Failed:
                AddProblem($"{name}: analysis failed. {result.Message}");
                break;
        }
        Rescan();
        CheckForUpdate();
    }

    /// <summary>Records something the user must act on and switches the tray icon to a warning until it is dismissed.</summary>
    private void AddProblem(string text)
    {
        _problems.Add($"{DateTime.Now:HH:mm}  {text}");
        Refresh();
        _tray.ShowBalloonTip(10000, "Media Analysis needs attention", Truncate(text, 250), ToolTipIcon.Warning);
    }

    private void Exit()
    {
        if (_runner.IsBusy && MessageBox.Show(
                "An analysis is running. Stop it and exit?\n\nAnalysing the project again later continues where it stopped.",
                "Media Analysis", MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes)
            return;
        ExitNow();
    }

    private void ExitNow()
    {
        _runner.Dispose();
        _rescanTimer.Dispose();
        _watcher?.Dispose();
        _watchDebounce?.Dispose();
        _updater.Dispose();
        _window?.Dispose();
        _tray.Visible = false;
        _tray.Dispose();
        _menu.Dispose();
        ExitThread();
    }

    private void ShowBalloon(string title, string text, ToolTipIcon icon) =>
        _tray.ShowBalloonTip(5000, title, string.IsNullOrEmpty(text) ? title : text, icon);

    private static string Truncate(string text, int max) =>
        text.Length <= max ? text : text[..(max - 3)] + "...";
}
