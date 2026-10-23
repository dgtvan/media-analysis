using System.Diagnostics;

namespace MediaAnalysis.Tray;

/// <summary>
/// The status window: the projects grid with an action per project, plus Config, Templates and About tabs.
/// Closing it hides it; the app keeps running in the tray.
/// </summary>
sealed class MainForm : Form
{
    private enum Filter { All, NotAnalysed, Done, Problems, Ignored }


    private readonly IAppHost _host;
    private readonly Panel _banner;
    private readonly Label _bannerText;
    private readonly DataGridView _grid;
    private readonly ComboBox _filter;
    private readonly TextBox _search;
    private readonly Label _summary;
    private readonly Button _analyseAll;
    private readonly Dictionary<string, LogWindow> _logWindows = new(StringComparer.OrdinalIgnoreCase);
    private readonly System.Windows.Forms.Timer _tick = new() { Interval = 1000 };

    private readonly TextBox _projectsRoot = new() { Width = 520 };
    private readonly TextBox _outputRoot = new() { Width = 520 };
    private readonly ComboBox _claudeProfile = new() { Width = 520, DropDownStyle = ComboBoxStyle.DropDown };
    private readonly ComboBox _whisperModel = new() { DropDownStyle = ComboBoxStyle.DropDownList, Width = 260 };
    private readonly ComboBox _language = new() { DropDownStyle = ComboBoxStyle.DropDownList, Width = 260 };
    private readonly ComboBox _effort = new() { DropDownStyle = ComboBoxStyle.DropDownList, Width = 260 };
    private readonly NumericUpDown _maxFrames = new() { Minimum = 0, Maximum = 100, Width = 80 };
    private readonly CheckBox _autoAnalyse = new() { Text = "Analyse new projects automatically", AutoSize = true };
    private readonly ListView _templates;
    private readonly TableLayoutPanel _about;

    private static readonly (string Label, string? Value)[] WhisperModels =
    [
        ("Large v3 (best, default)", null), ("Large v3 turbo (faster, a bit less accurate)", "large-v3-turbo"),
        ("Medium", "medium"), ("Small (fastest, least accurate)", "small"),
    ];
    private static readonly (string Label, string? Value)[] Languages =
        [("Detect (English, Vietnamese or mixed)", null), ("English", "en"), ("Vietnamese", "vi")];
    private static readonly (string Label, string? Value)[] Efforts =
    [
        ("Model default", null), ("Low", "low"), ("Medium", "medium"), ("High", "high"), ("Extra high", "xhigh"), ("Max", "max"),
    ];

    public MainForm(IAppHost host)
    {
        _host = host;
        Text = "Media Analysis";
        Size = new Size(1180, 680);
        MinimumSize = new Size(820, 420);
        StartPosition = FormStartPosition.CenterScreen;
        Icon = TrayIcons.Idle;
        Font = new Font("Segoe UI", 9f);

        _bannerText = new Label { AutoSize = false, Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleLeft, Padding = new Padding(8, 0, 0, 0) };
        var dismiss = new Button { Text = "Dismiss", Dock = DockStyle.Right, Width = 90 };
        dismiss.Click += (_, _) => _host.DismissProblems();
        var details = new Button { Text = "Details", Dock = DockStyle.Right, Width = 90 };
        details.Click += (_, _) => MessageBox.Show(this, string.Join("\n\n", _host.Problems), "Needs attention",
            MessageBoxButtons.OK, MessageBoxIcon.Warning);
        _banner = new Panel { Dock = DockStyle.Top, Height = 40, BackColor = Color.FromArgb(255, 236, 160), Visible = false, Padding = new Padding(4) };
        _banner.Controls.AddRange([_bannerText, details, dismiss]);

        var tabs = new TabControl { Dock = DockStyle.Fill, Padding = new Point(12, 5) };

        // Projects tab
        _filter = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 150 };
        _filter.Items.AddRange(["All projects", "Not analysed", "Done", "Failed or stopped", "Ignored"]);
        _filter.SelectedIndex = 0;
        _filter.SelectedIndexChanged += (_, _) => RefreshGrid();
        _search = new TextBox { Width = 200, PlaceholderText = "Search projects" };
        _search.TextChanged += (_, _) => RefreshGrid();
        _analyseAll = new Button { Text = "Analyse all not analysed", AutoSize = true };
        _analyseAll.Click += (_, _) => AnalyseAllWaiting();
        var openProjects = new Button { Text = "Open projects folder", AutoSize = true };
        openProjects.Click += (_, _) => OpenFolder(_host.Settings.ProjectsRoot);
        var openOutput = new Button { Text = "Open output folder", AutoSize = true };
        openOutput.Click += (_, _) => OpenFolder(_host.Settings.OutputRoot);
        _summary = new Label { Dock = DockStyle.Bottom, Height = 24, Padding = new Padding(6, 4, 0, 0), ForeColor = SystemColors.GrayText };
        var toolbar = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, Padding = new Padding(4, 6, 4, 4), WrapContents = false };
        toolbar.Controls.AddRange([_filter, _search, _analyseAll, openProjects, openOutput]);

        _grid = CreateGrid();
        var projectsPage = new TabPage("Projects");
        projectsPage.Controls.Add(_grid);
        projectsPage.Controls.Add(toolbar);
        projectsPage.Controls.Add(_summary);
        tabs.TabPages.Add(projectsPage);

        // Config tab
        tabs.TabPages.Add(CreateConfigPage());

        // Templates tab
        _templates = new ListView { Dock = DockStyle.Fill, View = View.Details, FullRowSelect = true, MultiSelect = false };
        _templates.Columns.Add("Id", 170);
        _templates.Columns.Add("Name", 200);
        _templates.Columns.Add("Source", 150);
        _templates.Columns.Add("Description", 560);
        _templates.DoubleClick += (_, _) => OpenSelectedTemplate();
        tabs.TabPages.Add(CreateTemplatesPage());

        // About tab
        _about = new TableLayoutPanel { Dock = DockStyle.Top, AutoSize = true, ColumnCount = 2, Padding = new Padding(12) };
        _about.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        _about.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        tabs.TabPages.Add(CreateAboutPage());

        tabs.SelectedIndexChanged += (_, _) =>
        {
            if (tabs.SelectedTab?.Text == "Templates")
                LoadTemplates();
            else if (tabs.SelectedTab?.Text == "About")
                LoadAbout();
            else if (tabs.SelectedTab?.Text == "Config")
                LoadConfig();
        };

        Controls.Add(tabs);
        Controls.Add(_banner);

        _host.Changed += OnHostChanged;
        _tick.Tick += (_, _) => RefreshGrid();
        VisibleChanged += (_, _) =>
        {
            _tick.Enabled = Visible;
            if (Visible)
                OnHostChanged();
        };
        FormClosing += (_, e) =>
        {
            if (e.CloseReason == CloseReason.UserClosing)
            {
                e.Cancel = true;
                Hide();
            }
        };
        LoadConfig();
    }

    /// <summary>Shows the window in front, restoring it when minimized.</summary>
    public void ShowInFront()
    {
        Show();
        if (WindowState == FormWindowState.Minimized)
            WindowState = FormWindowState.Normal;
        Activate();
    }

    private void OnHostChanged()
    {
        if (IsDisposed)
            return;
        _banner.Visible = _host.Problems.Count > 0;
        _bannerText.Text = _host.Problems.Count switch
        {
            0 => "",
            1 => "Needs attention: " + FirstLine(_host.Problems[0]),
            int n => $"Needs attention: {n} problems. Open Details to read them.",
        };
        RefreshGrid();
    }

    // ---- Projects -------------------------------------------------------------------------------------------------

    private DataGridView CreateGrid()
    {
        var grid = new DataGridView
        {
            Dock = DockStyle.Fill,
            EditMode = DataGridViewEditMode.EditOnKeystrokeOrF2,
            AllowUserToAddRows = false,
            AllowUserToDeleteRows = false,
            AllowUserToResizeRows = false,
            RowHeadersVisible = false,
            SelectionMode = DataGridViewSelectionMode.FullRowSelect,
            MultiSelect = true,
            AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill,
            BackgroundColor = SystemColors.Window,
            BorderStyle = BorderStyle.None,
            CellBorderStyle = DataGridViewCellBorderStyle.SingleHorizontal,
            ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.AutoSize,
        };
        grid.RowTemplate.Height = 30;
        grid.DefaultCellStyle.SelectionBackColor = Color.FromArgb(204, 228, 247);
        grid.DefaultCellStyle.SelectionForeColor = SystemColors.ControlText;
        grid.Columns.Add(new DataGridViewTextBoxColumn
        {
            HeaderText = "Name", FillWeight = 26, ToolTipText = "Your name for the project (F2 to rename). The folder isn't renamed.",
        });
        grid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Folder", FillWeight = 22, ReadOnly = true });
        grid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Status", FillWeight = 32, ReadOnly = true });
        grid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Elapsed", FillWeight = 9, ReadOnly = true });
        grid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Last analysed", FillWeight = 13, ReadOnly = true });
        grid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Length", FillWeight = 9, ReadOnly = true });
        grid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Files", FillWeight = 6, ReadOnly = true });
        foreach (DataGridViewColumn column in grid.Columns)
            column.SortMode = DataGridViewColumnSortMode.NotSortable;

        grid.CellEndEdit += (_, e) =>
        {
            if (e.ColumnIndex != 0 || grid.Rows[e.RowIndex].Tag is not string path)
                return;
            _host.Settings.SetDisplayName(path, grid.Rows[e.RowIndex].Cells[0].Value as string);
            _host.SaveSettings(foldersChanged: false);
        };
        grid.CellDoubleClick += (_, e) =>
        {
            if (e.RowIndex >= 0 && grid.Rows[e.RowIndex].Tag is string path)
            {
                if (FindProject(path) is { ReportWrittenUtc: not null })
                    OpenReport(path);
            }
        };

        var menu = new ContextMenuStrip();
        menu.Opening += (_, e) =>
        {
            menu.Items.Clear();
            List<Project> selected = SelectedProjects();
            if (selected.Count > 1)
            {
                BuildSelectionMenu(menu.Items, selected);
                return;
            }
            if (grid.CurrentRow?.Tag is not string path || FindProject(path) is not { } project)
            {
                e.Cancel = true;
                return;
            }
            BuildProjectMenu(menu.Items, project);
        };
        grid.CellMouseDown += (_, e) =>
        {
            // Right-clicking inside a multiple selection keeps it, so the menu acts on all of it.
            if (e.Button == MouseButtons.Right && e.RowIndex >= 0 && !grid.Rows[e.RowIndex].Selected)
                grid.CurrentCell = grid.Rows[e.RowIndex].Cells[0];
        };
        grid.KeyDown += (_, e) =>
        {
            if (e.KeyCode == Keys.Apps && grid.CurrentRow != null)
            {
                var cell = grid.GetCellDisplayRectangle(0, grid.CurrentRow.Index, false);
                menu.Show(grid, new Point(cell.Left + 20, cell.Bottom));
                e.Handled = true;
            }
            else if (e.KeyCode == Keys.Delete && !grid.IsCurrentCellInEditMode && SelectedProjects() is { Count: > 0 } selected)
            {
                DeleteProjects(selected);
                e.Handled = true;
            }
        };
        grid.ContextMenuStrip = menu;
        return grid;
    }

    private sealed record Row(string Path, string Name, string Folder, string Status, string Elapsed, string LastAnalysed,
        string Files, Color StatusColor)
    {
        public string Length { get; init; } = "";
    }

    private void RefreshGrid()
    {
        // Rebuilding rows would throw away a rename in progress.
        if ((!Visible && IsHandleCreated) || _grid.IsCurrentCellInEditMode)
            return;
        var runner = _host.Runner;
        string search = _search.Text.Trim();
        var filter = (Filter)Math.Max(0, _filter.SelectedIndex);
        var rows = new List<Row>();
        foreach (var p in _host.Projects)
        {
            if (search.Length > 0 && !p.Name.Contains(search, StringComparison.OrdinalIgnoreCase)
                && !_host.Settings.DisplayName(p.Path).Contains(search, StringComparison.OrdinalIgnoreCase))
                continue;
            Row row = Describe(p) with { Length = _host.Durations.Describe(p) };
            bool show = filter switch
            {
                Filter.NotAnalysed => p.State == ProjectState.NotAnalysed && !_host.Settings.IsIgnored(p.Path),
                Filter.Ignored => _host.Settings.IsIgnored(p.Path),
                Filter.Done => p.State == ProjectState.Analysed,
                Filter.Problems => runner.LastResult(p.Path) is { Outcome: RunOutcome.Failed or RunOutcome.Cancelled }
                    && p.State != ProjectState.Analysed,
                _ => true,
            };
            if (show)
                rows.Add(row);
        }

        bool sameRows = _grid.Rows.Count == rows.Count
            && rows.Select((r, i) => (string?)_grid.Rows[i].Tag == r.Path).All(same => same);
        if (!sameRows)
        {
            string? selected = _grid.CurrentRow?.Tag as string;
            var selectedPaths = _grid.SelectedRows.Cast<DataGridViewRow>().Select(r => r.Tag as string).ToHashSet();
            int scroll = Math.Max(0, _grid.FirstDisplayedScrollingRowIndex);
            _grid.Rows.Clear();
            foreach (var r in rows)
            {
                int i = _grid.Rows.Add();
                _grid.Rows[i].Tag = r.Path;
            }
            if (_grid.Rows.Count > 0)
                _grid.FirstDisplayedScrollingRowIndex = Math.Min(scroll, _grid.Rows.Count - 1);
            if (selected != null)
                foreach (DataGridViewRow gridRow in _grid.Rows)
                    if ((string?)gridRow.Tag == selected)
                        _grid.CurrentCell = gridRow.Cells[0];
            foreach (DataGridViewRow gridRow in _grid.Rows)
                if (selectedPaths.Contains(gridRow.Tag as string))
                    gridRow.Selected = true;
        }
        for (int i = 0; i < rows.Count; i++)
        {
            var r = rows[i];
            var cells = _grid.Rows[i].Cells;
            object[] values = [r.Name, r.Folder, r.Status, r.Elapsed, r.LastAnalysed, r.Length, r.Files];
            for (int c = 0; c < values.Length; c++)
                if (!Equals(cells[c].Value, values[c]))
                    cells[c].Value = values[c];
            cells[2].Style.ForeColor = cells[2].Style.SelectionForeColor = r.StatusColor;
        }

        int waiting = _host.Projects.Count(p => p.State == ProjectState.NotAnalysed && !_host.Settings.IsIgnored(p.Path)
            && !runner.IsRunning(p.Path) && !runner.IsQueued(p.Path));
        _analyseAll.Enabled = waiting > 0 && _host.Settings.OutputRoot != null;
        _summary.Text = _host.Settings.ProjectsRoot == null || _host.Settings.OutputRoot == null
            ? "Choose the projects and output folders on the Config tab."
            : string.Join(" · ", new[]
            {
                runner.Current is { } run ? $"Analysing {_host.Settings.DisplayName(run.ProjectPath)}" : null,
                runner.QueuedCount > 0 ? $"{runner.QueuedCount} queued" : null,
                $"{waiting} not analysed",
                $"{_host.Projects.Count} projects",
            }.Where(s => s != null));
    }

    private Row Describe(Project p)
    {
        var runner = _host.Runner;
        RunResult? last = runner.LastResult(p.Path);
        string lastAnalysed = p.ReportWrittenUtc is { } written ? written.ToLocalTime().ToString("yyyy-MM-dd HH:mm") : "";
        string files = p.MediaFileCount > 0 ? p.MediaFileCount.ToString() : "";
        string lastElapsed = last != null ? RunStatus.FormatElapsed(last.Elapsed) : "";
        string name = _host.Settings.DisplayName(p.Path);

        if (runner.Current is { } run && runner.IsRunning(p.Path))
            return new Row(p.Path, name, p.Name, run.Status, RunStatus.FormatElapsed(run.Elapsed), lastAnalysed, files, Color.FromArgb(0, 90, 200));
        if (runner.QueuePosition(p.Path) is > 0 and var position)
            return new Row(p.Path, name, p.Name, position == 1 ? "Queued (next)" : $"Queued ({position})", "", lastAnalysed, files, SystemColors.GrayText);
        if (_host.Settings.IsIgnored(p.Path))
            return new Row(p.Path, name, p.Name, "Ignored", lastElapsed, lastAnalysed, files, SystemColors.GrayText);

        (string status, Color color) = p.State switch
        {
            ProjectState.Empty => ("No video or audio", SystemColors.GrayText),
            ProjectState.Analysed when last is { Outcome: RunOutcome.Succeeded, GenericReport: true } =>
                ("Done (general report: no template fits)", Color.FromArgb(0, 120, 60)),
            ProjectState.Analysed => ("Done", Color.FromArgb(0, 120, 60)),
            _ when last is { Outcome: RunOutcome.Failed } => ("Failed: " + FirstLine(last.Message), Color.FromArgb(190, 30, 30)),
            _ when last is { Outcome: RunOutcome.Cancelled } => ("Stopped by you", Color.FromArgb(170, 100, 0)),
            _ when p.ReportWrittenUtc != null => ("Changed since the last report", Color.FromArgb(170, 100, 0)),
            _ when AutoAnalysis.IsSettling(p, _host.Settings, last, DateTime.UtcNow) =>
                ("New: starts once its files stop changing", SystemColors.GrayText),
            _ => ("Not analysed", Color.FromArgb(170, 100, 0)),
        };
        return new Row(p.Path, name, p.Name, status, lastElapsed, lastAnalysed, files, color);
    }

    /// <summary>Right-click menu of a project: actions first, then things to open.</summary>
    private void BuildProjectMenu(ToolStripItemCollection items, Project project)
    {
        var runner = _host.Runner;
        bool running = runner.IsRunning(project.Path);
        bool queued = runner.IsQueued(project.Path);
        bool ignored = _host.Settings.IsIgnored(project.Path);
        Font bold = new(_grid.Font, FontStyle.Bold);

        if (running)
            items.Add(new ToolStripMenuItem("Stop analysis", null, (_, _) => Stop(project.Path)) { Font = bold });
        else if (queued)
            items.Add(new ToolStripMenuItem("Remove from queue", null, (_, _) => Remove(project.Path)) { Font = bold });
        else
            items.Add(new ToolStripMenuItem(project.State == ProjectState.Analysed ? "Analyse again" : "Analyse", null, (_, _) => Analyse(project.Path))
            {
                Enabled = !ignored && project.State != ProjectState.Empty && _host.Settings.OutputRoot != null,
                Font = project.State == ProjectState.NotAnalysed && !ignored ? bold : _grid.Font,
            });
        if (!running && !queued && project.OutputPath.Length > 0 && Directory.Exists(project.OutputPath))
            items.Add(new ToolStripMenuItem("Analyse from scratch...", null, (_, _) => AnalyseFromScratch(project.Path))
            {
                Enabled = !ignored && project.State != ProjectState.Empty && _host.Settings.OutputRoot != null,
            });
        items.Add("Rename", null, (_, _) => BeginRename());
        if (ignored)
            items.Add("Unignore", null, (_, _) => SetIgnored(project.Path, false));
        else if (!running && !queued && project.State == ProjectState.NotAnalysed)
            items.Add("Ignore", null, (_, _) => SetIgnored(project.Path, true));

        items.Add(new ToolStripSeparator());
        string? log = running ? runner.Current!.LogPath : runner.LastResult(project.Path)?.LogPath;
        items.Add(new ToolStripMenuItem("View logs", null, (_, _) => ShowLog(project.Path)) { Enabled = log != null && File.Exists(log) });
        items.Add(new ToolStripMenuItem("Open report", null, (_, _) => OpenReport(project.Path)) { Enabled = project.ReportWrittenUtc != null });
        items.Add("Open project folder", null, (_, _) => OpenFolder(project.Path));
        items.Add(new ToolStripMenuItem("Open output folder", null, (_, _) => OpenFolder(project.OutputPath))
        {
            Enabled = project.OutputPath.Length > 0 && Directory.Exists(project.OutputPath),
        });

        string suggested = Path.Combine(project.OutputPath, "reference", "suggested-template.md");
        if (project.OutputPath.Length > 0 && File.Exists(suggested))
        {
            items.Add(new ToolStripSeparator());
            items.Add("Add the suggested template to my templates...", null, (_, _) => AddSuggestedTemplate(suggested));
        }

        items.Add(new ToolStripSeparator());
        items.Add(new ToolStripMenuItem("Delete project...", null, (_, _) => DeleteProjects([project])) { Enabled = !running && !queued });
    }

    /// <summary>Right-click menu of several selected projects.</summary>
    private void BuildSelectionMenu(ToolStripItemCollection items, List<Project> selected)
    {
        int busy = selected.Count(p => _host.Runner.IsRunning(p.Path) || _host.Runner.IsQueued(p.Path));
        items.Add(new ToolStripMenuItem($"Delete {selected.Count} projects...", null, (_, _) => DeleteProjects(selected))
        {
            Enabled = busy == 0,
            ToolTipText = busy > 0 ? "Stop or unqueue the analyses of the selected projects first." : null,
        });
    }

    private List<Project> SelectedProjects() =>
        _grid.SelectedRows.Cast<DataGridViewRow>()
            .Select(r => r.Tag is string path ? FindProject(path) : null)
            .OfType<Project>()
            .OrderBy(p => _grid.Rows.Cast<DataGridViewRow>().First(r => (string?)r.Tag == p.Path).Index)
            .ToList();

    /// <summary>Moves the projects' folders, and optionally their output folders, to the Recycle Bin after confirmation.</summary>
    private void DeleteProjects(IReadOnlyList<Project> projects)
    {
        projects = projects.Where(p => !_host.Runner.IsRunning(p.Path) && !_host.Runner.IsQueued(p.Path)).ToList();
        if (projects.Count == 0)
            return;
        bool hasOutput = projects.Any(HasOutput);
        string names = string.Join("\n", projects.Take(10).Select(p => "  " + _host.Settings.DisplayName(p.Path)))
            + (projects.Count > 10 ? $"\n  ...and {projects.Count - 10} more" : "");
        var delete = new TaskDialogButton("Delete");
        var page = new TaskDialogPage
        {
            Caption = "Media Analysis",
            Heading = projects.Count == 1 ? $"Delete {_host.Settings.DisplayName(projects[0].Path)}?" : $"Delete {projects.Count} projects?",
            Text = projects.Count == 1
                ? $"The project folder and its recordings go to the Recycle Bin:\n{projects[0].Path}"
                : $"The project folders and their recordings go to the Recycle Bin:\n{names}",
            Icon = TaskDialogIcon.Warning,
            Buttons = { delete, TaskDialogButton.Cancel },
            DefaultButton = TaskDialogButton.Cancel,
            Verification = !hasOutput ? null
                : projects.Count == 1
                    ? new TaskDialogVerificationCheckBox($"Also delete the report and its files ({projects[0].OutputPath})", isChecked: true)
                    : new TaskDialogVerificationCheckBox("Also delete their reports and files (output folders)", isChecked: true),
        };
        if (TaskDialog.ShowDialog(this, page) != delete)
            return;

        bool withOutput = hasOutput && page.Verification!.Checked;
        foreach (var project in projects)
        {
            var folders = new List<string> { project.Path };
            if (withOutput && HasOutput(project))
                folders.Add(project.OutputPath);
            try
            {
                foreach (string folder in folders)
                    Microsoft.VisualBasic.FileIO.FileSystem.DeleteDirectory(folder,
                        Microsoft.VisualBasic.FileIO.UIOption.OnlyErrorDialogs, Microsoft.VisualBasic.FileIO.RecycleOption.SendToRecycleBin);
                _host.Settings.Forget(project.Path);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or OperationCanceledException)
            {
                MessageBox.Show(this, $"{_host.Settings.DisplayName(project.Path)} could not be deleted: {ex.Message}\n\nThe remaining projects were left as they are.",
                    "Media Analysis", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                break;
            }
        }
        _host.SaveSettings(foldersChanged: false);

        static bool HasOutput(Project p) => p.OutputPath.Length > 0 && Directory.Exists(p.OutputPath);
    }

    private Project? FindProject(string path) =>
        _host.Projects.FirstOrDefault(p => string.Equals(p.Path, path, StringComparison.OrdinalIgnoreCase));

    private void BeginRename()
    {
        if (_grid.CurrentRow == null)
            return;
        _grid.CurrentCell = _grid.CurrentRow.Cells[0];
        _grid.BeginEdit(selectAll: true);
    }

    private void Analyse(string path)
    {
        if (FindProject(path) is { } project && _host.Settings.OutputRoot != null)
            _host.Runner.Enqueue(ProjectCatalog.Read(new DirectoryInfo(project.Path), _host.Settings.OutputRoot));
        RefreshGrid();
    }

    private void AnalyseFromScratch(string path)
    {
        if (FindProject(path) is not { } project || _host.Settings.OutputRoot == null)
            return;
        if (MessageBox.Show(this,
                "Clear everything kept from earlier analyses of this project (transcript, scan, Claude results) and analyse it "
                + "again from the start?\n\nThis takes as long as a first analysis and uses Claude again. The current report "
                + "stays until the new one replaces it.",
                "Media Analysis", MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes)
            return;
        _host.Runner.Enqueue(ProjectCatalog.Read(new DirectoryInfo(project.Path), _host.Settings.OutputRoot), fresh: true);
        RefreshGrid();
    }

    private void Stop(string path)
    {
        if (MessageBox.Show(this, "Stop this analysis? Analysing it again later continues where it stopped.",
                "Media Analysis", MessageBoxButtons.YesNo, MessageBoxIcon.Question) == DialogResult.Yes)
            _host.Runner.Cancel(path);
        RefreshGrid();
    }

    private void Remove(string path)
    {
        _host.Runner.Cancel(path);
        RefreshGrid();
    }

    private void SetIgnored(string path, bool ignored)
    {
        _host.Settings.SetIgnored(path, ignored);
        _host.SaveSettings(foldersChanged: false);
    }

    private void AnalyseAllWaiting()
    {
        foreach (var p in _host.Projects.Where(p => p.State == ProjectState.NotAnalysed && !_host.Settings.IsIgnored(p.Path))
                     .OrderBy(p => p.LastModifiedUtc))
            _host.Runner.Enqueue(p);
        RefreshGrid();
    }

    private void ShowLog(string path)
    {
        string? log = _host.Runner.IsRunning(path) ? _host.Runner.Current!.LogPath : _host.Runner.LastResult(path)?.LogPath;
        if (log == null || !File.Exists(log))
            return;
        if (_logWindows.TryGetValue(log, out var open) && !open.IsDisposed)
        {
            open.Activate();
            return;
        }
        var window = new LogWindow(log, _host.Settings.DisplayName(path));
        window.FormClosed += (_, _) => _logWindows.Remove(log);
        _logWindows[log] = window;
        window.Show();
    }

    private void OpenReport(string path)
    {
        if (FindProject(path) is { } p && ReportOpener.Open(p.ReportPath, _host.Settings.ClaudeProfileInUse()) is { } error)
            MessageBox.Show(this, error, "Media Analysis", MessageBoxButtons.OK, MessageBoxIcon.Warning);
    }

    private void AddSuggestedTemplate(string suggested)
    {
        var info = TemplateCatalog.Load(null, Path.GetDirectoryName(suggested)!).FirstOrDefault();
        string id = info?.Id ?? "suggested";
        string target = Path.Combine(TemplateCatalog.UserDir, id + ".md");
        if (File.Exists(target))
        {
            MessageBox.Show(this, $"You already have a template named '{id}':\n{target}", "Media Analysis",
                MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        Directory.CreateDirectory(TemplateCatalog.UserDir);
        File.Copy(suggested, target);
        Process.Start(new ProcessStartInfo(target) { UseShellExecute = true });
        MessageBox.Show(this, $"Added '{id}' to your templates and opened it. Write its instructions, save it, then analyse the project again.",
            "Media Analysis", MessageBoxButtons.OK, MessageBoxIcon.Information);
    }

    // ---- Config ---------------------------------------------------------------------------------------------------

    private TabPage CreateConfigPage()
    {
        foreach (var (label, _) in WhisperModels) _whisperModel.Items.Add(label);
        foreach (var (label, _) in Languages) _language.Items.Add(label);
        foreach (var (label, _) in Efforts) _effort.Items.Add(label);

        var table = new TableLayoutPanel { Dock = DockStyle.Top, AutoSize = true, ColumnCount = 3, Padding = new Padding(12) };
        table.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        table.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        table.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));

        void Heading(string text) => table.Controls.Add(new Label
        {
            Text = text, AutoSize = true, Font = new Font(Font, FontStyle.Bold), Padding = new Padding(0, 12, 0, 4),
        }, 0, table.RowCount++);
        void Field(string label, Control control, Control? extra = null, string? note = null)
        {
            int row = table.RowCount++;
            table.Controls.Add(new Label { Text = label, AutoSize = true, Padding = new Padding(0, 7, 12, 0) }, 0, row);
            table.Controls.Add(control, 1, row);
            if (extra != null)
                table.Controls.Add(extra, 2, row);
            if (note != null)
                table.Controls.Add(new Label { Text = note, AutoSize = true, ForeColor = SystemColors.GrayText, Padding = new Padding(0, 0, 0, 4) }, 1, table.RowCount++);
        }
        Button Browse(Control target, string description)
        {
            var button = new Button { Text = "Browse...", AutoSize = true };
            button.Click += (_, _) =>
            {
                using var dialog = new FolderBrowserDialog
                {
                    Description = description, UseDescriptionForTitle = true, ShowNewFolderButton = true,
                    InitialDirectory = Directory.Exists(target.Text) ? target.Text : "",
                };
                if (dialog.ShowDialog(this) == DialogResult.OK)
                    target.Text = dialog.SelectedPath;
            };
            return button;
        }

        Heading("Folders");
        Field("Projects folder", _projectsRoot, Browse(_projectsRoot, "Choose the folder whose subfolders are projects"),
            "Each subfolder is a project (one recording session). New subfolders appear automatically.");
        Field("Output folder", _outputRoot, Browse(_outputRoot, "Choose where reports are written"),
            "Each project's report goes to <output folder>\\<project name>.");
        Heading("New projects");
        Field("", _autoAnalyse, note: "Projects added after you turn this on are analysed once their files have stopped changing for "
            + $"{AutoAnalysis.SettleTime.TotalMinutes:0} minutes, one at a time.\nProjects that already exist are not affected; "
            + "use \"Analyse all not analysed\" for those. A failed or stopped analysis is not retried automatically.");
        Heading("Analysis");
        Field("Transcription model", _whisperModel, note: "Changing it transcribes projects again the next time they are analysed.");
        Field("Spoken language", _language);
        Field("Claude profile", _claudeProfile, Browse(_claudeProfile, "Choose the Claude Code profile folder to sign in with"),
            "The Claude Code profile (config folder) whose login and subscription analyses use; Open report starts VS Code with it too.\n"
            + "The list shows the .claude* folders in your home folder that hold a login; you can also type or browse to another.\n"
            + $"Empty = the default ({AppSettings.DefaultClaudeProfile}). Sign in once with that profile in Claude Code first.");
        Field("Claude thinking", _effort, note: "More thinking can give better reports but takes longer.");
        Field("Maximum key moments", _maxFrames, note: "How many moments may be captured as frames (0 = the default, 30).");

        var save = new Button { Text = "Save", AutoSize = true, Padding = new Padding(16, 2, 16, 2) };
        save.Click += (_, _) => SaveConfig();
        var revert = new Button { Text = "Revert", AutoSize = true };
        revert.Click += (_, _) => LoadConfig();
        var buttons = new FlowLayoutPanel { AutoSize = true, Padding = new Padding(0, 16, 0, 0) };
        buttons.Controls.AddRange([save, revert, new Label { Text = "Changes apply to the next analysis.", AutoSize = true, Padding = new Padding(8, 8, 0, 0), ForeColor = SystemColors.GrayText }]);
        table.Controls.Add(buttons, 1, table.RowCount++);

        var page = new TabPage("Config") { AutoScroll = true };
        page.Controls.Add(table);
        return page;
    }

    private void LoadConfig()
    {
        var s = _host.Settings;
        _projectsRoot.Text = s.ProjectsRoot ?? "";
        _outputRoot.Text = s.OutputRoot ?? "";
        _claudeProfile.Items.Clear();
        foreach (string profile in AppSettings.FindClaudeProfiles())
            _claudeProfile.Items.Add(profile);
        _claudeProfile.Text = s.ClaudeProfile ?? "";
        _whisperModel.SelectedIndex = Math.Max(0, Array.FindIndex(WhisperModels, m => m.Value == s.WhisperModel));
        _language.SelectedIndex = Math.Max(0, Array.FindIndex(Languages, m => m.Value == s.Language));
        _effort.SelectedIndex = Math.Max(0, Array.FindIndex(Efforts, m => m.Value == s.Effort));
        _maxFrames.Value = Math.Clamp(s.MaxFrames ?? 0, 0, 100);
        _autoAnalyse.Checked = s.AutoAnalyse;
    }

    private void SaveConfig()
    {
        string projects = _projectsRoot.Text.Trim();
        string output = _outputRoot.Text.Trim();
        if (projects.Length > 0 && !Directory.Exists(projects))
        {
            MessageBox.Show(this, $"The projects folder doesn't exist:\n{projects}", "Media Analysis", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return;
        }
        if (output.Length > 0)
        {
            try
            {
                Directory.CreateDirectory(output);
                string probe = Path.Combine(output, $".write-test-{Guid.NewGuid():N}");
                File.WriteAllBytes(probe, []);
                File.Delete(probe);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or ArgumentException or NotSupportedException)
            {
                MessageBox.Show(this, $"Reports can't be written to {output}:\n{ex.Message}", "Media Analysis", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
        }
        string profile = _claudeProfile.Text.Trim();
        if (profile.Length > 0)
        {
            if (!Directory.Exists(profile))
            {
                MessageBox.Show(this, $"The Claude profile folder doesn't exist:\n{profile}", "Media Analysis", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            if (!AppSettings.HasClaudeLogin(profile) && MessageBox.Show(this,
                    $"No Claude login was found in {profile}.\nAnalyses will fail until you sign in with this profile in Claude Code.\n\nSave anyway?",
                    "Media Analysis", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) != DialogResult.Yes)
                return;
        }
        var s = _host.Settings;
        bool foldersChanged = (s.ProjectsRoot ?? "") != projects || (s.OutputRoot ?? "") != output;
        s.ProjectsRoot = projects.Length > 0 ? projects : null;
        s.OutputRoot = output.Length > 0 ? output : null;
        s.ClaudeProfile = profile.Length > 0 ? profile : null;
        s.WhisperModel = WhisperModels[_whisperModel.SelectedIndex].Value;
        s.Language = Languages[_language.SelectedIndex].Value;
        s.Effort = Efforts[_effort.SelectedIndex].Value;
        s.MaxFrames = _maxFrames.Value > 0 ? (int)_maxFrames.Value : null;
        if (_autoAnalyse.Checked != s.AutoAnalyse)
        {
            s.AutoAnalyse = _autoAnalyse.Checked;
            s.AutoAnalyseSinceUtc = _autoAnalyse.Checked ? DateTime.UtcNow : null;
        }
        _host.SaveSettings(foldersChanged);
        MessageBox.Show(this, "Saved.", "Media Analysis", MessageBoxButtons.OK, MessageBoxIcon.Information);
    }

    // ---- Templates ------------------------------------------------------------------------------------------------

    private TabPage CreateTemplatesPage()
    {
        var create = new Button { Text = "New template...", AutoSize = true };
        create.Click += (_, _) => NewTemplate();
        var open = new Button { Text = "Open", AutoSize = true };
        open.Click += (_, _) => OpenSelectedTemplate();
        var copy = new Button { Text = "Copy to my templates", AutoSize = true };
        copy.Click += (_, _) => CopySelectedTemplate();
        var folder = new Button { Text = "Open my templates folder", AutoSize = true };
        folder.Click += (_, _) =>
        {
            Directory.CreateDirectory(TemplateCatalog.UserDir);
            OpenFolder(TemplateCatalog.UserDir);
        };
        var refresh = new Button { Text = "Refresh", AutoSize = true };
        refresh.Click += (_, _) => LoadTemplates();
        var bar = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, Padding = new Padding(4, 6, 4, 4) };
        bar.Controls.AddRange([create, open, copy, folder, refresh]);
        var note = new Label
        {
            Dock = DockStyle.Bottom, AutoSize = false, Height = 40, ForeColor = SystemColors.GrayText, Padding = new Padding(8, 4, 8, 4),
            Text = "Your templates replace built-in ones with the same id. To change a built-in template, copy it to your templates and edit the copy. "
                + "When no template fits, a general report is written and its output contains a suggested template (right-click the project).",
        };
        var page = new TabPage("Templates");
        page.Controls.Add(_templates);
        page.Controls.Add(bar);
        page.Controls.Add(note);
        return page;
    }

    private void LoadTemplates()
    {
        _templates.Items.Clear();
        foreach (var t in TemplateCatalog.Load(_host.LoadCore()?.BuiltinTemplatesDir, TemplateCatalog.UserDir))
        {
            string source = t.BuiltIn ? (t.Overridden ? "Built-in (replaced)" : t.Fallback ? "Built-in (when none fits)" : "Built-in") : "Yours";
            var item = new ListViewItem([t.Id, t.Name, source, t.Description]) { Tag = t };
            if (t.Overridden)
                item.ForeColor = SystemColors.GrayText;
            _templates.Items.Add(item);
        }
    }

    private TemplateInfo? SelectedTemplate => _templates.SelectedItems.Count > 0 ? _templates.SelectedItems[0].Tag as TemplateInfo : null;

    private void OpenSelectedTemplate()
    {
        if (SelectedTemplate is { } t)
            Process.Start(new ProcessStartInfo(t.Path) { UseShellExecute = true });
    }

    private void CopySelectedTemplate()
    {
        if (SelectedTemplate is not { BuiltIn: true } t)
        {
            MessageBox.Show(this, "Select a built-in template to copy.", "Media Analysis", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        string target = Path.Combine(TemplateCatalog.UserDir, Path.GetFileName(t.Path));
        if (!File.Exists(target))
        {
            Directory.CreateDirectory(TemplateCatalog.UserDir);
            File.Copy(t.Path, target);
        }
        Process.Start(new ProcessStartInfo(target) { UseShellExecute = true });
        LoadTemplates();
    }

    private void NewTemplate()
    {
        string? id = InputDialog.Ask(this, "New template", "Template id (lowercase letters, digits and dashes, e.g. sales-call):");
        if (string.IsNullOrWhiteSpace(id))
            return;
        CoreConfig? core = _host.LoadCore();
        if (core == null)
            return;
        try
        {
            using var process = Process.Start(core.Command(["templates", "new", id.Trim()]))!;
            string output = process.StandardOutput.ReadToEnd() + process.StandardError.ReadToEnd();
            process.WaitForExit(30_000);
            string path = Path.Combine(TemplateCatalog.UserDir, id.Trim() + ".md");
            if (process.ExitCode != 0 || !File.Exists(path))
            {
                MessageBox.Show(this, $"The template could not be created.\n\n{output.Trim()}", "Media Analysis", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
            LoadTemplates();
        }
        catch (Exception ex) when (ex is IOException or System.ComponentModel.Win32Exception or InvalidOperationException)
        {
            MessageBox.Show(this, $"The template could not be created: {ex.Message}", "Media Analysis", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }

    // ---- About ----------------------------------------------------------------------------------------------------

    private TabPage CreateAboutPage()
    {
        var logs = new Button { Text = "Open logs folder", AutoSize = true };
        logs.Click += (_, _) => OpenFolder(AppPaths.Logs);
        var bar = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, Padding = new Padding(8) };
        bar.Controls.Add(logs);
        var page = new TabPage("About");
        page.Controls.Add(bar);
        page.Controls.Add(_about);
        return page;
    }

    private void LoadAbout()
    {
        CoreConfig? core = _host.LoadCore();
        (string, string)[] rows =
        [
            ("Built", BuildInfo.Built),
            ("Installed in", AppContext.BaseDirectory.TrimEnd('\\')),
            ("Analysis engine", core != null ? core.Python : "not available (run deploy.cmd to repair)"),
            ("Data", AppPaths.Data),
            ("Run statistics", Path.Combine(AppPaths.Data, "stats", "runs.jsonl")),
        ];
        _about.SuspendLayout();
        _about.Controls.Clear();
        _about.RowCount = 0;
        foreach (var (label, value) in rows)
        {
            int row = _about.RowCount++;
            _about.Controls.Add(new Label { Text = label, AutoSize = true, Font = new Font(Font, FontStyle.Bold), Padding = new Padding(0, 4, 16, 4) }, 0, row);
            _about.Controls.Add(new TextBox { Text = value, ReadOnly = true, BorderStyle = BorderStyle.None, Width = 760, BackColor = SystemColors.Control }, 1, row);
        }
        _about.ResumeLayout();
    }

    // ---- Helpers --------------------------------------------------------------------------------------------------

    private void OpenFolder(string? path)
    {
        if (string.IsNullOrEmpty(path))
            MessageBox.Show(this, "Choose the folder on the Config tab first.", "Media Analysis", MessageBoxButtons.OK, MessageBoxIcon.Information);
        else if (Directory.Exists(path))
            Process.Start(new ProcessStartInfo("explorer.exe", $"\"{path}\"") { UseShellExecute = true });
        else
            MessageBox.Show(this, $"Folder not found:\n{path}", "Media Analysis", MessageBoxButtons.OK, MessageBoxIcon.Warning);
    }

    private static string FirstLine(string text)
    {
        string line = text.Split('\n', 2)[0].Trim();
        return line.Length > 160 ? line[..157] + "..." : line;
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _host.Changed -= OnHostChanged;
            _tick.Dispose();
        }
        base.Dispose(disposing);
    }
}

/// <summary>A one-line text prompt.</summary>
static class InputDialog
{
    public static string? Ask(IWin32Window owner, string title, string prompt)
    {
        using var form = new Form
        {
            Text = title, FormBorderStyle = FormBorderStyle.FixedDialog, StartPosition = FormStartPosition.CenterParent,
            MinimizeBox = false, MaximizeBox = false, ClientSize = new Size(440, 120), Font = new Font("Segoe UI", 9f),
        };
        var label = new Label { Text = prompt, Left = 12, Top = 12, Width = 416, AutoSize = false, Height = 34 };
        var box = new TextBox { Left = 12, Top = 48, Width = 416 };
        var ok = new Button { Text = "OK", DialogResult = DialogResult.OK, Left = 272, Top = 82, Width = 75 };
        var cancel = new Button { Text = "Cancel", DialogResult = DialogResult.Cancel, Left = 353, Top = 82, Width = 75 };
        form.Controls.AddRange([label, box, ok, cancel]);
        form.AcceptButton = ok;
        form.CancelButton = cancel;
        return form.ShowDialog(owner) == DialogResult.OK ? box.Text : null;
    }
}
