using System.Diagnostics;

namespace MediaAnalysis.Tray;

/// <summary>The analysis in progress.</summary>
sealed class ActiveRun(string projectPath, string name, string logPath)
{
    public string ProjectPath { get; } = projectPath;
    public string Name { get; } = name;
    public string LogPath { get; } = logPath;
    public DateTime StartedAt { get; } = DateTime.Now;
    public string? Stage { get; set; }
    public double? Percent { get; set; }
    public string? Activity { get; set; }
    public string? FinalMessage { get; set; }
    public bool GenericReport { get; set; }
    public bool Cancelled { get; set; }

    /// <summary>E.g. "Transcribing audio… 45%".</summary>
    public string Status => RunStatus.Describe(Stage, Percent, Activity);

    public TimeSpan Elapsed => DateTime.Now - StartedAt;
}

/// <summary>
/// Runs analyses one at a time (transcription already uses every CPU core) by starting the Python core as a
/// hidden child process and following its JSON progress. The full output goes to one log file per run from the
/// first line on, ending with the core's performance summary. Every event is raised on the UI thread.
/// </summary>
sealed class AnalysisRunner : IDisposable
{
    private readonly SynchronizationContext _ui;
    private readonly Func<CoreConfig?> _core;
    private readonly Func<IEnumerable<string>> _options;
    private readonly RunHistory _history;
    private readonly Func<string, string> _displayName;
    private readonly List<(string Path, string Name, string Output, bool Fresh)> _queue = [];
    private Process? _process;

    /// <summary>Something changed: queue, progress or a result.</summary>
    public event Action? Changed;

    /// <summary>An analysis ended (not raised for queued projects that were removed before starting).</summary>
    public event Action<string, RunResult>? Finished;

    /// <param name="core">How to start the core; null when it isn't available.</param>
    /// <param name="options">Core options from the settings, read when each analysis starts.</param>
    /// <param name="displayName">The user's name for a project folder, recorded in the run statistics.</param>
    public AnalysisRunner(SynchronizationContext ui, Func<CoreConfig?> core, Func<IEnumerable<string>> options, RunHistory history,
        Func<string, string>? displayName = null)
    {
        _ui = ui;
        _core = core;
        _options = options;
        _history = history;
        _displayName = displayName ?? Path.GetFileName;
    }

    public ActiveRun? Current { get; private set; }

    /// <summary>True while an analysis runs or is queued.</summary>
    public bool IsBusy => Current != null || _queue.Count > 0;

    public int QueuedCount => _queue.Count;

    public bool IsRunning(string projectPath) => Current != null && SamePath(Current.ProjectPath, projectPath);

    /// <summary>1-based position in the queue, or 0 when not queued.</summary>
    public int QueuePosition(string projectPath) => _queue.FindIndex(q => SamePath(q.Path, projectPath)) + 1;

    public bool IsQueued(string projectPath) => QueuePosition(projectPath) > 0;

    public RunResult? LastResult(string projectPath) => _history.Get(projectPath);

    /// <summary>Queues a project; it starts right away when nothing else runs.</summary>
    /// <param name="fresh">Clear the project's cached work first, so every step runs again (the core's <c>--fresh</c>).</param>
    public void Enqueue(Project project, bool fresh = false)
    {
        if (IsRunning(project.Path) || IsQueued(project.Path) || project.OutputPath.Length == 0)
            return;
        _queue.Add((project.Path, project.Name, project.OutputPath, fresh));
        StartNext();
        Changed?.Invoke();
    }

    /// <summary>Stops a running analysis (the core resumes from its cache next time) or removes it from the queue.</summary>
    public void Cancel(string projectPath)
    {
        if (_queue.RemoveAll(q => SamePath(q.Path, projectPath)) > 0)
            Changed?.Invoke();
        if (Current != null && IsRunning(projectPath))
        {
            Current.Cancelled = true;
            Kill();
        }
    }

    /// <summary>Empties the queue and stops the running analysis.</summary>
    public void CancelAll()
    {
        _queue.Clear();
        if (Current != null)
        {
            Current.Cancelled = true;
            Kill();
        }
        Changed?.Invoke();
    }

    private void StartNext()
    {
        if (Current != null || _queue.Count == 0)
            return;
        var (path, name, output, fresh) = _queue[0];
        _queue.RemoveAt(0);

        string logPath = Path.Combine(AppPaths.Logs, $"{DateTime.Now:yyyy-MM-dd_HH-mm-ss}_{Sanitize(name)}.log");
        var run = new ActiveRun(path, name, logPath);
        Current = run;

        StreamWriter? log = null;
        try
        {
            Directory.CreateDirectory(AppPaths.Logs);
            log = new StreamWriter(new FileStream(logPath, FileMode.Create, FileAccess.Write, FileShare.ReadWrite)) { AutoFlush = true };
            List<string> options = [.. _options()];
            if (fresh)
                options.Add("--fresh");
            log.WriteLine($"# Project:  {path}");
            log.WriteLine($"# Output:   {output}");
            log.WriteLine($"# Started:  {run.StartedAt:yyyy-MM-dd HH:mm:ss}");
            log.WriteLine($"# Build:    {BuildInfo.Built}");
            log.WriteLine($"# Options:  {(options.Count > 0 ? string.Join(' ', options) : "(defaults)")}");
            log.WriteLine($"# Claude:   {ClaudeProfile(options)}");

            CoreConfig? core = _core();
            if (core == null)
            {
                log.WriteLine("# The analysis engine is not available. Run deploy.cmd to repair the install.");
                log.Dispose();
                Complete(run, RunOutcome.Failed, "The analysis engine is not available. Run deploy.cmd to repair the install.");
                return;
            }

            // Recorded in the run statistics, so a record leads back to this log and the name shown in the app.
            List<string> runOptions = [.. options, "--log-file", logPath, "--project-name", _displayName(path)];
            var process = new Process { StartInfo = core.RunCommand(path, output, runOptions), EnableRaisingEvents = true };
            var stderr = new List<string>();
            process.OutputDataReceived += (_, e) => OnOutput(run, log, e.Data);
            process.ErrorDataReceived += (_, e) =>
            {
                if (e.Data == null)
                    return;
                lock (log)
                {
                    log.WriteLine(e.Data);
                    stderr.Add(e.Data);
                    if (stderr.Count > 20)
                        stderr.RemoveAt(0);
                }
            };
            process.Exited += (_, _) =>
            {
                process.WaitForExit();
                int code = process.ExitCode;
                string tail;
                lock (log)
                {
                    tail = string.Join("\n", stderr.TakeLast(5));
                    _ui.Post(_ => OnExited(run, log, code, tail), null);
                }
            };
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            _process = process;
        }
        catch (Exception ex)
        {
            log?.Dispose();
            Complete(run, RunOutcome.Failed, $"Could not start the analysis: {ex.Message}");
        }
    }

    private void OnOutput(ActiveRun run, StreamWriter log, string? line)
    {
        if (line == null)
            return;
        lock (log)
            log.WriteLine(line);
        var e = CoreEvent.Parse(line);
        if (e == null)
            return;
        switch (e.Event)
        {
            case "finished":
                run.GenericReport = e.Generic;
                return;
            case "error":
                run.FinalMessage = e.Message;
                return;
            case "stage_started" or "progress" or "activity":
                _ui.Post(_ =>
                {
                    if (e.Event == "stage_started")
                        (run.Stage, run.Percent, run.Activity) = (e.Stage, null, null);
                    else if (e.Event == "progress")
                        run.Percent = e.Percent ?? run.Percent;
                    else
                        (run.Percent, run.Activity) = (null, e.Message);
                    Changed?.Invoke();
                }, null);
                return;
        }
    }

    private void OnExited(ActiveRun run, StreamWriter log, int exitCode, string stderrTail)
    {
        _process?.Dispose();
        _process = null;
        var (outcome, message) = run.Cancelled ? (RunOutcome.Cancelled, "Stopped by you. Analysing again continues where it stopped.")
            : exitCode == 0 ? (RunOutcome.Succeeded, run.GenericReport ? "No template fits; wrote a general report." : "")
            : (RunOutcome.Failed, run.FinalMessage is { Length: > 0 } m ? m
                : stderrTail.Length > 0 ? stderrTail : $"The analysis ended with exit code {exitCode}.");
        lock (log)
        {
            log.WriteLine($"# Finished: {DateTime.Now:yyyy-MM-dd HH:mm:ss}, {outcome.ToString().ToLowerInvariant()}, " +
                $"elapsed {RunStatus.FormatElapsed(run.Elapsed)} (exit code {exitCode})");
            log.Dispose();
        }
        Complete(run, outcome, message);
    }

    private void Complete(ActiveRun run, RunOutcome outcome, string message)
    {
        var result = new RunResult(outcome, message, run.LogPath, run.StartedAt, DateTime.Now, run.GenericReport);
        Current = null;
        _history.Set(run.ProjectPath, result);
        Finished?.Invoke(run.ProjectPath, result);
        StartNext();
        Changed?.Invoke();
    }

    private void Kill()
    {
        try
        {
            // The core starts helper processes (ffmpeg, Claude Code); stop them too.
            _process?.Kill(entireProcessTree: true);
        }
        catch (Exception ex) when (ex is InvalidOperationException or System.ComponentModel.Win32Exception)
        {
        }
    }

    public void Dispose()
    {
        _queue.Clear();
        Kill();
        _process?.Dispose();
    }

    /// <summary>The Claude Code profile the core signs in with (its <c>--claude-profile</c>).</summary>
    internal static string ClaudeProfile(IReadOnlyList<string> options)
    {
        int i = options.ToList().IndexOf("--claude-profile");
        return i >= 0 && i + 1 < options.Count ? options[i + 1] : "(not set)";
    }

    private static bool SamePath(string a, string b) =>
        string.Equals(a.TrimEnd('\\'), b.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase);

    private static string Sanitize(string name)
    {
        var invalid = Path.GetInvalidFileNameChars();
        string clean = new(name.Select(c => invalid.Contains(c) ? '_' : c).ToArray());
        return clean.Length > 60 ? clean[..60] : clean;
    }
}
