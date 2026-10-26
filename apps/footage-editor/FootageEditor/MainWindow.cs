using System.Diagnostics;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace FootageEditor;

/// <summary>
/// The window: hosts the presentation (HTML served by the core's local service) in WebView2 and does only what a web
/// page can't: start and stop the service, pick and drop files with their paths, the settings dialog, and close on
/// request of an install (App deployment standard 3.5: refused while an analysis or a chat turn runs).
/// </summary>
sealed class MainWindow : Form
{
    readonly string _dataDir;
    readonly WebView2 _web = new() { Dock = DockStyle.Fill };
    readonly Label _status = new() { Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleCenter, ForeColor = Color.Gainsboro };
    readonly EventWaitHandle _exitRequest = new(false, EventResetMode.AutoReset, $@"Local\{Program.AppId}-Exit");
    readonly EventWaitHandle _exitRefused = new(false, EventResetMode.AutoReset, $@"Local\{Program.AppId}-ExitRefused");
    readonly EventWaitHandle _show = new(false, EventResetMode.AutoReset, $@"Local\{Program.AppId}-Show");
    AppSettings _settings;
    Service? _service;
    bool _closingConfirmed;

    public MainWindow(string dataDir)
    {
        _dataDir = dataDir;
        _settings = AppSettings.Load(dataDir);
        Text = Program.AppName;
        Width = 1600;
        Height = 950;
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(21, 23, 28);
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        _status.Text = "Starting…";
        Controls.Add(_status);
        Load += async (_, _) => await StartAsync();
        WatchHandle(_exitRequest, () => _ = OnExitRequestAsync());
        WatchHandle(_show, () => { if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal; Activate(); });
    }

    void WatchHandle(WaitHandle handle, Action action) =>
        ThreadPool.RegisterWaitForSingleObject(handle, (_, _) => { if (IsHandleCreated) BeginInvoke(action); }, null, -1, false);

    async Task StartAsync()
    {
        try
        {
            string appDir = AppContext.BaseDirectory;
            var core = CoreConfig.Load(appDir);
            _service = await Task.Run(() => Service.Start(core, Path.Combine(appDir, "ui"), _dataDir, _settings));
            if (!Controls.Contains(_web))
            {
                var env = await CoreWebView2Environment.CreateAsync(userDataFolder: Path.Combine(_dataDir, "webview2"));
                await _web.EnsureCoreWebView2Async(env);
                _web.CoreWebView2.WebMessageReceived += OnWebMessage;
                _web.CoreWebView2.Settings.AreDevToolsEnabled = false;
                _web.CoreWebView2.Settings.IsStatusBarEnabled = false;
                // The page has its own timeline zoom; browser zoom (Ctrl + wheel, Ctrl +/-, pinch) only breaks the layout.
                _web.CoreWebView2.Settings.IsZoomControlEnabled = false;
                _web.CoreWebView2.Settings.IsPinchZoomEnabled = false;
                _web.ZoomFactor = 1;
                Controls.Add(_web);
                _web.BringToFront();
            }
            _web.CoreWebView2.Navigate(_service.Url);
        }
        catch (Exception ex) when (ex is InvalidOperationException or IOException or WebView2RuntimeNotFoundException)
        {
            _status.Text = ex is WebView2RuntimeNotFoundException
                ? "The Microsoft Edge WebView2 Runtime is missing. Install it from https://go.microsoft.com/fwlink/p/?LinkId=2124703 and start again."
                : ex.Message;
            _status.BringToFront();
        }
    }

    void OnWebMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        string? type;
        try
        {
            using var doc = JsonDocument.Parse(e.WebMessageAsJson);
            type = doc.RootElement.TryGetProperty("type", out var t) ? t.GetString() : null;
        }
        catch (JsonException)
        {
            return;
        }
        switch (type)
        {
            case "pickFiles":
                using (var dialog = new OpenFileDialog
                {
                    Multiselect = true,
                    Title = "Import footage",
                    Filter = "Video and audio|*.mp4;*.mov;*.mkv;*.avi;*.webm;*.m4v;*.wmv;*.mts;*.ts;*.mpg;*.mpeg;*.mp3;*.wav;*.m4a;*.aac;*.flac;*.ogg;*.opus|All files|*.*",
                })
                {
                    if (dialog.ShowDialog(this) == DialogResult.OK)
                        SendFiles(dialog.FileNames);
                }
                break;
            case "dropped":
                var paths = e.AdditionalObjects?.OfType<CoreWebView2File>().Select(f => f.Path).ToArray() ?? [];
                SendFiles(paths);
                break;
            case "settings":
                ShowSettings();
                break;
        }
    }

    void SendFiles(string[] paths) =>
        _web.CoreWebView2.PostWebMessageAsJson(JsonSerializer.Serialize(new { type = "files", paths }));

    void ShowSettings()
    {
        var edited = _settings.Copy();
        using var dialog = new SettingsDialog(edited, AppSettings.ClaudeProfiles(), (endpoint, key) =>
            Service.CheckAzure(CoreConfig.Load(AppContext.BaseDirectory), _dataDir, endpoint, key));
        if (dialog.ShowDialog(this) != DialogResult.OK)
            return;
        bool restart = edited.ClaudeProfile != _settings.ClaudeProfile || edited.Transcriber != _settings.Transcriber
            || edited.AzureEndpoint != _settings.AzureEndpoint || edited.AzureKey != _settings.AzureKey;
        _settings = edited;
        _settings.Save();
        if (!restart)
            return;
        // The service passes these on to the analyses it starts, so it has to start again to use them.
        _service?.Dispose();
        _ = StartAsync();
    }

    async Task OnExitRequestAsync()
    {
        if (_service != null && await _service.IsBusyAsync())
        {
            _exitRefused.Set();
            return;
        }
        _closingConfirmed = true;
        Close();
    }

    protected override async void OnFormClosing(FormClosingEventArgs e)
    {
        if (!_closingConfirmed && _service != null && e.CloseReason == CloseReason.UserClosing)
        {
            e.Cancel = true;
            bool busy = await _service.IsBusyAsync();
            if (!busy || MessageBox.Show(this,
                    "An analysis or a chat answer is still running. Close anyway?\n\nIt stops now; analysing again continues where it stopped.",
                    Program.AppName, MessageBoxButtons.YesNo, MessageBoxIcon.Question) == DialogResult.Yes)
            {
                _closingConfirmed = true;
                Close();
            }
            return;
        }
        base.OnFormClosing(e);
    }

    protected override void OnFormClosed(FormClosedEventArgs e)
    {
        _service?.Dispose();
        _exitRequest.Dispose();
        _exitRefused.Dispose();
        _show.Dispose();
        base.OnFormClosed(e);
    }
}

/// <summary>Which Claude Code login the analyses and the chat use, and where speech is transcribed.</summary>
sealed class SettingsDialog : Form
{
    static readonly (string Label, string? Value)[] Transcribers =
    [
        ("On this computer (Local Whisper, free)", null),
        ("Azure AI Speech (in the cloud, much faster, billed to Azure)", AppSettings.AzureTranscriber),
    ];

    readonly AppSettings _settings;
    readonly ComboBox _profiles = new() { DropDownStyle = ComboBoxStyle.DropDown, Width = 460 };
    readonly ComboBox _transcriber = new() { DropDownStyle = ComboBoxStyle.DropDownList, Width = 460 };
    readonly TextBox _endpoint = new() { Width = 460, PlaceholderText = "https://<resource>.services.ai.azure.com/" };
    readonly TextBox _key = new() { Width = 460, UseSystemPasswordChar = true };
    readonly Label _testResult = new() { AutoSize = true, MaximumSize = new Size(460, 0), Padding = new Padding(0, 7, 0, 0) };
    readonly List<Control> _azureRows = [];

    /// <param name="settings">Changed in place when the user saves.</param>
    /// <param name="checkAzure">Tests an endpoint and key; runs off the UI thread.</param>
    public SettingsDialog(AppSettings settings, List<string> found, Func<string, string, (bool Ok, string Message)> checkAzure)
    {
        _settings = settings;
        Text = $"{Program.AppName} settings";
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = MinimizeBox = false;
        StartPosition = FormStartPosition.CenterParent;
        AutoSize = true;
        AutoSizeMode = AutoSizeMode.GrowAndShrink;
        Padding = new Padding(14);
        _profiles.Items.AddRange([.. found]);
        _profiles.Text = settings.ClaudeProfile ?? "";
        foreach (var (label, _) in Transcribers)
            _transcriber.Items.Add(label);
        _transcriber.SelectedIndex = Math.Max(0, Array.FindIndex(Transcribers, t => t.Value == settings.Transcriber));
        _endpoint.Text = settings.AzureEndpoint ?? "";
        _key.Text = settings.AzureKey ?? "";

        var layout = new TableLayoutPanel { AutoSize = true, ColumnCount = 3 };
        void Heading(string text) => layout.Controls.Add(new Label
        {
            Text = text, AutoSize = true, Font = new Font(Font, FontStyle.Bold), Padding = new Padding(0, 10, 0, 4),
        }, 0, layout.RowCount++);
        List<Control> Field(string label, Control control, Control? extra = null, string? note = null)
        {
            int row = layout.RowCount++;
            var added = new List<Control> { new Label { Text = label, AutoSize = true, Padding = new Padding(0, 7, 12, 0) }, control };
            layout.Controls.Add(added[0], 0, row);
            layout.Controls.Add(control, 1, row);
            if (extra != null)
            {
                layout.Controls.Add(extra, 2, row);
                added.Add(extra);
            }
            if (note != null)
            {
                var noteLabel = new Label { Text = note, AutoSize = true, MaximumSize = new Size(460, 0), ForeColor = SystemColors.GrayText, Padding = new Padding(0, 0, 0, 4) };
                layout.Controls.Add(noteLabel, 1, layout.RowCount++);
                added.Add(noteLabel);
            }
            return added;
        }

        Heading("Claude");
        Field("Profile", _profiles, note: "The Claude Code config folder whose login the analyses and the chat use. Empty = the default.");
        Heading("Transcription");
        Field("Engine", _transcriber, note: "Where speech is turned into text. Changing it transcribes footage again the next time it is analysed.");
        _azureRows.AddRange(Field("Endpoint", _endpoint, note: "The resource's \"AI services endpoint\" (Azure portal, Keys and Endpoint, AI Services tab), "
            + "not the \"Speech to Text\" one."));
        var showKey = new CheckBox { Text = "Show", AutoSize = true, Padding = new Padding(4, 3, 0, 0) };
        showKey.CheckedChanged += (_, _) => _key.UseSystemPasswordChar = !showKey.Checked;
        _azureRows.AddRange(Field("Key", _key, showKey, "Key 1 or Key 2 from the resource's Keys and Endpoint page. "
            + "Saved encrypted, readable only by your Windows account."));
        var test = new Button { Text = "Test connection", AutoSize = true };
        test.Click += async (_, _) =>
        {
            if (AzureProblem() is { } problem)
            {
                ShowTestResult(false, problem);
                return;
            }
            string endpoint = _endpoint.Text.Trim(), key = _key.Text.Trim();
            test.Enabled = false;
            _testResult.ForeColor = SystemColors.GrayText;
            _testResult.Text = "Testing\u2026";
            try
            {
                var (ok, message) = await Task.Run(() => checkAzure(endpoint, key));
                ShowTestResult(ok, message);
            }
            catch (Exception ex) when (ex is InvalidOperationException or System.ComponentModel.Win32Exception)
            {
                ShowTestResult(false, ex.Message);
            }
            finally
            {
                test.Enabled = true;
            }
        };
        var testRow = new FlowLayoutPanel { AutoSize = true, MaximumSize = new Size(560, 0), Margin = new Padding(0, 2, 0, 4) };
        testRow.Controls.AddRange([test, _testResult]);
        _azureRows.AddRange(Field("", testRow));
        var portal = new LinkLabel { Text = "Open AI resources in the Azure portal", AutoSize = true, Padding = new Padding(0, 7, 0, 0) };
        portal.LinkClicked += (_, _) => OpenExternal("https://portal.azure.com/#browse/Microsoft.CognitiveServices%2Faccounts");
        _azureRows.AddRange(Field("Firewall", portal, note: "If the resource only accepts listed IP addresses: open it in the Azure portal, then Networking, tick 'Add your client IP address' and Save (it takes a few minutes). Use that box rather than a what's-my-IP website: Azure may see a different address."));
        var terms = new Button { Text = "Edit list…", AutoSize = true };
        terms.Click += (_, _) => OpenExternal(AppSettings.EnsureTermsFile(System.IO.Path.GetDirectoryName(settings.Path)!));
        _azureRows.AddRange(Field("Terms", terms, note: "Names and technical terms Azure should expect, one per line. Azure uses them for English speech; its Vietnamese model ignores them. Changing the list transcribes projects again the next time they are analysed."));
        _transcriber.SelectedIndexChanged += (_, _) => ShowAzureFields();
        ShowAzureFields();

        var ok = new Button { Text = "Save", AutoSize = true };
        var cancel = new Button { Text = "Cancel", DialogResult = DialogResult.Cancel, AutoSize = true };
        ok.Click += (_, _) => Save();
        AcceptButton = ok;
        CancelButton = cancel;
        var buttons = new FlowLayoutPanel { AutoSize = true, FlowDirection = FlowDirection.RightToLeft, Dock = DockStyle.Fill, Padding = new Padding(0, 12, 0, 0) };
        buttons.Controls.AddRange([cancel, ok]);
        layout.Controls.Add(buttons, 0, layout.RowCount++);
        layout.SetColumnSpan(buttons, 3);
        Controls.Add(layout);
    }

    bool AzureChosen => Transcribers[_transcriber.SelectedIndex].Value == AppSettings.AzureTranscriber;

    void ShowAzureFields() => _azureRows.ForEach(c => c.Visible = AzureChosen);

    string? AzureProblem()
    {
        if (!Uri.TryCreate(_endpoint.Text.Trim(), UriKind.Absolute, out Uri? uri) || uri.Scheme != Uri.UriSchemeHttps)
            return "Enter the Azure endpoint, starting with https:// (the AI services endpoint on the resource's Keys and Endpoint page).";
        if (_key.Text.Trim().Length == 0)
            return "Enter the Azure key.";
        return null;
    }

    void OpenExternal(string target)
    {
        try
        {
            Process.Start(new ProcessStartInfo(target) { UseShellExecute = true });
        }
        catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or IOException or UnauthorizedAccessException)
        {
            MessageBox.Show(this, $"Couldn't open {target}:\n{ex.Message}", Program.AppName, MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }

    void ShowTestResult(bool ok, string text)
    {
        _testResult.ForeColor = ok ? Color.ForestGreen : Color.Firebrick;
        _testResult.Text = (ok ? "\u2713 " : "\u2717 ") + text;
    }

    void Save()
    {
        if (AzureChosen && AzureProblem() is { } problem)
        {
            MessageBox.Show(this, problem, Program.AppName, MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return;
        }
        _settings.ClaudeProfile = string.IsNullOrWhiteSpace(_profiles.Text) ? null : _profiles.Text.Trim();
        _settings.Transcriber = Transcribers[_transcriber.SelectedIndex].Value;
        // Kept when Local is chosen, so switching back to Azure doesn't need them again.
        _settings.AzureEndpoint = _endpoint.Text.Trim() is { Length: > 0 } endpoint ? endpoint : null;
        _settings.AzureKey = _key.Text;
        DialogResult = DialogResult.OK;
    }
}
